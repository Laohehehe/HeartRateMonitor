# pyright: reportAttributeAccessIssue=false
import sys
import os
import asyncio
import threading
import queue
import configparser
import traceback
import ctypes
import winsound
import logging
import winreg
import json
import subprocess
import urllib.request
import tempfile
import concurrent.futures
from datetime import datetime

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QListWidget, QFrame, QMenu, QCheckBox, QMessageBox,
    QGraphicsOpacityEffect
)
from PyQt5.QtCore import (
    Qt, QTimer, QPropertyAnimation, QRect, QEasingCurve, QSize, QEvent, QPoint, QPointF,
    pyqtProperty, pyqtSignal, QObject
)
from PyQt5.QtGui import (
    QFont, QPixmap, QImage, QPainter, QColor, QPen, QBrush, QPainterPath
)

from bleak import BleakScanner, BleakClient
from bleak.backends.characteristic import BleakGATTCharacteristic
from PIL import Image, ImageDraw, ImageFont
import pystray
from pystray import MenuItem as item

# ===================== 日志系统 =====================
_LOG_DIR = os.environ.get('TMP') or os.environ.get('TEMP') or os.path.expanduser("~")
_LOG_FILE = os.path.join(_LOG_DIR, f"heart_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.log")

_logger = logging.getLogger("Heart")
_logger.setLevel(logging.DEBUG)

_fh = logging.FileHandler(_LOG_FILE, encoding='utf-8')
_fh.setLevel(logging.DEBUG)
_fh.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', datefmt='%H:%M:%S'))
_logger.addHandler(_fh)

if not getattr(sys, 'frozen', False):
    _ch = logging.StreamHandler(sys.stdout)
    _ch.setLevel(logging.DEBUG)
    _ch.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', datefmt='%H:%M:%S'))
    _logger.addHandler(_ch)

logger = _logger
logger.info(f"日志文件: {_LOG_FILE}")

APP_VERSION = "26.7.28"
GITHUB_REPO = "Laohehehe/HeartRateMonitor"

# ===================== 资源路径 =====================
def resource_path(relative_path):
    meipass = getattr(sys, '_MEIPASS', None)
    if meipass is not None:
        return os.path.join(meipass, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)

# ===================== Windows 系统声音 =====================
# 系统声音事件名映射（显示名称 → 可能的注册表事件名列表，依次尝试）
_SOUND_EVENT_MAP = {
    "Windows Notify Calendar": ["CalendarReminder", "Notification.Default", "Reminder", "SystemAsterisk"],
    "Windows Hardware Insert": ["DeviceConnect", "SystemAsterisk"],
    "Windows Hardware Remove": ["DeviceDisconnect", "SystemExclamation"],
    "Windows Hardware Fail": ["DeviceFail", "SystemHand"],
}

def _get_sound_file(alias):
    """从注册表读取声音别名对应的 wav 文件路径"""
    try:
        key_path = f"AppEvents\\Schemes\\Apps\\.Default\\{alias}\\.default"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            value, _ = winreg.QueryValueEx(key, "")
            return value
    except:
        return None

def play_system_sound(sound_name):
    """异步播放 Windows 系统声音
    策略：
    1. 从注册表读取别名对应的 .wav 文件路径，展开环境变量后用 SND_FILENAME 播放
    2. 失败时尝试在 Windows Media 目录按文件名匹配直接播放
    3. 最后回退到 SND_ALIAS 模式
    """
    aliases = _SOUND_EVENT_MAP.get(sound_name, [sound_name])
    tried = []

    # 策略1：直接在 Windows Media 目录按文件名匹配（优先于注册表，避免注册表指向错误文件）
    system_root = os.environ.get('SystemRoot', 'C:\\Windows')
    media_dir = os.path.join(system_root, 'Media')
    for fname in [f"{sound_name}.wav", f"{sound_name.replace('Windows ', '')}.wav"]:
        filepath = os.path.join(media_dir, fname)
        if os.path.isfile(filepath):
            if ctypes.windll.winmm.PlaySoundW(filepath, None, 0x00020000 | 0x0001 | 0x0002):
                logger.debug(f"播放声音: {sound_name} (直接文件: {filepath}) 成功")
                return
            tried.append(f"文件({fname})(播放失败)")

    # 策略2：从注册表读取文件路径
    for alias in aliases:
        try:
            sound_file = _get_sound_file(alias)
            if sound_file:
                sound_file = os.path.expandvars(sound_file)
                if os.path.isfile(sound_file):
                    if ctypes.windll.winmm.PlaySoundW(sound_file, None, 0x00020000 | 0x0001 | 0x0002):
                        logger.debug(f"播放声音: {sound_name} -> {alias} (文件: {sound_file}) 成功")
                        return
                    tried.append(f"{alias}(播放失败)")
                else:
                    tried.append(f"{alias}(文件不存在)")
            else:
                tried.append(f"{alias}(未配置)")
        except Exception as e:
            tried.append(f"{alias}({e})")

    # 策略3：SND_ALIAS 回退
    for alias in aliases:
        try:
            if ctypes.windll.winmm.PlaySoundW(alias, None, 0x00010000 | 0x0001 | 0x0002 | 0x0010):
                logger.debug(f"播放声音: {sound_name} -> {alias} (SND_ALIAS 回退) 成功")
                return
        except:
            pass

    logger.warning(f"播放声音失败: {sound_name} (尝试: {', '.join(tried)})")
def get_config_path():
    appdata = os.environ.get('APPDATA', '')
    folder = os.path.join(appdata, 'Heart')
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, 'config.ini')

CONFIG_FILE = get_config_path()

def load_config():
    config = configparser.ConfigParser()
    config.read(CONFIG_FILE)
    return config

def save_config(config):
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    with open(CONFIG_FILE, 'w') as f:
        config.write(f)

# ===================== 信号发射器辅助类 =====================
class SignalEmitter(QObject):
    scan_finished = pyqtSignal()

# ===================== 心跳悬浮窗 =====================
class HeartRateWindow(QWidget):
    fixed_changed = pyqtSignal(bool)  # 固定状态改变信号

    def __init__(self, monitor):
        super().__init__()
        self.monitor = monitor
        self.fixed = False
        self.draggable = True
        self._drag_pos = None
        self.init_ui()
        # 初始置顶状态来自 monitor
        self.set_topmost(monitor.hr_window_topmost)

    def init_ui(self):
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
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
        self.hr_label.setFont(QFont("Microsoft YaHei", 24, QFont.Bold))
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

    def load_heart_pixmap(self):
        try:
            img = Image.open(resource_path("h.png")).resize((60, 60), Image.Resampling.LANCZOS)
            img = img.convert("RGBA")
            data = img.tobytes("raw", "RGBA")
            qimg = QImage(data, img.width, img.height, QImage.Format_RGBA8888)
            return QPixmap.fromImage(qimg)
        except:
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
        """设置固定状态，并发射信号"""
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
            except:
                pass

