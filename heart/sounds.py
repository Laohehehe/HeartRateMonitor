# -*- coding: utf-8 -*-
"""Windows 系统声音播放。

策略：
1. 直接在 Windows Media 目录按文件名匹配（优先，避免注册表指向错误文件）
2. 从注册表读取声音别名对应的 .wav 文件路径，展开环境变量后用 SND_FILENAME 播放
3. 回退到 SND_ALIAS 模式
"""
import os
import ctypes
import winreg

from .constants import SOUND_EVENT_MAP
from .logger_setup import logger

# SND_FILENAME(0x20000) | SND_ASYNC(0x1) | SND_NODEFAULT(0x2)
_SND_FILENAME = 0x00020000 | 0x0001 | 0x0002
# SND_ALIAS(0x10000) | SND_ASYNC(0x1) | SND_NODEFAULT(0x2) | SND_NOSTOP(0x10)
_SND_ALIAS = 0x00010000 | 0x0001 | 0x0002 | 0x0010


def _get_sound_file(alias):
    """从注册表读取声音别名对应的 wav 文件路径。"""
    try:
        key_path = f"AppEvents\\Schemes\\Apps\\.Default\\{alias}\\.default"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            value, _ = winreg.QueryValueEx(key, "")
            return value
    except Exception:
        return None


def play_system_sound(sound_name):
    """异步播放 Windows 系统声音。"""
    aliases = SOUND_EVENT_MAP.get(sound_name, [sound_name])
    tried = []

    # 策略1：直接在 Windows Media 目录按文件名匹配
    system_root = os.environ.get('SystemRoot', 'C:\\Windows')
    media_dir = os.path.join(system_root, 'Media')
    for fname in [f"{sound_name}.wav", f"{sound_name.replace('Windows ', '')}.wav"]:
        filepath = os.path.join(media_dir, fname)
        if os.path.isfile(filepath):
            if ctypes.windll.winmm.PlaySoundW(filepath, None, _SND_FILENAME):
                logger.debug(f"播放声音: {sound_name} (直接文件: {filepath}) 成功")
                return
            tried.append(f"文件({fname})(播放失败)")

    # 策略2：从注册表读取文件路径
    for alias in aliases:
        try:
            sound_file = _get_sound_file(alias)
            if sound_file:
                sound_file = os.path.expandvars(sound_file)
                if os.path.isfile(sound_file):
                    if ctypes.windll.winmm.PlaySoundW(sound_file, None, _SND_FILENAME):
                        logger.debug(f"播放声音: {sound_name} -> {alias} (文件: {sound_file}) 成功")
                        return
                    tried.append(f"{alias}(播放失败)")
                else:
                    tried.append(f"{alias}(文件不存在)")
            else:
                tried.append(f"{alias}(未配置)")
        except Exception as e:
            tried.append(f"{alias}({e})")

    # 策略3：SND_ALIAS 回退
    for alias in aliases:
        try:
            if ctypes.windll.winmm.PlaySoundW(alias, None, _SND_ALIAS):
                logger.debug(f"播放声音: {sound_name} -> {alias} (SND_ALIAS 回退) 成功")
                return
        except Exception:
            pass

    logger.warning(f"播放声音失败: {sound_name} (尝试: {', '.join(tried)})")
