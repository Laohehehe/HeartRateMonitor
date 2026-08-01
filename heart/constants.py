# -*- coding: utf-8 -*-
"""全局常量。"""

APP_VERSION = "26.8.1"
GITHUB_REPO = "Laohehehe/HeartRateMonitor"

# Windows 系统声音事件名映射（显示名称 → 可能的注册表事件名列表，依次尝试）
SOUND_EVENT_MAP = {
    "Windows Notify Calendar": ["CalendarReminder", "Notification.Default", "Reminder", "SystemAsterisk"],
    "Windows Hardware Insert": ["DeviceConnect", "SystemAsterisk"],
    "Windows Hardware Remove": ["DeviceDisconnect", "SystemExclamation"],
    "Windows Hardware Fail": ["DeviceFail", "SystemHand"],
}
