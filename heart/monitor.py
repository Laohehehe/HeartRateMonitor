# -*- coding: utf-8 -*-
"""主监控逻辑 HeartRateMonitor。

负责：BLE 扫描/连接/心率订阅、系统托盘、悬浮窗/曲线窗口生命周期、
自动更新调度，以及所有跨线程 UI 更新的信号接线。
"""
# pyright: reportAttributeAccessIssue=false
import sys
import asyncio
import threading
import queue
import traceback
from datetime import datetime

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import QApplication

from bleak import BleakScanner, BleakClient
from bleak.backends.characteristic import BleakGATTCharacteristic
from PIL import Image, ImageDraw, ImageFont
import pystray
from pystray import MenuItem as item

from .logger_setup import logger
from .resources import resource_path
from .config import load_config
from .sounds import play_system_sound
from .updater import UpdateSignal, _check_update_thread, _download_update_thread, _apply_update
from .toast import signal as toast_signal, notify_update_ready
from .ui.dispatcher import GuiDispatcher, RestoreHelper
from .ui.main_window import MainWindow
from .ui.heart_window import HeartRateWindow
from .ui.chart import ChartWindow

try:
    from .api import AppAPI
except ImportError:  # 打包时排除 heart.api（开发环境隐私）
    AppAPI = None  # type: ignore[assignment]

# 历史记录上限（约 1 小时 @1Hz），防止内存无限增长
HR_HISTORY_LIMIT = 3600


