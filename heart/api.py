# -*- coding: utf-8 -*-
"""可编程 API：供自动化测试 / 外部脚本调用程序功能。

用法（同一进程内，程序启动后）：
    from heart.monitor import HeartRateMonitor
    m = HeartRateMonitor()
    api = m.api                # 或 from heart.api import get_api; api = get_api()
    devices = api.scan()
    api.connect_by_name("Watch")   # 按设备名子串连接
    hr = api.wait_for_heart_rate(timeout=15)
    print(api.status())

说明：BLE / 逻辑类方法可从任意线程调用；涉及 Qt 界面的方法
（set_theme / show_chart 等）请从 Qt 主线程调用。
"""
# pyright: reportAttributeAccessIssue=false
import time
import asyncio
from typing import TYPE_CHECKING, Optional, List, Dict, Any

from .constants import APP_VERSION

if TYPE_CHECKING:
    from .monitor import HeartRateMonitor

_current_api = None


def get_api() -> Optional["AppAPI"]:
    """返回当前进程内已创建的 API 实例（HeartRateMonitor 启动后可用），未创建时返回 None。"""
    return _current_api


class AppAPI:
    """包装 HeartRateMonitor 的测试/脚本接口。"""

    def __init__(self, monitor: "HeartRateMonitor"):
        self._m = monitor
        global _current_api
        if _current_api is None:
            _current_api = self

    # ===================== 扫描 / 连接 =====================
    def scan(self, timeout: float = 8.0) -> List[Dict[str, str]]:
        """扫描蓝牙设备（阻塞至完成），timeout 为实际扫描时长，返回 [{'name', 'address'}, ...]。"""
        fut = asyncio.run_coroutine_threadsafe(self._m._scan_and_update(timeout), self._m.loop)
        fut.result(timeout + 5)
        return [{"name": d.name or "", "address": d.address} for d in self._m.devices]

    def _find_device(self, address=None, name=None):
        devices = self._m.devices
        if not devices:
            self.scan()
            devices = self._m.devices
        for d in devices:
            if address and d.address and d.address.lower() == address.lower():
                return d
            if name and d.name and name.lower() in d.name.lower():
                return d
        return None

    def connect(self, address=None, name=None) -> bool:
        """按 MAC 地址或名称子串连接设备。"""
        if self._m.connected or self._m.connecting:
            return True
        dev = self._find_device(address, name)
        if dev is None:
            return False
        self._m._start_monitoring(dev)
        return True

    def connect_by_address(self, address: str) -> bool:
        return self.connect(address=address)

    def connect_by_name(self, name: str) -> bool:
        return self.connect(name=name)

    def disconnect(self):
        self._m.disconnect_device()

    # ===================== 心率 / 状态 =====================
    def get_heart_rate(self) -> Optional[int]:
        """最近一次心率值（无数据返回 None）。"""
        with self._m.hr_lock:
            if self._m.hr_history:
                return self._m.hr_history[-1][1]
        return None

    def wait_for_heart_rate(self, timeout: float = 15.0) -> Optional[int]:
        """等待直到收到第一条心率数据。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            hr = self.get_heart_rate()
            if hr is not None:
                return hr
            time.sleep(0.5)
        return None

    def status(self) -> Dict[str, Any]:
        """返回当前运行状态快照。"""
        with self._m.hr_lock:
            history_len = len(self._m.hr_history)
            latest = self._m.hr_history[-1][1] if history_len else None
        return {
            "connected": self._m.connected,
            "connecting": self._m.connecting,
            "device": self._m.selected_device.name if self._m.selected_device else None,
            "theme": self._m.main_window._theme,
            "history_count": history_len,
            "latest_hr": latest,
            "version": APP_VERSION,
        }

    # ===================== 主题 =====================
    def get_theme(self) -> str:
        """当前主题：'light' / 'dark'。"""
        return self._m.main_window._theme

    def set_theme(self, theme: str):
        """切换主题（须从 Qt 主线程调用）。"""
        self._m.main_window.set_theme(theme)

    # ===================== 窗口 / 退出 =====================
    def show_chart(self):
        """打开心率曲线窗口（须从 Qt 主线程调用）。"""
        self._m.show_chart()

    def quit(self):
        """退出应用。"""
        self._m.quit_app()
