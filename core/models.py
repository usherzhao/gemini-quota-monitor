"""
Gemini Quota Monitor - Data Models
Defines structured quota items, models, snapshots, and percentage-based calculations for Gemini.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class QuotaItem:
    """Represents a quota metric (e.g., 5-Hour Window, Weekly Quota, Pro / Flash models)."""
    id: str
    name: str
    remaining: float
    total: float = 100.0
    unit: str = "%"
    reset_time: Optional[datetime] = None
    reset_text: str = ""
    description: str = ""

    @property
    def percentage(self) -> float:
        """Calculate remaining percentage (0.0 - 100.0)."""
        if self.total <= 0:
            return 0.0
        return max(0.0, min(100.0, (self.remaining / self.total) * 100.0))

    @property
    def used(self) -> float:
        """Calculate used amount."""
        return max(0.0, self.total - self.remaining)

    @property
    def used_percentage(self) -> float:
        """Calculate used percentage (0.0 - 100.0)."""
        if self.total <= 0:
            return 0.0
        return max(0.0, min(100.0, (self.used / self.total) * 100.0))

    @property
    def status_level(self) -> str:
        """Returns 'good', 'warning', or 'danger' based on remaining ratio."""
        pct = self.percentage
        if pct > 50.0:
            return "good"      # Green
        elif pct > 20.0:
            return "warning"   # Amber
        else:
            return "danger"    # Red

    @property
    def status_color(self) -> str:
        """Hex color code corresponding to current status."""
        level = self.status_level
        if level == "good":
            return "#10B981"  # Emerald 500
        elif level == "warning":
            return "#F59E0B"  # Amber 500
        else:
            return "#EF4444"  # Red 500

    def get_display_stats(self, mode: str = "remaining") -> Dict[str, Any]:
        """
        Returns display statistics formatted according to active mode ('remaining' or 'used').
        """
        is_pct = self.unit == "%" or (self.total == 100.0 and self.unit in ("%", ""))

        if mode == "used":
            used_val = self.used
            used_pct = self.used_percentage
            
            if is_pct:
                val_str = f"{used_val:.1f}%" if used_val != int(used_val) else f"{int(used_val)}%"
                text = f"已用 {val_str}"
                short_text = f"用{val_str}"
            else:
                text = f"已用 {used_val:.0f}/{self.total:.0f} {self.unit} ({used_pct:.0f}%)"
                short_text = f"用{used_val:.0f}/{self.total:.0f}"

            return {
                "mode": "used",
                "label": "已用",
                "val": used_val,
                "total": self.total,
                "unit": self.unit,
                "percentage": used_pct,
                "text": text,
                "short_text": short_text,
                "badge_num": f"{int(used_pct)}%",
                "color": self.status_color,
            }
        else:
            rem_val = self.remaining
            rem_pct = self.percentage

            if is_pct:
                val_str = f"{rem_val:.1f}%" if rem_val != int(rem_val) else f"{int(rem_val)}%"
                text = f"剩余 {val_str}"
                short_text = f"{val_str}"
            else:
                text = f"剩余 {rem_val:.0f}/{self.total:.0f} {self.unit} ({rem_pct:.0f}%)"
                short_text = f"{rem_val:.0f}/{self.total:.0f}"

            return {
                "mode": "remaining",
                "label": "剩余",
                "val": rem_val,
                "total": self.total,
                "unit": self.unit,
                "percentage": rem_pct,
                "text": text,
                "short_text": short_text,
                "badge_num": f"{int(rem_pct)}%",
                "color": self.status_color,
            }

    def format_countdown(self) -> str:
        """Format human-readable reset countdown string."""
        if self.reset_text:
            return self.reset_text
        if not self.reset_time:
            return "未知"
        now = datetime.now(self.reset_time.tzinfo) if self.reset_time.tzinfo else datetime.now()
        if self.reset_time <= now:
            return "即将重置"
        diff = self.reset_time - now
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
class QuotaSnapshot:
    """Snapshot of all quotas fetched at a given time."""
    source_name: str
    account_label: str
    project_id: str = ""
    items: List[QuotaItem] = field(default_factory=list)
    timestamp: datetime = field(default_factory=datetime.now)
    status: str = "ok"
    error_message: str = ""

    @property
    def item_5h(self) -> Optional[QuotaItem]:
        """Returns the 5-hour window quota item."""
        for it in self.items:
            lid = it.id.lower()
            lname = it.name.lower()
            if "5h" in lid or "five" in lid or "5小时" in lname:
                return it
        return None

    @property
    def item_weekly(self) -> Optional[QuotaItem]:
        """Returns the weekly quota item."""
        for it in self.items:
            lid = it.id.lower()
            lname = it.name.lower()
            if "week" in lid or "周" in lname:
                return it
        return None

    @property
    def primary_item(self) -> Optional[QuotaItem]:
        """Primary item for status coloring (uses 5h if available, otherwise weekly or min)."""
        if self.item_5h:
            return self.item_5h
        if self.item_weekly:
            return self.item_weekly
        if self.items:
            return min(self.items, key=lambda it: it.percentage)
        return None

    @property
    def overall_percentage(self) -> float:
        primary = self.primary_item
        return primary.percentage if primary else 100.0

    @property
    def is_healthy(self) -> bool:
        return self.status in ("ok", "simulated")
