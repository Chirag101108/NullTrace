'use strict';

const $ = (id) => document.getElementById(id);
const API_BASE = window.AFTERHOURS_BRAIN_API || 'http://127.0.0.1:8765';
const DT_H = 0.25;
const COMPONENTS = [
  ['energy_adequacy', 'Energy adequacy'],
  ['physical_efficiency', 'Physical efficiency'],
  ['renewable_utilization', 'Renewable utilization'],
  ['storage_performance', 'Storage performance'],
  ['economic_performance', 'Economic performance'],
  ['network_compliance', 'Network checks'],
];
const ALEPH_WEIGHTS = {
  energy_adequacy: 0.25,
  physical_efficiency: 0.20,
  renewable_utilization: 0.15,
  storage_performance: 0.15,
  economic_performance: 0.15,
  network_compliance: 0.10,
};
const PALETTE = ['#83e65e', '#efc56d', '#72b8ff', '#f08c7f', '#bf9bff'];
let selectedView = 'before';
let latestResult = null;
let timeSeries = null;
let alephData = null;
let alephChart = null;
let dispatchChart = null;
let customChart = null;

const number = (value, digits = 2) => value !== null && value !== undefined && value !== '' && Number.isFinite(Number(value))
  ? Number(value).toLocaleString(undefined, { maximumFractionDigits: digits, minimumFractionDigits: digits })
  : 'Unavailable';
const sum = (values) => Array.isArray(values) && values.length > 0
  && values.every((value) => value !== null && value !== undefined && Number.isFinite(Number(value)))
  ? values.reduce((total, value) => total + Number(value), 0) : null;
const intervalEnergy = (values) => {
  const total = sum(values);
  return total === null ? null : total * DT_H;
};
const currentViewName = () => selectedView === 'before' ? 'Before · Measured Baseline' : 'After · Optimized Candidate';

async function api(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : `API request failed (${response.status})`);
  return body;
}

function setStatus(message, kind = '') {
  const node = $('run-status');
  node.textContent = message;
  node.className = `notice ${kind}`;
}

// Keep the credential only in page memory. The editable DOM value always
// contains one asterisk per accepted ASCII key character.
let manualGeminiApiKey = '';
const geminiKeyInput = $('gemini-api-key');
const geminiKeyField = $('gemini-key-field');

function updateIterationLimit() {
  const selection = $('agent').value;
  const geminiWillRun = selection === 'gemini' || (selection === 'auto' && (
    Boolean(manualGeminiApiKey) || Boolean(window.AFTERHOURS_GEMINI_CONFIGURED)
      || window.AFTERHOURS_DEFAULT_AGENT === 'gemini'
  ));
  for (const option of $('iterations').options) {
    option.disabled = geminiWillRun && Number(option.value) > 5;
  }
  $('iterations').max = geminiWillRun ? '5' : '12';
  if (geminiWillRun && Number($('iterations').value) > 5) $('iterations').value = '5';
  $('iteration-limit-hint').textContent = geminiWillRun
    ? 'Gemini safety limit: 5 iterations'
    : 'Mock limit: 12 iterations · Gemini limit: 5';
}

function showGeminiKeyMask(caret = manualGeminiApiKey.length) {
  geminiKeyInput.value = '*'.repeat(manualGeminiApiKey.length);
  geminiKeyInput.setSelectionRange(caret, caret);
}

function editGeminiKey(start, end, inserted = '') {
  if (inserted && (!/^[\x21-\x7E]+$/.test(inserted)
      || /\s/.test(inserted) || manualGeminiApiKey.length - (end - start) + inserted.length > 256)) {
    manualGeminiApiKey = '';
    setStatus('The Gemini key must be at most 256 printable non-whitespace ASCII characters.', 'error');
    showGeminiKeyMask(0);
    updateIterationLimit();
    return;
  }
  manualGeminiApiKey = manualGeminiApiKey.slice(0, start) + inserted + manualGeminiApiKey.slice(end);
  showGeminiKeyMask(start + inserted.length);
  updateIterationLimit();
}

function clearGeminiApiKey() {
  manualGeminiApiKey = '';
  if (geminiKeyInput) geminiKeyInput.value = '';
  updateIterationLimit();
}

