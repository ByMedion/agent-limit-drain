# Collecting Data from Codex

This guide describes how to collect a Codex observation for Agent Limit Drain.

The goal is to compare the total token usage during one Codex weekly limit window with the percentage of that weekly limit consumed.

> [!IMPORTANT]
> Use one complete, uninterrupted weekly limit window. Do not reconstruct old observations from memory.

> [!WARNING]
> Do not submit the observation if the weekly limit was reset early, purchased/additional credits were used, automatic credit top-up supplied additional usage, or anything else changed how usage was charged during the window.

## Recommended: run the script

[`scripts/collect_codex_observation.py`](../../scripts/collect_codex_observation.py) reads Codex's local session logs (`~/.codex/sessions` and `~/.codex/archived_sessions`) and, when the data for a window is unambiguous, prints a ready-to-paste CSV row and evidence block:

```bash
uv run scripts/collect_codex_observation.py --start 2026-08-16T20:15 --end 2026-08-22T20:15
```

Run `uv run scripts/collect_codex_observation.py --help` for the full option list.

> [!WARNING]
> This depends on local log retention — Codex archives inactive sessions and can eventually delete them from disk. Run the script soon after the window you want to record ends, not to reconstruct old windows.

If the data is missing, ambiguous (mixed models/accounts, a reset inside the window), or the script errors out — for example because Codex changed its local log format — it prints what it found instead of guessing. Fall back to the manual method below in that case.

> [!NOTE]
> The script's `tokens_m` is a close approximation, not an exact replay of Codex's own accounting — expect a small gap (a few percent) against **Settings → Usage and billing** even on a clean window. Always compare the printed number against the UI before submitting. A small, explainable gap is fine; if it's large or you can't account for it, don't submit — fall back to the manual method or skip the observation.

## Manual method

The steps below are what the script automates. Read them to understand what it computes, to collect a window by hand when the script can't, or if the script breaks because Codex changed its local log format.

### 1. Identify the Weekly Limit Window

Open Codex **Settings → Usage and billing** and find **Weekly usage limit**.

Codex shows:

- the percentage of the weekly limit **remaining**
- the next reset date and time

For example, if Codex shows:

```text
8% remaining
Reset: Aug 22, 2026, 20:15
```

then:

```text
limit_used = 100 - 8 = 92
```

The observation represents the weekly limit window ending at that reset.

Codex does not need to reset on a calendar-week boundary. Use the reset window shown for the account rather than assuming Monday–Sunday or another fixed calendar week.

### 2. Get Token Usage

Use Codex token statistics to collect the token usage covering the same weekly limit window.

Record the total in **millions of tokens** — this is the `tokens_m` CSV field.

If token statistics are available by day, sum the daily token totals that overlap the weekly limit window.

For example:

```text
Weekly limit window:
Aug 16 → Aug 22

Daily token usage:
Aug 16   18.2M
Aug 17   21.4M
Aug 18   16.7M
Aug 19   14.1M
Aug 20   12.8M
Aug 21   20.3M
Aug 22   17.6M
         -----
Total   121.1M
```

Use:

```text
tokens_m = 121.1  # millions of tokens
```

#### Boundary days

The weekly reset can occur in the middle of a calendar day, while token statistics may only be available as daily totals.

In that case, include the overlapping boundary day as a whole.

This introduces some measurement error, but keeps data collection practical and consistent. Do not manually estimate partial-day token counts.

With many independent observations, these boundary effects are expected to contribute noise rather than requiring contributors to reconstruct token usage at exact reset timestamps.

### 3. Convert Remaining Limit to Consumed Limit

Codex reports the percentage **remaining**, while Agent Limit Drain stores the percentage **consumed**.

Calculate:

```text
limit_used = 100 - remaining_percent
```

Example:

```text
Codex: 8% remaining

limit_used = 100 - 8
           = 92
```

Store:

```text
limit_used = 92
```

Use the value actually shown by Codex. Do not estimate a more precise percentage than the UI provides.

### 4. Check Credits and Resets

Before submitting, verify in **Settings → Usage and billing** that the observation was not affected by additional credits or a reset outside the normal weekly window.

A valid observation should not include usage supplied by:

- purchased credits
- automatic credit top-up
- an early/manual/banked usage-limit reset
- another mechanism that changes how usage is charged against the normal weekly percentage limit

If any of these occurred during the weekly window, skip the observation.

### 5. Drain Factor

Drain Factor is calculated as:

```text
drain_factor = tokens_m / limit_used
```

For example:

```text
121.1 / 92 = 1.316
```

or:

```text
Drain Factor = 1.316M tokens / 1% limit
```

You do not need to calculate or store Drain Factor in the CSV. The project calculates it automatically.

### 6. Token Composition

Use the total token count reported by Codex.

Do not attempt to manually separate or estimate input, cached input, output, or reasoning tokens when Codex statistics do not provide a reliable breakdown.

Different token categories may affect provider-side accounting differently, so workload composition can introduce additional variation into Drain Factor. This is a known limitation of the dataset.

### 7. When Not to Submit

Do not contribute the observation if:

- the weekly limit reset unexpectedly or was manually/banked reset during the window
- purchased credits or automatic credit top-up supplied additional usage
- another product or feature consumed the same reported limit and its token usage is not included in your token statistics
- you switched provider, agent, model, reasoning configuration, plan, or limit type during the observation
- you are unsure which daily token totals overlap the relevant weekly window
- token statistics are incomplete
- the remaining-limit percentage is reconstructed from memory
- you are otherwise unsure that the token total and limit consumption describe the same usage

When in doubt, skip the observation. Data quality is more important than the number of observations.

### 8. CSV Example

For a weekly window ending August 22, with **8% remaining** and **121.1M tokens**:

```text
Consumed limit = 100 - 8 = 92%
```

```csv
measurement_start,measurement_end,provider,agent,model,reasoning,tokens_m,limit_used,plan,limit_type
2026-08-16,2026-08-22,openai,codex,gpt-5.6-terra,high,121.1,92,plus,weekly
```

Submit exactly one observation per data pull request and include the evidence requested by the Data Observation pull request template.
