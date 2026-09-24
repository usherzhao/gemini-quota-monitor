"""
Gemini Quota Monitor - Windows Autostart Manager
Configures application to start automatically with Windows via registry.
"""

import os
import sys
import winreg
from pathlib import Path

RUN_REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_NAME = "GeminiQuotaMonitor"


def is_autostart_enabled() -> bool:
    """Checks if the application is set to autostart in Windows registry."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_REG_KEY, 0, winreg.KEY_READ) as key:
            val, _ = winreg.QueryValueEx(key, APP_NAME)
            return bool(val)
    except FileNotFoundError:
        return False
    except Exception as e:
        print(f"[Autostart] Error checking status: {e}")
        return False


def set_autostart(enabled: bool) -> bool:
    """Enables or disables autostart in Windows registry."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_REG_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                if getattr(sys, "frozen", False):
                    exe_path = f'"{sys.executable}"'
                else:
                    dist_exe = Path(__file__).resolve().parent.parent / "dist" / "GeminiQuotaMonitor.exe"
                    if dist_exe.exists():
                        exe_path = f'"{dist_exe}"'
                    else:
                        python_exe = Path(sys.executable)
                        pythonw_exe = python_exe.parent / "pythonw.exe"
                        py_bin = str(pythonw_exe) if pythonw_exe.exists() else str(python_exe)
                        main_py = Path(__file__).resolve().parent.parent / "main.py"
                        exe_path = f'"{py_bin}" "{main_py}"'
                
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, exe_path)
                print(f"[Autostart] Enabled: {exe_path}")
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                    print("[Autostart] Disabled successfully")
                except FileNotFoundError:
                    pass
        return True
    except Exception as e:
        print(f"[Autostart] Error setting autostart to {enabled}: {e}")
        return False
