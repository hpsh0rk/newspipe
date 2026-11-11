"""Invocation clocks advance through nested work and never leak to later invocations."""
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class ClockTests(unittest.TestCase):
    def test_nested_scopes_share_elapsed_time_and_reset_after_failure(self):
        from newspipe import clock
        anchor = datetime(2026, 11, 11, 8, 15)
        elapsed = [10.0]
        @clock.scoped
        def nested(*, now=None):
            return now, clock.now()
        @clock.scoped
        def outer(*, now=None):
            elapsed[0] += 120
            observed = nested(now=anchor - timedelta(days=1))
            self.assertEqual(observed, (anchor + timedelta(minutes=2),) * 2)
            raise ValueError("scope must reset")
        with patch("time.monotonic", side_effect=lambda: elapsed[0]):
            with self.assertRaises(ValueError):
                outer(now=anchor)
            later = anchor + timedelta(days=1)
            self.assertEqual(nested(now=later), (later, later))

    def test_clock_preserves_timezone_and_clamps_negative_elapsed(self):
        from newspipe import clock
        anchor = datetime.fromisoformat("2026-11-11T08:15:00+08:00")
        elapsed = [10.0]
        @clock.scoped
        def run(*, now=None):
            elapsed[0] = 5.0
            return clock.now()
        with patch("time.monotonic", side_effect=lambda: elapsed[0]):
            self.assertEqual(run(now=anchor), anchor)
