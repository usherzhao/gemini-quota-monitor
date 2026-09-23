"""
Gemini Quota Monitor - Multi-Account & Historical Quota Manager
Persists and tracks quota recovery, countdowns, and readiness for multiple Antigravity IDE accounts.
Supports live API querying using persisted Google OAuth refresh tokens.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from utils.logger import logger


def get_default_accounts_path() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        data_dir = Path(appdata) / "GeminiQuotaMonitor"
    else:
        data_dir = Path.home() / ".gemini_quota_monitor"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "accounts.json"


def format_countdown_from_dt(reset_time: Optional[datetime]) -> str:
    """Formats human-readable countdown to reset_time."""
    if not reset_time:
        return "未知"
    now = datetime.now(reset_time.tzinfo) if reset_time.tzinfo else datetime.now()
    if now >= reset_time:
        return "已重置"
    diff = reset_time - now
    days = diff.days
    hours, rem = divmod(diff.seconds, 3600)
    minutes, _ = divmod(rem, 60)
    parts = []
    if days > 0:
        parts.append(f"{days}天")
    if hours > 0 or days > 0:
        parts.append(f"{hours}小时")
    parts.append(f"{minutes}分钟")
    return "".join(parts)


@dataclass
class AccountQuotaRecord:
    email: str
    name: str = ""
    plan_name: str = ""
    is_active: bool = False
    last_seen: str = field(default_factory=lambda: datetime.now().isoformat())
    last_api_refresh: Optional[str] = None  # ISO format string of last successful API fetch
    quota_source: str = "api"  # 'api' (live verified via Google API) or 'estimated' (countdown clock)

    # Gemini 5-Hour rolling window
    gemini_5h_remaining: float = 100.0
    gemini_5h_reset_time: Optional[str] = None  # ISO format string

    # Gemini Weekly quota
    gemini_weekly_remaining: float = 100.0
    gemini_weekly_reset_time: Optional[str] = None

    # Claude/GPT 5-Hour & Weekly quota
    claude_5h_remaining: float = 100.0
    claude_5h_reset_time: Optional[str] = None
    claude_weekly_remaining: float = 100.0
    claude_weekly_reset_time: Optional[str] = None

    auth_kind: str = "antigravity_ide"  # 'antigravity_ide' or 'oauth'
    refresh_token: str = ""

    def _parse_dt(self, iso_str: Optional[str]) -> Optional[datetime]:
        if not iso_str:
            return None
        try:
            return datetime.fromisoformat(iso_str)
        except Exception:
            return None

    @property
    def dt_5h_reset(self) -> Optional[datetime]:
        return self._parse_dt(self.gemini_5h_reset_time)

    @property
    def dt_weekly_reset(self) -> Optional[datetime]:
        return self._parse_dt(self.gemini_weekly_reset_time)

    @property
    def has_api_token(self) -> bool:
        """Returns True if this account has a saved OAuth refresh token for remote queries."""
        return bool(self.refresh_token and self.refresh_token.strip())

    def get_5h_status(self) -> Dict[str, Any]:
        """
        Returns dynamic 5-hour quota status.
        If past the reset_time, returns 100% (recovered).
        """
        dt = self.dt_5h_reset
        if not dt:
            return {
                "percentage": self.gemini_5h_remaining,
                "is_ready": self.gemini_5h_remaining >= 80.0,
                "countdown": "正常",
                "text": f"{self.gemini_5h_remaining:.0f}%",
                "source": self.quota_source,
                "has_token": self.has_api_token,
            }

        now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
        if now >= dt:
            return {
                "percentage": 100.0,
                "is_ready": True,
                "countdown": "已回满",
                "text": "100% (已满血恢复)",
                "source": self.quota_source,
                "has_token": self.has_api_token,
            }
        else:
            cd = format_countdown_from_dt(dt)
            return {
                "percentage": self.gemini_5h_remaining,
                "is_ready": False,
                "countdown": cd,
                "text": f"{self.gemini_5h_remaining:.0f}% (剩 {cd})",
                "source": self.quota_source,
                "has_token": self.has_api_token,
            }

    def get_weekly_status(self) -> Dict[str, Any]:
        """
        Returns dynamic weekly quota status.
        """
        dt = self.dt_weekly_reset
        if not dt:
            return {
                "percentage": self.gemini_weekly_remaining,
                "countdown": "周周期",
                "text": f"{self.gemini_weekly_remaining:.0f}%",
                "source": self.quota_source,
            }

        now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
        if now >= dt:
            return {
                "percentage": 100.0,
                "countdown": "已重置",
                "text": "100% (新周期)",
                "source": self.quota_source,
            }
        else:
            cd = format_countdown_from_dt(dt)
            return {
                "percentage": self.gemini_weekly_remaining,
                "countdown": cd,
                "text": f"{self.gemini_weekly_remaining:.0f}% (剩 {cd})",
                "source": self.quota_source,
            }

    @property
    def is_5h_ready(self) -> bool:
        """True if 5-hour quota is >= 80% or has passed reset time."""
        return self.get_5h_status()["is_ready"]

    @property
    def recommendation_level(self) -> str:
        """
        Returns 'active', 'ready', 'available', or 'cooling'.
        """
        if self.is_active:
            return "active"
        st = self.get_5h_status()
        if st["percentage"] >= 95.0 or st["is_ready"]:
            return "ready"
        elif st["percentage"] >= 40.0:
            return "available"
        else:
            return "cooling"


class AccountManager:
    """Manages local storage, token persistence, and querying of multiple Antigravity IDE accounts."""

    def __init__(self, file_path: Optional[Path] = None):
        self.file_path = file_path or get_default_accounts_path()
        self.accounts: Dict[str, AccountQuotaRecord] = {}
        self.load()

    def load(self):
        if not self.file_path.exists():
            self.accounts = {}
            return

        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                raw_data = json.load(f)

            accs = {}
            for item in raw_data:
                if isinstance(item, dict) and item.get("email"):
                    # Tolerate new/missing fields gracefully
                    valid_keys = AccountQuotaRecord.__dataclass_fields__.keys()
                    clean_dict = {k: v for k, v in item.items() if k in valid_keys}
                    acc = AccountQuotaRecord(**clean_dict)
                    accs[acc.email.lower()] = acc
            self.accounts = accs
        except Exception as e:
            logger.warning(f"Error reading accounts.json: {e}")
            self.accounts = {}

    def save(self):
        try:
            data = [asdict(acc) for acc in self.accounts.values()]
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"Error saving accounts.json: {e}")

    def update_or_add_account(
        self,
        email: str,
        name: str = "",
        plan_name: str = "",
        gemini_5h_remaining: float = 100.0,
        gemini_5h_reset_time: Optional[datetime] = None,
        gemini_weekly_remaining: float = 100.0,
        gemini_weekly_reset_time: Optional[datetime] = None,
        claude_5h_remaining: float = 100.0,
        claude_5h_reset_time: Optional[datetime] = None,
        claude_weekly_remaining: float = 100.0,
        claude_weekly_reset_time: Optional[datetime] = None,
        is_active: bool = True,
        refresh_token: str = "",
        quota_source: str = "api",
    ) -> AccountQuotaRecord:
        """Updates or registers an account, setting it as active if specified."""
        key = email.strip().lower()
        if not key:
            return None

        # Mark all other accounts as not active if this one is active
        if is_active:
            for acc in self.accounts.values():
                acc.is_active = False

        record = self.accounts.get(key)
        if not record:
            record = AccountQuotaRecord(
                email=email.strip(),
                name=name,
                plan_name=plan_name,
                is_active=is_active,
                refresh_token=refresh_token.strip(),
                quota_source=quota_source,
            )
            self.accounts[key] = record

        record.name = name or record.name
        record.plan_name = plan_name or record.plan_name
        record.is_active = is_active
        record.last_seen = datetime.now().isoformat()
        record.quota_source = quota_source

        # Preserve existing refresh_token if new one is empty
        if refresh_token and refresh_token.strip():
            record.refresh_token = refresh_token.strip()

        if quota_source == "api":
            record.last_api_refresh = datetime.now().isoformat()

        record.gemini_5h_remaining = gemini_5h_remaining
        if gemini_5h_reset_time:
            record.gemini_5h_reset_time = gemini_5h_reset_time.isoformat()

        record.gemini_weekly_remaining = gemini_weekly_remaining
        if gemini_weekly_reset_time:
            record.gemini_weekly_reset_time = gemini_weekly_reset_time.isoformat()

        record.claude_5h_remaining = claude_5h_remaining
        if claude_5h_reset_time:
            record.claude_5h_reset_time = claude_5h_reset_time.isoformat()

        record.claude_weekly_remaining = claude_weekly_remaining
        if claude_weekly_reset_time:
            record.claude_weekly_reset_time = claude_weekly_reset_time.isoformat()

        self.save()
        return record

    def update_quota_from_api_items(
        self,
        email: str,
        items: list,
        user_name: str = "",
        plan_name: str = "",
    ) -> Optional[AccountQuotaRecord]:
        """Updates an existing account's quota figures from remote API items."""
        key = email.strip().lower()
        record = self.accounts.get(key)
        if not record:
            return None

        if user_name:
            record.name = user_name
        if plan_name:
            record.plan_name = plan_name

        item_5h = next((it for it in items if "5h" in it.id.lower() and "claude" not in it.id.lower()), None)
        item_week = next((it for it in items if ("weekly" in it.id.lower() or "week" in it.id.lower()) and "claude" not in it.id.lower()), None)
        item_c5h = next((it for it in items if "claude" in it.id.lower() and "5h" in it.id.lower()), None)
        item_cweek = next((it for it in items if "claude" in it.id.lower() and ("weekly" in it.id.lower() or "week" in it.id.lower())), None)

        if item_5h:
            record.gemini_5h_remaining = item_5h.remaining
            if item_5h.reset_time:
                record.gemini_5h_reset_time = item_5h.reset_time.isoformat()
        if item_week:
            record.gemini_weekly_remaining = item_week.remaining
            if item_week.reset_time:
                record.gemini_weekly_reset_time = item_week.reset_time.isoformat()
        if item_c5h:
            record.claude_5h_remaining = item_c5h.remaining
            if item_c5h.reset_time:
                record.claude_5h_reset_time = item_c5h.reset_time.isoformat()
        if item_cweek:
            record.claude_weekly_remaining = item_cweek.remaining
            if item_cweek.reset_time:
                record.claude_weekly_reset_time = item_cweek.reset_time.isoformat()

        record.quota_source = "api"
        record.last_api_refresh = datetime.now().isoformat()
        record.last_seen = datetime.now().isoformat()
        self.save()
        return record

    def remove_account(self, email: str) -> bool:
        key = email.strip().lower()
        if key in self.accounts:
            del self.accounts[key]
            self.save()
            return True
        return False

    def list_accounts(self) -> List[AccountQuotaRecord]:
        """Returns sorted list: active first, then ready (recovered), then cooling."""
        def sort_key(acc: AccountQuotaRecord):
            order = {"active": 0, "ready": 1, "available": 2, "cooling": 3}
            return (order.get(acc.recommendation_level, 4), -acc.get_5h_status()["percentage"])

        return sorted(self.accounts.values(), key=sort_key)

    def get_active_account(self) -> Optional[AccountQuotaRecord]:
        for acc in self.accounts.values():
            if acc.is_active:
                return acc
        return None

    def get_ready_count(self) -> int:
        """Returns number of inactive accounts whose 5H quota is ready (recovered)."""
        count = 0
        for acc in self.accounts.values():
            if not acc.is_active and acc.is_5h_ready:
                count += 1
        return count


_global_account_manager: Optional[AccountManager] = None


def get_account_manager() -> AccountManager:
    global _global_account_manager
    if _global_account_manager is None:
        _global_account_manager = AccountManager()
    return _global_account_manager
