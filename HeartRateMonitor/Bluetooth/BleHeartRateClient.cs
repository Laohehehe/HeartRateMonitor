// ============================================================================
// 心率带连接与实时数据订阅（对应 Python: heart/monitor.py 的 _monitor_heart_rate）
//   · 后台循环：按地址定位设备 → 取 GATT 服务 → 找心率特征值 → 订阅通知 → 解析心率；
//   · 断线（ConnectionStatusChanged / GattSession.SessionStatusChanged）后等 5 秒自动重连；
//   · StartAsync 只负责启动循环、立即返回；StopAsync 幂等，停止后不再触发任何事件
//     （通过"代际 generation"使旧循环的所有回调失效）。
// ============================================================================
using System.Diagnostics;
using System.Globalization;
using HeartRateMonitor.Core;
using Windows.Devices.Bluetooth;
using Windows.Devices.Bluetooth.GenericAttributeProfile;
using Windows.Foundation;
using Windows.Storage.Streams;

namespace HeartRateMonitor.Bluetooth;

/// <summary>
/// 心率带连接与实时数据订阅（对应 Python: monitor._monitor_heart_rate 整个循环）。
///
/// 【契约】由 BLE 模块负责实现，其它模块只依赖下列签名，不要修改签名。
/// 行为要求（与原版一致）：
///  - StartAsync 启动一个后台监控循环（不阻塞调用方），循环内：
///      a) 按地址定位设备（超时 10s，找不到则抛"设备不在范围内"）；
///      b) 连接 GATT，查找 Heart Rate Measurement 特征值
///         （uuid 00002a37-0000-1000-8000-00805f9b34fb 或描述含 "Heart Rate Measurement"）；
///      c) 订阅通知，解析数据：flags = data[0]，flags & 0x01 == 0 时 hr = data[1]，
///         否则 hr = little-endian uint16(data[1..3])；
///      d) 每次收到数据触发 HeartRateReceived(hr)，并追加到历史（由上层处理）；
///      e) 连接成功触发 ConnectionChanged(true) + StatusChanged("--")；断开触发 ConnectionChanged(false)
///         + StatusChanged("已断连")；
///      f) 异常时通过 StatusChanged 报告原因，并等待 ReconnectDelaySeconds(5s) 后自动重连，直到 StopAsync；
///  - StopAsync 必须幂等，且保证循环退出后不再触发任何事件。
/// </summary>
public sealed class BleHeartRateClient : IAsyncDisposable
{
    /// <summary>心率特征值 UUID（Heart Rate Measurement），兼容 16 位短 UUID 的完整写法。</summary>
    private static readonly Guid HeartRateMeasurementGuid = Guid.Parse(AppConstants.HeartRateMeasurementUuid);

    /// <summary>心率服务 UUID（Heart Rate Service），用于优先遍历。</summary>
    private static readonly Guid HeartRateServiceGuid = Guid.Parse(AppConstants.HeartRateServiceUuid);

    /// <summary>按地址定位设备的最长等待时间（与原版 find_device_by_address timeout=10.0 一致）。</summary>
    private static readonly TimeSpan LocateDeviceTimeout = TimeSpan.FromSeconds(10);

    /// <summary>单次 GATT 操作的超时时间，避免 WinRT 调用永久挂起导致无法停止。</summary>
    private static readonly TimeSpan GattOperationTimeout = TimeSpan.FromSeconds(10);

    /// <summary>StopAsync 等待后台循环退出的上限。</summary>
    private static readonly TimeSpan StopWaitTimeout = TimeSpan.FromSeconds(3);

    /// <summary>定位设备失败时的重试间隔。</summary>
    private static readonly TimeSpan LocateRetryInterval = TimeSpan.FromMilliseconds(400);

    /// <summary>48 位蓝牙地址的最大值。</summary>
    private const ulong MaxBluetoothAddress = 0xFFFF_FFFF_FFFFUL;

    private readonly object _gate = new();

    private volatile bool _isConnected;
    private volatile bool _isMonitoring;

    /// <summary>已释放标记。</summary>
    private bool _disposed;

    /// <summary>监控代际：只有当前代际的循环才允许更新状态、触发事件。</summary>
    private int _generation;

