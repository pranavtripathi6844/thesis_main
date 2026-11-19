import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
TEACHER = ROOT.parent / "teacher_implementation"
sys.path.insert(0, str(ROOT))
import grid_search as gs
import table5_calibration as tc


class Table5Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = tc.load_config(ROOT / "table5_config.json")
        cls.model = gs.load_model(TEACHER / "NRHES_Model.py")
        cls.params = cls.model.create_params()
        cls.raw = gs.load_data(TEACHER / "InputData.xlsx")
        _, cls.wind = gs.renewable_components(cls.model, cls.raw, cls.params)

    def settings(self, track="fixed_inputs"):
        branch = next(tc.branches(track))
        names, _ = tc.fields_bounds(branch, self.spec)
        return tc.settings_for(branch, names, [25, 7.5, 0.1] + ([2, 0.15, 0.9] if track == "input_calibrated" else []))

    def test_ten_exact_designs_and_distinct_mode3_targets(self):
        b = self.spec["benchmarks"]
        self.assertEqual([r["id"] for r in b], list(tc.EXPECTED_IDS))
        self.assertEqual([r["capacity"]["C_TES"] for r in b[-4:]], [2000, 987, 583, 250])
        self.assertEqual(b[6]["target"]["EWP"], 0.0018)
        self.assertEqual(b[9]["target"]["EWP"], 0.0062)
        self.assertEqual(b[2]["capacity"]["N_pv"], 61422)
        self.assertEqual((len(list(tc.branches("fixed_inputs"))), len(list(tc.branches("input_calibrated")))), (8, 16))

    def test_bad_targets_ids_bounds_and_budgets_rejected(self):
        for change in ("target", "id", "bounds", "budget", "capacity"):
            s = copy.deepcopy(self.spec)
            if change == "target":
                s["benchmarks"][0]["target"]["EWP"] = 0
            elif change == "id":
                s["benchmarks"][1]["id"] = "1a"
            elif change == "bounds":
                s["bounds"]["real_rate"] = [0.12, 0.02]
            elif change == "capacity":
                s["benchmarks"][0]["capacity"]["C_TES"] = float("nan")
            else:
                s["search"]["fixed_inputs"]["generations"] = 0
            with tempfile.TemporaryDirectory() as tmp:
                p = Path(tmp) / "config.json"
                p.write_text(json.dumps(s))
                with self.assertRaises(ValueError):
                    tc.load_config(p)

    def test_identity_inputs_unchanged_and_weather_shift_is_joint(self):
        original = self.raw.copy(deep=True)
        fixed = tc.input_scenario(self.raw, self.settings())
        pd.testing.assert_frame_equal(fixed, self.raw)
        s = self.settings("input_calibrated")
        s.update(solar_exponent=1, load_offset_mw=0, load_seasonal_gain=1, weather_shift_hours=8)
        changed = tc.input_scenario(self.raw, s)
        for k in ("G_Tot", "T_a", "V_anem"):
            np.testing.assert_array_equal(changed[k], np.roll(self.raw[k], 8))
        np.testing.assert_array_equal(changed.P_grid, self.raw.P_grid)
        pd.testing.assert_frame_equal(self.raw, original)

    def test_irradiance_peak_night_and_load_mean_preserved_as_documented(self):
        s = self.settings("input_calibrated")
        changed = tc.input_scenario(self.raw, s)
        self.assertEqual(changed.G_Tot.max(), self.raw.G_Tot.max())
        np.testing.assert_array_equal(changed.G_Tot[self.raw.G_Tot == 0], 0)
        self.assertLess(changed.G_Tot.sum(), self.raw.G_Tot.sum())
        self.assertAlmostEqual(changed.P_grid.mean(), self.raw.P_grid.mean()+s["load_offset_mw"])
        self.assertGreater(changed.P_grid.min(), 0)
        for bad in (float("nan"), 0.5):
            s["solar_exponent"] = bad
            with self.assertRaises(ValueError):
                tc.input_scenario(self.raw, s)

    def test_fixed_track_cannot_silently_transform_inputs(self):
        s = self.settings()
        s["weather_shift_hours"] = 8
        with self.assertRaises(ValueError):
            tc.evaluate(self.raw, self.params, self.wind, self.spec, s)

    def test_absolute_and_capacity_scaled_power(self):
        s = self.settings()
        self.assertEqual(tc.limits(s, 250), [25, 7.5])
        s["charge_model"] = s["discharge_model"] = "capacity_scaled"
        self.assertEqual(tc.limits(s, 250), [3.125, 0.9375])

    def test_relative_not_percentage_point_tolerance_and_reserved_rows(self):
        b = self.spec["benchmarks"]
        rows = [{"id": r["id"], **{k: v*1.1 for k,v in r["target"].items()}} for r in b]
        result = tc.error_summary(rows, b, self.spec["reserved_rows"])
        self.assertTrue(result["all_within_10_percent"])
        self.assertFalse(result["all_within_5_percent"])
        rows[1]["EWP"] = b[1]["target"]["EWP"]*1.2
        result = tc.error_summary(rows, b, self.spec["reserved_rows"])
        self.assertFalse(result["all_within_10_percent"])
        self.assertTrue(result["fit_within_10_percent"])
        self.assertFalse(result["reserved_within_10_percent"])
        with self.assertRaises(ValueError):
            tc.error_summary(rows[::-1], b, self.spec["reserved_rows"])

    def test_accelerated_curves_and_economics_match_reference_all_ten(self):
        for track in tc.TRACKS:
            s = self.settings(track)
            if track == "input_calibrated":
                s.update(weather_shift_hours=8, charge_model="capacity_scaled", pv_interpretation="nameplate_150W")
            data, fast, curves, _, objective = tc.evaluate(self.raw, self.params, self.wind, self.spec, s)
            pv, wt = tc.components(data, self.params, s["pv_interpretation"], self.wind, s["weather_shift_hours"])
            for b, row, a in zip(self.spec["benchmarks"], fast, curves):
                c = b["capacity"]
                reference = gs.simulate(data, c["N_pv"]*pv+c["N_wt"]*wt, self.params, c, b["mode"],
                                        gs.Physics(*tc.limits(s,c["C_TES"])))
                for j,k in enumerate(tc.PLOT_FIELDS):
                    np.testing.assert_allclose(a[:,j], reference.series[k], rtol=0, atol=2e-11)
                econ = gs.economic_metrics(reference, self.params, gs.Economics(s["real_rate"]), 20)
                self.assertAlmostEqual(row["LCOE"], econ["LCOE"], places=12)
                self.assertLess(reference.audit["max_balance_residual_MWh"], 1e-9)
            changed_spec = copy.deepcopy(self.spec)
            for b in changed_spec["benchmarks"]:
                if b["id"] in changed_spec["reserved_rows"]:
                    b["target"]["EWP"] *= 2
            self.assertEqual(objective, tc.evaluate(self.raw, self.params, self.wind, changed_spec,s)[-1])

    def test_energy_bounds_exclude_ten_percent_fixed_input_mode1(self):
        with tempfile.TemporaryDirectory() as tmp:
            tc.preflight(self.raw, self.params, self.wind, self.spec, Path(tmp))
            bounds = pd.read_csv(Path(tmp)/"fixed_input_energy_bounds.csv")
            self.assertEqual(len(bounds), 6)
            self.assertFalse(bounds["10percent_fit_possible_by_energy_budget"].any())
            self.assertAlmostEqual(bounds.iloc[3]["minimum_possible_EWP"], .08432259644057105)

    def test_one_case_artifacts_dedup_and_frozen_rerun(self):
        before = tc.protected_hashes(TEACHER)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            exp = tc.Experiment(self.raw, self.params, self.wind, self.spec, out)
            s = self.settings("input_calibrated")
            value = exp.evaluate(s,"test")
            self.assertEqual(exp.evaluate(s,"repeat"), value)
            self.assertEqual(len(exp.records), 1)
            folder = out/"cases"/"C00001"
            with Image.open(folder/"candidate.png") as im:
                self.assertEqual(im.size,(2500,1700))
            self.assertEqual(len(pd.read_csv(folder/"metrics.csv")),10)
            # Use an always-design-valid area/efficiency control for reference export.
            s = self.settings()
            chosen = out/"selected"/"fixed_inputs"
            summary = tc.export_selection(self.raw,self.params,self.wind,self.spec,s,chosen,before)
            self.assertFalse(summary["all_within_10_percent"])
            saved = json.loads((chosen/"selection.json").read_text())
            self.assertEqual(saved["status"],"not_within_requested_margin")
            self.assertEqual(len(list(chosen.glob("row_*.npz"))),10)
            self.assertTrue(all(a["valid"] for a in json.loads((chosen/"audits.json").read_text())))
            pd.testing.assert_frame_equal(pd.read_csv(chosen/"scenario_inputs.csv"), self.raw, check_dtype=False)
            with patch.object(gs,"plot_mode"):
                repeated = tc.rerun_selection(chosen/"selection.json",out/"frozen")
            self.assertEqual(summary,repeated)
            with self.assertRaises(FileExistsError):
                tc.rerun_selection(chosen/"selection.json",out/"frozen")
            saved["protected_hashes"]["InputData.xlsx"] = "changed"
            bad = out/"bad.json"
            gs.save_json(bad,saved)
            with self.assertRaises(ValueError):
                tc.rerun_selection(bad,out/"bad-output")
        self.assertEqual(before,tc.protected_hashes(TEACHER))

    def test_small_both_track_search_reports_counts_and_plots(self):
        spec = copy.deepcopy(self.spec)
        for v in spec["search"].values():
            v.update(coarse_power=1,refine_branches=1,generations=1,popsize=1)
        real_branches = tc.branches
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp)/"config.json"
            gs.save_json(config,spec)
            # Keep the integration test small; branch coverage is checked above.
            with patch.object(tc,"branches",side_effect=lambda t: iter([next(real_branches(t))])), patch.object(gs,"plot_mode"):
                selections = tc.run(config,Path(tmp)/"run")
            self.assertEqual(set(selections),set(tc.TRACKS))
            out = Path(tmp)/"run"
            m = json.loads((out/"manifest.json").read_text())
            self.assertEqual(m["dashboard_count"],m["configuration_count"])
            self.assertEqual(m["metric_row_count"],m["configuration_count"]*10)
            self.assertEqual(len(pd.read_csv(out/"track_comparison.csv")),2)
            self.assertTrue(m["originals_unchanged"])
            self.assertIn("not recovered author", (out/"REPORT.md").read_text())


if __name__ == "__main__":
    unittest.main()
