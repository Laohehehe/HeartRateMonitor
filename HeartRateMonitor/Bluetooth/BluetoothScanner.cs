// ============================================================================
// BLE 设备扫描（对应 Python: heart/monitor.py 的 _scan_and_update → BleakScanner.discover）
//   · BluetoothLEAdvertisementWatcher(Active) 收集广播：按蓝牙地址合并，
//     保留最新 RSSI 与非空 LocalName；
//   · 已配对设备兜底：DeviceInformation.FindAllAsync(BluetoothLEDevice.GetDeviceSelector())
//     把"已配对但当前不广播"的设备并入结果；
//   · 超时后停止扫描并返回已收集结果；任何异常只记录日志，不向上抛。
// ============================================================================
using System.Globalization;
using HeartRateMonitor.Core;
using Windows.Devices.Bluetooth;
using Windows.Devices.Bluetooth.Advertisement;
using Windows.Devices.Enumeration;

namespace HeartRateMonitor.Bluetooth;

/// <summary>
/// BLE 设备扫描（对应 Python: monitor._scan_and_update 里的 BleakScanner.discover）。
///
/// 【契约】由 BLE 模块负责实现，其它模块只依赖下列签名，不要修改签名。
/// 要求：
///  - 使用 Windows.Devices.Bluetooth 的 BluetoothLEAdvertisementWatcher 扫描，收集
///    LocalName / BluetoothAddress / Rssi / IsPaired；
///  - 也需包含"已配对但当前未广播"的设备作为兜底（可用 BluetoothLEDevice.FromBluetoothAddressAsync 探测），
///    保证心率带即使不广播也能出现在列表里；
///  - 过滤规则与原版一致：Name 为空或 Name == Address 的条目**不要**在此过滤（由 UI 过滤更灵活），
///    但本方法应保证 Name 不为 null（空则回填 "未知设备" 之外保持空字符串，UI 侧过滤）；
///  - 超时后必须返回已收集到的结果，不允许抛异常（内部捕获并记录日志，返回空列表）。
/// </summary>
public static class BluetoothScanner
{
    /// <summary>默认扫描时长（与原版 BleakScanner.discover(timeout=2.0) 一致）。</summary>
    private static readonly TimeSpan DefaultScanTimeout = TimeSpan.FromSeconds(2);

    /// <summary>已配对设备兜底查询的超时时间，避免拖长整体扫描时间。</summary>
    private static readonly TimeSpan PairedQueryTimeout = TimeSpan.FromSeconds(2);

    /// <summary>48 位蓝牙地址的最大值。</summary>
    private const ulong MaxBluetoothAddress = 0xFFFF_FFFF_FFFFUL;

    /// <summary>扫描指定时长（默认 2 秒，与原版一致），返回发现的设备（按信号强度降序）。</summary>
    public static async Task<IReadOnlyList<BleDeviceInfo>> ScanAsync(
        TimeSpan? timeout = null,
        CancellationToken cancellationToken = default)
    {
        var effective = timeout ?? DefaultScanTimeout;
        if (effective <= TimeSpan.Zero) effective = DefaultScanTimeout;

        var results = new List<BleDeviceInfo>();
        try
        {
            // 不用 Task.Run 的取消令牌：即使已取消也要把 ScanCore 内部已收集的结果带回来
            results = await Task.Run(() => ScanCoreAsync(effective, cancellationToken)).ConfigureAwait(false);
        }
        catch (OperationCanceledException)
        {
            AppLogger.Debug("扫描被取消（超时或用户取消）");
        }
        catch (Exception ex)
        {
            AppLogger.Error("扫描设备失败", ex);
        }

        return results
            .Where(d => !string.IsNullOrWhiteSpace(d.Address) || !string.IsNullOrWhiteSpace(d.Name))
            .OrderByDescending(d => d.Rssi)
            .ThenBy(d => d.Name, StringComparer.CurrentCultureIgnoreCase)
            .ToList();
    }

