# -*- coding: utf-8 -*-
"""心率曲线画布与曲线窗口。

动画优化：
- 打开：几何缩放 + 透明度淡入并行（QParallelAnimationGroup），OutCubic 更顺滑
- 关闭：几何缩小 + 透明度淡出并行
- 清理：切换动画前停止旧的进行中动画；字体对象缓存，避免每帧创建
"""
# pyright: reportAttributeAccessIssue=false
from typing import TYPE_CHECKING

from PyQt5.QtWidgets import (
    QWidget, QFrame, QVBoxLayout, QHBoxLayout, QPushButton, QApplication
)
from PyQt5.QtCore import (
    Qt, QTimer, QRect, QSize, QEasingCurve, QPropertyAnimation,
    QPointF, QPoint, QParallelAnimationGroup
)
from PyQt5.QtGui import QPainter, QColor, QPen, QPainterPath, QFont

from ..fonts import get_app_font_family

if TYPE_CHECKING:
    from ..monitor import HeartRateMonitor


class ChartCanvas(QWidget):
    def __init__(self, monitor: "HeartRateMonitor"):
        super().__init__()
        self.monitor = monitor
        self.setMouseTracking(True)
        self.hovered_point = None
        # 缓存字体，避免每帧创建
        family = get_app_font_family()
        self._font_small = QFont(family, 9)
        self._font_mid = QFont(family, 12)

    def _smooth_points(self, history):
        if len(history) < 3:
            return history
        smoothed = []
        for i in range(len(history)):
            if i == 0 or i == len(history) - 1:
                smoothed.append(history[i])
            else:
                ts = history[i][0]
                val = (history[i - 1][1] + history[i][1] + history[i + 1][1]) / 3.0
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
            dx = xs[i] - xs[i - 1]
            if dx == 0:
                delta.append(0)
            else:
                delta.append((ys[i] - ys[i - 1]) / dx)

        m = [0.0] * n
        for i in range(1, n - 1):
            if delta[i - 1] * delta[i] > 0:
                m[i] = (delta[i - 1] + delta[i]) / 2.0
            else:
                m[i] = 0.0
        m[0] = delta[0] if n > 1 else 0
        m[-1] = delta[-1] if n > 1 else 0

        for i in range(n - 1):
            if abs(delta[i]) < 1e-9:
                m[i] = m[i + 1] = 0.0
                continue
            alpha = m[i] / delta[i]
            beta = m[i + 1] / delta[i]
            if alpha < 0 or beta < 0:
                m[i] = 0.0
                m[i + 1] = 0.0
            elif alpha * alpha + beta * beta > 9:
                tau = 3.0 / (alpha * alpha + beta * beta) ** 0.5
                m[i] = tau * alpha * delta[i]
                m[i + 1] = tau * beta * delta[i]

        path.moveTo(pts[0])
        for i in range(1, n):
            dx = xs[i] - xs[i - 1]
            cp1 = QPointF(xs[i - 1] + dx / 3.0, ys[i - 1] + m[i - 1] * dx / 3.0)
            cp2 = QPointF(xs[i] - dx / 3.0, ys[i] - m[i] * dx / 3.0)
            path.cubicTo(cp1, cp2, pts[i])
        return path

    def paintEvent(self, _event):
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
                painter.setFont(self._font_mid)
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
        painter.setFont(self._font_small)
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
            painter.setFont(self._font_small)
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


class ChartWindow(QWidget):
    def __init__(self, monitor: "HeartRateMonitor"):
        super().__init__()
        self.monitor = monitor
        self._drag_pos = None
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

    def _stop_running_animations(self):
        """停止进行中的动画，避免新旧动画互相干扰。"""
        for attr in ("_show_group", "_close_group"):
            anim = getattr(self, attr, None)
            if anim is not None and anim.state() == QPropertyAnimation.Running:
                anim.stop()

    def show_with_animation(self):
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        screen_geometry = screen.geometry()
        target_rect = QRect((screen_geometry.width() - self.width()) // 2,
                            (screen_geometry.height() - self.height()) // 2,
                            self.width(), self.height())
        start_rect = QRect(target_rect.center(), QSize(1, 1))

        self._stop_running_animations()
        self.setGeometry(start_rect)
        self.setWindowOpacity(0.0)
        self.show()

        group = QParallelAnimationGroup(self)
        geom_anim = QPropertyAnimation(self, b"geometry")
        geom_anim.setDuration(320)
        geom_anim.setStartValue(start_rect)
        geom_anim.setEndValue(target_rect)
        geom_anim.setEasingCurve(QEasingCurve.OutCubic)
        group.addAnimation(geom_anim)

        opacity_anim = QPropertyAnimation(self, b"windowOpacity")
        opacity_anim.setDuration(320)
        opacity_anim.setStartValue(0.0)
        opacity_anim.setEndValue(1.0)
        opacity_anim.setEasingCurve(QEasingCurve.OutCubic)
        group.addAnimation(opacity_anim)

        group.start()
        self._show_group = group

    def animate_close(self):
        current_geo = self.geometry()
        target_rect = QRect(current_geo.center(), QSize(1, 1))

        self._stop_running_animations()
        group = QParallelAnimationGroup(self)
        geom_anim = QPropertyAnimation(self, b"geometry")
        geom_anim.setDuration(220)
        geom_anim.setStartValue(current_geo)
        geom_anim.setEndValue(target_rect)
        geom_anim.setEasingCurve(QEasingCurve.InCubic)
        group.addAnimation(geom_anim)

        opacity_anim = QPropertyAnimation(self, b"windowOpacity")
        opacity_anim.setDuration(220)
        opacity_anim.setStartValue(self.windowOpacity())
        opacity_anim.setEndValue(0.0)
        opacity_anim.setEasingCurve(QEasingCurve.InCubic)
        group.addAnimation(opacity_anim)

        group.finished.connect(self._on_close_anim_finished)
        group.start()
        self._close_group = group

    def _on_close_anim_finished(self):
        self.monitor.chart_window_closed()
        self.close()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton and self._drag_pos is not None:
            self.move(event.globalPos() - self._drag_pos)
            event.accept()
