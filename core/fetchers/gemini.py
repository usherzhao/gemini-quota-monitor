"""
Gemini Quota Monitor - Gemini Quota Fetcher
Queries Google CloudCode-PA APIs to resolve project ID and retrieve 5-hour rolling window and weekly quota.
"""

from datetime import datetime, timedelta
import json
import re
from typing import Any, Dict, List, Optional, Tuple
import urllib.request

from config import ConfigManager
from core.models import QuotaItem, QuotaSnapshot
from core.oauth import GoogleOAuthClient
from utils.logger import logger

try:
    from curl_cffi import requests as cffi_requests
    HAS_CURL_CFFI = True
except ImportError:
    HAS_CURL_CFFI = False
    import requests as cffi_requests

ANTIGRAVITY_USER_AGENT = "antigravity/cli/1.0.13 (aidev_client; os_type=windows; arch=amd64)"
DEFAULT_PROJECT_ID = "bamboo-precept-lgxtn"

QUOTA_SUMMARY_ENDPOINTS = [
    "https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary",
    "https://daily-cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary",
    "https://daily-cloudcode-pa.sandbox.googleapis.com/v1internal:retrieveUserQuotaSummary",
]

CODE_ASSIST_ENDPOINTS = [
    "https://cloudcode-pa.googleapis.com/v1internal:loadCodeAssist",
    "https://daily-cloudcode-pa.googleapis.com/v1internal:loadCodeAssist",
]


def parse_iso_datetime(ts: str) -> Optional[datetime]:
    """Parses ISO 8601 string to local datetime."""
    if not ts or not isinstance(ts, str):
        return None
    try:
        clean = ts.strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean)
        return dt.astimezone()
    except Exception:
        return None


