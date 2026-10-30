# AfterHours Microgrid Brain v1

## What changed
- Root `main.py` is now the application entry point/orchestrator.
- The existing `microgrid_afterhours_hurdle_v1/optimizer.py` is preserved unchanged and remains the deterministic LP engine.
- `brain_core/` contains the compact MDP protocol, Gemini REST adapter, deterministic mock agent, pattern scan, bounded orchestration loop, surplus advisory and FastAPI interface.
- The default database is `microgrid_afterhours_hurdle_v1/microgrid.sqlite` (the hurdle DB), not the older root `microgrid.sqlite`.

## Install
From the repository root:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run offline first
```bash
python main.py run --agent mock --max-iterations 2
```
Gemini is called through Google's Interactions API using the `gemini-3.8-flash` model by default. The API key is sent in the request header, request storage is disabled, and Gemini responses use a JSON schema before local validation. The mock agent exercises the full loop without API credits; it is explicitly not Gemini reasoning.

## Run with Gemini
```bash
export GEMINI_API_KEY='YOUR_KEY'
# Optional; default is gemini-3.8-flash
export GEMINI_MODEL='gemini-3.8-flash'
python main.py run --agent gemini --max-iterations 3
```
`--agent auto` selects Gemini when `GEMINI_API_KEY` is present and otherwise uses the mock agent.

## Run the local API
```bash
python main.py serve --host 127.0.0.1 --port 8765
```
The teammate's authenticated dashboard is in `grid-system-copy/`. Run it in a second terminal with `cd grid-system-copy && npm start`, then open `http://127.0.0.1:3000`. It connects to `http://127.0.0.1:8765`. The separate root `index.html` is not part of this frontend integration.

Endpoints:
- `GET /api/health` returns `status`, `database_exists`, `database_ready`, and any `missing_tables`.
- `POST /api/run` accepts an agent of `auto`, `mock`, or `gemini`, `max_iterations` from 1–12, and optional `horizon` from 1–672. Example: `{"agent":"mock","max_iterations":2}`. It returns HTTP 202 with `{"request_id":"REQ-…","status":"queued"}`.
- `GET /api/status/{request_id}` returns `status`, normalized `request`, and `progress` (`stage`, `iteration`, `total_iterations`); failures also include `error`.
- `GET /api/result/{request_id}` returns the complete workflow object: `agent_run_id`, `status`, `agent`, `iterations`, `termination_reason`, `best_candidate`, `surplus_allocation`, `relevant_patterns`, `candidate_history`, `agent_decisions`, `report`, `report_source`, and `database`. `best_candidate` includes validation, power, energy, battery, metrics, and limitations.
- `GET /api/report/{request_id}` returns `agent_run_id`, `report`, and `report_source`.
- Interactive docs: `http://127.0.0.1:8765/docs`

The API binds to localhost by default. API run status, progress, results, and errors are stored in the configured SQLite database and completed runs remain retrievable after an API restart. An in-flight run cannot resume after a process restart and is marked failed when the API starts again.
Cross-origin browser calls are limited to `http://127.0.0.1` frontend origins; loopback ports are allowed so the frontend can use its fallback port when 3000 is busy.

## Safety and model boundaries
- Every candidate must have all recorded optimizer constraint checks passing before it is sent to the agent.
- Agent output is JSON-validated. The agent can only update four bounded optimizer weights and provide an advisory surplus policy.
- The best candidate is selected using a frozen reference objective so objective values from different agent-proposed weights are not compared directly.
- The feedback loop stops on an explicit agent stop, a continue-without-changes decision, two consecutive candidates without a tolerance-sized improvement, or the configured iteration limit (maximum 12).
- Battery discharge is treated as an internal energy transfer, never as a second external source.
- `eta_mainsys` is currently a useful-load utilization proxy: load-served energy divided by grid-import energy plus PV energy used. It is not a full export-aware efficiency metric.
- `eta_cost_mainsys` and `aleph_mainsys` are unavailable until tariff and grid-export data/dispatch are implemented.
- Curtailed PV is reported as candidate surplus. Battery headroom is only a theoretical ceiling; this release does not dispatch extra charging or export. The existing LP must be extended and re-solved before those recommendations become executable.
- No thermal reservoir is present in the supplied DB.
- The current optimizer remains an aggregate active-power LP with fixed measured network losses, not a nonlinear AC/thermal re-dispatch model.

## Database
The default run appends optimizer rows and one `agent_run` row to the hurdle SQLite database. Back up the database before repeated experimentation. Use `--db /path/to/copy.sqlite` to run against a separate copy.
