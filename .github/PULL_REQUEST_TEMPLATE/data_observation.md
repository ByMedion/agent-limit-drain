# Data Observation

> One pull request = one observation.
>
> Submit only measurements for which you still have the original token and limit-usage data. Do not reconstruct historical observations from memory.

The CSV row added by this PR *is* the observation — provider, agent, model, reasoning, plan, limit type, measurement dates, tokens, and limit consumed are all there in the diff.

## Source

**Source:**  
<!-- Where did the numbers come from? Example: "scripts/collect_codex_observation.py" or "manual — Codex usage UI". -->

## Limit integrity

**Did the relevant usage limit reset during the measurement?** No  
**Were purchased/additional credits used during the measurement?** No  
**Did anything else change how usage was charged against the reported limit?** No  

<!-- If any answer is Yes or you are unsure, do not submit this observation. -->

## Checklist

- [ ] This PR adds exactly one observation, using a single provider/agent/model/reasoning/plan/limit type.
- [ ] Token usage and limit consumption describe exactly the same interval, within one uninterrupted limit window.
- [ ] No purchased/additional credits were used.
- [ ] No other event changed how usage was charged against the reported limit.
- [ ] The values come from original measurement data, not memory or estimates.
