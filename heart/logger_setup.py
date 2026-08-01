# -*- coding: utf-8 -*-
"""日志系统。

日志写入 %TEMP%\\heart_YYYY-MM-DD_HH-MM-SS.log；非打包环境下同时输出到控制台。
模块导入即完成初始化，其余模块通过 ``from .logger_setup import logger`` 使用。
"""
import sys
import os
import logging
from datetime import datetime

_LOG_DIR = os.environ.get('TMP') or os.environ.get('TEMP') or os.path.expanduser("~")
_LOG_FILE = os.path.join(_LOG_DIR, f"heart_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.log")

_logger = logging.getLogger("Heart")
_logger.setLevel(logging.DEBUG)

# 防止重复导入（如热重载）时重复添加 handler
if not _logger.handlers:
    _fh = logging.FileHandler(_LOG_FILE, encoding='utf-8')
    _fh.setLevel(logging.DEBUG)
    _fh.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', datefmt='%H:%M:%S'))
    _logger.addHandler(_fh)

    if not getattr(sys, 'frozen', False):
        _ch = logging.StreamHandler(sys.stdout)
        _ch.setLevel(logging.DEBUG)
        _ch.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', datefmt='%H:%M:%S'))
        _logger.addHandler(_ch)

_logger.info(f"日志文件: {_LOG_FILE}")

logger = _logger


def get_logger():
    """获取全局日志器。"""
    return _logger
