# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "plotly>=6.0.0",
#     "kaleido>=1.3.0",
# ]
# ///

"""Agent Limit Drain build script.

Pipeline:

    raw CSV  ->  validate  ->  aggregate (once)  ->  stats.json + drain-factor.svg

Raw monthly CSV files under ``data/`` are the canonical dataset. They are never
deployed; the build produces a single compact aggregate dataset that is used both
for the static README SVG preview and for the GitHub Pages frontend, so the two
cannot drift statistically.
"""

from __future__ import annotations

import csv
import json
import re
import shutil
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Tuple

import plotly.graph_objects as go


MONTH_NAMES = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"
]

REQUIRED_FIELDS = [
    "measurement_start",
    "measurement_end",
    "provider",
    "agent",
    "model",
    "reasoning",
    "tokens_m",
    "limit_used",
    "plan",
    "limit_type",
]

# Identity fields of a series / aggregation group (period is added separately).
IDENTITY_FIELDS = ("provider", "agent", "model", "reasoning", "plan", "limit_type")

MONTHLY_FILE_RE = re.compile(r"^(\d{4})-(\d{2})\.csv$")

PALETTE = [
    "#10b981",  # Emerald green
    "#3b82f6",  # Blue
    "#8b5cf6",  # Purple
    "#f59e0b",  # Amber
    "#ec4899",  # Pink
    "#06b6d4",  # Cyan
    "#ef4444",  # Red
    "#6366f1",  # Indigo
]


@dataclass(frozen=True)
class Observation:
    """One raw community observation, exactly as contributed."""

    measurement_start: date
    measurement_end: date
    provider: str
    agent: str
    model: str
    reasoning: str
    tokens_m: float
    limit_used: float
    plan: str
    limit_type: str
    source: str

    @property
    def drain_factor(self) -> float:
        return self.tokens_m / self.limit_used

    @property
    def midpoint(self) -> date:
        """Midpoint of the measurement interval (floored to a whole day)."""
        return date.fromordinal(
            (self.measurement_start.toordinal() + self.measurement_end.toordinal()) // 2
        )

    @property
    def week_start(self) -> date:
        """Monday of the calendar week containing the measurement midpoint."""
        mid = self.midpoint
        return mid - timedelta(days=mid.weekday())

    @property
    def week_end(self) -> date:
        return self.week_start + timedelta(days=6)

    @property
    def group_key(self) -> Tuple[str, ...]:
        """Aggregation key: calendar week + provider/agent/model/reasoning/plan/limit type.

        Identity fields are compared case-insensitively so that ``Plus`` and ``plus``
        from different contributors do not produce two separate series.
        """
        return (
            self.week_start.isoformat(),
            self.week_end.isoformat(),
            self.provider.lower(),
            self.agent.lower(),
            self.model.lower(),
            self.reasoning.lower(),
            self.plan.lower(),
            self.limit_type.lower(),
        )


@dataclass
class Aggregate:
    """Pooled statistics for one calendar week and one configuration."""

    period_start: date
    period_end: date
    provider: str
    agent: str
    model: str
    reasoning: str
    plan: str
    limit_type: str
    observations: List[Observation] = field(default_factory=list)

    @property
    def observation_count(self) -> int:
        return len(self.observations)

    @property
    def tokens_total_m(self) -> float:
        return sum(obs.tokens_m for obs in self.observations)

    @property
    def limit_used_total(self) -> float:
        return sum(obs.limit_used for obs in self.observations)

    @property
    def drain_factor(self) -> float:
        """Pooled Drain Factor: SUM(tokens_m) / SUM(limit_used).

        Deliberately not AVG(tokens_m / limit_used) — the pooled ratio weights
        each observation by the amount of usage limit it actually measured.
        """
        return self.tokens_total_m / self.limit_used_total

    @property
    def period_label(self) -> str:
        return format_period(self.period_start, self.period_end)

    @property
    def series(self) -> str:
        """Readable series label, unique per provider/agent/model/reasoning/plan/limit type."""
        return (
            f"{self.provider}/{self.agent} · {self.model} ({self.reasoning}) "
            f"· {format_plan(self.plan)} · {self.limit_type}"
        )

    def to_json(self) -> dict:
        return {
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "period_label": self.period_label,
            "provider": self.provider,
            "agent": self.agent,
            "model": self.model,
            "reasoning": self.reasoning,
            "plan": self.plan,
            "limit_type": self.limit_type,
            "series": self.series,
            "observation_count": self.observation_count,
            "drain_factor": round(self.drain_factor, 10),
        }


