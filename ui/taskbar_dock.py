"""
Gemini Quota Monitor - Taskbar Floating Dock Bar
A sleek, customizable, and translucent mini capsule docked onto the Windows taskbar focusing on Gemini 5-hour and weekly quotas.
Equipped with a 64-bit Win32 TopmostKeeper and WinEventHook to guarantee persistent topmost visibility across all applications.
"""

import ctypes
from ctypes import wintypes
from typing import Callable, Optional
from PyQt6.QtCore import QPoint, QRect, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QCursor,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
)
from PyQt6.QtWidgets import QMenu, QWidget

from config import ConfigManager
from core.models import QuotaSnapshot
from utils.win_api import calculate_taskbar_dock_position

# ==============================================================================
# Robust 64-bit Windows Win32 API Configuration
# ==============================================================================
user32 = ctypes.windll.user32

user32.SetWindowPos.argtypes = [
    wintypes.HWND,
    wintypes.HWND,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.UINT,
]
user32.SetWindowPos.restype = wintypes.BOOL

user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = wintypes.LONG

user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.LONG]
user32.SetWindowLongW.restype = wintypes.LONG

user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL

user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL

user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL

user32.SetWinEventHook.argtypes = [
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.HMODULE,
    ctypes.c_void_p,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.UINT,
]
user32.SetWinEventHook.restype = wintypes.HANDLE

user32.UnhookWinEvent.argtypes = [wintypes.HANDLE]
user32.UnhookWinEvent.restype = wintypes.BOOL

HWND_TOPMOST = ctypes.cast(-1, wintypes.HWND)
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
SW_SHOWNOACTIVATE = 4

GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000