    private CancellationTokenSource? _loopCts;
    private Task? _loopTask;

    /// <summary>收到一个心率值（bpm）。</summary>
    public event EventHandler<int>? HeartRateReceived;

    /// <summary>连接状态变化：true=已连接，false=已断开。</summary>
    public event EventHandler<bool>? ConnectionChanged;

    /// <summary>状态文本（"--"、"已断连"、错误原因等），供悬浮窗展示。</summary>
    public event EventHandler<string>? StatusChanged;

    /// <summary>重连循环是否在运行（可驱动 UI 忙碌状态）。</summary>
    public event EventHandler<bool>? MonitoringChanged;

    /// <summary>连接/维持连接过程中发生错误（参数为面向用户的简短原因）；上层据此播放失败音效。</summary>
    public event EventHandler<string>? ConnectionFailed;

    public bool IsConnected => _isConnected;

    public bool IsMonitoring => _isMonitoring;

    public BleDeviceInfo? CurrentDevice { get; private set; }

    /// <summary>开始监控（内部自动重连）。立即返回，真正的连接在后台循环里完成。</summary>
    public Task StartAsync(BleDeviceInfo device, CancellationToken cancellationToken = default)
    {
        ArgumentNullException.ThrowIfNull(device);
        if (_disposed)
            throw new ObjectDisposedException(nameof(BleHeartRateClient));

        CancellationTokenSource cts;
        CancellationTokenSource? previousCts;
        int generation;

        lock (_gate)
        {
            previousCts = _loopCts;

            // 代际递增：旧循环的后续回调全部失效，避免两轮监控交叉触发事件
            _generation++;
            generation = _generation;

            cts = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
            _loopCts = cts;
            _isConnected = false;
            _isMonitoring = true;
            CurrentDevice = device;
            _loopTask = Task.Run(() => RunMonitorLoopAsync(device, generation, cts), CancellationToken.None);
        }

        if (previousCts is not null)
        {
            try { previousCts.Cancel(); }
            catch (ObjectDisposedException) { }
            catch (Exception ex) { AppLogger.Debug($"停止上一轮监控失败: {ex.Message}"); }
        }

        AppLogger.Info($"开始监控设备: {device.DisplayText}（地址 {device.Address}）");
        TryInvoke(MonitoringChanged, true);

        // 立即返回：连接/订阅/重连全部在后台任务中完成，不阻塞 UI 线程
        return Task.CompletedTask;
    }

    /// <summary>停止监控并断开连接（幂等）。返回时后台循环已退出（最多等待 3 秒）。</summary>
    public async Task StopAsync()
    {
        CancellationTokenSource? cts;
        Task? loopTask;
        bool wasMonitoring;

        lock (_gate)
        {
            cts = _loopCts;
            loopTask = _loopTask;
            _loopCts = null;
            _loopTask = null;

            // 提升代际 + 复位状态：此后后台循环的所有状态更新与事件均被忽略
            _generation++;
            wasMonitoring = _isMonitoring;
            _isMonitoring = false;
            _isConnected = false;
        }

        if (cts is null && loopTask is null)
        {
            // 已经停止过：只补一次监控状态事件，保证幂等
            if (wasMonitoring) TryInvoke(MonitoringChanged, false);
            return;
        }

        AppLogger.Info("正在停止心率监控");
        if (wasMonitoring) TryInvoke(MonitoringChanged, false);

        try { cts?.Cancel(); }
        catch (ObjectDisposedException) { }
        catch (Exception ex) { AppLogger.Debug($"取消监控循环失败: {ex.Message}"); }

        if (loopTask is not null)
        {
            try
            {
                await loopTask.WaitAsync(StopWaitTimeout).ConfigureAwait(false);
            }
            catch (TimeoutException)
            {
                AppLogger.Warn("等待蓝牙监控循环退出超时（后台可能仍在释放 GATT 资源）");
            }
            catch (OperationCanceledException) { }
            catch (Exception ex)
            {
                AppLogger.Debug($"等待监控循环退出时异常: {ex.Message}");
            }
        }

        try { cts?.Dispose(); }
        catch (Exception ex) { AppLogger.Debug($"释放监控取消源失败: {ex.Message}"); }

        AppLogger.Info("心率监控已停止");
    }

