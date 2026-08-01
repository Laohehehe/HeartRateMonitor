# -*- coding: utf-8 -*-
"""GitHub 自动更新：版本检查、多线程分块下载、PowerShell 应用更新。"""
# pyright: reportAttributeAccessIssue=false
import os
import sys
import json
import base64
import tempfile
import subprocess
import urllib.request
import concurrent.futures

from PyQt5.QtCore import QObject, pyqtSignal

from .constants import APP_VERSION, GITHUB_REPO
from .logger_setup import logger

__all__ = [
    "UpdateSignal",
    "_parse_version",
    "_check_latest_release",
    "_download_single",
    "_download_update_thread",
    "_apply_update",
    "_check_update_thread",
]


class UpdateSignal(QObject):
    """后台线程 → 主线程的更新信号。"""
    update_ready = pyqtSignal(str, str)  # (version, download_url)
    download_done = pyqtSignal(str)      # (local_exe_path)


def _parse_version(v):
    """将版本字符串转为可比较的整数元组，如 '26.7.28' -> (26, 7, 28)。"""
    try:
        return tuple(int(x) for x in v.lstrip('vV').split('.'))
    except Exception:
        return (0,)


def _check_latest_release():
    """查询 GitHub 最新 Release，返回 (tag_name, download_url) 或 (None, None)。"""
    url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
    req = urllib.request.Request(url, headers={
        "User-Agent": f"HeartRateMonitor/{APP_VERSION}",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode('utf-8'))
    tag = data.get("tag_name", "")
    download_url = None
    for asset in data.get("assets", []):
        if asset.get("name", "").lower().endswith(".exe"):
            download_url = asset.get("browser_download_url")
            break
    return tag, download_url


def _download_chunk(url, start, end, dest_base, index):
    """下载单个分块，保存到临时文件。"""
    req = urllib.request.Request(url, headers={
        "Range": f"bytes={start}-{end}",
        "User-Agent": f"HeartRateMonitor/{APP_VERSION}",
    })
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = resp.read()
    chunk_file = f"{dest_base}.part{index}"
    with open(chunk_file, 'wb') as f:
        f.write(data)
    return chunk_file


def _clean_partials(dest):
    """清理残留的分块临时文件。"""
    for i in range(10):
        try:
            os.remove(f"{dest}.part{i}")
        except OSError:
            pass


def _download_single(url, new_exe):
    """从单个 URL 下载：优先多线程分块，HEAD 失败或大小校验失败时回退单线程。"""
    file_size = 0
    try:
        req = urllib.request.Request(url, headers={"User-Agent": f"HeartRateMonitor/{APP_VERSION}"})
        req.method = 'HEAD'
        with urllib.request.urlopen(req, timeout=15) as resp:
            file_size = int(resp.headers.get('Content-Length', 0) or 0)
    except Exception as e:
        logger.warning(f"HEAD 获取大小失败: {e}")

    if file_size > 0:
        num_threads = 4 if file_size > 5 * 1024 * 1024 else 2
        chunk_size = file_size // num_threads
        logger.info(f"多线程下载: {file_size / 1024 / 1024:.1f}MB, {num_threads}线程")

        with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = []
            for i in range(num_threads):
                start = i * chunk_size
                end = start + chunk_size - 1 if i < num_threads - 1 else file_size - 1
                futures.append(executor.submit(_download_chunk, url, start, end, new_exe, i))
            for future in concurrent.futures.as_completed(futures):
                future.result()  # 抛出分块下载异常

        # 合并分块
        with open(new_exe, 'wb') as out:
            for i in range(num_threads):
                cf = f"{new_exe}.part{i}"
                with open(cf, 'rb') as f:
                    out.write(f.read())
                os.remove(cf)

        # 校验文件大小
        actual = os.path.getsize(new_exe)
        if actual != file_size:
            raise Exception(f"文件大小校验失败: 期望 {file_size}, 实际 {actual}")
    else:
        # HEAD 失败或大小未知，降级为单线程下载
        logger.info(f"单线程下载: {url[:60]}...")
        urllib.request.urlretrieve(url, new_exe)

    logger.info(f"下载完成: {new_exe}")


def _download_update_thread(version, download_url, signal):
    """后台线程：多线程分块下载更新文件。"""
    tmp_dir = tempfile.gettempdir()
    new_exe = os.path.join(tmp_dir, f"HeartRateMonitor_{version}.exe")

    # 尝试 URL 列表：镜像优先，原链接回退
    urls = [
        f"https://ghm.078465.xyz/{download_url}",
        download_url,
    ]

    for url in urls:
        try:
            _download_single(url, new_exe)
            signal.download_done.emit(new_exe)
            return
        except Exception as e:
            logger.warning(f"下载失败 ({url[:60]}...): {e}")
            _clean_partials(new_exe)
            try:
                if os.path.exists(new_exe):
                    os.remove(new_exe)
            except OSError:
                pass
            continue

    logger.error("下载更新失败: 所有链接均不可用")


def _apply_update(new_exe):
    """通过 PowerShell EncodedCommand 应用更新。

    脚本经 Base64(UTF-16LE) 编码传给 PowerShell，彻底规避中文路径/引号/编码问题。
    """
    current_exe = sys.executable if getattr(sys, 'frozen', False) else None
    if not current_exe:
        logger.warning("开发模式下不支持自动更新")
        return False

    exe_name = os.path.splitext(os.path.basename(current_exe))[0]
    ps_script = f"""
$src = "{new_exe}"
$dst = "{current_exe}"
$exe = "{exe_name}"
Start-Sleep -Seconds 3
$n = 0
while ((Get-Process -Name $exe -ErrorAction SilentlyContinue) -and $n -lt 60) {{
    Start-Sleep -Seconds 1
    $n++
}}
try {{
    Copy-Item -LiteralPath $src -Destination $dst -Force
    Start-Process -FilePath $dst
}} catch {{
    $err = $_ | Out-String
    Add-Content -Path "$env:TEMP\\heart_update_error.log" -Value $err -Encoding UTF8
}}
Remove-Item -LiteralPath $src -Force -ErrorAction SilentlyContinue
"""
    encoded = base64.b64encode(ps_script.encode('utf-16-le')).decode('ascii')
    # 用 CREATE_NO_WINDOW 隐藏窗口（避免 -WindowStyle Hidden 在某些环境下失败）
    subprocess.Popen(
        ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        creationflags=subprocess.CREATE_NO_WINDOW,
        close_fds=True
    )
    logger.info("启动更新进程 (PowerShell EncodedCommand)")
    return True


def _check_update_thread(update_signal):
    """后台线程：检查版本并触发更新。"""
    try:
        latest_tag, download_url = _check_latest_release()
        if not latest_tag and not download_url:
            logger.info("检查更新: 未找到 Release 信息")
            return
        logger.debug(f"检查更新: 最新 {latest_tag}, 当前 {APP_VERSION}")
        if _parse_version(latest_tag) > _parse_version(APP_VERSION):
            logger.info(f"发现新版本: {latest_tag}")
            if download_url:
                update_signal.update_ready.emit(latest_tag, download_url)
            else:
                logger.warning(f"新版本 {latest_tag} 无可下载的可执行文件")
        else:
            logger.info(f"已是最新版: {APP_VERSION}")
    except Exception as e:
        http_code = getattr(e, 'code', None)
        if http_code:
            logger.debug(f"检查更新失败 (HTTP {http_code})")
        else:
            logger.debug(f"检查更新失败: {e}")
