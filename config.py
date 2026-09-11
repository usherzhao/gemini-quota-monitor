"""
Gemini Quota Monitor - Configuration Management
Handles persistent user preferences, credentials, and network proxy settings.
"""

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional


def get_default_config_path() -> Path:
    """Returns path to configuration file in AppData or local fallback."""
    appdata = os.environ.get("APPDATA")
    if appdata:
        config_dir = Path(appdata) / "GeminiQuotaMonitor"
    else:
        config_dir = Path.home() / ".gemini_quota_monitor"
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir / "config.json"


@dataclass
class ProxyConfig:
    enabled: bool = False
    proxy_type: str = "socks5"  # 'socks5', 'http', 'https'
    host: str = "127.0.0.1"
    port: int = 7890
    username: str = ""
    password: str = ""

    def get_proxy_url(self) -> Optional[str]:
        """Builds formatted proxy URL string (e.g., socks5h://127.0.0.1:7890)."""
        if not self.enabled or not self.host or not self.port:
            return None
        
        auth_part = ""
        if self.username:
            if self.password:
                auth_part = f"{self.username}:{self.password}@"
            else:
                auth_part = f"{self.username}@"

        scheme = "socks5h" if self.proxy_type.lower() == "socks5" else self.proxy_type.lower()
        return f"{scheme}://{auth_part}{self.host}:{self.port}"


@dataclass
class GeminiAuthConfig:
    access_token: str = ""
    refresh_token: str = ""
    token_expiry: str = ""  # ISO format string or timestamp
    account_email: str = ""
    project_id: str = ""
    auth_kind: str = "oauth"  # 'oauth', 'token', 'cliproxy'
    cliproxy_base_url: str = "http://127.0.0.1:8080"
    cliproxy_auth_index: str = ""


@dataclass
class SimulatorConfig:
    enabled: bool = False
    profile: str = "normal"  # 'normal', 'warning', 'depleted'
    remaining_5h: float = 85.0
    remaining_weekly: float = 65.0


@dataclass
class GeneralConfig:
    active_source: str = "gemini_oauth"  # 'gemini_oauth', 'simulator'
    refresh_interval_sec: int = 120
    autostart: bool = False
    notify_low_quota: bool = True
    low_quota_threshold: int = 20  # Percentage threshold for warning


@dataclass
class DisplayConfig:
    usage_display_mode: str = "remaining"  # 'remaining' (default) or 'used'
    show_tray_icon: bool = True
    tray_style: str = "circle_ring"  # 'circle_ring', 'badge_percent', 'plain_icon'
    show_taskbar_dock: bool = True
    dock_x: int = -1  # -1 means auto-dock to taskbar
    dock_y: int = -1
    dock_width: int = 86
    dock_height: int = 36
    dock_bg_color: str = "#18181B"
    dock_text_color: str = "#F8FAFC"
    dock_font_family: str = "Segoe UI, Microsoft YaHei, sans-serif"
    dock_font_size: int = 9
    dock_opacity: float = 0.92
    dock_locked: bool = False
    dock_show_countdown: bool = False
    dock_auto_collapse_edge: bool = True


@dataclass
class AppConfig:
    general: GeneralConfig = field(default_factory=GeneralConfig)
    gemini: GeminiAuthConfig = field(default_factory=GeminiAuthConfig)
    proxy: ProxyConfig = field(default_factory=ProxyConfig)
    display: DisplayConfig = field(default_factory=DisplayConfig)
    simulator: SimulatorConfig = field(default_factory=SimulatorConfig)


class ConfigManager:
    """Manages reading and writing application configuration to disk."""

    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = config_path or get_default_config_path()
        self.config = self.load()

    def load(self) -> AppConfig:
        """Loads configuration from JSON file or returns defaults."""
        if not self.config_path.exists():
            default_config = AppConfig()
            self.save(default_config)
            return default_config

        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            general = GeneralConfig(**data.get("general", {}))
            gemini = GeminiAuthConfig(**data.get("gemini", {}))
            proxy = ProxyConfig(**data.get("proxy", {}))
            display = DisplayConfig(**data.get("display", {}))
            simulator = SimulatorConfig(**data.get("simulator", {}))

            return AppConfig(
                general=general,
                gemini=gemini,
                proxy=proxy,
                display=display,
                simulator=simulator,
            )
        except Exception as e:
            print(f"[ConfigManager] Error reading config: {e}. Falling back to default.")
            return AppConfig()

    def save(self, config: Optional[AppConfig] = None):
        """Persists configuration to disk as JSON."""
        if config:
            self.config = config

        try:
            raw_dict = asdict(self.config)
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(raw_dict, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[ConfigManager] Failed to save config: {e}")

    def update(self, **kwargs):
        """Helper to update and immediately persist config changes."""
        for key, value in kwargs.items():
            if hasattr(self.config, key):
                setattr(self.config, key, value)
        self.save()


_config_manager: Optional[ConfigManager] = None


def get_config_manager() -> ConfigManager:
    global _config_manager
    if _config_manager is None:
        _config_manager = ConfigManager()
    return _config_manager
