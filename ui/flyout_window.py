"""
Gemini Quota Monitor - Modern Flyout Window
Windows 11 Fluent-styled card popup displaying Gemini 5-hour rolling quota, weekly quota, reset timers, and Remaining/Used switcher.
"""

from datetime import datetime
from typing import Callable, Optional
from PyQt6.QtCore import QEvent, QPoint, QRect, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QGuiApplication, QIcon, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from config import ConfigManager
from core.models import QuotaItem, QuotaSnapshot
from utils.win_api import apply_dwm_window_attributes, get_taskbar_info


class QuotaCardWidget(QFrame):
    """Card widget rendering a single quota metric (e.g., 5-Hour or Weekly)."""

    def __init__(self, item: QuotaItem, display_mode: str = "remaining", parent=None):
        super().__init__(parent)
        self.item = item
        self.display_mode = display_mode
        self._init_ui()

    def _init_ui(self):
        self.setStyleSheet("""
            QFrame {
                background-color: #1E293B;
                border: 1px solid #334155;
                border-radius: 12px;
                padding: 12px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        stats = self.item.get_display_stats(self.display_mode)

        # 1. Header Row: Title & Percentage Value
        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(8)

        prefix = "⚡"
        if "5h" in self.item.id.lower() or "5小时" in self.item.name:
            prefix = "⚡"
        elif "week" in self.item.id.lower() or "周" in self.item.name:
            prefix = "📊"
        else:
            prefix = "🔹"

        title_lbl = QLabel(f"{prefix} {self.item.name}")
        title_lbl.setStyleSheet("color: #F8FAFC; font-weight: 700; font-size: 14px;")
        header_row.addWidget(title_lbl)

        header_row.addStretch()

        val_lbl = QLabel(stats["text"])
        val_lbl.setStyleSheet(f"color: {stats['color']}; font-weight: 800; font-size: 15px;")
        header_row.addWidget(val_lbl)

        layout.addLayout(header_row)

        # 2. Progress Bar
        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setValue(int(stats['percentage']))
        bar.setTextVisible(False)
        bar.setFixedHeight(8)

        color = stats['color']
        bar.setStyleSheet(f"""
            QProgressBar {{
                background-color: #334155;
                border-radius: 4px;
                border: none;
            }}
            QProgressBar::chunk {{
                background-color: {color};
                border-radius: 4px;
            }}
        """)
        layout.addWidget(bar)

        # 3. Footer Row: Reset Countdown & Description
        footer_layout = QVBoxLayout()
        footer_layout.setContentsMargins(0, 0, 0, 0)
        footer_layout.setSpacing(4)

        if self.item.reset_time or self.item.reset_text:
            timer_lbl = QLabel(f"⏱ 重置倒计时: {self.item.format_countdown()}")
            timer_lbl.setStyleSheet("color: #94A3B8; font-size: 12px;")
            footer_layout.addWidget(timer_lbl)

        if self.item.description:
            desc_lbl = QLabel(self.item.description)
            desc_lbl.setStyleSheet("color: #64748B; font-size: 11px;")
            footer_layout.addWidget(desc_lbl)

        layout.addLayout(footer_layout)


class FlyoutWindow(QWidget):
    """Modern popup panel positioned next to the Windows system tray with Mode switcher."""

    refresh_requested = pyqtSignal()
    settings_requested = pyqtSignal()
    mode_changed = pyqtSignal(str)

    def __init__(self, config_manager: ConfigManager):
        super().__init__()
        self.config_manager = config_manager
        self.current_snapshot: Optional[QuotaSnapshot] = None

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self._init_ui()
        apply_dwm_window_attributes(int(self.winId()), dark_mode=True, round_corners=True)

    def _init_ui(self):
        self.setFixedWidth(460)

        self.main_frame = QFrame(self)
        self.main_frame.setObjectName("MainFrame")
        self.main_frame.setStyleSheet("""
            QFrame#MainFrame {
                background-color: #0F172A;
                border: 1px solid #334155;
                border-radius: 16px;
            }
        """)

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.addWidget(self.main_frame)

        content_layout = QVBoxLayout(self.main_frame)
        content_layout.setContentsMargins(18, 18, 18, 16)
        content_layout.setSpacing(12)

        # 1. Header Row
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)

        app_title = QLabel("⚡ Gemini 用量监控")
        app_title.setStyleSheet("color: #F8FAFC; font-size: 16px; font-weight: 700;")
        header.addWidget(app_title)

        self.source_badge = QLabel("载入中...")
        self.source_badge.setStyleSheet("""
            background-color: #1E3A8A;
            color: #93C5FD;
            font-size: 11px;
            font-weight: 600;
            padding: 3px 8px;
            border-radius: 6px;
        """)
        header.addWidget(self.source_badge)

        header.addStretch()

        self.time_lbl = QLabel("")
        self.time_lbl.setStyleSheet("color: #64748B; font-size: 11px;")
        header.addWidget(self.time_lbl)

        content_layout.addLayout(header)

        # 2. Account info & Segmented Switcher Row
        mid_row = QHBoxLayout()
        mid_row.setContentsMargins(0, 0, 0, 0)
        mid_row.setSpacing(8)

        self.account_lbl = QLabel("正在连接账户...")
        self.account_lbl.setStyleSheet("color: #94A3B8; font-size: 12px;")
        mid_row.addWidget(self.account_lbl)

        mid_row.addStretch()

        # Mode Switcher Toggle: [ 剩余量 | 已用量 ]
        mode_pill = QFrame()
        mode_pill.setStyleSheet("""
            QFrame {
                background-color: #1E293B;
                border: 1px solid #334155;
                border-radius: 12px;
                padding: 2px;
            }
        """)
        pill_layout = QHBoxLayout(mode_pill)
        pill_layout.setContentsMargins(2, 2, 2, 2)
        pill_layout.setSpacing(2)

        self.btn_mode_rem = QPushButton("剩余量 %")
        self.btn_mode_rem.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_mode_rem.clicked.connect(lambda: self._set_display_mode("remaining"))

        self.btn_mode_used = QPushButton("已用量 %")
        self.btn_mode_used.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_mode_used.clicked.connect(lambda: self._set_display_mode("used"))

        pill_layout.addWidget(self.btn_mode_rem)
        pill_layout.addWidget(self.btn_mode_used)
        mid_row.addWidget(mode_pill)

        content_layout.addLayout(mid_row)
        self._update_mode_buttons_ui()

        # 3. Cards Area (Scrollable without horizontal scrollbar)
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setStyleSheet("""
            QScrollArea { background: transparent; border: none; }
            QScrollBar:vertical {
                background: #0F172A;
                width: 6px;
                margin: 0;
                border-radius: 3px;
            }
            QScrollBar::handle:vertical {
                background: #334155;
                min-height: 20px;
                border-radius: 3px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0;
            }
        """)

        self.cards_container = QWidget()
        self.cards_container.setStyleSheet("background: transparent;")
        self.cards_layout = QVBoxLayout(self.cards_container)
        self.cards_layout.setContentsMargins(0, 4, 4, 4)
        self.cards_layout.setSpacing(10)

        self.scroll_area.setWidget(self.cards_container)
        content_layout.addWidget(self.scroll_area)

        # 4. Action Buttons Footer Row
        footer = QHBoxLayout()
        footer.setContentsMargins(0, 4, 0, 0)
        footer.setSpacing(8)

        btn_settings = QPushButton("⚙️ 设置与登录")
        btn_settings.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_settings.setStyleSheet("""
            QPushButton {
                background-color: #1E293B;
                color: #CBD5E1;
                border: 1px solid #334155;
                border-radius: 8px;
                padding: 7px 12px;
                font-size: 12px;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: #334155;
                color: #F8FAFC;
            }
        """)
        btn_settings.clicked.connect(self._on_settings_clicked)
        footer.addWidget(btn_settings)

        footer.addStretch()

        self.btn_refresh = QPushButton("🔄 刷新")
        self.btn_refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_refresh.setStyleSheet("""
            QPushButton {
                background-color: #2563EB;
                color: #FFFFFF;
                border: none;
                border-radius: 8px;
                padding: 7px 16px;
                font-size: 12px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #1D4ED8;
            }
            QPushButton:pressed {
                background-color: #1E40AF;
            }
        """)
        self.btn_refresh.clicked.connect(self._on_refresh_clicked)
        footer.addWidget(self.btn_refresh)

        btn_close = QPushButton("关闭")
        btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_close.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #94A3B8;
                border: 1px solid #334155;
                border-radius: 8px;
                padding: 7px 12px;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #1E293B;
                color: #F8FAFC;
            }
        """)
        btn_close.clicked.connect(self.hide)
        footer.addWidget(btn_close)

        content_layout.addLayout(footer)

    def _set_display_mode(self, mode: str):
        if self.config_manager.config.display.usage_display_mode != mode:
            self.config_manager.config.display.usage_display_mode = mode
            self.config_manager.save()
            self._update_mode_buttons_ui()
            if self.current_snapshot:
                self.update_snapshot(self.current_snapshot)
            self.mode_changed.emit(mode)

    def _update_mode_buttons_ui(self):
        mode = self.config_manager.config.display.usage_display_mode
        active_style = """
            QPushButton {
                background-color: #2563EB;
                color: #FFFFFF;
                border: none;
                border-radius: 10px;
                padding: 4px 10px;
                font-size: 11px;
                font-weight: 700;
            }
        """
        inactive_style = """
            QPushButton {
                background-color: transparent;
                color: #94A3B8;
                border: none;
                border-radius: 10px;
                padding: 4px 10px;
                font-size: 11px;
                font-weight: 500;
            }
            QPushButton:hover {
                color: #F8FAFC;
            }
        """
        if mode == "used":
            self.btn_mode_rem.setStyleSheet(inactive_style)
            self.btn_mode_used.setStyleSheet(active_style)
        else:
            self.btn_mode_rem.setStyleSheet(active_style)
            self.btn_mode_used.setStyleSheet(inactive_style)

    def update_snapshot(self, snapshot: QuotaSnapshot):
        self.current_snapshot = snapshot
        self._update_mode_buttons_ui()

        now_str = datetime.now().strftime("%H:%M:%S")
        self.time_lbl.setText(f"{now_str} 同步")

        email = snapshot.account_label or self.config_manager.config.gemini.account_email or "未关联账号"
        proj = f" (项目: {snapshot.project_id})" if snapshot.project_id else ""
        self.account_lbl.setText(f"账号: {email}{proj}")

        if snapshot.is_healthy:
            self.source_badge.setText(snapshot.source_name)
            self.source_badge.setStyleSheet("""
                background-color: #064E3B;
                color: #6EE7B7;
                font-size: 11px;
                font-weight: 600;
                padding: 3px 8px;
                border-radius: 6px;
            """)
        else:
            self.source_badge.setText("连接异常")
            self.source_badge.setStyleSheet("""
                background-color: #7F1D1D;
                color: #FCA5A5;
                font-size: 11px;
                font-weight: 600;
                padding: 3px 8px;
                border-radius: 6px;
            """)

        # Clear existing cards
        while self.cards_layout.count():
            child = self.cards_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        if not snapshot.is_healthy:
            err_box = QFrame()
            err_box.setStyleSheet("background-color: #450A0A; border: 1px solid #7F1D1D; border-radius: 10px; padding: 14px;")
            err_lay = QVBoxLayout(err_box)
            err_title = QLabel("⚠️ 额度同步失败")
            err_title.setStyleSheet("color: #F87171; font-weight: 700; font-size: 14px;")
            err_lay.addWidget(err_title)

            err_msg = QLabel(snapshot.error_message or "无法获取 Gemini 配额数据，请检查网络或重新登录。")
            err_msg.setWordWrap(True)
            err_msg.setStyleSheet("color: #FECACA; font-size: 12px; margin-top: 4px;")
            err_lay.addWidget(err_msg)

            self.cards_layout.addWidget(err_box)
        elif not snapshot.items:
            empty_lbl = QLabel("暂未检测到配额信息，请确保已登录并具有 Code Assist 访问权限。")
            empty_lbl.setStyleSheet("color: #94A3B8; padding: 20px; font-size: 13px;")
            empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.cards_layout.addWidget(empty_lbl)
        else:
            mode = self.config_manager.config.display.usage_display_mode
            for item in snapshot.items:
                card = QuotaCardWidget(item, display_mode=mode)
                self.cards_layout.addWidget(card)

        self.cards_layout.addStretch()

        # Dynamic height calculation
        num_cards = len(snapshot.items) if snapshot.is_healthy and snapshot.items else 1
        calculated_h = min(600, max(260, 140 + num_cards * 110))
        self.resize(self.width(), calculated_h)

    def show_near_cursor_or_tray(self, reference_rect: Optional[QRect] = None):
        screen = QGuiApplication.primaryScreen()
        if not screen:
            self.show()
            return

        avail = screen.availableGeometry()
        w = self.width()
        h = self.height()

        x = avail.right() - w - 16
        y = avail.bottom() - h - 16

        tb_info = get_taskbar_info()
        if tb_info:
            if tb_info.edge == "bottom":
                y = tb_info.rect.top - h - 10
            elif tb_info.edge == "top":
                y = tb_info.rect.bottom + 10
            elif tb_info.edge == "right":
                x = tb_info.rect.left - w - 10
            elif tb_info.edge == "left":
                x = tb_info.rect.right + 10

        self.move(x, y)
        self.show()
        self.raise_()
        self.activateWindow()

    def changeEvent(self, event):
        if event.type() == QEvent.Type.ActivationChange and not self.isActiveWindow():
            self.hide()
        super().changeEvent(event)

    def _on_refresh_clicked(self):
        self.btn_refresh.setEnabled(False)
        self.btn_refresh.setText("刷新中...")
        self.refresh_requested.emit()
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(2000, lambda: (self.btn_refresh.setEnabled(True), self.btn_refresh.setText("🔄 刷新")))

    def _on_settings_clicked(self):
        self.hide()
        self.settings_requested.emit()
