import contextlib
import copy
import io
import json
import os
import runpy
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import formula_comparison as fc
import formula_engines as fe
import paper_reproduction as pr
import teacher_benchmarks_search as tb


class FormulaComparisonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = tb.load_spec(HERE / "teacher_benchmarks_config.json")
        cls.cases = cls.spec["benchmarks"]
        cls.inputs = pr.load_inputs(pr.ROOT / "InputData.xlsx")

    def test_every_patch_matches_the_original_teacher_text_once(self):
        source = fe.TEACHER_FILE.read_text()
        patched = fe.patched_teacher_source(source)
        for purpose, old, new, count in fe.PATCHES:
            self.assertEqual(source.count(old), count, purpose)
        self.assertNotEqual(source, patched)
        with self.assertRaises(RuntimeError):
            fe.patched_teacher_source(source.replace("min(H2_from_e, H2_from_th)", "H2_from_e"))

    def test_professor_settings_reproduce_original_run_bit_for_bit(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.symlink(pr.ROOT / "InputData.xlsx", Path(tmp) / "InputData.xlsx")
            with tb.working_directory(tmp), contextlib.redirect_stdout(io.StringIO()):
                ns = runpy.run_path(str(fe.TEACHER_FILE), run_name="teacher_check")
                res = ns["run_simulation"]()[0]
        for case, ref in zip(self.cases, res):
            for formula in fe.FORMULAS:
                r = fe.teacher_simulate(self.inputs, case, fe.TEACHER_PHYSICS, formula)
                lcoe = fe.teacher_lcoe(r, case, fe.TEACHER_PHYSICS, formula, fe.TEACHER_FINANCE)
                same = (all(np.array_equal(r[k], ref[k]) for k in ("DPS", "WER", "WEN", "H2_kg_hour", "LF"))
                        and (r["DPSP"], r["EWP"], lcoe) == (ref["DPSP"], ref["EWP"], ref["LCOE"]))
                # The formula cannot matter in Mode 1, which has no SOEC.
                self.assertEqual(same, formula == "minimum" or case["mode"] == 1, (case["id"], formula))

    def test_teacher_addition_is_sum_and_consumes_injected_power(self):
        t = fe.teacher()
        p = fe.teacher_params(fe.TEACHER_PHYSICS, "addition")
        p_min = fe.teacher_params(fe.TEACHER_PHYSICS, "minimum")
        # RE surplus 6 MW over grid1 = 4 MW; nuclear surplus 4 MWe -> 10 MWth.
        args = (10.0, 10.0, 10.0, 1000.0, 2000.0, 9.0, 20.0, .4)
        dps, wer, wen, h2, lf, q = t["Mode2"](*args, p)
        pe, pth = 6.0, min(10.0, 9.0 / .4)
        self.assertAlmostEqual(h2, p["HTSE"]["K_e"] * pe + p["HTSE"]["K_th"] * pth)
        self.assertEqual(wer, 0.0)
        h2_min = t["Mode2"](*args, p_min)[3]
        self.assertAlmostEqual(h2_min, min(p["HTSE"]["K_e"] * pe, p["HTSE"]["K_th"] * pth))

    def test_teacher_finance_switches(self):
        r = fe.teacher_simulate(self.inputs, self.cases[1], fe.TEACHER_PHYSICS, "minimum")
        served = fe.teacher_lcoe(r, self.cases[1], fe.TEACHER_PHYSICS, "minimum", pr.Finance())
        requested = fe.teacher_lcoe(r, self.cases[1], fe.TEACHER_PHYSICS, "minimum", pr.Finance(load_basis="requested"))
        self.assertAlmostEqual(requested / served, (r["total_grid"] - r["total_DPS"]) / r["total_grid"], places=12)
        p = fe.teacher_params(fe.TEACHER_PHYSICS, "minimum", pr.Finance(real_rate=.08, years=40))
        self.assertEqual(fe.teacher()["calculate_real_interest_rate"](p["econ"]["nominal_discount_rate"],
                                                                     p["econ"]["inflation_rate"]), .08)
        self.assertEqual(p["econ"]["project_lifetime"], 40)
        bop = fe.teacher_lcoe(r, self.cases[1], fe.TEACHER_PHYSICS, "minimum", pr.Finance(balance_of_plant="separate_rated_proxy"))
        self.assertGreater(bop, served)

    def test_teacher_tes_pv_and_reactor_switches(self):
        physics = pr.Physics(tes_charge_mwth=5, tes_discharge_mwth=25, tes_rating="both_scaled",
                             pv_model="nameplate_150W", reactor_2025_mwth=50)
        p = fe.teacher_params(physics, "minimum", case=self.cases[2])
        self.assertEqual((p["TES"]["P_max_ch"], p["TES"]["P_max_dis"]), (5 * 250 / 2000, 25 * 250 / 2000))
        self.assertEqual((p["smTMSR"]["P_th"], p["smTMSR"]["P_e"]), (50.0, 20.0))
        re150 = fe.teacher_renewables(self.inputs, self.cases[0], physics)
        panel, turbine = pr.renewables(self.inputs, "nameplate_150W")
        np.testing.assert_allclose(re150, self.cases[0]["pv"] * panel + turbine, rtol=1e-12, atol=1e-9)

    def test_our_minimum_kernel_is_reference_and_addition_conserves(self):
        for physics in (fe.TEACHER_PHYSICS, pr.Physics(inactive_soec="renewables_first", soc_switch="post_guard",
                                                       tes_rating="both_scaled", pv_model="nameplate_150W")):
            for case in self.cases:
                m, _, a = fe.our_simulate(self.inputs, case, physics, "minimum", check_reference=True)
                ma, sa, aa = fe.our_simulate(self.inputs, case, physics, "addition")
                self.assertTrue(a["valid"] and aa["valid"])
                self.assertGreaterEqual(ma["H2_kg"], m["H2_kg"])
                np.testing.assert_array_less(sa["H2_e"] - 1e-9, case["soec"] + 1e-9)
                if case["mode"] > 1:
                    self.assertGreater(ma["H2_kg"], m["H2_kg"])

    def test_small_end_to_end_run_and_validation(self):
        config = json.loads((HERE / "teacher_benchmarks_config.json").read_text())
        config["physical_grid"] = {k: [v[0]] for k, v in config["physical_grid"].items()}
        config["physical_grid"]["tes_discharge_mwth"] = [10, 25]
        config["economic_grid"] = {k: [v[0]] for k, v in config["economic_grid"].items()}
        config["economic_grid"]["years"] = [20, 40]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            pr.save_json(path, config)
            with contextlib.redirect_stdout(io.StringIO()):
                manifest = fc.run(Path(tmp) / "run", path)
            self.assertEqual(manifest["configurations_per_engine_formula"], 4)
            report = fc.validate(Path(tmp) / "run")
            self.assertEqual(report["status"], "passed")
            self.assertEqual(report["table_rows"], 18)
            table = fc.csv(Path(tmp) / "run/comparison_table.csv")
            self.assertEqual(set(table.formula), {"Minimum", "Addition"})
            with self.assertRaises(FileExistsError):
                fc.run(Path(tmp) / "run", path)


if __name__ == "__main__":
    unittest.main()
