# -*- coding: utf-8 -*-
"""退出时清理临时文件：heart_*.log 与 PyInstaller 的 _MEI* 解压残留。

日志仅供开发排障，普通用户不需要；_MEI* 为 onefile 解压残留（正常退出由
bootloader 自清理，被强杀/崩溃时残留）。均在程序正常退出时清理。
"""
# pyright: reportAttributeAccessIssue=false
import os
import sys
import glob
import logging
import shutil


def _close_loggers():
    """关闭所有 FileHandler，释放日志文件句柄（Windows 下打开的文件无法删除）。"""
    root = logging.getLogger("Heart")
    for handler in list(root.handlers):
        try:
            handler.close()
            root.removeHandler(handler)
        except Exception:
            pass


def cleanup_temp_files():
    """清理 %TEMP% 下的 heart_*.log 与 _MEI* 解压残留文件夹。

    被其他进程占用/锁定的项会自动跳过（删除失败即忽略）。
    当前进程的 _MEI 文件夹由 PyInstaller bootloader 负责，这里跳过。
    """
    tmp = os.environ.get('TMP') or os.environ.get('TEMP') or os.path.expanduser("~")

    # 1. 关闭日志句柄，释放文件锁
    _close_loggers()

    # 2. 删除所有 heart_*.log（本程序日志）
    for f in glob.glob(os.path.join(tmp, "heart_*.log")):
        try:
            os.remove(f)
        except OSError:
            pass

    # 3. 删除 _MEI* 解压残留（跳过当前进程正在用的）
    current_mei = getattr(sys, '_MEIPASS', None)
    for d in glob.glob(os.path.join(tmp, "_MEI*")):
        if current_mei and os.path.normpath(d) == os.path.normpath(current_mei):
            continue
        try:
            shutil.rmtree(d)
        except OSError:
            pass
