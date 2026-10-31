"""Gemini REST adapter and deterministic mock agent."""
from __future__ import annotations
import json
import os
import urllib.error
import urllib.request

from .agent_tools import TOOL_DECLARATIONS


class AgentError(RuntimeError):
    pass


class GeminiAgent:
    """Gemini Interactions API adapter (REST; no SDK dependency required)."""
    name = "gemini-interactions"
    supports_database_tools = True

    def __init__(self, api_key: str | None = None, model: str | None = None,
                 timeout: int = 60, interaction_requester=None):
        configured_key = api_key if api_key is not None else os.getenv("GEMINI_API_KEY")
        self.api_key = configured_key.strip() if isinstance(configured_key, str) else None
        self.model = model or os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
        self.timeout = timeout
        self._interaction_requester = interaction_requester
        if not self.api_key:
            raise AgentError("GEMINI_API_KEY is not set")
        if len(self.api_key) > 256:
            raise AgentError("Gemini API key exceeds the 256-character limit")
        if any(ch.isspace() for ch in self.api_key):
            raise AgentError("Gemini API key contains whitespace; use the raw key without spaces or line breaks")
        if len(self.api_key) >= 2 and self.api_key[0] in "'\"" and self.api_key[-1] == self.api_key[0]:
            raise AgentError("Gemini API key includes surrounding quote characters; provide the raw key value")

    def _request_interaction(self, body: dict) -> dict:
        if self._interaction_requester is not None:
            return self._interaction_requester(body)
        url = "https://generativelanguage.googleapis.com/v1beta/interactions"
        request = urllib.request.Request(
            url, data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": self.api_key,
            }, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:1500]
            raise AgentError(f"Gemini HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise AgentError(f"Gemini request failed: {exc}") from exc
        try:
            envelope = json.loads(raw)
            if not isinstance(envelope, dict):
                raise ValueError("Interaction response is not an object")
            return envelope
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise AgentError("Gemini returned an unexpected Interactions API response") from exc

    @staticmethod
    def _output_text(envelope: dict) -> str:
        pieces = []
        for step in envelope.get("steps", []):
            if step.get("type") == "model_output":
                for part in step.get("content", []):
                    if part.get("type") == "text" and isinstance(part.get("text"), str):
                        pieces.append(part["text"])
        text = "\n".join(pieces).strip()
        if not text:
            raise AgentError("Gemini returned no model_output text")
        return text

    def _generate_text(self, system_instruction: str, payload: dict,
                       max_output_tokens: int = 1600,
                       response_schema: dict | None = None) -> str:
        generation_config = {"max_output_tokens": max_output_tokens}
        # Gemini 3.8 Flash removed temperature sampling controls. Keep its
        # reasoning budget at the documented low setting for this bounded
        # controller, reducing unnecessary thinking tokens in the first trial.
        if self.model == "gemini-3.8-flash":
            generation_config["thinking_level"] = "low"
        body = {
            "model": self.model,
            "system_instruction": system_instruction,
            "input": json.dumps(payload, separators=(",", ":"), ensure_ascii=False),
            "generation_config": generation_config,
            "store": False,
        }
        if response_schema is not None:
            body["response_format"] = {
                "type": "text", "mime_type": "application/json", "schema": response_schema,
            }
        return self._output_text(self._request_interaction(body))

    @staticmethod
    def _decision_from_text(text: str) -> dict:
        try:
            result = json.loads(text)
        except json.JSONDecodeError as exc:
            raise AgentError("Gemini decision was not valid JSON") from exc
        if not isinstance(result, dict):
            raise AgentError("Gemini decision JSON was not an object")
        return result

    def evaluate(self, payload: dict, tool_executor=None) -> dict:
        instruction = (
            "You are the decision layer of a constrained microgrid optimizer. "
            "Analyze only the supplied evidence. Do not invent missing tariffs, export "
            "capabilities, thermal storage, forecasts, or measurements. The deterministic "
            "optimizer and validator own all physical calculations. Your optimization target "
            "is to increase aleph_mainsys using its fixed published weights; do not alter Aleph "
            "weights or any source data. You may only update the four listed optimizer "
            "parameters through the bounded set_optimizer_parameter tool, or stop. These "
            "parameter writes apply to the next iteration. Prefer stopping when the candidate "
            "Aleph score is not improving. If aleph_mainsys is null, stop without tuning. "
            "If historical surplus patterns "
            "are supplied, you may return surplus_policy with store_pct and export_pct. "
            "It is advisory only; export is independently disabled without verified export "
            "capability. Return ONLY a JSON object with keys: action ('continue'|'stop'), "
            "parameter_updates (object), surplus_policy (object or null), reason (string), "
            "stop_reason (string)."
        )
        decision_schema = {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["continue", "stop"]},
                "parameter_updates": {
                    "type": "object",
                    "properties": {
                        "peak_penalty": {"type": "number"},
                        "curtailment_penalty": {"type": "number"},
                        "flex_risk_penalty": {"type": "number"},
                        "shedding_scale": {"type": "number"},
                    },
                    "required": [],
                },
                "surplus_policy": {
                    "type": ["object", "null"],
                    "properties": {
                        "store_pct": {"type": "number"},
                        "export_pct": {"type": "number"},
                    },
                    "required": ["store_pct", "export_pct"],
                },
                "reason": {"type": "string"},
                "stop_reason": {"type": "string"},
            },
            "required": ["action", "parameter_updates", "surplus_policy", "reason", "stop_reason"],
        }
        if tool_executor is None:
            text = self._generate_text(
                instruction, payload, max_output_tokens=1200,
                response_schema=decision_schema,
            )
            return self._decision_from_text(text)

        history = [{
            "type": "user_input",
            "content": [{"type": "text", "text": json.dumps(
                payload, separators=(",", ":"), ensure_ascii=False
            )}],
        }]
        tool_calls_used = 0
        max_tool_calls = 4
        for _ in range(max_tool_calls + 1):
            generation_config = {"max_output_tokens": 1200}
            if self.model == "gemini-3.8-flash":
                generation_config["thinking_level"] = "low"
            envelope = self._request_interaction({
                "model": self.model,
                "system_instruction": instruction + (
                    " You have read-only evidence tools and one narrowly scoped tuning-write tool "
                    "for this candidate's marked run database copy. Use the write tool only for "
                    "bounded optimizer parameters, never request or emit SQL, and never try to "
                    "change dispatch or measured/configuration data. After inspection, return "
                    "the required decision JSON; any parameter_updates must agree with tool writes."
                ),
                "input": history,
                "tools": TOOL_DECLARATIONS,
                "generation_config": generation_config,
                "store": False,
            })
            steps = envelope.get("steps")
            if not isinstance(steps, list):
                raise AgentError("Gemini interaction did not contain a steps array")
            history.extend(steps)
            calls = [step for step in steps if step.get("type") == "function_call"]
            if not calls:
                return self._decision_from_text(self._output_text(envelope))
            for call in calls:
                tool_calls_used += 1
                if tool_calls_used > max_tool_calls:
                    raise AgentError("Gemini exceeded the database tool-call budget")
                name = call.get("name")
                call_id = call.get("id")
                arguments = call.get("arguments", {})
                if not isinstance(call_id, str) or not call_id or not isinstance(name, str):
                    raise AgentError("Gemini returned a malformed function_call step")
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except json.JSONDecodeError:
                        arguments = None
                try:
                    if not isinstance(arguments, dict):
                        raise ValueError("Tool call arguments are not an object")
                    output = tool_executor(name, arguments)
                except Exception as exc:
                    output = {"tool_error": f"{type(exc).__name__}: {exc}"}
                history.append({
                    "type": "function_result",
                    "name": name,
                    "call_id": call_id,
                    "result": [{"type": "text", "text": json.dumps(
                        output, separators=(",", ":"), ensure_ascii=False
                    )}],
                })
        raise AgentError("Gemini did not return a final decision within the tool-call budget")

    def generate_report(self, payload: dict) -> str:
        instruction = (
            "Write a concise but technically comprehensive microgrid optimization report. "
            "Use only supplied facts. Clearly separate synthetic dataset facts, optimizer "
            "outputs, AI recommendations, assumptions, and unavailable metrics. Never claim "
            "grid export was optimized: this optimizer has no export decision variable. "
            "Label the tariff and Aleph as synthetic-demo outputs when the supplied metrics say so. "
            "Use computed cost and Aleph values only as supplied. Explain that battery "
            "discharge is internal energy transfer. Return ONLY a JSON object "
            "with one string field named 'report'."
        )
        text = self._generate_text(
            instruction,
            {"report_request": "Generate the final technical report.", "final_context": payload},
            max_output_tokens=2600,
            response_schema={
                "type": "object",
                "properties": {"report": {"type": "string"}},
                "required": ["report"],
            },
        )
        try:
            result = json.loads(text)
        except json.JSONDecodeError as exc:
            raise AgentError("Gemini report response was not valid JSON") from exc
        report = result.get("report") if isinstance(result, dict) else None
        if not isinstance(report, str) or not report.strip():
            raise AgentError("Gemini report response did not contain a non-empty 'report'")
        return report.strip()