class GeminiQuotaFetcher:
    """Fetches and parses Gemini 5-hour and weekly quotas from Google APIs."""

    def __init__(self, config_manager: ConfigManager):
        self.config_manager = config_manager
        self.oauth_client = GoogleOAuthClient(
            proxy_url=self.config_manager.config.proxy.get_proxy_url()
        )
        self._cached_ls_port: Optional[int] = None
        self._cached_ls_csrf: Optional[str] = None

    def _query_local_ls(self, port: int, csrf_token: str) -> Optional[Dict[str, Any]]:
        """Queries local Antigravity Language Server RPC for live quota summary."""
        url = f"http://127.0.0.1:{port}/exa.language_server_pb.LanguageServerService/RetrieveUserQuotaSummary"
        body = json.dumps({"forceRefresh": True}).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-Codeium-Csrf-Token": csrf_token,
                "Connect-Protocol-Version": "1",
            },
        )
        try:
            # Bypass system/SOCKS proxies to directly hit localhost RPC
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(req, timeout=1.5) as resp:
                if resp.getcode() == 200:
                    raw = resp.read().decode("utf-8", errors="ignore")
                    return json.loads(raw)
        except Exception:
            return None
        return None

    def _fetch_from_antigravity_ls(self) -> Optional[Dict[str, Any]]:
        """Automatically discovers running Antigravity IDE and queries its local Language Server."""
        # 1. Try cached connection first (lightning fast, ~20ms)
        if self._cached_ls_port and self._cached_ls_csrf:
            data = self._query_local_ls(self._cached_ls_port, self._cached_ls_csrf)
            if data and ("groups" in data or "response" in data):
                return data
            self._cached_ls_port = None
            self._cached_ls_csrf = None

        # 2. Discover via psutil
        try:
            import psutil
            for p in psutil.process_iter(["name"]):
                try:
                    name = (p.info["name"] or "").lower()
                    if "language_server" in name:
                        cmdline = " ".join(p.cmdline())
                        csrf_match = re.search(r"--csrf_token\s+([a-zA-Z0-9-]+)", cmdline)
                        if not csrf_match:
                            continue
                        csrf_token = csrf_match.group(1)

                        # Check listening ports
                        for conn in p.net_connections(kind="tcp"):
                            if conn.status == psutil.CONN_LISTEN:
                                port = conn.laddr.port
                                data = self._query_local_ls(port, csrf_token)
                                if data and ("groups" in data or "response" in data):
                                    self._cached_ls_port = port
                                    self._cached_ls_csrf = csrf_token
                                    return data
                except (psutil.NoSuchProcess, psutil.AccessDenied, Exception):
                    continue
        except Exception as e:
            logger.debug(f"psutil discovery error: {e}")

        return None

    def _build_session(self):
        proxy_url = self.config_manager.config.proxy.get_proxy_url()
        if HAS_CURL_CFFI:
            session = cffi_requests.Session(impersonate="chrome124")
        else:
            session = cffi_requests.Session()

        if proxy_url:
            session.proxies = {
                "http": proxy_url,
                "https": proxy_url,
            }
        return session

    def _ensure_valid_token(self) -> str:
        """Returns active access_token, refreshing it if expired or nearing expiry."""
        cfg = self.config_manager.config.gemini
        access_token = cfg.access_token.strip()
        refresh_token = cfg.refresh_token.strip()

        if not access_token and not refresh_token:
            raise RuntimeError("未登录 Google 账号，请在设置中点击登录")

        # Check expiry
        need_refresh = False
        if cfg.token_expiry:
            try:
                expiry = datetime.fromisoformat(cfg.token_expiry)
                # If expires in less than 60 seconds, refresh now
                now = datetime.now(expiry.tzinfo) if expiry.tzinfo else datetime.now()
                if (expiry - now).total_seconds() < 60:
                    need_refresh = True
            except Exception:
                need_refresh = False

        if (not access_token or need_refresh) and refresh_token:
            logger.info("Access token expired or missing, refreshing via refresh_token...")
            token_data = self.oauth_client.refresh_access_token(refresh_token)
            new_access_token = token_data.get("access_token")
            expires_in = token_data.get("expires_in", 3600)

            if new_access_token:
                cfg.access_token = new_access_token
                cfg.token_expiry = (datetime.now() + timedelta(seconds=expires_in)).isoformat()
                if "refresh_token" in token_data:
                    cfg.refresh_token = token_data["refresh_token"]
                self.config_manager.save()
                logger.info("Access token refreshed successfully")
                return new_access_token

        return access_token

    def _resolve_project_id(self, session, access_token: str) -> str:
        """Resolves project ID via loadCodeAssist if not already saved."""
        cfg = self.config_manager.config.gemini
        if cfg.project_id.strip():
            return cfg.project_id.strip()

        logger.info("Resolving Google Cloud Code project ID...")
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "User-Agent": ANTIGRAVITY_USER_AGENT,
        }
        body = {
            "metadata": {
                "ideType": "ANTIGRAVITY"
            }
        }

        for endpoint in CODE_ASSIST_ENDPOINTS:
            try:
                resp = session.post(endpoint, json=body, headers=headers, timeout=12)
                if resp.status_code == 200:
                    data = resp.json()
                    project_id = ""
                    for key in ["cloudaicompanionProject", "projectId", "project"]:
                        val = data.get(key)
                        if isinstance(val, str) and val.strip():
                            project_id = val.strip()
                            break
                        elif isinstance(val, dict) and val.get("id"):
                            project_id = str(val.get("id")).strip()
                            break

                    if project_id:
                        cfg.project_id = project_id
                        self.config_manager.save()
                        logger.info(f"Resolved project ID: {project_id}")
                        return project_id
            except Exception as e:
                logger.warning(f"Error checking {endpoint}: {e}")

        logger.info(f"Using default project ID fallback: {DEFAULT_PROJECT_ID}")
        cfg.project_id = DEFAULT_PROJECT_ID
        self.config_manager.save()
        return DEFAULT_PROJECT_ID

    def fetch(self) -> QuotaSnapshot:
        """Fetches quota snapshot from Google CloudCode-PA API."""
        cfg = self.config_manager.config.gemini
        account_email = cfg.account_email or "Google Account"

        # Check if simulator mode is active
        if self.config_manager.config.general.active_source == "simulator":
            return self._generate_simulated_snapshot()

        # Priority 1: Direct Live Link to local Antigravity IDE
        # If Antigravity IDE is running, this gives the exact 100% synchronized live quota & countdowns with zero proxy/latency.
        try:
            local_payload = self._fetch_from_antigravity_ls()
            if local_payload:
                items = self._parse_quota_payload(local_payload)
                if items:
                    logger.info(f"Successfully fetched {len(items)} live quota items directly from local Antigravity IDE")
                    return QuotaSnapshot(
                        source_name="Google Gemini (Antigravity)",
                        account_label=account_email or "Antigravity Active User",
                        project_id="Antigravity Live",
                        items=items,
                        status="ok",
                    )
        except Exception as e:
            logger.debug(f"Local Antigravity fetch attempt failed: {e}")

        try:
            access_token = self._ensure_valid_token()
        except Exception as e:
            return QuotaSnapshot(
                source_name="Google Gemini",
                account_label=account_email,
                status="unauthorized",
                error_message=str(e),
            )

        session = self._build_session()
        project_id = self._resolve_project_id(session, access_token)

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "User-Agent": ANTIGRAVITY_USER_AGENT,
        }
        body = {"project": project_id}

        last_error = ""
        payload = None

        for endpoint in QUOTA_SUMMARY_ENDPOINTS:
            try:
                resp = session.post(endpoint, json=body, headers=headers, timeout=15)
                if resp.status_code == 401:
                    # Token might have been revoked; attempt one force refresh
                    logger.warning("Got 401, trying to force refresh access token...")
                    if cfg.refresh_token:
                        token_data = self.oauth_client.refresh_access_token(cfg.refresh_token)
                        access_token = token_data.get("access_token", "")
                        if access_token:
                            cfg.access_token = access_token
                            cfg.token_expiry = (datetime.now() + timedelta(seconds=token_data.get("expires_in", 3600))).isoformat()
                            self.config_manager.save()
                            headers["Authorization"] = f"Bearer {access_token}"
                            resp = session.post(endpoint, json=body, headers=headers, timeout=15)

                if resp.status_code == 200:
                    payload = resp.json()
                    break
                else:
                    last_error = f"HTTP {resp.status_code}: {resp.text[:120]}"
            except Exception as e:
                last_error = str(e)
                logger.warning(f"Error querying {endpoint}: {e}")

        if not payload:
            return QuotaSnapshot(
                source_name="Google Gemini",
                account_label=account_email,
                project_id=project_id,
                status="error",
                error_message=last_error or "请求额度接口失败",
            )

        items = self._parse_quota_payload(payload)

        return QuotaSnapshot(
            source_name="Google Gemini",
            account_label=account_email,
            project_id=project_id,
            items=items,
            status="ok",
        )

    def _parse_quota_payload(self, payload: Dict[str, Any]) -> List[QuotaItem]:
        """Parses API response payload into QuotaItem instances for Gemini and Claude/GPT."""
        gemini_items: List[QuotaItem] = []
        claude_items: List[QuotaItem] = []
        other_items: List[QuotaItem] = []

        # 1. Parse from 'groups' (standard Antigravity quota payload structure)
        groups = payload.get("groups") or payload.get("response", {}).get("groups", [])
        if isinstance(groups, list):
            for group in groups:
                group_title = str(group.get("displayName") or group.get("display_name") or "")
                group_desc = str(group.get("description") or "")
                is_gemini_group = "gemini" in group_title.lower()
                is_claude_group = "claude" in group_title.lower() or "gpt" in group_title.lower()

                buckets = group.get("buckets", [])
                for b in buckets:
                    bucket_id = str(b.get("bucketId") or b.get("bucket_id") or "")
                    window = str(b.get("window") or "").lower()
                    display_name = str(b.get("displayName") or b.get("display_name") or "")

                    rem_frac = b.get("remainingFraction")
                    if rem_frac is None:
                        rem_frac = b.get("remaining_fraction")
                    try:
                        rem_pct = float(rem_frac) * 100.0 if rem_frac is not None else 100.0
                    except Exception:
                        rem_pct = 100.0

                    reset_time_str = b.get("resetTime") or b.get("reset_time") or ""
                    reset_dt = parse_iso_datetime(reset_time_str)

                    is_5h = "5h" in window or "5h" in bucket_id.lower() or "five" in window or "five" in display_name.lower()
                    is_week = "week" in window or "week" in bucket_id.lower()

                    if is_gemini_group or "gemini" in bucket_id.lower():
                        if is_5h:
                            item_id = "gemini_5h"
                            name = "Gemini 5小时额度"
                            desc = group_desc or "Gemini Flash, Gemini Pro 共享 5 小时滚动配额"
                        elif is_week:
                            item_id = "gemini_weekly"
                            name = "Gemini 周总额度"
                            desc = group_desc or "Gemini 每周周期总额度"
                        else:
                            item_id = f"gemini_{bucket_id}"
                            name = f"Gemini {display_name}"
                            desc = group_desc

                        gemini_items.append(QuotaItem(
                            id=item_id,
                            name=name,
                            remaining=rem_pct,
                            total=100.0,
                            unit="%",
                            reset_time=reset_dt,
                            description=desc,
                        ))
                    elif is_claude_group or "3p" in bucket_id.lower():
                        if is_5h:
                            item_id = "claude_5h"
                            name = "Claude/GPT 5小时额度"
                            desc = group_desc or "Claude Opus, Sonnet, GPT 共享 5 小时滚动配额"
                        elif is_week:
                            item_id = "claude_weekly"
                            name = "Claude/GPT 周总额度"
                            desc = group_desc or "Claude / GPT 每周周期总额度"
                        else:
                            item_id = f"claude_{bucket_id}"
                            name = f"Claude/GPT {display_name}"
                            desc = group_desc

                        claude_items.append(QuotaItem(
                            id=item_id,
                            name=name,
                            remaining=rem_pct,
                            total=100.0,
                            unit="%",
                            reset_time=reset_dt,
                            description=desc,
                        ))
                    else:
                        other_items.append(QuotaItem(
                            id=bucket_id or f"item_{len(other_items)}",
                            name=display_name or "配额",
                            remaining=rem_pct,
                            total=100.0,
                            unit="%",
                            reset_time=reset_dt,
                            description=group_desc,
                        ))

        # 2. Parse from 'models' if groups didn't provide any items
        if not gemini_items and not claude_items:
            models = payload.get("models", {})
            if isinstance(models, dict):
                gemini_models: List[Tuple[str, float, Optional[datetime]]] = []
                for mid, minfo in models.items():
                    if not isinstance(minfo, dict):
                        continue
                    dname = str(minfo.get("displayName", "")).lower()
                    mid_lower = str(mid).lower()
                    api_provider = str(minfo.get("apiProvider", "")).lower()

                    if "gemini" in dname or "gemini" in mid_lower or "google" in api_provider:
                        qinfo = minfo.get("quotaInfo") or minfo.get("quota_info") or {}
                        rem_val = qinfo.get("remainingFraction") or qinfo.get("remaining_fraction")
                        if rem_val is not None:
                            try:
                                pct = float(rem_val) * 100.0
                            except Exception:
                                pct = 100.0
                            rdt = parse_iso_datetime(qinfo.get("resetTime") or qinfo.get("reset_time") or "")
                            gemini_models.append((mid, pct, rdt))

                if gemini_models:
                    min_mid, min_pct, min_rdt = min(gemini_models, key=lambda x: x[1])
                    gemini_items.append(QuotaItem(
                        id="gemini_5h",
                        name="Gemini 5小时额度",
                        remaining=min_pct,
                        total=100.0,
                        unit="%",
                        reset_time=min_rdt,
                        description="Gemini 实时可用额度",
                    ))
                    gemini_items.append(QuotaItem(
                        id="gemini_weekly",
                        name="Gemini 周总额度",
                        remaining=min_pct,
                        total=100.0,
                        unit="%",
                        reset_time=min_rdt,
                        description="Gemini 周配额",
                    ))

        # Sort so 5h is first, then weekly
        def _sort_key(it: QuotaItem):
            if "5h" in it.id:
                return 0
            if "week" in it.id:
                return 1
            return 2

        gemini_items.sort(key=_sort_key)
        claude_items.sort(key=_sort_key)

        return gemini_items + claude_items + other_items

    def _generate_simulated_snapshot(self) -> QuotaSnapshot:
        """Generates realistic mock data for testing UI and development."""
        sim = self.config_manager.config.simulator
        reset_5h = datetime.now() + timedelta(hours=2, minutes=45)
        reset_weekly = datetime.now() + timedelta(days=5, hours=14)

        if sim.profile == "warning":
            rem_5h = 32.0
            rem_week = 25.0
        elif sim.profile == "depleted":
            rem_5h = 0.0
            rem_week = 5.0
        else:
            rem_5h = sim.remaining_5h
            rem_week = sim.remaining_weekly

        item_5h = QuotaItem(
            id="gemini_5h",
            name="5小时额度",
            remaining=rem_5h,
            total=100.0,
            unit="%",
            reset_time=reset_5h,
            description="Gemini 5小时滚动用量",
        )
        item_weekly = QuotaItem(
            id="gemini_weekly",
            name="周总额度",
            remaining=rem_week,
            total=100.0,
            unit="%",
            reset_time=reset_weekly,
            description="Gemini 每周周期总额度",
        )

        return QuotaSnapshot(
            source_name="Google Gemini (模拟模式)",
            account_label="developer@gmail.com",
            project_id="bamboo-precept-mock",
            items=[item_5h, item_weekly],
            status="simulated",
        )
