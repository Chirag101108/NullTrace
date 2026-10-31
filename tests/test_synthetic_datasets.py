import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import main
from brain_core.api import RunRequest, create_app
from brain_core.synthetic_datasets import create_variant


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "microgrid_afterhours_hurdle_v1" / "microgrid.sqlite"


class SyntheticDatasetTests(unittest.TestCase):
    def test_variant_is_bounded_balanced_and_does_not_change_source(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "variant.sqlite"
            create_variant(SOURCE, target, "scenario-test", "Test Variant", 7)
            src = sqlite3.connect(f"file:{SOURCE}?mode=ro", uri=True)
            db = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
            try:
                self.assertEqual(src.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(src.execute("SELECT COUNT(*) FROM optimization_run").fetchone()[0], 14)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM optimization_run").fetchone()[0], 0)
                residual = db.execute("SELECT MAX(ABS(grid_import_kw-(total_load_kw-pv_generation_kw-battery_bus_injection_kw+network_active_losses_kw))) FROM interval_summary").fetchone()[0]
                self.assertLess(residual, 1e-6)
                qresidual = db.execute("""SELECT MAX(ABS(s.grid_reactive_import_kvar-
                    ((SELECT -SUM(value) FROM measurement m JOIN asset a USING(asset_id)
                      WHERE m.interval_start=s.interval_start AND m.measurement_type_id='reactive_power_kvar' AND a.asset_type='load')
                     +s.network_reactive_losses_kvar))) FROM interval_summary s""").fetchone()[0]
                self.assertLess(qresidual, 1e-6)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM branch_measurement bm JOIN branch b USING(branch_id) WHERE bm.current_a>b.ampacity_a").fetchone()[0], 0)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM bus_measurement WHERE voltage_ll_v NOT BETWEEN 360 AND 440").fetchone()[0], 0)
            finally:
                src.close(); db.close()

    def test_api_lists_and_accepts_selected_synthetic_dataset(self):
        with tempfile.TemporaryDirectory() as temp:
            submitted = []
            class NoopExecutor:
                def __init__(self, *args, **kwargs): pass
                def submit(self, *args, **kwargs): submitted.append(args)
            with patch("brain_core.api.ThreadPoolExecutor", NoopExecutor):
                app = create_app(str(SOURCE), main.load_optimizer(), run_dir=temp,
                                 state_db_path=str(Path(temp) / "state.sqlite"))
            routes = {r.path:r.endpoint for r in app.routes if hasattr(r,"path")}
            items = routes["/api/datasets"]()["datasets"]
            self.assertTrue(any(x["id"] == "scenario-default" for x in items))
            result = routes["/api/run"](RunRequest(agent="mock", max_iterations=1,
                                                     dataset_id="scenario-default"))
            self.assertEqual(result["dataset_id"], "scenario-default")
            self.assertEqual(Path(submitted[0][3]).name, "scenario-default.sqlite")


if __name__ == "__main__":
    unittest.main()
