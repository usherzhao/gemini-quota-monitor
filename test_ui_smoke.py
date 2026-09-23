"""
Smoke test for Gemini Quota Monitor UI components.
Instantiates all UI widgets in offscreen mode and verifies snapshot rendering.
"""

import sys
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from config import ConfigManager
from core.models import QuotaItem, QuotaSnapshot
from ui.taskbar_dock import TaskbarDockWidget
from ui.flyout_window import FlyoutWindow
from ui.settings_window import SettingsWindow
from ui.tray_manager import TrayIconManager


def test_ui():
    import tempfile
    from pathlib import Path
    app = QApplication.instance() or QApplication(sys.argv)
    temp_dir = tempfile.TemporaryDirectory()
    temp_path = Path(temp_dir.name) / "test_config.json"
    cfg_mgr = ConfigManager(temp_path)

    dock = TaskbarDockWidget(cfg_mgr)
    flyout = FlyoutWindow(cfg_mgr)
    settings = SettingsWindow(cfg_mgr)

    tray = TrayIconManager(
        config_manager=cfg_mgr,
        on_toggle_flyout=lambda: None,
        on_refresh=lambda: None,
        on_open_settings=lambda: None,
        on_toggle_dock=lambda: None,
        on_reset_dock=lambda: None,
        on_toggle_mode=lambda: None,
        on_exit=lambda: None,
    )

    # Test snapshot
    item_5h = QuotaItem(id="gemini_5h", name="5小时额度", remaining=82.0, total=100.0)
    item_weekly = QuotaItem(id="gemini_weekly", name="周总额度", remaining=65.0, total=100.0)
    snapshot = QuotaSnapshot(
        source_name="Google Gemini",
        account_label="test_user@gmail.com",
        project_id="sample-project-id",
        items=[item_5h, item_weekly],
        status="ok",
    )

    # Feed snapshot to all components
    dock.update_snapshot(snapshot)
    flyout.update_snapshot(snapshot)
    tray.update_snapshot(snapshot)

    # Verify dock text
    dock_text = dock._build_display_text()
    print(f"[SmokeTest] Dock text (remaining): {dock_text}")
    assert "82%" in dock_text
    assert "65%" in dock_text

    # Switch to used mode
    cfg_mgr.config.display.usage_display_mode = "used"
    dock.update_snapshot(snapshot)
    dock_text_used = dock._build_display_text()
    print(f"[SmokeTest] Dock text (used): {dock_text_used}")
    assert "18%" in dock_text_used
    assert "35%" in dock_text_used

    print("[SmokeTest] All UI components rendered successfully!")

    # Test edge collapse and expand
    dock._edge_docked = "right"
    dock._collapse_to_edge()
    assert dock._is_collapsed is True
    assert dock.width() == 20
    assert dock.height() == 52
    print(f"[SmokeTest] Collapsed dimensions verified: {dock.width()}x{dock.height()}")

    # Offscreen paint collapsed tab
    from PyQt6.QtGui import QImage, QPainter
    img_collapsed = QImage(dock.width(), dock.height(), QImage.Format.Format_ARGB32_Premultiplied)
    p = QPainter(img_collapsed)
    dock._paint_collapsed(p)
    p.end()
    assert not img_collapsed.isNull()
    print("[SmokeTest] Collapsed tab painted successfully!")

    # Expand back
    dock._expand_from_edge()
    assert dock._is_collapsed is False
    assert dock.width() >= 86
    print(f"[SmokeTest] Expanded dimensions verified: {dock.width()}x{dock.height()}")

    # Test settings load & save
    settings._load_values()
    assert settings.chk_auto_collapse.isChecked() == cfg_mgr.config.display.dock_auto_collapse_edge
    settings.chk_auto_collapse.setChecked(False)
    settings._save_and_close()
    assert cfg_mgr.config.display.dock_auto_collapse_edge is False
    print("[SmokeTest] Settings load and save verified!")

    # Test Multi-Account Dashboard in Flyout & Dock
    from core.account_manager import get_account_manager
    from datetime import datetime, timedelta
    acc_mgr = get_account_manager()
    acc_mgr.update_or_add_account(
        email="active_user@example.com",
        name="Active Tester",
        plan_name="Pro",
        gemini_5h_remaining=45.0,
        gemini_5h_reset_time=datetime.now() + timedelta(hours=2),
        is_active=True,
    )
    acc_mgr.update_or_add_account(
        email="recovered_user@example.com",
        name="Offline Ready",
        plan_name="Standard",
        gemini_5h_remaining=10.0,
        gemini_5h_reset_time=datetime.now() - timedelta(minutes=10),  # expired -> recovered
        is_active=False,
    )

    # Switch to accounts tab
    flyout.show_tab("accounts")
    assert flyout.current_tab == "accounts"
    assert "满血" in flyout.btn_tab_accounts.text()
    print(f"[SmokeTest] Accounts tab rendered with button: {flyout.btn_tab_accounts.text()}")

    # Verify dock tooltip includes multi-account summary
    dock.update_snapshot(snapshot)
    assert "满血恢复" in dock.toolTip()
    print(f"[SmokeTest] Dock tooltip verified: {dock.toolTip()}")

    # Switch back to current tab
    flyout.show_tab("current")
    assert flyout.current_tab == "current"

    # Clean up test accounts
    acc_mgr.remove_account("active_user@example.com")
    acc_mgr.remove_account("recovered_user@example.com")

    dock.close()
    flyout.close()
    settings.close()
    tray.hide()


if __name__ == "__main__":
    test_ui()
