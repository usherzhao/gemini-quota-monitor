"""
Unit tests for AccountManager and historical quota calculation.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from core.account_manager import AccountManager, AccountQuotaRecord, format_countdown_from_dt


class TestAccountManager(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_accounts.json"
        self.mgr = AccountManager(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_add_and_switch_accounts(self):
        now = datetime.now(timezone.utc)
        # 1. Register Account A as active
        rec_a = self.mgr.update_or_add_account(
            email="accountA@gmail.com",
            name="Alice",
            plan_name="Google AI Pro",
            gemini_5h_remaining=15.0,
            gemini_5h_reset_time=now + timedelta(hours=2),
            gemini_weekly_remaining=40.0,
            gemini_weekly_reset_time=now + timedelta(days=3),
            is_active=True,
        )
        self.assertTrue(rec_a.is_active)
        self.assertEqual(len(self.mgr.accounts), 1)

        # 2. Register Account B as active -> Account A becomes inactive
        rec_b = self.mgr.update_or_add_account(
            email="accountB@gmail.com",
            name="Bob",
            plan_name="Google AI Pro",
            gemini_5h_remaining=90.0,
            gemini_5h_reset_time=now + timedelta(hours=4),
            gemini_weekly_remaining=85.0,
            gemini_weekly_reset_time=now + timedelta(days=6),
            is_active=True,
        )
        self.assertTrue(rec_b.is_active)
        self.assertFalse(self.mgr.accounts["accounta@gmail.com"].is_active)
        self.assertEqual(len(self.mgr.accounts), 2)

    def test_5h_recovery_time_calculation(self):
        now = datetime.now(timezone.utc)

        # Account with reset time in the PAST (recovered to 100%)
        rec_recovered = AccountQuotaRecord(
            email="past@gmail.com",
            gemini_5h_remaining=10.0,
            gemini_5h_reset_time=(now - timedelta(minutes=15)).isoformat(),
            gemini_weekly_remaining=50.0,
            gemini_weekly_reset_time=(now + timedelta(days=2)).isoformat(),
            is_active=False,
        )
        status_past = rec_recovered.get_5h_status()
        self.assertEqual(status_past["percentage"], 100.0)
        self.assertTrue(status_past["is_ready"])
        self.assertEqual(rec_recovered.recommendation_level, "ready")

        # Account with reset time in the FUTURE (still cooling down)
        rec_cooling = AccountQuotaRecord(
            email="cooling@gmail.com",
            gemini_5h_remaining=10.0,
            gemini_5h_reset_time=(now + timedelta(hours=1, minutes=30)).isoformat(),
            gemini_weekly_remaining=50.0,
            gemini_weekly_reset_time=(now + timedelta(days=2)).isoformat(),
            is_active=False,
        )
        status_cooling = rec_cooling.get_5h_status()
        self.assertEqual(status_cooling["percentage"], 10.0)
        self.assertFalse(status_cooling["is_ready"])
        self.assertIn("小时", status_cooling["countdown"])
        self.assertEqual(rec_cooling.recommendation_level, "cooling")

    def test_list_accounts_ordering(self):
        now = datetime.now(timezone.utc)
        # Add cooling account
        self.mgr.update_or_add_account(
            email="cooling@gmail.com",
            gemini_5h_remaining=10.0,
            gemini_5h_reset_time=now + timedelta(hours=3),
            is_active=False,
        )
        # Add ready account
        self.mgr.update_or_add_account(
            email="ready@gmail.com",
            gemini_5h_remaining=10.0,
            gemini_5h_reset_time=now - timedelta(hours=1),
            is_active=False,
        )
        # Add active account
        self.mgr.update_or_add_account(
            email="active@gmail.com",
            gemini_5h_remaining=50.0,
            gemini_5h_reset_time=now + timedelta(hours=2),
            is_active=True,
        )

        accs = self.mgr.list_accounts()
        self.assertEqual(accs[0].email, "active@gmail.com")
        self.assertEqual(accs[1].email, "ready@gmail.com")
        self.assertEqual(accs[2].email, "cooling@gmail.com")
        self.assertEqual(self.mgr.get_ready_count(), 1)


if __name__ == "__main__":
    unittest.main()
