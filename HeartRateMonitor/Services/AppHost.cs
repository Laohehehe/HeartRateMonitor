using System;
using System.Threading.Tasks;
using HeartRateMonitor.Bluetooth;
using HeartRateMonitor.Core;
using HeartRateMonitor.Views;
using Microsoft.UI.Dispatching;

namespace HeartRateMonitor.Services;

/// <summary>
/// 应用编排层（对应归档版 HeartRateMonitorApp.cs 的核心职责）。
/// 持有 BLE 客户端与历史数据，负责把后台线程事件 marshal 到 UI 线程，
/// 并管理悬浮心率窗的生命周期（曲线由主窗口内的 ChartPage 直接读取 History）。
/// </summary>
public sealed class AppHost
{
    public static AppHost Instance { get; } = new();

    public BleHeartRateClient Client { get; } = new();

    public HeartRateHistory History { get; } = new();

    /// <summary>收到一个心率值（UI 线程）。</summary>
    public event EventHandler<int>? HeartRateUpdated;

    /// <summary>连接状态变化（UI 线程）。</summary>
    public event EventHandler<bool>? ConnectionStateChanged;

    /// <summary>状态文本变化："--" / "已断连" / "ERR" 等（UI 线程）。</summary>
    public event EventHandler<string>? StatusChanged;

    /// <summary>连接失败原因（UI 线程）。</summary>
    public event EventHandler<string>? ConnectionFailed;

    /// <summary>「置顶并固定」状态变化（UI 线程），供托盘菜单与首页复选框同步。</summary>
    public event EventHandler<bool>? PinnedChanged;

    private DispatcherQueue? _ui;
    private HeartOverlayWindow? _floatingWindow;
    private Microsoft.UI.Xaml.DispatcherTimer? _demoTimer;
    private bool _demoMode;
    private bool _initialized;

    private AppHost()
    {
    }

    public bool IsPinned { get; private set; }

    public bool IsConnected => Client.IsConnected;

    public BleDeviceInfo? CurrentDevice => Client.CurrentDevice;

    /// <summary>在 UI 线程初始化一次，接线 BLE 事件。</summary>
    public void Initialize(DispatcherQueue ui)
    {
        if (_initialized) return;
        _initialized = true;
        _ui = ui;

        Client.HeartRateReceived += (_, value) => OnHeartRate(value);

        Client.ConnectionChanged += (_, connected) =>
        {
            Post(() => ConnectionStateChanged?.Invoke(this, connected));

            if (connected)
            {
                Post(() => EnsureFloatingWindow().ShowWithoutActivating());
            }
        };

        Client.StatusChanged += (_, text) =>
        {
            Post(() =>
            {
                _floatingWindow?.SetHeartRateText(text);
                StatusChanged?.Invoke(this, text);
            });
        };

        Client.ConnectionFailed += (_, reason) =>
        {
            Post(() => ConnectionFailed?.Invoke(this, reason));
        };

        AppLogger.Info("AppHost 已初始化");
    }

    /// <summary>连接指定设备（立即返回，真正的连接在后台循环里完成）。</summary>
    public async Task ConnectAsync(BleDeviceInfo device)
    {
        History.Clear();
        EnsureFloatingWindow();
        await Client.StartAsync(device).ConfigureAwait(true);
    }

    /// <summary>断开当前设备（幂等）。</summary>
    public async Task DisconnectAsync()
    {
        await Client.StopAsync().ConfigureAwait(true);
        Post(() => _floatingWindow?.SetHeartRateText("--"));
    }

    /// <summary>置顶并固定（固定后悬浮窗鼠标穿透、不可拖动）。</summary>
    public void SetPinned(bool pinned)
    {
        IsPinned = pinned;
        EnsureFloatingWindow().SetFixed(pinned);
        PinnedChanged?.Invoke(this, pinned);
    }

    /// <summary>退出清理：停监控、关窗、关日志。不阻塞 UI 线程（进程马上退出，BLE 资源由系统回收）。</summary>
    public void Shutdown()
    {
        try
        {
            // 不等待：避免退出时阻塞 UI 线程
            _ = Client.StopAsync();
        }
        catch (Exception ex)
        {
            AppLogger.Debug($"退出时停止监控失败: {ex.Message}");
        }

        try
        {
            _floatingWindow?.SavePosition();
            _floatingWindow?.Close();
        }
        catch (Exception ex)
        {
            AppLogger.Debug($"退出时关闭窗口失败: {ex.Message}");
        }

        AppLogger.Info("AppHost 已关闭");
        AppLogger.Shutdown();
    }

    private HeartOverlayWindow EnsureFloatingWindow()
    {
        if (_floatingWindow is not null) return _floatingWindow;

        _floatingWindow = new HeartOverlayWindow();
        _floatingWindow.SetFixed(IsPinned);
        return _floatingWindow;
    }

    /// <summary>把一次心率读数送进统一管线（历史 + 悬浮窗 + 页面）。</summary>
    private void OnHeartRate(int value)
    {
        History.Add(value);
        Post(() =>
        {
            _floatingWindow?.SetHeartRate(value);
            HeartRateUpdated?.Invoke(this, value);
        });
    }

    /// <summary>
    /// 离线演示模式（对应归档版 HeartRateMonitor.exe --demo）：
    /// 每秒生成一个模拟心率值，用于在没有心率设备时查看悬浮窗与曲线效果。
    /// </summary>
    public void StartDemoMode()
    {
        if (_demoMode) return;
        _demoMode = true;

        var random = new Random();
        var value = 82;

        _demoTimer = new Microsoft.UI.Xaml.DispatcherTimer
        {
            Interval = TimeSpan.FromSeconds(1)
        };
        _demoTimer.Tick += (_, _) =>
        {
            value = Math.Clamp(value + random.Next(-4, 5), 58, 132);
            OnHeartRate(value);
        };
        _demoTimer.Start();

        Post(() =>
        {
            EnsureFloatingWindow().ShowWithoutActivating();
            ConnectionStateChanged?.Invoke(this, true);
            StatusChanged?.Invoke(this, "--");
        });

        AppLogger.Info("已进入离线演示模式（--demo）：每秒生成一个模拟心率值");
    }

    /// <summary>把回调投递到 UI 线程（已在 UI 线程则直接执行）。</summary>
    private void Post(Action action)
    {
        var queue = _ui;
        if (queue is null || queue.HasThreadAccess)
        {
            action();
            return;
        }

        queue.TryEnqueue(() => action());
    }
}