class MockAgent:
    """Deterministic offline agent for testing the complete orchestration path."""
    name = "mock-deterministic"
    supports_database_tools = False

    def evaluate(self, payload: dict, tool_executor=None) -> dict:
        iteration = int(payload["iteration"])
        params = payload["optimizer_parameters"]
        if iteration == 1 and payload["iteration_limit"] > 1:
            return {
                "action": "continue",
                "parameter_updates": {"peak_penalty": min(100.0, params["peak_penalty"] * 1.1)},
                "reason": "Offline demonstration: test a modestly stronger peak-import preference.",
                "stop_reason": "",
                "surplus_policy": {"store_pct": 100.0, "export_pct": 0.0},
            }
        return {
            "action": "stop",
            "parameter_updates": {},
            "reason": "Offline demonstration completed; no further evidence-backed change proposed.",
            "stop_reason": "mock_agent_completion",
            "surplus_policy": {"store_pct": 100.0, "export_pct": 0.0},
        }

    def generate_report(self, payload: dict) -> str:
        s = payload["best_candidate"]
        e = s["energy_kwh"]
        m = s["metrics"]
        aleph = m.get("aleph_mainsys")
        aleph_text = f"{aleph:.2f}/100" if isinstance(aleph, (int, float)) else "unavailable"
        return "\n".join([
            "AFTERHOURS MICROGRID SCHEDULING — TECHNICAL RUN REPORT",
            "",
            f"Run: {payload['agent_run_id']}",
            f"Optimizer run: {s['run_id']}",
            f"Iterations: {payload['iterations']}",
            f"Agent: {payload['agent']}",
            "",
            "RESULTS",
            f"Intervals: {s['intervals']}",
            f"Peak grid import: {s['power']['max_grid_import_kw']:.3f} kW",
            f"Average grid import: {s['power']['average_grid_import_kw']:.3f} kW",
            f"Grid import energy: {e['grid_import']:.3f} kWh",
            f"Synthetic tariff grid import cost: {payload['best_candidate']['cost']['import_cost']:.2f} {payload['best_candidate']['cost']['currency_code']}",
            f"Aleph microgrid score: {aleph_text}",
            f"Solar generation: {e['solar_generation_gross']:.3f} kWh",
            f"Load served: {e['load_served']:.3f} kWh",
            f"Battery charge/discharge: {e['battery_charge_internal']:.3f}/{e['battery_discharge_internal']:.3f} kWh",
            f"Battery SOC range: {s['battery']['soc_min_pct']:.3f}–{s['battery']['soc_max_pct']:.3f}%",
            "",
            "SURPLUS ADVISORY",
            f"Candidate curtailed-PV surplus: {payload['surplus_allocation']['candidate_surplus_kwh']:.3f} kWh",
            f"Theoretical battery headroom ceiling: {payload['surplus_allocation']['storage_capacity_ceiling_kwh']:.3f} kWh",
            "Actual storage/export dispatch recommendation: not issued by this prototype",
            f"Unallocated candidate surplus: {payload['surplus_allocation']['unallocated_kwh']:.3f} kWh",
            "METRICS",
            f"eta_mainsys useful-load utilization proxy: {m['eta_mainsys']}",
            f"eta_cost_mainsys economic component: {payload['best_candidate']['metrics']['eta_cost_mainsys']}",
            f"Aleph component scores: {payload['best_candidate']['metrics']['aleph_components']}",
            "",
            "LIMITATIONS",
            *[f"- {x}" for x in s["limitations"]],
            "",
            "Tariff schedule and export credit are synthetic demonstration assumptions, not a utility contract.",
            "This report was generated by the deterministic mock agent. Configure GEMINI_API_KEY and select --agent gemini for Gemini-generated explanations.",
        ])
