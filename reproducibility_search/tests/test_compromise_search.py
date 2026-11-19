import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
TEACHER = ROOT.parent / "teacher_implementation"
sys.path.insert(0, str(ROOT))
import compromise_search as cs
import compromise_model as cm
import grid_search as gs


class SpecificationTests(unittest.TestCase):
    def setUp(self):
        self.spec = json.loads((ROOT / "compromise_config.json").read_text())
        self.spec["base_config"] = str(ROOT / "search_config.json")

    def read_changed(self, spec):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "spec.json"
            path.write_text(json.dumps(spec))
            return cs.load_spec(path)

    def test_default_grid_has_3000_cases_and_only_allowed_hypotheses(self):
        spec, base, physical, economic = cs.load_spec(ROOT / "compromise_config.json")
        self.assertEqual((len(physical), len(economic)), (50, 60))
        self.assertTrue(all(p.hydrogen_formula == "minimum" and p.overflow == "cap" for p in physical))
        self.assertEqual(base["training"][0]["config"]["N_pv"], 60423)
        self.assertTrue((cs.PHYSICAL_KEYS | cs.ECONOMIC_KEYS).issubset(spec["provenance"]))

    def test_published_parameter_override_is_rejected(self):
        for key in ("PV_efficiency", "sigma", "C_TES", "TES_factor"):
            spec = copy.deepcopy(self.spec)
            spec["physical_grid"][key] = [0.1]
            with self.assertRaises(ValueError):
                self.read_changed(spec)

    def test_bad_financial_horizon_and_duplicates_rejected(self):
        for years in (0, 61, 20.5, True):
            spec = copy.deepcopy(self.spec)
            spec["economic_grid"]["project_years"] = [years]
            with self.assertRaises(ValueError):
                self.read_changed(spec)
        self.spec["physical_grid"]["charge_mwth"] = [10, 10]
        with self.assertRaises(ValueError):
            self.read_changed(self.spec)

    def test_nan_selection_scale_rejected(self):
        self.spec["selection"]["numerical_reference_relative_error"] = float("nan")
        with self.assertRaises(ValueError):
            self.read_changed(self.spec)

    def test_reporting_bands_and_absolute_relative_distinction(self):
        training = [{"target": {"DPSP": 0.01, "EWP": 0.04, "LCOE": 0.1}}]
        row = {"DPSP": 0.012, "EWP": 0.04, "LCOE": 0.1}
        result = cs.numerical_errors([row], training, [10, 20, 30])
        self.assertFalse(result["within_10_percent"])
        self.assertTrue(result["within_20_percent"])
        self.assertAlmostEqual(result["minimum_all_metric_band_percent"], 20)
        self.assertAlmostEqual(result["numerical_relative_rms"], 0.2 / np.sqrt(3))
        with self.assertRaises(ValueError):
            cs.numerical_errors([], training, [10])

    def test_explicit_score_weight_sensitivity(self):
        a = {"numerical_relative_rms": 0.25, "shape_feature_score": 3}
        b = {"numerical_relative_rms": 0.5, "shape_feature_score": 0}
        self.assertLess(cs.score_case(a, self.spec, 1), cs.score_case(b, self.spec, 1))
        self.assertGreater(cs.score_case(a, self.spec, 0), cs.score_case(b, self.spec, 0))
        self.assertEqual(cs.score_case(a, self.spec), 2)

    def test_versioned_selection_retains_fixed_published_parameters(self):
        selection = json.loads((ROOT / "compromise_selection.json").read_text())
        model = gs.load_model(TEACHER / "NRHES_Model.py")
        self.assertEqual(selection["fixed_params"], model.create_params())
        self.assertEqual(selection["model_sha256"], gs.sha256(TEACHER / "NRHES_Model.py"))
        self.assertEqual(selection["input_sha256"], gs.sha256(TEACHER / "InputData.xlsx"))
        self.assertEqual(selection["status"], "provisional_compromise_not_professor_approved")
        self.assertEqual(selection["physics"]["hydrogen_formula"], "minimum")
        for item in selection["training"]:
            self.assertEqual(item["config"], getattr(model, f"CFG{item['mode']}"))


