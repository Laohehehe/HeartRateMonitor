using System;
using HeartRateMonitor.Core;
using Microsoft.UI.Composition;
using Microsoft.UI.Composition.SystemBackdrops;
using Microsoft.UI.Dispatching;
using Microsoft.UI.Xaml;
using WinRT;

namespace HeartRateMonitor.Services;

/// <summary>
/// 把背景材质挂到某个窗口上（对应官方文档的 controller 用法）。
///
/// 之所以不用 <c>Window.SystemBackdrop = new DesktopAcrylicBackdrop()</c>：
/// 那个 XAML 类只暴露无参构造，<b>既不能选 Base/Thin 变体，也不能调色调</b>；
/// 要拿到 <c>DesktopAcrylicKind</c>、<c>TintColor</c>、<c>TintOpacity</c>、
/// <c>LuminosityOpacity</c> 必须走 <see cref="DesktopAcrylicController"/> /
/// <see cref="MicaController"/>。
/// </summary>
public sealed class BackdropHost : IDisposable
{
    private readonly Window _window;
    private readonly FrameworkElement? _content;

    private SystemBackdropConfiguration? _configuration;
    private MicaController? _micaController;
    private DesktopAcrylicController? _acrylicController;
    private bool _disposed;

    public BackdropHost(Window window, FrameworkElement? content)
    {
        _window = window;
        _content = content;

        // 材质会跟随窗口激活状态与主题变化，否则窗口失焦时会整片发灰
        _window.Activated += OnWindowActivated;
        if (_content is not null) _content.ActualThemeChanged += OnThemeChanged;
    }

    /// <summary>当前是否成功挂上了系统材质。</summary>
    public bool IsMaterialActive { get; private set; }

    /// <summary>按当前配置应用材质；返回是否成功（false 表示应回退纯色）。</summary>
    public bool Apply()
    {
        Release();

        var mode = BackdropService.Current;
        if (mode == BackdropService.Solid)
        {
            IsMaterialActive = false;
            return false;
        }

        // 说明：WinUI 3 是 XAML 应用，UI 线程本就带 DispatcherQueue，
        // 不需要再调 Windows.System.DispatcherQueue.EnsureSystemDispatcherQueue()。
        var configuration = EnsureConfiguration();
        configuration.IsInputActive = true;
        configuration.Theme = ResolveTheme();

        try
        {
            switch (mode)
            {
                case BackdropService.Mica:
                    return AttachMica(configuration, MicaKind.Base);

                case BackdropService.MicaAlt:
                    return AttachMica(configuration, MicaKind.BaseAlt);

                case BackdropService.AcrylicThin:
                    return AttachAcrylic(configuration, DesktopAcrylicKind.Thin);

                case BackdropService.Acrylic:
                default:
                    return AttachAcrylic(configuration, DesktopAcrylicKind.Base);
            }
        }
        catch (Exception ex)
        {
            AppLogger.Warn($"应用背景材质失败（{mode}）: {ex.Message}");
            Release();
            IsMaterialActive = false;
            return false;
        }
    }

    private bool AttachMica(SystemBackdropConfiguration configuration, MicaKind kind)
    {
        if (!MicaController.IsSupported())
        {
            AppLogger.Debug("当前系统不支持 Mica");
            IsMaterialActive = false;
            return false;
        }

        _micaController = new MicaController { Kind = kind };
        _micaController.AddSystemBackdropTarget(_window.As<ICompositionSupportsSystemBackdrop>());
        _micaController.SetSystemBackdropConfiguration(configuration);

        IsMaterialActive = true;
        return true;
    }

    private bool AttachAcrylic(SystemBackdropConfiguration configuration, DesktopAcrylicKind kind)
    {
        if (!DesktopAcrylicController.IsSupported())
        {
            AppLogger.Debug("当前系统不支持亚克力");
            IsMaterialActive = false;
            return false;
        }

        _acrylicController = new DesktopAcrylicController { Kind = kind };
        _acrylicController.AddSystemBackdropTarget(_window.As<ICompositionSupportsSystemBackdrop>());
        _acrylicController.SetSystemBackdropConfiguration(configuration);

        IsMaterialActive = true;
        return true;
    }

    private SystemBackdropConfiguration EnsureConfiguration()
    {
        return _configuration ??= new SystemBackdropConfiguration
        {
            IsInputActive = true,
            Theme = ResolveTheme()
        };
    }

    private SystemBackdropTheme ResolveTheme()
        => _content?.ActualTheme == ElementTheme.Dark
            ? SystemBackdropTheme.Dark
            : SystemBackdropTheme.Light;

    private void OnWindowActivated(object sender, WindowActivatedEventArgs args)
    {
        if (_configuration is not null)
        {
            _configuration.IsInputActive = args.WindowActivationState != WindowActivationState.Deactivated;
        }
    }

    private void OnThemeChanged(FrameworkElement sender, object args)
    {
        if (_configuration is not null) _configuration.Theme = ResolveTheme();
    }

    private void Release()
    {
        try
        {
            if (_micaController is not null)
            {
                _micaController.RemoveAllSystemBackdropTargets();
                _micaController.Dispose();
                _micaController = null;
            }
        }
        catch (Exception ex)
        {
            AppLogger.Debug($"释放 Mica 控制器失败: {ex.Message}");
        }

        try
        {
            if (_acrylicController is not null)
            {
                _acrylicController.RemoveAllSystemBackdropTargets();
                _acrylicController.Dispose();
                _acrylicController = null;
            }
        }
        catch (Exception ex)
        {
            AppLogger.Debug($"释放亚克力控制器失败: {ex.Message}");
        }

        IsMaterialActive = false;
    }

    public void Dispose()
    {
        if (_disposed) return;
        _disposed = true;

        Release();

        _window.Activated -= OnWindowActivated;
        if (_content is not null) _content.ActualThemeChanged -= OnThemeChanged;
    }
}
