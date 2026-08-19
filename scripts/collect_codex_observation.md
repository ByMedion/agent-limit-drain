# `collect_codex_observation.py` — implementation notes

This is a code-contributor doc: how the script actually works and why, for whoever
next has to change it. If you're trying to *submit* an observation, see
[docs/collecting-data/codex.md](../docs/collecting-data/codex.md) instead — this file
assumes you're already reading the source.

Everything here was reverse-engineered from real local Codex log data, not from
official documentation — Codex's rollout log format is internal and undocumented, and
can change between CLI versions without notice. Where a design decision looks odd,
it's very likely working around one of the gotchas below, not an oversight.

## Data source

Codex writes JSONL "rollout" files under `$CODEX_HOME/sessions/**/*.jsonl` and
`$CODEX_HOME/archived_sessions/*.jsonl` (default `CODEX_HOME` is `~/.codex`). The
script reads both directories. Inactive sessions get moved from `sessions/` into
`archived_sessions/`, but old sessions can eventually be **deleted from disk
entirely** — there is no way to recover a window whose logs are already gone. Run the
script soon after the window you want ends; don't rely on it for old windows.

Each rollout file periodically logs an `event_msg` record with
`payload.type == "token_count"`. That event is the primary data source this script
uses; everything else (model, reasoning, provider) is read from nearby records in the
same file to enrich it.

## Key fields and their gotchas

Inside one `token_count` event's `payload.info`:

- `total_token_usage.total_tokens` — **not a billing total.** Verified directly: in 179
  of 180 local rollout files, the *first* `token_count` event already had a non-zero
  `total_token_usage`, and in a 2156-event session picked for a deep check it grew
  monotonically (never decreased, even across compaction) to 92.7M while that same
  session's `last_token_usage` values summed to 224.8M — a 2.4x gap with no fork, no
  resume, nothing to dedupe. That rules out "cumulative billed tokens": it behaves like
  a running measure of *current context/conversation size*, which persists across a
  resumed/forked file's parent (explaining the non-zero first value) but does not track
  1:1 with what each API call actually cost. Don't use it as a cross-check for
  `tokens_m`, and don't assume it resets to 0 per file.
- `last_token_usage.total_tokens` — the token cost of just that turn (a delta, not a
  running total). **This is what `tokens_m` is summed from.** This is the right field
  for billing: a chat-completions-style API call re-sends (and re-bills) the prior
  context on every turn unless server-side caching applies, which is consistent with it
  summing higher than `total_token_usage`'s growth, not a bug to reconcile away.

Inside `payload.rate_limits.primary`:

- `used_percent` — the account's live usage of *this* rate limit, as a percentage
  already consumed. Not monotonic: it can jitter down by a few points between two
  readings taken moments apart, with no reset involved (see "Noise vs. real resets").
- `resets_at` — Unix epoch of the *next* reset. Drifts by a few seconds even within a
  stable cycle (server-side recompute noise) — only a jump measured in hours or days
  means an actual reset happened. See `RESET_JITTER_TOLERANCE_SECONDS`.
- `window_minutes` — the limit's cycle length in minutes. `10080` = weekly, `300` =
  5-hour rolling; mapped to a `limit_type` label in `limit_type_for()`.
- `plan_type` — frequently `null` in older CLI versions' logs. When missing, the script
  doesn't abort: it prints a warning and leaves the CSV row's `plan` field blank for you
  to fill in by hand (or pass `--plan` to skip that). Don't assume it'll always be
  there.
- `limit_id` — the account/quota this reading belongs to. **Not globally unique to
  one Codex install**: a machine used with more than one ChatGPT account/workspace
  will have multiple distinct `limit_id` values interleaved chronologically in the
  same log directories. The script partitions almost everything by `limit_id` and
  requires the caller to disambiguate with `--limit-id` when more than one appears in
  the requested window — never silently picks one.

## Noise vs. real resets

