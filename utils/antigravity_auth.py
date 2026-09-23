"""
Gemini Quota Monitor - Antigravity IDE Auth Token Extractor
Extracts Google OAuth access_token and refresh_token from local Antigravity IDE state database.
"""

import base64
import os
from pathlib import Path
import re
import sqlite3
from typing import Optional, Tuple

from utils.logger import logger


def get_antigravity_state_db_paths() -> list[Path]:
    """Returns candidate paths for Antigravity IDE state.vscdb SQLite file."""
    candidates = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        candidates.append(Path(appdata) / "Antigravity IDE" / "User" / "globalStorage" / "state.vscdb")
        candidates.append(Path(appdata) / "antigravity" / "User" / "globalStorage" / "state.vscdb")

    home = Path.home()
    candidates.append(home / ".config" / "Antigravity IDE" / "User" / "globalStorage" / "state.vscdb")
    candidates.append(home / "Library" / "Application Support" / "Antigravity IDE" / "User" / "globalStorage" / "state.vscdb")
    return candidates


def extract_antigravity_tokens() -> Tuple[Optional[str], Optional[str]]:
    """
    Safely reads access_token and refresh_token from Antigravity IDE state.vscdb without locking.
    Returns (access_token, refresh_token).
    """
    for db_path in get_antigravity_state_db_paths():
        if not db_path.exists():
            continue
        try:
            # Open with read-only URI mode to prevent any file locks with the running IDE
            conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
            cur = conn.cursor()
            cur.execute("SELECT value FROM ItemTable WHERE key = 'antigravityUnifiedStateSync.oauthToken'")
            row = cur.fetchone()
            conn.close()

            if not row or not row[0]:
                continue

            raw_b64 = row[0]
            try:
                decoded = base64.b64decode(raw_b64)
            except Exception:
                continue

            at, rt = None, None

            # Antigravity IDE protobuf wraps tokens in inner base64 chunks
            for m in re.findall(rb'[A-Za-z0-9+/=]{20,}', decoded):
                try:
                    inner = base64.b64decode(m)
                    rt_m = re.search(rb'(1//[A-Za-z0-9_\-]+)', inner)
                    if rt_m and not rt:
                        rt = rt_m.group(1).decode('ascii')
                    at_m = re.search(rb'(ya29\.[A-Za-z0-9_\-]+)', inner)
                    if at_m and not at:
                        at = at_m.group(1).decode('ascii')
                except Exception:
                    pass

            # Also check direct regex on decoded string in case format differs
            if not rt:
                rt_m = re.search(rb'(1//[A-Za-z0-9_\-]+)', decoded)
                if rt_m:
                    rt = rt_m.group(1).decode('ascii')
            if not at:
                at_m = re.search(rb'(ya29\.[A-Za-z0-9_\-]+)', decoded)
                if at_m:
                    at = at_m.group(1).decode('ascii')

            if at or rt:
                logger.debug(f"Successfully extracted tokens from {db_path} (has_at={bool(at)}, has_rt={bool(rt)})")
                return at, rt
        except Exception as e:
            logger.debug(f"Could not read tokens from {db_path}: {e}")
            continue

    return None, None