function updateGeminiKeyVisibility() {
  const enabled = $('agent').value !== 'mock';
  geminiKeyField.hidden = !enabled;
  if (!enabled) clearGeminiApiKey();
}

geminiKeyInput.addEventListener('beforeinput', (event) => {
  event.preventDefault();
  const start = geminiKeyInput.selectionStart ?? manualGeminiApiKey.length;
  const end = geminiKeyInput.selectionEnd ?? start;
  if (event.inputType === 'insertText' && typeof event.data === 'string') {
    editGeminiKey(start, end, event.data);
  } else if (event.inputType === 'deleteContentBackward') {
    editGeminiKey(start === end ? Math.max(0, start - 1) : start, end);
  } else if (event.inputType === 'deleteContentForward') {
    editGeminiKey(start, start === end ? Math.min(manualGeminiApiKey.length, end + 1) : end);
  }
});

geminiKeyInput.addEventListener('paste', (event) => {
  event.preventDefault();
  const start = geminiKeyInput.selectionStart ?? manualGeminiApiKey.length;
  const end = geminiKeyInput.selectionEnd ?? start;
  editGeminiKey(start, end, event.clipboardData?.getData('text/plain') || '');
});

geminiKeyInput.addEventListener('cut', (event) => {
  event.preventDefault();
  const start = geminiKeyInput.selectionStart ?? 0;
  const end = geminiKeyInput.selectionEnd ?? start;
  if (start !== end) editGeminiKey(start, end);
});

$('agent').addEventListener('change', () => {
  updateGeminiKeyVisibility();
  updateIterationLimit();
});
updateGeminiKeyVisibility();
updateIterationLimit();

function showPage(name) {
  const valid = ['home', 'graph', 'optimizer', 'report'].includes(name) ? name : 'home';
  document.querySelectorAll('[data-page]').forEach((button) => {
    const active = button.dataset.page === valid;
    if (active) button.setAttribute('aria-current', 'page');
    else button.removeAttribute('aria-current');
  });
  document.querySelectorAll('.page[id^="page-"]').forEach((page) => { page.hidden = page.id !== `page-${valid}`; });
  document.title = `${valid[0].toUpperCase()}${valid.slice(1)} · AfterHours`;
  if (valid === 'graph') {
    alephChart?.resize();
    dispatchChart?.resize();
    customChart?.resize();
  }
}

document.querySelectorAll('[data-page]').forEach((button) => button.addEventListener('click', () => {
  const page = button.dataset.page;
  history.replaceState(null, '', `#${page}`);
  showPage(page);
}));
document.querySelectorAll('[data-page-link]').forEach((link) => link.addEventListener('click', (event) => {
  event.preventDefault();
  const page = link.dataset.pageLink;
  history.replaceState(null, '', `#${page}`);
  showPage(page);
}));
window.addEventListener('hashchange', () => showPage(location.hash.slice(1)));
showPage(location.hash.slice(1));

function updateViewControls() {
  document.querySelectorAll('[data-view]').forEach((button) => {
    button.setAttribute('aria-pressed', String(button.dataset.view === selectedView));
  });
  document.querySelectorAll('.current-view-label').forEach((node) => { node.textContent = currentViewName(); });
  $('compare-context').textContent = selectedView === 'before' ? 'Before: Measured Baseline' : 'After: Optimized Candidate';
  $('view-caption').textContent = selectedView === 'before'
    ? 'Before shows signed historical grid measurements and the measured source profile. Baseline service and optimizer validation are not inferred.'
    : 'After shows the validated candidate dispatch. Candidate export is zero because the optimizer has no export decision variable.';
}

document.querySelectorAll('[data-view]').forEach((button) => button.addEventListener('click', () => {
  selectedView = button.dataset.view === 'after' ? 'after' : 'before';
  updateViewControls();
  renderComparison();
}));

