# CLAUDE.md

## Two documentation audiences

This project has two distinct kinds of contributor, and documentation must stay separated by which one it serves:

- **Data contributors** submit observations (a CSV row) via PR. They read `README.md`, `CONTRIBUTING.md`, `docs/collecting-data/*.md`, and the PR template. They do not touch code.
- **Code contributors** work on the project's implementation — the build pipeline, frontend, or data-collection scripts. They read `scripts/README.md` and code comments.

Do not cross-reference one audience's docs from the other's. A data-contributor doc should never mention tests, internal code structure, or how a script's code is organized — a data contributor has no reason to run or care about that. A code-contributor doc should not duplicate the data-contribution workflow that already lives in `CONTRIBUTING.md`.

When adding or editing documentation, decide which audience it's for first, then place it accordingly — even a single sentence or a single link pointing across the boundary reintroduces the mixing this rule exists to prevent.

## Running tests

Run `python3 -m unittest discover -s tests` in its default (non-verbose) form first. The summary line (`Ran N tests ... OK`) is enough to confirm a pass, and a failure prints its own traceback automatically without `-v`. Don't add `-v` or pipe the output through `tail` on a pass-or-fail-unknown run — that just dumps every test name into context for no benefit. Only reach for `-v` after a run has already failed and you need to see which test broke and why.

## Don't duplicate a command's own `--help`

When a script has a real `--help` (or equivalent), point docs at it instead of enumerating its flags. A flag list in prose goes stale the moment an argument is added, renamed, or removed in code — `--help` can't drift from the implementation because argparse generates it from the same source. "Run `X --help` for the full option list" is the complete, correct doc; appending `(--foo, --bar, --baz)` after it is redundant duplication with an extra maintenance burden, not extra clarity.
