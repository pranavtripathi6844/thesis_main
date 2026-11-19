import copy
import itertools
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
from PIL import Image

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import paper_reproduction as pr
import teacher_benchmarks_search as tb


class TeacherBenchmarkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = json.loads((HERE / "teacher_benchmarks_config.json").read_text())
        cls.spec = tb.load_spec(HERE / "teacher_benchmarks_config.json")
        cls.inputs = pr.load_inputs(pr.ROOT / "InputData.xlsx")

    def test_exact_three_designs_nine_targets_and_grid(self):
        cases = self.spec["benchmarks"]
        self.assertEqual([c["id"] for c in cases], ["1a", "2a", "3d"])
        self.assertEqual([c["mode"] for c in cases], [1, 2, 3])
        self.assertEqual([(c["pv"], c["tes"]) for c in cases],
                         [(60423, 2000), (80000, 2000), (75054, 250)])
        self.assertEqual(sum(len(c["target"]) for c in cases), 9)
        self.assertEqual(len(list(itertools.product(*self.spec["physical_grid"].values()))), 1600)
        self.assertEqual(len(list(itertools.product(*self.spec["economic_grid"].values()))), 240)

    def test_reject_extra_designs_input_fitting_and_duplicate_axes(self):
        bad = []
        config = copy.deepcopy(self.config)
        config["benchmark_ids"] = ["1a", "2a", "3a"]
        bad.append(config)
        for key in ("load_scale", "solar_shift", "eta_PB", "N_pv", "hydrogen_formula"):
            config = copy.deepcopy(self.config)
            config["physical_grid"][key] = [1]
            bad.append(config)
        config = copy.deepcopy(self.config)
        config["economic_grid"]["years"] = [20, 20]
        bad.append(config)
        for config in bad:
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "config.json"
                pr.save_json(path, config)
                with self.assertRaises(ValueError):
                    tb.load_spec(path)

    def test_only_nine_entries_control_scoring(self):
        cases = self.spec["benchmarks"]
        rows = [{"id": c["id"], **c["target"]} for c in cases]
        self.assertEqual(pr.errors(rows, cases)["metrics_within_10_percent"], 9)
        rows[-1]["EWP"] *= 1.11
        result = pr.errors(rows, cases)
        self.assertFalse(result["all_within_10_percent"])
        self.assertEqual(result["metrics_within_10_percent"], 8)
        self.assertEqual(result["rows_within_10_percent"], 2)
        with self.assertRaises(ValueError):
            tb.assert_scope(rows[::-1], cases)

    def test_plot_orders_are_the_teacher_orders(self):
        self.assertEqual(tb.PLOT_KEYS, (("LF", "DPS", "WEN"),
                                       ("H2", "LF", "DPS", "WEN", "WER"),
                                       ("H2", "LF", "DPS", "WEN", "WER")))
        rows, curves, audits = pr.evaluate(self.inputs, self.spec["benchmarks"],
                                          pr.Physics(), pr.Finance())
        self.assertTrue(all(a["valid"] for a in audits))
        native = tb.native_results(rows, curves)
        for source, converted in zip(curves, native):
            for key in ("LF", "DPS", "WEN", "WER"):
                np.testing.assert_array_equal(source[key], converted[key])
            np.testing.assert_array_equal(source["H2"], converted["H2_kg_hour"])
        before = self.inputs.fingerprint()
        image = tb.raster(curves)
        self.assertEqual(image.size, tb.SIZE)
        image.close()
        curves[0] = {**curves[0], "WEN": np.full(8760, 51.)}
        with self.assertRaisesRegex(ValueError, "clip WEN"):
            tb.raster(curves)
        self.assertEqual(self.inputs.fingerprint(), before)

    def test_original_plotter_receives_candidate_arrays_unchanged(self):
        seen = {}
        def plot(res, cfgs, params, hours, solar, temperature, wind, load):
            seen["res"], seen["cfgs"], seen["hours"], seen["load"] = res, cfgs, hours, load
        stub = {"plot_figures": plot, "save_results": lambda *args: None, "create_params": lambda: {}}
        with tempfile.TemporaryDirectory() as tmp, patch.object(tb.runpy, "run_path", return_value=stub):
            rows, _ = tb.export_bundle(self.inputs, self.spec["benchmarks"], pr.Physics(),
                                       pr.Finance(), Path(tmp) / "selected")
            for c, r, nr in zip(self.spec["benchmarks"], rows, seen["res"]):
                self.assertEqual(r["LCOE"], nr["LCOE"])
                with np.load(Path(tmp) / "selected" / f"row_{c['id']}.npz") as arrays:
                    np.testing.assert_array_equal(nr["H2_kg_hour"], arrays["H2"])
                    np.testing.assert_array_equal(nr["LF"], arrays["LF"])
            np.testing.assert_array_equal(seen["load"], self.inputs.load)
            self.assertEqual(seen["cfgs"][2]["TES_factor"], .8048)

    def test_mode1_bound_blocks_all_nine_acceptance_with_unchanged_inputs(self):
        bounds = tb.energy_bounds(self.inputs, self.spec["benchmarks"])
        self.assertEqual(len(bounds), 4)
        self.assertFalse(bounds.within_10_percent_possible.any())
        lower = bounds[(bounds.pv_model == "nameplate_150W") & (bounds.reactor_MWth == 25)].iloc[0]
        self.assertAlmostEqual(lower.minimum_EWP, .0843226, places=7)
        self.assertEqual(lower.paper_EWP, .0402)

    def test_small_real_search_teacher_native_plots_frozen_and_validation(self):
        config = copy.deepcopy(self.config)
        config["physical_grid"] = {k: [v[0]] for k, v in config["physical_grid"].items()}
        config["physical_grid"]["tes_charge_mwth"] = [5, 25]
        config["economic_grid"] = {k: [v[0]] for k, v in config["economic_grid"].items()}
        config["economic_grid"]["real_rate"] = [pr.Finance().real_rate, .1]
        config["financial_shortlist_dispatches"] = 1
        original_hashes, code = pr.original_hashes(), tb.source_hashes()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            pr.save_json(base / "config.json", config)
            manifest = tb.run(base / "config.json", base / "run")
            self.assertEqual(manifest["tested_configurations"], 3)
            self.assertEqual(manifest["benchmark_entries"], 9)
            self.assertEqual(manifest["all_result_rows"], 9)
            native = tb.csv(base / "run/teacher/NRHES_Economic_Results.csv")
            expected = np.array([[.00557652462, .04631463647, .06868801519],
                                 [.00396945828, .04663451093, .08328545166],
                                 [.01118994443, .02461842513, .06526651657]])
            np.testing.assert_allclose(native[["DPSP", "EWP", "LCOE_USD_per_kWh"]], expected, atol=1e-10, rtol=0)
            self.assertEqual(len(list((base / "run/cases").glob("*/candidate.png"))), 3)
            self.assertEqual(set(tb.csv(base / "run/all_results.csv").id), set(tb.IDS))
            tb.rerun(base / "run/selection.json", base / "frozen")
            report = tb.validate(base / "run", base / "frozen")
            self.assertEqual(report["status"], "passed")
            self.assertTrue(report["frozen_exact"])
            self.assertEqual(report["physical_audit_rows"], 6)
            self.assertEqual(report["unique_candidate_plots"], 3)
            with self.assertRaises(FileExistsError):
                tb.run(base / "config.json", base / "run")
            with self.assertRaises(FileExistsError):
                tb.rerun(base / "run/selection.json", base / "frozen")
            selection = json.loads((base / "run/selection.json").read_text())
            selection["source_hashes"]["runner"] = "changed"
            pr.save_json(base / "bad_selection.json", selection)
            with self.assertRaises(ValueError):
                tb.rerun(base / "bad_selection.json", base / "rejected")
            self.assertFalse((base / "rejected").exists())
        self.assertEqual(pr.original_hashes(), original_hashes)
        self.assertEqual(tb.source_hashes(), code)


if __name__ == "__main__":
    unittest.main()