function metricsForView() {
  const s = timeSeries?.series;
  if (!s) return null;
  const baselineGrid = s.baseline_grid_kw || [];
  const gridImport = s.grid_import_kw || [];
  const demand = s.demand_kw || [];
  const pvGeneration = s.pv_generation_kw || [];
  const pvUsed = s.pv_used_kw || [];
  const isBefore = selectedView === 'before';
  const dataQuality = timeSeries.data_quality || latestResult?.best_candidate?.cost?.data_quality || 'unknown';
  const currency = timeSeries.currency_code || latestResult?.best_candidate?.cost?.currency_code || 'INR';
  const baselineImport = baselineGrid.map((value) => Math.max(0, Number(value) || 0));
  const baselineExport = baselineGrid.map((value) => Math.max(0, -(Number(value) || 0)));
  const bill = isBefore ? sum(s.baseline_interval_cost) : sum(s.interval_energy_cost);
  return {
    gridImportKwh: intervalEnergy(isBefore ? baselineImport : gridImport),
    gridExportKwh: intervalEnergy(isBefore ? baselineExport : (s.grid_export_kw || [])),
    bill,
    currency,
    demandKwh: intervalEnergy(demand),
    servedKwh: isBefore ? null : intervalEnergy(s.load_served_kw || []),
    pvKwh: intervalEnergy(isBefore ? pvGeneration : pvUsed),
    pvLabel: isBefore ? 'Measured PV generation' : 'PV energy used',
    demandLabel: isBefore ? 'Measured demand' : 'Candidate load served',
    aleph: isBefore ? null : alephData?.score,
    validation: isBefore ? null : latestResult?.best_candidate?.validation,
    baselineExportKwh: intervalEnergy(baselineExport),
    dataQuality,
  };
}

function renderMetricCards(targetId) {
  const target = $(targetId);
  if (!target) return;
  const m = metricsForView();
  if (!m) return;
  const alephText = m.aleph == null ? 'Not scored' : `${number(m.aleph, 1)} / 100`;
  const validationText = m.validation
    ? (m.validation.passed ? `${number(m.validation.checks, 0)} passed` : 'Failed')
    : 'Not evaluated';
  const rows = [
    ['Grid import energy', `${number(m.gridImportKwh)} kWh`],
    ['Net energy bill', `${number(m.bill)} ${m.currency}`],
    [m.demandLabel, `${number(m.servedKwh ?? m.demandKwh)} kWh${m.servedKwh == null ? ' · baseline service not separately measured' : ''}`],
    [m.pvLabel, `${number(m.pvKwh)} kWh`],
    ['Aleph score', alephText],
    ['Constraint checks', validationText],
  ];
  target.replaceChildren();
  for (const [label, value] of rows) {
    const card = document.createElement('article');
    card.className = 'metric';
    const title = document.createElement('span');
    title.textContent = label;
    const detail = document.createElement('strong');
    detail.textContent = value;
    card.append(title, detail);
    target.append(card);
  }
}

function renderRunFacts() {
  const target = $('run-facts');
  if (!target || !timeSeries) return;
  const s = timeSeries.series || {};
  const m = metricsForView();
  const rows = selectedView === 'before' ? [
    ['Data basis', 'Measured baseline intervals'],
    ['Intervals', String(timeSeries.timestamps?.length || 0)],
    ['Grid import energy', `${number(m.gridImportKwh)} kWh`],
    ['Historical grid export', `${number(m.baselineExportKwh)} kWh`],
    ['Measured demand', `${number(m.demandKwh)} kWh`],
    ['Measured PV generation', `${number(intervalEnergy(s.pv_generation_kw))} kWh`],
    ['Aleph / validation', 'Not scored for the baseline'],
    ['Tariff basis', m.dataQuality === 'synthetic' ? 'Synthetic demonstration schedule' : 'Configured tariff snapshot'],
  ] : [
    ['Data basis', 'Validated optimizer candidate'],
    ['Intervals', String(timeSeries.timestamps?.length || 0)],
    ['Grid import energy', `${number(m.gridImportKwh)} kWh`],
    ['Candidate export', `${number(m.gridExportKwh)} kWh · optimizer has no export variable`],
    ['Load served / demand', `${number(m.servedKwh)} / ${number(m.demandKwh)} kWh`],
    ['PV used / curtailed', `${number(intervalEnergy(s.pv_used_kw))} / ${number(intervalEnergy(s.pv_curtailed_kw))} kWh`],
    ['Constraint checks', m.validation?.passed ? `${m.validation.checks} passed` : 'Unavailable'],
    ['Aleph', m.aleph == null ? 'Unavailable · a component is missing' : `${number(m.aleph, 2)} / 100`],
  ];
  target.replaceChildren();
  rows.forEach(([label, value]) => {
    const item = document.createElement('div');
    item.className = 'fact';
    const name = document.createElement('span');
    name.textContent = label;
    const detail = document.createElement('strong');
    detail.textContent = value;
    item.append(name, detail);
    target.append(item);
  });
}

