import copy
import json
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
TEACHER = ROOT.parent / "teacher_implementation"
sys.path.insert(0, str(ROOT))
import grid_search as gs


class ModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = gs.load_model(TEACHER / "NRHES_Model.py")
        cls.params = cls.model.create_params()
        cls.capacity = dict(cls.model.CFG1)
        cls.capacity.update(N_pv=40000, C_TES=250.0)

    def data(self, re, load):
        return pd.DataFrame({"hours": np.arange(1, len(re) + 1), "P_grid": load})

    def test_charging_rejection_is_counted(self):
        re = np.array([12.0])
        s = gs.simulate(self.data(re, [10]), re, self.params, self.capacity, 1, gs.Physics())
        self.assertEqual(s.series["charge"][0], 10)
        self.assertEqual(s.series["WEN"][0], 15)
        self.assertTrue(s.audit["valid"])

    def test_discharge_shortage_is_recomputed(self):
        re = np.array([12.0, 12.0, 0.0])
        data = self.data(re, [10, 10, 15])
        s = gs.simulate(data, re, self.params, self.capacity, 1, gs.Physics())
        old = gs.legacy_simulate(self.model, data, re, self.params, self.capacity, 1, 1e-7)
        self.assertAlmostEqual(s.series["DPS"][-1], 1.0)
        self.assertEqual(old.series["DPS"][-1], 0.0)
        self.assertTrue(s.audit["valid"])
        self.assertFalse(old.audit["valid"])

    def test_nonunit_efficiencies_conserve_energy(self):
        params = copy.deepcopy(self.params)
        params["TES"].update(eta_ch=0.9, eta_dis=0.8, sigma=0.001)
        re = np.array([12.0, 12.0, 0.0])
        s = gs.simulate(self.data(re, [10, 10, 15]), re, params, self.capacity, 1, gs.Physics())
        self.assertTrue(s.audit["valid"], s.audit)
        self.assertGreater(s.series["storage_loss"].sum(), 0)

    def test_addition_is_not_silently_accepted(self):
        re = np.array([10.0])
        c = {**self.capacity, "P_SOEC": 9.0, "DF": 0.5}
        data = self.data(re, [10])
        valid = gs.simulate(data, re, self.params, c, 2, gs.Physics(hydrogen_formula="minimum"))
        invalid = gs.simulate(data, re, self.params, c, 2, gs.Physics(hydrogen_formula="addition"))
        self.assertTrue(valid.audit["valid"])
        self.assertFalse(invalid.audit["valid"])
        self.assertGreater(invalid.series["H2"][0], valid.series["H2"][0])
        self.assertGreater(invalid.series["H2_e_deficit"][0], 0)

    def test_electrical_minimum_uses_actual_consumption(self):
        re = np.array([5.0])
        c = {**self.capacity, "P_SOEC": 4.0, "DF": 0.2}
        s = gs.simulate(self.data(re, [12.45]), re, self.params, c, 2, gs.Physics())
        self.assertEqual(s.series["H2"][0], 0)
        self.assertTrue(s.audit["valid"])

    def test_mode3_gate_uses_start_of_hour_state(self):
        re = np.full(12, 10.0)
        c = {**self.capacity, "P_SOEC": 9.0, "DF": 0.3, "TES_factor": 0.8048}
        s = gs.simulate(self.data(re, np.full(12, 10.0)), re, self.params, c, 3,
                        gs.Physics(charge_mwth=25))
        self.assertEqual(s.series["H2"][7], 0)
        self.assertGreater(s.series["H2"][8], 0)
        self.assertTrue(s.audit["valid"])

    def test_shutdown_and_cap_are_distinct(self):
        re = np.array([15.0])
        c = {**self.capacity, "P_SOEC": 5.0, "DF": 0.5}
        data = self.data(re, [10])
        cap = gs.simulate(data, re, self.params, c, 2, gs.Physics())
        stop = gs.simulate(data, re, self.params, c, 2, gs.Physics(overflow="shutdown"))
        self.assertGreater(cap.series["H2"][0], 0)
        self.assertEqual(stop.series["H2"][0], 0)
        self.assertTrue(stop.audit["valid"])

    def test_cashflow_replacements_exclude_endpoint(self):
        self.assertEqual(gs.component_npc(100, 10, 5, 20, 0), 600)
        self.assertEqual(gs.component_npc(100, 10, 5, 25, 0), 750)
        self.assertEqual(gs.crf(0, 20), 0.05)

    def test_tank_is_maximum_daily_mass(self):
        re = np.full(48, 10.0)
        c = {**self.capacity, "P_SOEC": 9.0, "DF": 0.5}
        s = gs.simulate(self.data(re, np.full(48, 10)), re, self.params, c, 2, gs.Physics())
        s.series["H2"] = np.r_[np.ones(24), np.full(24, 3.0)]
        s.metrics["H2_tonnes"] = 0.096
        original_lf = s.series["LF"].copy()
        result = gs.economic_metrics(s, self.params, gs.Economics())
        self.assertEqual(result["tank_kg"], 72)
        np.testing.assert_array_equal(s.series["LF"], original_lf)

    def test_exact_target_has_zero_score(self):
        target = {"DPSP": 0.009, "EWP": 0.0062, "LCOE": 0.0931}
        settings = json.loads((ROOT / "search_config.json").read_text())
        row = gs.target_errors(target, target, settings["tolerances"])
        self.assertEqual(gs.score_rows([row]), (0.0, True))

    def test_energy_lower_bound(self):
        re = np.full(24, 5.0)
        bound = gs.energy_lower_bound(self.data(re, np.full(24, 10)), re, self.params, self.capacity)
        self.assertAlmostEqual(bound["minimum_possible_waste_MWh_e_equivalent"], 25.0)
        self.assertAlmostEqual(bound["minimum_possible_EWP"], 25 / 360)

    def test_genome_bounds_and_dynamic_soec(self):
        components = (np.array([0.00015, 0.0001]), np.array([0.0, 1.0]))
        c = gs.decode_genome([60423.4, 250, 0.5, 0.3, 0.8], 3, components)
        self.assertEqual(c["N_pv"], 60423)
        self.assertAlmostEqual(c["P_SOEC"], gs.renewable(c, components).max() * 0.5)
        self.assertTrue(gs.design_valid(c, 3, gs.renewable(c, components).max()))

    def test_invalid_assumptions_rejected(self):
        with self.assertRaises(ValueError):
            gs.Physics(hydrogen_formula="guess")
        with self.assertRaises(ValueError):
            gs.Physics(charge_mwth=-1)
        with self.assertRaises(ValueError):
            gs.Physics(discharge_mwth=float("nan"))
        with self.assertRaises(ValueError):
            gs.Economics(real_rate=float("nan"))


