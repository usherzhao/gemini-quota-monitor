"""
Gemini Quota Monitor - Main Entry Point
Desktop Taskbar & System Tray Quota Monitor for Windows 10 & 11 tracking 5-Hour and Weekly Quotas.
"""

import os
import sys
from pathlib import Path

# In Windows GUI / PyInstaller --windowed mode, sys.stdout and sys.stderr can be None.
# Redirecting to os.devnull prevents unhandled AttributeError on any print() or logging call.
if sys.stdout is None:
    try:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    except Exception:
        pass
if sys.stderr is None:
    try:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    except Exception:
        pass

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PyQt6.QtCore import QObject, Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWidgets import QApplication, QMessageBox

from config import get_config_manager
from core.engine import QuotaEngine
from core.models import QuotaSnapshot
from ui.flyout_window import FlyoutWindow
from ui.settings_window import SettingsWindow
from ui.taskbar_dock import TaskbarDockWidget
from ui.tray_manager import TrayIconManager
from utils.logger import logger

SINGLE_INSTANCE_KEY = "GeminiQuotaMonitor_SingleInstance_Mutex_Key"


class AppController(QObject):
    """Coordinates UI windows, background engine, and system tray."""

    def __init__(self, app: QApplication):
        super().__init__()
        self.app = app
        self.config_manager = get_config_manager()
        self.engine = QuotaEngine(self.config_manager)

        # UI Components
        self.flyout_window = FlyoutWindow(self.config_manager)
        self.settings_window = SettingsWindow(self.config_manager)
        self.dock_widget = TaskbarDockWidget(self.config_manager)

        self.tray_manager = TrayIconManager(
            config_manager=self.config_manager,
            on_toggle_flyout=self.toggle_flyout,
            on_refresh=self.engine.refresh_now,
            on_open_settings=self.open_settings,
            on_toggle_dock=self.toggle_dock,
            on_reset_dock=self.reset_dock,
            on_toggle_mode=self.toggle_usage_mode,
            on_exit=self.exit_app,
            on_open_accounts=self.open_accounts,
        )

        self._connect_signals()
        self._init_state()

    def _connect_signals(self):
        # Engine -> UI updates
        self.engine.quota_updated.connect(self._on_quota_updated)
        self.engine.low_quota_alert.connect(self._on_low_quota_alert)

        # Flyout actions
        self.flyout_window.refresh_requested.connect(self.engine.refresh_now)
        self.flyout_window.settings_requested.connect(self.open_settings)
        self.flyout_window.mode_changed.connect(self._on_mode_changed_by_ui)

        # Dock actions
        self.dock_widget.clicked.connect(self.toggle_flyout)
        self.dock_widget.open_accounts_requested.connect(self.open_accounts)
        self.dock_widget.refresh_requested.connect(self.engine.refresh_now)
        self.dock_widget.settings_requested.connect(self.open_settings)
        self.dock_widget.toggle_mode_requested.connect(self.toggle_usage_mode)

        # Settings saved -> Apply
        self.settings_window.settings_saved.connect(self._on_settings_saved)
        self.settings_window.reset_dock_requested.connect(self.reset_dock)

    def _init_state(self):
        self.tray_manager.show()

        if self.config_manager.config.display.show_taskbar_dock:
            self.dock_widget.auto_dock_to_taskbar()
            self.dock_widget.show()
            self.dock_widget.raise_()
            self.dock_widget.ensure_topmost()
        else:
            self.dock_widget.hide()

        self.engine.start()

        # Check if first time or not logged in
        cfg = self.config_manager.config
        if not cfg.gemini.refresh_token and not cfg.gemini.access_token and cfg.general.active_source != "simulator":
            logger.info("No credentials found on startup, opening settings window...")
            self.open_settings()

    def _on_quota_updated(self, snapshot: QuotaSnapshot):
        self.tray_manager.update_snapshot(snapshot)
        self.flyout_window.update_snapshot(snapshot)
        self.dock_widget.update_snapshot(snapshot)

    def _on_low_quota_alert(self, item_name: str, percentage: float):
        self.tray_manager.tray_icon.showMessage(
            "⚡ Gemini 配额不足预警",
            f"您的 {item_name} 当前剩余额度仅剩 {percentage:.0f}%，请合理安排使用！",
            QIcon(),
            5000,
        )

    def _on_mode_changed_by_ui(self, mode: str):
        snap = self.engine.current_snapshot or self.engine.fetcher.fetch()
        self.tray_manager.update_snapshot(snap)
        self.dock_widget.update_snapshot(snap)

    def toggle_usage_mode(self):
        """Toggles between 'remaining' and 'used'."""
        cfg = self.config_manager.config.display
        cfg.usage_display_mode = "used" if cfg.usage_display_mode == "remaining" else "remaining"
        self.config_manager.save()
        
        snap = self.engine.current_snapshot
        if not snap and hasattr(self.engine, "fetcher") and self.engine.fetcher:
            try:
                snap = self.engine.fetcher.fetch()
            except Exception:
                pass
        if snap:
            self.tray_manager.update_snapshot(snap)
            self.flyout_window.update_snapshot(snap)
            self.dock_widget.update_snapshot(snap)
        else:
            self.dock_widget._update_menu_texts()
            self.dock_widget.update()

    def _on_settings_saved(self):
        self.engine.reload_config()

        if self.config_manager.config.display.show_taskbar_dock:
            self.dock_widget.auto_dock_to_taskbar()
            self.dock_widget.show()
        else:
            self.dock_widget.hide()

        self.tray_manager.act_dock_toggle.setChecked(self.config_manager.config.display.show_taskbar_dock)
        snap = self.engine.current_snapshot or self.engine.fetcher.fetch()
        self.tray_manager.update_snapshot(snap)
        self.flyout_window.update_snapshot(snap)
        self.dock_widget.update_snapshot(snap)

    def toggle_flyout(self):
        if self.flyout_window.isVisible():
            self.flyout_window.hide()
        else:
            self.flyout_window.show_tab("current")
            self.flyout_window.show_near_cursor_or_tray()

    def open_accounts(self):
        self.flyout_window.show_tab("accounts")
        self.flyout_window.show_near_cursor_or_tray()

    def open_settings(self):
        self.settings_window.show()
        self.settings_window.raise_()
        self.settings_window.activateWindow()

    def toggle_dock(self):
        cfg = self.config_manager.config.display
        cfg.show_taskbar_dock = not cfg.show_taskbar_dock
        self.config_manager.save()
        if cfg.show_taskbar_dock:
            self.dock_widget.auto_dock_to_taskbar()
            self.dock_widget.show()
            self.dock_widget.raise_()
        else:
            self.dock_widget.hide()
        self.tray_manager.act_dock_toggle.setChecked(cfg.show_taskbar_dock)

    def reset_dock(self):
        cfg = self.config_manager.config.display
        cfg.show_taskbar_dock = True
        cfg.dock_x = -1
        cfg.dock_y = -1
        self.config_manager.save()
        self.dock_widget.auto_dock_to_taskbar()
        self.dock_widget.show()
        self.dock_widget.raise_()
        self.tray_manager.act_dock_toggle.setChecked(True)

    def show_on_wakeup(self):
        """Called when a second instance tries to launch."""
        logger.info("Wakeup signal received, bringing dock and flyout to front...")
        if self.dock_widget.isVisible():
            self.dock_widget.raise_()
            self.dock_widget.ensure_topmost()
        self.toggle_flyout()

    def exit_app(self):
        self.engine.stop()
        self.tray_manager.hide()
        self.dock_widget.close()
        self.flyout_window.close()
        self.settings_window.close()
        self.app.quit()


