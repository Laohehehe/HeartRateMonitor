# -*- coding: utf-8 -*-
"""主界面窗口。

- 日间/夜间双主题：通过标题栏 ☀/🌙 按钮切换，主题持久化到 config
- 主题图标用 SVG 渲染（太阳 / 月牙），不依赖 emoji 字体与外部资源
- 主题切换带淡入淡出过渡动画
"""
# pyright: reportAttributeAccessIssue=false
import os
import sys
from typing import TYPE_CHECKING

from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QListWidget, QFrame, QCheckBox, QMessageBox, QGraphicsOpacityEffect, QApplication
)
from PyQt5.QtCore import (
    Qt, QPropertyAnimation, QRect, QSize, QEasingCurve, QEvent, QByteArray
)
from PyQt5.QtGui import (
    QFont, QPixmap, QImage, QPainter, QIcon
)
from PyQt5.QtSvg import QSvgRenderer
from PIL import Image

from ..config import load_theme, save_theme
from ..fonts import get_app_font_family
from ..logger_setup import logger
from ..resources import resource_path
from .progress_bar import ModernProgressBar

if TYPE_CHECKING:
    from ..monitor import HeartRateMonitor

# ===================== 主题图标 SVG =====================
_SUN_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="24" height="24" '
            'fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
            '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4 12H2M22 12h-2M4.9 4.9l-1.4-1.4'
            'M20.5 20.5l-1.4-1.4M4.9 19.1l-1.4 1.4M20.5 3.5l-1.4 1.4"/></svg>')

_MOON_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="24" height="24" '
             'fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
             '<path d="M21 12.8A9 9 0 1 1 11.2 3 7 7 0 0 0 21 12.8z"/></svg>')

_MIN_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="24" height="24" '
            'fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
            '<path d="M5 12h14"/></svg>')

_CLOSE_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="24" height="24" '
              'fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
              '<path d="M18 6L6 18M6 6l12 12"/></svg>')

# ===================== 主题定义 =====================
_THEMES = {
    'light': {
        'card_bg': '#F0F0F0',
        'card_border': '#D0D0D0',
        'text_color': '#333333',
        'title_color': '#0078D4',
        'list_bg': '#FFFFFF',
        'list_border': '#E0E0E0',
        'item_text': '#333333',
        'capsule_bg': '#E8E8E8',
        'btn_text': '#333333',
        'overlay_bg': 'rgba(255, 255, 255, 180)',
    },
    'dark': {
        'card_bg': '#1E1E1E',
        'card_border': '#3A3A3A',
        'text_color': '#E8E8E8',
        'title_color': '#4CC2FF',
        'list_bg': '#2A2A2A',
        'list_border': '#3A3A3A',
        'item_text': '#E8E8E8',
        'capsule_bg': '#2E2E2E',
        'btn_text': '#E8E8E8',
        'overlay_bg': 'rgba(30, 30, 30, 190)',
    },
}