    public async ValueTask DisposeAsync()
    {
        try
        {
            await StopAsync().ConfigureAwait(false);
        }
        catch (Exception ex)
        {
            AppLogger.Debug($"释放 BLE 客户端时异常: {ex.Message}");
        }

        _disposed = true;
        AppLogger.Debug("BLE 客户端已释放");
    }

    // ===================================================================== 后台监控循环

    /// <summary>后台任务入口：包裹循环，保证异常不外泄、结束时收尾状态。</summary>
    private async Task RunMonitorLoopAsync(BleDeviceInfo device, int generation, CancellationTokenSource cts)
    {
        try
        {
            await MonitorLoopAsync(device, generation, cts.Token).ConfigureAwait(false);
        }
        catch (OperationCanceledException)
        {
            // 停止监控导致的正常取消
        }
        catch (Exception ex)
        {
            AppLogger.Error($"蓝牙监控循环异常退出: {device.DisplayText}", ex);
            RaiseConnectionFailed(generation, DescribeFailure(ex));
        }
        finally
        {
            var notifyStopped = false;
            lock (_gate)
            {
                if (ReferenceEquals(_loopCts, cts)) _loopCts = null;

                // 仅当仍是当前代际（即非 StopAsync 触发的退出）才自行收尾
                if (_generation == generation)
                {
                    _isConnected = false;
                    _isMonitoring = false;
                    notifyStopped = true;
                }
            }

            try { cts.Dispose(); }
            catch (Exception ex) { AppLogger.Debug($"释放取消源失败: {ex.Message}"); }

            if (notifyStopped) TryInvoke(MonitoringChanged, false);
            AppLogger.Info("蓝牙监控循环已退出");
        }
    }

    /// <summary>重连循环：连接成功则等待断开；任何异常均通过 ConnectionFailed 报告后等待 5 秒重试。</summary>
    private async Task MonitorLoopAsync(BleDeviceInfo device, int generation, CancellationToken ct)
    {
        var attempt = 0;

        while (!ct.IsCancellationRequested && IsCurrent(generation))
        {
            attempt++;
            var reachedConnected = false;
            string? failure = null;

            try
            {
                await RunSessionAsync(device, generation, ct, () => reachedConnected = true).ConfigureAwait(false);
            }
            catch (OperationCanceledException) when (ct.IsCancellationRequested)
            {
                break;
            }
            catch (Exception ex)
            {
                failure = DescribeFailure(ex);
                AppLogger.Warn($"连接心率设备失败（第 {attempt} 次尝试）: {failure}");
            }

            if (reachedConnected)
            {
                // 连接期间断开：回到未连接状态并提示（与原版一致）
                SetConnectedState(generation, false);
                RaiseStatus(generation, "已断连");
                AppLogger.Info("蓝牙设备断开连接");
            }

            if (failure is not null)
            {
                // 上层据此播放失败音效，并展示"X 秒后重试"
                SetConnectedState(generation, false);
                RaiseConnectionFailed(generation, failure);
            }

            if (ct.IsCancellationRequested || !IsCurrent(generation)) break;

            AppLogger.Debug($"{AppConstants.ReconnectDelaySeconds} 秒后重连心率设备");
            try
            {
                await Task.Delay(TimeSpan.FromSeconds(AppConstants.ReconnectDelaySeconds), ct).ConfigureAwait(false);
            }
            catch (OperationCanceledException)
            {
                break;
            }
        }

        AppLogger.Info("心率监控循环已结束");
    }