def check_single_instance(app: QApplication) -> tuple[bool, QLocalServer]:
    socket = QLocalSocket()
    socket.connectToServer(SINGLE_INSTANCE_KEY)
    if socket.waitForConnected(500):
        socket.write(b"WAKEUP")
        socket.waitForBytesWritten(1000)
        socket.disconnectFromServer()
        return False, None

    server = QLocalServer()
    server.removeServer(SINGLE_INSTANCE_KEY)
    server.listen(SINGLE_INSTANCE_KEY)
    return True, server


def global_excepthook(exc_type, exc_value, exc_traceback):
    logger.critical("Global unhandled exception:", exc_info=(exc_type, exc_value, exc_traceback))
    sys.__excepthook__(exc_type, exc_value, exc_traceback)

sys.excepthook = global_excepthook


def main():
    try:
        logger.info(f"[main] Gemini Quota Monitor starting... (frozen={getattr(sys, 'frozen', False)}, exe={sys.executable})")
        app = QApplication(sys.argv)
        app.setQuitOnLastWindowClosed(False)
        app.setApplicationName("Gemini Quota Monitor")
        app.setApplicationDisplayName("Gemini Quota Monitor")

        is_primary, server = check_single_instance(app)
        logger.info(f"[main] check_single_instance: is_primary={is_primary}")
        if not is_primary:
            logger.info("[main] Secondary instance detected. WAKEUP sent to primary. Exiting gracefully.")
            sys.exit(0)

        controller = AppController(app)

        try:
            import pyi_splash
            pyi_splash.close()
        except Exception:
            pass

        if server:
            def on_new_connection():
                client = server.nextPendingConnection()
                if client:
                    client.waitForReadyRead(500)
                    msg = client.readAll().data().decode("utf-8")
                    if "WAKEUP" in msg:
                        controller.show_on_wakeup()
                    client.disconnectFromServer()

            server.newConnection.connect(on_new_connection)

        logger.info("[main] Entering Qt event loop (app.exec)...")
        exit_code = app.exec()
        logger.info(f"[main] Qt event loop exited with code: {exit_code}")
        sys.exit(exit_code)
    except SystemExit as se:
        logger.info(f"[main] SystemExit caught with code: {se.code}")
        raise
    except Exception as e:
        logger.exception(f"[main] Fatal unhandled exception in main: {e}")
        raise


if __name__ == "__main__":
    main()
