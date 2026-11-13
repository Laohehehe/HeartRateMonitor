namespace HeartRateMonitor.Views;

/// <summary>
/// 设备列表的显示辅助（供 x:Bind 函数绑定调用）。
/// </summary>
public static class DeviceDisplay
{
    /// <summary>RSSI 显示文本；无信号强度时返回空串。</summary>
    public static string FormatRssi(sbyte rssi)
        => rssi == sbyte.MinValue ? string.Empty : $"{rssi} dBm";
}
