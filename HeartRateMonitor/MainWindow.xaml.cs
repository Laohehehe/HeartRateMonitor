using HeartRateMonitor.Core;
using HeartRateMonitor.Services;
using HeartRateMonitor.Views;
using Microsoft.UI;
using Microsoft.UI.Composition.SystemBackdrops;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Windows.Graphics;

namespace HeartRateMonitor;

/// <summary>
/// 应用主窗口：自定义标题栏 + 可切换的窗口背景（亚克力 / 云母 / 纯色）+ 页面宿主框架。
/// </summary>
public sealed partial class MainWindow : Window
{
    private const int InitialWidth = 1160;
    private const int InitialHeight = 740;

    public MainWindow()
    {
        AppLogger.Debug("MainWindow: 开始构造");

        InitializeComponent();
        AppLogger.Debug("MainWindow: InitializeComponent 完成");

        Title = "心率监测器";

        ExtendsContentIntoTitleBar = true;
        SetTitleBar(AppTitleBar);
        AppLogger.Debug("MainWindow: 标题栏完成");

        _backdropHost = new BackdropHost(this, RootGrid);
        AppLogger.Debug("MainWindow: BackdropHost 构造完成");

        ResizeWindow();
        ApplyBackdrop();
        AppLogger.Debug("MainWindow: 背景材质应用完成");

        // 设置页切换背景后即时生效
        BackdropService.Changed += OnBackdropSettingChanged;

        InitializeTray();
        AppLogger.Debug("MainWindow: 托盘初始化完成");

        AppWindow.Closing += OnAppWindowClosing;

        RootFrame.Navigate(typeof(HomePage));
        AppLogger.Debug("MainWindow: HomePage 导航完成");

        NavView.SelectedItem = NavView.MenuItems[0];
        AppLogger.Debug("MainWindow: 侧边栏选中完成");

        Closed += OnClosed;

        AppLogger.Debug("MainWindow: 构造结束");
    }

    private BackdropHost? _backdropHost;

    /// <summary>true 时真正退出；否则关闭主窗口只是收进托盘。</summary>
    private bool _reallyClosing;

    /// <summary>托盘提示是否已弹过（只弹一次）。</summary>
    private bool _trayHintShown;

    /// <summary>
    /// 侧边栏切换页面（均在主窗口内，不弹独立窗口）。
    /// </summary>
    private void OnNavSelectionChanged(NavigationView sender, NavigationViewSelectionChangedEventArgs args)
    {
        if (args.SelectedItem is not NavigationViewItem item) return;

        var tag = item.Tag as string;
        var pageType = tag switch
        {
            "chart" => typeof(ChartPage),
            "settings" => typeof(SettingsPage),
            _ => typeof(HomePage)
        };

        if (RootFrame.CurrentSourcePageType != pageType)
        {
            RootFrame.Navigate(pageType);
        }
    }

    private void OnBackdropSettingChanged(object? sender, EventArgs e) => ApplyBackdrop();

    // ---------------- 系统托盘（H.NotifyIcon） ----------------

    private void InitializeTray()
    {
        try
        {
            // 左键双击恢复主界面；右键弹菜单（MenuActivation 在 XAML 里设为 RightClick）
            TrayIcon.DoubleClickCommand = new RelayCommand(RestoreFromTray);
            TrayIcon.Icon = LoadTrayIcon();

            if (!TrayIcon.IsCreated) TrayIcon.ForceCreate(false);

            AppHost.Instance.PinnedChanged += OnPinnedStateChanged;
            SetTrayPinned(AppHost.Instance.IsPinned);

            AppLogger.Info("托盘图标已创建（H.NotifyIcon）");
        }
        catch (Exception ex)
        {
            // 托盘失败不影响主功能
            AppLogger.Warn($"初始化系统托盘失败: {ex.Message}");
        }
    }

    private static System.Drawing.Icon LoadTrayIcon()
    {
        try
        {
            var path = System.IO.Path.Combine(AppContext.BaseDirectory, "Assets", "tray_icon.ico");
            if (System.IO.File.Exists(path)) return new System.Drawing.Icon(path);
        }
        catch (Exception ex)
        {
            AppLogger.Warn($"加载托盘图标失败，改用系统默认图标: {ex.Message}");
        }

        return System.Drawing.SystemIcons.Application;
    }

