import sys
import os
import asyncio
import threading
import queue
import configparser
import traceback
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

# ===================== 资源路径 =====================
def resource_path(relative_path):
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)

# ===================== 配置文件管理 =====================
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
        screen_geometry = QApplication.primaryScreen().geometry()
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
    
    @pyqtProperty(int)
    def current_width(self):
        return self._current_width
    
    @current_width.setter
    def current_width(self, value):
        self._current_width = value
        self.update()
    
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
        self.min_btn.clicked.connect(self.showMinimized)
        self.min_btn.installEventFilter(self)
        title_bar.addWidget(self.min_btn)

        self.tray_btn = QPushButton("◉")
        self.tray_btn.setFixedSize(30, 30)
        self.tray_btn.setToolTip("最小化到系统托盘")
        self.tray_btn.setStyleSheet(btn_base_style + "QPushButton:hover { background: #E0E0E0; border-radius: 4px; }")
        self.tray_btn.clicked.connect(self.monitor.minimize_to_tray)
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
        self.close_btn.clicked.connect(self.monitor.quit_app)
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
        self.connect_btn.clicked.connect(self.monitor.connect_device)
        self.disconnect_btn.setStyleSheet(self._disabled_button_style())
        self.disconnect_btn.setEnabled(False)
        self.disconnect_btn.clicked.connect(self.monitor.disconnect_device)
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
        if hasattr(self, 'anim_show') and self.anim_show.state() == QPropertyAnimation.Running:
            self.anim_show.stop()
        self.anim_show = QPropertyAnimation(self.overlay_effect, b"opacity")
        self.anim_show.setDuration(300)
        self.anim_show.setStartValue(self.overlay_effect.opacity())
        self.anim_show.setEndValue(1.0)
        self.anim_show.setEasingCurve(QEasingCurve.OutCubic)
        self.anim_show.start()

    def hide_scan_overlay(self):
        if hasattr(self, 'anim_hide') and self.anim_hide.state() == QPropertyAnimation.Running:
            self.anim_hide.stop()
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
        self.monitor.minimize_to_tray()
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
        screen_geometry = QApplication.primaryScreen().geometry()
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
        self.loop = None
        self.main_window = None
        self.chart_window = None

        self.hr_history = []
        self.hr_max_value = None
        self.hr_min_value = None
        self.hr_lock = threading.Lock()

        # 心率窗口置顶状态，默认置顶
        self.hr_window_topmost = True

        self._emitter = SignalEmitter()
        self._emitter.scan_finished.connect(self._on_scan_finished)

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
            print(f"主窗口创建失败: {e}")
            traceback.print_exc()
            sys.exit(1)

        self.load_hr_window_position()

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
            return
        selected = self.main_window.device_list.currentItem()
        if not selected or "未找到" in selected.text():
            return
        try:
            idx = self.main_window.device_list.currentRow()
            filtered_devices = [d for d in self.devices
                                if d.name and d.name.strip() != "" and d.name.lower() != d.address.lower()]
            self.selected_device = filtered_devices[idx]
        except:
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
        self.reconnecting = False
        if self.client and self.client.is_connected:
            asyncio.run_coroutine_threadsafe(self.client.disconnect(), self.loop)
        self.connected = False
        self.connecting = False
        self.main_window.set_connected_state(False)
        if self.hr_window:
            self.hr_window.update_hr("已断连")

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

                    await disconnected_event.wait()
            except Exception as e:
                print(f"连接异常: {e}")
            finally:
                self.client = None
                self.connected = False
                self.connecting = False
                self.main_window.set_connected_state(False)
                if self.hr_window:
                    self.hr_window.update_hr("已断连")
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
        if self.tray_icon:
            self.tray_icon.stop()
            self.tray_icon = None
        self.main_window.show()
        self.main_window.activateWindow()

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
        if self.tray_icon:
            self.tray_icon.stop()
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
            print(f"运行时异常: {e}")
            traceback.print_exc()

if __name__ == "__main__":
    try:
        monitor = HeartRateMonitor()
        monitor.run()
    except Exception as e:
        print("程序崩溃:", e)
        traceback.print_exc()
        input("按回车键退出...")