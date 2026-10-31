import json
import os
import shutil
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from fastapi import HTTPException

import main
from brain_core.agent import AgentError, GeminiAgent, MockAgent
from brain_core.agent_tools import AgentDatabaseTools
from brain_core.api import RunRequest, create_app
from brain_core.db_copy import create_working_copy
from brain_core.orchestrator import run_agentic_workflow


ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DB = ROOT / "microgrid_afterhours_hurdle_v1" / "microgrid.sqlite"


def optimizer_run_count(path):
    with closing(sqlite3.connect(path)) as conn:
        return int(conn.execute("SELECT COUNT(*) FROM optimization_run").fetchone()[0])


class AgentWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="afterhours-agent-test-")
        self.root = Path(self.temp.name)
        self.sample = self.root / "sample.sqlite"
        shutil.copy2(SAMPLE_DB, self.sample)

    def tearDown(self):
        self.temp.cleanup()

    def test_manual_gemini_key_is_not_in_persistable_run_metadata(self):
        secret = "AIza-test-key-never-persist"
        request = RunRequest(agent="gemini", gemini_api_key=secret)
        self.assertNotIn("gemini_api_key", request.public_request())
        self.assertNotIn(secret, json.dumps(request.public_request()))

    def test_gemini_key_header_uses_trimmed_secret_without_network(self):
        secret = "AIza-valid-looking-test-key"

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'{"steps":[]}'

        def fake_urlopen(request, timeout):
            self.assertEqual(request.headers.get("X-goog-api-key"), secret)
            self.assertEqual(timeout, 17)
            self.assertNotIn(secret.encode(), request.data)
            return FakeResponse()

        agent = GeminiAgent(api_key=f"  {secret}  ", timeout=17)
        with patch("urllib.request.urlopen", fake_urlopen):
            agent._request_interaction({"model": "gemini-3.8-flash"})
        with self.assertRaisesRegex(AgentError, "surrounding quote"):
            GeminiAgent(api_key=f"'{secret}'")
        with self.assertRaisesRegex(AgentError, "whitespace"):
            GeminiAgent(api_key="AIza invalid")

    def test_manual_gemini_key_is_used_but_never_persisted_or_sent_to_real_gemini(self):
        secret = "AIza-dry-run-key"
        run_dir = self.root / "manual-key-runs"
        state_db = self.root / "manual-key-state.sqlite"

        class FakeGeminiAgent:
            name = "fake-gemini"
            received_key = None

            def __init__(self, api_key=None):
                type(self).received_key = api_key

            def evaluate(self, payload):
                return {
                    "action": "stop", "parameter_updates": {},
                    "surplus_policy": None, "reason": "Token-free API path test.",
                    "stop_reason": "test_complete",
                }

            def generate_report(self, payload):
                return "Token-free manual-key report."

        app = create_app(str(self.sample), main.load_optimizer(), default_agent="gemini",
                         run_dir=str(run_dir), state_db_path=str(state_db))
        routes = {route.path: route.endpoint for route in app.routes if hasattr(route, "path")}
        with patch.dict(os.environ, {"GEMINI_API_KEY": ""}), \
             patch("brain_core.agent.GeminiAgent", FakeGeminiAgent):
            accepted = routes["/api/run"](RunRequest(
                agent="gemini", max_iterations=1, horizon=48, gemini_api_key=secret,
            ))
            deadline = time.time() + 15
            while time.time() < deadline:
                status = routes["/api/status/{request_id}"](accepted["request_id"])
                if status["status"] in {"completed", "failed"}:
                    break
                time.sleep(0.05)
        self.assertEqual(status["status"], "completed", status)
        self.assertEqual(FakeGeminiAgent.received_key, secret)
        with closing(sqlite3.connect(state_db)) as conn:
            stored = conn.execute(
                "SELECT request_json FROM brain_api_run WHERE request_id=?",
                (accepted["request_id"],),
            ).fetchone()[0]
        self.assertNotIn(secret, stored)

    def test_db_tools_and_gemini_loop_run_without_network_or_tokens(self):
        original_count = optimizer_run_count(self.sample)
        work_db = create_working_copy(self.sample, "dry-run", self.root / "working")
        result = run_agentic_workflow(
            str(work_db), main.load_optimizer(), MockAgent(),
            max_iterations=1, horizon=12,
        )
        run_id = result["best_candidate"]["run_id"]
        tools = AgentDatabaseTools(work_db, run_id)
        summary = tools.execute("get_candidate_summary", {})
        self.assertTrue(summary["validation"]["passed"])
        self.assertEqual(tools.execute("get_constraint_summary", {})["failed_checks"], 0)
        self.assertTrue(tools.execute("get_tariff_profile", {})["is_synthetic"])
        window = tools.execute("get_interval_window", {"start_index": 0, "count": 3})
        self.assertEqual(len(window["intervals"]), 3)
        writable_tools = AgentDatabaseTools(work_db, run_id, "AGENT-test", 1,
                                            allow_parameter_writes=True)
        write_result = writable_tools.execute(
            "set_optimizer_parameter", {"name": "peak_penalty", "value": 1.5}
        )
        self.assertTrue(write_result["accepted"])
        self.assertEqual(writable_tools.get_parameter_updates(), {"peak_penalty": 1.5})
        with self.assertRaisesRegex(ValueError, "between"):
            writable_tools.execute(
                "set_optimizer_parameter", {"name": "peak_penalty", "value": 1000}
            )
        with self.assertRaises(PermissionError):
            AgentDatabaseTools(self.sample, run_id, "AGENT-test", 1,
                               allow_parameter_writes=True).execute(
                "set_optimizer_parameter", {"name": "peak_penalty", "value": 1.5}
            )
        with closing(tools._connect()) as conn:
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("UPDATE optimization_run SET status='changed'")
        with self.assertRaisesRegex(ValueError, "Unsupported database tool"):
            tools.execute("run_arbitrary_sql", {"sql": "DROP TABLE interval_summary"})

        requests = []

        def fake_interaction(body):
            requests.append(body)
            if len(requests) == 1:
                return {"steps": [{"type": "function_call", "id": "call-1",
                                   "name": "get_constraint_summary", "arguments": {}}]}
            if len(requests) == 2:
                history = body["input"]
                self.assertTrue(any(step.get("type") == "function_result"
                                    and step.get("call_id") == "call-1" for step in history))
                return {"steps": [{"type": "model_output", "content": [{
                    "type": "text", "text": json.dumps({
                        "action": "stop", "parameter_updates": {},
                        "surplus_policy": None, "reason": "Dry-run checks passed.",
                        "stop_reason": "dry_run_complete",
                    }),
                }]}]}
            return {"steps": [{"type": "model_output", "content": [{
                "type": "text", "text": json.dumps({"report": "Token-free simulated report."}),
            }]}]}

        agent = GeminiAgent(api_key="dry-run-placeholder", interaction_requester=fake_interaction)
        decision = agent.evaluate({"iteration": 1, "candidate": summary}, tool_executor=tools.execute)
        report = agent.generate_report({"best_candidate": result["best_candidate"]})
        self.assertEqual(decision["action"], "stop")
        self.assertEqual(report, "Token-free simulated report.")
        self.assertEqual(len(requests), 3)
        self.assertTrue(all(request["store"] is False for request in requests))
        self.assertTrue(all("temperature" not in request["generation_config"]
                            for request in requests))
        self.assertTrue(all(request["generation_config"].get("thinking_level") == "low"
                            for request in requests))
        self.assertEqual(optimizer_run_count(self.sample), original_count)
        self.assertGreater(optimizer_run_count(work_db), original_count)

    def test_api_runs_on_a_copy_and_timeseries_uses_that_copy(self):
        original_count = optimizer_run_count(self.sample)
        run_dir = self.root / "api-runs"
        state_db = self.root / "api-state.sqlite"
        app = create_app(str(self.sample), main.load_optimizer(), default_agent="mock",
                         run_dir=str(run_dir), state_db_path=str(state_db))
        routes = {route.path: route.endpoint for route in app.routes if hasattr(route, "path")}
        accepted = routes["/api/run"](RunRequest(agent="mock", max_iterations=1, horizon=12))
        request_id = accepted["request_id"]
        deadline = time.time() + 15
        while time.time() < deadline:
            status = routes["/api/status/{request_id}"](request_id)
            if status["status"] in {"completed", "failed"}:
                break
            time.sleep(0.05)
        self.assertEqual(status["status"], "completed", status)
        result = routes["/api/result/{request_id}"](request_id)
        optimizer_id = result["best_candidate"]["run_id"]
        series = routes["/api/timeseries/{run_id}"](optimizer_id)
        self.assertEqual(len(series["timestamps"]), 12)
        self.assertTrue((run_dir / f"{request_id}.sqlite").is_file())
        self.assertEqual(optimizer_run_count(self.sample), original_count)
        self.assertTrue(state_db.is_file())

    def test_gemini_iteration_cap_is_enforced_and_mock_retains_12(self):
        class NoopExecutor:
            def __init__(self, *args, **kwargs):
                self.submitted = []

            def submit(self, *args, **kwargs):
                self.submitted.append((args, kwargs))

        with patch("brain_core.api.ThreadPoolExecutor", NoopExecutor):
            app = create_app(
                str(self.sample), main.load_optimizer(), default_agent="gemini",
                run_dir=str(self.root / "iteration-runs"),
                state_db_path=str(self.root / "iteration-state.sqlite"),
            )
        routes = {route.path: route.endpoint for route in app.routes if hasattr(route, "path")}

        mock_request = RunRequest(agent="mock", max_iterations=12)
        self.assertEqual(mock_request.max_iterations, 12)
        accepted = routes["/api/run"](mock_request)
        self.assertEqual(accepted["agent"], "mock")

        for agent in ("gemini", "auto"):
            with self.subTest(agent=agent), self.assertRaises(HTTPException) as caught:
                routes["/api/run"](RunRequest(agent=agent, max_iterations=6))
            self.assertEqual(caught.exception.status_code, 422)

    def test_database_tuning_write_drives_next_iteration_and_selects_best_aleph(self):
        original_count = optimizer_run_count(self.sample)
        work_db = create_working_copy(self.sample, "tuning-dry-run", self.root / "tuning-runs")

        class ToolWritingAgent:
            name = "fake-tool-writing-agent"
            supports_database_tools = True

            def evaluate(self, payload, tool_executor=None):
                if payload["iteration"] == 1:
                    result = tool_executor("set_optimizer_parameter", {
                        "name": "peak_penalty", "value": 1.5,
                    })
                    self.asserted_write = result["accepted"]
                    return {
                        "action": "continue", "parameter_updates": {},
                        "surplus_policy": None, "reason": "Try the bounded next setting.",
                        "stop_reason": "",
                    }
                self.seen_next_parameter = payload["optimizer_parameters"]["peak_penalty"]
                return {
                    "action": "stop", "parameter_updates": {},
                    "surplus_policy": None, "reason": "Finished the dry run.",
                    "stop_reason": "test_complete",
                }

            def generate_report(self, payload):
                return "Offline parameter-write test report."

        agent = ToolWritingAgent()
        result = run_agentic_workflow(
            str(work_db), main.load_optimizer(), agent, max_iterations=2, horizon=48,
        )
        self.assertTrue(agent.asserted_write)
        self.assertEqual(agent.seen_next_parameter, 1.5)
        scores = [record["aleph_score"] for record in result["candidate_history"]]
        self.assertEqual(result["best_candidate"]["metrics"]["aleph_mainsys"], max(scores))
        self.assertEqual(optimizer_run_count(self.sample), original_count)


if __name__ == "__main__":
    unittest.main()