    /// <summary>真正的 WinRT 扫描逻辑：先收广播，再并入已配对设备（兜底）。</summary>
    private static async Task<List<BleDeviceInfo>> ScanCoreAsync(TimeSpan timeout, CancellationToken ct)
    {
        // 以蓝牙地址为键合并：同一设备可能收到多包广播
        var collected = new Dictionary<ulong, ScannedDevice>();
        var gate = new object();
        BluetoothLEAdvertisementWatcher? watcher = null;

        try
        {
            // 广播阶段：超时（或调用方取消）即停止，异常只记日志
            var scanWindow = StartAdvertisementScan(collected, gate);
            watcher = scanWindow.Watcher;

            AppLogger.Debug($"开始扫描 BLE 设备（{timeout.TotalSeconds:0.#} 秒）");
            await WaitScanWindowAsync(scanWindow.Stopped, timeout, ct).ConfigureAwait(false);
        }
        catch (OperationCanceledException)
        {
            AppLogger.Debug("扫描窗口被取消，返回已收集结果");
        }
        catch (Exception ex)
        {
            AppLogger.Error("BLE 广播扫描失败", ex);
        }
        finally
        {
            if (watcher is not null)
            {
                try { watcher.Stop(); }
                catch (Exception ex) { AppLogger.Debug($"停止扫描失败: {ex.Message}"); }
            }
        }

        // 已配对设备兜底：这类设备可能不广播（例如已连过的心率带）
        if (!ct.IsCancellationRequested)
        {
            await MergePairedDevicesAsync(collected, gate, ct).ConfigureAwait(false);
        }

        return Snapshot(collected, gate);
    }

    /// <summary>启动广播扫描并返回"停止"信号。</summary>
    private static (BluetoothLEAdvertisementWatcher Watcher, Task Stopped) StartAdvertisementScan(
        Dictionary<ulong, ScannedDevice> collected,
        object gate)
    {
        var stopped = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
        var watcher = new BluetoothLEAdvertisementWatcher
        {
            // Active 扫描可拿到 ScanResponse 里的 LocalName，与原版 bleak 行为一致
            ScanningMode = BluetoothLEScanningMode.Active,
        };

        watcher.Received += (_, args) =>
        {
            try { CollectAdvertisement(args, collected, gate); }
            catch (Exception ex) { AppLogger.Debug($"处理广播数据失败: {ex.Message}"); }
        };
        // 无线电关闭 / 被系统中止时提前结束等待
        watcher.Stopped += (_, _) => stopped.TrySetResult(true);

        watcher.Start();
        return (watcher, stopped.Task);
    }

    /// <summary>等待扫描窗口结束（超时或被调用方取消都算正常结束）。</summary>
    private static async Task WaitScanWindowAsync(Task stopped, TimeSpan timeout, CancellationToken ct)
    {
        using var windowCts = CancellationTokenSource.CreateLinkedTokenSource(ct);
        windowCts.CancelAfter(timeout);
        try
        {
            await stopped.WaitAsync(windowCts.Token).ConfigureAwait(false);
        }
        catch (OperationCanceledException)
        {
            // 超时或用户取消：返回已收集结果，不视为错误
        }
    }

    /// <summary>把一包广播合并进结果集（保留最新 RSSI 与非空名称）。</summary>
    private static void CollectAdvertisement(
        BluetoothLEAdvertisementReceivedEventArgs args,
        Dictionary<ulong, ScannedDevice> collected,
        object gate)
    {
        var address = args.BluetoothAddress;
        if (address == 0 || address > MaxBluetoothAddress) return;

        var name = args.Advertisement?.LocalName ?? string.Empty;
        var rssi = ToRssi(args.RawSignalStrengthInDBm);

        lock (gate)
        {
            if (!collected.TryGetValue(address, out var entry))
            {
                entry = new ScannedDevice { Address = address };
                collected[address] = entry;
            }

            // 名称只在拿到非空值时覆盖，避免后续空名称包冲掉已有名称
            if (!string.IsNullOrWhiteSpace(name)) entry.Name = name;
            entry.Rssi = rssi;
            entry.HasRssi = true;
        }
    }

