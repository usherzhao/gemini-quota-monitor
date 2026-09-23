"""
Gemini Quota Monitor - Modern Flyout Window
Windows 11 Fluent-styled card popup displaying Gemini 5-hour rolling quota, weekly quota, reset timers,
and a Multi-Account Quota Tracking Dashboard.
Equipped with draggable window mobility and tight auto-fitting responsive layouts.
"""

from datetime import datetime
from typing import Callable, Optional
from PyQt6.QtCore import QEvent, QPoint, QRect, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QFont,
    QGuiApplication,
    QIcon,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
)
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from config import ConfigManager
from core.account_manager import AccountManager, AccountQuotaRecord, get_account_manager
from core.models import QuotaItem, QuotaSnapshot
from utils.win_api import apply_dwm_window_attributes, get_taskbar_info


class ResizableStackedWidget(QStackedWidget):
    """QStackedWidget that reports sizeHint based strictly on the current active page."""

    def sizeHint(self):
        cur = self.currentWidget()
        if cur:
            return cur.sizeHint()
        return super().sizeHint()

    def minimumSizeHint(self):
        cur = self.currentWidget()
        if cur:
            return cur.minimumSizeHint()
        return super().minimumSizeHint()

    def setCurrentIndex(self, index: int):
        prev = self.currentWidget()
        super().setCurrentIndex(index)
        now = self.currentWidget()
        if prev and prev != now:
            prev.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        if now:
            now.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.updateGeometry()


class QuotaCardWidget(QFrame):
    """Card widget rendering a single quota metric (e.g., 5-Hour or Weekly)."""

    def __init__(self, item: QuotaItem, display_mode: str = "remaining", parent=None):
        super().__init__(parent)
        self.item = item
        self.display_mode = display_mode
        self._init_ui()

    def _init_ui(self):
        self.setStyleSheet("""
            QFrame#QuotaCard {
                background-color: #1E293B;
                border: 1px solid #334155;
                border-radius: 12px;
            }
        """)
        self.setObjectName("QuotaCard")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(6)

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
        footer_layout.setSpacing(2)

        if self.item.reset_time or self.item.reset_text:
            timer_lbl = QLabel(f"⏱ 重置倒计时: {self.item.format_countdown()}")
            timer_lbl.setStyleSheet("color: #94A3B8; font-size: 12px;")
            footer_layout.addWidget(timer_lbl)

        if self.item.description:
            desc_lbl = QLabel(self.item.description)
            desc_lbl.setStyleSheet("color: #64748B; font-size: 11px;")
            footer_layout.addWidget(desc_lbl)

        layout.addLayout(footer_layout)


