# -*- coding: utf-8 -*-
"""心跳悬浮窗（半透明、可拖动、可固定）。"""
# pyright: reportAttributeAccessIssue=false
import ctypes
from typing import TYPE_CHECKING

from PyQt5.QtWidgets import QWidget, QVBoxLayout, QLabel, QMenu
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QFont, QPixmap, QImage
from PIL import Image

from ..resources import resource_path
from ..config import load_config, save_config
from ..fonts import get_app_font_family

if TYPE_CHECKING:
    from ..monitor import HeartRateMonitor


class HeartRateWindow(QWidget):
    fixed_changed = pyqtSignal(bool)  # 固定状态改变信号

    def __init__(self, monitor: "HeartRateMonitor"):
        super().__init__()
        self.monitor = monitor
        self.fixed = False
        self.draggable = True
        self._drag_pos = None
        self.init_ui()
        # 初始置顶状态来自 monitor
        self.set_topmost(monitor.hr_window_topmost)

    def init_ui(self):
        # 不使用 Qt.Tool：tool 窗口会被 OBS 等录屏软件过滤，改用普通无边框窗口
        self.setWindowFlags(Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        # 显示时不抢占焦点，避免遮挡主界面操作
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setGeometry(100, 100, 220, 170)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.heart_label = QLabel()
        self.heart_label.setAlignment(Qt.AlignCenter)
        heart_pixmap = self.load_heart_pixmap()
        if heart_pixmap:
            self.heart_label.setPixmap(heart_pixmap)
        else:
            self.heart_label.setText("❤")
            self.heart_label.setFont(QFont("Arial", 30))
            self.heart_label.setStyleSheet("color: red; background: transparent;")
        layout.addWidget(self.heart_label, alignment=Qt.AlignCenter)

        self.hr_label = QLabel("--")
        self.hr_label.setAlignment(Qt.AlignCenter)
        self.hr_label.setFont(QFont(get_app_font_family(), 24, QFont.Bold))
        self.hr_label.setStyleSheet("color: white; background: transparent;")
        layout.addWidget(self.hr_label, alignment=Qt.AlignCenter)

        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_context_menu)

    def set_topmost(self, topmost):
        flags = self.windowFlags()
        if topmost:
            flags |= Qt.WindowStaysOnTopHint
        else:
            flags &= ~Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.show()

    def showEvent(self, event):
        """每次显示后隐藏任务栏按钮（普通窗口类型仍可被 OBS 捕获）。"""
        super().showEvent(event)
        self._hide_from_taskbar()

    def _hide_from_taskbar(self):
        """通过 Win32 移除 WS_EX_APPWINDOW，使悬浮窗不显示在任务栏。

        保留普通窗口类型（非 Qt.Tool）从而可被 OBS 等录屏软件捕获。
        """
        try:
            GWL_EXSTYLE = -20
            WS_EX_APPWINDOW = 0x00040000
            hwnd = int(self.winId())
            ex_style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            if ex_style & WS_EX_APPWINDOW:
                ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex_style & ~WS_EX_APPWINDOW)
        except Exception:
            pass

    def load_heart_pixmap(self):
        try:
            img = Image.open(resource_path("h.png")).resize((60, 60), Image.Resampling.LANCZOS)
            img = img.convert("RGBA")
            data = img.tobytes("raw", "RGBA")
            qimg = QImage(data, img.width, img.height, QImage.Format_RGBA8888)
            return QPixmap.fromImage(qimg)
        except Exception:
            return None

    def show_context_menu(self, pos):
        menu = QMenu(self)
        action = menu.addAction("取消固定" if self.fixed else "固定窗口")
        assert action is not None
        action.triggered.connect(self.monitor.toggle_fixed)
        menu.exec_(self.mapToGlobal(pos))

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.draggable:
            self._drag_pos = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton and self.draggable and self._drag_pos is not None:
            self.move(event.globalPos() - self._drag_pos)
            event.accept()

    def set_fixed(self, fixed):
        """设置固定状态，并发射信号。"""
        if self.fixed != fixed:
            self.fixed = fixed
            if fixed:
                self.draggable = False
                self.setWindowFlags(self.windowFlags() | Qt.WindowTransparentForInput)
                self.show()
            else:
                self.draggable = True
                self.setWindowFlags(self.windowFlags() & ~Qt.WindowTransparentForInput)
                self.show()
            self.fixed_changed.emit(self.fixed)

    def update_hr(self, value):
        self.hr_label.setText(value)

    def save_position(self):
        config = load_config()
        if 'HeartRateWindow' not in config:
            config.add_section('HeartRateWindow')
        config.set('HeartRateWindow', 'x', str(self.x()))
        config.set('HeartRateWindow', 'y', str(self.y()))
        save_config(config)

    def load_position(self):
        config = load_config()
        if config.has_section('HeartRateWindow'):
            try:
                x = int(config.get('HeartRateWindow', 'x'))
                y = int(config.get('HeartRateWindow', 'y'))
                self.move(x, y)
            except Exception:
                pass
