# -*- coding: utf-8 -*-
"""心率监测 HeartRateMonitor — 模块化包。

模块结构：
    constants.py      版本号、仓库、系统声音事件映射
    logger_setup.py   日志系统（%TEMP%\\heart_yyyy-MM-dd_HH-MM-SS.log）
    config.py         应用配置（%APPDATA%\\Heart\\config.ini）
    resources.py      资源路径（PyInstaller 冻结与源码双支持）
    sounds.py         Windows 系统声音播放
    updater.py        GitHub 版本检查 / 多线程下载 / 应用更新
    monitor.py        HeartRateMonitor 主逻辑
    ui/               界面相关（调度器、悬浮窗、曲线、进度条、主窗口）
"""

from .constants import APP_VERSION, GITHUB_REPO

__version__ = APP_VERSION
__all__ = ["APP_VERSION", "GITHUB_REPO"]
