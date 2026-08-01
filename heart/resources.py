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
    return os.path.join(os.path.abspath("."), relative_path)
