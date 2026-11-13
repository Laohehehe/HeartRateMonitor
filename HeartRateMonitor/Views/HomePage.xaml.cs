using System;
using System.Collections.ObjectModel;
using System.Linq;
using HeartRateMonitor.Bluetooth;
using HeartRateMonitor.Core;
using HeartRateMonitor.Services;
using Microsoft.UI;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Media.Animation;

namespace HeartRateMonitor.Views;

/// <summary>
/// 初始页面：BLE 设备扫描 + 连接 + 实时心率显示。
/// 扫描与连接直接复用归档版 BluetoothScanner / BleHeartRateClient（零改动）。
/// </summary>
public sealed partial class HomePage : Page
{
    private readonly ObservableCollection<BleDeviceInfo> _devices = new();

    private readonly SolidColorBrush _connectedBrush;
    private readonly SolidColorBrush _idleBrush;
    private readonly SolidColorBrush _failedBrush;

    private bool _scanning;

    public HomePage()
    {
        InitializeComponent();

        _connectedBrush = new SolidColorBrush(ColorHelper.FromArgb(255, 0x3F, 0xB9, 0x50));
        _idleBrush = new SolidColorBrush(ColorHelper.FromArgb(255, 0x8A, 0x8A, 0x8A));
        _failedBrush = new SolidColorBrush(ColorHelper.FromArgb(255, 0xF8, 0x51, 0x49));

        DeviceList.ItemsSource = _devices;

        Loaded += OnLoaded;
        Unloaded += OnUnloaded;
    }

    // ---------------- 生命周期 ----------------

    private void OnLoaded(object sender, RoutedEventArgs e)
    {
        RestartAnimations();

        var host = AppHost.Instance;

        // 先退订再订阅，避免 Loaded 重复触发导致重复处理
        host.HeartRateUpdated -= OnHeartRateUpdated;
        host.ConnectionStateChanged -= OnConnectionStateChanged;
        host.StatusChanged -= OnStatusChanged;
        host.ConnectionFailed -= OnConnectionFailed;

        host.HeartRateUpdated += OnHeartRateUpdated;
        host.ConnectionStateChanged += OnConnectionStateChanged;
        host.StatusChanged += OnStatusChanged;
        host.ConnectionFailed += OnConnectionFailed;
        host.PinnedChanged += OnPinnedStateChanged;

        TopMostPinCheck.IsChecked = host.IsPinned;
        UpdateConnectionUi(host.IsConnected);
        UpdateEmptyState();
    }

    private void OnUnloaded(object sender, RoutedEventArgs e)
    {
        var host = AppHost.Instance;
        host.HeartRateUpdated -= OnHeartRateUpdated;
        host.ConnectionStateChanged -= OnConnectionStateChanged;
        host.StatusChanged -= OnStatusChanged;
        host.ConnectionFailed -= OnConnectionFailed;
        host.PinnedChanged -= OnPinnedStateChanged;
    }

    // ---------------- 扫描 ----------------

    private async void OnScanClick(object sender, RoutedEventArgs e)
    {
        if (_scanning) return;

        _scanning = true;
        ScanButton.IsEnabled = false;
        ScanStatusText.Text = "扫描中…";
        _devices.Clear();
        UpdateEmptyState();

        try
        {
            // BluetoothScanner 内部已做 Task.Run + 超时兜底，不阻塞 UI 线程
            var found = await BluetoothScanner.ScanAsync();

            // 与旧版一致：过滤无名 / 名称等于地址的无效条目
            var filtered = found
                .Where(d => !string.IsNullOrWhiteSpace(d.Name)
                            && !string.Equals(d.Name, d.Address, StringComparison.OrdinalIgnoreCase))
                .ToList();

            foreach (var device in filtered) _devices.Add(device);

            ScanStatusText.Text = filtered.Count == 0
                ? "未发现设备"
                : $"发现 {filtered.Count} 个设备";

            if (filtered.Count == 0)
            {
                ShowNotice("未扫描到设备。请确认电脑蓝牙已开启，且心率设备处于广播状态。", InfoBarSeverity.Warning);
            }
        }
        catch (Exception ex)
        {
            AppLogger.Warn($"扫描设备失败: {ex.Message}");
            ScanStatusText.Text = "扫描失败";
            ShowNotice($"扫描失败：{ex.Message}", InfoBarSeverity.Error);
        }
        finally
        {
            _scanning = false;
            ScanButton.IsEnabled = true;
            UpdateEmptyState();
            UpdateButtonState();
        }
    }

    // ---------------- 连接 ----------------

    private async void OnConnectClick(object sender, RoutedEventArgs e)
    {
        var host = AppHost.Instance;
        ConnectButton.IsEnabled = false;

        try
        {
            if (host.Client.IsMonitoring)
            {
                await host.DisconnectAsync();
                UpdateConnectionUi(false);
                return;
            }

            if (DeviceList.SelectedItem is not BleDeviceInfo device)
            {
                ShowNotice("请先在列表中选择一个设备。", InfoBarSeverity.Warning);
                return;
            }

            HeartHintText.Text = "正在连接…";
            ConnectionStateText.Text = "连接中…";
            StatusDot.Fill = _idleBrush;

            await host.ConnectAsync(device);

            AppLogger.Info($"已发起连接: {device.DisplayText}");
        }
        finally
        {
            UpdateButtonState();
        }
    }

