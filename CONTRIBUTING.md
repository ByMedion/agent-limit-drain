# Contributing

Agent Limit Drain is community-driven.

> [!IMPORTANT]
> **Data quality is more important than the number of observations.**
> Please be as accurate and honest as possible when submitting data. Only contribute measurements you are confident are correct. If you are unsure whether token usage, models, reasoning settings, plans, or limit periods were mixed, do not submit the observation.
>
> This is about being confident in *what* you measured — no mixed models/plans/limits, no reconstructing values from memory. A small, unavoidable measurement error in the token count itself (from tooling, rounding, or the provider's own UI precision) is expected and fine — see [Measurement requirements](#measurement-requirements) below for what's strict and what isn't.

> [!WARNING]
> **Token composition can affect the result.**
> Agents may not expose a reliable breakdown between input, cached input, output, and reasoning tokens, and providers may account for these categories differently. Use the total token count reported by the agent, but keep in mind that workload/token composition can influence Drain Factor. Do not attempt to estimate or reconstruct token categories that the agent does not report.

> [!WARNING]
> **Resets, credit top-ups, and other changes to limit accounting invalidate an observation.**
> Do not submit a measurement if the relevant usage limit reset during the measurement interval, if purchased/additional credits were used, or if any other event changed how usage was charged against the reported 0–100% limit. If you are not certain the interval stayed within one uninterrupted limit window, do not contribute it.

You do not need to run a standardized benchmark to contribute. The project primarily collects real-world observations of token usage versus reported usage-limit consumption.

Contributions for agents, models, providers, and plans not currently covered by the maintainer are especially welcome.

## What to contribute

The preferred contribution is a new observation added to the appropriate monthly CSV file under `data/YYYY/`.

If you are contributing Codex data, follow [Collecting Data from Codex](docs/collecting-data/codex.md) before recording the observation.

Each observation should describe one measurement interval using a single provider, agent, model, reasoning configuration, plan, limit type, and uninterrupted usage-limit window.

The measurement interval does **not** need to match the provider's full weekly/reset window. It may cover any portion of that window as long as token usage and limit consumption refer to exactly the same interval and no reset or accounting change occurs within it.

Each data pull request must contain exactly **one observation**. Submit only measurements for which you still have the original token and limit-usage data; do not reconstruct historical observations from memory.

Store the observation in the monthly CSV corresponding to its `measurement_end` date.

To submit an observation:

1. Fork the repository.
2. Add the observation to the appropriate monthly CSV file under `data/YYYY/`.
3. Commit the change to a branch in your fork.
4. Open a pull request against `main` using the **Data Observation** pull request template.

## CSV schema

```csv
measurement_start,measurement_end,provider,agent,model,reasoning,tokens_m,limit_used,plan,limit_type
2026-08-16,2026-08-22,openai,codex,gpt-5.6-terra,high,176.4,92,plus,weekly
```

Fields:

- `measurement_start` — measurement start date in ISO format (`YYYY-MM-DD`)
- `measurement_end` — measurement end date in ISO format (`YYYY-MM-DD`)
- `provider` — AI provider, e.g. `openai`
- `agent` — coding agent/product, e.g. `codex`
- `model` — model used during the measurement
- `reasoning` — reasoning effort/configuration
- `tokens_m` — tokens processed during the interval, in millions
- `limit_used` — percentage points of the reported usage limit **consumed** during the same interval. Agents/providers often show remaining limit instead — if so, this is `100 - remaining_percent`, not the number shown directly.
- `plan` — subscription or usage plan, e.g. `plus`
- `limit_type` — the specific reported limit being measured, e.g. `weekly`, `rolling_5h`, or `model_weekly`

Do not store Drain Factor in the CSV. It is calculated as:

```text
drain_factor = tokens_m / limit_used
```

Calendar-week buckets used by the website are derived during the build from the midpoint of `measurement_start` and `measurement_end` and are not stored in the raw CSV.

## Measurement requirements

These fall into two kinds: things that make an observation invalid outright, and things where a small, honest margin of error is expected and fine.

### Never acceptable — skip the observation instead

- Mixing more than one provider, agent, model, reasoning configuration, plan, or limit type within a single observation — including mixing two independent limits that happen to share a limit type (e.g. separate per-model weekly quotas).
- A measurement interval that isn't entirely within one uninterrupted usage-limit window (a reset, credit top-up, or other accounting change happened inside it).
- Token usage and limit consumption that don't describe the same interval.
- Reconstructing a value from memory instead of data you still have.
- Usage paid for with purchased/additional credits, or any other usage not represented by the same reported percentage limit.

If you're unsure whether any of the above applies, don't submit — data quality matters more than observation count.

### Best effort is fine — small, explainable imprecision is expected

- The exact token count. Tooling, daily-boundary rounding (see the per-agent collecting guide), and the provider's own UI precision all introduce a small margin of error. A few percent of drift against the source, once cross-checked, is not a reason to discard an otherwise-valid observation.
- Very small measurement intervals are allowed but tend to be noisy — prefer a meaningful portion of the limit when practical.
- Record the start and end of the actual measurement interval, and the number of percentage points of the relevant usage limit consumed, as precisely as your source data allows — but "as precisely as the source allows" is the bar, not perfection.

Use real measured values rather than estimates whenever possible. A standardized workload is not required — the project is intended to track the observable relationship between token usage and reported limit consumption in real-world use.

## Evidence

Each pull request that adds data should say where the numbers came from and confirm nothing invalidated the measurement — the **Source** and **Limit integrity** fields in the pull request template cover this. Provider, agent, model, reasoning, plan, limit type, measurement dates, `tokens_m`, and `limit_used` don't need repeating there — they're already in the CSV row itself.

## Pull request guidelines

Keep data pull requests focused.

A typical data contribution should contain exactly one new observation in the relevant monthly CSV file.

CI runs the build and validates the CSV automatically on every pull request — you don't need to run anything locally before submitting, and a red check will tell you if a field is malformed.

## Interpretation

Agent Limit Drain measures an observable relationship between reported limit consumption and token usage.

It does **not** claim to prove why that relationship changed or whether a provider intentionally changed an undocumented policy.

Changes may reflect provider-side accounting, model behavior, reasoning configuration, caching, product changes, or other factors.

The dataset is intended to make those changes measurable rather than anecdotal.
