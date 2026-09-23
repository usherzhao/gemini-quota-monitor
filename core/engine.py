"""
Gemini Quota Monitor - Quota Engine
Background coordinator for periodic polling, caching, and broadcasting Gemini quota updates.
Supports both active IDE local query and offline accounts remote API refreshing.
"""

from datetime import datetime
from typing import Optional
from PyQt6.QtCore import QObject, QThread, QTimer, pyqtSignal

from config import AppConfig, ConfigManager, get_config_manager
from core.fetchers.gemini import GeminiQuotaFetcher
from core.models import QuotaSnapshot
from utils.logger import logger


class FetchWorker(QThread):
    """Worker thread to execute network fetch asynchronously without freezing UI."""
    finished = pyqtSignal(QuotaSnapshot)

    def __init__(self, fetcher: GeminiQuotaFetcher):
        super().__init__()
        self.fetcher = fetcher

    def run(self):
        logger.debug("FetchWorker thread started for GeminiQuotaFetcher")
        try:
            snapshot = self.fetcher.fetch()
        except Exception as e:
            logger.error(f"Unhandled exception in FetchWorker: {e}", exc_info=True)
            snapshot = QuotaSnapshot(
                source_name="Google Gemini",
                account_label="异常",
                items=[],
                status="error",
                error_message=str(e),
            )
        logger.debug(f"FetchWorker finished with status='{snapshot.status}', items={len(snapshot.items)}")
        self.finished.emit(snapshot)


class AccountsRefreshWorker(QThread):
    """Worker thread to query Google remote quota APIs for offline accounts."""
    finished = pyqtSignal(bool, int)  # (success, updated_count)

    def __init__(self, fetcher: GeminiQuotaFetcher, target_email: Optional[str] = None):
        super().__init__()
        self.fetcher = fetcher
        self.target_email = target_email

    def run(self):
        from core.account_manager import get_account_manager
        acc_mgr = get_account_manager()
        acc_mgr.load()

        if self.target_email:
            targets = [acc for acc in acc_mgr.accounts.values() if acc.email.lower() == self.target_email.lower()]
        else:
            targets = [acc for acc in acc_mgr.accounts.values() if not acc.is_active and acc.has_api_token]

        updated = 0
        for acc in targets:
            if not acc.refresh_token:
                continue
            try:
                ok = self.fetcher.refresh_offline_account(acc.email, acc.refresh_token)
                if ok:
                    updated += 1
            except Exception as e:
                logger.warning(f"Failed to refresh offline account {acc.email}: {e}")

        self.finished.emit(updated > 0, updated)


class QuotaEngine(QObject):
    """Central engine managing fetchers, periodic polling, and state broadcasting."""

    quota_updated = pyqtSignal(QuotaSnapshot)
    fetch_started = pyqtSignal()
    low_quota_alert = pyqtSignal(str, float)
    accounts_refreshed = pyqtSignal(bool, int)
    accounts_refresh_started = pyqtSignal()

    def __init__(self, config_manager: Optional[ConfigManager] = None):
        super().__init__()
        self.config_manager = config_manager or get_config_manager()
        self.current_snapshot: Optional[QuotaSnapshot] = None
        self.fetcher: GeminiQuotaFetcher = GeminiQuotaFetcher(self.config_manager)
        self._worker: Optional[FetchWorker] = None
        self._acc_worker: Optional[AccountsRefreshWorker] = None

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh_now)

        # Background timer for offline accounts refresh (every 5 minutes)
        self._acc_timer = QTimer(self)
        self._acc_timer.setInterval(300000)  # 5 minutes
        self._acc_timer.timeout.connect(lambda: self.refresh_offline_accounts())

        self._update_timer_interval()

    def _update_timer_interval(self):
        interval_ms = max(10, self.config_manager.config.general.refresh_interval_sec) * 1000
        self._timer.setInterval(interval_ms)

    def start(self):
        """Starts the engine and initiates first fetch."""
        logger.info("Starting QuotaEngine background timer...")
        self._timer.start()
        self._acc_timer.start()
        self.refresh_now()

    def stop(self):
        """Stops the periodic timer."""
        logger.info("Stopping QuotaEngine...")
        self._timer.stop()
        self._acc_timer.stop()

    def reload_config(self):
        """Re-reads config and updates fetcher and timers."""
        logger.info("Reloading QuotaEngine configuration...")
        self.fetcher = GeminiQuotaFetcher(self.config_manager)
        self._update_timer_interval()
        self.refresh_now()

    def refresh_now(self):
        """Triggers immediate asynchronous quota refresh."""
        if self._worker and self._worker.isRunning():
            logger.debug("Fetch is already running, skipping duplicate refresh.")
            return

        logger.info("Triggering Gemini quota refresh...")
        self.fetch_started.emit()
        self._worker = FetchWorker(self.fetcher)
        self._worker.finished.connect(self._on_fetch_completed)
        self._worker.start()

    def refresh_offline_accounts(self, target_email: Optional[str] = None):
        """Triggers background live API refresh for offline accounts with refresh_token."""
        if self._acc_worker and self._acc_worker.isRunning():
            logger.debug("Offline accounts refresh already in progress.")
            return

        logger.info(f"Triggering offline accounts refresh (target={target_email or 'all'})...")
        self.accounts_refresh_started.emit()
        self._acc_worker = AccountsRefreshWorker(self.fetcher, target_email)
        self._acc_worker.finished.connect(self._on_accounts_refreshed)
        self._acc_worker.start()

    def _on_accounts_refreshed(self, success: bool, count: int):
        logger.info(f"Offline accounts refresh finished (success={success}, count={count})")
        self.accounts_refreshed.emit(success, count)

    def _on_fetch_completed(self, snapshot: QuotaSnapshot):
        self.current_snapshot = snapshot
        self.quota_updated.emit(snapshot)

        cfg = self.config_manager.config.general
        if cfg.notify_low_quota and snapshot.is_healthy:
            threshold = cfg.low_quota_threshold
            for item in snapshot.items:
                if item.percentage <= threshold:
                    logger.warning(f"Low quota alert: {item.name} is at {item.percentage:.0f}%")
                    self.low_quota_alert.emit(item.name, item.percentage)
                    break