class AccountPoolCardWidget(QFrame):
    """Card widget rendering a saved account record with 5H & 7D quota recovery stats."""

    def __init__(
        self,
        record: AccountQuotaRecord,
        on_delete: Optional[Callable[[str], None]] = None,
        on_refresh: Optional[Callable[[str], None]] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.record = record
        self.on_delete = on_delete
        self.on_refresh = on_refresh
        self._init_ui()

    def _init_ui(self):
        level = self.record.recommendation_level
        if self.record.is_active:
            bg_color = "#172554"
            border_color = "#3B82F6"
        elif level == "ready":
            bg_color = "#064E3B"
            border_color = "#10B981"
        else:
            bg_color = "#1E293B"
            border_color = "#334155"

        self.setStyleSheet(f"""
            QFrame#AccountCard {{
                background-color: {bg_color};
                border: 1.5px solid {border_color};
                border-radius: 12px;
            }}
        """)
        self.setObjectName("AccountCard")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(6)

        # 1. Header Row: Badge, Email, Name/Plan, Copy Button, Delete Button
        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(8)

        # Badge
        badge = QLabel()
        if self.record.is_active:
            badge.setText("🟢 当前在线")
            badge.setStyleSheet("background-color: #2563EB; color: #FFFFFF; font-size: 11px; font-weight: 700; padding: 2px 7px; border-radius: 5px;")
        elif level == "ready":
            badge.setText("✨ 建议切换")
            badge.setStyleSheet("background-color: #059669; color: #FFFFFF; font-size: 11px; font-weight: 700; padding: 2px 7px; border-radius: 5px;")
        elif level == "available":
            badge.setText("⚡ 额度充裕")
            badge.setStyleSheet("background-color: #0284C7; color: #FFFFFF; font-size: 11px; font-weight: 600; padding: 2px 7px; border-radius: 5px;")
        else:
            badge.setText("⏳ 冷却中")
            badge.setStyleSheet("background-color: #334155; color: #94A3B8; font-size: 11px; font-weight: 600; padding: 2px 7px; border-radius: 5px;")
        header_row.addWidget(badge)

        # Email
        email_lbl = QLabel(self.record.email)
        email_lbl.setToolTip(self.record.email)
        email_lbl.setStyleSheet("color: #F8FAFC; font-weight: 700; font-size: 13px;")
        header_row.addWidget(email_lbl)

        # Extra info (Name or Tier)
        info_parts = []
        if self.record.name:
            info_parts.append(self.record.name)
        if self.record.plan_name:
            info_parts.append(self.record.plan_name)
        if info_parts:
            info_lbl = QLabel(f"({' · '.join(info_parts)})")
            info_lbl.setStyleSheet("color: #94A3B8; font-size: 11px;")
            header_row.addWidget(info_lbl)

        header_row.addStretch()

        # Single Account API Refresh Button (if offline and has token)
        if not self.record.is_active and self.record.has_api_token and self.on_refresh:
            self.btn_refresh = QPushButton("🔄")
            self.btn_refresh.setCursor(Qt.CursorShape.PointingHandCursor)
            self.btn_refresh.setToolTip("通过 Google 官方 API 刷新此离线账号的真实额度")
            self.btn_refresh.setStyleSheet("""
                QPushButton {
                    background-color: transparent;
                    color: #38BDF8;
                    border: 1px solid #0369A1;
                    border-radius: 6px;
                    padding: 3px 6px;
                    font-size: 11px;
                }
                QPushButton:hover {
                    background-color: #0369A1;
                    color: #FFFFFF;
                }
            """)
            self.btn_refresh.clicked.connect(self._on_single_refresh_clicked)
            header_row.addWidget(self.btn_refresh)

        # Copy Email Button
        self.btn_copy = QPushButton("📋 复制")
        self.btn_copy.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_copy.setStyleSheet("""
            QPushButton {
                background-color: #334155;
                color: #F1F5F9;
                border: 1px solid #475569;
                border-radius: 6px;
                padding: 3px 8px;
                font-size: 11px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #475569;
                color: #FFFFFF;
            }
        """)
        self.btn_copy.clicked.connect(self._copy_email)
        header_row.addWidget(self.btn_copy)

        # Delete Button (only if not active)
        if not self.record.is_active and self.on_delete:
            btn_del = QPushButton("🗑️")
            btn_del.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_del.setToolTip("删除该账号记录")
            btn_del.setStyleSheet("""
                QPushButton {
                    background-color: transparent;
                    color: #EF4444;
                    border: 1px solid #7F1D1D;
                    border-radius: 6px;
                    padding: 3px 6px;
                    font-size: 11px;
                }
                QPushButton:hover {
                    background-color: #7F1D1D;
                    color: #FFFFFF;
                }
            """)
            btn_del.clicked.connect(lambda: self.on_delete(self.record.email))
            header_row.addWidget(btn_del)

        layout.addLayout(header_row)

        # 2. 5-Hour Quota Row
        st_5h = self.record.get_5h_status()
        h5_row = QHBoxLayout()
        h5_row.setContentsMargins(0, 2, 0, 0)
        h5_row.setSpacing(8)

        lbl_5h_title = QLabel("5H 额度:")
        lbl_5h_title.setStyleSheet("color: #CBD5E1; font-size: 12px; font-weight: 600;")
        h5_row.addWidget(lbl_5h_title)

        bar_5h = QProgressBar()
        bar_5h.setRange(0, 100)
        val_5h = int(min(100.0, max(0.0, st_5h["percentage"])))
        bar_5h.setValue(val_5h)
        bar_5h.setTextVisible(False)
        bar_5h.setFixedHeight(6)

        color_5h = "#10B981" if val_5h >= 75 else ("#F59E0B" if val_5h >= 30 else "#EF4444")
        bar_5h.setStyleSheet(f"""
            QProgressBar {{
                background-color: #334155;
                border-radius: 3px;
                border: none;
            }}
            QProgressBar::chunk {{
                background-color: {color_5h};
                border-radius: 3px;
            }}
        """)
        h5_row.addWidget(bar_5h, 1)

        val_5h_lbl = QLabel(st_5h["text"])
        val_5h_lbl.setStyleSheet(f"color: {color_5h}; font-size: 12px; font-weight: 700;")
        val_5h_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        h5_row.addWidget(val_5h_lbl)

        layout.addLayout(h5_row)

        # 3. Weekly Quota Row
        st_wk = self.record.get_weekly_status()
        wk_row = QHBoxLayout()
        wk_row.setContentsMargins(0, 0, 0, 0)
        wk_row.setSpacing(8)

        lbl_wk_title = QLabel("周 额度:")
        lbl_wk_title.setStyleSheet("color: #94A3B8; font-size: 12px; font-weight: 600;")
        wk_row.addWidget(lbl_wk_title)

        bar_wk = QProgressBar()
        bar_wk.setRange(0, 100)
        val_wk = int(min(100.0, max(0.0, st_wk["percentage"])))
        bar_wk.setValue(val_wk)
        bar_wk.setTextVisible(False)
        bar_wk.setFixedHeight(6)

        color_wk = "#3B82F6" if val_wk >= 30 else "#EF4444"
        bar_wk.setStyleSheet(f"""
            QProgressBar {{
                background-color: #334155;
                border-radius: 3px;
                border: none;
            }}
            QProgressBar::chunk {{
                background-color: {color_wk};
                border-radius: 3px;
            }}
        """)
        wk_row.addWidget(bar_wk, 1)

        val_wk_lbl = QLabel(st_wk["text"])
        val_wk_lbl.setStyleSheet(f"color: {color_wk}; font-size: 12px; font-weight: 600;")
        val_wk_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        wk_row.addWidget(val_wk_lbl)

        layout.addLayout(wk_row)

        # 4. Status Footer Row
        footer_row = QHBoxLayout()
        footer_row.setContentsMargins(0, 2, 0, 0)
        footer_row.setSpacing(6)

        if self.record.is_active:
            status_text = "🟢 IDE 进程实时直连"
            status_color = "#38BDF8"
        elif self.record.has_api_token:
            sync_time = self.record.last_api_refresh[11:19] if self.record.last_api_refresh and len(self.record.last_api_refresh) >= 19 else "刚刚"
            status_text = f"🌐 官方 API 真实同步 (更新于 {sync_time})"
            status_color = "#34D399"
        else:
            status_text = "⏱️ 倒计时推算中 (切换至该账号后自动激活真实API)"
            status_color = "#94A3B8"

        lbl_src = QLabel(status_text)
        lbl_src.setStyleSheet(f"color: {status_color}; font-size: 11px;")
        footer_row.addWidget(lbl_src)
        footer_row.addStretch()
        layout.addLayout(footer_row)

    def _on_single_refresh_clicked(self):
        if hasattr(self, "btn_refresh"):
            self.btn_refresh.setText("⏳")
            self.btn_refresh.setEnabled(False)
        if self.on_refresh:
            self.on_refresh(self.record.email)

    def _copy_email(self):
        cb = QGuiApplication.clipboard()
        if cb:
            cb.setText(self.record.email)
        self.btn_copy.setText("✅ 已复制!")
        self.btn_copy.setStyleSheet("""
            QPushButton {
                background-color: #059669;
                color: #FFFFFF;
                border: 1px solid #10B981;
                border-radius: 6px;
                padding: 3px 8px;
                font-size: 11px;
                font-weight: 600;
            }
        """)
        QTimer.singleShot(1500, self._reset_copy_btn)

    def _reset_copy_btn(self):
        self.btn_copy.setText("📋 复制")
        self.btn_copy.setStyleSheet("""
            QPushButton {
                background-color: #334155;
                color: #F1F5F9;
                border: 1px solid #475569;
                border-radius: 6px;
                padding: 3px 8px;
                font-size: 11px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #475569;
                color: #FFFFFF;
            }
        """)


class FlyoutWindow(QWidget):
    """Modern popup panel positioned next to the Windows system tray with Multi-Account Dashboard and Drag support."""

    refresh_requested = pyqtSignal()
    settings_requested = pyqtSignal()
    mode_changed = pyqtSignal(str)
    account_refresh_requested = pyqtSignal(str)  # email or "" for all

    def __init__(self, config_manager: ConfigManager):
        super().__init__()
        self.config_manager = config_manager
        self.account_manager = get_account_manager()
        self.current_snapshot: Optional[QuotaSnapshot] = None
        self.current_tab = "current"  # "current" or "accounts"

        # Dragging state
        self._dragging = False
        self._drag_start_pos = QPoint()
        self._user_dragged = False
        self._custom_pos: Optional[QPoint] = None

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self._init_ui()
        self._init_countdown_timer()
        apply_dwm_window_attributes(int(self.winId()), dark_mode=True, round_corners=True)

    def _init_countdown_timer(self):
        self._timer = QTimer(self)
        self._timer.setInterval(15000)  # Refresh every 15s when visible
        self._timer.timeout.connect(self._on_timer_tick)
        self._timer.start()

    def _on_timer_tick(self):
        if not self.isVisible():
            return
        if self.current_tab == "accounts":
            self._render_accounts_tab()
        elif self.current_snapshot:
            self._render_current_tab(self.current_snapshot)

    def _init_ui(self):
        self.setFixedWidth(620)

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
        content_layout.setContentsMargins(18, 16, 18, 14)
        content_layout.setSpacing(10)

        # 1. Header Row (Draggable title bar)
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)

        app_title = QLabel("⚡ Gemini 用量监控")
        app_title.setStyleSheet("color: #F8FAFC; font-size: 16px; font-weight: 700;")
        app_title.setToolTip("按住可鼠标拖拽移动窗口位置；双击顶栏贴齐任务栏")
        header.addWidget(app_title)

        drag_hint = QLabel("⋮⋮")
        drag_hint.setToolTip("按住可鼠标拖拽移动窗口位置；双击顶栏贴齐任务栏")
        drag_hint.setStyleSheet("color: #475569; font-size: 14px; font-weight: bold; padding-left: 2px;")
        drag_hint.setCursor(Qt.CursorShape.SizeAllCursor)
        header.addWidget(drag_hint)

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

        # 2. Tab Navigation Bar: [ 📊 当前额度 ]  [ 👥 账号看板 ]
        tab_nav_bar = QHBoxLayout()
        tab_nav_bar.setContentsMargins(0, 0, 0, 0)
        tab_nav_bar.setSpacing(8)

        self.btn_tab_current = QPushButton("📊 当前额度")
        self.btn_tab_current.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_tab_current.clicked.connect(lambda: self.show_tab("current"))
        tab_nav_bar.addWidget(self.btn_tab_current)

        self.btn_tab_accounts = QPushButton("👥 账号看板")
        self.btn_tab_accounts.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_tab_accounts.clicked.connect(lambda: self.show_tab("accounts"))
        tab_nav_bar.addWidget(self.btn_tab_accounts)

        tab_nav_bar.addStretch()
        content_layout.addLayout(tab_nav_bar)

        # 3. Dynamic Stacked Pages Widget (Auto-resizes strictly to active page)
        self.stacked_widget = ResizableStackedWidget()

        # --- PAGE 0: CURRENT QUOTA PAGE ---
        page_current = QWidget()
        page_current_layout = QVBoxLayout(page_current)
        page_current_layout.setContentsMargins(0, 0, 0, 0)
        page_current_layout.setSpacing(8)

        # Account info & Segmented Switcher Row
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

        page_current_layout.addLayout(mid_row)

        # Cards Scroll Area
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
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
        self.cards_layout.setContentsMargins(0, 2, 2, 2)
        self.cards_layout.setSpacing(8)

        self.scroll_area.setWidget(self.cards_container)
        page_current_layout.addWidget(self.scroll_area)
        self.stacked_widget.addWidget(page_current)

        # --- PAGE 1: ACCOUNTS POOL DASHBOARD PAGE ---
        page_accounts = QWidget()
        page_accounts_layout = QVBoxLayout(page_accounts)
        page_accounts_layout.setContentsMargins(0, 0, 0, 0)
        page_accounts_layout.setSpacing(8)

        acc_header_row = QHBoxLayout()
        acc_header_row.setContentsMargins(0, 0, 0, 0)
        acc_header_row.setSpacing(8)

        self.acc_summary_lbl = QLabel("多账号轮转管理 (切换 IDE 账号后自动登记)")
        self.acc_summary_lbl.setStyleSheet("color: #94A3B8; font-size: 12px;")
        acc_header_row.addWidget(self.acc_summary_lbl)
        acc_header_row.addStretch()

        self.btn_acc_refresh = QPushButton("🔄 刷新真实额度")
        self.btn_acc_refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_acc_refresh.setToolTip("通过 Google 官方 API 刷新所有离线账号的最新真实配额")
        self.btn_acc_refresh.setStyleSheet("""
            QPushButton {
                background-color: #1E293B;
                color: #CBD5E1;
                border: 1px solid #334155;
                border-radius: 6px;
                padding: 4px 10px;
                font-size: 11px;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: #334155;
                color: #F8FAFC;
            }
        """)
        self.btn_acc_refresh.clicked.connect(self._on_request_refresh_all_accounts)
        acc_header_row.addWidget(self.btn_acc_refresh)

        page_accounts_layout.addLayout(acc_header_row)

        self.acc_scroll_area = QScrollArea()
        self.acc_scroll_area.setWidgetResizable(True)
        self.acc_scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.acc_scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.acc_scroll_area.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.acc_scroll_area.setStyleSheet("""
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

        self.acc_cards_container = QWidget()
        self.acc_cards_container.setStyleSheet("background: transparent;")
        self.acc_cards_layout = QVBoxLayout(self.acc_cards_container)
        self.acc_cards_layout.setContentsMargins(0, 2, 2, 2)
        self.acc_cards_layout.setSpacing(8)

        self.acc_scroll_area.setWidget(self.acc_cards_container)
        page_accounts_layout.addWidget(self.acc_scroll_area)
        self.stacked_widget.addWidget(page_accounts)

        content_layout.addWidget(self.stacked_widget)

        # 4. Action Buttons Footer Row
        footer = QHBoxLayout()
        footer.setContentsMargins(0, 2, 0, 0)
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

        self._update_tab_buttons_ui()
        self._update_mode_buttons_ui()

    # =========================================================================
    # Mouse Dragging Support
    # =========================================================================
    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._drag_start_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._dragging and (event.buttons() & Qt.MouseButton.LeftButton):
            self._user_dragged = True
            new_pos = event.globalPosition().toPoint() - self._drag_start_pos
            self._custom_pos = new_pos
            self.move(new_pos)
            event.accept()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            event.accept()
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            # Double click resets position back to taskbar tray dock
            self._user_dragged = False
            self._custom_pos = None
            self.reposition_near_tray()
            event.accept()
        super().mouseDoubleClickEvent(event)

    def _adjust_window_height(self, target_h: int):
        target_h = max(240, min(680, target_h))
        old_h = self.height()
        old_bottom = self.y() + old_h
        self.resize(self.width(), target_h)

        # If anchored near taskbar, keep bottom aligned above taskbar
        if not self._user_dragged:
            new_y = old_bottom - target_h
            self.move(self.x(), new_y)
        self.updateGeometry()

    def show_tab(self, tab_name: str):
        """Switches between 'current' and 'accounts' tabs."""
        self.current_tab = tab_name
        self._update_tab_buttons_ui()

        if tab_name == "accounts":
            self.stacked_widget.setCurrentIndex(1)
            self._render_accounts_tab()
            # If any offline accounts have an API token, trigger auto refresh in background
            if any(acc.has_api_token for acc in self.account_manager.accounts.values() if not acc.is_active):
                self.account_refresh_requested.emit("")
        else:
            self.stacked_widget.setCurrentIndex(0)
            if self.current_snapshot:
                self._render_current_tab(self.current_snapshot)

    def _update_tab_buttons_ui(self):
        ready = self.account_manager.get_ready_count()
        if ready > 0:
            self.btn_tab_accounts.setText(f"👥 账号看板 ({ready}个满血 ✨)")
        else:
            self.btn_tab_accounts.setText("👥 账号看板")

        active_style = """
            QPushButton {
                background-color: #2563EB;
                color: #FFFFFF;
                border: none;
                border-radius: 8px;
                padding: 6px 14px;
                font-size: 12px;
                font-weight: 700;
            }
        """
        inactive_style = """
            QPushButton {
                background-color: #1E293B;
                color: #94A3B8;
                border: 1px solid #334155;
                border-radius: 8px;
                padding: 6px 14px;
                font-size: 12px;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: #334155;
                color: #F8FAFC;
            }
        """
        if self.current_tab == "accounts":
            self.btn_tab_accounts.setStyleSheet(active_style)
            self.btn_tab_current.setStyleSheet(inactive_style)
        else:
            self.btn_tab_current.setStyleSheet(active_style)
            self.btn_tab_accounts.setStyleSheet(inactive_style)

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
        self._update_tab_buttons_ui()

        now_str = datetime.now().strftime("%H:%M:%S")
        self.time_lbl.setText(f"{now_str} 同步")

        email = snapshot.account_label or self.config_manager.config.gemini.account_email or "未关联账号"
        name_str = f" ({snapshot.user_name})" if snapshot.user_name else ""
        plan_str = f" [{snapshot.plan_name}]" if snapshot.plan_name else ""
        proj = f" (项目: {snapshot.project_id})" if snapshot.project_id else ""
        self.account_lbl.setText(f"账号: {email}{name_str}{plan_str}{proj}")

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

        if self.current_tab == "current":
            self._render_current_tab(snapshot)
        else:
            self._render_accounts_tab()

    def _render_current_tab(self, snapshot: QuotaSnapshot):
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
            cards_h = 110
        elif not snapshot.items:
            empty_lbl = QLabel("暂未检测到配额信息，请确保已登录并具有 Code Assist 访问权限。")
            empty_lbl.setStyleSheet("color: #94A3B8; padding: 20px; font-size: 13px;")
            empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.cards_layout.addWidget(empty_lbl)
            cards_h = 100
        else:
            mode = self.config_manager.config.display.usage_display_mode
            for item in snapshot.items:
                card = QuotaCardWidget(item, display_mode=mode)
                self.cards_layout.addWidget(card)

            num_cards = len(snapshot.items)
            cards_h = num_cards * 85 + max(0, num_cards - 1) * 8

        self.scroll_area.setFixedHeight(min(360, max(90, cards_h)))
        target_h = 205 + min(360, max(90, cards_h))
        self._adjust_window_height(target_h)

    def _render_accounts_tab(self):
        self.account_manager.load()
        accounts = self.account_manager.list_accounts()
        ready_count = self.account_manager.get_ready_count()
        self._update_tab_buttons_ui()

        if accounts:
            self.acc_summary_lbl.setText(
                f"已记录 {len(accounts)} 个账号 · {ready_count} 个满血恢复就绪 ✨" if ready_count > 0 else f"已记录 {len(accounts)} 个账号"
            )
        else:
            self.acc_summary_lbl.setText("多账号轮转管理 (切换 IDE 账号后自动登记)")

        while self.acc_cards_layout.count():
            child = self.acc_cards_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        if not accounts:
            empty_box = QFrame()
            empty_box.setStyleSheet("background-color: #1E293B; border: 1px dashed #334155; border-radius: 12px; padding: 24px;")
            empty_lay = QVBoxLayout(empty_box)
            empty_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)

            t1 = QLabel("🔍 暂未记录多账号")
            t1.setStyleSheet("color: #F8FAFC; font-weight: 700; font-size: 14px;")
            t1.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_lay.addWidget(t1)

            t2 = QLabel("在 Antigravity IDE 中切换不同 Google 账号，\n本程序会自动捕获凭据与配额，并在后台自动刷新真实额度。")
            t2.setStyleSheet("color: #94A3B8; font-size: 12px; margin-top: 6px;")
            t2.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_lay.addWidget(t2)

            self.acc_cards_layout.addWidget(empty_box)
            cards_h = 105
        else:
            for acc in accounts:
                card = AccountPoolCardWidget(
                    record=acc,
                    on_delete=self._on_delete_account,
                    on_refresh=self._on_request_refresh_single_account,
                )
                self.acc_cards_layout.addWidget(card)

            num_acc = len(accounts)
            cards_h = num_acc * 125 + max(0, num_acc - 1) * 8

        self.acc_scroll_area.setFixedHeight(min(390, max(90, cards_h)))
        target_h = 205 + min(390, max(90, cards_h))
        self._adjust_window_height(target_h)

    def _on_request_refresh_all_accounts(self):
        if hasattr(self, "btn_acc_refresh"):
            self.btn_acc_refresh.setText("⏳ 正在查询...")
            self.btn_acc_refresh.setEnabled(False)
        self.account_refresh_requested.emit("")

    def _on_request_refresh_single_account(self, email: str):
        self.account_refresh_requested.emit(email)

    def on_accounts_refreshed(self, success: bool, count: int):
        if hasattr(self, "btn_acc_refresh"):
            self.btn_acc_refresh.setText("🔄 刷新真实额度")
            self.btn_acc_refresh.setEnabled(True)
        if self.current_tab == "accounts":
            self._render_accounts_tab()

    def _on_delete_account(self, email: str):
        self.account_manager.remove_account(email)
        self._render_accounts_tab()

    def show_near_cursor_or_tray(self, reference_rect: Optional[QRect] = None):
        # If user has dragged the window to a custom location, keep it there
        if self._user_dragged and self._custom_pos:
            screen = QGuiApplication.screenAt(self._custom_pos)
            if screen:
                self.move(self._custom_pos)
                self.show()
                self.raise_()
                self.activateWindow()
                return

        self.reposition_near_tray()
        self.show()
        self.raise_()
        self.activateWindow()

    def reposition_near_tray(self):
        screen = QGuiApplication.primaryScreen()
        if not screen:
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

    def changeEvent(self, event):
        if event.type() == QEvent.Type.ActivationChange and not self.isActiveWindow():
            self.hide()
        super().changeEvent(event)

    def _on_refresh_clicked(self):
        self.btn_refresh.setEnabled(False)
        self.btn_refresh.setText("刷新中...")
        if self.current_tab == "accounts":
            self._render_accounts_tab()
        self.refresh_requested.emit()
        QTimer.singleShot(2000, lambda: (self.btn_refresh.setEnabled(True), self.btn_refresh.setText("🔄 刷新")))

    def _on_settings_clicked(self):
        self.hide()
        self.settings_requested.emit()