# ===================== 曲线画布 =====================
class ChartCanvas(QWidget):
    def __init__(self, monitor):
        super().__init__()
        self.monitor = monitor
        self.setMouseTracking(True)
        self.hovered_point = None

    def _smooth_points(self, history):
        if len(history) < 3:
            return history
        smoothed = []
        for i in range(len(history)):
            if i == 0 or i == len(history) - 1:
                smoothed.append(history[i])
            else:
                ts = history[i][0]
                val = (history[i-1][1] + history[i][1] + history[i+1][1]) / 3.0
                smoothed.append((ts, int(round(val))))
        return smoothed

    def _monotone_cubic_spline(self, points):
        path = QPainterPath()
        n = len(points)
        if n < 2:
            return path
        if n == 2:
            path.moveTo(points[0])
            path.lineTo(points[1])
            return path

        pts = [QPointF(p) for p in points]
        xs = [p.x() for p in pts]
        ys = [p.y() for p in pts]

        delta = []
        for i in range(1, n):
            dx = xs[i] - xs[i-1]
            if dx == 0:
                delta.append(0)
            else:
                delta.append((ys[i] - ys[i-1]) / dx)

        m = [0.0] * n
        for i in range(1, n-1):
            if delta[i-1] * delta[i] > 0:
                m[i] = (delta[i-1] + delta[i]) / 2.0
            else:
                m[i] = 0.0
        m[0] = delta[0] if n > 1 else 0
        m[-1] = delta[-1] if n > 1 else 0

        for i in range(n-1):
            if abs(delta[i]) < 1e-9:
                m[i] = m[i+1] = 0.0
                continue
            alpha = m[i] / delta[i]
            beta = m[i+1] / delta[i]
            if alpha < 0 or beta < 0:
                m[i] = 0.0
                m[i+1] = 0.0
            elif alpha * alpha + beta * beta > 9:
                tau = 3.0 / (alpha*alpha + beta*beta)**0.5
                m[i] = tau * alpha * delta[i]
                m[i+1] = tau * beta * delta[i]

        path.moveTo(pts[0])
        for i in range(1, n):
            dx = xs[i] - xs[i-1]
            cp1 = QPointF(xs[i-1] + dx/3.0, ys[i-1] + m[i-1] * dx/3.0)
            cp2 = QPointF(xs[i] - dx/3.0, ys[i] - m[i] * dx/3.0)
            path.cubicTo(cp1, cp2, pts[i])
        return path

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w = self.width()
        h = self.height()
        if w <= 0 or h <= 0:
            return

        with self.monitor.hr_lock:
            raw_history = list(self.monitor.hr_history)
            if len(raw_history) < 2:
                painter.setPen(QPen(QColor(150, 150, 150)))
                painter.setFont(QFont("Microsoft YaHei", 12))
                painter.drawText(QRect(0, 0, w, h), Qt.AlignCenter, "数据不足，至少需要两个数据点")
                return

            history = self._smooth_points(raw_history)
            hr_max = self.monitor.hr_max_value
            hr_min = self.monitor.hr_min_value
            if hr_max is None or hr_min is None:
                return
            if hr_max == hr_min:
                hr_max += 1

        margin_left = 60
        margin_right = 20
        margin_top = 30
        margin_bottom = 40
        draw_x_start = margin_left
        draw_y_start = margin_top
        draw_width = w - margin_left - margin_right
        draw_height = h - margin_top - margin_bottom

        if draw_width <= 0 or draw_height <= 0:
            return

        painter.setPen(QPen(QColor(60, 60, 60), 1))
        for i in range(5):
            yy = draw_y_start + draw_height * i / 4
            painter.drawLine(draw_x_start, int(yy), draw_x_start + draw_width, int(yy))
        for i in range(7):
            xx = draw_x_start + draw_width * i / 6
            painter.drawLine(int(xx), draw_y_start, int(xx), draw_y_start + draw_height)

        painter.setPen(QPen(QColor(255, 100, 100, 80), 1, Qt.DashLine))
        painter.drawLine(draw_x_start, int(draw_y_start), draw_x_start + draw_width, int(draw_y_start))
        painter.drawLine(draw_x_start, int(draw_y_start + draw_height), draw_x_start + draw_width, int(draw_y_start + draw_height))

        points = []
        data_times = []
        data_hrs = []
        for i, (ts, hr_val) in enumerate(history):
            x_coord = draw_x_start + (i / (len(history) - 1)) * draw_width
            y_coord = draw_y_start + (hr_max - hr_val) / (hr_max - hr_min) * draw_height
            points.append(QPoint(int(x_coord), int(y_coord)))
            data_times.append(ts)
            data_hrs.append(hr_val)

        pen = QPen(QColor("#FF69B4"), 2)
        painter.setPen(pen)
        smooth_path = self._monotone_cubic_spline(points)
        painter.drawPath(smooth_path)

        painter.setBrush(QColor("#FF69B4"))
        painter.setPen(Qt.NoPen)
        for pt in points:
            painter.drawEllipse(pt, 3, 3)

        painter.setPen(QColor("#FF69B4"))
        painter.setFont(QFont("Microsoft YaHei", 9))
        painter.drawText(draw_x_start, draw_y_start - 5, f"最高: {hr_max}")
        painter.drawText(draw_x_start, draw_y_start + draw_height + 15, f"最低: {hr_min}")

        painter.setPen(QColor(180, 180, 180))
        for i in range(5):
            val = hr_max - (hr_max - hr_min) * i / 4
            yy = draw_y_start + draw_height * i / 4
            painter.drawText(5, int(yy) + 5, f"{val:.0f}")

        if len(data_times) > 1:
            t1 = data_times[0].split()[-1] if ' ' in data_times[0] else data_times[0]
            t2 = data_times[-1].split()[-1] if ' ' in data_times[-1] else data_times[-1]
            painter.drawText(draw_x_start, draw_y_start + draw_height + 20, t1)
            painter.drawText(draw_x_start + draw_width - 50, draw_y_start + draw_height + 20, t2)

        if self.hovered_point is not None and 0 <= self.hovered_point < len(points):
            idx = self.hovered_point
            pt = points[idx]
            painter.setPen(QPen(QColor(255, 255, 255, 100), 1, Qt.DashLine))
            painter.drawLine(pt.x(), draw_y_start, pt.x(), draw_y_start + draw_height)
            tooltip = f"时间: {data_times[idx]}\n心率: {data_hrs[idx]}"
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(50, 50, 50, 220))
            text_rect = painter.boundingRect(QRect(), Qt.AlignLeft, tooltip)
            text_rect.moveTo(pt.x() + 10, pt.y() - text_rect.height() - 10)
            if text_rect.right() > draw_x_start + draw_width:
                text_rect.moveLeft(pt.x() - text_rect.width() - 10)
            if text_rect.y() < draw_y_start:
                text_rect.moveTop(pt.y() + 10)
            painter.drawRoundedRect(text_rect.adjusted(-5, -5, 5, 5), 5, 5)
            painter.setPen(QColor(255, 255, 255))
            painter.setFont(QFont("Microsoft YaHei", 9))
            painter.drawText(text_rect, Qt.AlignLeft, tooltip)

    def mouseMoveEvent(self, event):
        super().mouseMoveEvent(event)
        with self.monitor.hr_lock:
            raw_history = list(self.monitor.hr_history)
            if len(raw_history) < 2:
                self.hovered_point = None
                self.update()
                return
            history = self._smooth_points(raw_history)
            hr_max = self.monitor.hr_max_value
            hr_min = self.monitor.hr_min_value
            if hr_max is None or hr_min is None:
                return
            if hr_max == hr_min:
                hr_max += 1

        margin_left = 60
        margin_right = 20
        margin_top = 30
        margin_bottom = 40
        draw_x_start = margin_left
        draw_width = self.width() - margin_left - margin_right
        draw_height = self.height() - margin_top - margin_bottom
        draw_y_start = margin_top

        if draw_width <= 0 or draw_height <= 0:
            return

        closest_idx = None
        min_dist = float('inf')
        pos = event.pos()
        for i in range(len(history)):
            x_coord = draw_x_start + (i / (len(history) - 1)) * draw_width
            y_coord = draw_y_start + (hr_max - history[i][1]) / (hr_max - hr_min) * draw_height
            dist = ((pos.x() - x_coord) ** 2 + (pos.y() - y_coord) ** 2) ** 0.5
            if dist < min_dist and dist < 30:
                min_dist = dist
                closest_idx = i

        if closest_idx != self.hovered_point:
            self.hovered_point = closest_idx
            self.update()

