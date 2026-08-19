"""Unit tests for scripts/collect_codex_observation.py.

Stdlib-only, matching the script's own zero-dependency footprint. Run with:

    python3 -m unittest discover -s tests

or a single file:

    python3 -m unittest tests.collect_codex_observation.test_collect_codex_observation
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional
from unittest import mock

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "collect_codex_observation.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("collect_codex_observation", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


cco = _load_module()

# 2026-08-22T20:15:00+02:00 — an arbitrary but realistic weekly resets_at epoch.
RESET_EPOCH = 1787422507


def make_reading(
    ts: datetime,
    used_percent: float,
    resets_at: Optional[int] = RESET_EPOCH,
    *,
    model: Optional[str] = "gpt-5.6-terra",
    reasoning: Optional[str] = "high",
    plan: Optional[str] = "plus",
    limit_id: str = "codex",
    window_minutes: Optional[int] = 10080,
    turn_tokens: int = 1_000_000,
    provider: Optional[str] = "openai",
):
    return cco.Reading(
        timestamp=ts,
        limit_id=limit_id,
        plan_type=plan,
        used_percent=used_percent,
        window_minutes=window_minutes,
        resets_at=resets_at,
        turn_tokens=turn_tokens,
        model=model,
        reasoning=reasoning,
        provider=provider,
        source_file="rollout-test.jsonl",
    )


class RunnerMixin:
    """Runs cco.main() against a fixed set of Reading objects, capturing stdout/stderr."""

    def run_main(self, readings: List["cco.Reading"], args: List[str]):
        argv = ["collect_codex_observation.py", "--codex-home", str(self.codex_home), *args]
        stdout, stderr = io.StringIO(), io.StringIO()
        sorted_readings = sorted(readings, key=lambda r: r.timestamp)
        with mock.patch.object(cco, "collect_readings", lambda codex_home: sorted_readings), \
                mock.patch.object(sys, "argv", argv), \
                contextlib.redirect_stdout(stdout), \
                contextlib.redirect_stderr(stderr):
            rc = cco.main()
        return rc, stdout.getvalue(), stderr.getvalue()


class CollectCodexObservationTest(RunnerMixin, unittest.TestCase):
    def setUp(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        self.codex_home = Path(tmpdir.name)
        self.base = datetime(2026, 8, 16, 5, 0, tzinfo=timezone.utc)

    # --- success path ---------------------------------------------------

    def test_clean_window_produces_csv_row(self):
        readings = [
            make_reading(self.base, 63.0),  # becomes the baseline (timestamp == start)
            make_reading(self.base + timedelta(hours=1), 70.0, turn_tokens=800_000),
        ]
        rc, out, err = self.run_main(
            readings,
            ["--start", self.base.isoformat(), "--end", (self.base + timedelta(hours=2)).isoformat(), "--plan", "plus"],
        )
        self.assertEqual(rc, 0, err)
        self.assertIn(
            "measurement_start,measurement_end,provider,agent,model,reasoning,tokens_m,limit_used,plan,limit_type",
            out,
        )
        self.assertIn("openai,codex,gpt-5.6-terra,high,0.8,7.0,plus,weekly", out)

    def test_default_start_detects_cycle_start_from_resets_at(self):
        # window_minutes=10080 (7 days) but resets_at is chosen so the detected cycle
        # start lands exactly at self.base — nowhere near a flat "7 days before --end".
        window_minutes = 10080
        resets_at = int((self.base + timedelta(minutes=window_minutes)).timestamp())
        readings = [
            make_reading(self.base - timedelta(minutes=1), 0.0, resets_at=resets_at, window_minutes=window_minutes),
            make_reading(
                self.base + timedelta(hours=1), 50.0,
                resets_at=resets_at, window_minutes=window_minutes, turn_tokens=2_000_000,
            ),
        ]
        end = self.base + timedelta(days=10)  # far more than 7 days after base
        rc, out, err = self.run_main(readings, ["--end", end.isoformat(), "--plan", "plus"])
        self.assertEqual(rc, 0, err)
        self.assertIn("Detected window start", err)
        self.assertIn(f"{self.base.date().isoformat()},{end.date().isoformat()},openai,codex", out)

    def test_default_start_falls_back_to_seven_days_without_resets_at(self):
        readings = [
            make_reading(self.base - timedelta(minutes=1), 0.0, resets_at=None, window_minutes=None),
            make_reading(self.base + timedelta(hours=1), 10.0, resets_at=None, window_minutes=None, turn_tokens=500_000),
        ]
        end = self.base + timedelta(hours=2)
        rc, out, err = self.run_main(readings, ["--end", end.isoformat(), "--plan", "plus", "--assume-fresh-start"])
        self.assertEqual(rc, 0, err)
        self.assertIn("defaulting to 7 days back", err)
        self.assertIn(",1.5,10.0,plus,unknown", out)

    def test_detected_start_ignores_previous_cycle_reading_as_baseline(self):
        # Regression: when --start is auto-detected as a reset boundary, a reading from
        # the PREVIOUS cycle sitting right before it must not become the baseline — that
        # would spuriously look like "a reset happened inside the window" every time,
        # since its resets_at necessarily differs from the new cycle's.
        window_minutes = 10080
        resets_at = int((self.base + timedelta(minutes=window_minutes)).timestamp())
        old_resets_at = resets_at - window_minutes * 60
        readings = [
            make_reading(self.base - timedelta(hours=2), 55.0, resets_at=old_resets_at, window_minutes=window_minutes),
            make_reading(self.base + timedelta(hours=1), 0.0, resets_at=resets_at, window_minutes=window_minutes),
            make_reading(self.base + timedelta(hours=2), 10.0, resets_at=resets_at, window_minutes=window_minutes),
        ]
        end = self.base + timedelta(hours=3)
        rc, out, err = self.run_main(readings, ["--end", end.isoformat(), "--plan", "plus"])
        self.assertEqual(rc, 0, err)
        self.assertNotIn("the limit reset inside this window", err)
        self.assertIn(",2.0,10.0,plus,weekly", out)

    def test_all_readings_are_summed_including_apparent_replays(self):
        # Regression: an earlier version excluded readings whose response_item ids
        # looked like confirmed duplicates of already-seen content (e.g. from a session
        # resume/fork/compaction), on the theory those tokens were already billed. Tested
        # against real data, that exclusion made tokens_m LESS accurate (moved it further
        # from Settings -> Usage and billing, not closer) on two independent exclusion
        # mechanisms — see collect_codex_observation.md. Every token_count reading in the
        # window must be summed, full stop.
        readings = [
            make_reading(self.base, 0.0),
            make_reading(self.base + timedelta(hours=1), 10.0, turn_tokens=500_000),
            make_reading(self.base + timedelta(hours=1, milliseconds=5), 20.0, turn_tokens=9_000_000),
        ]
        rc, out, err = self.run_main(
            readings,
            ["--start", self.base.isoformat(), "--end", (self.base + timedelta(hours=2)).isoformat(), "--plan", "plus"],
        )
        self.assertEqual(rc, 0, err)
        self.assertIn(",9.5,20.0,plus,weekly", out)

    def test_noise_dip_is_tolerated(self):
        readings = [
            make_reading(self.base, 30.0),
            make_reading(self.base + timedelta(minutes=1), 36.0),
            make_reading(self.base + timedelta(minutes=2), 31.0, turn_tokens=500_000),  # 5pt dip, same resets_at
            make_reading(self.base + timedelta(minutes=3), 40.0, turn_tokens=500_000),
        ]
        rc, out, err = self.run_main(
            readings,
            ["--start", self.base.isoformat(), "--end", (self.base + timedelta(minutes=10)).isoformat(), "--plan", "plus"],
        )
        self.assertEqual(rc, 0, err)
        self.assertIn("reporting noise", err)
        # baseline=30 (at start), end=40 -> limit_used=10.0; tokens summed over the 3 in-window readings
        self.assertIn(",2.0,10.0,plus,weekly", out)

    # --- reset detection --------------------------------------------------

    def test_real_reset_aborts(self):
        readings = [
            make_reading(self.base, 39.0),
            make_reading(self.base + timedelta(minutes=1), 40.0),
            make_reading(self.base + timedelta(hours=7), 0.0, resets_at=RESET_EPOCH + 7 * 86400),
            make_reading(self.base + timedelta(hours=8), 2.0, resets_at=RESET_EPOCH + 7 * 86400),
        ]
        rc, out, err = self.run_main(
            readings,
            ["--start", self.base.isoformat(), "--end", (self.base + timedelta(hours=9)).isoformat(), "--plan", "plus"],
        )
        self.assertEqual(rc, 1)
        self.assertIn("the limit reset inside this window", err)
        self.assertEqual(out, "")

    def test_reset_is_caught_even_without_a_used_percent_drop(self):
        # Real-world case: usage was ~0% right before AND right after a reset, so
        # used_percent never dips — only resets_at reveals the reset happened.
        readings = [
            make_reading(self.base, 0.0, resets_at=RESET_EPOCH),
            make_reading(self.base + timedelta(days=5), 0.0, resets_at=RESET_EPOCH + 7 * 86400),
            make_reading(self.base + timedelta(days=5, hours=1), 20.0, resets_at=RESET_EPOCH + 7 * 86400),
        ]
        rc, out, err = self.run_main(
            readings,
            ["--start", self.base.isoformat(), "--end", (self.base + timedelta(days=6)).isoformat(), "--plan", "plus"],
        )
        self.assertEqual(rc, 1)
        self.assertIn("the limit reset inside this window", err)
        self.assertEqual(out, "")

    def test_drop_with_missing_resets_at_is_not_guessed(self):
        readings = [
            make_reading(self.base, 20.0, resets_at=None),
            make_reading(self.base + timedelta(minutes=1), 15.0, resets_at=None),
        ]
        rc, out, err = self.run_main(
            readings,
            ["--start", self.base.isoformat(), "--end", (self.base + timedelta(minutes=10)).isoformat(), "--plan", "plus"],
        )
        self.assertEqual(rc, 1)
        self.assertIn("resets_at is missing on at least one side", err)

    def test_large_unexplained_drop_aborts(self):
        readings = [
            make_reading(self.base, 50.0),
            make_reading(self.base + timedelta(minutes=1), 55.0),
            make_reading(self.base + timedelta(minutes=2), 20.0),  # 35pt drop, same resets_at
            make_reading(self.base + timedelta(minutes=3), 25.0),
        ]
        rc, out, err = self.run_main(
            readings,
            ["--start", self.base.isoformat(), "--end", (self.base + timedelta(minutes=10)).isoformat(), "--plan", "plus"],
        )
        self.assertEqual(rc, 1)
        self.assertIn("too large to treat as", err)

    # --- ambiguity checks ---------------------------------------------------

    def test_mixed_accounts_require_limit_id(self):
        readings = [
            make_reading(self.base + timedelta(minutes=1), 10.0, limit_id="codex"),
            make_reading(self.base + timedelta(minutes=2), 5.0, limit_id="codex_other"),
        ]
        rc, out, err = self.run_main(
            readings,
            ["--start", self.base.isoformat(), "--end", (self.base + timedelta(minutes=10)).isoformat()],
        )
        self.assertEqual(rc, 1)
        self.assertIn("multiple accounts found", err)
        self.assertIn("codex_other", err)

    def test_mixed_models_are_rejected(self):
        readings = [
            make_reading(self.base + timedelta(minutes=1), 10.0, model="gpt-5.6-terra"),
            make_reading(self.base + timedelta(minutes=2), 15.0, model="gpt-5.3-codex"),
        ]
        rc, out, err = self.run_main(
            readings,
            ["--start", self.base.isoformat(), "--end", (self.base + timedelta(minutes=10)).isoformat(), "--plan", "plus"],
        )
        self.assertEqual(rc, 1)
        self.assertIn("multiple configurations used", err)
        self.assertIn("gpt-5.6-terra", err)
        self.assertIn("gpt-5.3-codex", err)

    def test_mixed_reasoning_are_rejected(self):
        readings = [
            make_reading(self.base + timedelta(minutes=1), 10.0, reasoning="high"),
            make_reading(self.base + timedelta(minutes=2), 15.0, reasoning="medium"),
        ]
        rc, out, err = self.run_main(
            readings,
            ["--start", self.base.isoformat(), "--end", (self.base + timedelta(minutes=10)).isoformat(), "--plan", "plus"],
        )
        self.assertEqual(rc, 1)
        self.assertIn("multiple configurations used", err)

    def test_missing_plan_leaves_field_blank_with_warning(self):
        readings = [
            make_reading(self.base, 10.0, plan=None),
            make_reading(self.base + timedelta(minutes=1), 15.0, plan=None),
        ]
        rc, out, err = self.run_main(
            readings,
            ["--start", self.base.isoformat(), "--end", (self.base + timedelta(minutes=10)).isoformat()],
        )
        self.assertEqual(rc, 0, err)
        self.assertIn("plan_type is not present", err)
        self.assertIn(",1.0,5.0,,weekly", out)

    def test_missing_plan_with_override_succeeds(self):
        readings = [
            make_reading(self.base, 10.0, plan=None),
            make_reading(self.base + timedelta(minutes=1), 15.0, plan=None),
        ]
        rc, out, err = self.run_main(
            readings,
            ["--start", self.base.isoformat(), "--end", (self.base + timedelta(minutes=10)).isoformat(), "--plan", "pro"],
        )
        self.assertEqual(rc, 0, err)
        self.assertIn(",pro,weekly", out)

    # --- baseline handling ---------------------------------------------------

    def test_missing_baseline_without_flag_aborts(self):
        readings = [make_reading(self.base + timedelta(minutes=1), 10.0)]
        rc, out, err = self.run_main(
            readings,
            ["--start", self.base.isoformat(), "--end", (self.base + timedelta(minutes=10)).isoformat(), "--plan", "plus"],
        )
        self.assertEqual(rc, 1)
        self.assertIn("no reading found before --start", err)

    def test_missing_baseline_with_flag_assumes_zero(self):
        readings = [make_reading(self.base + timedelta(minutes=1), 10.0)]
        rc, out, err = self.run_main(
            readings,
            [
                "--start", self.base.isoformat(),
                "--end", (self.base + timedelta(minutes=10)).isoformat(),
                "--plan", "plus",
                "--assume-fresh-start",
            ],
        )
        self.assertEqual(rc, 0, err)
        self.assertIn(",10.0,plus,weekly", out)

    def test_non_positive_values_are_rejected(self):
        readings = [
            make_reading(self.base, 10.0),
            make_reading(self.base + timedelta(minutes=1), 10.0),  # limit_used delta == 0
        ]
        rc, out, err = self.run_main(
            readings,
            ["--start", self.base.isoformat(), "--end", (self.base + timedelta(minutes=10)).isoformat(), "--plan", "plus"],
        )
        self.assertEqual(rc, 1)
        self.assertIn("not a usable observation", err)


class LimitTypeForTest(unittest.TestCase):
    def test_known_windows(self):
        self.assertEqual(cco.limit_type_for(10080), "weekly")
        self.assertEqual(cco.limit_type_for(300), "rolling_5h")

    def test_unknown_window(self):
        self.assertEqual(cco.limit_type_for(60), "rolling_60m")

    def test_missing_window(self):
        self.assertEqual(cco.limit_type_for(None), "unknown")


class SummarizeByGroupTest(unittest.TestCase):
    def test_groups_and_sums(self):
        base = datetime(2026, 8, 16, 5, 0, tzinfo=timezone.utc)
        readings = [
            make_reading(base, 10.0, model="a", turn_tokens=100),
            make_reading(base + timedelta(minutes=5), 20.0, model="a", turn_tokens=200),
            make_reading(base + timedelta(minutes=1), 5.0, model="b", turn_tokens=50),
        ]
        groups = {g.model: g for g in cco.summarize_by_group(readings)}
        self.assertEqual(groups["a"].count, 2)
        self.assertEqual(groups["a"].tokens_sum, 300)
        self.assertEqual(groups["a"].used_percent_first, 10.0)
        self.assertEqual(groups["a"].used_percent_last, 20.0)
        self.assertEqual(groups["b"].count, 1)


class DailyTokenBreakdownTest(unittest.TestCase):
    def test_sums_per_utc_day(self):
        base = datetime(2026, 8, 16, 5, 0, tzinfo=timezone.utc)
        readings = [
            make_reading(base, 10.0, turn_tokens=1_000_000),
            make_reading(base + timedelta(hours=2), 15.0, turn_tokens=500_000),
            make_reading(base + timedelta(days=1), 20.0, turn_tokens=2_000_000),
        ]
        breakdown = dict(cco.daily_token_breakdown(readings))
        self.assertEqual(len(breakdown), 2)
        self.assertEqual(breakdown[base.date()], 1_500_000)
        self.assertEqual(breakdown[(base + timedelta(days=1)).date()], 2_000_000)

    def test_day_boundary_is_utc_not_local(self):
        # 23:30 UTC is already the next UTC day at UTC+2 (01:30 local) — must still bucket
        # under the UTC date, since that's what Codex's own daily chart uses.
        ts = datetime(2026, 8, 16, 23, 30, tzinfo=timezone.utc)
        breakdown = dict(cco.daily_token_breakdown([make_reading(ts, 10.0, turn_tokens=1_000_000)]))
        self.assertEqual(list(breakdown.keys()), [date(2026, 8, 16)])


if __name__ == "__main__":
    unittest.main()