    /// <summary>已配对设备兜底：把不广播的已配对设备并入结果集。</summary>
    private static async Task MergePairedDevicesAsync(
        Dictionary<ulong, ScannedDevice> collected,
        object gate,
        CancellationToken ct)
    {
        try
        {
            var selector = BluetoothLEDevice.GetDeviceSelector();
            var infos = await DeviceInformation.FindAllAsync(selector)
                .AsTask()
                .WaitAsync(PairedQueryTimeout, ct)
                .ConfigureAwait(false);

            var merged = 0;
            foreach (var info in infos)
            {
                if (info is null) continue;

                bool isPaired;
                try { isPaired = info.Pairing?.IsPaired == true; }
                catch (Exception ex)
                {
                    AppLogger.Debug($"读取配对状态失败（{info.Id}）: {ex.Message}");
                    isPaired = false;
                }

                // 只补"已配对"的设备：系统缓存里的历史设备会给列表带来噪音
                if (!isPaired) continue;

                var address = ParseAddressFromDeviceId(info.Id);
                if (address == 0)
                {
                    AppLogger.Debug($"无法从设备 Id 解析蓝牙地址，跳过兜底: {info.Id}");
                    continue;
                }

                lock (gate)
                {
                    if (!collected.TryGetValue(address, out var entry))
                    {
                        entry = new ScannedDevice { Address = address };
                        collected[address] = entry;
                    }

                    entry.IsPaired = true;
                    entry.DeviceId = info.Id;
                    if (string.IsNullOrWhiteSpace(entry.Name) && !string.IsNullOrWhiteSpace(info.Name))
                        entry.Name = info.Name;
                }
                merged++;
            }

            AppLogger.Debug($"已配对设备兜底：并入 {merged} 个设备");
        }
        catch (OperationCanceledException)
        {
            AppLogger.Debug("已配对设备兜底查询被取消");
        }
        catch (TimeoutException)
        {
            AppLogger.Debug("已配对设备兜底查询超时，使用已扫描结果");
        }
        catch (Exception ex)
        {
            AppLogger.Warn($"查询已配对设备失败（不影响扫描结果）: {ex.Message}");
        }
    }

    /// <summary>把内部收集结果转成对外不可变快照。</summary>
    private static List<BleDeviceInfo> Snapshot(Dictionary<ulong, ScannedDevice> collected, object gate)
    {
        var results = new List<BleDeviceInfo>(collected.Count);
        lock (gate)
        {
            foreach (var entry in collected.Values)
            {
                results.Add(new BleDeviceInfo
                {
                    Name = entry.Name,
                    Address = FormatAddress(entry.Address),
                    DeviceId = entry.DeviceId,
                    BluetoothAddress = entry.Address,
                    IsPaired = entry.IsPaired,
                    Rssi = entry.HasRssi ? entry.Rssi : sbyte.MinValue,
                });
            }
        }
        return results;
    }

    /// <summary>RSSI（dBm）转 sbyte，并保留 sbyte.MinValue 作为"无 RSSI"哨兵。</summary>
    private static sbyte ToRssi(short dbm)
    {
        if (dbm >= sbyte.MaxValue) return sbyte.MaxValue;
        // -128 被 BleDeviceInfo.HasRssi 用作"没有信号强度"的哨兵，最低只取 -127
        if (dbm <= sbyte.MinValue + 1) return (sbyte)(sbyte.MinValue + 1);
        return (sbyte)dbm;
    }

    /// <summary>48 位蓝牙地址转 "AA:BB:CC:DD:EE:FF"。</summary>
    private static string FormatAddress(ulong address)
    {
        var bytes = new byte[6];
        for (var i = 0; i < bytes.Length; i++)
            bytes[i] = (byte)((address >> (8 * (5 - i))) & 0xFF);
        return string.Join(':', bytes.Select(b => b.ToString("X2", CultureInfo.InvariantCulture)));
    }

    /// <summary>从 DeviceInformation.Id 尾部（"…-aa:bb:cc:dd:ee:ff"）解析蓝牙地址。</summary>
    private static ulong ParseAddressFromDeviceId(string? deviceId)
    {
        if (string.IsNullOrWhiteSpace(deviceId)) return 0u;

        var index = deviceId.LastIndexOf('-');
        if (index < 0 || index >= deviceId.Length - 1) return 0u;

        var tail = deviceId[(index + 1)..].Replace(":", string.Empty).Trim();
        if (tail.Length != 12) return 0u;

        return ulong.TryParse(tail, NumberStyles.HexNumber, CultureInfo.InvariantCulture, out var value)
            ? value
            : 0u;
    }

    /// <summary>扫描过程中的可变中间结果。</summary>
    private sealed class ScannedDevice
    {
        public string Name = string.Empty;
        public string? DeviceId;
        public bool IsPaired;
        public bool HasRssi;
        public sbyte Rssi = sbyte.MinValue;

        /// <summary>蓝牙地址（48 位）。</summary>
        public ulong Address;
    }
}
