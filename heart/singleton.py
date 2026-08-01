# -*- coding: utf-8 -*-
"""单实例互斥：确保程序只有一个实例在运行。

后开实例不会静默退出，而是通过命名事件唤醒已有实例，
让已有实例把主窗口提到前台，随后后开实例退出。
"""
# pyright: reportAttributeAccessIssue=false
import ctypes
from ctypes import wintypes

_MUTEX_NAME = "Local\\HeartRateMonitor_SingleInstance"
_EVENT_NAME = "Local\\HeartRateMonitor_Activate"
ERROR_ALREADY_EXISTS = 183
EVENT_MODIFY_STATE = 0x0002
WAIT_OBJECT_0 = 0

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_mutex = None
_event = None


def _create_mutex():
    """创建命名互斥体。返回 True 表示本实例是首个实例。"""
    global _mutex
    _kernel32.CreateMutexW.restype = wintypes.HANDLE
    _kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    _mutex = _kernel32.CreateMutexW(None, False, _MUTEX_NAME)
    return ctypes.get_last_error() != ERROR_ALREADY_EXISTS


def is_duplicate():
    """启动早期调用：创建互斥体与命名事件，返回 True 表示已有实例在运行。"""
    global _event
    _kernel32.CreateEventW.restype = wintypes.HANDLE
    _kernel32.CreateEventW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    _event = _kernel32.CreateEventW(None, False, False, _EVENT_NAME)
    return not _create_mutex()


def signal_activate():
    """后开实例调用：唤醒已有实例把主窗口提到前台，随后退出。"""
    _kernel32.OpenEventW.restype = wintypes.HANDLE
    _kernel32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    evt = _kernel32.OpenEventW(EVENT_MODIFY_STATE, False, _EVENT_NAME)
    if evt:
        _kernel32.SetEvent(evt)
        _kernel32.CloseHandle(evt)
    else:
        from .logger_setup import logger
        logger.debug("激活事件打开失败（OpenEventW 返回 NULL），跳过唤醒")


def activate_requested():
    """首实例在 Qt 定时器中轮询：事件被置位时返回 True（由调用方把窗口提到前台）。"""
    if not _event:
        return False
    _kernel32.WaitForSingleObject.restype = wintypes.DWORD
    _kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    return _kernel32.WaitForSingleObject(_event, 0) == WAIT_OBJECT_0