    /// <summary>
    /// 一次完整的连接会话：定位设备 → 取 GATT 服务 → 订阅心率通知 → 等待断开。
    /// 成功订阅后调用 <paramref name="onConnected"/>；断开/异常返回后由调用方收尾。
    /// </summary>
    private async Task RunSessionAsync(
        BleDeviceInfo device,
        int generation,
        CancellationToken ct,
        Action onConnected)
    {
        BluetoothLEDevice? bleDevice = null;
        GattSession? gattSession = null;
        var services = new List<GattDeviceService>();
        GattCharacteristic? hrCharacteristic = null;

        TypedEventHandler<GattCharacteristic, GattValueChangedEventArgs>? valueHandler = null;
        TypedEventHandler<BluetoothLEDevice, object>? connectionHandler = null;
        TypedEventHandler<GattSession, GattSessionStatusChangedEventArgs>? sessionHandler = null;

        // 断线信号：三种来源（设备连接状态 / GATT 会话关闭）都汇入这里
        var disconnected = new TaskCompletionSource<string>(TaskCreationOptions.RunContinuationsAsynchronously);

        try
        {
            // ---------- 1) 按地址定位设备（10 秒超时，为空视为不在范围内） ----------
            bleDevice = await LocateDeviceAsync(device, ct).ConfigureAwait(false);
            AppLogger.Debug($"已定位蓝牙设备: {device.DisplayText}");

            connectionHandler = (sender, _) =>
            {
                try
                {
                    if (sender.ConnectionStatus == BluetoothConnectionStatus.Disconnected)
                        disconnected.TrySetResult("设备连接已断开");
                }
                catch (Exception ex) { AppLogger.Debug($"读取设备连接状态失败: {ex.Message}"); }
            };
            bleDevice.ConnectionStatusChanged += connectionHandler;

            // ---------- 2) 取 GATT 服务 ----------
            var servicesResult = await bleDevice.GetGattServicesAsync(BluetoothCacheMode.Uncached)
                .AsTask()
                .WaitAsync(GattOperationTimeout, ct)
                .ConfigureAwait(false);

            if (servicesResult.Status != GattCommunicationStatus.Success)
                throw new InvalidOperationException(BuildGattFailure("获取 GATT 服务失败", servicesResult.Status));

            services.AddRange(servicesResult.Services);
            if (services.Count == 0)
                throw new InvalidOperationException("设备没有可用的 GATT 服务");

            // ---------- 3) 找心率特征值并订阅 ----------
            hrCharacteristic = await FindHeartRateCharacteristicAsync(services, ct).ConfigureAwait(false)
                               ?? throw new InvalidOperationException("未找到心率特征值");

            var descriptorValue = SelectNotificationValue(hrCharacteristic);
            var cccStatus = await hrCharacteristic
                .WriteClientCharacteristicConfigurationDescriptorAsync(descriptorValue)
                .AsTask()
                .WaitAsync(GattOperationTimeout, ct)
                .ConfigureAwait(false);

            if (cccStatus != GattCommunicationStatus.Success)
                throw new InvalidOperationException(BuildGattFailure("订阅心率通知失败", cccStatus));

            valueHandler = (_, args) => OnHeartRateValueReceived(args, generation);
            hrCharacteristic.ValueChanged += valueHandler;

            // GATT 会话：部分设备断开时不触发 ConnectionStatusChanged，用会话状态兜底
            gattSession = await TryOpenGattSessionAsync(bleDevice, ct).ConfigureAwait(false);
            if (gattSession is not null)
            {
                sessionHandler = (_, args) =>
                {
                    try
                    {
                        if (args.Status == GattSessionStatus.Closed)
                            disconnected.TrySetResult("连接会话已关闭");
                    }
                    catch (Exception ex) { AppLogger.Debug($"读取 GATT 会话状态失败: {ex.Message}"); }
                };
                gattSession.SessionStatusChanged += sessionHandler;
            }

            // ---------- 4) 连接成功 ----------
            onConnected();
            SetConnectedState(generation, true);
            RaiseStatus(generation, "--");
            AppLogger.Info($"蓝牙设备连接成功: {device.DisplayText}");

            // ---------- 5) 阻塞等待断开或停止 ----------
            var reason = await disconnected.Task.WaitAsync(ct).ConfigureAwait(false);
            AppLogger.Info($"蓝牙设备连接断开: {device.DisplayText}（{reason}）");
        }
        finally
        {
            await CleanupSessionAsync(
                bleDevice, gattSession, services, hrCharacteristic,
                valueHandler, connectionHandler, sessionHandler).ConfigureAwait(false);
        }
    }

