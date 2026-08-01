# -*- coding: utf-8 -*-
"""资源路径。

PyInstaller 打包后资源位于 sys._MEIPASS；源码运行时位于脚本所在目录。
"""
import sys
import os


def resource_path(relative_path):
    meipass = getattr(sys, '_MEIPASS', None)
    if meipass is not None:
        return os.path.join(meipass, relative_path)
    # 源码模式：资源位于项目根目录（heart/ 的上一级），不依赖当前工作目录
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_dir, relative_path)
