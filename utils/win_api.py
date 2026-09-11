"""
Gemini Quota Monitor - Windows API Helper
Interacts with Windows Desktop Window Manager, Taskbar, and DPI scaling.
"""

import ctypes
from ctypes import Structure, byref, c_int, c_long, sizeof
from dataclasses import dataclass
from typing import Optional, Tuple


class RECT(Structure):
    _fields_ = [
        ("left", c_long),
        ("top", c_long),
        ("right", c_long),
        ("bottom", c_long),
    ]

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top


@dataclass
class TaskbarInfo:
    rect: RECT
    edge: str  # 'bottom', 'top', 'left', 'right'
    tray_rect: Optional[RECT] = None


def get_taskbar_info() -> Optional[TaskbarInfo]:
    """
    Retrieves taskbar position and bounds using Windows Win32 API.
    Works seamlessly across Windows 10 and Windows 11.
    """
    try:
        user32 = ctypes.windll.user32
        hwnd_taskbar = user32.FindWindowW("Shell_TrayWnd", None)
        if not hwnd_taskbar:
            return None

        tb_rect = RECT()
        if not user32.GetWindowRect(hwnd_taskbar, byref(tb_rect)):
            return None

        if tb_rect.width <= 0 or tb_rect.height <= 0:
            return None

        if tb_rect.width >= tb_rect.height:
            edge = "bottom" if tb_rect.top > 100 else "top"
        else:
            edge = "right" if tb_rect.left > 100 else "left"

        tray_rect = None
        hwnd_tray = user32.FindWindowExW(hwnd_taskbar, None, "TrayNotifyWnd", None)
        if hwnd_tray:
            t_rect = RECT()
            if user32.GetWindowRect(hwnd_tray, byref(t_rect)):
                if t_rect.width > 0 and t_rect.height > 0:
                    tray_rect = t_rect

        return TaskbarInfo(rect=tb_rect, edge=edge, tray_rect=tray_rect)
    except Exception as e:
        print(f"[WinAPI] Error getting taskbar info: {e}")
        return None


def calculate_taskbar_dock_position(widget_width: int, widget_height: int) -> Tuple[int, int]:
    """
    Calculates the optimal screen (X, Y) coordinates for docking on the taskbar.
    Uses Qt's primaryScreen geometry when available for high-DPI and multi-monitor accuracy,
    falling back to Win32 API.
    """
    try:
        from PyQt6.QtGui import QGuiApplication
        screen = QGuiApplication.primaryScreen()
        if screen:
            screen_geom = screen.geometry()
            avail_geom = screen.availableGeometry()

            diff_bottom = screen_geom.bottom() - avail_geom.bottom()
            diff_top = avail_geom.top() - screen_geom.top()
            diff_left = avail_geom.left() - screen_geom.left()
            diff_right = screen_geom.right() - avail_geom.right()

            if diff_bottom > 10:  # Bottom taskbar (default)
                tb_h = diff_bottom
                y = avail_geom.bottom() + 1 + (tb_h - widget_height) // 2
                x = avail_geom.right() - widget_width - 180
                return x, y
            elif diff_top > 10:   # Top taskbar
                tb_h = diff_top
                y = screen_geom.top() + (tb_h - widget_height) // 2
                x = avail_geom.right() - widget_width - 180
                return x, y
            elif diff_left > 10:  # Left taskbar
                tb_w = diff_left
                x = screen_geom.left() + (tb_w - widget_width) // 2
                y = avail_geom.bottom() - widget_height - 100
                return x, y
            elif diff_right > 10: # Right taskbar
                tb_w = diff_right
                x = avail_geom.right() + 1 + (tb_w - widget_width) // 2
                y = avail_geom.bottom() - widget_height - 100
                return x, y
            else:
                return screen_geom.width() - widget_width - 200, screen_geom.height() - widget_height - 6
    except Exception as e:
        print(f"[WinAPI] Qt screen fallback: {e}")

    tb_info = get_taskbar_info()
    if not tb_info:
        user32 = ctypes.windll.user32
        screen_w = user32.GetSystemMetrics(0)
        screen_h = user32.GetSystemMetrics(1)
        return max(10, screen_w - widget_width - 200), max(10, screen_h - widget_height - 8)

    tb = tb_info.rect
    tray = tb_info.tray_rect

    if tb_info.edge == "bottom":
        y = tb.top + (tb.height - widget_height) // 2
        x = (tray.left - widget_width - 12) if (tray and tray.left > 100) else (tb.right - widget_width - 180)
        return x, y
    elif tb_info.edge == "top":
        y = tb.top + (tb.height - widget_height) // 2
        x = (tray.left - widget_width - 12) if (tray and tray.left > 100) else (tb.right - widget_width - 180)
        return x, y
    elif tb_info.edge == "left":
        x = tb.left + (tb.width - widget_width) // 2
        y = (tray.top - widget_height - 10) if (tray and tray.top > 100) else (tb.bottom - widget_height - 120)
        return x, y
    else:  # right
        x = tb.left + (tb.width - widget_width) // 2
        y = (tray.top - widget_height - 10) if (tray and tray.top > 100) else (tb.bottom - widget_height - 120)
        return x, y


def apply_dwm_window_attributes(hwnd: int, dark_mode: bool = True, round_corners: bool = True):
    """Applies modern Windows 10/11 DWM dark titlebar and rounded corners if supported."""
    try:
        dwmapi = ctypes.windll.dwmapi
        dark_value = c_int(1 if dark_mode else 0)
        dwmapi.DwmSetWindowAttribute(hwnd, 20, byref(dark_value), sizeof(dark_value))

        if round_corners:
            corner_value = c_int(2)
            dwmapi.DwmSetWindowAttribute(hwnd, 33, byref(corner_value), sizeof(corner_value))
    except Exception:
        pass