# ===================== 心率曲线窗口 =====================
class ChartWindow(QWidget):
    def __init__(self, monitor):
        super().__init__()
        self.monitor = monitor
        self.setWindowTitle("心率曲线")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose, False)
        self.setMinimumSize(600, 400)
        self.resize(800, 500)

        self.card = QFrame(self)
        self.card.setObjectName("chartCard")
        self.card.setStyleSheet("""
            QFrame#chartCard {
                background: #2B2B2B;
                border-radius: 12px;
                border: 1px solid #444444;
            }
        """)
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(self.card)

        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(10, 10, 10, 10)

        title_bar = QHBoxLayout()
        title_bar.addStretch()
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(30, 30)
        close_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #AAAAAA;
                border: none;
                font-size: 16px;
            }
            QPushButton:hover {
                background: #E81123;
                color: white;
                border-radius: 4px;
            }
        """)
        close_btn.clicked.connect(self.animate_close)
        title_bar.addWidget(close_btn)
        card_layout.addLayout(title_bar)

        self.canvas = ChartCanvas(monitor)
        card_layout.addWidget(self.canvas, stretch=1)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.canvas.update)
        self.timer.start(1000)

    def show_with_animation(self):
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        screen_geometry = screen.geometry()
        target_rect = QRect((screen_geometry.width() - self.width()) // 2,
                            (screen_geometry.height() - self.height()) // 2,
                            self.width(), self.height())
        start_rect = QRect(target_rect.center(), QSize(1, 1))
        self.setGeometry(start_rect)
        self.show()
        self.animation = QPropertyAnimation(self, b"geometry")
        self.animation.setDuration(300)
        self.animation.setStartValue(start_rect)
        self.animation.setEndValue(target_rect)
        self.animation.setEasingCurve(QEasingCurve.OutBack)
        self.animation.start()

    def animate_close(self):
        current_geo = self.geometry()
        target_rect = QRect(current_geo.center(), QSize(1, 1))
        self.anim_close = QPropertyAnimation(self, b"geometry")
        self.anim_close.setDuration(200)
        self.anim_close.setStartValue(current_geo)
        self.anim_close.setEndValue(target_rect)
        self.anim_close.setEasingCurve(QEasingCurve.InBack)
        self.anim_close.finished.connect(self._on_close_anim_finished)
        self.anim_close.start()

    def _on_close_anim_finished(self):
        self.monitor.chart_window_closed()
        self.close()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton and hasattr(self, '_drag_pos'):
            self.move(event.globalPos() - self._drag_pos)
            event.accept()

# ===================== 动态进度条 =====================
class ModernProgressBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(24)
        self.setMinimumWidth(360)
        
        self.progress_width = 80
        self.position = 0
        self._current_width = self.progress_width
        
        self.state = "moving"
        self.speed = 10
        
        self.bg_color = QColor(240, 240, 240, 200)
        self.progress_color = QColor(0, 120, 212)
        
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_animation)
        self.timer.start(30)
    
    def _get_current_width(self):
        return self._current_width
    
    def _set_current_width(self, value):
        self._current_width = value
        self.update()
    
    current_width = pyqtProperty(int, _get_current_width, _set_current_width)
    
    def update_animation(self):
        if self.state == "moving":
            self.position += self.speed
            if self.position + self.progress_width >= 440:
                self.state = "shrinking"
                self.position = 440 - self.progress_width
        elif self.state == "shrinking":
            self.current_width -= self.speed
            if self.current_width <= 0:
                self.current_width = 0
                self.state = "expanding"
                self.position = 0
        elif self.state == "expanding":
            self.current_width += self.speed
            if self.current_width >= self.progress_width:
                self.current_width = self.progress_width
                self.state = "moving"
        self.update()
    
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        painter.setPen(Qt.NoPen)
        painter.setBrush(self.bg_color)
        painter.drawRoundedRect(0, 0, self.width(), self.height(), 8, 8)
        
        painter.setPen(QPen(QColor(200, 200, 200), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(0, 0, self.width(), self.height(), 8, 8)
        
        if self.current_width > 0:
            painter.setPen(Qt.NoPen)
            painter.setBrush(self.progress_color)
            
            path = QPainterPath()
            if self.position == 0:
                path.addRoundedRect(self.position, 2, self.current_width, self.height()-4, 6, 6)
            elif self.position + self.current_width >= self.width():
                path.addRoundedRect(self.position, 2, self.current_width, self.height()-4, 6, 6)
            else:
                path.addRect(self.position, 2, self.current_width, self.height()-4)
            
            painter.drawPath(path)
            
            painter.setPen(QPen(QColor(0, 150, 255, 100), 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(path)

    def start_animation(self):
        if self.width() < self.progress_width:
            self.position = 0
            self.current_width = self.width()
        self.timer.start()

    def stop_animation(self):
        self.timer.stop()

# ===================== 主窗口 =====================
class MainWindow(QMainWindow):
    def __init__(self, monitor):
        super().__init__()
        self.monitor = monitor
        self.btn_orig_geos = {}
        self.init_ui()

    def init_ui(self):
        self.setWindowTitle("心率监测")
        self.setFixedSize(400, 640)  # 稍高以容纳新开关
        self.setWindowFlags(Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)

        central = QWidget()
        central.setStyleSheet("background: transparent;")
        self.setCentralWidget(central)

        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(1, 1, 1, 1)

        self.card = QFrame()
        self.card.setStyleSheet("""
            QFrame {
                background: #F0F0F0;
                border-radius: 10px;
                border: 1px solid #D0D0D0;
            }
        """)
        main_layout.addWidget(self.card)

        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(20, 20, 20, 20)
        card_layout.setSpacing(10)

        # 标题栏
        title_bar = QHBoxLayout()
        title = QLabel("心率监测")
        title.setFont(QFont("Microsoft YaHei", 18, QFont.Bold))
        title.setStyleSheet("color: #0078D4; background: transparent;")
        title_bar.addWidget(title, alignment=Qt.AlignLeft)
        title_bar.addStretch()

        btn_base_style = """
            QPushButton {
                background: transparent;
                border: none;
                font-size: 16px;
                padding: 5px;
                color: #333;
            }
        """
        self.min_btn = QPushButton("─")
        self.min_btn.setFixedSize(30, 30)
        self.min_btn.setStyleSheet(btn_base_style + "QPushButton:hover { background: #E0E0E0; border-radius: 4px; }")
        self.min_btn.clicked.connect(self.animate_minimize)
        self.min_btn.installEventFilter(self)
        title_bar.addWidget(self.min_btn)

        self.tray_btn = QPushButton("◉")
        self.tray_btn.setFixedSize(30, 30)
        self.tray_btn.setToolTip("最小化到系统托盘")
        self.tray_btn.setStyleSheet(btn_base_style + "QPushButton:hover { background: #E0E0E0; border-radius: 4px; }")
        self.tray_btn.clicked.connect(self.animate_to_tray)
        self.tray_btn.installEventFilter(self)
        title_bar.addWidget(self.tray_btn)

        self.close_btn = QPushButton("✕")
        self.close_btn.setFixedSize(30, 30)
        self.close_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: none;
                font-size: 16px;
                padding: 5px;
                color: #333;
            }
            QPushButton:hover {
                background: #E81123;
                color: white;
                border-radius: 4px;
            }
        """)
        self.close_btn.clicked.connect(self.animate_quit)
        self.close_btn.installEventFilter(self)
        title_bar.addWidget(self.close_btn)

        card_layout.addLayout(title_bar)

        # 扫描按钮
        self.scan_btn = QPushButton("🔍  扫描设备")
        self.scan_btn.setFont(QFont("Microsoft YaHei", 10))
        self.scan_btn.setStyleSheet(self._button_style())
        self.scan_btn.clicked.connect(self.monitor.start_scan)
        card_layout.addWidget(self.scan_btn)

        # 设备列表
        self.device_list = QListWidget()
        self.device_list.setFont(QFont("Microsoft YaHei", 10))
        self.device_list.setStyleSheet("""
            QListWidget {
                background: white;
                border: 1px solid #E0E0E0;
                border-radius: 8px;
                padding: 5px;
                color: #333;
            }
            QListWidget::item:selected {
                background: #0078D4;
                color: white;
                border-radius: 4px;
            }
        """)
        card_layout.addWidget(self.device_list)

        # 连接/断开按钮
        btn_layout = QHBoxLayout()
        self.connect_btn = QPushButton("连接设备")
        self.disconnect_btn = QPushButton("断开连接")
        for btn in (self.connect_btn, self.disconnect_btn):
            btn.setFont(QFont("Microsoft YaHei", 10))
        self.connect_btn.setStyleSheet(self._button_style())
        self.connect_btn.clicked.connect(self._on_connect_click)
        self.disconnect_btn.setStyleSheet(self._disabled_button_style())
        self.disconnect_btn.setEnabled(False)
        self.disconnect_btn.clicked.connect(self._on_disconnect_click)
        btn_layout.addWidget(self.connect_btn)
        btn_layout.addWidget(self.disconnect_btn)
        card_layout.addLayout(btn_layout)

        # 功能区域
        func_layout = QVBoxLayout()

        # 开机自启胶囊
        capsule1 = QFrame()
        capsule1.setObjectName("capsule")
        capsule1.setStyleSheet("""
            QFrame#capsule {
                background: #E8E8E8;
                border-radius: 14px;
                padding: 6px 15px;
                border: none;
            }
        """)
        capsule_layout1 = QHBoxLayout(capsule1)
        capsule_layout1.setContentsMargins(0, 0, 0, 0)

        self.autostart_check = QCheckBox("开机自启")
        self.autostart_check.setFont(QFont("Microsoft YaHei", 9))
        self.autostart_check.setStyleSheet("""
            QCheckBox {
                spacing: 8px;
                color: #333;
                background: transparent;
            }
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
                border-radius: 9px;
                background-color: #CCCCCC;
            }
            QCheckBox::indicator:checked {
                background-color: #9ACD32;
            }
        """)
        self.autostart_check.stateChanged.connect(self.toggle_autostart)
        self.autostart_check.setChecked(self.is_autostart_enabled())
        capsule_layout1.addWidget(self.autostart_check)
        func_layout.addWidget(capsule1)

        # 置顶并固定胶囊
        capsule2 = QFrame()
        capsule2.setObjectName("capsule")
        capsule2.setStyleSheet("""
            QFrame#capsule {
                background: #E8E8E8;
                border-radius: 14px;
                padding: 6px 15px;
                border: none;
            }
        """)
        capsule_layout2 = QHBoxLayout(capsule2)
        capsule_layout2.setContentsMargins(0, 0, 0, 0)

        self.topmost_fixed_check = QCheckBox("置顶并固定")
        self.topmost_fixed_check.setFont(QFont("Microsoft YaHei", 9))
        self.topmost_fixed_check.setStyleSheet("""
            QCheckBox {
                spacing: 8px;
                color: #333;
                background: transparent;
            }
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
                border-radius: 9px;
                background-color: #CCCCCC;
            }
            QCheckBox::indicator:checked {
                background-color: #9ACD32;
            }
        """)
        self.topmost_fixed_check.stateChanged.connect(self.toggle_topmost_fixed)
        # 初始状态取决于 hr_window 是否存在及其固定状态
        self.topmost_fixed_check.setChecked(False)  # 默认不勾选
        capsule_layout2.addWidget(self.topmost_fixed_check)
        func_layout.addWidget(capsule2)

        save_pos_btn = QPushButton("保存心率窗口位置")
        save_pos_btn.setFont(QFont("Microsoft YaHei", 9))
        save_pos_btn.setStyleSheet("""
            QPushButton {
                background: #0078D4;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 5px;
            }
            QPushButton:hover { background: #106EBE; }
        """)
        save_pos_btn.clicked.connect(self.save_hr_window_position)
        func_layout.addWidget(save_pos_btn)

        chart_btn = QPushButton("📈 心率曲线")
        chart_btn.setFont(QFont("Microsoft YaHei", 9))
        chart_btn.setStyleSheet("""
            QPushButton {
                background: #FF69B4;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 5px;
            }
            QPushButton:hover { background: #FF85C0; }
        """)
        chart_btn.clicked.connect(self.monitor.show_chart)
        func_layout.addWidget(chart_btn)

        card_layout.addLayout(func_layout)

        self._drag_pos = None

        # 扫描蒙版
        self.overlay = QFrame(central)
        self.overlay.setGeometry(0, 0, self.width(), self.height())
        self.overlay.setStyleSheet("background: rgba(255, 255, 255, 180); border-radius: 10px;")
        self.overlay_effect = QGraphicsOpacityEffect(self.overlay)
        self.overlay_effect.setOpacity(0.0)
        self.overlay.setGraphicsEffect(self.overlay_effect)
        self.overlay.hide()

        overlay_layout = QVBoxLayout(self.overlay)
        overlay_layout.setAlignment(Qt.AlignCenter)
        overlay_layout.setContentsMargins(0, 0, 0, 0)

        self.scan_image_label = QLabel()
        pix = self.load_scan_image()
        if pix:
            self.scan_image_label.setPixmap(pix)
        else:
            self.scan_image_label.setText("📡")
            self.scan_image_label.setFont(QFont("Arial", 40))
            self.scan_image_label.setStyleSheet("color: #333; background: transparent;")
        self.scan_image_label.setAlignment(Qt.AlignCenter)
        overlay_layout.addWidget(self.scan_image_label)

        self.progress_bar = ModernProgressBar()
        self.progress_bar.setFixedWidth(300)
        overlay_layout.addWidget(self.progress_bar, alignment=Qt.AlignCenter)

    def load_scan_image(self):
        try:
            img = Image.open(resource_path("sikao.jpg"))
            img = img.convert("RGBA")
            data = img.tobytes("raw", "RGBA")
            qimg = QImage(data, img.width, img.height, QImage.Format_RGBA8888)
            pix = QPixmap.fromImage(qimg)
            pix = pix.scaledToWidth(150, Qt.SmoothTransformation)
            return pix
        except:
            return None

    def show_scan_overlay(self):
        self.overlay.setGeometry(0, 0, self.width(), self.height())
        self.overlay.show()
        self.progress_bar.start_animation()
        self.scan_btn.setEnabled(False)
        anim_show = getattr(self, 'anim_show', None)
        if anim_show is not None and anim_show.state() == QPropertyAnimation.Running:
            anim_show.stop()
        self.anim_show = QPropertyAnimation(self.overlay_effect, b"opacity")
        self.anim_show.setDuration(300)
        self.anim_show.setStartValue(self.overlay_effect.opacity())
        self.anim_show.setEndValue(1.0)
        self.anim_show.setEasingCurve(QEasingCurve.OutCubic)
        self.anim_show.start()

    def hide_scan_overlay(self):
        anim_hide = getattr(self, 'anim_hide', None)
        if anim_hide is not None and anim_hide.state() == QPropertyAnimation.Running:
            anim_hide.stop()
        self.anim_hide = QPropertyAnimation(self.overlay_effect, b"opacity")
        self.anim_hide.setDuration(300)
        self.anim_hide.setStartValue(self.overlay_effect.opacity())
        self.anim_hide.setEndValue(0.0)
        self.anim_hide.setEasingCurve(QEasingCurve.OutCubic)
        self.anim_hide.finished.connect(self._on_overlay_hidden)
        self.anim_hide.start()

    def _on_overlay_hidden(self):
        self.overlay.hide()
        self.progress_bar.stop_animation()
        self.scan_btn.setEnabled(True)

    def toggle_topmost_fixed(self, state):
        """同时切换置顶与固定状态"""
        is_checked = state == Qt.Checked
        self.monitor.hr_window_topmost = is_checked
        if self.monitor.hr_window:
            self.monitor.hr_window.set_topmost(is_checked)
            self.monitor.hr_window.set_fixed(is_checked)

    def sync_fixed_checkbox(self, fixed):
        """根据心率窗口的固定状态更新复选框（用于右键菜单同步）"""
        self.topmost_fixed_check.blockSignals(True)
        self.topmost_fixed_check.setChecked(fixed)
        self.topmost_fixed_check.blockSignals(False)
        # 同时更新置顶标志（保持与固定一致）
        if self.monitor.hr_window:
            self.monitor.hr_window_topmost = fixed
            self.monitor.hr_window.set_topmost(fixed)

    def _button_style(self):
        return """
            QPushButton {
                background: #0078D4;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 10px 20px;
            }
            QPushButton:hover { background: #106EBE; }
            QPushButton:pressed { background: #005A9E; }
        """

    def _disabled_button_style(self):
        return """
            QPushButton {
                background: #CCCCCC;
                color: #666666;
                border: none;
                border-radius: 6px;
                padding: 10px 20px;
            }
        """

    def eventFilter(self, obj, event):
        if obj in (self.min_btn, self.tray_btn, self.close_btn):
            if event.type() == QEvent.Enter:
                self.animate_button_scale(obj, 1.1)
                return True
            elif event.type() == QEvent.Leave:
                self.animate_button_scale(obj, 1.0)
                return True
        return super().eventFilter(obj, event)

    def animate_button_scale(self, btn, scale_factor):
        if btn not in self.btn_orig_geos:
            self.btn_orig_geos[btn] = btn.geometry()
        orig_geo = self.btn_orig_geos[btn]
        new_w = int(orig_geo.width() * scale_factor)
        new_h = int(orig_geo.height() * scale_factor)
        new_x = orig_geo.x() - (new_w - orig_geo.width()) // 2
        new_y = orig_geo.y() - (new_h - orig_geo.height()) // 2
        new_geo = QRect(new_x, new_y, new_w, new_h)

        if hasattr(btn, '_anim') and btn._anim:
            btn._anim.stop()
        anim = QPropertyAnimation(btn, b"geometry")
        anim.setDuration(200)
        anim.setStartValue(btn.geometry())
        anim.setEndValue(new_geo)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.start()
        btn._anim = anim

    def toggle_autostart(self, state):
        startup_dir = os.path.join(os.environ['APPDATA'], 'Microsoft', 'Windows', 'Start Menu', 'Programs', 'Startup')
        shortcut_path = os.path.join(startup_dir, 'HeartRateMonitor.lnk')
        if state == Qt.Checked:
            try:
                from win32com.client import Dispatch
                shell = Dispatch('WScript.Shell')
                shortcut = shell.CreateShortCut(shortcut_path)
                shortcut.Targetpath = sys.executable if getattr(sys, 'frozen', False) else os.path.abspath(sys.argv[0])
                shortcut.WorkingDirectory = os.path.dirname(shortcut.Targetpath)
                shortcut.save()
            except:
                QMessageBox.warning(self, "错误", "创建开机自启失败，可能需要管理员权限。")
        else:
            if os.path.exists(shortcut_path):
                os.remove(shortcut_path)

    def is_autostart_enabled(self):
        startup_dir = os.path.join(os.environ['APPDATA'], 'Microsoft', 'Windows', 'Start Menu', 'Programs', 'Startup')
        return os.path.exists(os.path.join(startup_dir, 'HeartRateMonitor.lnk'))

    def save_hr_window_position(self):
        if self.monitor.hr_window:
            self.monitor.hr_window.save_position()
            QMessageBox.information(self, "提示", "心率窗口位置已保存。")

    def closeEvent(self, event):
        self.animate_to_tray()
        event.ignore()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton and self._drag_pos is not None:
            self.move(event.globalPos() - self._drag_pos)
            event.accept()

    def update_device_list(self, devices):
        self.device_list.clear()
        filtered = []
        for dev in devices:
            name = dev.name if dev.name else ""
            if name.strip() == "" or name.lower() == dev.address.lower():
                continue
            filtered.append(dev)
            self.device_list.addItem(f"{name} ({dev.address})")
        if not filtered:
            self.device_list.addItem("未找到有效设备")
        return filtered

    def set_connecting_state(self, connecting):
        self.connect_btn.setEnabled(not connecting)
        self.disconnect_btn.setEnabled(False)

    def set_connected_state(self, connected):
        self.connect_btn.setEnabled(not connected)
        self.disconnect_btn.setEnabled(connected)
        if not connected:
            self.scan_btn.setEnabled(True)

    def show_with_animation(self):
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        screen_geometry = screen.geometry()
        target_rect = QRect((screen_geometry.width() - self.width()) // 2,
                            (screen_geometry.height() - self.height()) // 2,
                            self.width(), self.height())
        start_rect = QRect(target_rect.center(), QSize(1, 1))
        self.setGeometry(start_rect)
        self.show()
        self.animation = QPropertyAnimation(self, b"geometry")
        self.animation.setDuration(300)
        self.animation.setStartValue(start_rect)
        self.animation.setEndValue(target_rect)
        self.animation.setEasingCurve(QEasingCurve.OutBack)
        self.animation.start()

    def _on_connect_click(self):
        logger.debug("点击: 连接设备按钮")
        self.monitor.connect_device()

    def _on_disconnect_click(self):
        logger.debug("点击: 断开连接按钮")
        self.monitor.disconnect_device()

    def _fade_out_animation(self, callback):
        """窗口淡出动画（windowOpacity），动画结束后调用 callback"""
        self._fade_anim = QPropertyAnimation(self, b"windowOpacity")
        self._fade_anim.setDuration(150)
        self._fade_anim.setStartValue(self.windowOpacity())
        self._fade_anim.setEndValue(0.0)
        self._fade_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._fade_anim.finished.connect(callback)
        self._fade_anim.start()

    def changeEvent(self, event):
        """窗口状态变化时恢复透明度（从最小化恢复时 opacity 可能为0）"""
        if event.type() == QEvent.WindowStateChange:
            old_state = event.oldState()
            new_state = self.windowState()
            if (old_state & Qt.WindowMinimized) and not (new_state & Qt.WindowMinimized):
                self.setWindowOpacity(1.0)
        super().changeEvent(event)

    def animate_minimize(self):
        """淡出动画后最小化到任务栏"""
        logger.debug("触发: 最小化按钮")
        self._fade_out_animation(self._do_minimize)

    def _do_minimize(self):
        # changeEvent 会在从任务栏恢复时重置透明度，此处不能重置（否则闪烁）
        self.showMinimized()

    def animate_to_tray(self):
        """淡出动画后最小化到系统托盘"""
        logger.debug("触发: 托盘按钮")
        self._fade_out_animation(self._do_tray)

    def _do_tray(self):
        self.monitor.minimize_to_tray()

    def animate_quit(self):
        """淡出动画后退出应用"""
        logger.debug("触发: 关闭按钮")
        self._fade_out_animation(self._do_quit)

    def _do_quit(self):
        self.monitor.quit_app()

