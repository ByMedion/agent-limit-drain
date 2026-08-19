/**
 * Agent Limit Drain — Client-side Application
 *
 * Fetches the build-time aggregate dataset (stats.json) and renders it with Plotly.js
 * using safe DOM APIs. Validation and aggregation happen in scripts/build.py, so the
 * browser only formats values, builds traces and renders the table.
 */

// Color palette (matches scripts/build.py palette)
const PALETTE = [
  '#10b981', // Emerald green
  '#3b82f6', // Blue
  '#8b5cf6', // Purple
  '#f59e0b', // Amber
  '#ec4899', // Pink
  '#06b6d4', // Cyan
  '#ef4444', // Red
  '#6366f1', // Indigo
];

/**
 * Capitalizes a plan name for display (e.g. "plus" -> "Plus").
 */
function formatPlan(planStr) {
  if (!planStr) return '';
  return planStr.charAt(0).toUpperCase() + planStr.slice(1);
}

/**
 * Main initialization and data loading workflow.
 */
async function init() {
  try {
    const res = await fetch('stats.json');
    if (!res.ok) {
      throw new Error(`Failed to load stats.json (status: ${res.status})`);
    }

    const aggregates = await res.json();
    if (!Array.isArray(aggregates) || aggregates.length === 0) {
      throw new Error('No aggregated statistics available.');
    }

    // stats.json is already sorted by period, but do not rely on it for rendering.
    aggregates.sort((a, b) =>
      a.period_start.localeCompare(b.period_start) || a.series.localeCompare(b.series)
    );

    renderChart(aggregates);
    renderTable(aggregates);
  } catch (err) {
    console.error(err);
    showError(err.message);
  }
}

/**
 * Replaces the chart placeholder with an error message using safe DOM APIs.
 */
function showError(message) {
  const chartContainer = document.getElementById('chart');
  chartContainer.textContent = '';
  const errDiv = document.createElement('div');
  errDiv.className = 'loading-state';
  errDiv.style.color = '#ef4444';
  errDiv.textContent = `⚠️ Error loading statistics: ${message}`;
  chartContainer.appendChild(errDiv);
}

/**
 * Renders the aggregated statistics table, most recent period first.
 */
function renderTable(aggregates) {
  const tbody = document.getElementById('table-body');
  tbody.textContent = '';

  const reversed = [...aggregates].reverse();
  for (const agg of reversed) {
    const tr = document.createElement('tr');

    const tdPeriod = document.createElement('td');
    const strongPeriod = document.createElement('strong');
    strongPeriod.textContent = agg.period_label;
    tdPeriod.appendChild(strongPeriod);

    const tdProvider = document.createElement('td');
    tdProvider.textContent = agg.provider;

    const tdAgent = document.createElement('td');
    tdAgent.textContent = agg.agent;

    const tdModel = document.createElement('td');
    const codeModel = document.createElement('code');
    codeModel.textContent = agg.model;
    tdModel.appendChild(codeModel);

    const tdReasoning = document.createElement('td');
    const badge = document.createElement('span');
    badge.className = `badge badge-${agg.reasoning.toLowerCase()}`;
    badge.textContent = agg.reasoning;
    tdReasoning.appendChild(badge);

    const tdPlan = document.createElement('td');
    tdPlan.textContent = formatPlan(agg.plan);

    const tdLimitType = document.createElement('td');
    tdLimitType.textContent = agg.limit_type;

    const tdCount = document.createElement('td');
    tdCount.className = 'num';
    tdCount.textContent = String(agg.observation_count);

    const tdDrain = document.createElement('td');
    tdDrain.className = 'num highlight';
    const strongDrain = document.createElement('strong');
    strongDrain.textContent = Number(agg.drain_factor).toFixed(3);
    tdDrain.appendChild(strongDrain);

    tr.append(
      tdPeriod, tdProvider, tdAgent, tdModel, tdReasoning,
      tdPlan, tdLimitType, tdCount, tdDrain
    );
    tbody.appendChild(tr);
  }
}

/**
 * Renders the interactive Plotly.js chart. One trace per
 * provider + agent + model + reasoning + plan + limit type.
 */
