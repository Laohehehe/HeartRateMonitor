# -*- coding: utf-8 -*-
"""跨线程 UI 调度器。

信号在 Qt 主线程创建，从后台线程 emit 时会自动将槽调用投递到
主线程事件循环，避免直接跨线程操作 Qt 控件。
"""
# pyright: reportAttributeAccessIssue=false
from PyQt5.QtCore import QObject, pyqtSignal


class GuiDispatcher(QObject):
    """蓝牙扫描/连接等后台线程 → 主线程 UI 更新的信号集。"""
    scan_finished = pyqtSignal()
    scan_devices = pyqtSignal(list)
    scan_failed = pyqtSignal(str)
    connection_changed = pyqtSignal(bool)
    hr_text = pyqtSignal(str)


class RestoreHelper(QObject):
    """托盘线程（pystray 守护线程）→ 主线程操作的信号集。"""
    restore = pyqtSignal()
    toggle_fixed_requested = pyqtSignal()
    quit_requested = pyqtSignal()
