"""
Gemini Quota Monitor - Antigravity IDE Auth Token & Port Extractor
Extracts Google OAuth tokens, user profiles, and server ports from local Antigravity IDE state databases and logs.
Supports multiple installations, sorts by modification time, and verifies account emails offline and online.
"""

import base64
import glob
import os
from pathlib import Path
import re
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from utils.logger import logger


def get_antigravity_state_db_paths() -> List[Path]:
    """
    Returns candidate paths for Antigravity IDE state.vscdb SQLite files,
    sorted by file modification time (newest first).
    """
    candidates: List[Tuple[Path, float]] = []
    seen = set()

    appdata = os.environ.get("APPDATA", "")
    localappdata = os.environ.get("LOCALAPPDATA", "")
    home = Path.home()

    search_patterns = [
        os.path.join(appdata, "*antigravity*", "User", "globalStorage", "state.vscdb"),
        os.path.join(appdata, "*Antigravity*", "User", "globalStorage", "state.vscdb"),
        os.path.join(localappdata, "*antigravity*", "User", "globalStorage", "state.vscdb"),
        os.path.join(localappdata, "*Antigravity*", "User", "globalStorage", "state.vscdb"),
        os.path.join(appdata, "Cursor", "User", "globalStorage", "state.vscdb"),
        os.path.join(appdata, "Code", "User", "globalStorage", "state.vscdb"),
        os.path.join(str(home), ".config", "*antigravity*", "User", "globalStorage", "state.vscdb"),
        os.path.join(str(home), ".config", "*Antigravity*", "User", "globalStorage", "state.vscdb"),
        os.path.join(str(home), "Library", "Application Support", "*antigravity*", "User", "globalStorage", "state.vscdb"),
        os.path.join(str(home), "Library", "Application Support", "*Antigravity*", "User", "globalStorage", "state.vscdb"),
    ]

    for pat in search_patterns:
        try:
            for match in glob.glob(pat):
                norm = os.path.normpath(match).lower()
                if norm not in seen and os.path.exists(match):
                    seen.add(norm)
                    candidates.append((Path(match), os.path.getmtime(match)))
        except Exception:
            pass

    # Sort descending by modification time (most recently active database first)
    candidates.sort(key=lambda x: x[1], reverse=True)
    return [c[0] for c in candidates]


def get_antigravity_http_port_from_logs() -> Optional[int]:
    """
    Reads the latest HTTP Connect-RPC port directly from Antigravity language_server.log.
    Works reliably without requiring administrator privileges or process inspection.
    """
    appdata = os.environ.get("APPDATA", "")
    localappdata = os.environ.get("LOCALAPPDATA", "")
    search_patterns = [
        os.path.join(appdata, "*antigravity*", "logs", "language_server.log"),
        os.path.join(appdata, "*Antigravity*", "logs", "language_server.log"),
        os.path.join(localappdata, "*antigravity*", "logs", "language_server.log"),
        os.path.join(localappdata, "*Antigravity*", "logs", "language_server.log"),
    ]

    for pat in search_patterns:
        try:
            for log_file in glob.glob(pat):
                if not os.path.exists(log_file):
                    continue
                with open(log_file, "r", encoding="utf-8", errors="ignore") as fp:
                    lines = fp.readlines()
                    # Search backwards from the end of the log
                    for line in reversed(lines):
                        m = re.search(r"listening on random port at (\d+) for HTTP\b", line)
                        if m:
                            port = int(m.group(1))
                            logger.debug(f"Found Antigravity HTTP port {port} in {log_file}")
                            return port
        except Exception as e:
            logger.debug(f"Error parsing log file for port: {e}")

    return None


def _parse_db_row_tokens(raw_b64: str) -> Tuple[Optional[str], Optional[str]]:
    """Extracts (access_token, refresh_token) from raw base64 string."""
    try:
        decoded = base64.b64decode(raw_b64)
    except Exception:
        return None, None

    at, rt = None, None

    # Antigravity IDE protobuf wraps tokens in inner base64 chunks
    for m in re.findall(rb'[A-Za-z0-9+/=]{20,}', decoded):
        try:
            inner = base64.b64decode(m)
            rt_m = re.search(rb'(1//[A-Za-z0-9_\-]+)', inner)
            if rt_m and not rt:
                rt = rt_m.group(1).decode("ascii")
            at_m = re.search(rb'(ya29\.[A-Za-z0-9_\-]+)', inner)
            if at_m and not at:
                at = at_m.group(1).decode("ascii")
        except Exception:
            pass

    if not rt:
        rt_m = re.search(rb'(1//[A-Za-z0-9_\-]+)', decoded)
        if rt_m:
            rt = rt_m.group(1).decode("ascii")
    if not at:
        at_m = re.search(rb'(ya29\.[A-Za-z0-9_\-]+)', decoded)
        if at_m:
            at = at_m.group(1).decode("ascii")

    return at, rt