`used_percent` dropping between two readings does **not** reliably mean a reset
happened — concurrent sessions reading the rate-limit state can produce small,
spurious dips (a few points) that self-correct within moments. `resets_at` is the
only field the account itself uses to mark a reset, so it's the authoritative signal.

**Important:** the `resets_at` check runs on *every* consecutive pair of readings,
unconditionally — not only when `used_percent` happens to drop. An earlier version of
this script only checked `resets_at` inside the "used_percent dropped" branch, which
missed real resets whenever usage happened to be ~0% on both sides of the reset (e.g.
an idle period spanning it) — no visible drop, so the reset went undetected and the
computed window silently spanned two different limit cycles. Don't reintroduce that
gate.

Given a drop with no `resets_at` change, the size of the drop still matters:

- `resets_at` differs by more than `RESET_JITTER_TOLERANCE_SECONDS` → real reset,
  abort (`the limit reset inside this window`).
- `resets_at` unchanged but the drop exceeds `NOISE_DROP_THRESHOLD_PERCENT` → too
  large to hand-wave away as noise, abort rather than guess.
- `resets_at` unchanged and the drop is smaller than that → treated as reporting
  noise, logged, and the run continues.
- `resets_at` missing on either side of a drop → can't verify either way, so this
  aborts too rather than silently trusting the smaller-drop branch.

## Auto-detected `--start` and its interaction with the baseline

When neither `--start` nor `--days` is given, the window doesn't default to "7 days
before `--end`" — that assumes both a clean weekly cadence and that "now" happens to
sit near a fresh reset, neither of which is guaranteed. Instead, the script finds the
most recent reading at or before `--end` and computes
`start = resets_at - window_minutes`: the exact start of the account's current,
still-open cycle.

This interacts with baseline resolution in a way that isn't obvious: normally the
baseline (the `used_percent` at `start`) is found by looking up the last reading at or
before `start`. But when `start` **is** a detected reset boundary, that lookup would
find a reading from the *previous* cycle — which necessarily has a different
`resets_at` and would trip the unconditional reset check above on every single
auto-detected run. So when `start` came from auto-detection, the baseline is set to
`0.0` directly, by construction, skipping the lookup entirely — this isn't a fallback
assumption like `--assume-fresh-start`, it's exact given how `start` was derived. See
`start_is_detected_reset` in `main()`.

## Replay bursts (session resume / context compaction) — not excluded, and don't try to

Codex can rewrite an *entire prior session's* history into a new rollout file within
milliseconds — observed directly: one file contained 73 `token_count` events spanning
10ms of wall-clock time, with the cumulative counter climbing to 8.25M tokens in that
span. It's tempting to treat this as double-counting and exclude it. **Don't — this was
tried twice, and both times made `tokens_m` less accurate, not more.**

**Attempt 1: timing-based exclusion.** Readings within ~1s of each other in the same
account were flagged as a replay burst and dropped. On a real window with Settings ->
Usage and billing reading 192.5M, this took the unfiltered sum from 197.7M down to
181.3M — moving *away* from the true value (+5.2M error became -11.2M error).

**Attempt 2: `response_item.id`-based deduplication.** More principled: every
`response_item` record carries the server's own id (`msg_...`, `rs_...`, `ctc_...`,
`ctco_...`), and a forked file's ids were verified to match its parent's **exactly and
completely** (249/249 in the case examined) — genuine proof of replayed content, not a
timing guess. Readings whose `response_item`s were all confirmed duplicates of
already-seen ids were excluded (readings with no preceding `response_item` at all, e.g.
right after a `compacted` marker, were kept, since that's the compaction call's own
cost). On the same window this produced 181.9M — still worse than the unfiltered
197.7M, by almost exactly the same margin as attempt 1.