class HeartRateMonitor:
    def __init__(self):
        self.devices = []
        self.selected_device = None
        self.heart_rate = queue.Queue()
        self.heart_rate.put("--")
        self.connected = False
        self.connecting = False
        self.reconnecting = True
        self.client = None
        self.hr_window = None
        self.tray_icon = None
        self.chart_window = None

        self.hr_history = []
        self.hr_max_value = None
        self.hr_min_value = None
        self.hr_lock = threading.Lock()

        # 心率窗口置顶状态，默认置顶
        self.hr_window_topmost = True

        # ===== 跨线程 UI 调度 =====
        self._ui = GuiDispatcher()
        self._ui.scan_finished.connect(self._on_scan_finished)
        self._ui.scan_devices.connect(self._on_scan_devices)
        self._ui.scan_failed.connect(self._on_scan_failed)
        self._ui.connection_changed.connect(self._on_connection_changed)
        self._ui.hr_text.connect(self._on_hr_text)

        # ===== 自动更新 =====
        self._update_signal = UpdateSignal()
        self._update_signal.update_ready.connect(self._on_update_ready)
        self._update_signal.download_done.connect(self._on_download_done)
        threading.Thread(target=_check_update_thread, args=(self._update_signal,), daemon=True).start()

        # ===== 待更新标记（下载完成时设置）=====
        self._pending_update_exe = None
        self._new_version = None

        # ===== 托盘线程 → 主线程 =====
        self._restore_helper = RestoreHelper()
        self._restore_helper.restore.connect(self._do_restore)
        self._restore_helper.toggle_fixed_requested.connect(self._do_toggle_fixed)
        self._restore_helper.quit_requested.connect(self.quit_app)

        # ===== 事件循环线程 =====
        self.loop = asyncio.new_event_loop()
        self.loop_thread = threading.Thread(target=self._run_event_loop, daemon=True)
        self.loop_thread.start()

        # ===== GUI =====
        self.app = QApplication(sys.argv)
        self.app.setStyle("Fusion")

        # 应用内字体（Lolita.ttf，失败自动回退）
        from .fonts import apply_app_font
        apply_app_font(self.app)

        # 窗口/任务栏图标（否则任务栏/标题栏显示默认 exe 图标）
        from PyQt5.QtGui import QIcon
        self.app.setWindowIcon(QIcon(resource_path("tray_icon.ico")))

        self.tray_image = self._load_tray_image()

        try:
            self.main_window = MainWindow(self)
            self.main_window.show_with_animation()
        except Exception as e:
            logger.critical(f"主窗口创建失败: {e}")
            traceback.print_exc()
            sys.exit(1)

        logger.info("心率监测应用启动完成")
        self.load_hr_window_position()

        QTimer.singleShot(200, lambda: play_system_sound("Windows Notify Calendar"))

        self.update_timer = QTimer()
        self.update_timer.timeout.connect(self.update_display)
        self.update_timer.start(500)

        # ===== Toast 通知信号与单实例激活轮询（需 QApplication 已创建）=====
        toast_signal().action.connect(self._on_toast_action)
        self._activate_timer = QTimer()
        self._activate_timer.timeout.connect(self._check_activate_request)
        self._activate_timer.start(1000)

        QTimer.singleShot(500, self.start_scan)

        # 可编程 API（供自动化测试/外部脚本调用，打包版排除）
        if AppAPI is not None:
            self.api = AppAPI(self)
        else:
            self.api = None

    # ===================== 跨线程槽（主线程执行） =====================
    def _on_scan_finished(self):
        QTimer.singleShot(1, self.main_window.hide_scan_overlay)

    def _on_scan_devices(self, devices):
        """主线程：更新设备列表。"""
        self.main_window.update_device_list(devices)

    def _on_scan_failed(self, msg):
        """主线程：显示扫描失败。"""
        self.main_window.device_list.clear()
        self.main_window.device_list.addItem(f"扫描失败: {msg}")

    def _on_connection_changed(self, connected):
        """主线程：更新连接状态按钮。"""
        self.main_window.set_connected_state(connected)

    def _on_hr_text(self, text):
        """主线程：更新悬浮窗心率文本。"""
        if self.hr_window:
            self.hr_window.update_hr(text)

    def load_hr_window_position(self):
        config = load_config()
        if config.has_section('HeartRateWindow'):
            try:
                self.hr_window_pos = (int(config.get('HeartRateWindow', 'x')),
                                      int(config.get('HeartRateWindow', 'y')))
            except Exception:
                self.hr_window_pos = (100, 100)
        else:
            self.hr_window_pos = (100, 100)

    def _run_event_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    # ===================== 自动更新回调 =====================
    def _on_update_ready(self, version, download_url, sha256):
        """主线程：发现新版本，在后台线程下载（避免阻塞 UI）。"""
        logger.info(f"发现新版本: {version}，开始后台下载（使用镜像加速）")
        self._new_version = version
        threading.Thread(
            target=_download_update_thread,
            args=(version, download_url, sha256, self._update_signal),
            daemon=True
        ).start()

    def _on_download_done(self, new_exe):
        """主线程：下载完成，发送通知并等待用户选择更新时机（不自动重启）。"""
        logger.info(f"下载完成，等待用户选择更新时机: {new_exe}")
        self._pending_update_exe = new_exe
        try:
            if not notify_update_ready(self._new_version or "新版本"):
                logger.info("Toast 通知不可用，用户可在退出时自动更新")
        except Exception as e:
            logger.error(f"发送更新通知失败: {e}")

    def _on_toast_action(self, action):
        """主线程：处理 Toast 按钮点击。"""
        if action == "restart":
            logger.info("用户选择立即重启更新")
            self._apply_pending_update(restart=True)
        elif action == "later":
            logger.info("用户选择退出时更新")
        else:
            # 点击通知正文 → 主窗口提到前台
            self._do_restore()

    def _apply_pending_update(self, restart):
        exe = self._pending_update_exe
        if not exe:
            return
        self._pending_update_exe = None
        try:
            if _apply_update(exe, restart=restart):
                if restart:
                    logger.info("更新完成，程序即将重启")
                    QTimer.singleShot(500, self.quit_app)
            else:
                logger.warning("更新失败：无法应用更新")
        except Exception as e:
            logger.error(f"更新失败: {e}")

    # ===================== 蓝牙扫描/连接 =====================
    def start_scan(self):
        self.main_window.show_scan_overlay()
        self.main_window.device_list.clear()
        self.main_window.device_list.addItem("正在扫描设备...")
        asyncio.run_coroutine_threadsafe(self._scan_and_update(), self.loop)

    async def _scan_and_update(self, timeout: float = 2.0):
        try:
            self.devices = await BleakScanner.discover(timeout=timeout)
            self._ui.scan_devices.emit(self.devices)
        except Exception as e:
            logger.error(f"扫描失败: {e}")
            self._ui.scan_failed.emit(str(e))
        finally:
            self._ui.scan_finished.emit()

    def connect_device(self):
        if self.connected or self.connecting:
            logger.debug("连接设备: 已在连接中，忽略")
            return
        selected = self.main_window.device_list.currentItem()
        if not selected or "未找到" in selected.text():
            logger.debug("连接设备: 未选择有效设备")
            return
        try:
            idx = self.main_window.device_list.currentRow()
            filtered_devices = [d for d in self.devices
                                if d.name and d.name.strip() != "" and d.name.lower() != d.address.lower()]
            device = filtered_devices[idx]
        except Exception as e:
            logger.warning(f"连接设备: 获取选中设备失败: {e}")
            return
        self._start_monitoring(device)

    def _start_monitoring(self, device):
        """设置目标设备并启动心率监控（GUI 连接与 API 共用）。"""
        self.selected_device = device
        self.connecting = True
        self.reconnecting = True
        self.main_window.set_connecting_state(True)
        logger.info(f"连接设备: {device.name} (…{device.address[-4:]})")

        if not self.hr_window:
            self.hr_window = HeartRateWindow(self)
            self.hr_window.move(self.hr_window_pos[0], self.hr_window_pos[1])
            # 连接固定状态改变信号，同步复选框
            self.hr_window.fixed_changed.connect(self.main_window.sync_fixed_checkbox)
            self.hr_window.show()

        with self.hr_lock:
            self.hr_history.clear()
            self.hr_max_value = None
            self.hr_min_value = None

        asyncio.run_coroutine_threadsafe(self._monitor_heart_rate(), self.loop)

    def disconnect_device(self):
        logger.info("用户手动断开连接")
        self.reconnecting = False
        if self.client and self.client.is_connected:
            asyncio.run_coroutine_threadsafe(self.client.disconnect(), self.loop)
        self.connected = False
        self.connecting = False
        self.main_window.set_connected_state(False)
        if self.hr_window:
            self.hr_window.update_hr("已断连")
        play_system_sound("Windows Hardware Remove")

    async def _monitor_heart_rate(self):
        while self.reconnecting:
            try:
                if not self.selected_device:
                    break
                device = await BleakScanner.find_device_by_address(
                    self.selected_device.address, timeout=10.0
                )
                if device is None:
                    raise Exception("设备不在范围内")

                disconnected_event = asyncio.Event()
                async with BleakClient(device, disconnected_callback=lambda _c: disconnected_event.set()) as client:
                    self.client = client
                    hr_uuid = None
                    for service in client.services:
                        for char in service.characteristics:
                            if (char.uuid.lower() == "00002a37-0000-1000-8000-00805f9b34fb"
                                    or "Heart Rate Measurement" in char.description):
                                hr_uuid = char.uuid
                                break
                        if hr_uuid:
                            break
                    if not hr_uuid:
                        raise Exception("未找到心率特征值")

                    await client.start_notify(hr_uuid, self._on_heart_rate_update)

                    self.connected = True
                    self.connecting = False
                    self._ui.connection_changed.emit(True)
                    self._ui.hr_text.emit("--")
                    logger.info("蓝牙设备连接成功")
                    play_system_sound("Windows Hardware Insert")

                    await disconnected_event.wait()
            except Exception as e:
                logger.error(f"连接异常: {e}")
                play_system_sound("Windows Hardware Fail")
            finally:
                was_connected = self.connected
                self.client = None
                self.connected = False
                self.connecting = False
                self._ui.connection_changed.emit(False)
                self._ui.hr_text.emit("已断连")
                if was_connected:
                    logger.info("蓝牙设备断开连接")
                    play_system_sound("Windows Hardware Remove")
            if not self.reconnecting:
                break
            await asyncio.sleep(5)

    def _on_heart_rate_update(self, _char: BleakGATTCharacteristic, data: bytearray):
        try:
            flags = data[0]
            hr_format = flags & 0x01
            if hr_format == 0:
                hr = data[1]
            else:
                hr = int.from_bytes(data[1:3], byteorder='little')
            self.heart_rate.put(str(hr))

            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with self.hr_lock:
                self.hr_history.append((timestamp, hr))
                if len(self.hr_history) > HR_HISTORY_LIMIT:
                    self.hr_history.pop(0)
                if self.hr_max_value is None or hr > self.hr_max_value:
                    self.hr_max_value = hr
                if self.hr_min_value is None or hr < self.hr_min_value:
                    self.hr_min_value = hr
        except (IndexError, ValueError):
            self.heart_rate.put("ERR")
            logger.debug("心率数据解析失败", exc_info=True)
        except Exception:
            # 兜底：异常数据格式不应导致程序崩溃
            self.heart_rate.put("ERR")
            logger.debug("心率数据解析异常", exc_info=True)

    def update_display(self):
        # 循环取空队列，避免高频率数据积压导致显示延迟
        hr = None
        while True:
            try:
                hr = self.heart_rate.get_nowait()
            except queue.Empty:
                break
        if hr is not None and self.hr_window:
            self.hr_window.update_hr(hr)

    # ===================== 固定 / 托盘 / 恢复 =====================
    def toggle_fixed(self):
        """切换固定（仅固定，不改变置顶）。由主线程调用。"""
        if self.hr_window:
            self.hr_window.set_fixed(not self.hr_window.fixed)

    def minimize_to_tray(self):
        self.main_window.hide()
        if not self.tray_icon:
            menu = pystray.Menu(
                item('显示主界面', self.restore_window),
                item('固定窗口', self._tray_toggle_fixed,
                     checked=lambda _item: self.hr_window.fixed if self.hr_window else False),
                item('退出', self._tray_quit)
            )
            self.tray_icon = pystray.Icon(
                "heart_monitor",
                self.tray_image,
                "心率监测",
                menu
            )
            threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def _tray_toggle_fixed(self):
        """托盘线程回调：通过信号派发到主线程。"""
        self._restore_helper.toggle_fixed_requested.emit()

    def _tray_quit(self):
        """托盘线程回调：通过信号派发到主线程。"""
        self._restore_helper.quit_requested.emit()

    def _do_toggle_fixed(self):
        """主线程：切换固定状态。"""
        self.toggle_fixed()

    def restore_window(self):
        logger.debug("托盘菜单: 显示主界面")
        # pyqtSignal.emit() 跨线程安全，自动将槽派发到接收者所在线程（Qt 主线程）
        self._restore_helper.restore.emit()

    def _do_restore(self):
        """在主线程中执行窗口恢复操作（不停止托盘图标，避免线程死锁）。"""
        self.main_window.setWindowOpacity(1.0)
        self.main_window.show()
        self.main_window.activateWindow()
        self.main_window.setWindowState(self.main_window.windowState() & ~Qt.WindowMinimized)
        logger.info("主窗口已恢复显示")

    def _check_activate_request(self):
        """单实例：若后开实例请求激活，把主窗口提到前台。"""
        from .singleton import activate_requested
        if activate_requested():
            self._do_restore()

    def chart_window_closed(self):
        self.chart_window = None

    def show_chart(self):
        if self.chart_window is not None:
            try:
                if self.chart_window.isVisible():
                    self.chart_window.activateWindow()
                    return
            except RuntimeError:
                self.chart_window = None
        self.chart_window = ChartWindow(self)
        self.chart_window.show_with_animation()

    # ===================== 退出 =====================
    def quit_app(self):
        # 若有待应用更新（用户选"退出时更新"），退出时替换 exe（下次启动即新版，不重启）
        pending = self._pending_update_exe
        if pending:
            self._pending_update_exe = None
            try:
                _apply_update(pending, restart=False)
            except Exception as e:
                logger.error(f"退出时应用更新失败: {e}")
        if self.hr_window:
            self.hr_window.save_position()
        self.reconnecting = False
        # 先真正断开 BLE 再退出：run_coroutine_threadsafe 是异步调度，
        # 若不等待，紧接着的 loop.stop() 会让断开协程来不及执行，设备保持被占用。
        if self.client and self.client.is_connected:
            try:
                fut = asyncio.run_coroutine_threadsafe(self.client.disconnect(), self.loop)
                fut.result(timeout=3.0)  # 等待断开完成（最多 3 秒）
                play_system_sound("Windows Hardware Remove")  # 与"断开连接"按钮一致
            except TimeoutError:
                logger.warning("BLE 断开超时，进程仍将退出")
            except Exception:
                pass
        if self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)
        # 清理主题过渡动画，避免析构期崩溃
        self.main_window.shutdown()
        # 托盘图标是 daemon 线程，进程退出后自动销毁，不显式 stop（避免死锁）
        if self.chart_window:
            self.chart_window.close()
        # 退出时清理临时文件（heart_*.log 与 _MEI* 解压残留）
        try:
            from .cleanup import cleanup_temp_files
            cleanup_temp_files()
        except Exception:
            pass
        QApplication.quit()

    def _load_tray_image(self):
        try:
            return Image.open(resource_path("tray_icon.ico"))
        except Exception:
            image = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
            draw = ImageDraw.Draw(image)
            font = ImageFont.load_default()
            draw.text((12, 12), "♥", fill="red", font=font)
            return image

    def run(self):
        try:
            sys.exit(self.app.exec_())
        except Exception as e:
            logger.critical(f"运行时异常: {e}")
            traceback.print_exc()
