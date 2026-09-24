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
from utils.antigravity_auth import (
    extract_all_antigravity_accounts,
    extract_latest_antigravity_account_and_token,
    get_antigravity_http_port_from_logs,
)
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
        """Queries local Antigravity Language Server RPC for live quota summary with failover."""
        url = f"http://127.0.0.1:{port}/exa.language_server_pb.LanguageServerService/RetrieveUserQuotaSummary"
        # First attempt forceRefresh: True with 4.0s timeout; fallback to forceRefresh: False with 2.0s timeout
        for force_ref in [True, False]:
            body = json.dumps({"forceRefresh": force_ref}).encode("utf-8")
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
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                timeout = 4.0 if force_ref else 2.0
                with opener.open(req, timeout=timeout) as resp:
                    if resp.getcode() == 200:
                        raw = resp.read().decode("utf-8", errors="ignore")
                        data = json.loads(raw)
                        if data and ("groups" in data or "response" in data):
                            return data
            except Exception:
                continue
        return None

    def _query_local_user_status(self, port: int, csrf_token: str) -> Optional[Dict[str, Any]]:
        """Queries local Antigravity Language Server RPC for active user email, name and plan status."""
        url = f"http://127.0.0.1:{port}/exa.language_server_pb.LanguageServerService/GetUserStatus"
        req = urllib.request.Request(
            url,
            data=b"{}",
            headers={
                "Content-Type": "application/json",
                "X-Codeium-Csrf-Token": csrf_token,
                "Connect-Protocol-Version": "1",
            },
        )
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(req, timeout=3.0) as resp:
                if resp.getcode() == 200:
                    raw = resp.read().decode("utf-8", errors="ignore")
                    return json.loads(raw)
        except Exception:
            return None
        return None

    def _fetch_from_antigravity_ls(self) -> Optional[Tuple[Dict[str, Any], Optional[Dict[str, Any]]]]:
        """Automatically discovers running Antigravity IDE and queries its local Language Server."""
        # 1. Try cached connection first (lightning fast, ~20ms)
        if self._cached_ls_port and self._cached_ls_csrf:
            data = self._query_local_ls(self._cached_ls_port, self._cached_ls_csrf)
            if data and ("groups" in data or "response" in data):
                user_data = self._query_local_user_status(self._cached_ls_port, self._cached_ls_csrf)
                return data, user_data
            self._cached_ls_port = None
            self._cached_ls_csrf = None

        # 2. Discover via psutil and log files
        try:
            import psutil
            log_port = get_antigravity_http_port_from_logs()

            for p in psutil.process_iter(["name"]):
                try:
                    name = (p.info["name"] or "").lower()
                    if "language_server" in name:
                        cmdline = " ".join(p.cmdline())
                        csrf_match = re.search(r"--csrf_token\s+([a-zA-Z0-9-]+)", cmdline)
                        if not csrf_match:
                            continue
                        csrf_token = csrf_match.group(1)

                        candidate_ports: List[int] = []
                        if log_port:
                            candidate_ports.append(log_port)

                        # Also inspect listening tcp ports from process if permission allows
                        try:
                            for conn in p.net_connections(kind="tcp"):
                                if conn.status == psutil.CONN_LISTEN:
                                    if conn.laddr.port not in candidate_ports:
                                        candidate_ports.append(conn.laddr.port)
                        except Exception:
                            pass

                        for port in candidate_ports:
                            data = self._query_local_ls(port, csrf_token)
                            if data and ("groups" in data or "response" in data):
                                self._cached_ls_port = port
                                self._cached_ls_csrf = csrf_token
                                user_data = self._query_local_user_status(port, csrf_token)
                                return data, user_data
                except (psutil.NoSuchProcess, psutil.AccessDenied, Exception):
                    continue
        except Exception as e:
            logger.debug(f"Antigravity language server discovery error: {e}")

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
        """Fetches quota snapshot with automatic multi-account detection, account switching, and failover."""
        cfg = self.config_manager.config.gemini

        # Check if simulator mode is active
        if self.config_manager.config.general.active_source == "simulator":
            return self._generate_simulated_snapshot()

        proxy_url = self.config_manager.config.proxy.get_proxy_url()

        # Priority 1: Direct Live Link to local Antigravity IDE Language Server
        try:
            ls_result = self._fetch_from_antigravity_ls()
            if ls_result:
                quota_payload, user_payload = ls_result
                items = self._parse_quota_payload(quota_payload)
                if items:
                    user_email = ""
                    user_name = ""
                    plan_name = ""
                    if user_payload and isinstance(user_payload, dict):
                        us = user_payload.get("userStatus", {}) or user_payload.get("user_status", {})
                        if isinstance(us, dict):
                            user_email = str(us.get("email") or us.get("user_email") or us.get("emailAddress") or us.get("username") or "").strip()
                            user_name = str(us.get("name") or us.get("user_name") or us.get("displayName") or "").strip()
                            plan_info = us.get("userTier") or us.get("planStatus", {}).get("planInfo", {})
                            if isinstance(plan_info, dict):
                                plan_name = str(plan_info.get("name") or plan_info.get("planName") or "").strip()

                    latest_acc = extract_latest_antigravity_account_and_token(proxy_url=proxy_url)
                    if not user_email and latest_acc:
                        user_email = latest_acc.get("email", "")
                        user_name = user_name or latest_acc.get("name", "")

                    discovered_accounts = extract_all_antigravity_accounts(proxy_url=proxy_url)
                    active_rt = ""
                    for d_acc in discovered_accounts:
                        if user_email and d_acc.get("email", "").lower() == user_email.lower():
                            active_rt = d_acc.get("refresh_token", "")
                            break
                    if not active_rt and latest_acc:
                        active_rt = latest_acc.get("refresh_token", "")

                    active_email = user_email or cfg.account_email or "Antigravity Active User"

                    # Sync config if account switched or refresh token discovered
                    if user_email:
                        changed = False
                        if cfg.account_email != user_email:
                            logger.info(f"Detected Antigravity account switch: '{cfg.account_email}' -> '{user_email}'")
                            cfg.account_email = user_email
                            changed = True
                        if active_rt and (cfg.refresh_token != active_rt or not cfg.refresh_token):
                            cfg.refresh_token = active_rt
                            changed = True
                        if changed:
                            self.config_manager.save()

                    # Automatically update multi-account manager
                    try:
                        from core.account_manager import get_account_manager
                        acc_mgr = get_account_manager()
                        acc_mgr.load()

                        item_5h = next((it for it in items if "5h" in it.id.lower() and "claude" not in it.id.lower()), None)
                        item_week = next((it for it in items if ("weekly" in it.id.lower() or "week" in it.id.lower()) and "claude" not in it.id.lower()), None)
                        item_c5h = next((it for it in items if "claude" in it.id.lower() and "5h" in it.id.lower()), None)
                        item_cweek = next((it for it in items if "claude" in it.id.lower() and ("weekly" in it.id.lower() or "week" in it.id.lower())), None)

                        if user_email:
                            acc_mgr.update_or_add_account(
                                email=user_email,
                                name=user_name,
                                plan_name=plan_name,
                                gemini_5h_remaining=item_5h.remaining if item_5h else 100.0,
                                gemini_5h_reset_time=item_5h.reset_time if item_5h else None,
                                gemini_weekly_remaining=item_week.remaining if item_week else 100.0,
                                gemini_weekly_reset_time=item_week.reset_time if item_week else None,
                                claude_5h_remaining=item_c5h.remaining if item_c5h else 100.0,
                                claude_5h_reset_time=item_c5h.reset_time if item_c5h else None,
                                claude_weekly_remaining=item_cweek.remaining if item_cweek else 100.0,
                                claude_weekly_reset_time=item_cweek.reset_time if item_cweek else None,
                                is_active=True,
                                refresh_token=active_rt or "",
                                quota_source="api",
                            )

                        # Register any other discovered offline accounts into the pool
                        for disc in discovered_accounts:
                            d_em = disc.get("email", "").strip()
                            d_tok = disc.get("refresh_token", "").strip()
                            if d_em and d_em.lower() != user_email.lower() and d_tok:
                                if d_em.lower() not in acc_mgr.accounts:
                                    acc_mgr.update_or_add_account(
                                        email=d_em,
                                        name=disc.get("name", ""),
                                        is_active=False,
                                        refresh_token=d_tok,
                                        quota_source="estimated",
                                    )
                    except Exception as acc_err:
                        logger.debug(f"AccountManager sync error: {acc_err}")

                    logger.info(f"Successfully fetched {len(items)} live quota items directly from local Antigravity IDE (User: {active_email})")
                    return QuotaSnapshot(
                        source_name="Google Gemini (Antigravity)",
                        account_label=active_email,
                        user_name=user_name,
                        plan_name=plan_name,
                        project_id="Antigravity Live",
                        items=items,
                        status="ok",
                    )
        except Exception as e:
            logger.debug(f"Local Antigravity fetch attempt failed: {e}")

        # Priority 2: Remote Google API fallback
        logger.info("Local Antigravity LS not available, running remote API fallback...")

        latest_acc = extract_latest_antigravity_account_and_token(proxy_url=proxy_url)
        target_rt = ""
        target_email = ""
        target_name = ""

        if latest_acc and latest_acc.get("refresh_token"):
            target_rt = latest_acc["refresh_token"]
            target_email = latest_acc.get("email", "")
            target_name = latest_acc.get("name", "")

        if not target_rt:
            target_rt = cfg.refresh_token.strip()
            target_email = cfg.account_email.strip()

        if not target_rt:
            return QuotaSnapshot(
                source_name="Google Gemini",
                account_label=cfg.account_email or "未登录",
                status="unauthorized",
                error_message="未检测到 Antigravity IDE 登录状态或 Google 账号，请在 IDE 中登录",
            )

        # Sync config if account switched
        if target_email and cfg.account_email != target_email:
            logger.info(f"Detected account switch in state.vscdb: '{cfg.account_email}' -> '{target_email}'")
            cfg.account_email = target_email
            cfg.refresh_token = target_rt
            self.config_manager.save()
        elif target_rt and not cfg.refresh_token:
            cfg.refresh_token = target_rt
            self.config_manager.save()

        items = self.fetch_quota_via_refresh_token(target_rt)
        if not items:
            return QuotaSnapshot(
                source_name="Google Gemini",
                account_label=cfg.account_email or target_email or "Google Account",
                status="error",
                error_message="请求额度接口失败或连接超时",
            )

        acc_email = target_email or cfg.account_email or "Google Account"

        # Register / Update active account in AccountManager
        try:
            from core.account_manager import get_account_manager
            acc_mgr = get_account_manager()
            acc_mgr.load()

            item_5h = next((it for it in items if "5h" in it.id.lower() and "claude" not in it.id.lower()), None)
            item_week = next((it for it in items if ("weekly" in it.id.lower() or "week" in it.id.lower()) and "claude" not in it.id.lower()), None)
            item_c5h = next((it for it in items if "claude" in it.id.lower() and "5h" in it.id.lower()), None)
            item_cweek = next((it for it in items if "claude" in it.id.lower() and ("weekly" in it.id.lower() or "week" in it.id.lower())), None)

            acc_mgr.update_or_add_account(
                email=acc_email,
                name=target_name,
                is_active=True,
                refresh_token=target_rt,
                gemini_5h_remaining=item_5h.remaining if item_5h else 100.0,
                gemini_5h_reset_time=item_5h.reset_time if item_5h else None,
                gemini_weekly_remaining=item_week.remaining if item_week else 100.0,
                gemini_weekly_reset_time=item_week.reset_time if item_week else None,
                claude_5h_remaining=item_c5h.remaining if item_c5h else 100.0,
                claude_5h_reset_time=item_c5h.reset_time if item_c5h else None,
                claude_weekly_remaining=item_cweek.remaining if item_cweek else 100.0,
                claude_weekly_reset_time=item_cweek.reset_time if item_cweek else None,
                quota_source="api",
            )

            for disc in extract_all_antigravity_accounts(proxy_url=proxy_url):
                d_em = disc.get("email", "").strip()
                d_tok = disc.get("refresh_token", "").strip()
                if d_em and d_em.lower() != acc_email.lower() and d_tok:
                    if d_em.lower() not in acc_mgr.accounts:
                        acc_mgr.update_or_add_account(
                            email=d_em,
                            name=disc.get("name", ""),
                            is_active=False,
                            refresh_token=d_tok,
                            quota_source="estimated",
                        )
        except Exception as acc_err:
            logger.debug(f"AccountManager sync error in fallback: {acc_err}")

        return QuotaSnapshot(
            source_name="Google Gemini",
            account_label=acc_email,
            project_id=DEFAULT_PROJECT_ID,
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

    def fetch_quota_via_refresh_token(self, refresh_token: str) -> Optional[List[QuotaItem]]:
        """
        Fetches live quota snapshot from Google CloudCode-PA APIs using a refresh_token.
        Handles proxy fallbacks smoothly.
        """
        if not refresh_token or not refresh_token.strip():
            return None

        # 1. Exchange refresh_token for access_token with proxy fallback
        token_data = None
        try:
            token_data = self.oauth_client.refresh_access_token(refresh_token)
        except Exception as e:
            logger.debug(f"Refresh with proxy failed ({e}), attempting direct connection...")
            try:
                direct_client = GoogleOAuthClient(proxy_url=None)
                token_data = direct_client.refresh_access_token(refresh_token)
            except Exception as direct_err:
                logger.warning(f"Failed to refresh access token for offline account: {direct_err}")
                return None

        access_token = token_data.get("access_token")
        if not access_token:
            return None

        # 2. Build session
        session = self._build_session()
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "User-Agent": ANTIGRAVITY_USER_AGENT,
        }

        # 3. Resolve project ID
        project_id = DEFAULT_PROJECT_ID
        for endpoint in CODE_ASSIST_ENDPOINTS:
            try:
                resp = session.post(endpoint, json={"metadata": {"ideType": "ANTIGRAVITY"}}, headers=headers, timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    for key in ["cloudaicompanionProject", "projectId", "project"]:
                        val = data.get(key)
                        if isinstance(val, str) and val.strip():
                            project_id = val.strip()
                            break
                    if project_id != DEFAULT_PROJECT_ID:
                        break
            except Exception:
                pass

        # 4. Fetch User Quota Summary
        body = {"project": project_id}
        payload = None
        for endpoint in QUOTA_SUMMARY_ENDPOINTS:
            try:
                resp = session.post(endpoint, json=body, headers=headers, timeout=12)
                if resp.status_code == 200:
                    payload = resp.json()
                    break
            except Exception:
                # If proxy connection failed, retry direct
                try:
                    if HAS_CURL_CFFI:
                        direct_session = cffi_requests.Session(impersonate="chrome124")
                    else:
                        direct_session = cffi_requests.Session()
                    resp = direct_session.post(endpoint, json=body, headers=headers, timeout=12)
                    if resp.status_code == 200:
                        payload = resp.json()
                        break
                except Exception:
                    pass

        if not payload:
            return None

        return self._parse_quota_payload(payload)

    def refresh_offline_account(self, email: str, refresh_token: str) -> bool:
        """
        Refreshes a specific offline account via Google API and updates AccountManager.
        """
        items = self.fetch_quota_via_refresh_token(refresh_token)
        if items:
            from core.account_manager import get_account_manager
            acc_mgr = get_account_manager()
            acc_mgr.update_quota_from_api_items(email=email, items=items)
            logger.info(f"Successfully refreshed real API quota for offline account: {email}")
            return True
        return False

