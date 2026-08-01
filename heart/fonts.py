# -*- coding: utf-8 -*-
"""应用内字体（Lolita.ttf）加载与全局应用。

说明：Lolita 为装饰性显示字体（英文/数字效果佳）；中文字符由 Qt 自动回退
到系统字体，不影响可读性。打包时需将 Lolita.ttf 作为数据文件包含。
"""
import os

from .resources import resource_path

_FAMILY = None
_FALLBACK = "Microsoft YaHei"


def get_app_font_family() -> str:
    """惰性加载 Lolita.ttf，返回字体族名；失败回退 'Microsoft YaHei'。

    需在 QApplication 创建后首次调用（QFontDatabase 依赖 GUI 实例）。
    """
    global _FAMILY
    if _FAMILY is not None:
        return _FAMILY
    try:
        from PyQt5.QtGui import QFontDatabase
        path = resource_path("Lolita.ttf")
        if os.path.isfile(path):
            fid = QFontDatabase.addApplicationFont(path)
            families = QFontDatabase.applicationFontFamilies(fid)
            if families:
                _FAMILY = families[0]
                return _FAMILY
    except Exception:
        pass
    _FAMILY = _FALLBACK
    return _FAMILY


def apply_app_font(app) -> str:
    """将应用字体设为全局默认字体（未显式设置字体的控件生效），返回字体族名。"""
    from PyQt5.QtGui import QFont
    family = get_app_font_family()
    app.setFont(QFont(family, 10))
    return family
