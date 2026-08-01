# -*- coding: utf-8 -*-
"""应用配置读写（%APPDATA%\\Heart\\config.ini）。

除窗口位置外，还用于持久化主题模式（日间/夜间）。
"""
import os
import configparser


def get_config_path():
    appdata = os.environ.get('APPDATA', '')
    folder = os.path.join(appdata, 'Heart')
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, 'config.ini')


CONFIG_FILE = get_config_path()


def load_config():
    config = configparser.ConfigParser()
    config.read(CONFIG_FILE)
    return config


def save_config(config):
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        config.write(f)


# ===================== 主题模式 =====================
DEFAULT_THEME = 'light'
THEMES = ('light', 'dark')


def load_theme():
    """读取主题，非法值回退日间。"""
    try:
        cfg = load_config()
        theme = cfg.get('App', 'theme', fallback=DEFAULT_THEME)
        return theme if theme in THEMES else DEFAULT_THEME
    except Exception:
        return DEFAULT_THEME


def save_theme(theme):
    """持久化主题。"""
    if theme not in THEMES:
        theme = DEFAULT_THEME
    cfg = load_config()
    if 'App' not in cfg:
        cfg.add_section('App')
    cfg.set('App', 'theme', theme)
    save_config(cfg)