function renderChart(aggregates) {
  const seriesMap = new Map();
  for (const agg of aggregates) {
    if (!seriesMap.has(agg.series)) {
      seriesMap.set(agg.series, []);
    }
    seriesMap.get(agg.series).push(agg);
  }

  // Deterministic category order along the time axis.
  const periodOrder = new Map();
  for (const agg of aggregates) {
    if (!periodOrder.has(agg.period_start)) {
      periodOrder.set(agg.period_start, agg.period_label);
    }
  }
  const categoryArray = [...periodOrder.entries()]
    .sort((a, b) => a[0].localeCompare(b[0]))
    .map(entry => entry[1]);

  const sortedSeriesNames = [...seriesMap.keys()].sort();
  const traces = [];

  for (let i = 0; i < sortedSeriesNames.length; i++) {
    const seriesName = sortedSeriesNames[i];
    const points = seriesMap.get(seriesName);
    const color = PALETTE[i % PALETTE.length];

    // Only aggregate values are exposed — never a single contributor's raw numbers.
    const customdata = points.map(p => [
      p.provider,
      p.agent,
      p.model,
      p.reasoning,
      formatPlan(p.plan),
      p.limit_type,
      p.observation_count,
    ]);

    const hovertemplate =
      '<b>%{customdata[2]}</b><br>' +
      'Provider: %{customdata[0]}<br>' +
      'Agent: %{customdata[1]}<br>' +
      'Reasoning: %{customdata[3]}<br>' +
      'Plan: %{customdata[4]}<br>' +
      'Limit type: %{customdata[5]}<br><br>' +
      'Period: %{x}<br>' +
      'Drain Factor: <b>%{y:.3f}</b><br>' +
      'Observations (n): %{customdata[6]}' +
      '<extra></extra>';

    traces.push({
      x: points.map(p => p.period_label),
      y: points.map(p => Number(Number(p.drain_factor).toFixed(3))),
      name: seriesName,
      type: 'scatter',
      mode: 'lines+markers',
      line: {
        color: color,
        width: 3,
      },
      marker: {
        size: 10,
        symbol: 'circle',
        color: color,
        line: {
          color: '#ffffff',
          width: 2,
        },
      },
      customdata: customdata,
      hovertemplate: hovertemplate,
    });
  }

  const isDarkMode = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
  const layoutFontFamily = '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif';

  const layout = {
    title: {
      text: 'Agent Limit Drain Factor Over Time',
      font: {
        size: 18,
        family: layoutFontFamily,
        color: isDarkMode ? '#f8fafc' : '#1e293b',
      },
      x: 0.02,
      y: 0.95,
    },
    xaxis: {
      title: {
        text: 'Week',
        font: { size: 13, family: layoutFontFamily, color: isDarkMode ? '#94a3b8' : '#475569' },
      },
      tickfont: { size: 12, family: layoutFontFamily, color: isDarkMode ? '#cbd5e1' : '#334155' },
      showgrid: true,
      gridcolor: isDarkMode ? '#334155' : '#e2e8f0',
      linecolor: isDarkMode ? '#475569' : '#cbd5e1',
      zeroline: false,
      type: 'category',
      categoryorder: 'array',
      categoryarray: categoryArray,
    },
    yaxis: {
      title: {
        text: 'Drain Factor (M tokens / 1% limit)',
        font: { size: 13, family: layoutFontFamily, color: isDarkMode ? '#94a3b8' : '#475569' },
      },
      tickfont: { size: 12, family: layoutFontFamily, color: isDarkMode ? '#cbd5e1' : '#334155' },
      showgrid: true,
      gridcolor: isDarkMode ? '#334155' : '#e2e8f0',
      linecolor: isDarkMode ? '#475569' : '#cbd5e1',
      zeroline: false,
      rangemode: 'tozero',
    },
    showlegend: true,
    legend: {
      font: { size: 11, family: layoutFontFamily, color: isDarkMode ? '#cbd5e1' : '#334155' },
      bgcolor: isDarkMode ? 'rgba(30, 41, 59, 0.9)' : 'rgba(255, 255, 255, 0.9)',
      bordercolor: isDarkMode ? '#334155' : '#e2e8f0',
      borderwidth: 1,
      orientation: 'h',
      yanchor: 'bottom',
      y: 1.02,
      xanchor: 'right',
      x: 1,
    },
    paper_bgcolor: isDarkMode ? '#1e293b' : '#ffffff',
    plot_bgcolor: isDarkMode ? '#0f172a' : '#f8fafc',
    margin: { l: 65, r: 30, t: 85, b: 65 },
    hoverlabel: {
      bgcolor: isDarkMode ? '#334155' : '#1e293b',
      font: { color: '#ffffff', size: 13, family: layoutFontFamily },
      bordercolor: isDarkMode ? '#334155' : '#1e293b',
    },
    autosize: true,
  };

  const config = {
    responsive: true,
    displayModeBar: true,
    displaylogo: false,
    modeBarButtonsToRemove: ['lasso2d', 'select2d'],
    toImageButtonOptions: {
      format: 'svg',
      filename: 'agent-limit-drain-factor',
      height: 500,
      width: 900,
      scale: 2,
    },
  };

  Plotly.newPlot('chart', traces, layout, config);
}

// Initialize when DOM is ready
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
