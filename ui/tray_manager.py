"""
Gemini Quota Monitor - System Tray Manager
Renders dynamic tray icon (percentage circle / badges), tooltip, and context menu for Gemini.
"""

from typing import Callable, Optional
from PyQt6.QtCore import QPoint, QRectF, Qt
from PyQt6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QFont,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PyQt6.QtWidgets import QMenu, QSystemTrayIcon

from config import ConfigManager
from core.models import QuotaItem, QuotaSnapshot
from utils.logger import open_log_file


class TrayIconManager:
    """Manages the Windows system tray icon, live painting, and context menu."""

    def __init__(
        self,
        config_manager: ConfigManager,
        on_toggle_flyout: Callable[[], None],
        on_refresh: Callable[[], None],
        on_open_settings: Callable[[], None],
        on_toggle_dock: Callable[[], None],
        on_reset_dock: Callable[[], None],
        on_toggle_mode: Callable[[], None],
        on_exit: Callable[[], None],
    ):
        self.config_manager = config_manager
        self.on_toggle_flyout = on_toggle_flyout
        self.on_refresh = on_refresh
        self.on_open_settings = on_open_settings
        self.on_toggle_dock = on_toggle_dock
        self.on_reset_dock = on_reset_dock
        self.on_toggle_mode = on_toggle_mode
        self.on_exit = on_exit

        self.tray_icon = QSystemTrayIcon()
        self._init_menu()
        self._init_events()

    def _init_menu(self):
        self.menu = QMenu()
        self.menu.setStyleSheet("""
            QMenu {
                background-color: #0F172A;
                color: #F8FAFC;
                border: 1px solid #334155;
                border-radius: 8px;
                padding: 6px;
                font-family: 'Segoe UI', 'Microsoft YaHei UI', sans-serif;
                font-size: 13px;
            }
            QMenu::item {
                padding: 6px 20px 6px 12px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background-color: #2563EB;
                color: #FFFFFF;
            }
            QMenu::separator {
                height: 1px;
                background: #1E293B;
                margin: 4px 6px;
            }
        """)

        self.act_status = QAction("Gemini Quota: 正在连接...", self.menu)
        self.act_status.setEnabled(False)
        self.menu.addAction(self.act_status)

        self.menu.addSeparator()

        self.act_panel = QAction("📊 打开额度详情面板", self.menu)
        self.act_panel.triggered.connect(self.on_toggle_flyout)
        self.menu.addAction(self.act_panel)

        self.act_mode_toggle = QAction("🔀 切换视角 (剩余量 % ⇄ 已用量 %)", self.menu)
        self.act_mode_toggle.triggered.connect(self.on_toggle_mode)
        self.menu.addAction(self.act_mode_toggle)

        self.act_refresh = QAction("🔄 立即刷新数据", self.menu)
        self.act_refresh.triggered.connect(self.on_refresh)
        self.menu.addAction(self.act_refresh)

        self.act_dock_toggle = QAction("📌 显示/隐藏任务栏小胶囊", self.menu)
        self.act_dock_toggle.setCheckable(True)
        self.act_dock_toggle.setChecked(self.config_manager.config.display.show_taskbar_dock)
        self.act_dock_toggle.triggered.connect(self.on_toggle_dock)
        self.menu.addAction(self.act_dock_toggle)

        act_reset_dock = QAction("🎯 重新吸附胶囊到任务栏", self.menu)
        act_reset_dock.triggered.connect(self.on_reset_dock)
        self.menu.addAction(act_reset_dock)

        self.menu.addSeparator()

        self.act_logs = QAction("📑 查看运行日志 (Log)...", self.menu)
        self.act_logs.triggered.connect(open_log_file)
        self.menu.addAction(self.act_logs)

        self.act_settings = QAction("⚙️ 偏好设置...", self.menu)
        self.act_settings.triggered.connect(self.on_open_settings)
        self.menu.addAction(self.act_settings)

        self.act_exit = QAction("❌ 退出程序", self.menu)
        self.act_exit.triggered.connect(self.on_exit)
        self.menu.addAction(self.act_exit)

        self.tray_icon.setContextMenu(self.menu)

    def _init_events(self):
        self.tray_icon.activated.connect(self._on_tray_activated)

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.on_toggle_flyout()

    def show(self):
        self.tray_icon.show()

    def hide(self):
        self.tray_icon.hide()

    def update_snapshot(self, snapshot: QuotaSnapshot):
        """Updates tray icon and tooltip based on latest QuotaSnapshot."""
        if not snapshot.is_healthy:
            self.update_error_state(snapshot.error_message or "获取失败")
            return

        primary = snapshot.primary_item
        if not primary:
            self.update_icon_with_value(100.0, "Gemini", "good")
            return

        mode = self.config_manager.config.display.usage_display_mode
        stats = primary.get_display_stats(mode)

        pct = stats["percentage"]
        level = primary.status_level
        display_text = f"{int(pct)}"
        
        self.update_icon_with_value(pct, display_text, level)

        # Update menu title
        item_5h = snapshot.item_5h
        item_weekly = snapshot.item_weekly
        parts = []
        if item_5h:
            st5 = item_5h.get_display_stats(mode)
            parts.append(f"5h: {st5['text']}")
        if item_weekly:
            st_w = item_weekly.get_display_stats(mode)
            parts.append(f"周: {st_w['text']}")
        summary_str = " | ".join(parts) if parts else f"{primary.name}: {stats['text']}"
        self.act_status.setText(f"⚡ {summary_str}")

        # Update Tooltip
        mode_str = "已用量" if mode == "used" else "剩余量"
        tooltip_lines = [
            f"⚡ Gemini Quota Monitor [{mode_str}]",
            f"👤 账号: {snapshot.account_label}",
        ]
        if snapshot.project_id:
            tooltip_lines.append(f"📁 项目: {snapshot.project_id}")
        tooltip_lines.append("───────────────────")

        for item in snapshot.items:
            it_stats = item.get_display_stats(mode)
            tooltip_lines.append(f"• {item.name}: {it_stats['text']}")
            if item.reset_time:
                tooltip_lines.append(f"  ⏱ 重置: {item.format_countdown()}")

        tooltip_lines.append("───────────────────")
        tooltip_lines.append("💡 左键点击打开面板 / 右键打开菜单")
        self.tray_icon.setToolTip("\n".join(tooltip_lines))

    def update_error_state(self, message: str):
        """Renders an error badge on the tray icon."""
        pixmap = QPixmap(64, 64)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        painter.setBrush(QBrush(QColor("#EF4444")))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(4, 4, 56, 56)

        painter.setPen(QPen(QColor("#FFFFFF"), 4))
        font = QFont("Segoe UI", 26, QFont.Weight.Bold)
        painter.setFont(font)
        painter.drawText(QRectF(0, 0, 64, 64), Qt.AlignmentFlag.AlignCenter, "!")
        painter.end()

        self.tray_icon.setIcon(QIcon(pixmap))
        self.tray_icon.setToolTip(f"Gemini 配额获取异常: {message}")
        self.act_status.setText("⚠️ 状态: 连接异常")

    def update_icon_with_value(self, percentage: float, text: str, level: str):
        """Draws clean high-DPI percentage ring and text for tray icon."""
        size = 64
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        color_map = {
            "good": "#10B981",    # Emerald
            "warning": "#F59E0B", # Amber
            "danger": "#EF4444",  # Red
        }
        main_color = QColor(color_map.get(level, "#10B981"))
        track_color = QColor("#334155")

        rect = QRectF(6, 6, size - 12, size - 12)

        # Track circle
        pen_track = QPen(track_color, 7)
        pen_track.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen_track)
        painter.drawArc(rect, 0, 360 * 16)

        # Progress Arc
        span_angle = int((percentage / 100.0) * 360 * 16)
        pen_prog = QPen(main_color, 7)
        pen_prog.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen_prog)
        painter.drawArc(rect, 90 * 16, -span_angle)

        # Text in center
        painter.setPen(QPen(QColor("#F8FAFC")))
        font = QFont("Segoe UI", 16, QFont.Weight.Bold)
        painter.setFont(font)
        painter.drawText(QRectF(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, text)

        painter.end()

        self.tray_icon.setIcon(QIcon(pixmap))
