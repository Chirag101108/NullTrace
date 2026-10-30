"""Gemini REST adapter and deterministic mock agent."""
from __future__ import annotations
import json
import os
import urllib.error
import urllib.request


class AgentError(RuntimeError):
    pass


class GeminiAgent:
    """Gemini Interactions API adapter (REST; no SDK dependency required)."""
    name = "gemini-interactions"

    def __init__(self, api_key: str | None = None, model: str | None = None,
                 timeout: int = 60):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        self.model = model or os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
        self.timeout = timeout
        if not self.api_key:
            raise AgentError("GEMINI_API_KEY is not set")

    def _generate_text(self, system_instruction: str, payload: dict,
                       max_output_tokens: int = 1600) -> str:
        # Interactions is Google's recommended API primitive for agentic workflows.
        url = "https://generativelanguage.googleapis.com/v1beta/interactions"
        prompt = (
            system_instruction
            + "\n\nINPUT DATA (JSON):\n"
            + json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        )
        body = {
            "model": self.model,
            "input": prompt,
            "generation_config": {
                "temperature": 0.15,
                "max_output_tokens": max_output_tokens,
            },
            "store": False,
        }
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
            pieces = []
            for step in envelope.get("steps", []):
                if step.get("type") == "model_output":
                    for part in step.get("content", []):
                        if part.get("type") == "text" and isinstance(part.get("text"), str):
                            pieces.append(part["text"])
            text = "\n".join(pieces).strip()
            if not text:
                raise ValueError("No model_output text in interaction")
            return text
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise AgentError("Gemini returned an unexpected Interactions API response") from exc

    def evaluate(self, payload: dict) -> dict:
        instruction = (
            "You are the decision layer of a constrained microgrid optimizer. "
            "Analyze only the supplied evidence. Do not invent missing tariffs, export "
            "capabilities, thermal storage, forecasts, or measurements. The deterministic "
            "optimizer and validator own all physical calculations. You may only recommend "
            "bounded updates to the listed optimizer parameters or stop. Prefer stopping "
            "when there is no evidence-backed improvement. If historical surplus patterns "
            "are supplied, you may return surplus_policy with store_pct and export_pct. "
            "It is advisory only; export is independently disabled without verified export "
            "capability. Return ONLY a JSON object with keys: action ('continue'|'stop'), "
            "parameter_updates (object), surplus_policy (object or null), reason (string), "
            "stop_reason (string)."
        )
        text = self._generate_text(instruction, payload, max_output_tokens=1200)
        try:
            result = json.loads(text)
        except json.JSONDecodeError as exc:
            raise AgentError("Gemini decision was not valid JSON") from exc
        if not isinstance(result, dict):
            raise AgentError("Gemini decision JSON was not an object")
        return result

    def generate_report(self, payload: dict) -> str:
        instruction = (
            "Write a concise but technically comprehensive microgrid optimization report. "
            "Use only supplied facts. Clearly separate synthetic dataset facts, optimizer "
            "outputs, AI recommendations, assumptions, and unavailable metrics. Never claim "
            "grid export was optimized or tariffs were known if they were absent. Explain "
            "that battery discharge is internal energy transfer. Return ONLY a JSON object "
            "with one string field named 'report'."
        )
        text = self._generate_text(
            instruction,
            {"report_request": "Generate the final technical report.", "final_context": payload},
            max_output_tokens=2600,
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

    def evaluate(self, payload: dict) -> dict:
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
            "eta_cost_mainsys: unavailable (no tariff/export model)",
            "aleph_mainsys: unavailable (depends on eta_cost_mainsys)",
            "",
            "LIMITATIONS",
            *[f"- {x}" for x in s["limitations"]],
            "",
            "This report was generated by the deterministic mock agent. Configure GEMINI_API_KEY and select --agent gemini for Gemini-generated reasoning and reporting.",
        ])