**Why unfiltered is right:** a duplicate `response_item.id` proves the *content* was
resent, not that the tokens were free the second time. An LLM API call re-sends the
full prior context on every turn and is billed for it again unless server-side prompt
caching applies — and caching itself isn't free, it has separate (lower, but non-zero)
read/write rates. So replaying old history into a new file for a resume/fork/compaction
is a second, real, billable call, and excluding it drops real usage from `tokens_m`.
This is also why compaction itself isn't free — direct evidence: the `compacted`
event's `replacement_history` contains an entry with `"type": "compaction"` and its own
`encrypted_content`, the same shape the Responses API uses for a real model response's
encrypted reasoning.

`collect_readings()` sums every `token_count` reading's `last_token_usage` with no
exclusion. If you're tempted to reintroduce one, get evidence it moves `tokens_m`
*closer* to Settings -> Usage and billing on a real window first — twice now, the
opposite happened.

## Daily breakdown buckets by UTC day, not local time

`daily_token_breakdown()` groups by the UTC calendar date, not the machine's local
timezone. This was originally local-day, and it was wrong: bucketing by local day
shifted tokens between adjacent days by however far local time sits from UTC (e.g. two
hours for Europe/Warsaw), which showed up as two adjacent days each being wildly off
from Settings → Usage and billing while their *sum* matched almost exactly — a
textbook symptom of a shifted bucket boundary, not a data problem. Switching to UTC
days brought individual days back in line with the UI (one day matched to within
0.1M). Codex's UI appears to bucket by UTC date internally, though this isn't
documented anywhere — treat it as inferred, not confirmed.

The window's first and/or last day is usually partial (the window starts/ends at the
account's exact reset time, not UTC midnight), so it legitimately won't match the
UI's full-day total for that day — that's expected, not a bug.

## `--full-utc-days`: diagnostic only, never feeds the CSV row

This flag widens the *reported* breakdown to whole UTC calendar days covering
`[start, end]`, for comparing against a UI that seems to show whole days. It
deliberately does **not** change `tokens_m`/`limit_used` in the actual CSV row:
widening the real measurement window could pull in usage from before the account's
actual reset, mixing two different limit cycles into one observation — which
CONTRIBUTING.md explicitly forbids submitting. Keep this flag output- and
diagnostics-only; don't let it leak into the values used to build `csv_row`.

In practice, this flag doesn't always explain a gap: tested against a real window
where the reported day-1 total was noticeably lower than the equivalent UI figure, it
turned out there was *zero* logged activity before the actual reset that day — the
extra hours it covers were legitimately empty, so widening changed nothing. The
residual difference against the UI in that case is still unexplained. Don't assume
this flag will close a gap; it's a way to test one specific hypothesis about how
Codex's UI buckets time, not a general reconciliation tool.

## Known limitations, summarized

- Depends on an undocumented, internal log format that can change between Codex CLI
  versions without notice — if this script starts erroring or producing obviously
  wrong numbers after a Codex update, the format is the first thing to check (`codex
  migrate-rollouts` existing as a CLI command is itself a sign the on-disk format has
  changed before).
- Depends on local log retention — old sessions can be gone by the time you look.
- `plan_type` is frequently absent from local logs — the row still gets built, but with
  `plan` blank and a warning; pass `--plan` to fill it in automatically instead.
- Multiple accounts/workspaces on one machine need `--limit-id` to disambiguate.
- Even with every fix above, the computed `tokens_m` is not guaranteed to match
  Codex's own UI exactly — a real residual gap (~2.7%, tokens_m reading *higher* than
  the UI) was observed in testing and never fully explained. Ruled out so far: day/UTC
  bucketing (narrowed individual days but not the window total), the account being used
  on another device during the window (confirmed not the case), missing/dropped
  `token_count` events (checked by comparing summed `last_token_usage` against
  `total_token_usage` per file — see "Key fields" above; that comparison turned out to
  be invalid rather than revealing a gap, since the two fields measure different
  things), and excluding replayed/duplicated readings (see "Replay bursts" above — this
  made the gap larger, not smaller, so it's excluded as a cause). Always cross-check
  against Settings → Usage and billing before submitting a PR — the script's own output
  reminds you of this on every run.
