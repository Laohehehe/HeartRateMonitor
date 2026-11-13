using System;
using HeartRateMonitor.Services;
using Microsoft.UI.Dispatching;
using Microsoft.UI.Xaml;

namespace HeartRateMonitor;

/// <summary>
/// 应用程序入口。初始化编排层并创建主窗口。
/// 支持 <c>--demo</c>：离线演示模式（每秒生成模拟心率，用于查看悬浮窗与曲线）。
/// </summary>
public partial class App : Application
{
    private Window? _window;

    public App()
    {
        InitializeComponent();
    }

    protected override void OnLaunched(LaunchActivatedEventArgs args)
    {
        // 必须先于任何 UI 订阅完成接线（AppHost 内部把 BLE 事件 marshal 回 UI 线程）
        AppHost.Instance.Initialize(DispatcherQueue.GetForCurrentThread());

        _window = new MainWindow();
        _window.Activate();

        if (IsDemoRequested())
        {
            AppHost.Instance.StartDemoMode();
        }
    }

    /// <summary>命令行是否带 --demo。</summary>
    private static bool IsDemoRequested()
    {
        foreach (var argument in Environment.GetCommandLineArgs())
        {
            if (string.Equals(argument, "--demo", StringComparison.OrdinalIgnoreCase)) return true;
        }

        return false;
    }
}