class MainWindow(QMainWindow):
    def __init__(self, monitor: "HeartRateMonitor"):
        super().__init__()
        self.monitor = monitor
        self.btn_orig_geos = {}
        self._theme = load_theme()
        self.init_ui()

    # ===================== 主题 =====================
    def _theme_color(self, key):
        return _THEMES[self._theme][key]

    def toggle_theme(self):
        """☀/🌙 按钮：切换日间/夜间并持久化。"""
        new_theme = 'dark' if self._theme == 'light' else 'light'
        self.set_theme(new_theme)

    def set_theme(self, theme):
        if theme not in _THEMES:
            theme = 'light'
        if theme == self._theme:
            return
        self._theme = theme
        save_theme(theme)
        self._animate_theme_change()

    def _animate_theme_change(self):
        """主题切换过渡：先整体淡出 → 应用新主题 → 淡入。"""
        fade = getattr(self, '_theme_fade', None)
        if fade is not None and fade.state() == QPropertyAnimation.Running:
            fade.stop()
        central = self.centralWidget()
        assert central is not None
        effect = QGraphicsOpacityEffect(central)
        central.setGraphicsEffect(effect)
        self._theme_effect = effect
        fade = QPropertyAnimation(effect, b"opacity")
        fade.setDuration(120)
        fade.setStartValue(1.0)
        fade.setEndValue(0.0)
        fade.setEasingCurve(QEasingCurve.InOutQuad)
        fade.finished.connect(self._on_theme_faded_out)
        fade.start()
        self._theme_fade = fade

    def _on_theme_faded_out(self):
        """主题已淡出：应用新主题并淡入。"""
        self._apply_theme()
        fade = self._theme_fade
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.finished.disconnect(self._on_theme_faded_out)
        fade.finished.connect(self._on_theme_faded_in)
        fade.start()

    def _on_theme_faded_in(self):
        """主题淡入完成：移除过渡 effect，恢复正常渲染。"""
        central = self.centralWidget()
        assert central is not None
        central.setGraphicsEffect(None)

    def shutdown(self):
        """退出前清理：停止主题过渡动画并移除 effect，避免析构期 use-after-free 崩溃。"""
        fade = getattr(self, '_theme_fade', None)
        if fade is not None:
            if fade.state() == QPropertyAnimation.Running:
                fade.stop()
            try:
                fade.finished.disconnect()
            except TypeError:
                pass
        central = self.centralWidget()
        assert central is not None
        central.setGraphicsEffect(None)

    def _svg_icon(self, svg_str, color):
        """将 SVG 渲染为 QIcon（把 currentColor 替换为指定颜色）。"""
        svg = svg_str.replace('currentColor', color)
        renderer = QSvgRenderer(QByteArray(svg.encode('utf-8')))
        pm = QPixmap(24, 24)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        renderer.render(p)
        p.end()
        return QIcon(pm)

    def _theme_icon(self, target_theme):
        """返回主题切换按钮图标：日间界面显示月牙（切夜间），夜间界面显示太阳（切日间）。"""
        svg = _SUN_SVG if target_theme == 'light' else _MOON_SVG
        return self._svg_icon(svg, '#F5C518')

    def _apply_theme(self):
        """按当前主题重新应用所有控件样式。"""
        t = _THEMES[self._theme]

        self.card.setStyleSheet(f"""
            QFrame {{
                background: {t['card_bg']};
                border-radius: 10px;
                border: 1px solid {t['card_border']};
            }}
        """)

        self.title_label.setStyleSheet(f"color: {t['title_color']}; background: transparent;")

        btn_base = f"""
            QPushButton {{
                background: transparent;
                border: none;
                font-size: 16px;
                padding: 5px;
                color: {t['btn_text']};
            }}
        """
        hover_bg = '#E0E0E0' if self._theme == 'light' else '#3A3A3A'
        # 主题按钮：透明背景（btn_base 无背景色）+ SVG 图标
        self.theme_btn.setStyleSheet(btn_base + f"QPushButton:hover {{ background: {hover_bg}; border-radius: 4px; }}")
        target = 'dark' if self._theme == 'light' else 'light'
        self.theme_btn.setIcon(self._theme_icon(target))
        self.theme_btn.setToolTip("切换到夜间模式" if target == 'dark' else "切换到日间模式")

        # 最小化 / 关闭按钮：SVG 图标，颜色随主题
        self.min_btn.setStyleSheet(btn_base + f"QPushButton:hover {{ background: {hover_bg}; border-radius: 4px; }}")
        self.min_btn.setIcon(self._svg_icon(_MIN_SVG, t['btn_text']))
        self.close_btn.setStyleSheet(btn_base + "QPushButton:hover { background: #E81123; color: white; border-radius: 4px; }")
        self.close_btn.setIcon(self._svg_icon(_CLOSE_SVG, t['btn_text']))

        self.device_list.setStyleSheet(f"""
            QListWidget {{
                background: {t['list_bg']};
                border: 1px solid {t['list_border']};
                border-radius: 8px;
                padding: 5px;
                color: {t['item_text']};
            }}
            QListWidget::item:selected {{
                background: #0078D4;
                color: white;
                border-radius: 4px;
            }}
        """)

        capsule_style = f"""
            QFrame#capsule {{
                background: {t['capsule_bg']};
                border-radius: 14px;
                padding: 6px 15px;
                border: none;
            }}
        """
        self.capsule1.setStyleSheet(capsule_style)
        self.capsule2.setStyleSheet(capsule_style)

        indicator_off = '#CCCCCC' if self._theme == 'light' else '#4A4A4A'
        chk_style = f"""
            QCheckBox {{
                spacing: 8px;
                color: {t['text_color']};
                background: transparent;
            }}
            QCheckBox::indicator {{
                width: 18px;
                height: 18px;
                border-radius: 9px;
                background-color: {indicator_off};
            }}
            QCheckBox::indicator:checked {{
                background-color: #9ACD32;
            }}
        """
        self.autostart_check.setStyleSheet(chk_style)
        self.topmost_fixed_check.setStyleSheet(chk_style)

        self.scan_btn.setStyleSheet(self._button_style())
        self.connect_btn.setStyleSheet(self._button_style())
        self.disconnect_btn.setStyleSheet(self._disabled_button_style())

        self.save_pos_btn.setStyleSheet(self._secondary_button_style())
        self.chart_btn.setStyleSheet(self._chart_button_style())

        self.overlay.setStyleSheet(f"background: {t['overlay_bg']}; border-radius: 10px;")

    # ===================== UI 构建 =====================
    def init_ui(self):
        family = get_app_font_family()
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
        main_layout.addWidget(self.card)

        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(20, 20, 20, 20)
        card_layout.setSpacing(10)

        # 标题栏
        title_bar = QHBoxLayout()
        self.title_label = QLabel("心率监测")
        self.title_label.setFont(QFont(family, 18, QFont.Bold))
        title_bar.addWidget(self.title_label, alignment=Qt.AlignLeft)
        title_bar.addStretch()

        # 主题切换按钮（☀/🌙）
        self.theme_btn = QPushButton()
        self.theme_btn.setFixedSize(30, 30)
        self.theme_btn.setToolTip("切换日间/夜间模式")
        self.theme_btn.clicked.connect(self.toggle_theme)
        self.theme_btn.installEventFilter(self)
        title_bar.addWidget(self.theme_btn)

        # 最小化按钮：点击直接最小化到系统托盘（原“托盘”按钮功能已并入）
        self.min_btn = QPushButton()
        self.min_btn.setFixedSize(30, 30)
        self.min_btn.setToolTip("最小化到系统托盘")
        self.min_btn.clicked.connect(self.animate_to_tray)
        self.min_btn.installEventFilter(self)
        title_bar.addWidget(self.min_btn)

        self.close_btn = QPushButton()
        self.close_btn.setFixedSize(30, 30)
        self.close_btn.setToolTip("退出")
        self.close_btn.clicked.connect(self.animate_quit)
        self.close_btn.installEventFilter(self)
        title_bar.addWidget(self.close_btn)

        card_layout.addLayout(title_bar)

        # 扫描按钮
        self.scan_btn = QPushButton("🔍  扫描设备")
        self.scan_btn.setFont(QFont(family, 10))
        self.scan_btn.clicked.connect(self.monitor.start_scan)
        card_layout.addWidget(self.scan_btn)

        # 设备列表
        self.device_list = QListWidget()
        self.device_list.setFont(QFont(family, 10))
        card_layout.addWidget(self.device_list)

        # 连接/断开按钮
        btn_layout = QHBoxLayout()
        self.connect_btn = QPushButton("连接设备")
        self.disconnect_btn = QPushButton("断开连接")
        for btn in (self.connect_btn, self.disconnect_btn):
            btn.setFont(QFont(family, 10))
        self.connect_btn.clicked.connect(self._on_connect_click)
        self.disconnect_btn.setEnabled(False)
        self.disconnect_btn.clicked.connect(self._on_disconnect_click)
        btn_layout.addWidget(self.connect_btn)
        btn_layout.addWidget(self.disconnect_btn)
        card_layout.addLayout(btn_layout)

        # 功能区域
        func_layout = QVBoxLayout()

        # 开机自启胶囊
        self.capsule1 = QFrame()
        self.capsule1.setObjectName("capsule")
        capsule_layout1 = QHBoxLayout(self.capsule1)
        capsule_layout1.setContentsMargins(0, 0, 0, 0)

        self.autostart_check = QCheckBox("开机自启")
        self.autostart_check.setFont(QFont(family, 9))
        self.autostart_check.stateChanged.connect(self.toggle_autostart)
        self.autostart_check.setChecked(self.is_autostart_enabled())
        capsule_layout1.addWidget(self.autostart_check)
        func_layout.addWidget(self.capsule1)

        # 置顶并固定胶囊
        self.capsule2 = QFrame()
        self.capsule2.setObjectName("capsule")
        capsule_layout2 = QHBoxLayout(self.capsule2)
        capsule_layout2.setContentsMargins(0, 0, 0, 0)

        self.topmost_fixed_check = QCheckBox("置顶并固定")
        self.topmost_fixed_check.setFont(QFont(family, 9))
        self.topmost_fixed_check.stateChanged.connect(self.toggle_topmost_fixed)
        self.topmost_fixed_check.setChecked(False)  # 默认不勾选
        capsule_layout2.addWidget(self.topmost_fixed_check)
        func_layout.addWidget(self.capsule2)

        self.save_pos_btn = QPushButton("保存心率窗口位置")
        self.save_pos_btn.setFont(QFont(family, 9))
        self.save_pos_btn.clicked.connect(self.save_hr_window_position)
        func_layout.addWidget(self.save_pos_btn)

        self.chart_btn = QPushButton("📈 心率曲线")
        self.chart_btn.setFont(QFont(family, 9))
        self.chart_btn.clicked.connect(self.monitor.show_chart)
        func_layout.addWidget(self.chart_btn)

        card_layout.addLayout(func_layout)

        self._drag_pos = None

        # 扫描蒙版
        self.overlay = QFrame(central)
        self.overlay.setGeometry(0, 0, self.width(), self.height())
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

        self._apply_theme()

    def load_scan_image(self):
        try:
            img = Image.open(resource_path("sikao.jpg"))
            img = img.convert("RGBA")
            data = img.tobytes("raw", "RGBA")
            qimg = QImage(data, img.width, img.height, QImage.Format_RGBA8888)
            pix = QPixmap.fromImage(qimg)
            pix = pix.scaledToWidth(150, Qt.SmoothTransformation)
            return pix
        except Exception:
            return None

    # ===================== 扫描蒙版 =====================
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

    # ===================== 置顶/固定 =====================
    def toggle_topmost_fixed(self, state):
        """同时切换置顶与固定状态。"""
        is_checked = state == Qt.Checked
        self.monitor.hr_window_topmost = is_checked
        if self.monitor.hr_window:
            self.monitor.hr_window.set_topmost(is_checked)
            self.monitor.hr_window.set_fixed(is_checked)

    def sync_fixed_checkbox(self, fixed):
        """根据心率窗口的固定状态更新复选框（用于右键菜单同步）。"""
        self.topmost_fixed_check.blockSignals(True)
        self.topmost_fixed_check.setChecked(fixed)
        self.topmost_fixed_check.blockSignals(False)
        # 同时更新置顶标志（保持与固定一致）
        if self.monitor.hr_window:
            self.monitor.hr_window_topmost = fixed
            self.monitor.hr_window.set_topmost(fixed)

    # ===================== 样式 =====================
    def _button_style(self):
        """主操作按钮（扫描/连接），夜间模式使用暗色调 #6475A6。"""
        if self._theme == 'dark':
            return """
                QPushButton {
                    background: #6475A6;
                    color: white;
                    border: none;
                    border-radius: 6px;
                    padding: 10px 20px;
                }
                QPushButton:hover { background: #7484B8; }
                QPushButton:pressed { background: #55638F; }
            """
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
        """断开连接（禁用态）按钮，夜间模式暗化。"""
        if self._theme == 'dark':
            return """
                QPushButton {
                    background: #333333;
                    color: #777777;
                    border: none;
                    border-radius: 6px;
                    padding: 10px 20px;
                }
            """
        return """
            QPushButton {
                background: #CCCCCC;
                color: #666666;
                border: none;
                border-radius: 6px;
                padding: 10px 20px;
            }
        """

    def _secondary_button_style(self):
        """保存心率窗口位置按钮，夜间模式暗化。"""
        if self._theme == 'dark':
            return """
                QPushButton {
                    background: #6475A6;
                    color: white;
                    border: none;
                    border-radius: 4px;
                    padding: 5px;
                }
                QPushButton:hover { background: #7484B8; }
            """
        return """
            QPushButton {
                background: #0078D4;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 5px;
            }
            QPushButton:hover { background: #106EBE; }
        """

    def _chart_button_style(self):
        """心率曲线按钮（粉色系），夜间模式暗化。"""
        if self._theme == 'dark':
            return """
                QPushButton {
                    background: #D589D9;
                    color: white;
                    border: none;
                    border-radius: 4px;
                    padding: 5px;
                }
                QPushButton:hover { background: #E0A0E4; }
            """
        return """
            QPushButton {
                background: #FF69B4;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 5px;
            }
            QPushButton:hover { background: #FF85C0; }
        """

    # ===================== 按钮悬停缩放 =====================
    def eventFilter(self, obj, event):
        if obj in (self.min_btn, self.close_btn, self.theme_btn):
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

    # ===================== 开机自启 =====================
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
            except Exception:
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

    # ===================== 窗口事件 =====================
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

    # ===================== 最小化/托盘/关闭动画 =====================
    def _fade_out_animation(self, callback):
        """窗口淡出动画（windowOpacity），动画结束后调用 callback。"""
        # 先停止并清理上一次动画，避免快速连点时旧动画仍运行/回调重复触发
        old_anim = getattr(self, "_fade_anim", None)
        if old_anim is not None:
            try:
                old_anim.stop()
                old_anim.finished.disconnect()
                old_anim.deleteLater()
            except Exception:
                pass
        self._fade_anim = QPropertyAnimation(self, b"windowOpacity")
        self._fade_anim.setDuration(150)
        self._fade_anim.setStartValue(self.windowOpacity())
        self._fade_anim.setEndValue(0.0)
        self._fade_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._fade_anim.finished.connect(callback)
        self._fade_anim.start()

    def changeEvent(self, event):
        """窗口状态变化时恢复透明度（从最小化恢复时 opacity 可能为0）。"""
        if event.type() == QEvent.WindowStateChange:
            old_state = event.oldState()
            new_state = self.windowState()
            if (old_state & Qt.WindowMinimized) and not (new_state & Qt.WindowMinimized):
                self.setWindowOpacity(1.0)
        super().changeEvent(event)

    def animate_to_tray(self):
        """淡出动画后最小化到系统托盘。"""
        logger.debug("触发: 最小化到托盘")
        self._fade_out_animation(self._do_tray)

    def _do_tray(self):
        self.monitor.minimize_to_tray()

    def animate_quit(self):
        """淡出动画后退出应用。"""
        logger.debug("触发: 关闭按钮")
        self._fade_out_animation(self._do_quit)

    def _do_quit(self):
        self.monitor.quit_app()
