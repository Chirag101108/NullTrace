# AfterHours Microgrid Brain v1

## What changed
- Root `main.py` is now the application entry point/orchestrator.
- `microgrid_afterhours_hurdle_v1/optimizer.py` remains the deterministic LP engine and prices grid imports from the run's tariff snapshot.
- `brain_core/` contains the compact MDP protocol, Gemini Interactions adapter with bounded database tools, deterministic mock agent, pattern scan, bounded orchestration loop, surplus advisory and FastAPI interface.
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
Gemini is called through Google's Interactions API using the `gemini-3.8-flash` model by default. The API key is sent in the request header and request storage is disabled. The mock agent exercises the full optimizer loop without API credits; it is explicitly not Gemini reasoning. Both modes use a per-run copy of the configured sample database.

## Run with Gemini
```bash
export GEMINI_API_KEY='YOUR_KEY'
# Optional; default is gemini-3.8-flash
export GEMINI_MODEL='gemini-3.8-flash'
python main.py run --agent gemini --max-iterations 3
```
`--agent auto` selects Gemini when `GEMINI_API_KEY` is present and otherwise uses the mock agent.

During feedback, Gemini receives compact JSON evidence and may inspect candidate summaries, constraints, tariff snapshots, and up to 12 interval rows. It may write only bounded optimizer tuning values to the dedicated `agent_optimizer_tuning` audit table in the marked per-run database copy. It cannot submit SQL or change telemetry, tariffs, asset limits, or dispatch. The optimizer re-solves and validates each candidate. Gemini's target is the fixed-weight Aleph score; the candidate with the highest available Aleph is selected, with the frozen reference objective used only to break ties. If Aleph is unavailable, tuning writes are disabled and candidate selection falls back to the reference objective without inventing or renormalizing a score. At termination, the selected candidate is sent for a separate human-readable report.

## Run the local API
```bash
python main.py serve --host 127.0.0.1 --port 8765
```
The teammate's authenticated dashboard is in `grid-system-copy/`. From the repository root, run `./start_local.sh --agent mock` or `./start_local.sh --agent gemini`; Gemini can use `GEMINI_API_KEY` from the environment or a one-run key entered in the dashboard. Without the flag, Automatic uses Gemini when the environment key exists and Mock otherwise. The selected mode becomes the dashboard default and can still be changed per run. The script starts the API on `127.0.0.1:8765` and the dashboard on `127.0.0.1:3000`; set `AFTERHOURS_API_PORT` and `AFTERHOURS_FRONTEND_PORT` for alternate distinct ports. The `grid-system/` directory is older; the separate root `index.html` is not the authenticated dashboard served by the launcher.

Endpoints:
- `GET /api/health` returns `status`, `database_exists`, `database_ready`, and any `missing_tables`.
- `POST /api/run` accepts an agent of `auto`, `mock`, or `gemini`, `max_iterations` from 1–12, and optional `horizon` from 1–672. Example: `{"agent":"mock","max_iterations":2}`. It returns HTTP 202 with `{"request_id":"REQ-…","status":"queued"}`.
- `GET /api/status/{request_id}` returns `status`, normalized `request`, and `progress` (`stage`, `iteration`, `total_iterations`); failures also include `error`.
- `GET /api/result/{request_id}` returns the complete workflow object: `agent_run_id`, `status`, `agent`, `iterations`, `termination_reason`, `best_candidate`, `surplus_allocation`, `relevant_patterns`, `candidate_history`, `agent_decisions`, `report`, `report_source`, and `database`. `best_candidate` includes validation, power, energy, battery, metrics, and limitations.
- `GET /api/report/{request_id}` returns `agent_run_id`, `report`, and `report_source`.
- `GET /api/timeseries/{optimizer_run_id}` returns actual interval timestamps,
  units, and measured/optimized series (demand, PV, grid, storage, cost and Aleph)
  in JSON suitable for Chart.js. Values align to the optimizer run ID, not the
  asynchronous API request ID.
- `GET /api/aleph/{optimizer_run_id}` returns component series, weights,
  horizon score and Chart.js `labels`/`datasets`. `ALEPH.md` documents formulas,
  normalization and assumptions.
- Interactive docs: `http://127.0.0.1:8765/docs`

The API binds to localhost by default. The configured database is a seed/sample and is opened read-only for health checks and SQLite backups. Every API or `main.py run` optimization operates on a separate copy under `microgrid_afterhours_hurdle_v1/runs/`; optimizer and Gemini tool access use that copy. Request status and results are stored separately under `.afterhours-runtime/`. Completed run copies remain available after an API restart; an in-flight run is marked failed and is not resumed. Directly running `microgrid_afterhours_hurdle_v1/optimizer.py` remains a lower-level operation that writes to the database path passed to that standalone optimizer.
Cross-origin browser calls are limited to `http://127.0.0.1` frontend origins; loopback ports are allowed so the frontend can use its fallback port when 3000 is busy.

## Safety and model boundaries
- Every candidate must have all recorded optimizer constraint checks passing before it is sent to the agent.
- Agent output is JSON-validated. The agent can only update four bounded optimizer weights and provide an advisory surplus policy.
- The best candidate is selected by fixed-weight Aleph; the frozen reference objective breaks Aleph ties and is used only when Aleph is unavailable.
- The feedback loop stops on an explicit agent stop, a continue-without-changes decision, two consecutive candidates without a tolerance-sized improvement, or the configured iteration limit (maximum 12).
- Battery discharge is treated as an internal energy transfer, never as a second external source.
- `eta_mainsys` is currently a useful-load utilization proxy: load-served energy divided by grid-import energy plus PV energy used. It is not a full export-aware efficiency metric.
- The database includes a clearly labeled synthetic time-of-use tariff; it is
  not an official utility schedule. Candidate import cost is tariff-aware.
- Aleph uses six weights (0.25 adequacy, 0.20 physical efficiency, 0.15
  renewable utilization, 0.15 storage, 0.15 economics, 0.10 modeled network
  constraint compliance). Its x-axis is the timestamped 15-minute interval;
  each point reflects cumulative results through that interval.
- Missing Aleph components make the index unavailable; weights are not silently
  renormalized. The displayed network component reflects optimizer checks only;
  it is not an AC voltage/thermal-feasibility proof.
- Curtailed PV is reported as candidate surplus. Battery headroom is only a theoretical ceiling; this release does not dispatch extra charging or export. The existing LP must be extended and re-solved before those recommendations become executable.
- No thermal reservoir is present in the supplied DB.
- The current optimizer remains an aggregate active-power LP with fixed measured network losses, not a nonlinear AC/thermal re-dispatch model.

## Database
The default `microgrid_afterhours_hurdle_v1/microgrid.sqlite` remains the seed database; the launcher and `main.py run` do not append optimizer runs to it. Use `--db /path/to/sample.sqlite` to choose a different seed database.
