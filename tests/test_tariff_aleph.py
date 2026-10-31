import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

import main
from brain_core.agent import MockAgent
from brain_core.aleph import ALEPH_WEIGHTS, build_run_timeseries
from brain_core.orchestrator import run_agentic_workflow
from brain_core.tariffs import _profile_and_periods, load_active_tariff, validate_tariff


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DB = ROOT / "microgrid_afterhours_hurdle_v1" / "microgrid.sqlite"
MIGRATION = ROOT / "microgrid_afterhours_hurdle_v1" / "schema_update_tariffs.sql"


class TariffAlephTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="afterhours-tariff-test-")
        self.db_path = Path(self.temp.name) / "microgrid.sqlite"
        shutil.copy2(SOURCE_DB, self.db_path)

    def tearDown(self):
        self.temp.cleanup()

    def connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def test_migration_is_repeatable_and_periods_cover_week(self):
        conn = self.connect()
        conn.executescript(MIGRATION.read_text(encoding="utf-8"))
        conn.executescript(MIGRATION.read_text(encoding="utf-8"))
        profile, periods = _profile_and_periods(conn)
        validate_tariff(conn, profile, periods)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM tariff_profiles").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM tariff_periods").fetchone()[0], 4)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM interval_summary").fetchone()[0], 672)
        self.assertFalse(conn.execute("PRAGMA foreign_key_check").fetchall())
        conn.close()

    def test_period_boundaries_select_expected_import_and_export_rates(self):
        conn = self.connect()
        sample_times = [
            "2026-10-19T05:45:00+05:30",
            "2026-10-19T06:00:00+05:30",
            "2026-10-19T17:00:00+05:30",
            "2026-10-19T22:00:00+05:30",
        ]
        tariff = load_active_tariff(conn, sample_times)
        self.assertEqual([x["import_rate_per_kwh"] for x in tariff["rates"]], [5.5, 7.0, 10.5, 7.0])
        self.assertEqual([x["export_rate_per_kwh"] for x in tariff["rates"]], [5.5, 7.0, 10.5, 7.0])
        self.assertTrue(tariff["profile"]["is_synthetic"])
        conn.close()

    def test_weekly_gap_is_rejected(self):
        conn = self.connect()
        profile, periods = _profile_and_periods(conn)
        periods[0] = {**periods[0], "end_minute": 345}
        with self.assertRaisesRegex(ValueError, "cover each local week minute exactly once"):
            validate_tariff(conn, profile, periods)
        conn.close()

    def test_full_mock_run_accounts_costs_and_produces_aleph_series(self):
        result = run_agentic_workflow(
            str(self.db_path), main.load_optimizer(), MockAgent(),
            max_iterations=1, horizon=48,
        )
        candidate = result["best_candidate"]
        self.assertTrue(candidate["validation"]["passed"])
        self.assertEqual(candidate["validation"]["failed"], 0)
        self.assertEqual(len(ALEPH_WEIGHTS), 6)
        self.assertAlmostEqual(sum(ALEPH_WEIGHTS.values()), 1.0)
        self.assertGreaterEqual(candidate["aleph"]["score"], 0)
        self.assertLessEqual(candidate["aleph"]["score"], 100)
        self.assertEqual(candidate["cost"]["data_quality"], "synthetic")

        conn = self.connect()
        run_id = candidate["run_id"]
        series = build_run_timeseries(conn, run_id)
        self.assertEqual(len(series["timestamps"]), 48)
        self.assertEqual(len(series["aleph"]), 48)
        self.assertEqual(len(series["components"]["energy_adequacy"]), 48)
        rows = conn.execute("""
            SELECT r.grid_import_kw,c.import_rate_per_kwh,c.import_cost
            FROM optimization_result r JOIN optimization_energy_cost c
              USING(optimization_run_id,interval_start)
            WHERE r.optimization_run_id=?
        """, (run_id,)).fetchall()
        expected_cost = sum(float(r[0])*0.25*float(r[1]) for r in rows)
        self.assertAlmostEqual(expected_cost, candidate["cost"]["import_cost"], places=6)
        snapshot_count = conn.execute(
            "SELECT COUNT(*) FROM optimization_tariff_snapshot WHERE optimization_run_id=?",
            (run_id,),
        ).fetchone()[0]
        self.assertEqual(snapshot_count, 1)
        conn.close()

    def test_old_optimizer_run_exposes_missing_tariff_as_unavailable(self):
        conn = self.connect()
        old_run = conn.execute("SELECT optimization_run_id FROM optimization_run LIMIT 1").fetchone()[0]
        series = build_run_timeseries(conn, old_run)
        self.assertEqual(series["data_quality"], "unavailable (pre-tariff run)")
        self.assertIsNone(series["aleph_summary"]["score"])
        self.assertIn("economic_performance", series["aleph_summary"]["unavailable_components"])
        conn.close()


if __name__ == "__main__":
    unittest.main()