    /// <summary>释放一次连接会话占用的资源（退订回调 → 关闭通知 → 释放服务/设备）。</summary>
    private static async Task CleanupSessionAsync(
        BluetoothLEDevice? bleDevice,
        GattSession? gattSession,
        List<GattDeviceService> services,
        GattCharacteristic? hrCharacteristic,
        TypedEventHandler<GattCharacteristic, GattValueChangedEventArgs>? valueHandler,
        TypedEventHandler<BluetoothLEDevice, object>? connectionHandler,
        TypedEventHandler<GattSession, GattSessionStatusChangedEventArgs>? sessionHandler)
    {
        // 1) 先脱钩回调，避免清理过程中再次触发事件
        if (hrCharacteristic is not null && valueHandler is not null)
        {
            try { hrCharacteristic.ValueChanged -= valueHandler; }
            catch (Exception ex) { AppLogger.Debug($"退订心率通知回调失败: {ex.Message}"); }
        }

        if (bleDevice is not null && connectionHandler is not null)
        {
            try { bleDevice.ConnectionStatusChanged -= connectionHandler; }
            catch (Exception ex) { AppLogger.Debug($"退订连接状态回调失败: {ex.Message}"); }
        }

        if (gattSession is not null && sessionHandler is not null)
        {
            try { gattSession.SessionStatusChanged -= sessionHandler; }
            catch (Exception ex) { AppLogger.Debug($"退订会话状态回调失败: {ex.Message}"); }
        }

        // 2) 关闭通知（尽力而为；不受监控取消影响，自带 2 秒超时）
        if (hrCharacteristic is not null)
        {
            try
            {
                await hrCharacteristic
                    .WriteClientCharacteristicConfigurationDescriptorAsync(GattClientCharacteristicConfigurationDescriptorValue.None)
                    .AsTask()
                    .WaitAsync(TimeSpan.FromSeconds(2), CancellationToken.None)
                    .ConfigureAwait(false);
            }
            catch (Exception ex) { AppLogger.Debug($"关闭心率通知失败: {ex.Message}"); }
        }

        if (gattSession is not null)
        {
            try { gattSession.Dispose(); }
            catch (Exception ex) { AppLogger.Debug($"释放 GATT 会话失败: {ex.Message}"); }
        }

        // 3) 释放 GATT 服务与设备（顺序：服务 → 设备）
        foreach (var service in services)
        {
            try { service.Dispose(); }
            catch (Exception ex) { AppLogger.Debug($"释放 GATT 服务失败: {ex.Message}"); }
        }
        services.Clear();

        if (bleDevice is not null)
        {
            try { bleDevice.Dispose(); }
            catch (Exception ex) { AppLogger.Debug($"释放蓝牙设备失败: {ex.Message}"); }
        }
    }

    // ===================================================================== 设备定位 / GATT

    /// <summary>按蓝牙地址定位设备：10 秒内多次尝试，超时或一直为空则视为"设备不在范围内"。</summary>
    private static async Task<BluetoothLEDevice> LocateDeviceAsync(BleDeviceInfo device, CancellationToken ct)
    {
        var address = device.BluetoothAddress;
        if (address == 0) address = ParseBluetoothAddress(device.Address);
        if (address == 0 || address > MaxBluetoothAddress)
            throw new InvalidOperationException("设备地址无效，无法连接");

        var elapsed = Stopwatch.StartNew();
        while (!ct.IsCancellationRequested)
        {
            var remaining = LocateDeviceTimeout - elapsed.Elapsed;
            if (remaining <= TimeSpan.Zero) break;

            try
            {
                var operation = BluetoothLEDevice.FromBluetoothAddressAsync(address);
                var located = await operation.AsTask()
                    .WaitAsync(remaining, ct)
                    .ConfigureAwait(false);

                if (located is not null) return located;

                // WinRT 首次定位偶尔返回空，稍后重试可明显提升成功率
                AppLogger.Debug("按地址定位设备返回空，稍后重试");
            }
            catch (TimeoutException)
            {
                break;
            }
            catch (OperationCanceledException) when (ct.IsCancellationRequested)
            {
                throw;
            }
            catch (Exception ex)
            {
                AppLogger.Debug($"按地址定位设备异常: {ex.Message}");
            }

            try
            {
                await Task.Delay(LocateRetryInterval, ct).ConfigureAwait(false);
            }
            catch (OperationCanceledException)
            {
                throw;
            }
        }

        throw new InvalidOperationException("设备不在范围内");
    }