    private void OnDeviceSelectionChanged(object sender, SelectionChangedEventArgs e) => UpdateButtonState();

    // ---------------- AppHost 事件 ----------------

    private void OnHeartRateUpdated(object? sender, int value)
    {
        HeartRateText.Text = value.ToString();
        HeartRateText.Opacity = 1;

        // 心率变化超过 3 bpm 才重启动画，避免每次读数都重建 Storyboard
        if (_bpm is null || Math.Abs(_bpm.Value - value) > 3)
        {
            _bpm = value;
            RestartAnimations();
        }
    }

    private void OnConnectionStateChanged(object? sender, bool connected)
    {
        UpdateConnectionUi(connected);
    }

    private void OnStatusChanged(object? sender, string text)
    {
        switch (text)
        {
            case "ERR":
                HeartRateText.Text = "ERR";
                HeartRateText.Opacity = 1;
                break;

            case "已断连":
                HeartRateText.Text = "--";
                HeartRateText.Opacity = 0.4;
                HeartHintText.Text = $"设备已断连，将在 {AppConstants.ReconnectDelaySeconds} 秒后自动重连…";
                StatusDot.Fill = _failedBrush;
                ConnectionStateText.Text = "已断连";
                break;
        }
    }

    private void OnConnectionFailed(object? sender, string reason)
    {
        HeartHintText.Text = $"{reason}（{AppConstants.ReconnectDelaySeconds} 秒后重试）";
        StatusDot.Fill = _failedBrush;
        ConnectionStateText.Text = "连接失败";
        ShowNotice($"连接失败：{reason}", InfoBarSeverity.Warning);
    }

    // ---------------- 交互 ----------------

    /// <summary>正在按外部状态回填复选框时不回写，避免与托盘菜单互相触发。</summary>
    private bool _syncingPin;

    private void OnPinnedStateChanged(object? sender, bool pinned)
    {
        _syncingPin = true;
        TopMostPinCheck.IsChecked = pinned;
        _syncingPin = false;
    }

    private void OnPinnedChanged(object sender, RoutedEventArgs e)
    {
        if (_syncingPin) return;

        AppHost.Instance.SetPinned(TopMostPinCheck.IsChecked == true);
    }

    // ---------------- UI 状态 ----------------

    private void UpdateConnectionUi(bool connected)
    {
        StatusDot.Fill = connected ? _connectedBrush : _idleBrush;
        ConnectionStateText.Text = connected ? "已连接" : "未连接";
        HeartHintText.Text = connected ? "正在接收心率数据" : "连接设备后显示实时心率";

        if (!connected)
        {
            HeartRateText.Text = "--";
            HeartRateText.Opacity = 0.4;
        }

        UpdateButtonState();
    }

    private void UpdateButtonState()
    {
        var monitoring = AppHost.Instance.Client.IsMonitoring;
        ConnectButton.Content = monitoring ? "断开连接" : "连接设备";
        ConnectButton.IsEnabled = monitoring || DeviceList.SelectedItem is not null;
        ScanButton.IsEnabled = !_scanning;
    }

    private void UpdateEmptyState()
    {
        EmptyHint.Visibility = _devices.Count == 0 ? Visibility.Visible : Visibility.Collapsed;
    }

    private void ShowNotice(string message, InfoBarSeverity severity)
    {
        NoticeBar.Severity = severity;
        NoticeBar.Message = message;
        NoticeBar.IsOpen = true;
    }

    // ---------------- 动画 ----------------

    private Storyboard? _pulseStoryboard;
    private int? _bpm;

    /// <summary>
    /// 按当前 BPM 重启心形搏动动画：一个完整周期 = 60/BPM 秒（90 bpm 比 60 bpm 快一倍）。
    /// </summary>
    private void RestartAnimations()
    {
        var bpm = Math.Clamp(_bpm ?? 72, 30, 220);
        var beatMs = 60000.0 / bpm;

        _pulseStoryboard?.Stop();
        var pulse = new Storyboard { RepeatBehavior = RepeatBehavior.Forever };
        foreach (var property in new[] { "ScaleX", "ScaleY" })
        {
            var animation = new DoubleAnimation
            {
                From = 1.0,
                To = 1.22,
                Duration = new Duration(TimeSpan.FromMilliseconds(Math.Max(beatMs / 2, 90))),
                AutoReverse = true,
                EasingFunction = new SineEase { EasingMode = EasingMode.EaseInOut }
            };

            Storyboard.SetTarget(animation, HeartScale);
            Storyboard.SetTargetProperty(animation, property);
            pulse.Children.Add(animation);
        }

        _pulseStoryboard = pulse;
        pulse.Begin();
    }
}
