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
Open `index.html` in a browser to use the local run console. It connects to `http://127.0.0.1:8765` by default. To use another API address, add `?api=http://127.0.0.1:PORT` to the page URL.

Endpoints:
- `GET /api/health`
- `POST /api/run` JSON body, e.g. `{"agent":"mock","max_iterations":2}`
- `GET /api/status/{request_id}`
- `GET /api/result/{request_id}`
- `GET /api/report/{request_id}`
- Interactive docs: `http://127.0.0.1:8765/docs`

The API binds to localhost by default and keeps request status in process memory. Restarting the server clears the API request cache, although completed optimization/agent records remain in SQLite.

## Safety and model boundaries
- Every candidate must have all recorded optimizer constraint checks passing before it is sent to the agent.
- Agent output is JSON-validated. The agent can only update four bounded optimizer weights and provide an advisory surplus policy.
- The best candidate is selected using a frozen reference objective so objective values from different agent-proposed weights are not compared directly.
- Battery discharge is treated as an internal energy transfer, never as a second external source.
- `eta_mainsys` is currently a useful-load utilization proxy: load-served energy divided by grid-import energy plus PV energy used. It is not a full export-aware efficiency metric.
- `eta_cost_mainsys` and `aleph_mainsys` are unavailable until tariff and grid-export data/dispatch are implemented.
- Curtailed PV is reported as candidate surplus. Battery headroom is only a theoretical ceiling; this release does not dispatch extra charging or export. The existing LP must be extended and re-solved before those recommendations become executable.
- No thermal reservoir is present in the supplied DB.
- The current optimizer remains an aggregate active-power LP with fixed measured network losses, not a nonlinear AC/thermal re-dispatch model.

## Database
The default run appends optimizer rows and one `agent_run` row to the hurdle SQLite database. Back up the database before repeated experimentation. Use `--db /path/to/copy.sqlite` to run against a separate copy.
