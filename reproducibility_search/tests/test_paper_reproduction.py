import copy
import itertools
import json
import sys
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
TEACHER = ROOT.parent / "teacher_implementation"
sys.path.insert(0,str(ROOT))
import paper_reproduction as pr
import verify_paper_reproduction as pv


def short_inputs(load):
    n=len(load)
    arrays=[np.arange(1,n+1,dtype=float),np.zeros(n),np.zeros(n),np.zeros(n),np.array(load,dtype=float)]
    for a in arrays:a.flags.writeable=False
    return pr.Inputs(*arrays)


class IndependentPaperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec=pr.load_config(ROOT/"paper_reproduction_config.json")
        cls.raw=pr.load_inputs(TEACHER/"InputData.xlsx")

    def source_case(self,index=0,year=2025):
        return copy.deepcopy(self.spec[f"benchmarks_{year}"][index])

    def supplied_re(self,load,re,case,physics=None):
        data=short_inputs(load)
        components=(np.array(re,dtype=float)/case["pv"],np.zeros(len(re)))
        return pr.simulate(data,case,physics or pr.Physics(),components)

    def test_all_targets_and_grid_sizes(self):
        self.assertEqual(len(self.spec["benchmarks_2025"]),10)
        self.assertEqual(len(self.spec["benchmarks_2024"]),10)
        p=self.spec["physical_grid"];e=self.spec["economic_grid"]
        self.assertEqual(len(list(itertools.product(*p.values()))),1600)
        self.assertEqual(len(list(itertools.product(*e.values()))),240)
        self.assertEqual(self.source_case(6)["target"]["EWP"],.0018)
        self.assertEqual(self.source_case(0,2024)["pv"],131144)
        self.assertEqual(self.source_case(9,2024)["battery"],47.95)
        self.assertEqual(self.spec["algorithm_table5_2024"][0]["HV_mean"],.7859)

    def test_config_rejects_weather_adjustment_and_published_efficiency_fitting(self):
        for key in ("load_scale","solar_exponent","weather_shift","eta_PB"):
            spec=copy.deepcopy(self.spec)
            spec["physical_grid"][key]=[1]
            with tempfile.TemporaryDirectory() as tmp:
                p=Path(tmp)/"config.json";pr.save_json(p,spec)
                with self.assertRaises(ValueError):pr.load_config(p)

    def test_original_input_values_are_read_only_and_identical(self):
        original=pd.read_excel(TEACHER/"InputData.xlsx")
        for name,key in zip(("hours","solar","temperature","wind","load"),("hours","G_Tot","T_a","V_anem","P_grid")):
            arr=getattr(self.raw,name)
            np.testing.assert_array_equal(arr,original[key].to_numpy(dtype=float))
            with self.assertRaises(ValueError):arr[0]=0
        fingerprint=self.raw.fingerprint()
        pr.evaluate(self.raw,self.spec["benchmarks_2025"],pr.Physics(),pr.Finance())
        self.assertEqual(fingerprint,self.raw.fingerprint())

    def test_stc_power_for_both_conflicting_PV_conventions(self):
        data=pr.Inputs(np.array([1.]),np.array([1000.]),np.array([25-(47.5-20)/800*1000]),
                       np.array([0.]),np.array([1.]))
        a,_=pr.renewables(data,"area_efficiency")
        b,_=pr.renewables(data,"nameplate_150W")
        self.assertAlmostEqual(a[0],.137*1.658*.834*1000/1e6)
        self.assertAlmostEqual(b[0],.000150)

    def test_wind_curve_including_cutout_boundary(self):
        factor=np.log(70/1.5)/np.log(10/1.5)
        hub=np.array([0.,2.9,3.,8.,13.,21.9,22.,23.])
        data=pr.Inputs(np.arange(len(hub)),np.zeros(len(hub)),np.zeros(len(hub)),hub/factor,np.ones(len(hub)))
        _,wind=pr.renewables(data,"area_efficiency")
        expected=np.array([0,0,0,1.5*(8**3-3**3)/(13**3-3**3),1.5,1.5,0,0])
        np.testing.assert_allclose(wind,expected,rtol=0,atol=1e-12)

    def test_charge_rejection_and_power_limited_shortage_are_counted(self):
        c=self.source_case();c["tes"]=250
        m,s,a=self.supplied_re([5,15],[0,0],c,pr.Physics(tes_charge_mwth=10,tes_discharge_mwth=10))
        self.assertAlmostEqual(s["WEN"][0],2.5)
        self.assertAlmostEqual(s["DPS"][1],1)
        self.assertTrue(a["valid"])
        self.assertLess(a["max_balance_residual_MWh"],1e-10)

    def test_ineligible_SOEC_fallback_preserves_energy(self):
        c=self.source_case(3);c.update(df=.85,soec=9,tes=250)
        _,keep,ak=self.supplied_re([10],[9],c)
        _,fallback,af=self.supplied_re([10],[9],c,pr.Physics(inactive_soec="renewables_first"))
        self.assertEqual(keep["H2"][0],0)
        self.assertAlmostEqual(keep["WER"][0],.5)
        self.assertAlmostEqual(fallback["WER"][0],0)
        self.assertGreater(fallback["Q"][0],keep["Q"][0])
        self.assertTrue(ak["valid"] and af["valid"])

    def test_H2_uses_both_resources_and_honours_minimum_power(self):
        c=self.source_case(3);c.update(df=.5,soec=9,tes=250)
        _,s,a=self.supplied_re([10],[12],c)
        self.assertGreater(s["H2"][0],0)
        self.assertAlmostEqual(s["H2_e"][0],s["H2"][0]/pr.KE)
        self.assertAlmostEqual(s["H2_th"][0],s["H2"][0]/pr.KTH)
        self.assertGreaterEqual(s["H2_e"][0],.15*c["soec"])
        self.assertLessEqual(s["H2_e"][0],c["soec"])
        self.assertTrue(a["valid"])

    def test_literal_additive_equation_cannot_pass_resource_audit(self):
        c=self.source_case(3);c.update(df=.5,soec=9,tes=250)
        _,s,a=self.supplied_re([10],[12],c,pr.Physics(hydrogen_formula="additive_diagnostic"))
        self.assertFalse(a["valid"])
        self.assertGreater(a["max_H2_resource_violation"],0)
        self.assertLess(a["max_balance_residual_MWh"],1e-10)

    def test_battery_and_TES_discharge_priorities_are_distinct_and_conservative(self):
        c1=self.source_case(0,2024);c1.update(tes=250,battery=10,mode=1)
        c2={**c1,"mode":2}
        _,s1,a1=self.supplied_re([10,21,21],[10,0,0],c1)
        _,s2,a2=self.supplied_re([10,21,21],[10,0,0],c2)
        self.assertTrue(a1["valid"] and a2["valid"])
        self.assertLess(s1["Q"][-1],s2["Q"][-1])
        self.assertGreater(s1["B"][-1],s2["B"][-1])
        self.assertEqual(s1["DPS"].sum(),0)
        self.assertEqual(s2["DPS"].sum(),0)

    def test_battery_paper_reactor_overrides_2025_convention(self):
        case=self.source_case(0,2024)
        a,_,_=pr.simulate(self.raw,case,pr.Physics(reactor_2025_mwth=25))
        b,_,_=pr.simulate(self.raw,case,pr.Physics(reactor_2025_mwth=50))
        self.assertEqual(a,b)
        self.assertEqual(a["DPSP"],0)
        for case in self.spec["benchmarks_2024"]:
            m,_,audit=pr.simulate(self.raw,case,pr.Physics())
            self.assertEqual(m["DPSP"],0)
            self.assertTrue(audit["valid"])

    def test_battery_absent_has_no_fixed_power_capital_cost(self):
        c=self.source_case(2,2024)
        m,_,_=pr.simulate(self.raw,c,pr.Physics())
        costs=pr.economics(m,c,pr.Physics(),pr.Finance())
        self.assertEqual(costs["NPC_BS"],0)

    def test_replacements_exclude_project_endpoint_and_zero_rate_works(self):
        f=pr.Finance(real_rate=0,years=20)
        self.assertEqual(pr.crf(0,20),.05)
        self.assertEqual(pr.npc(100,0,5,f),400)
        self.assertEqual(pr.npc(100,0,20,f),100)

    def test_finance_never_changes_dispatch_and_auxiliary_cost_is_explicit(self):
        c=self.source_case(3)
        m,s,a=pr.simulate(self.raw,c,pr.Physics())
        before={k:v.copy() for k,v in s.items()}
        a=pr.economics(m,c,pr.Physics(),pr.Finance())
        b=pr.economics(m,c,pr.Physics(),pr.Finance(balance_of_plant="separate_rated_proxy"))
        self.assertGreater(b["NPC_balance_of_plant"],0)
        self.assertGreater(b["LCOE"],a["LCOE"])
        for k,v in s.items():np.testing.assert_array_equal(v,before[k])

    def test_compiled_and_Python_reference_dispatch_match(self):
        c=self.source_case(3)
        data=short_inputs([5,15,12,10])
        components=(np.array([0.,0.,15.,4.])/c["pv"],np.zeros(4))
        a,sa,_=pr.simulate(data,c,pr.Physics(),components)
        b,sb,_=pr.simulate(data,c,pr.Physics(),components,python_reference=True)
        self.assertEqual(a,b)
        for k in sa:np.testing.assert_allclose(sa[k],sb[k],rtol=0,atol=1e-12)
        for year in (2025,2024):
            for case in self.spec[f"benchmarks_{year}"]:
                a,sa,aa=pr.simulate(self.raw,case,pr.Physics())
                b,sb,ab=pr.simulate(self.raw,case,pr.Physics(),python_reference=True)
                self.assertEqual(a,b)
                self.assertTrue(aa["valid"] and ab["valid"])
                for k in sa:
                    np.testing.assert_array_equal(sa[k],sb[k],err_msg=f"{year} {case['id']} {k}")

    def test_independent_reference_matches_previously_audited_physics_all_ten(self):
        # A regression oracle only. The production module imports no old model.
        import grid_search as gs
        original=gs.load_model(TEACHER/"NRHES_Model.py")
        p=original.create_params();d=gs.load_data(TEACHER/"InputData.xlsx")
        components=gs.renewable_components(original,d,p)
        for c in self.spec["benchmarks_2025"]:
            cap={"N_pv":c["pv"],"N_wt":c["wind"],"C_TES":c["tes"],
                 "P_SOEC":c["soec"],"DF":c["df"],"TES_factor":c["tes_factor"]}
            old=gs.simulate(d,gs.renewable(cap,components),p,cap,c["mode"],gs.Physics(25,25))
            m,s,a=pr.simulate(self.raw,c,pr.Physics())
            self.assertTrue(a["valid"])
            for key in ("LF","H2","DPS","WER","WEN","Q"):
                np.testing.assert_allclose(s[key],old.series[key],rtol=0,atol=1e-8)
            self.assertAlmostEqual(m["DPSP"],old.metrics["DPSP"],places=10)
            self.assertAlmostEqual(m["EWP"],old.metrics["EWP"],places=10)

    def test_teacher_canonical_results_are_reproduced_without_modifying_source(self):
        expected=[(.00557652462,.04631463647,.06868801519),
                  (.00396945828,.04663451093,.08328545166),
                  (.01118994443,.02461842513,.06526651657)]
        cases=[self.source_case(i) for i in (0,3,9)]
        before=pr.original_hashes()
        with tempfile.TemporaryDirectory() as tmp,patch.object(pr,"plot_case"):
            rows,_=pr.teacher_comparison(self.raw,cases,Path(tmp)/"teacher")
            for r,values in zip(rows,expected):
                np.testing.assert_allclose([r[k] for k in pr.METRICS],values,rtol=0,atol=1e-10)
                self.assertGreater(r["max_balance_residual_MWh"],1e-7)
        self.assertEqual(before,pr.original_hashes())

    def test_relative_bands_not_percentage_points(self):
        c=[self.source_case()]
        rows=[{"id":c[0]["id"],**{k:v*1.1 for k,v in c[0]["target"].items()}}]
        result=pr.errors(rows,c)
        self.assertTrue(result["all_within_10_percent"])
        self.assertFalse(result["all_within_5_percent"])
        rows[0]["EWP"]*=1.001
        self.assertFalse(pr.errors(rows,c)["all_within_10_percent"])

    def test_mode1_feasibility_rejects_all_published_generation_interpretations(self):
        with tempfile.TemporaryDirectory() as tmp:
            pr.feasibility(self.raw,self.spec,Path(tmp))
            bounds=pd.read_csv(Path(tmp)/"mode1_energy_bounds.csv")
            self.assertEqual(len(bounds),12)
            self.assertFalse(bounds["within_10percent_possible"].any())
            compatibility=json.loads((Path(tmp)/"paper_2024_input_compatibility.json").read_text())
            self.assertTrue(compatibility["reactor_alone_meets_all_hours"])

    def test_invalid_parameters_and_config_are_rejected(self):
        for change in ({"tes_charge_mwth":0},{"pv_model":"adjust_solar"},{"reactor_2025_mwth":22}):
            with self.assertRaises(ValueError):pr.Physics(**change)
        for change in ({"years":True},{"years":61},{"real_rate":float("nan")},{"load_basis":"fit_target"}):
            with self.assertRaises(ValueError):pr.Finance(**change)

    def test_artifact_validator_rejects_changed_files_code_and_input_values(self):
        for key,message in (("original_hashes","Protected files changed"),
                            ("implementation_sha256","Implementation changed"),
                            ("input_values_fingerprint","Input arrays changed")):
            manifest={"original_hashes":pr.original_hashes(),
                      "implementation_sha256":pr.digest(pr.__file__),
                      "input_values_fingerprint":self.raw.fingerprint()}
            if key=="original_hashes":manifest[key]["InputData.xlsx"]="changed"
            else:manifest[key]="changed"
            with tempfile.TemporaryDirectory() as tmp:
                out=Path(tmp)
                pr.save_json(out/"manifest.json",manifest)
                pr.save_json(out/"paper_reproduction_config.json",self.spec)
                with self.assertRaisesRegex(AssertionError,message):pv.validate(out)

    def test_artifact_link_parser_and_image_checks(self):
        parser=pv.Links()
        parser.feed('<a href="../index.html">All</a><img src="candidate.png"><a href="results.csv">Results</a>')
        self.assertEqual(parser.links,["../index.html","candidate.png","results.csv"])
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/"candidate.png";Image.new("RGB",(20,30),"white").save(p)
            pv.check_image(p,(20,30))
            with self.assertRaises(AssertionError):pv.check_image(p,(21,30))

    def test_small_grid_full_comparison_and_frozen_rerun(self):
        spec=copy.deepcopy(self.spec)
        spec["physical_grid"]={k:[v[0]] for k,v in spec["physical_grid"].items()}
        spec["physical_grid"]["tes_charge_mwth"]=[5,25]
        spec["economic_grid"]={k:[v[0]] for k,v in spec["economic_grid"].items()}
        spec["economic_grid"]["real_rate"]=[pr.Finance().real_rate,.1]
        spec["financial_shortlist_dispatches"]=1
        before=pr.original_hashes()
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);config=base/"config.json";pr.save_json(config,spec)
            with patch.object(pr,"plot_case"):
                snapshot=pr.run(config,base/"run")
            out=base/"run";manifest=json.loads((out/"manifest.json").read_text())
            self.assertEqual(manifest["physical_dispatches"],2)
            rank=pd.read_csv(out/"ranking.csv")
            self.assertEqual(len(list((out/"cases").glob("*/candidate.png"))),len(rank))
            self.assertEqual(len(pd.read_csv(out/"all_results.csv")),len(rank)*10)
            diag=rank[rank.stage=="invalid_additive_diagnostic"]
            self.assertEqual(len(diag),1)
            self.assertFalse(diag.valid.iloc[0])
            self.assertNotEqual(snapshot["selected_case"],diag.case_id.iloc[0])
            self.assertTrue(manifest["Excel_values_unchanged_in_memory"])
            with Image.open(out/"selected/candidate.png") as image:
                self.assertEqual(image.size,(2500,1630))
            self.assertEqual(len(pd.read_csv(out/"teacher/results.csv")),10)
            self.assertEqual(len(pd.read_csv(out/"paper_2024_all_controls.csv")),40)
            self.assertEqual(len(pd.read_csv(out/"paper_2024_Table5_algorithm_reference.csv")),4)
            self.assertIn("reference_only",pd.read_csv(out/"paper_2024_Table5_algorithm_reference.csv").reproduction_status.iloc[0])
            common=pd.read_csv(out/"teacher_common_finance.csv")
            for c,r in zip(spec["benchmarks_2025"],common.to_dict("records")):
                self.assertAlmostEqual(r["LCOE_relative_error_percent"],100*(r["LCOE"]/c["target"]["LCOE"]-1))
            with patch.object(pr,"plot_case"):
                pr.rerun(out/"selection.json",base/"frozen")
            pd.testing.assert_frame_equal(pd.read_csv(out/"selected/results.csv"),pd.read_csv(base/"frozen/results.csv"),check_exact=True)
            with self.assertRaises(FileExistsError):pr.run(config,out)
        self.assertEqual(before,pr.original_hashes())


if __name__=="__main__":
    unittest.main()
