# Agent Limit Drain

Track how much actual model usage AI providers give you for their reported usage limits — and how that changes over time.

## 🎯 Mission

AI coding agents typically expose usage limits as a percentage from 0% to 100%, while the actual amount of model usage represented by that percentage is opaque.

Users often notice that the same limits seem to drain faster or last longer over time, but these observations are usually anecdotal.

**Agent Limit Drain** turns this into a measurable time series by tracking actual token usage against reported limit consumption.

The goal is not to infer undocumented provider policies, but to measure how the observable relationship between token usage and reported limits changes over time.

Contributions for any provider, agent, model, or plan are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md).

> [!IMPORTANT]
> The dataset is based on community observations, not official provider data. Measurements are reviewed for consistency, but may still contain errors or inaccuracies.

## 📈 Statistics

[![Drain Factor](https://bymedion.github.io/agent-limit-drain/drain-factor.svg)](https://bymedion.github.io/agent-limit-drain/)

👉 Click the chart to open the **interactive dashboard**, with hover details and the full aggregated dataset.

Each line represents one independent combination of:

- **Provider**
- **Agent**
- **Model**
- **Reasoning Effort**
- **Plan**

---

## 📊 Drain Factor

**Drain Factor** measures how many million tokens are processed per 1% of the reported usage limit consumed.

$$
\text{Drain Factor} =
\frac{\text{Tokens Used (M)}}{\text{Usage Limit Consumed}}
$$

For example, **44.1M tokens** and **23% limit consumption**:

$$
\text{Drain Factor} =
\frac{44.1}{23}
\approx 1.917
$$

**Drain Factor = 1.917M tokens / 1% limit**

A higher value means more token usage is available per percentage point of the reported limit.

> [!NOTE]
> Total token counts are used as reported by each agent. Input, cached input, output, and reasoning tokens may affect provider limits differently, while some agents do not expose a reliable breakdown between these categories. Changes in workload or token composition can therefore affect Drain Factor even when the provider's underlying limit policy has not changed.

## 📐 Aggregation

Provider reset windows are not assumed to align with calendar weeks. Each raw observation keeps its actual measurement interval and may cover any uninterrupted portion of a provider limit window.

Different products may expose multiple independent limits, such as weekly, rolling, or model-specific limits. Observations are only combined when they refer to the same **limit type**.

For the time series, an observation is assigned to the calendar week containing the **midpoint of its measurement interval**. This avoids systematically shifting longer measurements toward their end date. Multiple observations in the same week and configuration are pooled by summing tokens used and percentage points of limit consumed:

$$
\text{Aggregated Drain Factor} =
\frac{\sum \text{Tokens Used (M)}}{\sum \text{Usage Limit Consumed}}
$$

Observations are grouped by calendar week, provider, agent, model, reasoning configuration, plan, and limit type.

This is a pooled ratio, not an average of individual Drain Factors. Observations covering a larger share of the usage limit therefore contribute proportionally more to the result. The number of independent observations included in each aggregated point is shown as **n**.

## 🤝 Contributing

This project is community-driven — see [CONTRIBUTING.md](CONTRIBUTING.md) for how to submit a new observation.

If you're contributing Codex data, start with [Collecting Data from Codex](docs/collecting-data/codex.md).
