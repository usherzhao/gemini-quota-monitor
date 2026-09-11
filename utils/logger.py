r"""
Gemini Quota Monitor - Centralized Logging System
Writes diagnostic logs to %APPDATA%\GeminiQuotaMonitor\app.log with rotation.
"""

import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import subprocess
import sys


def get_log_file_path() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        log_dir = Path(appdata) / "GeminiQuotaMonitor"
    else:
        log_dir = Path.home() / ".gemini_quota_monitor"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / "app.log"


def setup_logger() -> logging.Logger:
    logger = logging.getLogger("GeminiQuotaMonitor")
    logger.setLevel(logging.DEBUG)

    if logger.handlers:
        return logger

    log_path = get_log_file_path()
    file_handler = RotatingFileHandler(
        str(log_path),
        maxBytes=5 * 1024 * 1024,  # 5 MB
        backupCount=3,
        encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [%(filename)s:%(lineno)d] - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    if sys.stdout is not None and hasattr(sys.stdout, "write"):
        try:
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setLevel(logging.INFO)
            console_handler.setFormatter(formatter)
            logger.addHandler(console_handler)
        except Exception:
            pass

    return logger


logger = setup_logger()


def open_log_file():
    """Opens the log file in Windows default text editor (Notepad)."""
    log_path = get_log_file_path()
    if not log_path.exists():
        log_path.write_text("[GeminiQuotaMonitor] 日志文件初始化...\n", encoding="utf-8")
    
    try:
        os.startfile(str(log_path))
    except Exception:
        subprocess.Popen(["notepad.exe", str(log_path)])