def format_period(start_date: date, end_date: date) -> str:
    """Formats a period into a clean human-readable string like 'Aug 17–23, 2026'."""
    s_mon = MONTH_NAMES[start_date.month - 1]
    e_mon = MONTH_NAMES[end_date.month - 1]

    if start_date.year == end_date.year:
        if start_date.month == end_date.month:
            return f"{s_mon} {start_date.day}–{end_date.day}, {start_date.year}"
        return f"{s_mon} {start_date.day}–{e_mon} {end_date.day}, {start_date.year}"
    return f"{s_mon} {start_date.day}, {start_date.year}–{e_mon} {end_date.day}, {end_date.year}"


def format_plan(plan: str) -> str:
    """Capitalizes a plan name for display (e.g. 'plus' -> 'Plus')."""
    return plan[:1].upper() + plan[1:] if plan else plan


def parse_and_validate_csv(file_path: Path) -> List[Observation]:
    """Parses and validates one monthly CSV file.

    Multiple independent observations sharing the same period and configuration are
    expected from community contributors and are explicitly *not* treated as duplicates.
    """
    observations: List[Observation] = []

    with file_path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError(f"Empty or missing header in {file_path}")

        missing = set(REQUIRED_FIELDS) - set(reader.fieldnames)
        if missing:
            raise ValueError(f"Missing required fields {sorted(missing)} in {file_path}")

        expected_month = MONTHLY_FILE_RE.match(file_path.name)

        for line_num, row in enumerate(reader, start=2):
            location = f"{file_path}:{line_num}"

            raw_start = (row["measurement_start"] or "").strip()
            raw_end = (row["measurement_end"] or "").strip()

            try:
                start_date = date.fromisoformat(raw_start)
                end_date = date.fromisoformat(raw_end)
            except (ValueError, AttributeError) as err:
                raise ValueError(f"Invalid ISO date format at {location}: {err}")

            if start_date > end_date:
                raise ValueError(
                    f"measurement_start ({start_date}) cannot be after "
                    f"measurement_end ({end_date}) at {location}"
                )

            if expected_month:
                exp_year, exp_mon = int(expected_month.group(1)), int(expected_month.group(2))
                if (end_date.year, end_date.month) != (exp_year, exp_mon):
                    raise ValueError(
                        f"Observation at {location} has measurement_end {end_date}; it belongs "
                        f"in data/{end_date.year}/{end_date:%Y-%m}.csv, not {file_path.name}"
                    )

            values: Dict[str, str] = {}
            for field_name in IDENTITY_FIELDS:
                value = (row[field_name] or "").strip()
                if not value:
                    raise ValueError(f"{field_name} must be non-empty at {location}")
                values[field_name] = value

            try:
                tokens_m = float((row["tokens_m"] or "").strip())
                limit_used = float((row["limit_used"] or "").strip())
            except ValueError as err:
                raise ValueError(f"Invalid numeric value at {location}: {err}")

            if tokens_m <= 0:
                raise ValueError(f"tokens_m must be > 0 at {location} (got {tokens_m})")

            # No upper bound on limit_used: a measurement may legitimately cover
            # more than a single limit cycle depending on future data semantics.
            if limit_used <= 0:
                raise ValueError(
                    f"limit_used must be > 0 at {location} (got {limit_used})"
                )

            observations.append(
                Observation(
                    measurement_start=start_date,
                    measurement_end=end_date,
                    tokens_m=tokens_m,
                    limit_used=limit_used,
                    source=f"{file_path.name}:{line_num}",
                    **values,
                )
            )

    return observations


def load_all_observations(data_dir: Path) -> List[Observation]:
    """Loads and validates every CSV file under data/."""
    if not data_dir.exists():
        raise FileNotFoundError(f"Data directory '{data_dir}' not found.")

    csv_files = sorted(data_dir.glob("**/*.csv"))
    if not csv_files:
        raise FileNotFoundError(f"No CSV files found under {data_dir}")

    all_obs: List[Observation] = []
    for csv_file in csv_files:
        all_obs.extend(parse_and_validate_csv(csv_file))

    print(f"✓ Validated {len(all_obs)} observation(s) from {len(csv_files)} CSV file(s)")
    return all_obs


def aggregate_observations(observations: List[Observation]) -> List[Aggregate]:
    """Pools observations by calendar week + provider/agent/model/reasoning/plan/limit type.

    Observations from different providers, agents, models, reasoning configurations,
    plans, or limit types are never merged.
    """
    groups: Dict[Tuple[str, ...], Aggregate] = {}

    for obs in observations:
        key = obs.group_key
        aggregate = groups.get(key)
        if aggregate is None:
            # The first observation of a group defines the display spelling.
            aggregate = Aggregate(
                period_start=obs.week_start,
                period_end=obs.week_end,
                provider=obs.provider,
                agent=obs.agent,
                model=obs.model,
                reasoning=obs.reasoning,
                plan=obs.plan,
                limit_type=obs.limit_type,
            )
            groups[key] = aggregate
        aggregate.observations.append(obs)

    aggregates = sorted(groups.values(), key=lambda a: (a.period_start, a.series))
    print(f"✓ Aggregated into {len(aggregates)} data point(s)")
    for agg in aggregates:
        print(
            f"  • {agg.period_label}: {agg.series} → "
            f"{agg.tokens_total_m:.1f}M / {agg.limit_used_total:g}% = "
            f"{agg.drain_factor:.3f} (n={agg.observation_count})"
        )
    return aggregates


