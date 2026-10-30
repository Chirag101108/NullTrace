
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS battery_degradation_model (
    model_id TEXT PRIMARY KEY,
    battery_asset_id TEXT NOT NULL REFERENCES asset(asset_id),
    model_name TEXT NOT NULL,
    replacement_cost REAL NOT NULL CHECK(replacement_cost>=0),
    expected_equivalent_full_cycles REAL NOT NULL CHECK(expected_equivalent_full_cycles>0),
    nominal_capacity_kwh REAL NOT NULL CHECK(nominal_capacity_kwh>0),
    throughput_cost_per_kwh REAL NOT NULL CHECK(throughput_cost_per_kwh>=0),
    charge_efficiency REAL NOT NULL CHECK(charge_efficiency>0 AND charge_efficiency<=1),
    discharge_efficiency REAL NOT NULL CHECK(discharge_efficiency>0 AND discharge_efficiency<=1),
    model_version TEXT NOT NULL,
    assumptions TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS battery_health (
    interval_start TEXT NOT NULL,
    battery_asset_id TEXT NOT NULL REFERENCES asset(asset_id),
    state_of_health_pct REAL NOT NULL CHECK(state_of_health_pct>0 AND state_of_health_pct<=100),
    nominal_capacity_kwh REAL NOT NULL CHECK(nominal_capacity_kwh>0),
    usable_capacity_kwh REAL NOT NULL CHECK(usable_capacity_kwh>0),
    internal_resistance_ohm REAL,
    battery_temperature_c REAL,
    equivalent_full_cycles REAL NOT NULL DEFAULT 0 CHECK(equivalent_full_cycles>=0),
    cumulative_capacity_fade_kwh REAL NOT NULL DEFAULT 0 CHECK(cumulative_capacity_fade_kwh>=0),
    PRIMARY KEY(interval_start,battery_asset_id)
);

CREATE TABLE IF NOT EXISTS battery_degradation (
    interval_start TEXT NOT NULL,
    battery_asset_id TEXT NOT NULL REFERENCES asset(asset_id),
    charge_throughput_kwh REAL NOT NULL CHECK(charge_throughput_kwh>=0),
    discharge_throughput_kwh REAL NOT NULL CHECK(discharge_throughput_kwh>=0),
    total_throughput_kwh REAL NOT NULL CHECK(total_throughput_kwh>=0),
    depth_of_discharge_pct REAL,
    c_rate REAL,
    cycle_ageing_increment_pct REAL NOT NULL DEFAULT 0,
    calendar_ageing_increment_pct REAL NOT NULL DEFAULT 0,
    total_capacity_fade_increment_kwh REAL NOT NULL DEFAULT 0,
    estimated_degradation_cost REAL NOT NULL DEFAULT 0,
    PRIMARY KEY(interval_start,battery_asset_id)
);

CREATE TABLE IF NOT EXISTS flexibility_declaration (
    declaration_id INTEGER PRIMARY KEY,
    interval_start TEXT NOT NULL,
    provider_asset_id TEXT NOT NULL REFERENCES asset(asset_id),
    provider_type TEXT NOT NULL DEFAULT 'synthetic_load_proxy',
    declared_power_kw REAL NOT NULL CHECK(declared_power_kw>=0),
    declared_energy_kwh REAL NOT NULL CHECK(declared_energy_kwh>=0),
    availability_start TEXT NOT NULL,
    availability_end TEXT NOT NULL,
    max_duration_minutes INTEGER NOT NULL CHECK(max_duration_minutes>=0),
    notice_minutes INTEGER NOT NULL CHECK(notice_minutes>=0),
    rebound_energy_kwh REAL NOT NULL DEFAULT 0 CHECK(rebound_energy_kwh>=0),
    operating_constraints TEXT NOT NULL,
    declaration_source TEXT NOT NULL,
    declaration_version TEXT NOT NULL,
    UNIQUE(interval_start,provider_asset_id)
);

CREATE TABLE IF NOT EXISTS flexibility_verification (
    interval_start TEXT NOT NULL,
    provider_asset_id TEXT NOT NULL REFERENCES asset(asset_id),
    verified_power_kw REAL NOT NULL CHECK(verified_power_kw>=0),
    verified_energy_kwh REAL NOT NULL CHECK(verified_energy_kwh>=0),
    usable_power_kw REAL NOT NULL CHECK(usable_power_kw>=0),
    confidence REAL NOT NULL CHECK(confidence BETWEEN 0 AND 1),
    reliability_score REAL CHECK(reliability_score BETWEEN 0 AND 1),
    verification_method TEXT NOT NULL,
    verification_timestamp TEXT NOT NULL,
    verification_notes TEXT NOT NULL,
    PRIMARY KEY(interval_start,provider_asset_id)
);

CREATE TABLE IF NOT EXISTS flexibility_delivery (
    interval_start TEXT NOT NULL,
    provider_asset_id TEXT NOT NULL REFERENCES asset(asset_id),
    committed_power_kw REAL NOT NULL DEFAULT 0 CHECK(committed_power_kw>=0),
    requested_power_kw REAL NOT NULL DEFAULT 0 CHECK(requested_power_kw>=0),
    delivered_power_kw REAL NOT NULL DEFAULT 0 CHECK(delivered_power_kw>=0),
    shortfall_power_kw REAL NOT NULL DEFAULT 0 CHECK(shortfall_power_kw>=0),
    committed_energy_kwh REAL NOT NULL DEFAULT 0 CHECK(committed_energy_kwh>=0),
    delivered_energy_kwh REAL NOT NULL DEFAULT 0 CHECK(delivered_energy_kwh>=0),
    shortfall_energy_kwh REAL NOT NULL DEFAULT 0 CHECK(shortfall_energy_kwh>=0),
    delivery_status TEXT NOT NULL DEFAULT 'not_dispatched',
    PRIMARY KEY(interval_start,provider_asset_id)
);

CREATE TABLE IF NOT EXISTS agent_run (
    agent_run_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    agent_name TEXT NOT NULL,
    model_version TEXT NOT NULL,
    run_status TEXT NOT NULL,
    input_snapshot TEXT NOT NULL,
    output_summary TEXT
);

CREATE TABLE IF NOT EXISTS agent_claim (
    claim_id TEXT PRIMARY KEY,
    agent_run_id TEXT NOT NULL REFERENCES agent_run(agent_run_id),
    claim_timestamp TEXT NOT NULL,
    claim_type TEXT NOT NULL,
    subject_asset_id TEXT REFERENCES asset(asset_id),
    claimed_value REAL,
    claimed_unit TEXT,
    claim_text TEXT NOT NULL,
    evidence_reference TEXT NOT NULL,
    verification_status TEXT NOT NULL DEFAULT 'pending',
    verification_reason TEXT,
    corrected_value REAL,
    verifier_version TEXT
);

CREATE TABLE IF NOT EXISTS claim_verification (
    verification_id INTEGER PRIMARY KEY,
    claim_id TEXT NOT NULL REFERENCES agent_claim(claim_id),
    verified_at TEXT NOT NULL,
    verification_status TEXT NOT NULL CHECK(verification_status IN ('passed','failed','inconclusive')),
    evidence_check TEXT NOT NULL,
    expected_value REAL,
    observed_value REAL,
    tolerance REAL,
    explanation TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS optimization_run (
    optimization_run_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    solver_name TEXT NOT NULL,
    solver_version TEXT NOT NULL,
    model_version TEXT NOT NULL,
    status TEXT NOT NULL,
    objective_value REAL,
    objective_peak_component REAL,
    objective_shedding_component REAL,
    objective_curtailment_component REAL,
    objective_degradation_component REAL,
    objective_flexibility_component REAL,
    degradation_cost_per_kwh REAL NOT NULL,
    flexibility_penalty_per_kwh REAL NOT NULL,
    peak_penalty_per_kw REAL NOT NULL,
    terminal_soc_target_pct REAL NOT NULL,
    input_database TEXT NOT NULL,
    notes TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS optimization_result (
    optimization_run_id TEXT NOT NULL REFERENCES optimization_run(optimization_run_id),
    interval_start TEXT NOT NULL,
    grid_import_kw REAL NOT NULL,
    battery_charge_kw REAL NOT NULL,
    battery_discharge_kw REAL NOT NULL,
    pv_curtailment_kw REAL NOT NULL,
    battery_soc_start_pct REAL NOT NULL,
    battery_soc_end_pct REAL NOT NULL,
    load_shed_kw REAL NOT NULL,
    flexibility_committed_kw REAL NOT NULL,
    flexibility_expected_shortfall_kw REAL NOT NULL,
    estimated_degradation_cost REAL NOT NULL,
    objective_contribution REAL NOT NULL,
    PRIMARY KEY(optimization_run_id,interval_start)
);

CREATE TABLE IF NOT EXISTS optimization_load_result (
    optimization_run_id TEXT NOT NULL REFERENCES optimization_run(optimization_run_id),
    interval_start TEXT NOT NULL,
    provider_asset_id TEXT NOT NULL REFERENCES asset(asset_id),
    shed_power_kw REAL NOT NULL,
    usable_flexibility_kw REAL NOT NULL,
    PRIMARY KEY(optimization_run_id,interval_start,provider_asset_id)
);

CREATE TABLE IF NOT EXISTS optimization_constraint_check (
    optimization_run_id TEXT NOT NULL REFERENCES optimization_run(optimization_run_id),
    interval_start TEXT NOT NULL,
    constraint_name TEXT NOT NULL,
    measured_value REAL,
    limit_value REAL,
    passed INTEGER NOT NULL CHECK(passed IN (0,1)),
    explanation TEXT NOT NULL,
    PRIMARY KEY(optimization_run_id,interval_start,constraint_name)
);

CREATE INDEX IF NOT EXISTS idx_flex_decl_interval ON flexibility_declaration(interval_start);
CREATE INDEX IF NOT EXISTS idx_agent_claim_run ON agent_claim(agent_run_id);
CREATE INDEX IF NOT EXISTS idx_opt_result_interval ON optimization_result(interval_start);
