import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
TEACHER = ROOT.parent / "teacher_implementation"
sys.path.insert(0,str(ROOT))
import grid_search as gs
import table5_calibration as tc
import paper_input_check as pc


class PaperInputCheckTests(unittest.TestCase):
    def test_source_control_rejects_weather_or_load_fitting(self):
        with tempfile.TemporaryDirectory() as tmp:
            e=pc.SourceExperiment(None,None,None,None,Path(tmp),source_hash="fixture")
            branch=next(tc.branches("fixed_inputs"))
            names,_=tc.fields_bounds(branch,tc.load_config(ROOT/"table5_config.json"))
            s=tc.settings_for(branch,names,[25,7.5,.1])
            s["solar_exponent"]=1.1
            with self.assertRaises(ValueError):
                e.evaluate(s,"test")

    def test_frozen_source_case_is_audited_and_wrong_input_hash_rejected(self):
        # Excel is only a convenient finite 8760-hour fixture for this pipeline
        # test, not a purported digitization of the paper.
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp)
            data=gs.load_data(TEACHER/"InputData.xlsx")
            input_file=base/"fixture.csv"
            data.to_csv(input_file,index=False)
            spec=tc.load_config(ROOT/"table5_config.json")
            branch=next(tc.branches("fixed_inputs"))
            names,_=tc.fields_bounds(branch,spec)
            s=tc.settings_for(branch,names,[25,7.5,.1])
            s["figure5_input_sha256"]=gs.sha256(input_file)
            settings=base/"settings.json"
            gs.save_json(settings,s)
            with patch.object(gs,"plot_mode"):
                result=pc.run(input_file,base/"run",settings)
            self.assertFalse(result["all_within_10_percent"])
            self.assertEqual(len(pd.read_csv(base/"run"/"selected"/"results.csv")),10)
            audits=json.loads((base/"run"/"selected"/"audits.json").read_text())
            self.assertTrue(all(a["valid"] for a in audits))
            s["figure5_input_sha256"]="different"
            gs.save_json(settings,s)
            with self.assertRaises(ValueError):
                pc.run(input_file,base/"bad",settings)