class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = gs.load_model(TEACHER / "NRHES_Model.py")
        cls.params = cls.model.create_params()
        cls.data = gs.load_data(TEACHER / "InputData.xlsx")
        cls.components = gs.renewable_components(cls.model, cls.data, cls.params)
        cls.settings = json.loads((ROOT / "search_config.json").read_text())

    def test_original_matches_previously_saved_run(self):
        previous = pd.read_csv(TEACHER / "early_runs" / "python_run_20261025" / "NRHES_Economic_Results.csv") if (TEACHER / "early_runs" / "python_run_20261025" / "NRHES_Economic_Results.csv").exists() else None
        expected = [
            (0.00557652461970455, 0.04631463647210821, 0.06868801518673093),
            (0.003969458276905974, 0.04663451092698604, 0.08328545166453068),
            (0.01118994442910629, 0.024618425132781098, 0.06526651657089234),
        ]
        for item, target in zip(self.settings["training"], expected):
            sim = gs.legacy_simulate(self.model, self.data, gs.renewable(item["config"], self.components),
                                     self.params, item["config"], item["mode"], 1e-7)
            np.testing.assert_allclose([sim.metrics[k] for k in gs.METRICS], target, atol=1e-12, rtol=1e-10)
            if previous is not None:
                self.assertAlmostEqual(sim.metrics["LCOE"], previous.iloc[item["mode"] - 1].LCOE_USD_per_kWh, places=10)

    def test_small_search_end_to_end_and_source_unchanged(self):
        before = gs.sha256(TEACHER / "NRHES_Model.py")
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            config = copy.deepcopy(self.settings)
            config["physical_grid"] = {
                "charge_mwth": [10], "discharge_mwth": [10],
                "hydrogen_formula": ["minimum", "addition"],
                "thermal_gate": ["resource"], "overflow": ["cap"],
            }
            config["economic_grid"] = {"real_rate": [0.0392156862745098], "variable_cost_basis": ["thermal"], "decommissioning": ["project"]}
            path = folder / "config.json"
            path.write_text(json.dumps(config))
            args = gs.parse_args(["--config", str(path), "--output", str(folder / "run"), "--no-plots"])
            result = gs.run(args)
            self.assertEqual(result["combined_cases"], 2)
            self.assertEqual(result["valid_combined_cases"], 1)
            self.assertEqual(result["best_valid_candidate"]["hydrogen_formula"], "minimum")
            self.assertTrue((args.output / "REPORT.md").exists())
            self.assertTrue((args.output / "index.html").exists())
            self.assertEqual(result["search_script_sha256"], gs.sha256(ROOT / "grid_search.py"))
            self.assertEqual(len(pd.read_csv(args.output / "case_manifest.csv")), 2)
            with self.assertRaises(FileExistsError):
                gs.run(args)
        self.assertEqual(before, gs.sha256(TEACHER / "NRHES_Model.py"))

    def test_nsga2_smoke(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = Namespace(ga_population=4, ga_generations=2, ga_seeds=[123], no_plots=True)
            result = gs.optimize(
                self.data, self.components, self.params, gs.Physics(25, 25), gs.Economics(),
                self.settings, args, [], Path(tmp),
            )
            self.assertGreater(result["evaluations"], 0)
            front = pd.read_csv(Path(tmp) / "optimization" / "pareto_solutions.csv")
            self.assertTrue(front.valid.all())
            self.assertTrue(front.N_pv.between(40000, 80000).all())
            self.assertTrue((front.N_pv % 1 == 0).all())


if __name__ == "__main__":
    unittest.main()
