"""
Unit tests for Gemini Quota Monitor models, parsers, and OAuth configuration.
"""

from datetime import datetime, timedelta
import unittest

from config import ConfigManager, get_default_config_path
from core.models import QuotaItem, QuotaSnapshot
from core.fetchers.gemini import GeminiQuotaFetcher, parse_iso_datetime
from core.oauth import GoogleOAuthClient, find_free_port


class TestGeminiModels(unittest.TestCase):

    def test_quota_item_stats(self):
        reset_time = datetime.now() + timedelta(hours=2, minutes=30)
        item = QuotaItem(
            id="gemini_5h",
            name="5小时额度",
            remaining=85.0,
            total=100.0,
            unit="%",
            reset_time=reset_time,
        )

        self.assertEqual(item.percentage, 85.0)
        self.assertEqual(item.used, 15.0)
        self.assertEqual(item.used_percentage, 15.0)
        self.assertEqual(item.status_level, "good")
        self.assertEqual(item.status_color, "#10B981")

        # Test remaining mode stats
        rem_stats = item.get_display_stats("remaining")
        self.assertEqual(rem_stats["text"], "剩余 85%")
        self.assertEqual(rem_stats["short_text"], "85%")

        # Test used mode stats
        used_stats = item.get_display_stats("used")
        self.assertEqual(used_stats["text"], "已用 15%")
        self.assertEqual(used_stats["short_text"], "用15%")

        # Test countdown formatting
        countdown = item.format_countdown()
        self.assertIn("小时", countdown)
        self.assertIn("分钟", countdown)

    def test_parse_iso_datetime(self):
        iso_str = "2026-09-08T18:30:00Z"
        dt = parse_iso_datetime(iso_str)
        self.assertIsNotNone(dt)
        self.assertEqual(dt.year, 2026)
        # 18:30 UTC is next day 02:30 in UTC+8
        utc_dt = dt.astimezone(datetime.fromisoformat("2026-09-08T00:00:00+00:00").tzinfo)
        self.assertEqual(utc_dt.day, 8)
        self.assertEqual(utc_dt.hour, 18)
        self.assertEqual(utc_dt.minute, 30)

    def test_parse_groups_payload(self):
        payload = {
            "groups": [
                {
                    "displayName": "Gemini",
                    "buckets": [
                        {
                            "bucketId": "5h",
                            "displayName": "5小时额度",
                            "window": "5h",
                            "remainingFraction": 0.85,
                            "resetTime": "2026-09-08T18:30:00Z",
                        },
                        {
                            "bucketId": "weekly",
                            "displayName": "周总额度",
                            "window": "weekly",
                            "remainingFraction": 0.60,
                            "resetTime": "2026-09-15T00:00:00Z",
                        }
                    ]
                }
            ]
        }

        fetcher = GeminiQuotaFetcher(ConfigManager())
        items = fetcher._parse_quota_payload(payload)

        self.assertEqual(len(items), 2)
        item_5h = items[0]
        self.assertEqual(item_5h.id, "gemini_5h")
        self.assertAlmostEqual(item_5h.remaining, 85.0)

        item_weekly = items[1]
        self.assertEqual(item_weekly.id, "gemini_weekly")
        self.assertAlmostEqual(item_weekly.remaining, 60.0)

    def test_parse_models_fallback_payload(self):
        payload = {
            "models": {
                "gemini-pro-agent": {
                    "displayName": "Gemini 3.1 Pro (High)",
                    "quotaInfo": {
                        "remainingFraction": 0.72,
                        "resetTime": "2026-09-08T16:00:00Z",
                    },
                    "apiProvider": "API_PROVIDER_GOOGLE_GEMINI",
                },
                "gemini-3.5-flash-low": {
                    "displayName": "Gemini 3.5 Flash (Medium)",
                    "quotaInfo": {
                        "remainingFraction": 0.95,
                        "resetTime": "2026-09-08T16:00:00Z",
                    },
                    "apiProvider": "API_PROVIDER_GOOGLE_GEMINI",
                }
            }
        }

        fetcher = GeminiQuotaFetcher(ConfigManager())
        items = fetcher._parse_quota_payload(payload)

        self.assertGreaterEqual(len(items), 2)
        # Limiting model should be selected for 5h
        item_5h = items[0]
        self.assertAlmostEqual(item_5h.remaining, 72.0)

    def test_oauth_auth_url(self):
        client = GoogleOAuthClient()
        url, redirect_uri = client.build_auth_url(51121)
        self.assertIn("accounts.google.com", url)
        self.assertIn("client_id=", url)
        self.assertIn("redirect_uri=http%3A%2F%2Flocalhost%3A51121%2Foauth-callback", url)
        self.assertEqual(redirect_uri, "http://localhost:51121/oauth-callback")

    def test_find_free_port(self):
        port = find_free_port()
        self.assertGreater(port, 1024)


if __name__ == "__main__":
    unittest.main()