def build_svg_figure(aggregates: List[Aggregate]) -> go.Figure:
    """Builds the static Plotly figure for the README SVG preview.

    Consumes the same aggregates that are serialized to stats.json for the website.
    """
    series_map: Dict[str, List[Aggregate]] = {}
    for agg in aggregates:
        series_map.setdefault(agg.series, []).append(agg)

    # Deterministic category order along the time axis.
    categories = sorted(
        {agg.period_start: agg.period_label for agg in aggregates}.items()
    )
    category_array = [label for _, label in categories]

    fig = go.Figure()

    for idx, series_name in enumerate(sorted(series_map.keys())):
        points = series_map[series_name]
        color = PALETTE[idx % len(PALETTE)]

        fig.add_trace(
            go.Scatter(
                x=[p.period_label for p in points],
                y=[round(p.drain_factor, 3) for p in points],
                name=series_name,
                mode="lines+markers",
                marker=dict(
                    size=10,
                    symbol="circle",
                    color=color,
                    line=dict(width=2, color="#ffffff"),
                ),
                line=dict(
                    width=3,
                    color=color,
                ),
            )
        )

    layout_font_family = (
        '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif'
    )

    fig.update_layout(
        title=dict(
            text="Agent Limit Drain Factor Over Time",
            font=dict(size=18, family=layout_font_family, color="#1e293b"),
            x=0.02,
            y=0.95,
        ),
        xaxis=dict(
            title=dict(
                text="Week",
                font=dict(size=13, family=layout_font_family, color="#475569"),
            ),
            tickfont=dict(size=12, family=layout_font_family, color="#334155"),
            showgrid=True,
            gridcolor="#e2e8f0",
            gridwidth=1,
            linecolor="#cbd5e1",
            zeroline=False,
            type="category",
            categoryorder="array",
            categoryarray=category_array,
        ),
        yaxis=dict(
            title=dict(
                text="Drain Factor (M tokens / 1% limit)",
                font=dict(size=13, family=layout_font_family, color="#475569"),
            ),
            tickfont=dict(size=12, family=layout_font_family, color="#334155"),
            showgrid=True,
            gridcolor="#e2e8f0",
            gridwidth=1,
            linecolor="#cbd5e1",
            zeroline=False,
            rangemode="tozero",
        ),
        showlegend=True,
        legend=dict(
            font=dict(size=11, family=layout_font_family, color="#334155"),
            bgcolor="rgba(255, 255, 255, 0.9)",
            bordercolor="#e2e8f0",
            borderwidth=1,
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1,
        ),
        paper_bgcolor="#ffffff",
        plot_bgcolor="#f8fafc",
        margin=dict(l=65, r=30, t=85, b=65),
        width=860,
        height=480,
    )

    return fig


def main() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    data_dir = repo_root / "data"
    site_dir = repo_root / "site"
    build_dir = repo_root / "build"

    print("→ Discovering and validating monthly CSV files...")
    observations = load_all_observations(data_dir)

    print("→ Aggregating observations...")
    aggregates = aggregate_observations(observations)

    print("→ Preparing fresh build directory...")
    if build_dir.exists():
        shutil.rmtree(build_dir)
    build_dir.mkdir(parents=True, exist_ok=True)

    print("→ Copying static site files...")
    if not site_dir.exists():
        raise FileNotFoundError(f"Site directory '{site_dir}' not found.")
    for site_file in site_dir.iterdir():
        if site_file.is_file():
            shutil.copy2(site_file, build_dir / site_file.name)
            print(f"  • Copied {site_file.name}")

    # Raw CSV files are intentionally not deployed — the repository is their home.
    print("→ Generating build/stats.json...")
    stats = [agg.to_json() for agg in aggregates]
    (build_dir / "stats.json").write_text(
        json.dumps(stats, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"✓ Written {len(stats)} aggregate(s)")

    print("→ Generating build/drain-factor.svg preview...")
    svg_fig = build_svg_figure(aggregates)
    svg_fig.write_image(str(build_dir / "drain-factor.svg"), width=860, height=480)
    print("✓ Generated SVG preview")

    print("\n🎉 Build completed successfully! Deployment artifacts ready in build/")


if __name__ == "__main__":
    main()
