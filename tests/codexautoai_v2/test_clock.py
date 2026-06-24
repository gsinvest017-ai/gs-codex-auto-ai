"""Tests for src/codexautoai_v2/clock.py"""

import re
import time

from src.codexautoai_v2.clock import now_iso, now_ts


class TestNowIso:
    def test_returns_string(self):
        result = now_iso()
        assert isinstance(result, str)

    def test_iso8601_format(self):
        result = now_iso()
        # Must match UTC ISO-8601 with timezone offset (+HH:MM or Z)
        iso_pattern = re.compile(
            r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$'
        )
        assert iso_pattern.match(result), f"Not a valid ISO-8601 timestamp: {result!r}"

    def test_utc_timezone(self):
        result = now_iso()
        # Python datetime.isoformat() for UTC gives '+00:00' suffix
        assert result.endswith('+00:00') or result.endswith('Z'), (
            f"Expected UTC timezone in: {result!r}"
        )

    def test_monotonically_nondecreasing(self):
        t1 = now_iso()
        t2 = now_iso()
        assert t2 >= t1

    def test_changes_over_time(self):
        t1 = now_ts()
        time.sleep(0.05)
        t2 = now_ts()
        assert t2 > t1


class TestNowTs:
    def test_returns_float(self):
        result = now_ts()
        assert isinstance(result, float)

    def test_reasonable_epoch_value(self):
        # 2020-01-01 in epoch seconds ~ 1577836800
        # 2040-01-01 in epoch seconds ~ 2208988800
        result = now_ts()
        assert 1_577_836_800.0 < result < 2_208_988_800.0, (
            f"Epoch seconds {result} looks unreasonable"
        )

    def test_consistent_with_now_iso(self):
        from datetime import datetime, timezone
        ts = now_ts()
        iso = now_iso()
        # Parse the ISO string back to epoch
        parsed = datetime.fromisoformat(iso).timestamp()
        # They are called at slightly different times; allow 1-second delta
        assert abs(ts - parsed) < 1.0