# ===================== 跨线程恢复辅助 =====================
class _RestoreHelper(QObject):
    """用于从非 Qt 线程安全地触发窗口恢复操作"""
    restore = pyqtSignal()

class _UpdateSignal(QObject):
    """用于从后台线程安全地触发更新操作"""
    update_ready = pyqtSignal(str, str)  # (version, download_url)
    download_done = pyqtSignal(str)  # (local_exe_path) 下载完成后在主线程触发

# ===================== 版本检查与自动更新 =====================
def _parse_version(v):
    """将版本字符串转为可比较的整数元组，如 '26.7.28' -> (26, 7, 28)"""
    try:
        return tuple(int(x) for x in v.lstrip('vV').split('.'))
    except:
        return (0,)

def _check_latest_release():
    """查询 GitHub 最新 Release，返回 (tag_name, download_url) 或 (None, None)"""
    url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
    req = urllib.request.Request(url, headers={"User-Agent": f"HeartRateMonitor/{APP_VERSION}", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode('utf-8'))
    tag = data.get("tag_name", "")
    assets = data.get("assets", [])
    # 找第一个 .exe 下载链接
    download_url = None
    for asset in assets:
        name = asset.get("name", "")
        if name.lower().endswith(".exe"):
            download_url = asset.get("browser_download_url")
            break
    return tag, download_url

def _download_chunk(url, start, end, dest_base, index):
    """下载单个分块，保存到临时文件"""
    req = urllib.request.Request(url, headers={
        "Range": f"bytes={start}-{end}",
        "User-Agent": f"HeartRateMonitor/{APP_VERSION}"
    })
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = resp.read()
    chunk_file = f"{dest_base}.part{index}"
    with open(chunk_file, 'wb') as f:
        f.write(data)
    return chunk_file


def _download_update_thread(version, download_url, signal):
    """后台线程：多线程分块下载更新文件"""
    tmp_dir = tempfile.gettempdir()
    new_exe = os.path.join(tmp_dir, f"HeartRateMonitor_{version}.exe")

    # 尝试 URL 列表：镜像优先，原链接回退
    urls = [
        f"https://ghm.078465.xyz/{download_url}",
        download_url,
    ]

    for url in urls:
        try:
            # 获取文件大小
            req = urllib.request.Request(url, headers={"User-Agent": f"HeartRateMonitor/{APP_VERSION}"})
            req.method = 'HEAD'
            with urllib.request.urlopen(req, timeout=15) as resp:
                file_size = int(resp.headers.get('Content-Length', 0))
            if file_size <= 0:
                raise Exception("无法获取文件大小")

            # 根据文件大小决定线程数
            num_threads = 4 if file_size > 5 * 1024 * 1024 else 2
            chunk_size = file_size // num_threads
            logger.info(f"多线程下载: {file_size / 1024 / 1024:.1f}MB, {num_threads}线程")

            chunks = []
            with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
                futures = []
                for i in range(num_threads):
                    start = i * chunk_size
                    end = start + chunk_size - 1 if i < num_threads - 1 else file_size - 1
                    futures.append(executor.submit(_download_chunk, url, start, end, new_exe, i))

                for future in concurrent.futures.as_completed(futures):
                    chunks.append(future.result())

            # 合并分块
            with open(new_exe, 'wb') as out:
                for i in range(num_threads):
                    cf = f"{new_exe}.part{i}"
                    with open(cf, 'rb') as f:
                        out.write(f.read())
                    os.remove(cf)

            logger.info(f"下载完成: {new_exe}")
            signal.download_done.emit(new_exe)
            return

        except Exception as e:
            logger.warning(f"下载失败: {e}")
            # 清理残留分块
            for i in range(10):
                try:
                    os.remove(f"{new_exe}.part{i}")
                except:
                    pass
            continue

    logger.error("下载更新失败: 所有链接均不可用")

def _apply_update(new_exe):
    """创建 ANSI 编码的更新批处理并执行"""
    current_exe = sys.executable if getattr(sys, 'frozen', False) else None
    if not current_exe:
        logger.warning("开发模式下不支持自动更新")
        return False

    tmp_dir = tempfile.gettempdir()
    script_path = os.path.join(tmp_dir, "heart_update.bat")
    exe_name = os.path.basename(current_exe)

    # 用短路径规避中文/空格问题
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(260)
        ctypes.windll.kernel32.GetShortPathNameW(current_exe, buf, 260)
        short_current = buf.value
        ctypes.windll.kernel32.GetShortPathNameW(new_exe, buf, 260)
        short_new = buf.value
    except:
        short_new = new_exe
        short_current = current_exe

    bat_content = f"""@echo off
:wait
timeout /t 2 /nobreak >nul
tasklist /FI "IMAGENAME eq {exe_name}" 2>nul | find /I "{exe_name}" >nul
if not errorlevel 1 goto wait
copy /y "{short_new}" "{short_current}" >nul 2>&1
if exist "{short_current}" (
    start "" "{short_current}"
)
del /q "{short_new}" >nul 2>&1
"""
    # 用系统默认编码（Windows ANSI，兼容中文字符）
    with open(script_path, 'w') as f:
        f.write(bat_content)
    subprocess.Popen(
        [script_path],
        creationflags=subprocess.CREATE_NEW_CONSOLE,
        close_fds=True
    )
    logger.info(f"启动更新脚本: {script_path}")
    return True

def _check_update_thread(update_signal):
    """后台线程：检查版本并触发更新"""
    try:
        latest_tag, download_url = _check_latest_release()
        if not latest_tag and not download_url:
            logger.info("检查更新: 未找到 Release 信息")
            return
        logger.debug(f"检查更新: 最新 {latest_tag}, 当前 {APP_VERSION}")
        if _parse_version(latest_tag) > _parse_version(APP_VERSION):
            logger.info(f"发现新版本: {latest_tag}")
            if download_url:
                update_signal.update_ready.emit(latest_tag, download_url)
            else:
                logger.warning(f"新版本 {latest_tag} 无可下载的可执行文件")
        else:
            logger.info(f"已是最新版: {APP_VERSION}")
    except Exception as e:
        http_code = getattr(e, 'code', None)
        if http_code:
            logger.debug(f"检查更新失败 (HTTP {http_code})")
        else:
            logger.debug(f"检查更新失败: {e}")

# ===================== 主监控逻辑 =====================
class HeartRateMonitor:
    def __init__(self):
        self.devices = []
        self.selected_device = None
        self.heart_rate = queue.Queue()
        self.heart_rate.put("--")
        self.connected = False
        self.connecting = False
        self.reconnecting = True
        self.client = None
        self.hr_window = None
        self.tray_icon = None
        self.chart_window = None

        self.hr_history = []
        self.hr_max_value = None
        self.hr_min_value = None
        self.hr_lock = threading.Lock()

        # 心率窗口置顶状态，默认置顶
        self.hr_window_topmost = True

        self._emitter = SignalEmitter()
        self._emitter.scan_finished.connect(self._on_scan_finished)

        # 自动更新
        self._update_signal = _UpdateSignal()
        self._update_signal.update_ready.connect(self._on_update_ready)
        self._update_signal.download_done.connect(self._on_download_done)
        threading.Thread(target=_check_update_thread, args=(self._update_signal,), daemon=True).start()

        self._restore_helper = _RestoreHelper()
        self._restore_helper.restore.connect(self._do_restore)

        self.loop = asyncio.new_event_loop()
        self.loop_thread = threading.Thread(target=self._run_event_loop, daemon=True)
        self.loop_thread.start()

        self.app = QApplication(sys.argv)
        self.app.setStyle("Fusion")

        self.tray_image = self._load_tray_image()

        try:
            self.main_window = MainWindow(self)
            self.main_window.show_with_animation()
        except Exception as e:
            logger.critical(f"主窗口创建失败: {e}")
            traceback.print_exc()
            sys.exit(1)

        logger.info("心率监测应用启动完成")
        self.load_hr_window_position()

        QTimer.singleShot(200, lambda: play_system_sound("Windows Notify Calendar"))

        self.update_timer = QTimer()
        self.update_timer.timeout.connect(self.update_display)
        self.update_timer.start(500)

        QTimer.singleShot(500, self.start_scan)

    def _on_scan_finished(self):
        QTimer.singleShot(1, self.main_window.hide_scan_overlay)

    def load_hr_window_position(self):
        config = load_config()
        if config.has_section('HeartRateWindow'):
            try:
                self.hr_window_pos = (int(config.get('HeartRateWindow', 'x')),
                                      int(config.get('HeartRateWindow', 'y')))
            except:
                self.hr_window_pos = (100, 100)
        else:
            self.hr_window_pos = (100, 100)

    def _run_event_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def _on_update_ready(self, version, download_url):
        """主线程：发现新版本，在后台线程下载（避免阻塞 UI）"""
        logger.info(f"发现新版本: {version}，开始后台下载（使用镜像加速）")
        threading.Thread(
            target=_download_update_thread,
            args=(version, download_url, self._update_signal),
            daemon=True
        ).start()

    def _on_download_done(self, new_exe):
        """主线程：下载完成，应用更新"""
        logger.info(f"下载完成，准备应用更新: {new_exe}")
        try:
            if _apply_update(new_exe):
                logger.info("更新完成，程序即将重启")
                QTimer.singleShot(500, self.quit_app)
            else:
                logger.warning("更新失败：无法应用更新")
        except Exception as e:
            logger.error(f"更新失败: {e}")

    def start_scan(self):
        self.main_window.show_scan_overlay()
        self.main_window.device_list.clear()
        self.main_window.device_list.addItem("正在扫描设备...")
        asyncio.run_coroutine_threadsafe(self._scan_and_update(), self.loop)

    async def _scan_and_update(self):
        try:
            self.devices = await BleakScanner.discover()
            self.main_window.update_device_list(self.devices)
        except Exception as e:
            self.main_window.device_list.clear()
            self.main_window.device_list.addItem(f"扫描失败: {e}")
        finally:
            self._emitter.scan_finished.emit()

    def connect_device(self):
        if self.connected or self.connecting:
            logger.debug("连接设备: 已在连接中，忽略")
            return
        selected = self.main_window.device_list.currentItem()
        if not selected or "未找到" in selected.text():
            logger.debug("连接设备: 未选择有效设备")
            return
        try:
            idx = self.main_window.device_list.currentRow()
            filtered_devices = [d for d in self.devices
                                if d.name and d.name.strip() != "" and d.name.lower() != d.address.lower()]
            self.selected_device = filtered_devices[idx]
            logger.info(f"连接设备: {self.selected_device.name} ({self.selected_device.address})")
        except:
            logger.warning("连接设备: 获取选中设备失败")
            return

        self.connecting = True
        self.reconnecting = True
        self.main_window.set_connecting_state(True)

        if not self.hr_window:
            self.hr_window = HeartRateWindow(self)
            self.hr_window.move(self.hr_window_pos[0], self.hr_window_pos[1])
            # 连接固定状态改变信号，同步复选框
            self.hr_window.fixed_changed.connect(self.main_window.sync_fixed_checkbox)
            self.hr_window.show()

        with self.hr_lock:
            self.hr_history.clear()
            self.hr_max_value = None
            self.hr_min_value = None

        asyncio.run_coroutine_threadsafe(self._monitor_heart_rate(), self.loop)

    def disconnect_device(self):
        logger.info("用户手动断开连接")
        self.reconnecting = False
        if self.client and self.client.is_connected:
            asyncio.run_coroutine_threadsafe(self.client.disconnect(), self.loop)
        self.connected = False
        self.connecting = False
        self.main_window.set_connected_state(False)
        if self.hr_window:
            self.hr_window.update_hr("已断连")
        play_system_sound("Windows Hardware Remove")

    async def _monitor_heart_rate(self):
        while self.reconnecting:
            try:
                if not self.selected_device:
                    break
                device = await BleakScanner.find_device_by_address(
                    self.selected_device.address, timeout=10.0
                )
                if device is None:
                    raise Exception("设备不在范围内")

                disconnected_event = asyncio.Event()
                async with BleakClient(device, disconnected_callback=lambda c: disconnected_event.set()) as client:
                    self.client = client
                    hr_uuid = None
                    for service in client.services:
                        for char in service.characteristics:
                            if "Heart Rate Measurement" in char.description:
                                hr_uuid = char.uuid
                                break
                    if not hr_uuid:
                        raise Exception("未找到心率特征值")

                    await client.start_notify(hr_uuid, self._on_heart_rate_update)

                    self.connected = True
                    self.connecting = False
                    self.main_window.set_connected_state(True)
                    if self.hr_window:
                        self.hr_window.update_hr("--")
                    logger.info("蓝牙设备连接成功")
                    play_system_sound("Windows Hardware Insert")

                    await disconnected_event.wait()
            except Exception as e:
                logger.error(f"连接异常: {e}")
                play_system_sound("Windows Hardware Fail")
            finally:
                was_connected = self.connected
                self.client = None
                self.connected = False
                self.connecting = False
                self.main_window.set_connected_state(False)
                if self.hr_window:
                    self.hr_window.update_hr("已断连")
                if was_connected:
                    logger.info("蓝牙设备断开连接")
                    play_system_sound("Windows Hardware Remove")
            if not self.reconnecting:
                break
            await asyncio.sleep(5)

    def _on_heart_rate_update(self, char: BleakGATTCharacteristic, data: bytearray):
        try:
            flags = data[0]
            hr_format = flags & 0x01
            index = 1
            if hr_format == 0:
                hr = data[1]
            else:
                hr = int.from_bytes(data[1:3], byteorder='little')
            self.heart_rate.put(str(hr))

            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with self.hr_lock:
                self.hr_history.append((timestamp, hr))
                if self.hr_max_value is None or hr > self.hr_max_value:
                    self.hr_max_value = hr
                if self.hr_min_value is None or hr < self.hr_min_value:
                    self.hr_min_value = hr
        except:
            self.heart_rate.put("ERR")

    def update_display(self):
        try:
            hr = self.heart_rate.get_nowait()
            if self.hr_window:
                self.hr_window.update_hr(hr)
        except queue.Empty:
            pass

    def toggle_fixed(self):
        """右键菜单切换固定（仅固定，不改变置顶）"""
        if self.hr_window:
            self.hr_window.set_fixed(not self.hr_window.fixed)

    def minimize_to_tray(self):
        self.main_window.hide()
        if not self.tray_icon:
            menu = pystray.Menu(
                item('显示主界面', self.restore_window),
                item('固定窗口', self.tray_toggle_fixed,
                     checked=lambda item: self.hr_window.fixed if self.hr_window else False),
                item('退出', self.quit_app)
            )
            self.tray_icon = pystray.Icon(
                "heart_monitor",
                self.tray_image,
                "心率监测",
                menu
            )
            threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def tray_toggle_fixed(self):
        self.toggle_fixed()

    def restore_window(self):
        logger.debug("托盘菜单: 显示主界面")
        # pyqtSignal.emit() 跨线程安全，自动将槽派发到接收者所在线程（Qt 主线程）
        self._restore_helper.restore.emit()

    def _do_restore(self):
        """在主线程中执行窗口恢复操作（不停止托盘图标，避免线程死锁）"""
        self.main_window.setWindowOpacity(1.0)
        self.main_window.show()
        self.main_window.activateWindow()
        self.main_window.setWindowState(self.main_window.windowState() & ~Qt.WindowMinimized)
        logger.info("主窗口已恢复显示")

    def chart_window_closed(self):
        self.chart_window = None

    def show_chart(self):
        if self.chart_window is not None:
            try:
                if self.chart_window.isVisible():
                    self.chart_window.activateWindow()
                    return
            except RuntimeError:
                self.chart_window = None
        self.chart_window = ChartWindow(self)
        self.chart_window.show_with_animation()

    def quit_app(self):
        if self.hr_window:
            self.hr_window.save_position()
        self.reconnecting = False
        if self.client and self.client.is_connected:
            asyncio.run_coroutine_threadsafe(self.client.disconnect(), self.loop)
        if self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)
        # 托盘图标是 daemon 线程，进程退出后自动销毁，不显式 stop（避免死锁）
        if self.chart_window:
            self.chart_window.close()
        QApplication.quit()

    def _load_tray_image(self):
        try:
            return Image.open(resource_path("tray_icon.ico"))
        except:
            image = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
            draw = ImageDraw.Draw(image)
            font = ImageFont.load_default()
            draw.text((12, 12), "♥", fill="red", font=font)
            return image

    def run(self):
        try:
            sys.exit(self.app.exec_())
        except Exception as e:
            logger.critical(f"运行时异常: {e}")
            traceback.print_exc()

if __name__ == "__main__":
    try:
        monitor = HeartRateMonitor()
        monitor.run()
    except Exception as e:
        logger.critical(f"程序崩溃: {e}")
        traceback.print_exc()
        input("按回车键退出...")