class TaskbarDockWidget(QWidget):
    """Semi-transparent floating capsule widget docked onto the Windows Taskbar for Gemini."""

    clicked = pyqtSignal()
    open_accounts_requested = pyqtSignal()
    refresh_requested = pyqtSignal()
    settings_requested = pyqtSignal()
    toggle_mode_requested = pyqtSignal()

    def __init__(self, config_manager: ConfigManager):
        super().__init__()
        self.config_manager = config_manager
        self.current_snapshot: Optional[QuotaSnapshot] = None

        self._dragging = False
        self._drag_start_pos = QPoint()
        self._mouse_press_pos = QPoint()
        self._is_hovered = False

        # Edge auto-collapse state
        self._is_collapsed = False
        self._edge_docked = "none"  # "none", "left", "right", "top"
        self._expanded_width = self.config_manager.config.display.dock_width
        self._expanded_height = self.config_manager.config.display.dock_height

        self._collapse_timer = QTimer(self)
        self._collapse_timer.setSingleShot(True)
        self._collapse_timer.setInterval(350)
        self._collapse_timer.timeout.connect(self._collapse_to_edge)

        self._hook = None
        self._hook_proc = None

        self._init_window_flags()
        self._init_geometry()
        self._init_menu()
        self._init_topmost_keeper()

    def _init_window_flags(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def _apply_extended_window_styles(self):
        """Applies WS_EX_TOPMOST, WS_EX_TOOLWINDOW, and WS_EX_NOACTIVATE styles."""
        hwnd = int(self.winId())
        if not hwnd:
            return
        try:
            ex_style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            target_style = ex_style | WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
            if ex_style != target_style:
                user32.SetWindowLongW(hwnd, GWL_EXSTYLE, target_style)
        except Exception as e:
            print(f"[TaskbarDock] Error applying Win32 styles: {e}")

    def _init_topmost_keeper(self):
        """Sets up high-frequency topmost heartbeat timer."""
        self._topmost_timer = QTimer(self)
        self._topmost_timer.setInterval(200)
        self._topmost_timer.timeout.connect(self.ensure_topmost)
        self._topmost_timer.start()

    def showEvent(self, event):
        super().showEvent(event)
        self._apply_extended_window_styles()
        self.ensure_topmost()

    def closeEvent(self, event):
        if hasattr(self, "_topmost_timer") and self._topmost_timer:
            self._topmost_timer.stop()
        if hasattr(self, "_collapse_timer") and self._collapse_timer:
            self._collapse_timer.stop()
        super().closeEvent(event)

    def _init_geometry(self):
        cfg = self.config_manager.config.display
        self.resize(cfg.dock_width, cfg.dock_height)

        if cfg.dock_x >= 0 and cfg.dock_y >= 0:
            self._ensure_on_screen(cfg.dock_x, cfg.dock_y)
            self._check_edge_snap_after_drag()
        else:
            self.auto_dock_to_taskbar()

    def _ensure_on_screen(self, x: int, y: int):
        screen = QGuiApplication.primaryScreen()
        if screen:
            avail = screen.availableGeometry()
            full = screen.geometry()
            if x < full.left() - 50 or x > full.right() - 50 or y < full.top() - 50 or y > full.bottom() - 10:
                self.auto_dock_to_taskbar()
                return
        self.move(x, y)

    def auto_dock_to_taskbar(self):
        """Calculates and positions the widget onto the taskbar."""
        self._edge_docked = "none"
        if self._is_collapsed:
            self._is_collapsed = False
        cfg = self.config_manager.config.display
        x, y = calculate_taskbar_dock_position(self.width(), cfg.dock_height)
        self.resize(self.width(), cfg.dock_height)
        self.move(x, y)
        cfg.dock_x = x
        cfg.dock_y = y
        self.config_manager.save()
        self.ensure_topmost()

    def _init_menu(self):
        self.context_menu = QMenu(self)
        self.context_menu.setWindowFlags(
            Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
        )
        self.context_menu.setStyleSheet("""
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
                background-color: #1E293B;
                margin: 4px 6px;
            }
        """)

        action_details = QAction("📊 打开详情面板", self)
        action_details.triggered.connect(self.clicked.emit)
        self.context_menu.addAction(action_details)

        action_accounts = QAction("👥 账号看板 (多账号管理)...", self)
        action_accounts.triggered.connect(self.open_accounts_requested.emit)
        self.context_menu.addAction(action_accounts)

        action_refresh = QAction("🔄 立即刷新配额", self)
        action_refresh.triggered.connect(self.refresh_requested.emit)
        self.context_menu.addAction(action_refresh)

        self.context_menu.addSeparator()

        self.action_toggle_mode = QAction("🔀 切换显示模式 (当前: 剩余)", self)
        self.action_toggle_mode.triggered.connect(self._on_toggle_mode_action)
        self.context_menu.addAction(self.action_toggle_mode)

        self.action_toggle_countdown = QAction("⏱ 显示倒计时 (当前: 关闭)", self)
        self.action_toggle_countdown.triggered.connect(self._on_toggle_countdown_action)
        self.context_menu.addAction(self.action_toggle_countdown)

        self.action_toggle_auto_collapse = QAction("🧲 靠边自动收起 (当前: 开启)", self)
        self.action_toggle_auto_collapse.triggered.connect(self._on_toggle_auto_collapse_action)
        self.context_menu.addAction(self.action_toggle_auto_collapse)

        self.action_lock = QAction("🔒 锁定位置 (禁止拖动)", self)
        self.action_lock.setCheckable(True)
        self.action_lock.setChecked(self.config_manager.config.display.dock_locked)
        self.action_lock.toggled.connect(self._on_lock_toggled)
        self.context_menu.addAction(self.action_lock)

        action_dock = QAction("⚓ 贴齐任务栏", self)
        action_dock.triggered.connect(self.auto_dock_to_taskbar)
        self.context_menu.addAction(action_dock)

        self.context_menu.addSeparator()

        action_settings = QAction("⚙️ 设置与登录...", self)
        action_settings.triggered.connect(self.settings_requested.emit)
        self.context_menu.addAction(action_settings)

        action_exit = QAction("❌ 退出程序", self)
        action_exit.triggered.connect(QGuiApplication.instance().quit)
        self.context_menu.addAction(action_exit)

    def _on_toggle_mode_action(self):
        if self.receivers(self.toggle_mode_requested) > 0:
            self.toggle_mode_requested.emit()
        else:
            cfg = self.config_manager.config.display
            cfg.usage_display_mode = "used" if cfg.usage_display_mode == "remaining" else "remaining"
            self.config_manager.save()
            self._update_menu_texts()
            if self.current_snapshot:
                self.update_snapshot(self.current_snapshot)
            else:
                self.update()

    def _on_toggle_countdown_action(self):
        cfg = self.config_manager.config.display
        cfg.dock_show_countdown = not cfg.dock_show_countdown
        self.config_manager.save()
        self._update_menu_texts()
        if self.current_snapshot:
            self.update_snapshot(self.current_snapshot)
        else:
            self.update()

    def _on_toggle_auto_collapse_action(self):
        cfg = self.config_manager.config.display
        cfg.dock_auto_collapse_edge = not cfg.dock_auto_collapse_edge
        self.config_manager.save()
        self._update_menu_texts()
        if not cfg.dock_auto_collapse_edge:
            if self._is_collapsed:
                self._expand_from_edge()
        else:
            self._check_edge_snap_after_drag()

    def _on_lock_toggled(self, checked: bool):
        self.config_manager.config.display.dock_locked = checked
        self.config_manager.save()

    def _update_menu_texts(self):
        cfg = self.config_manager.config.display
        mode_text = "剩余" if cfg.usage_display_mode == "remaining" else "已用"
        self.action_toggle_mode.setText(f"🔀 切换显示模式 (当前: {mode_text})")
        cd_text = "开启" if cfg.dock_show_countdown else "关闭"
        if hasattr(self, "action_toggle_countdown"):
            self.action_toggle_countdown.setText(f"⏱ 显示倒计时 (当前: {cd_text})")
        ac_text = "开启" if cfg.dock_auto_collapse_edge else "关闭"
        if hasattr(self, "action_toggle_auto_collapse"):
            self.action_toggle_auto_collapse.setText(f"🧲 靠边自动收起 (当前: {ac_text})")
        self.action_lock.setChecked(cfg.dock_locked)

    def ensure_topmost(self):
        """Forces window to stay topmost without stealing keyboard or window focus."""
        if hasattr(self, "context_menu") and self.context_menu and self.context_menu.isVisible():
            return

        hwnd = int(self.winId())
        if not hwnd:
            return

        try:
            if not user32.IsWindowVisible(hwnd):
                user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)

            user32.SetWindowPos(
                hwnd,
                HWND_TOPMOST,
                0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW
            )
        except Exception:
            pass

    def update_snapshot(self, snapshot: QuotaSnapshot):
        self.current_snapshot = snapshot
        self._update_menu_texts()

        # Update rich tooltip on hover
        try:
            from core.account_manager import get_account_manager
            acc_mgr = get_account_manager()
            ready_cnt = acc_mgr.get_ready_count()
            total_acc = len(acc_mgr.accounts)

            if self.current_snapshot and self.current_snapshot.is_healthy:
                tip_lines = ["Gemini 配额监控 (左键打开面板 / 右键菜单):"]
                if self.current_snapshot.account_label:
                    tip_lines.append(f"当前账号: {self.current_snapshot.account_label}")
                if self.current_snapshot.item_5h:
                    it = self.current_snapshot.item_5h
                    st = it.get_display_stats(self.config_manager.config.display.usage_display_mode)
                    cd = it.format_countdown()
                    tip_lines.append(f"  • 5小时额度: {st['text']} (重置: {cd})")
                if self.current_snapshot.item_weekly:
                    it = self.current_snapshot.item_weekly
                    st = it.get_display_stats(self.config_manager.config.display.usage_display_mode)
                    cd = it.format_countdown()
                    tip_lines.append(f"  • 周总额度: {st['text']} (重置: {cd})")
                if total_acc > 1:
                    if ready_cnt > 0:
                        tip_lines.append(f"多账号看板: {ready_cnt}个历史账号已满血恢复 ✨ (右键查看)")
                    else:
                        tip_lines.append(f"多账号看板: 共记录 {total_acc} 个账号")
                tip_lines.append(f"更新时间: {self.current_snapshot.timestamp.strftime('%H:%M:%S')}")
                self.setToolTip("\n".join(tip_lines))
            elif self.current_snapshot and not self.current_snapshot.is_healthy:
                self.setToolTip(f"配额获取异常: {self.current_snapshot.error_message}")
            else:
                self.setToolTip("Gemini 配额监控: 正在连接...")
        except Exception:
            pass

        # Dynamically auto-size width so 2-line text is never clipped
        try:
            cfg = self.config_manager.config.display
            font = QFont(cfg.dock_font_family.split(",")[0].strip(), cfg.dock_font_size, QFont.Weight.Bold)
            fm = QFontMetrics(font)
            p1, val1, _, p2, val2, _ = self._build_display_lines()
            w1 = fm.horizontalAdvance(p1) + 4 + fm.horizontalAdvance(val1)
            w2 = fm.horizontalAdvance(p2) + 4 + fm.horizontalAdvance(val2)
            pad_left = 18
            needed_w = pad_left + max(w1, w2) + 14
            target_w = max(cfg.dock_width, needed_w)
            self._expanded_width = target_w
            self._expanded_height = cfg.dock_height
            if not self._is_collapsed:
                if self.width() != target_w or self.height() != cfg.dock_height:
                    self.resize(target_w, cfg.dock_height)
        except Exception:
            pass

        self.update()
        self.ensure_topmost()

    def enterEvent(self, event):
        self._is_hovered = True
        if hasattr(self, "_collapse_timer"):
            self._collapse_timer.stop()
        if self._is_collapsed:
            self._expand_from_edge()
        else:
            self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._is_hovered = False
        cfg = self.config_manager.config.display
        if cfg.dock_auto_collapse_edge and self._edge_docked in ("left", "right", "top"):
            if hasattr(self, "_collapse_timer"):
                self._collapse_timer.start(350)
        self.update()
        super().leaveEvent(event)

    def _collapse_to_edge(self):
        """Collapses window to a sleek vertical LED tab at the docked screen edge."""
        cfg = self.config_manager.config.display
        if not cfg.dock_auto_collapse_edge:
            return
        if self._edge_docked not in ("left", "right", "top"):
            return
        if self._dragging:
            return
        if hasattr(self, "context_menu") and self.context_menu and self.context_menu.isVisible():
            return
        if self.underMouse():
            return
        if self._is_collapsed:
            return

        self._expanded_width = self.width()
        self._expanded_height = self.height()
        self._is_collapsed = True

        screen = QGuiApplication.screenAt(self.geometry().center()) or QGuiApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)

        collapsed_w = 20
        collapsed_h = 52

        curr_x = self.x()
        curr_y = self.y()

        if self._edge_docked == "right":
            target_x = avail.right() - collapsed_w + 1
            target_y = min(max(curr_y, avail.top()), avail.bottom() - collapsed_h + 1)
        elif self._edge_docked == "left":
            target_x = avail.left()
            target_y = min(max(curr_y, avail.top()), avail.bottom() - collapsed_h + 1)
        elif self._edge_docked == "top":
            target_x = min(max(curr_x, avail.left()), avail.right() - collapsed_w + 1)
            target_y = avail.top()
        else:
            target_x, target_y = curr_x, curr_y

        self.setGeometry(target_x, target_y, collapsed_w, collapsed_h)
        self.update()
        self.ensure_topmost()

    def _expand_from_edge(self):
        """Expands collapsed vertical tab back into full TrafficMonitor card."""
        if not self._is_collapsed:
            return
        self._is_collapsed = False

        target_w = max(self._expanded_width, self.config_manager.config.display.dock_width)
        target_h = max(self._expanded_height, self.config_manager.config.display.dock_height)

        screen = QGuiApplication.screenAt(self.geometry().center()) or QGuiApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)

        curr_x = self.x()
        curr_y = self.y()

        if self._edge_docked == "right":
            target_x = avail.right() - target_w + 1
            target_y = min(max(curr_y, avail.top()), avail.bottom() - target_h + 1)
        elif self._edge_docked == "left":
            target_x = avail.left()
            target_y = min(max(curr_y, avail.top()), avail.bottom() - target_h + 1)
        elif self._edge_docked == "top":
            target_x = min(max(curr_x, avail.left()), avail.right() - target_w + 1)
            target_y = avail.top()
        else:
            target_x, target_y = curr_x, curr_y

        self.setGeometry(target_x, target_y, target_w, target_h)
        self.update()
        self.ensure_topmost()

    def _check_edge_snap_after_drag(self):
        """Detects if released near a screen edge and snaps/docks accordingly."""
        cfg = self.config_manager.config.display
        if not cfg.dock_auto_collapse_edge:
            self._edge_docked = "none"
            return

        screen = QGuiApplication.screenAt(self.geometry().center()) or QGuiApplication.primaryScreen()
        if not screen:
            return

        avail = screen.availableGeometry()
        curr_rect = self.geometry()

        threshold = 30
        dist_left = abs(curr_rect.left() - avail.left())
        dist_right = abs(curr_rect.right() - avail.right())
        dist_top = abs(curr_rect.top() - avail.top())

        min_dist = min(dist_left, dist_right, dist_top)
        if min_dist <= threshold:
            if min_dist == dist_right:
                self._edge_docked = "right"
                new_x = avail.right() - self.width() + 1
                self.move(new_x, self.y())
            elif min_dist == dist_left:
                self._edge_docked = "left"
                new_x = avail.left()
                self.move(new_x, self.y())
            elif min_dist == dist_top:
                self._edge_docked = "top"
                new_y = avail.top()
                self.move(self.x(), new_y)
        else:
            self._edge_docked = "none"

        cfg.dock_x = self.x()
        cfg.dock_y = self.y()
        self.config_manager.save()

        if self._edge_docked != "none":
            if not self.underMouse():
                self._collapse_to_edge()
            else:
                self._collapse_timer.start(500)
        else:
            if self._is_collapsed:
                self._expand_from_edge()

    def _show_context_menu(self, click_pos: QPoint):
        """Calculates popup position above dock capsule to prevent overlap."""
        self._update_menu_texts()
        self.context_menu.adjustSize()
        menu_size = self.context_menu.sizeHint()
        menu_w = max(menu_size.width(), 230)
        menu_h = menu_size.height()

        screen = QGuiApplication.screenAt(click_pos) or QGuiApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)

        widget_global_top_left = self.mapToGlobal(QPoint(0, 0))
        widget_top = widget_global_top_left.y()
        widget_bottom = widget_top + self.height()

        x = click_pos.x() - 15
        if x + menu_w > avail.right() - 8:
            x = avail.right() - menu_w - 8
        if x < avail.left() + 8:
            x = avail.left() + 8

        space_above = widget_top - avail.top()
        space_below = avail.bottom() - widget_bottom

        if space_above >= menu_h or space_above > space_below:
            y = widget_top - menu_h - 6
            if y < avail.top() + 6:
                y = avail.top() + 6
        else:
            y = widget_bottom + 6
            if y + menu_h > avail.bottom() - 6:
                y = avail.bottom() - menu_h - 6

        self._topmost_timer.stop()
        try:
            self.context_menu.exec(QPoint(x, y))
        finally:
            self._topmost_timer.start()
            self.ensure_topmost()

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._mouse_press_pos = event.globalPosition().toPoint()
            self._drag_start_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            if not self.config_manager.config.display.dock_locked:
                self._dragging = True
                self.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))
        elif event.button() == Qt.MouseButton.RightButton:
            self._show_context_menu(event.globalPosition().toPoint())

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._dragging and not self.config_manager.config.display.dock_locked:
            new_pos = event.globalPosition().toPoint() - self._drag_start_pos
            self.move(new_pos)
            cfg = self.config_manager.config.display
            cfg.dock_x = new_pos.x()
            cfg.dock_y = new_pos.y()
            self.ensure_topmost()

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            if self._dragging:
                self._dragging = False
                self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
                self.config_manager.save()
                self._check_edge_snap_after_drag()

            dist = (event.globalPosition().toPoint() - self._mouse_press_pos).manhattanLength()
            if dist < 6:
                if self._is_collapsed:
                    self._expand_from_edge()
                else:
                    self.clicked.emit()

            self.ensure_topmost()

    def _paint_collapsed(self, painter: QPainter):
        """Paints the slim edge indicator bar with 5H (top amber) and 7D (bottom green) LED ticks."""
        cfg = self.config_manager.config.display
        w = self.width()
        h = self.height()

        # Rounded tab background
        bg_color = QColor(cfg.dock_bg_color)
        bg_color.setAlpha(int(255 * (cfg.dock_opacity + (0.06 if self._is_hovered else 0.0))))

        path = QPainterPath()
        path.addRoundedRect(0.5, 0.5, w - 1.0, h - 1.0, 4.0, 4.0)
        painter.fillPath(path, QBrush(bg_color))

        border_color = QColor(255, 255, 255, 45 if self._is_hovered else 25)
        painter.setPen(QPen(border_color, 1))
        painter.drawPath(path)

        pct1 = 100.0
        pct2 = 100.0
        status_5h = "healthy"
        status_7d = "healthy"

        if self.current_snapshot:
            if self.current_snapshot.item_5h:
                pct1 = self.current_snapshot.item_5h.percentage
                status_5h = self.current_snapshot.item_5h.status_level
            elif self.current_snapshot.items:
                pct1 = self.current_snapshot.items[0].percentage
                status_5h = self.current_snapshot.items[0].status_level

            if self.current_snapshot.item_weekly:
                pct2 = self.current_snapshot.item_weekly.percentage
                status_7d = self.current_snapshot.item_weekly.status_level
            elif len(self.current_snapshot.items) > 1:
                pct2 = self.current_snapshot.items[1].percentage
                status_7d = self.current_snapshot.items[1].status_level

        if cfg.usage_display_mode == "used":
            pct1_bar = 100.0 - pct1
            pct2_bar = 100.0 - pct2
        else:
            pct1_bar = pct1
            pct2_bar = pct2

        ticks_total = 8
        tick_w = 10
        tick_x = (w - tick_w) // 2

        # 5H ladder (top segment: 8 ticks)
        lit1 = max(0, min(ticks_total, int(round(pct1_bar / 100.0 * ticks_total))))
        col1 = QColor("#EF4444") if pct1 <= 20.0 or status_5h == "danger" else QColor("#F59E0B")

        painter.setPen(Qt.PenStyle.NoPen)
        for i in range(ticks_total):
            level_from_bottom = ticks_total - 1 - i
            is_lit = level_from_bottom < lit1
            tick_y = 6.0 + i * 2.5
            col = col1 if is_lit else QColor(255, 255, 255, 30)
            painter.setBrush(QBrush(col))
            painter.drawRect(QRect(tick_x, int(tick_y), tick_w, 1))

        # 7D ladder (bottom segment: 8 ticks)
        lit2 = max(0, min(ticks_total, int(round(pct2_bar / 100.0 * ticks_total))))
        col2 = QColor("#EF4444") if pct2 <= 20.0 or status_7d == "danger" else QColor("#10B981")

        for i in range(ticks_total):
            level_from_bottom = ticks_total - 1 - i
            is_lit = level_from_bottom < lit2
            tick_y = 29.0 + i * 2.5
            col = col2 if is_lit else QColor(255, 255, 255, 30)
            painter.setBrush(QBrush(col))
            painter.drawRect(QRect(tick_x, int(tick_y), tick_w, 1))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        if self._is_collapsed:
            self._paint_collapsed(painter)
            painter.end()
            return

        cfg = self.config_manager.config.display
        w = self.width()
        h = self.height()

        # TrafficMonitor style rounded rectangle background
        bg_color = QColor(cfg.dock_bg_color)
        bg_color.setAlpha(int(255 * (cfg.dock_opacity + (0.06 if self._is_hovered else 0.0))))

        path = QPainterPath()
        path.addRoundedRect(0.5, 0.5, w - 1.0, h - 1.0, 4.0, 4.0)
        painter.fillPath(path, QBrush(bg_color))

        border_color = QColor(255, 255, 255, 45 if self._is_hovered else 25)
        painter.setPen(QPen(border_color, 1))
        painter.drawPath(path)

        p1, val1, col1, p2, val2, col2 = self._build_display_lines()

        # Status dot
        dot_color = QColor("#10B981")
        if self.current_snapshot:
            if not self.current_snapshot.is_healthy:
                dot_color = QColor("#EF4444")
            else:
                item_5h = self.current_snapshot.item_5h
                item_weekly = self.current_snapshot.item_weekly
                levels = [it.status_level for it in (item_5h, item_weekly) if it]
                if "danger" in levels:
                    dot_color = QColor("#EF4444")
                elif "warning" in levels:
                    dot_color = QColor("#F59E0B")
                else:
                    dot_color = QColor("#10B981")

        dot_x = 9
        dot_y = h / 2.0
        dot_r = 3
        painter.setBrush(QBrush(dot_color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPoint(int(dot_x), int(dot_y)), dot_r, dot_r)

        if self._is_hovered:
            halo = QColor(dot_color)
            halo.setAlpha(60)
            painter.setBrush(QBrush(halo))
            painter.drawEllipse(QPoint(int(dot_x), int(dot_y)), dot_r + 2, dot_r + 2)

        pad_left = 18
        font = QFont(cfg.dock_font_family.split(",")[0].strip(), cfg.dock_font_size, QFont.Weight.Bold)
        painter.setFont(font)
        fm = QFontMetrics(font)

        row_h = (h - 2) // 2
        y1 = 1
        y2 = 1 + row_h

        # Row 1 (5H Quota)
        rect_r1 = QRect(pad_left, y1, w - pad_left, row_h)
        painter.setPen(QColor(col1))
        painter.drawText(rect_r1, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, p1)

        pw1 = fm.horizontalAdvance(p1)
        painter.setPen(QColor(cfg.dock_text_color))
        rect_r1_v = QRect(pad_left + pw1 + 4, y1, w - pad_left - pw1 - 4, row_h)
        painter.drawText(rect_r1_v, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, val1)

        # Row 2 (7D Weekly Quota)
        rect_r2 = QRect(pad_left, y2, w - pad_left, row_h)
        painter.setPen(QColor(col2))
        painter.drawText(rect_r2, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, p2)

        pw2 = fm.horizontalAdvance(p2)
        painter.setPen(QColor(cfg.dock_text_color))
        rect_r2_v = QRect(pad_left + pw2 + 4, y2, w - pad_left - pw2 - 4, row_h)
        painter.drawText(rect_r2_v, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, val2)

        painter.end()

    def _build_display_lines(self) -> tuple[str, str, str, str, str, str]:
        """
        Constructs focused 2-line display metrics: (p1, val1, col1, p2, val2, col2).
        Top row: 5-hour quota prefixed with 5H:
        Bottom row: weekly quota prefixed with 7D:
        """
        cfg = self.config_manager.config.display
        mode = cfg.usage_display_mode
        show_cd = cfg.dock_show_countdown

        if not self.current_snapshot:
            return ("5H:", "--%", "#F59E0B", "7D:", "--%", "#10B981")

        if not self.current_snapshot.is_healthy:
            err = self.current_snapshot.error_message[:6] if self.current_snapshot.error_message else "异常"
            return ("5H:", err, "#EF4444", "7D:", "错误", "#EF4444")

        items = self.current_snapshot.items
        if not items:
            return ("5H:", "无额度", "#F59E0B", "7D:", "无额度", "#10B981")

        item_5h = self.current_snapshot.item_5h
        item_weekly = self.current_snapshot.item_weekly

        if not item_5h and items:
            item_5h = items[0]
        if not item_weekly and len(items) > 1:
            item_weekly = items[1]

        # 1. 5H Row
        p1 = "5H:"
        val1 = "--%"
        col1 = "#F59E0B"
        if item_5h:
            st1 = item_5h.get_display_stats(mode)
            pct1 = f"{st1['percentage']:.0f}%"
            val1 = f"用{pct1}" if mode == "used" else f"{pct1}"
            col1 = "#EF4444" if item_5h.percentage <= 20.0 else "#F59E0B"
            if show_cd and item_5h.reset_time and item_5h.percentage < 100.0:
                cd1 = item_5h.format_countdown().replace("小时", "h").replace("分钟", "m")
                val1 = f"{val1} {cd1}"

        # 2. 7D Row
        p2 = "7D:"
        val2 = "--%"
        col2 = "#10B981"
        if item_weekly:
            st2 = item_weekly.get_display_stats(mode)
            pct2 = f"{st2['percentage']:.0f}%"
            val2 = f"用{pct2}" if mode == "used" else f"{pct2}"
            col2 = "#EF4444" if item_weekly.percentage <= 20.0 else "#10B981"
            if show_cd and item_weekly.reset_time and item_weekly.percentage < 100.0:
                cd2 = item_weekly.format_countdown().replace("小时", "h").replace("分钟", "m")
                if "天" in cd2:
                    import re
                    m = re.match(r"(\d+天(?:\d+h)?)", cd2)
                    if m:
                        cd2 = m.group(1)
                val2 = f"{val2} {cd2}"

        return (p1, val1, col1, p2, val2, col2)

    def _build_display_text(self) -> str:
        """Helper for single-line representation, testing, and debugging."""
        p1, val1, _, p2, val2, _ = self._build_display_lines()
        if p2 or val2:
            return f"{p1} {val1} | {p2} {val2}".strip()
        return f"{p1} {val1}".strip()
