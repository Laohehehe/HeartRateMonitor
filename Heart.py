# -*- coding: utf-8 -*-
"""心率监测 HeartRateMonitor — 入口脚本。

程序逻辑已模块化至 heart/ 包。本文件仅作为启动入口（PyInstaller 入口名保持 Heart.py）。
"""
import sys
import traceback

from heart.monitor import HeartRateMonitor


if __name__ == "__main__":
    # 单实例：若已有实例运行，唤醒其主窗口后本实例退出
    from heart.singleton import is_duplicate, signal_activate
    if is_duplicate():
        signal_activate()
        sys.exit(0)

    try:
        monitor = HeartRateMonitor()
        monitor.run()
    except Exception as e:
        from heart.logger_setup import logger
        logger.critical(f"程序崩溃: {e}")
        traceback.print_exc()
        # 无控制台（--windowed）下 input() 会抛错，改用 sys.exit 正常结束
        sys.exit(1)