    /// <summary>遍历服务/特征值查找心率特征值（优先心率服务）。</summary>
    private static async Task<GattCharacteristic?> FindHeartRateCharacteristicAsync(
        IReadOnlyList<GattDeviceService> services,
        CancellationToken ct)
    {
        var ordered = services
            .OrderByDescending(s => s.Uuid == HeartRateServiceGuid || s.Uuid == GattServiceUuids.HeartRate)
            .ToList();

        foreach (var service in ordered)
        {
            try
            {
                var result = await service.GetCharacteristicsAsync(BluetoothCacheMode.Uncached)
                    .AsTask()
                    .WaitAsync(GattOperationTimeout, ct)
                    .ConfigureAwait(false);

                if (result.Status != GattCommunicationStatus.Success)
                {
                    AppLogger.Debug($"读取服务 {service.Uuid} 的特征值失败：{result.Status}");
                    continue;
                }

                foreach (var characteristic in result.Characteristics)
                {
                    if (IsHeartRateMeasurementCharacteristic(characteristic))
                        return characteristic;
                }
            }
            catch (OperationCanceledException) when (ct.IsCancellationRequested)
            {
                throw;
            }
            catch (TimeoutException)
            {
                AppLogger.Debug($"读取服务 {service.Uuid} 的特征值超时");
            }
            catch (Exception ex)
            {
                AppLogger.Debug($"读取服务 {service.Uuid} 的特征值异常: {ex.Message}");
            }
        }

        return null;
    }

    /// <summary>特征值是否为心率测量（完整 UUID 或短 UUID 常量都算匹配）。</summary>
    private static bool IsHeartRateMeasurementCharacteristic(GattCharacteristic characteristic)
        => characteristic.Uuid == HeartRateMeasurementGuid
           || characteristic.Uuid == GattCharacteristicUuids.HeartRateMeasurement;

    /// <summary>订阅方式：优先 Notify；仅支持 Indicate 的设备退化为 Indicate（否则永远收不到数据）。</summary>
    private static GattClientCharacteristicConfigurationDescriptorValue SelectNotificationValue(
        GattCharacteristic characteristic)
    {
        try
        {
            var properties = characteristic.CharacteristicProperties;
            if ((properties & GattCharacteristicProperties.Notify) != 0)
                return GattClientCharacteristicConfigurationDescriptorValue.Notify;

            if ((properties & GattCharacteristicProperties.Indicate) != 0)
            {
                AppLogger.Warn("心率特征值不支持 Notify，改用 Indicate 订阅");
                return GattClientCharacteristicConfigurationDescriptorValue.Indicate;
            }
        }
        catch (Exception ex)
        {
            AppLogger.Debug($"读取特征值属性失败: {ex.Message}");
        }

        // 属性未知时仍按原版行为尝试 Notify
        return GattClientCharacteristicConfigurationDescriptorValue.Notify;
    }

    /// <summary>打开 GATT 会话（仅用于感知断开；失败不影响订阅）。</summary>
    private static async Task<GattSession?> TryOpenGattSessionAsync(BluetoothLEDevice bleDevice, CancellationToken ct)
    {
        try
        {
            var deviceId = bleDevice.BluetoothDeviceId;
            if (deviceId is null || string.IsNullOrEmpty(deviceId.Id)) return null;

            return await GattSession.FromDeviceIdAsync(BluetoothDeviceId.FromId(deviceId.Id))
                .AsTask()
                .WaitAsync(GattOperationTimeout, ct)
                .ConfigureAwait(false);
        }
        catch (OperationCanceledException) when (ct.IsCancellationRequested)
        {
            throw;
        }
        catch (Exception ex)
        {
            AppLogger.Debug($"打开 GATT 会话失败（不影响心率订阅）: {ex.Message}");
            return null;
        }
    }

    // ===================================================================== 心率数据解析

    /// <summary>心率通知回调（后台线程；上层自行 marshal 到 UI 线程）。</summary>
    private void OnHeartRateValueReceived(GattValueChangedEventArgs args, int generation)
    {
        if (!IsCurrent(generation)) return;

        try
        {
            var buffer = args.CharacteristicValue;
            var data = new byte[buffer.Length];
            using (var reader = DataReader.FromBuffer(buffer))
            {
                reader.ReadBytes(data);
            }

            var heartRate = TryParseHeartRate(data);
            if (heartRate is null)
            {
                AppLogger.Debug("心率数据解析失败：数据长度不足");
                RaiseStatus(generation, "ERR");
                return;
            }

            TryInvoke(HeartRateReceived, heartRate.Value);
        }
        catch (Exception ex)
        {
            // 异常数据格式不应导致监控循环崩溃
            AppLogger.Debug($"心率数据解析异常: {ex.Message}");
            RaiseStatus(generation, "ERR");
        }
    }