    private void OnPinnedStateChanged(object? sender, bool pinned) => SetTrayPinned(pinned);

    /// <summary>同步菜单项的勾选状态与文字（文字保持 ≤2 字，规避 SecondWindow 的裁字）。</summary>
    private void SetTrayPinned(bool pinned)
    {
        TrayPinItem.IsChecked = pinned;
        TrayPinItem.Text = pinned ? "取消" : "固定";
    }

    /// <summary>关闭主窗口 → 收进托盘继续后台监测（托盘菜单「退出」才真正退出）。</summary>
    private void OnAppWindowClosing(AppWindow sender, AppWindowClosingEventArgs args)
    {
        if (_reallyClosing) return;

        args.Cancel = true;
        sender.Hide();
        ShowTrayHintOnce();
        AppLogger.Info("主窗口已收进托盘");
    }

    private void ShowTrayHintOnce()
    {
        if (_trayHintShown) return;
        _trayHintShown = true;

        try
        {
            TrayIcon.ShowNotification("心率监测器仍在运行", "双击托盘图标恢复主界面，右键可选择退出。");
        }
        catch (Exception ex)
        {
            AppLogger.Debug($"显示托盘通知失败: {ex.Message}");
        }
    }

    private void RestoreFromTray()
    {
        AppWindow.Show();
        Activate();
    }

    private void OnTrayShowClick(object sender, RoutedEventArgs e) => RestoreFromTray();

    private void OnTrayPinClick(object sender, RoutedEventArgs e)
        => AppHost.Instance.SetPinned(!AppHost.Instance.IsPinned);

    private void OnTrayExitClick(object sender, RoutedEventArgs e) => ExitApplication();

    private void ExitApplication()
    {
        _reallyClosing = true;
        AppHost.Instance.PinnedChanged -= OnPinnedStateChanged;
        Close();
    }

    /// <summary>主窗口关闭即退出应用：停监控、保存悬浮窗位置、关闭日志。</summary>
    private void OnClosed(object sender, WindowEventArgs args)
    {
        AppHost.Instance.PinnedChanged -= OnPinnedStateChanged;

        try
        {
            TrayIcon.Dispose();
        }
        catch
        {
            // 退出阶段忽略清理异常
        }

        try
        {
            _backdropHost?.Dispose();
        }
        catch
        {
            // 退出阶段忽略清理异常
        }

        AppHost.Instance.Shutdown();
    }

    /// <summary>最小可用的 ICommand 包装（供托盘双击使用）。</summary>
    private sealed class RelayCommand : System.Windows.Input.ICommand
    {
        private readonly Action _action;

        public RelayCommand(Action action) => _action = action;

        /// <summary>ICommand 要求实现，本场景不需要变更通知。</summary>
        public event EventHandler? CanExecuteChanged
        {
            add { }
            remove { }
        }

        public bool CanExecute(object? parameter) => true;

        public void Execute(object? parameter) => _action();
    }

    private void ResizeWindow()
    {
        try
        {
            AppWindow.Resize(new SizeInt32(InitialWidth, InitialHeight));
        }
        catch
        {
            // 窗口尺寸调整失败不影响启动。
        }
    }

    /// <summary>
    /// 应用当前选择的窗口背景材质（云母 / 亚克力），并让标题栏系统按钮透明。
    /// 系统不支持所选材质时回退主题纯色，避免透明区域露黑底。
    /// </summary>
    private void ApplyBackdrop()
    {
        var applied = _backdropHost?.Apply() ?? false;

        RootGrid.Background = applied
            ? null
            : Application.Current.Resources.TryGetValue("ApplicationPageBackgroundThemeBrush", out var brush)
                ? brush as Brush
                : null;

        // 自定义标题栏上让系统按钮背景透明，避免出现一块突兀的底色。
        try
        {
            if (AppWindowTitleBar.IsCustomizationSupported())
            {
                var titleBar = AppWindow.TitleBar;
                titleBar.ButtonBackgroundColor = Colors.Transparent;
                titleBar.ButtonInactiveBackgroundColor = Colors.Transparent;
            }
        }
        catch
        {
            // Windows 10 等不支持标题栏自定义的环境忽略。
        }
    }
}
