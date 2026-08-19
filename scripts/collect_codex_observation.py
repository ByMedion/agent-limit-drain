# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///

"""Collects a Codex observation for Agent Limit Drain from local session logs.

Codex (the OpenAI CLI/desktop agent) writes JSONL "rollout" logs under
``$CODEX_HOME/sessions`` and ``$CODEX_HOME/archived_sessions`` (default
``CODEX_HOME`` is ``~/.codex``). Each rollout periodically logs a
``token_count`` event containing the account's live rate-limit usage
(``used_percent``) together with the token usage of that turn — this is the
same "Codex token statistics" the manual guide
(docs/collecting-data/codex.md) asks contributors to read from the UI.

This script reads those local logs for a given time window and, when the
data is unambiguous (single model/reasoning/plan, no reset crossed the
window, a usable rate-limit reading at both ends), prints a ready-to-paste
CSV row plus an evidence block for the Data Observation pull request.

When the data is missing, ambiguous, or mixes multiple models/accounts, it
prints the raw readings it found instead and lets you assemble the CSV row
by hand, following docs/collecting-data/codex.md.

This is best-effort and depends on local log retention: Codex moves inactive
sessions into archived_sessions/ (this script reads both directories), but
old sessions can eventually be deleted from disk entirely. Run this script
soon after the window you want to record ends — do not rely on it to
reconstruct old windows months later. If the needed logs are already gone,
it falls back to the manual guide rather than guessing.

This is also best-effort in another sense: it depends on Codex's internal,
undocumented log format, which may change between CLI versions. Always
sanity-check the result against Settings -> Usage and billing before
submitting a PR.

With no --start/--days, the window ends now and starts at the detected beginning of the
current cycle (the latest reading's resets_at minus its window length) — not a hardcoded
7 days back, since the account's real reset cadence and where "now" falls inside it can vary.

Usage:
    uv run scripts/collect_codex_observation.py
    uv run scripts/collect_codex_observation.py --start 2026-08-16T20:15 --end 2026-08-22T20:15
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

LOCAL_TZ = datetime.now().astimezone().tzinfo

# provider/agent are fixed: this script only understands Codex's own log format.
PROVIDER = "openai"
AGENT = "codex"

LIMIT_TYPE_BY_WINDOW_MINUTES = {
    10080: "weekly",
    300: "rolling_5h",
}

# resets_at drifts by a few seconds even within a stable cycle (server-side recompute
# noise); only a jump bigger than this counts as an actual reset.
RESET_JITTER_TOLERANCE_SECONDS = 300

# A used_percent dip this large without a resets_at change is treated as suspicious
# rather than as reporting noise from a race between concurrent sessions.
NOISE_DROP_THRESHOLD_PERCENT = 15.0

_ANSI_YELLOW = "\033[33m"
_ANSI_RED = "\033[31m"
_ANSI_CYAN = "\033[36m"
_ANSI_RESET = "\033[0m"


def _colorize(text: str, code: str, stream) -> str:
    """Wraps text in an ANSI color code, but only when writing to a real terminal —
    piping/redirecting output (e.g. saving to a file for a PR) must stay plain text."""
    if not stream.isatty():
        return text
    return f"{code}{text}{_ANSI_RESET}"


def warn(msg: str) -> None:
    print(_colorize(msg, _ANSI_YELLOW, sys.stderr), file=sys.stderr)


def error(msg: str) -> None:
    print(_colorize(msg, _ANSI_RED, sys.stderr), file=sys.stderr)


def paste(msg: str = "") -> None:
    """Prints one line of the copy-pasteable CSV row / evidence block, accented so it
    stands out from the surrounding diagnostic text."""
    print(_colorize(msg, _ANSI_CYAN, sys.stdout))


@dataclass
class Reading:
    """One token_count event, enriched with the model/reasoning active at the time."""

    timestamp: datetime
    limit_id: str
    plan_type: Optional[str]
    used_percent: float
    window_minutes: Optional[int]
    resets_at: Optional[int]
    turn_tokens: int
    model: Optional[str]
    reasoning: Optional[str]
    provider: Optional[str]
    source_file: str


def parse_timestamp(raw: str) -> datetime:
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


def parse_cli_datetime(raw: str) -> datetime:
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=LOCAL_TZ)
    return dt


def iter_rollout_files(codex_home: Path) -> List[Path]:
    """Lists rollout files across sessions/ and archived_sessions/, sorted by filename
    (rollout-<timestamp>-<uuid>.jsonl) rather than full path. Sorting by full path would
    group all of archived_sessions/ before all of sessions/ (alphabetically 'a' < 's'),
    which is unrelated to when the files were actually created."""
    files = list((codex_home / "sessions").glob("**/*.jsonl"))
    files += list((codex_home / "archived_sessions").glob("*.jsonl"))
    return sorted(files, key=lambda p: p.name)


def collect_readings(codex_home: Path) -> List[Reading]:
    """Parses every rollout file, pairing each token_count event with the
    model/reasoning/provider active in that same session at that point.

    Does NOT deduplicate replayed history (session resume/fork/compaction can rewrite
    prior `response_item` records into a new rollout file with the same server-assigned
    id as one already seen). An earlier version of this script excluded readings whose
    response_item ids were confirmed duplicates, on the theory that replayed content was
    already billed once. That was tested against real data and made tokens_m *less*
    accurate, not more: on the same window, the unfiltered sum (197.7M) was within 2.7%
    of Settings -> Usage and billing (192.5M), while excluding confirmed-duplicate
    readings pushed it to 181.9M, 5.5% away in the same direction on two independent
    exclusion mechanisms (a timing heuristic, then id-based dedup). A duplicated
    response_item id proves the *content* was resent, not that the tokens were free the
    second time — an LLM call re-sends and re-bills its context on every turn unless
    server-side prompt caching applies, and caching is priced (separate read/write
    rates), not free. Don't reintroduce a replay exclusion without new evidence it
    actually tracks billing.
    """
    readings: List[Reading] = []

    for path in iter_rollout_files(codex_home):
        model: Optional[str] = None
        reasoning: Optional[str] = None
        provider: Optional[str] = None

        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue

        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue

            rtype = record.get("type")
            payload = record.get("payload") or {}

            if rtype == "session_meta":
                provider = payload.get("model_provider") or provider
            elif rtype == "turn_context":
                model = payload.get("model") or model
                reasoning = payload.get("effort") or reasoning
            elif rtype == "event_msg" and payload.get("type") == "token_count":
                info = payload.get("info") or {}
                rate_limits = payload.get("rate_limits") or {}
                primary = rate_limits.get("primary")
                if not primary:
                    continue
                used_percent = primary.get("used_percent")
                limit_id = rate_limits.get("limit_id")
                if used_percent is None or not limit_id:
                    continue
                turn_tokens = (info.get("last_token_usage") or {}).get("total_tokens")
                if turn_tokens is None:
                    continue
                try:
                    ts = parse_timestamp(record["timestamp"])
                except (KeyError, ValueError):
                    continue

                readings.append(
                    Reading(
                        timestamp=ts,
                        limit_id=limit_id,
                        plan_type=primary.get("plan_type"),
                        used_percent=float(used_percent),
                        window_minutes=primary.get("window_minutes"),
                        resets_at=primary.get("resets_at"),
                        turn_tokens=int(turn_tokens),
                        model=model,
                        reasoning=reasoning,
                        provider=provider,
                        source_file=path.name,
                    )
                )

    readings.sort(key=lambda r: r.timestamp)
    return readings


def limit_type_for(window_minutes: Optional[int]) -> str:
    if window_minutes is None:
        return "unknown"
    return LIMIT_TYPE_BY_WINDOW_MINUTES.get(window_minutes, f"rolling_{window_minutes}m")


def format_resets_at(epoch: Optional[int]) -> str:
    if epoch is None:
        return "unknown"
    return datetime.fromtimestamp(epoch, tz=timezone.utc).astimezone(LOCAL_TZ).isoformat()


@dataclass
class GroupSummary:
    """Aggregated view of every reading sharing one account/model/reasoning/plan/limit."""

    limit_id: str
    model: Optional[str]
    reasoning: Optional[str]
    plan_type: Optional[str]
    window_minutes: Optional[int]
    count: int
    first_ts: datetime
    last_ts: datetime
    tokens_sum: int
    used_percent_first: float
    used_percent_last: float
    used_percent_min: float
    used_percent_max: float


def daily_token_breakdown(readings: List[Reading]) -> List[Tuple[date, int]]:
    """Sums tokens per UTC calendar day.

    For cross-checking tokens_m against Codex's own Settings -> Usage and billing daily
    chart, which buckets by UTC day (confirmed empirically: bucketing by local day instead
    shifts tokens between adjacent days by however far local time sits from UTC). The
    window's first/last day is commonly partial (the window doesn't start/end at UTC
    midnight), so it won't match the UI's full-day total for that day — that's expected,
    not an error.
    """
    by_day: Dict[date, int] = {}
    for r in readings:
        day = r.timestamp.astimezone(timezone.utc).date()
        by_day[day] = by_day.get(day, 0) + r.turn_tokens
    return sorted(by_day.items())


def summarize_by_group(readings: List[Reading]) -> List[GroupSummary]:
    """Groups readings by (account, model, reasoning, plan, limit window) so the
    fallback output is one summary line per configuration, not one per event."""
    groups: Dict[Tuple, List[Reading]] = {}
    for r in readings:
        key = (r.limit_id, r.model, r.reasoning, r.plan_type, r.window_minutes)
        groups.setdefault(key, []).append(r)

    summaries = []
    for (limit_id, model, reasoning, plan_type, window_minutes), items in groups.items():
        items.sort(key=lambda r: r.timestamp)
        used_percents = [r.used_percent for r in items]
        summaries.append(
            GroupSummary(
                limit_id=limit_id,
                model=model,
                reasoning=reasoning,
                plan_type=plan_type,
                window_minutes=window_minutes,
                count=len(items),
                first_ts=items[0].timestamp,
                last_ts=items[-1].timestamp,
                tokens_sum=sum(r.turn_tokens for r in items),
                used_percent_first=used_percents[0],
                used_percent_last=used_percents[-1],
                used_percent_min=min(used_percents),
                used_percent_max=max(used_percents),
            )
        )
    summaries.sort(key=lambda g: g.first_ts)
    return summaries


def print_raw_dump(readings: List[Reading], start: datetime, end: datetime, reason: str) -> None:
    in_window = [r for r in readings if start < r.timestamp <= end]
    error(f"Could not assemble a CSV row: {reason}")
    print(
        f"\nReadings in {start.isoformat()} .. {end.isoformat()}, "
        "grouped by account/model/reasoning/plan/limit:",
        file=sys.stderr,
    )
    if not in_window:
        print("  (none found — is CODEX_HOME correct, and does the window overlap real usage?)", file=sys.stderr)
    for g in summarize_by_group(in_window):
        print(
            f"\n  {g.limit_id} / {g.model} / {g.reasoning} / plan={g.plan_type} / {limit_type_for(g.window_minutes)}",
            file=sys.stderr,
        )
        print(
            f"    {g.count} reading(s), {g.first_ts.isoformat()} .. {g.last_ts.isoformat()}\n"
            f"    tokens: {g.tokens_sum / 1_000_000:.1f}M   "
            f"used_percent: {g.used_percent_first:.1f}% -> {g.used_percent_last:.1f}% "
            f"(range {g.used_percent_min:.1f}-{g.used_percent_max:.1f}%)",
            file=sys.stderr,
        )
    print(
        "\nExactly one group cleanly covering your window? Use its token sum and "
        "(last - first) used_percent. Otherwise narrow --start/--end, pass --limit-id, "
        "or assemble the row by hand — see docs/collecting-data/codex.md.",
        file=sys.stderr,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--codex-home", type=Path, default=None, help="Defaults to $CODEX_HOME or ~/.codex")
    parser.add_argument("--start", type=str, default=None, help="Measurement window start (ISO 8601)")
    parser.add_argument("--end", type=str, default=None, help="Measurement window end (ISO 8601, default: now)")
    parser.add_argument(
        "--days",
        type=float,
        default=None,
        help=(
            "Window length in days if --start is omitted. Default: detect the start of the "
            "current cycle from the latest rate-limit reading (resets_at minus the window "
            "length), falling back to 7 days back from --end if no reading is found."
        ),
    )
    parser.add_argument("--limit-id", type=str, default=None, help="Restrict to a specific rate_limits.limit_id")
    parser.add_argument("--plan", type=str, default=None, help="Override/force the plan value")
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print every tolerated used_percent dip and long gap individually instead of a collapsed summary.",
    )
    parser.add_argument(
        "--assume-fresh-start",
        action="store_true",
        help=(
            "If no reading exists before --start, assume 0%% used at that point instead of "
            "aborting. Only pass this after confirming in Settings -> Usage and billing that "
            "usage was actually 0%% then — missing local logs can also mean old sessions were "
            "already archived/deleted, not that the window is fresh."
        ),
    )
    parser.add_argument(
        "--full-utc-days",
        action="store_true",
        help=(
            "Also print a diagnostic token breakdown widened to whole UTC calendar days "
            "covering [start, end], ignoring the reset's exact time-of-day — Settings -> "
            "Usage and billing appears to bucket by date only, so this can be closer to "
            "what it shows. Does not change tokens_m/limit_used in the CSV row: "
            "widening the real window would pull in usage from before the reset, mixing "
            "two limit cycles into one observation, which is invalid to submit."
        ),
    )
    args = parser.parse_args()

    codex_home = args.codex_home or Path(
        __import__("os").environ.get("CODEX_HOME", str(Path.home() / ".codex"))
    )
    if not codex_home.exists():
        error(f"CODEX_HOME not found at {codex_home}. Follow docs/collecting-data/codex.md manually.")
        return 1

    readings = collect_readings(codex_home)
    if not readings:
        error(f"No token_count readings found under {codex_home}. Follow docs/collecting-data/codex.md manually.")
        return 1

    end = parse_cli_datetime(args.end) if args.end else datetime.now(tz=LOCAL_TZ)
    start_is_detected_reset = False
    if args.start:
        start = parse_cli_datetime(args.start)
    elif args.days is not None:
        start = end - timedelta(days=args.days)
    else:
        # Don't assume "now" is 7 days into the cycle — detect the actual cycle start from
        # the latest reading's resets_at (the account's real reset cadence may not be
        # exactly 7 days, and "now" may fall anywhere inside the current cycle).
        candidates = [r for r in readings if r.timestamp <= end and r.resets_at and r.window_minutes]
        if args.limit_id:
            candidates = [r for r in candidates if r.limit_id == args.limit_id]
        if candidates:
            latest = candidates[-1]
            start = datetime.fromtimestamp(latest.resets_at, tz=timezone.utc) - timedelta(minutes=latest.window_minutes)
            start_is_detected_reset = True
            print(
                f"i Detected window start: {start.isoformat()} (resets_at minus window length). "
                "Pass --start/--days to override.",
                file=sys.stderr,
            )
            print(file=sys.stderr)
        else:
            start = end - timedelta(days=7)
            warn(
                "! No rate-limit reading found to detect the cycle start; defaulting to 7 days "
                "back from --end. Pass --start/--days explicitly if this is wrong."
            )
            print(file=sys.stderr)

    in_window = [r for r in readings if start < r.timestamp <= end]
    candidate_ids = args.limit_id and {args.limit_id} or {r.limit_id for r in in_window}
    if len(candidate_ids) != 1:
        reason = (
            f"multiple accounts found ({sorted(candidate_ids)}) — rerun with --limit-id to pick one."
            if len(candidate_ids) > 1
            else "no rate-limit readings found in this window."
        )
        print_raw_dump(readings, start, end, reason)
        return 1
    limit_id = next(iter(candidate_ids))

    scoped = [r for r in readings if r.limit_id == limit_id]
    window_scoped = [r for r in scoped if start < r.timestamp <= end]
    before_start = [r for r in scoped if r.timestamp <= start]

    if not window_scoped:
        print_raw_dump(readings, start, end, "no rate-limit readings found in this window.")
        return 1

    # Different models/reasoning configurations must become separate observations —
    # check this before anything else, since it's the most actionable thing to fix.
    models = {r.model for r in window_scoped if r.model}
    reasonings = {r.reasoning for r in window_scoped if r.reasoning}
    providers = {r.provider for r in window_scoped if r.provider} or {PROVIDER}
    if len(models) != 1 or len(reasonings) != 1 or len(providers) != 1:
        print_raw_dump(
            readings,
            start,
            end,
            f"multiple configurations used in this window (models={sorted(models)}, "
            f"reasoning={sorted(reasonings)}, providers={sorted(providers)}). Each observation "
            "must use a single provider/model/reasoning — narrow the window or split into "
            "separate observations.",
        )
        return 1

    if start_is_detected_reset:
        # start was computed as this account's own reset boundary (resets_at minus the
        # window length), not guessed — usage at that instant is 0% by construction, and
        # any reading from before it belongs to the previous cycle, not a valid baseline.
        baseline = None
        baseline_used_percent = 0.0
    else:
        baseline = before_start[-1] if before_start else None
        if baseline is None:
            earliest_known = readings[0].timestamp
            if not args.assume_fresh_start:
                print_raw_dump(
                    readings,
                    start,
                    end,
                    f"no reading found before --start, so the baseline usage is unknown. The oldest "
                    f"local session log found is from {earliest_known.isoformat()} — if you were "
                    "already using Codex before that, older logs were likely archived/deleted and "
                    "this window cannot be trusted from local logs alone. Pass --assume-fresh-start "
                    "only if you've confirmed in Settings -> Usage and billing that usage was "
                    "actually 0% at --start (e.g. right after a reset).",
                )
                return 1
            baseline_used_percent = 0.0
            warn(
                f"! Assuming 0% baseline at {start.isoformat()} (--assume-fresh-start). Oldest local "
                f"session log found is from {earliest_known.isoformat()} — double-check this against "
                "Settings -> Usage and billing; local logs may have been pruned rather than this "
                "genuinely being the start of a reset cycle."
            )
            print(file=sys.stderr)
        else:
            baseline_used_percent = baseline.used_percent

    end_reading = window_scoped[-1]

    # resets_at is the authoritative signal for a reset — check it on every consecutive
    # pair, unconditionally. A drop in used_percent is NOT a reliable trigger on its own:
    # if usage happened to be low (or zero) on both sides of an actual reset, used_percent
    # won't dip at all even though the window silently spans two different cycles.
    sequence = ([baseline] if baseline else []) + window_scoped
    noise_dips: List[float] = []
    for prev, cur in zip(sequence, sequence[1:]):
        resets_at_changed = (
            prev.resets_at is not None
            and cur.resets_at is not None
            and abs(cur.resets_at - prev.resets_at) > RESET_JITTER_TOLERANCE_SECONDS
        )
        if resets_at_changed:
            print_raw_dump(
                readings,
                start,
                end,
                f"the limit reset inside this window: resets_at moved from "
                f"{format_resets_at(prev.resets_at)} to {format_resets_at(cur.resets_at)} between "
                f"{prev.timestamp.isoformat()} (used_percent={prev.used_percent}%) and "
                f"{cur.timestamp.isoformat()} (used_percent={cur.used_percent}%). Narrow "
                "--start/--end to stay within one reset cycle.",
            )
            return 1

        if cur.used_percent >= prev.used_percent:
            continue

        drop = prev.used_percent - cur.used_percent

        if prev.resets_at is None or cur.resets_at is None:
            print_raw_dump(
                readings,
                start,
                end,
                f"used_percent dropped {drop:.1f} points (from {prev.used_percent}% to "
                f"{cur.used_percent}%) between {prev.timestamp.isoformat()} and "
                f"{cur.timestamp.isoformat()}, and resets_at is missing on at least one side, so "
                "there's no way to confirm whether a reset caused it. Check Settings -> Usage and "
                "billing, or narrow --start/--end to avoid this pair.",
            )
            return 1

        if drop > NOISE_DROP_THRESHOLD_PERCENT:
            print_raw_dump(
                readings,
                start,
                end,
                f"used_percent dropped {drop:.1f} points (from {prev.used_percent}% to "
                f"{cur.used_percent}%) between {prev.timestamp.isoformat()} and "
                f"{cur.timestamp.isoformat()} without resets_at changing — too large to treat as "
                "reporting noise. Check Settings -> Usage and billing before trusting this window, "
                "or narrow --start/--end to avoid it.",
            )
            return 1

        if args.verbose:
            warn(
                f"! used_percent dipped from {prev.used_percent}% to {cur.used_percent}% at "
                f"{cur.timestamp.isoformat()} without resets_at changing — treating as reporting noise "
                "(likely a race between concurrent sessions) and continuing."
            )
        noise_dips.append(drop)

    if noise_dips:
        if not args.verbose:
            warn(
                f"! Tolerated {len(noise_dips)} used_percent dip(s) as reporting noise "
                f"(max {max(noise_dips):.1f}pt, no resets_at change — likely a race between "
                "concurrent sessions). Pass --verbose to see each one."
            )
        print(file=sys.stderr)

    tokens_m = sum(r.turn_tokens for r in window_scoped) / 1_000_000
    limit_used = end_reading.used_percent - baseline_used_percent
    limit_type = limit_type_for(end_reading.window_minutes)
    model = next(iter(models))
    reasoning = next(iter(reasonings))
    provider = next(iter(providers))

    # Shown regardless of whether a CSV row can be assembled below (e.g. even if --plan
    # is still missing) — it doesn't depend on plan and is useful on its own for
    # cross-checking against Settings -> Usage and billing.
    breakdown = daily_token_breakdown(window_scoped)
    print("Daily token breakdown (UTC day, first/last day often partial) —")
    print("cross-check against Settings -> Usage and billing:")
    for day, day_tokens in breakdown:
        print(f"  {day.isoformat()}  {day_tokens / 1_000_000:6.1f}M")
    print(f"  {'total':10}  {sum(t for _, t in breakdown) / 1_000_000:6.1f}M")
    print()
    sys.stdout.flush()

    if args.full_utc_days:
        day_lo = datetime.combine(start.astimezone(timezone.utc).date(), time.min, tzinfo=timezone.utc)
        day_hi = datetime.combine(end.astimezone(timezone.utc).date() + timedelta(days=1), time.min, tzinfo=timezone.utc)
        widened = [r for r in scoped if day_lo <= r.timestamp < day_hi]
        widened_tokens_m = sum(r.turn_tokens for r in widened) / 1_000_000
        print(
            "Diagnostic: widened to whole UTC calendar days (ignores reset time-of-day) —"
        )
        print("NOT used for tokens_m/limit_used above; may include usage from the previous cycle:")
        print(f"  window  {day_lo.isoformat()} .. {day_hi.isoformat()}")
        for day, day_tokens in daily_token_breakdown(widened):
            print(f"  {day.isoformat()}  {day_tokens / 1_000_000:6.1f}M")
        print(f"  {'total':10}  {widened_tokens_m:6.1f}M")
        print()
        sys.stdout.flush()

    plan = args.plan or end_reading.plan_type or ""
    if not plan:
        warn(
            "! plan_type is not present in these logs — leaving it blank below. Fill it in "
            "from Settings -> Usage and billing, or pass --plan next time to skip this."
        )
        print(file=sys.stderr)

    if tokens_m <= 0 or limit_used <= 0:
        print_raw_dump(
            readings,
            start,
            end,
            f"computed tokens_m={tokens_m:.6f}, limit_used={limit_used:.3f} — "
            "not a usable observation (need both > 0).",
        )
        return 1

    csv_row = (
        f"{start.date().isoformat()},{end.date().isoformat()},{provider},{AGENT},"
        f"{model},{reasoning},{tokens_m:.1f},{limit_used:.1f},{plan},{limit_type}"
    )

    gap_threshold = timedelta(hours=36)
    gap_pairs = [(prev, cur) for prev, cur in zip(sequence, sequence[1:]) if cur.timestamp - prev.timestamp > gap_threshold]
    if args.verbose:
        for prev, cur in gap_pairs:
            warn(
                f"! {cur.timestamp - prev.timestamp} gap between readings at {prev.timestamp.isoformat()} "
                f"and {cur.timestamp.isoformat()} — could be normal inactivity, or local logs pruned "
                "from the middle of the window. Cross-check tokens_m against Settings -> Usage and billing."
            )
    elif gap_pairs:
        longest = max(cur.timestamp - prev.timestamp for prev, cur in gap_pairs)
        warn(
            f"! {len(gap_pairs)} gap(s) over {gap_threshold} between readings (longest: {longest}) — "
            "could be normal inactivity, or local logs pruned from the middle of the window. "
            "Cross-check tokens_m against Settings -> Usage and billing. Pass --verbose to see each one."
        )
    if gap_pairs:
        print(file=sys.stderr)

    print("Verify against Settings -> Usage and billing before submitting, then paste below.")
    print()
    print("CSV row for data/YYYY/YYYY-MM.csv:")
    paste("```csv")
    paste("measurement_start,measurement_end,provider,agent,model,reasoning,tokens_m,limit_used,plan,limit_type")
    paste(csv_row)
    paste("```")
    print()
    print("Evidence for the Data Observation pull request (provider/agent/model/tokens/limit")
    print("are already in the CSV row above — this is just what isn't):")
    paste("```text")
    paste(
        f"Source: local Codex session logs (~/.codex/sessions), via scripts/collect_codex_observation.py "
        f"— used_percent {baseline_used_percent:.1f}% -> {end_reading.used_percent:.1f}%"
    )
    paste()
    paste("Reset/accounting changes during measurement: No (verify manually)")
    paste("Purchased/additional credits used: No (verify manually)")
    paste("```")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
