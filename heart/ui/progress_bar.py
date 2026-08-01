# -*- coding: utf-8 -*-
"""扫描加载动画进度条。

动画模型：蓝色滑块保持完整宽度、始终圆角，从右端平滑滑出、从左端滑入，
循环往复。相比旧的“移动→收缩→增长”模型，不会提前缩短、过渡更流畅，
也不会超出控件边界（基于 self.width() 计算）。
"""
# pyright: reportAttributeAccessIssue=false
from PyQt5.QtWidgets import QWidget
from PyQt5.QtCore import Qt, QTimer, pyqtProperty
from PyQt5.QtGui import QPainter, QColor, QPen, QPainterPath


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
        # 只设间隔不启动：空闲时不消耗 CPU，start_animation() 的无参 start() 复用 30ms
        self.timer.setInterval(30)

    def _get_current_width(self):
        return self._current_width

    def _set_current_width(self, value):
        self._current_width = value
        self.update()

    current_width = pyqtProperty(int, _get_current_width, _set_current_width)

    def update_animation(self):
        w = self.width()
        if w <= 0:
            return
        # 循环滑块：保持完整宽度向右移动，完全滑出右端后从左侧外重新进入
        self.position += self.speed
        if self.position >= w:
            self.position = -self.progress_width
        self.current_width = self.progress_width
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w = self.width()
        h = self.height()

        painter.setPen(Qt.NoPen)
        painter.setBrush(self.bg_color)
        painter.drawRoundedRect(0, 0, w, h, 8, 8)

        painter.setPen(QPen(QColor(200, 200, 200), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(0, 0, w, h, 8, 8)

        if self.current_width > 0:
            # 蓝色滑块始终圆角
            path = QPainterPath()
            path.addRoundedRect(self.position, 2, self.current_width, h - 4, 6, 6)

            painter.setPen(Qt.NoPen)
            painter.setBrush(self.progress_color)
            painter.drawPath(path)

            painter.setPen(QPen(QColor(0, 150, 255, 100), 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(path)

    def start_animation(self):
        # 从左侧外开始进入，保证首个动画帧滑块完整圆角可见
        self.position = -self.progress_width
        self.current_width = self.progress_width
        self.timer.start()

    def stop_animation(self):
        self.timer.stop()