function makeChart(canvasId, previous, config) {
  if (!window.Chart) throw new Error('Chart.js could not be loaded.');
  const canvas = $(canvasId);
  if (previous) previous.destroy();
  return new window.Chart(canvas, config);
}

function renderAleph() {
  const isBefore = selectedView === 'before';
  const empty = $('aleph-empty');
  const canvas = $('aleph-chart');
  const score = $('aleph-score');
  const note = $('aleph-note');
  const componentTarget = $('aleph-components');
  const weightTarget = $('aleph-weights');
  const noBaselineIndex = isBefore || !alephData?.available || !Array.isArray(alephData.chart?.datasets?.[0]?.data);
  score.textContent = noBaselineIndex || alephData.score == null ? '— / 100' : `${number(alephData.score, 1)} / 100`;
  empty.hidden = !noBaselineIndex;
  canvas.hidden = noBaselineIndex;
  note.textContent = isBefore
    ? 'Aleph is a candidate index. The measured baseline is not scored because comparable baseline component data is not available.'
    : (alephData?.data_quality === 'synthetic'
      ? 'Candidate score · tariff schedule and one-to-one export credit are synthetic demonstration assumptions.'
      : 'Weighted candidate score from 0 to 100. Hard optimizer checks remain separate from the score.');
  componentTarget.replaceChildren();
  const values = alephData?.summary?.components || {};
  const weights = alephData?.weights || ALEPH_WEIGHTS;
  COMPONENTS.forEach(([key, label]) => {
    const card = document.createElement('div');
    const value = isBefore ? null : values[key];
    card.className = `aleph-component${Number.isFinite(value) ? '' : ' unavailable'}`;
    const name = document.createElement('span');
    name.textContent = label;
    const scoreText = document.createElement('strong');
    scoreText.textContent = Number.isFinite(value) ? `${number(value, 1)} / 100` : (isBefore ? 'Not scored' : 'Unavailable');
    const weight = document.createElement('small');
    weight.textContent = `${Math.round((weights[key] || 0) * 100)}% weight`;
    card.append(name, scoreText, weight);
    componentTarget.append(card);
  });
  weightTarget.replaceChildren();
  COMPONENTS.forEach(([key, label]) => {
    const item = document.createElement('span');
    item.textContent = `${label} ${Math.round((weights[key] || 0) * 100)}%`;
    weightTarget.append(item);
  });
  alephChart?.destroy();
  alephChart = null;
  if (noBaselineIndex) return;
  try {
    const labels = alephData.chart.labels || alephData.timestamps || [];
    const points = alephData.chart.datasets[0].data;
    alephChart = makeChart('aleph-chart', null, {
      type: 'line',
      data: { labels, datasets: [{
        label: 'Aleph', data: points, spanGaps: false,
        borderColor: '#83e65e', backgroundColor: 'rgba(131,230,94,.16)',
        borderWidth: 2.5, pointRadius: 0, pointHitRadius: 8, fill: true, tension: .2,
      }] },
      options: {
        responsive: true, maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: { legend: { display: false }, tooltip: { callbacks: {
          title: (items) => items.length ? new Date(labels[items[0].dataIndex]).toLocaleString([], { timeZone: 'Asia/Kolkata', dateStyle: 'medium', timeStyle: 'short' }) : '',
          label: (item) => Number.isFinite(item.raw) ? `Aleph: ${number(item.raw)} / 100` : 'Aleph unavailable',
        } } },
        scales: {
          x: { grid: { color: 'rgba(154,180,158,.10)' }, ticks: { color: '#9aaea2', maxTicksLimit: 8, maxRotation: 0, callback: (index) => {
            const stamp = labels[index];
            return stamp ? new Date(stamp).toLocaleString([], { timeZone: 'Asia/Kolkata', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '';
          } } },
          y: { min: 0, max: 100, grid: { color: 'rgba(154,180,158,.12)' }, ticks: { color: '#9aaea2', stepSize: 20 }, title: { display: true, text: 'Aleph (0–100)', color: '#9aaea2' } },
        },
      },
    });
  } catch (error) {
    note.textContent = `Aleph graph unavailable: ${error.message}`;
  }
}

function renderDispatch() {
  const empty = $('dispatch-empty');
  const canvas = $('dispatch-chart');
  if (!timeSeries?.timestamps?.length || !timeSeries.series) {
    canvas.hidden = true;
    empty.hidden = false;
    dispatchChart?.destroy();
    dispatchChart = null;
    return;
  }
  canvas.hidden = false;
  empty.hidden = true;
  const s = timeSeries.series;
  const baseline = selectedView === 'before';
  const datasets = baseline ? [
    { label: 'Measured demand', data: s.demand_kw, borderColor: PALETTE[0] },
    { label: 'Measured PV generation', data: s.pv_generation_kw, borderColor: PALETTE[2] },
    { label: 'Measured grid exchange', data: s.baseline_grid_kw, borderColor: PALETTE[1] },
  ] : [
    { label: 'Demand', data: s.demand_kw, borderColor: PALETTE[0] },
    { label: 'PV used', data: s.pv_used_kw, borderColor: PALETTE[2] },
    { label: 'Grid import', data: s.grid_import_kw, borderColor: PALETTE[1] },
    { label: 'Load served', data: s.load_served_kw, borderColor: PALETTE[3] },
  ];
  $('dispatch-note').textContent = baseline
    ? 'Measured demand, PV generation and signed grid exchange from the supplied baseline intervals.'
    : 'Optimized candidate demand, PV used, grid import and load served. Export remains zero in this optimizer.';
  try {
    dispatchChart?.destroy();
    dispatchChart = makeChart('dispatch-chart', null, {
      type: 'line',
      data: { labels: timeSeries.timestamps, datasets: datasets.map((set) => ({
        ...set, data: set.data, borderWidth: 1.8, pointRadius: 0, pointHitRadius: 6,
        tension: .15, spanGaps: false,
      })) },
      options: {
        responsive: true, maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: { legend: { labels: { color: '#c5d2c9', usePointStyle: true } }, tooltip: { callbacks: {
          title: (items) => items.length ? new Date(timeSeries.timestamps[items[0].dataIndex]).toLocaleString([], { timeZone: 'Asia/Kolkata', dateStyle: 'medium', timeStyle: 'short' }) : '',
          label: (item) => `${item.dataset.label}: ${number(item.raw)} kW`,
        } } },
        scales: {
          x: { grid: { color: 'rgba(154,180,158,.08)' }, ticks: { color: '#9aaea2', maxTicksLimit: 8, maxRotation: 0, callback: (index) => {
            const stamp = timeSeries.timestamps[index];
            return stamp ? new Date(stamp).toLocaleString([], { timeZone: 'Asia/Kolkata', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '';
          } } },
          y: { grid: { color: 'rgba(154,180,158,.10)' }, ticks: { color: '#9aaea2' }, title: { display: true, text: 'Power (kW)', color: '#9aaea2' } },
        },
      },
    });
  } catch (error) {
    empty.hidden = false;
    empty.textContent = `Power graph unavailable: ${error.message}`;
    canvas.hidden = true;
  }
}

function renderPatterns(patterns) {
  const target = $('patterns');
  const rows = Array.isArray(patterns) ? patterns : [];
  $('pattern-count').textContent = `${rows.length} found`;
  target.replaceChildren();
  if (!rows.length) {
    const empty = document.createElement('div');
    empty.className = 'empty';
    empty.textContent = 'No recurring surplus patterns met the scan threshold.';
    target.append(empty);
    return;
  }
  rows.forEach((pattern) => {
    const card = document.createElement('div');
    card.className = 'pattern';
    const title = document.createElement('strong');
    title.textContent = `${String(pattern.pattern || 'Pattern').replaceAll('_', ' ')} · ${String(pattern.time_of_day_hour ?? '—').padStart(2, '0')}:00`;
    const detail = document.createElement('span');
    detail.textContent = `${pattern.surplus_observations ?? '—'}/${pattern.observations ?? '—'} observations · mean margin ${number(pattern.mean_solar_margin_kw)} kW`;
    card.append(title, detail);
    target.append(card);
  });
}

function renderComparison() {
  renderMetricCards('summary-metrics');
  renderMetricCards('optimizer-metrics');
  renderRunFacts();
  renderAleph();
  renderDispatch();
  if (customChart) renderCustomGraph();
  document.querySelectorAll('.candidate-only').forEach((node) => { node.hidden = selectedView === 'before'; });
}

async function loadCompletedResult(requestId) {
  const result = await api(`/api/result/${encodeURIComponent(requestId)}`);
  latestResult = result;
  const runId = result.best_candidate?.run_id;
  if (!runId) throw new Error('Completed run did not include an optimizer run ID.');
  const [seriesResponse, scoreResponse] = await Promise.all([
    api(`/api/timeseries/${encodeURIComponent(runId)}`),
    api(`/api/aleph/${encodeURIComponent(runId)}`),
  ]);
  timeSeries = seriesResponse;
  alephData = scoreResponse;
  $('run-id').textContent = `${result.agent_run_id || requestId} · ${result.agent || 'agent'} · ${runId}`;
  $('report-title').textContent = `${result.agent_run_id || requestId} · ${runId}`;
  $('report-source').textContent = `${result.report_source || 'unknown source'} · ${result.agent || 'agent'}`;
  $('report-content').textContent = result.report || 'No report was returned.';
  $('decision-log').textContent = JSON.stringify(result.agent_decisions || [], null, 2);
  const decisions = result.agent_decisions || [];
  const lastDecision = decisions.length ? decisions[decisions.length - 1] : null;
  $('decision-summary').textContent = lastDecision
    ? `${result.agent || 'Agent'} ended after ${result.iterations} iteration(s): ${lastDecision.reason || result.termination_reason}. Termination: ${result.termination_reason}.`
    : `The workflow ended after ${result.iterations} iteration(s): ${result.termination_reason}.`;
  $('candidate-history').textContent = JSON.stringify(result.candidate_history || [], null, 2);
  renderPatterns(result.relevant_patterns);
  $('data-note').textContent = `Comparison loaded · ${seriesResponse.timestamps?.length || 0} aligned intervals · tariff data: ${seriesResponse.data_quality || 'unknown'}.`;
  renderComparison();
  return result;
}

async function pollRun(requestId) {
  const deadline = Date.now() + 15 * 60 * 1000;
  while (Date.now() < deadline) {
    const status = await api(`/api/status/${encodeURIComponent(requestId)}`);
    const progress = status.progress || {};
    if (status.status === 'completed') return loadCompletedResult(requestId);
    if (status.status === 'failed') throw new Error(status.error || 'Run failed.');
    const iteration = progress.iteration ? ` · iteration ${progress.iteration}/${progress.total_iterations}` : '';
    setStatus(`Run ${status.status} · ${progress.stage || 'working'}${iteration}`);
    await new Promise((resolve) => window.setTimeout(resolve, 1000));
  }
  throw new Error('Run timed out in the dashboard. Check the API status before starting another run.');
}

$('run-button').addEventListener('click', async () => {
  const button = $('run-button');
  button.disabled = true;
  button.textContent = 'Running…';
  setStatus('Submitting optimization…');
  try {
    const request = { agent: $('agent').value, max_iterations: Number($('iterations').value) };
    if (manualGeminiApiKey && request.agent !== 'mock') {
      request.gemini_api_key = manualGeminiApiKey;
    }
    const horizon = $('horizon').value.trim();
    if (horizon) request.horizon = Number(horizon);
    const accepted = await api('/api/run', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(request),
    });
    delete request.gemini_api_key;
    clearGeminiApiKey();
    const result = await pollRun(accepted.request_id);
    selectedView = 'after';
    updateViewControls();
    renderComparison();
    setStatus(`Run completed · ${result.agent} · ${result.report_source || 'report ready'}`, 'success');
  } catch (error) {
    setStatus(error.message || 'The run could not be completed.', 'error');
  } finally {
    clearGeminiApiKey();
    button.disabled = false;
    button.innerHTML = 'Run optimizer <span aria-hidden="true">→</span>';
  }
});

function renderCustomGraph() {
  const notice = $('graph-notice');
  try {
    const parsed = JSON.parse($('json-data').value);
    const validValues = (values) => Array.isArray(values) && values.length === parsed.labels.length
      && values.every((value) => typeof value === 'number' && Number.isFinite(value));
    if (!parsed || !Array.isArray(parsed.labels) || !Array.isArray(parsed.datasets) || !parsed.datasets.length
      || parsed.datasets.some((set) => !set || typeof set.label !== 'string'
        || !(validValues(set.data) || (validValues(set.beforeData) && validValues(set.afterData))))) {
      throw new Error('Use labels and named datasets with finite numeric values. For comparison, provide matching beforeData and afterData arrays.');
    }
    if (!window.Chart) throw new Error('Chart.js could not be loaded.');
    customChart?.destroy();
    customChart = new window.Chart($('data-chart'), {
      type: $('chart-type').value,
      data: { labels: parsed.labels, datasets: parsed.datasets.map((set, index) => {
        const paired = validValues(set.beforeData) && validValues(set.afterData);
        return {
        label: paired ? `${set.label} · ${selectedView === 'before' ? 'Before' : 'After'}` : set.label,
        data: paired ? set[selectedView === 'before' ? 'beforeData' : 'afterData'] : set.data,
        borderColor: PALETTE[index % PALETTE.length],
        backgroundColor: `${PALETTE[index % PALETTE.length]}33`,
        tension: .28, fill: $('chart-type').value === 'line',
      };}) },
      options: { responsive: true, maintainAspectRatio: false, interaction: { intersect: false, mode: 'index' },
        plugins: { legend: { labels: { color: '#c5d2c9' } } },
        scales: { x: { ticks: { color: '#9aaea2' }, grid: { color: '#263c31' } }, y: { ticks: { color: '#9aaea2' }, grid: { color: '#263c31' } } } },
    });
    notice.textContent = `Rendered ${parsed.datasets.length} dataset(s) across ${parsed.labels.length} labels.`;
    notice.className = 'notice success';
  } catch (error) {
    notice.textContent = error.message;
    notice.className = 'notice error';
  }
}

$('draw-chart').addEventListener('click', renderCustomGraph);
$('json-data').value = JSON.stringify({ labels: ['00:00', '04:00', '08:00', '12:00', '16:00', '20:00'], datasets: [{ label: 'Example grid import (kW)', beforeData: [18, 19, 28, 43, 47, 36], afterData: [15, 17, 21, 30, 32, 25] }] }, null, 2);
renderCustomGraph();

const configuredAgent = window.AFTERHOURS_DEFAULT_AGENT;
if (['auto', 'mock', 'gemini'].includes(configuredAgent)) $('agent').value = configuredAgent;
updateGeminiKeyVisibility();
updateIterationLimit();

updateViewControls();

// Authentication routes belong to this same-origin Node app. API_BASE points
// at the separate Brain service, which intentionally has no /api/me route.
fetch('/api/me')
  .then(async (response) => {
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.error || 'Not signed in.');
    return body;
  })
  .then(({ user }) => { $('welcome').textContent = `Welcome, ${user.name}`; })
  .catch(() => window.location.assign('/'));

async function checkHealth() {
  try {
    const health = await api('/api/health');
    const ready = health.status === 'ok' && health.database_exists;
    $('health-lamp').className = `lamp ${ready ? 'online' : 'offline'}`;
    $('health-label').textContent = ready ? 'Local API connected' : 'API connected · database unavailable';
    if (ready && !$('run-status').classList.contains('error')) {
      setStatus('Ready. Select an agent and start a run.', 'success');
    }
  } catch (_) {
    $('health-lamp').className = 'lamp offline';
    $('health-label').textContent = 'Local API unavailable';
    setStatus('Local API unavailable. Start the app with ./start_local.sh and check its terminal for startup errors.', 'error');
  }
}

$('signout').addEventListener('click', async () => {
  try { await fetch('/api/logout', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' }); } catch (_) { /* session redirect below */ }
  window.location.assign('/');
});

checkHealth();
window.setInterval(checkHealth, 15000);