    /// <summary>
    /// 解析 Heart Rate Measurement：flags = data[0]；bit0=0 时 hr = data[1]（uint8），
    /// bit0=1 时 hr = little-endian uint16(data[1..3])。长度不足或无法解析返回 null。
    /// </summary>
    internal static int? TryParseHeartRate(byte[]? data)
    {
        if (data is null || data.Length < 2) return null;

        var flags = data[0];
        if ((flags & 0x01) == 0) return data[1];

        if (data.Length < 3) return null;
        return data[1] | (data[2] << 8);
    }

    // ===================================================================== 状态与事件

    /// <summary>当前代际是否仍是活动监控（否则回调一律丢弃）。</summary>
    private bool IsCurrent(int generation) => Volatile.Read(ref _generation) == generation;

    /// <summary>更新连接状态，仅在状态真正变化时触发 ConnectionChanged。</summary>
    private void SetConnectedState(int generation, bool connected)
    {
        bool changed;
        lock (_gate)
        {
            if (_generation != generation) return;
            changed = _isConnected != connected;
            _isConnected = connected;
        }

        if (changed) TryInvoke(ConnectionChanged, connected);
    }

    /// <summary>触发状态文本（停止后自动丢弃）。</summary>
    private void RaiseStatus(int generation, string text)
    {
        if (!IsCurrent(generation)) return;
        TryInvoke(StatusChanged, text);
    }

    /// <summary>触发连接失败（停止后自动丢弃）。</summary>
    private void RaiseConnectionFailed(int generation, string reason)
    {
        if (!IsCurrent(generation)) return;
        AppLogger.Warn($"心率连接失败: {reason}");
        TryInvoke(ConnectionFailed, reason);
    }

    /// <summary>安全触发事件：处理器异常不影响监控循环。订阅方在任意线程接收。</summary>
    private void TryInvoke<T>(EventHandler<T>? handler, T value)
    {
        if (handler is null) return;
        try
        {
            handler(this, value);
        }
        catch (Exception ex)
        {
            AppLogger.Debug($"心率事件处理器异常: {ex.Message}");
        }
    }

    // ===================================================================== 工具方法

    /// <summary>把 WinRT 异常/GATT 状态转成面向用户的简短中文原因。</summary>
    private static string DescribeFailure(Exception ex)
    {
        var message = ex switch
        {
            TimeoutException => "连接超时",
            OperationCanceledException => "连接已取消",
            UnauthorizedAccessException => "没有蓝牙访问权限",
            _ => ex.Message,
        };

        if (string.IsNullOrWhiteSpace(message)) message = ex.GetType().Name;
        if (message.Length > 60) message = message[..60] + "…";
        return message;
    }

    /// <summary>构造 GATT 失败消息；Unreachable 明确提示"不在范围内"。</summary>
    private static string BuildGattFailure(string prefix, GattCommunicationStatus status)
        => status switch
        {
            GattCommunicationStatus.Unreachable => $"{prefix}：设备不在范围内（{status}）",
            GattCommunicationStatus.AccessDenied => $"{prefix}：缺少访问权限（{status}）",
            GattCommunicationStatus.ProtocolError => $"{prefix}：GATT 协议错误（{status}）",
            _ => $"{prefix}：{status}",
        };

    /// <summary>"AA:BB:CC:DD:EE:FF" 转 48 位蓝牙地址。</summary>
    private static ulong ParseBluetoothAddress(string? address)
    {
        if (string.IsNullOrWhiteSpace(address)) return 0u;

        var hex = address.Replace(":", string.Empty).Replace("-", string.Empty).Trim();
        if (hex.Length != 12) return 0u;

        return ulong.TryParse(hex, NumberStyles.HexNumber, CultureInfo.InvariantCulture, out var value)
            ? value
            : 0u;
    }
}
