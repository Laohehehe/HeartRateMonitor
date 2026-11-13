namespace HeartRateMonitor.Core;

/// <summary>
/// 一个已发现的 BLE 设备（对应 Python 中 bleak BLEDevice 的展示信息）。
/// </summary>
public sealed class BleDeviceInfo
{
    public required string Name { get; init; }

    /// <summary>显示用 MAC，如 "AA:BB:CC:DD:EE:FF"。</summary>
    public required string Address { get; init; }

    /// <summary>Windows 设备信息 Id（用于连接定位）。</summary>
    public string? DeviceId { get; init; }

    /// <summary>数值蓝牙地址（用于按地址再次匹配）。</summary>
    public ulong BluetoothAddress { get; init; }

    public bool IsPaired { get; init; }

    public sbyte Rssi { get; init; } = sbyte.MinValue;

    public bool HasRssi => Rssi != sbyte.MinValue;

    /// <summary>列表显示文本。</summary>
    public string DisplayText => $"{Name} ({Address})";

    public override string ToString() => DisplayText;
}