def _parse_db_row_user_status(raw_b64: str) -> Tuple[str, str]:
    """Extracts (email, name) offline directly from userStatus protobuf payload."""
    try:
        decoded = base64.b64decode(raw_b64)
    except Exception:
        return "", ""

    email, name = "", ""
    for m in re.findall(rb'[A-Za-z0-9+/=]{20,}', decoded):
        try:
            inner = base64.b64decode(m)
            em_m = re.search(rb'([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)', inner)
            if em_m and not email:
                email = em_m.group(1).decode("ascii")
                # Parse protobuf name right before email (\x1a<len><name>:<len><email>)
                name_m = re.search(rb'\x1a([\x01-\x30])(.*?):\x17?' + re.escape(em_m.group(1)), inner)
                if name_m:
                    try:
                        name = name_m.group(2).decode("utf-8").strip()
                    except Exception:
                        pass
        except Exception:
            pass

    return email, name


def extract_antigravity_tokens() -> Tuple[Optional[str], Optional[str]]:
    """
    Safely reads access_token and refresh_token from the freshest Antigravity IDE state.vscdb.
    Returns (access_token, refresh_token).
    """
    for db_path in get_antigravity_state_db_paths():
        if not db_path.exists():
            continue
        try:
            conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
            cur = conn.cursor()
            cur.execute("SELECT value FROM ItemTable WHERE key = 'antigravityUnifiedStateSync.oauthToken'")
            row = cur.fetchone()
            conn.close()

            if not row or not row[0]:
                continue

            at, rt = _parse_db_row_tokens(row[0])
            if at or rt:
                logger.debug(f"Extracted tokens from {db_path} (has_at={bool(at)}, has_rt={bool(rt)})")
                return at, rt
        except Exception as e:
            logger.debug(f"Could not read tokens from {db_path}: {e}")
            continue

    return None, None


def extract_all_antigravity_accounts(proxy_url: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Scans all discovered Antigravity state.vscdb files and returns all found accounts.
    Returns list of dicts: {'email', 'name', 'refresh_token', 'access_token', 'mtime', 'db_path'}
    Deduplicated by email (retaining the record with newest mtime).
    """
    accounts_by_email: Dict[str, Dict[str, Any]] = {}

    for db_path in get_antigravity_state_db_paths():
        if not db_path.exists():
            continue
        try:
            mtime = os.path.getmtime(db_path)
            conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
            cur = conn.cursor()
            cur.execute("SELECT key, value FROM ItemTable WHERE key IN ('antigravityUnifiedStateSync.oauthToken', 'antigravityUnifiedStateSync.userStatus')")
            rows = dict(cur.fetchall())
            conn.close()

            raw_tok = rows.get("antigravityUnifiedStateSync.oauthToken")
            raw_st = rows.get("antigravityUnifiedStateSync.userStatus")

            at, rt = _parse_db_row_tokens(raw_tok) if raw_tok else (None, None)
            email, name = _parse_db_row_user_status(raw_st) if raw_st else ("", "")

            if not rt and not at and not email:
                continue

            # If email is still missing but we have a valid refresh token, try resolving via Google API
            if not email and rt:
                try:
                    from core.oauth import GoogleOAuthClient
                    client = GoogleOAuthClient(proxy_url=proxy_url)
                    try:
                        token_data = client.refresh_access_token(rt)
                    except Exception:
                        client = GoogleOAuthClient(proxy_url=None)
                        token_data = client.refresh_access_token(rt)
                    fresh_at = token_data.get("access_token", "")
                    if fresh_at:
                        at = fresh_at
                        uinfo = client.fetch_user_info(fresh_at)
                        email = uinfo.get("email", "")
                        name = uinfo.get("name", "")
                except Exception:
                    pass

            if email:
                key = email.strip().lower()
                if key not in accounts_by_email or mtime > accounts_by_email[key]["mtime"]:
                    accounts_by_email[key] = {
                        "email": email.strip(),
                        "name": name,
                        "refresh_token": rt or "",
                        "access_token": at or "",
                        "mtime": mtime,
                        "db_path": str(db_path),
                    }
            elif rt:
                # Store unassociated token with pseudo-key
                key = f"token_{rt[:10]}"
                if key not in accounts_by_email:
                    accounts_by_email[key] = {
                        "email": "",
                        "name": "",
                        "refresh_token": rt,
                        "access_token": at or "",
                        "mtime": mtime,
                        "db_path": str(db_path),
                    }
        except Exception as e:
            logger.debug(f"Error reading account from {db_path}: {e}")
            continue

    # Return list sorted by mtime descending
    acc_list = list(accounts_by_email.values())
    acc_list.sort(key=lambda x: x["mtime"], reverse=True)
    return acc_list


def extract_latest_antigravity_account_and_token(proxy_url: Optional[str] = None) -> Optional[Dict[str, str]]:
    """
    Extracts the latest token from Antigravity IDE state database, and verifies the
    exact Google Account email and name.
    Returns dict: {'email': ..., 'name': ..., 'refresh_token': ..., 'access_token': ...}
    """
    accounts = extract_all_antigravity_accounts(proxy_url=proxy_url)
    if not accounts:
        return None

    # Pick the newest record with a refresh token
    for acc in accounts:
        if acc.get("refresh_token"):
            return {
                "email": acc.get("email", ""),
                "name": acc.get("name", ""),
                "refresh_token": acc.get("refresh_token", ""),
                "access_token": acc.get("access_token", ""),
            }

    # Fallback to first record
    first = accounts[0]
    return {
        "email": first.get("email", ""),
        "name": first.get("name", ""),
        "refresh_token": first.get("refresh_token", ""),
        "access_token": first.get("access_token", ""),
    }
