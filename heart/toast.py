# -*- coding: utf-8 -*-
"""Windows Toast 通知：基于 windows-toasts + 自定义 AUMID 注册。

提供"下载完成"通知（含 [立即重启更新] [退出时更新] 按钮），
按钮回调通过 Qt 信号安全派发到主线程。
"""
# pyright: reportAttributeAccessIssue=false
import os
import winreg

from PyQt5.QtCore import QObject, pyqtSignal

from .resources import resource_path
from .logger_setup import logger

APP_ID = "Laohehehe.HeartRateMonitor"
APP_NAME = "心率监测"

try:
    from windows_toasts import InteractableWindowsToaster, Toast, ToastButton
    _AVAILABLE = True
except Exception:
    _AVAILABLE = False


class _ToastSignal(QObject):
    """Toast 按钮点击 → 主线程信号（跨线程安全）。"""
    action = pyqtSignal(str)


_signal = _ToastSignal()
_toaster = None


def signal():
    """返回 Toast 按钮点击信号对象（主线程连接）。"""
    return _signal


def _ensure_aumid():
    """在 HKCU 注册 AppUserModelId，使通知显示自己的名称/图标并支持按钮激活。"""
    if not _AVAILABLE:
        return False
    try:
        key_path = f"SOFTWARE\\Classes\\AppUserModelId\\{APP_ID}"
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path) as key:
            winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, APP_NAME)
            icon = resource_path("tray_icon.ico")
            if os.path.isfile(icon):
                winreg.SetValueEx(key, "IconUri", 0, winreg.REG_SZ, icon)
        return True
    except Exception as e:
        logger.warning(f"Toast AUMID 注册失败: {e}")
        return False


def _get_toaster():
    global _toaster
    if _toaster is None:
        _toaster = InteractableWindowsToaster(APP_NAME, notifierAUMID=APP_ID)
    return _toaster


def notify_update_ready(version):
    """发送"新版本已就绪"通知，带 [立即重启更新] [退出时更新] 按钮。失败返回 False。"""
    if not _AVAILABLE:
        logger.info("Toast 通知不可用（windows-toasts 未安装），功能降级")
        return False
    try:
        if not _ensure_aumid():
            return False
        toast = Toast([f"新版本 v{version} 已就绪", "点击选择更新方式"])
        toast.AddAction(ToastButton("立即重启更新", "restart"))
        toast.AddAction(ToastButton("退出时更新", "later"))
        toast.on_activated = lambda args: _signal.action.emit(args.arguments or "")
        _get_toaster().show_toast(toast)
        return True
    except Exception as e:
        logger.warning(f"发送 Toast 通知失败: {e}")
        return False
