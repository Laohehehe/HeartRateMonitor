// ============================================================================
// 全局常量（对应 Python: heart/constants.py）
// ============================================================================
namespace HeartRateMonitor.Core;

public static class AppConstants
{
    /// <summary>应用版本号。发布新版本时修改此处（同时修改 csproj 的 Version/FileVersion）。</summary>
    public const string AppVersion = "26.11.13.1";

    /// <summary>GitHub 仓库（owner/repo），用于自动更新检查。</summary>
    public const string GitHubRepo = "Laohehehe/HeartRateMonitor";

    /// <summary>历史记录上限（约 1 小时 @1Hz），防止内存无限增长。</summary>
    public const int HeartRateHistoryLimit = 3600;

    /// <summary>心率的 GATT 特征值 UUID（Heart Rate Measurement）。</summary>
    public const string HeartRateMeasurementUuid = "00002a37-0000-1000-8000-00805f9b34fb";

    /// <summary>心率服务的 GATT 服务 UUID（Heart Rate Service）。</summary>
    public const string HeartRateServiceUuid = "0000180d-0000-1000-8000-00805f9b34fb";

    /// <summary>断线重连间隔（秒）。</summary>
    public const int ReconnectDelaySeconds = 5;

    /// <summary>界面心率刷新定时器间隔（毫秒）。</summary>
    public const int DisplayRefreshIntervalMs = 500;

    /// <summary>Windows Toast 通知的 AppUserModelId。</summary>
    public const string ToastAppId = "Laohehehe.HeartRateMonitor";

    /// <summary>Toast 通知显示名。</summary>
    public const string ToastAppName = "心率监测";

    /// <summary>程序单实例互斥体名。</summary>
    public const string SingleInstanceMutexName = @"Local\HeartRateMonitor_SingleInstance";

    /// <summary>唤醒已有实例的命名事件名。</summary>
    public const string ActivateEventName = @"Local\HeartRateMonitor_Activate";

    /// <summary>
    /// Windows 系统声音事件名映射（显示名称 → 可能的注册表事件名列表，依次尝试）。
    /// 对应 Python SOUND_EVENT_MAP。
    /// </summary>
    public static readonly IReadOnlyDictionary<string, string[]> SoundEventMap = new Dictionary<string, string[]>
    {
        ["Windows Notify Calendar"] = new[] { "CalendarReminder", "Notification.Default", "Reminder", "SystemAsterisk" },
        ["Windows Hardware Insert"] = new[] { "DeviceConnect", "SystemAsterisk" },
        ["Windows Hardware Remove"] = new[] { "DeviceDisconnect", "SystemExclamation" },
        ["Windows Hardware Fail"] = new[] { "DeviceFail", "SystemHand" },
    };
}
