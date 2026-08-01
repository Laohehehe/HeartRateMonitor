# -*- coding: utf-8 -*-
"""心率监测 HeartRateMonitor — 入口脚本。

程序逻辑已模块化至 heart/ 包。本文件仅作为启动入口（PyInstaller 入口名保持 Heart.py）。
"""
import traceback

from heart.monitor import HeartRateMonitor


if __name__ == "__main__":
    try:
        monitor = HeartRateMonitor()
        monitor.run()
    except Exception as e:
        from heart.logger_setup import logger
        logger.critical(f"程序崩溃: {e}")
        traceback.print_exc()
        input("按回车键退出...")