class EndToEndTests(unittest.TestCase):
    def test_search_exports_each_case_and_frozen_model_reproduces_selection(self):
        hashes = {p: gs.sha256(TEACHER / p) for p in ("NRHES_Model.py", "NRHES_Model.m", "InputData.xlsx")}
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            spec = json.loads((ROOT / "compromise_config.json").read_text())
            spec["base_config"] = str(ROOT / "search_config.json")
            spec["physical_grid"] = {"charge_mwth": [10, 25], "discharge_mwth": [7.5], "thermal_gate": ["resource"]}
            spec["economic_grid"] = {"real_rate": [0.04, 0.10], "project_years": [20], "variable_cost_basis": ["thermal"], "decommissioning": ["retirement"]}
            config = temp / "spec.json"
            config.write_text(json.dumps(spec))
            args = cs.parse_args(["--config", str(config), "--output", str(temp / "run")])
            summary = cs.run(args)
            out = args.output
            self.assertEqual(summary["combined_cases"], 4)
            self.assertEqual(summary["dedicated_dashboard_count"], 4)
            self.assertEqual(summary["valid_cases"], 4)
            self.assertTrue(summary["protected_sources_unchanged"])
            self.assertEqual(summary["minimum_achievable_all_metric_band_percent"] > 100, True)
            cases = sorted((out / "cases").iterdir())
            self.assertEqual(len(cases), 4)
            for folder in cases:
                for name in ("comparison.png", "index.html", "parameters.json", "metrics.csv"):
                    self.assertTrue((folder / name).is_file())
                page = (folder / "index.html").read_text()
                self.assertIn("Paper_Fig10_Mode3.png", page)
                with Image.open(folder / "comparison.png") as im:
                    im.verify()
            # Same physics and different finances: curves match but dedicated images differ.
            self.assertNotEqual(gs.sha256(cases[0] / "comparison.png"), gs.sha256(cases[1] / "comparison.png"))
            r1, r2 = [pd.read_csv(c / "metrics.csv") for c in cases[:2]]
            np.testing.assert_allclose(r1[["DPSP", "EWP", "H2_tonnes"]], r2[["DPSP", "EWP", "H2_tonnes"]])
            self.assertFalse(np.allclose(r1.LCOE, r2.LCOE))
            chosen = summary["selected_case"]["case_id"]
            expected = pd.read_csv(out / "cases" / chosen / "metrics.csv")
            actual = cm.run_selected(out / "selected_compromise.json", TEACHER / "NRHES_Model.py",
                                     TEACHER / "InputData.xlsx", temp / "frozen", plots=False)
            np.testing.assert_allclose(pd.DataFrame(actual)[["DPSP", "EWP", "LCOE", "H2_tonnes"]],
                                       expected[["DPSP", "EWP", "LCOE", "H2_tonnes"]], rtol=1e-12, atol=1e-12)
            with self.assertRaises(FileExistsError):
                cs.run(args)
            with self.assertRaises(FileExistsError):
                cm.run_selected(out / "selected_compromise.json", TEACHER / "NRHES_Model.py",
                                TEACHER / "InputData.xlsx", temp / "frozen", plots=False)
            changed = json.loads((out / "selected_compromise.json").read_text())
            changed["input_sha256"] = "wrong"
            (temp / "bad_selection.json").write_text(json.dumps(changed))
            with self.assertRaises(ValueError):
                cm.run_selected(temp / "bad_selection.json", TEACHER / "NRHES_Model.py",
                                TEACHER / "InputData.xlsx", temp / "bad_frozen", plots=False)
        for p, expected in hashes.items():
            self.assertEqual(gs.sha256(TEACHER / p), expected)


if __name__ == "__main__":
    unittest.main()
