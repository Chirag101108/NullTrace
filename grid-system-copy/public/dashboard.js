'use strict';

const $ = (id) => document.getElementById(id);
const BRAIN_API = 'http://127.0.0.1:8765';
const fmt = (iso) => (iso ? new Date(iso).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }) : '—');
const number = (value, unit = '') => Number.isFinite(value) ? `${value.toLocaleString([], { maximumFractionDigits: 2 })}${unit}` : 'Unavailable';

fetch('/api/me').then((r) => (r.ok ? r.json() : Promise.reject())).then(({ user }) => {
  $('welcome').textContent = `Welcome, ${user.name}`;
  $('f-name').textContent = user.name;
  $('f-username').textContent = user.username;
  $('f-email').textContent = user.email || 'Not provided';
  $('f-created').textContent = fmt(user.createdAt);
}).catch(() => window.location.assign('/'));

async function api(path, options = {}) {
  const response = await fetch(`${BRAIN_API}${path}`, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || data.error || `Brain API returned ${response.status}`);
  return data;
}

function setStatus(message, kind = 'info') {
  const status = $('run-status');
  status.textContent = message;
  status.dataset.kind = kind;
}

function appendMetric(label, value) {
  const term = document.createElement('dt');
  const detail = document.createElement('dd');
  term.textContent = label;
  detail.textContent = value;
  $('result-metrics').append(term, detail);
}

function showResult(result) {
  const candidate = result.best_candidate;
  const metrics = candidate.metrics;
  const energy = candidate.energy_kwh;
  const power = candidate.power;
  $('result-metrics').replaceChildren();
  appendMetric('Agent run', result.agent_run_id);
  appendMetric('Agent', result.agent);
  appendMetric('Iterations', `${result.iterations} · ${result.termination_reason}`);
  appendMetric('Validation', `${candidate.validation.passed ? 'Passed' : 'Failed'} (${candidate.validation.checks} checks)`);
  appendMetric('Peak grid import', number(power.max_grid_import_kw, ' kW'));
  appendMetric('Average grid import', number(power.average_grid_import_kw, ' kW'));
  appendMetric('Load served', number(energy.load_served, ' kWh'));
  appendMetric('Load shed', number(energy.load_shed, ' kWh'));
  appendMetric('Solar used', number(energy.solar_used, ' kWh'));
  appendMetric('PV curtailed', number(energy.pv_curtailed, ' kWh'));
  appendMetric('Battery SOC range', `${number(candidate.battery.soc_min_pct, '%')} – ${number(candidate.battery.soc_max_pct, '%')}`);
  appendMetric('Useful-load utilization', metrics.eta_mainsys === null ? 'Unavailable' : number(metrics.eta_mainsys * 100, '%'));
  appendMetric('Cost efficiency / aleph', metrics.eta_cost_mainsys === null ? 'Unavailable: tariff/export data not supplied' : number(metrics.eta_cost_mainsys));
  $('candidate-history').textContent = JSON.stringify(result.candidate_history, null, 2);
  $('run-context').textContent = JSON.stringify({ relevant_patterns: result.relevant_patterns, surplus_allocation: result.surplus_allocation }, null, 2);
  $('run-report').textContent = result.report || 'No report returned.';
  $('run-results').hidden = false;
}

async function pollRun(requestId, timeoutMs = 15 * 60 * 1000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    const state = await api(`/api/status/${encodeURIComponent(requestId)}`);
    const progress = state.progress || {};
    const iteration = progress.iteration ? ` · iteration ${progress.iteration}/${progress.total_iterations}` : '';
    setStatus(`Run ${state.status}: ${progress.stage || state.status}${iteration}`);
    if (state.status === 'completed') {
      showResult(await api(`/api/result/${encodeURIComponent(requestId)}`));
      setStatus(`Run completed · ${requestId}`, 'success');
      return;
    }
    if (state.status === 'failed') throw new Error(state.error || 'Optimization failed.');
    await new Promise((resolve) => window.setTimeout(resolve, 1000));
  }
  throw new Error('Still running after 15 minutes. The backend may continue; refresh and check its status in the API docs.');
}

$('run-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const button = $('run-button');
  button.disabled = true;
  $('run-results').hidden = true;
  try {
    const horizonValue = $('horizon').value.trim();
    const payload = {
      agent: $('agent').value,
      max_iterations: Number($('iterations').value),
      ...(horizonValue ? { horizon: Number(horizonValue) } : {}),
    };
    setStatus('Submitting optimization…');
    const accepted = await api('/api/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    await pollRun(accepted.request_id);
  } catch (error) {
    setStatus(error.message, 'error');
  } finally {
    button.disabled = false;
  }
});

api('/api/health').then((health) => {
  if (health.database_ready) setStatus('Brain API ready · microgrid schema is available.', 'success');
  else setStatus(`Brain API is not ready${health.missing_tables?.length ? ` · missing tables: ${health.missing_tables.join(', ')}` : ''}.`, 'error');
}).catch(() => setStatus(`Brain API unavailable at ${BRAIN_API}. Start it with: python main.py serve`, 'error'));

$('signout').addEventListener('click', async () => {
  try { await fetch('/api/logout', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' }); } catch {}
  window.location.assign('/');
});
