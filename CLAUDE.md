# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

**Agent Limit Drain** (repo/dir name is still `codex-limits-drain-stats`) tracks the *Drain Factor* — million tokens processed per 1 percentage point of a provider's reported usage limit — from community-submitted observations, and publishes a static GitHub Pages dashboard plus an SVG preview embedded in the README.

Data flows one way, with no backend and no frontend framework:

```
data/YYYY/YYYY-MM.csv   (raw observations, canonical, hand-edited via PRs)
        ↓ scripts/build.py — validate + aggregate
build/                  (generated Pages artifact; gitignored, never commit)
        ├── SVG preview  (Plotly + Kaleido, server-side)
        └── site/* + generated dataset consumed by the browser
```

`build/` is in `.gitignore`; the copy currently on disk is stale local output. Never hand-edit anything under `build/`.

## Commands

Build the site and SVG preview (validates all CSVs as a side effect — this is the only test/lint gate the project has):

```bash
uv run scripts/build.py
```

`scripts/build.py` carries PEP 723 inline dependency metadata that mirrors `pyproject.toml`, so `uv run` needs no venv. CI (`.github/workflows/pages.yml`) runs exactly this command; a build failure is the CI failure. Pages deploys only from `main`; pushes to `develop` build but do not deploy.

## Repository state: docs are ahead of the code

**`uv run scripts/build.py` currently fails on the committed data.** This is the known state of the working branch, not a regression to hunt down:

- `data/2026/2026-08.csv`, `CONTRIBUTING.md`, and `docs/collecting-data/codex.md` use the current schema: `measurement_start,measurement_end,provider,agent,model,reasoning,tokens_m,limit_percent,plan,limit_type`.
- `scripts/build.py` and `site/app.js` still expect the older `period_start`/`period_end` columns and ignore `provider`, `agent`, and `limit_type` entirely. The build aborts on the missing-field check in `parse_and_validate_csv`.

`PROJECT_UPDATE_PROMPT.md` is the specification for the pending migration that closes this gap. Treat it as the requirements doc for build/frontend work; it is not documentation of what exists. Note that it itself predates the `measurement_*`/`limit_type` rename, so the real target schema is the one in `CONTRIBUTING.md` and the CSV, not the one quoted in that prompt.

`README.md` and `CONTRIBUTING.md` are the source of truth for project intent. Do not rewrite them to match the code; change the code to match them.

## Semantics that are easy to get wrong

These are deliberate modeling decisions, not implementation details to optimize away:

- **Drain Factor aggregation is a pooled ratio**: `SUM(tokens_m) / SUM(limit_percent)` over a group. It is explicitly *not* `AVG(tokens_m / limit_percent)` — pooling weights each observation by how much limit it actually measured. The count of pooled observations is surfaced to users as **n**.
- **Grouping key** for a chart series / aggregate row: period bucket + `provider` + `agent` + `model` + `reasoning` + `plan` + `limit_type`. Observations from different providers, agents, models, reasoning settings, plans, or limit types must never merge.
- **Week bucketing** uses the calendar week containing the *midpoint* of `measurement_start`..`measurement_end`, so long intervals are not systematically pushed toward their end date. The bucket is derived at build time and is never stored in the CSV.
- **Drain Factor is never stored** in the CSV — it is always derived (`tokens_m / limit_percent`).
- **Duplicate observations are legitimate.** The existing `seen_observations` duplicate rejection in `scripts/build.py` contradicts the community model: several contributors may independently report the same period and configuration. That check is slated for removal (see `PROJECT_UPDATE_PROMPT.md` §2); keep schema/value validation.
- **Do not enforce `limit_percent <= 100`.** An accumulated measurement may legitimately exceed one limit cycle.
- Aggregates are community observations, not provider-official numbers. Wording in the UI and docs should not imply otherwise, and the project measures the *observable relationship* between tokens and reported limits without claiming to prove provider policy changes.

## Frontend conventions

- `site/` is copied verbatim into `build/`; there is no bundler. Plotly.js (and, until the migration lands, Papa Parse) load from CDN in `site/index.html`.
- Keep dataset processing in Python. The browser should fetch the pre-aggregated build output, format labels, build Plotly traces, and render the table — nothing more.
- `site/app.js` builds the DOM with `document.createElement` / `textContent` rather than `innerHTML`. Preserve that.
- The color palette in `site/app.js` (`PALETTE`) and `scripts/build.py` (`colors` in `build_svg_figure`) are duplicated on purpose so the SVG and the interactive chart match. Change both together, and keep series ordering deterministic (alphabetical by series name) in both.
- The SVG and the website must render the same aggregates — Python computes them once; JavaScript must not recompute statistics independently.
- Chart titles, page title, footer, and the Plotly export filename should say *Agent Limit Drain*, not *Codex*. Several of these are still Codex-branded.

## Contributing data

Data PRs add **exactly one** observation to the monthly CSV matching its `measurement_end`, using the **Data Observation** PR template (`.github/PULL_REQUEST_TEMPLATE/data_observation.md`) and targeting `main`. `CONTRIBUTING.md` holds the full field reference and validity rules; `docs/collecting-data/codex.md` covers the Codex-specific collection procedure (Codex reports percent *remaining*, so `limit_percent = 100 - remaining`).

`TODO.md` tracks planned features (a client-side Drain Factor calculator; variability visualization once sample sizes support it).
