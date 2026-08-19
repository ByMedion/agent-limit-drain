# Scripts

## `build.py`

Build-time pipeline for the project: validates the raw CSV files under `data/`, aggregates observations once, and generates the GitHub Pages artifact (`build/`) — `stats.json`, `drain-factor.svg`, and the static site files. See the main [README.md](../README.md) for the aggregation rules.

```bash
uv run scripts/build.py
```

## `collect_codex_observation.py`

Recommended way to collect a Codex observation for a data pull request — reads Codex's local session logs and prints a ready-to-paste CSV row when the data is unambiguous. See [docs/collecting-data/codex.md](../docs/collecting-data/codex.md) for the full guide, including the manual fallback method.

```bash
uv run scripts/collect_codex_observation.py --start 2026-08-16T20:15 --end 2026-08-22T20:15
```

Run `uv run scripts/collect_codex_observation.py --help` for the full option list.

See [`collect_codex_observation.md`](collect_codex_observation.md) for implementation notes — the log format's gotchas, why several checks are structured the way they are, and known limitations.

### Tests

Unit tests live in [`tests/collect_codex_observation/`](../tests/collect_codex_observation/), stdlib `unittest` only — no extra dependencies to install.

```bash
python3 -m unittest discover -s tests
```
