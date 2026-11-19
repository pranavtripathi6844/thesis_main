"""Check Table 5 using vector-digitized Figure 5 inputs, without fitting weather.

This source-derived control complements, rather than replaces, the two-track
Excel experiment. It fits only common unspecified TES power limits and real rate.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

import figure5_reconstruction as fr
import grid_search as gs
import table5_calibration as tc

HERE = Path(__file__).resolve().parent
TEACHER = HERE.parent / "teacher_implementation"


class SourceExperiment(tc.Experiment):
    def __init__(self, *args, source_hash, **kwargs):
        super().__init__(*args, **kwargs)
        self.source_hash = source_hash

    def evaluate(self, settings, stage):
        settings = {**settings, "track":"paper_digitized",
                    "figure5_input_sha256":self.source_hash}
        # digitized inputs are used as they are, no tuning
        if any(settings[k] != v for k,v in
               {"solar_exponent":1,"load_offset_mw":0,"load_seasonal_gain":1,"weather_shift_hours":0}.items()):
            raise ValueError("The digitized-paper control does not tune or shift the reconstructed weather/load.")
        return super().evaluate(settings,stage)


def run(input_path, output, case_settings=None):
    input_path, out = Path(input_path).resolve(), Path(output).resolve()
    if out.exists():
        raise FileExistsError("Use a new output directory.")
    raw = pd.read_csv(input_path)
    if len(raw)!=8760 or list(raw.columns)!=["hours","G_Tot","T_a","V_anem","P_grid"] or not np.isfinite(raw.to_numpy()).all():
        raise ValueError("Require the reconstructed Figure 5 hourly CSV.")
    if not np.array_equal(raw.hours,np.arange(1,8761)) or (raw[["G_Tot","V_anem","P_grid"]]<0).any().any() or raw.P_grid.min()<=0:
        raise ValueError("Invalid reconstructed hours or physical inputs.")
    hashes = tc.protected_hashes(TEACHER)
    source_hash = gs.sha256(input_path)
    params = gs.load_model(TEACHER/"NRHES_Model.py").create_params()
    _, wind = gs.renewable_components(gs.load_model(TEACHER/"NRHES_Model.py"),raw,params)
    spec = tc.load_config(HERE/"table5_config.json")
    spec["search"]["fixed_inputs"]["generations"] = 12
    out.mkdir(parents=True)
    raw.to_csv(out/"digitized_inputs.csv",index=False)
    gs.save_json(out/"config.json",spec)
    tc.preflight(raw,params,wind,spec,out)
    experiment = SourceExperiment(raw,params,wind,spec,out,source_hash=source_hash)
    if case_settings:
        saved = json.loads(Path(case_settings).read_text())
        if saved.get("figure5_input_sha256")!=source_hash:
            raise ValueError("Reconstructed input hash differs from the saved case.")
        experiment.evaluate(saved,"frozen")
    else:
        # Paired controls include all three diagnostic TES settings.
        for interpretation in ("area_efficiency","nameplate_150W"):
            branch = next(tc.branches("fixed_inputs"))
            branch["pv_interpretation"] = interpretation
            names,_ = tc.fields_bounds(branch,spec)
            for ch,dis in ((10,10),(25,7.5),(25,25)):
                experiment.evaluate(tc.settings_for(branch,names,[ch,dis,.1]),"control")
        experiment.search("fixed_inputs")
    rank = pd.DataFrame(experiment.records).sort_values(
        ["design_valid","objective","case_id"],ascending=[False,True,True])
    valid = rank[rank.design_valid]
    if valid.empty:
        raise RuntimeError("No design-valid digitized-input candidate.")
    chosen = valid.iloc[0]
    settings = json.loads((out/"cases"/chosen.case_id/"settings.json").read_text())
    data,expected,_,summary,_ = tc.evaluate(raw,params,wind,spec,settings)
    pv,wt = tc.components(data,params,settings["pv_interpretation"],wind,0)
    selected = out/"selected"
    selected.mkdir()
    rows,audits = [],[]
    for b,e in zip(spec["benchmarks"],expected):
        c = b["capacity"]
        physical = gs.Physics(*tc.limits(settings,c["C_TES"]))
        sim = gs.simulate(data,c["N_pv"]*pv+c["N_wt"]*wt,params,c,b["mode"],physical)
        sim.metrics.update(gs.economic_metrics(sim,params,gs.Economics(
            settings["real_rate"],spec["variable_cost_basis"],spec["decommissioning"]),spec["project_years"]))
        if not sim.audit["valid"] or any(abs(sim.metrics[k]-e[k])>1e-10 for k in gs.METRICS):
            raise AssertionError(f"Reference audit failed for {b['id']}.")
        np.savez_compressed(selected/f"row_{b['id']}.npz",**sim.series)
        gs.plot_mode(sim,selected/f"row_{b['id']}.png",
            f"Digitized Figure 5 input, not author raw data | Table 5 row {b['id']} | candidate only\n"
            f"PV={c['N_pv']}; TES={c['C_TES']} MWhth; power limits={physical.charge_mwth:.4g}/{physical.discharge_mwth:.4g} MWth; "
            f"real rate={settings['real_rate']*100:.4g}%\n"
            f"DPSP={sim.metrics['DPSP']*100:.5g}%; EWP={sim.metrics['EWP']*100:.5g}%; LCOE=${sim.metrics['LCOE']:.6g}/kWh")
        rows.append({"id":b["id"],**sim.metrics,
                     **{k+"_relative_error_percent":e[k+"_relative_error_percent"] for k in gs.METRICS}})
        audits.append({"id":b["id"],**sim.audit})
    gs.save_json(selected/"settings.json",settings)
    gs.save_json(selected/"audits.json",audits)
    pd.DataFrame(rows).to_csv(selected/"results.csv",index=False)
    rank.to_csv(out/"ranking.csv",index=False)
    pd.DataFrame(experiment.rows).to_csv(out/"all_metrics.csv",index=False)
    gs.save_json(out/"manifest.json",{
        "track":"paper_digitized","selected_case":chosen.case_id,"summary":summary,
        "originals_unchanged":tc.protected_hashes(TEACHER)==hashes,
        "protected_hashes":hashes,"input_sha256":source_hash,"paper_sha256":gs.sha256(fr.PAPER),
        "reconstruction_implementation_sha256":gs.sha256(HERE/"figure5_reconstruction.py"),
        "configuration_count":len(rank),"dashboard_count":len(list((out/"cases").glob("*/candidate.png"))),
        "no_reconstructed_weather_or_load_tuning":True})
    if tc.protected_hashes(TEACHER)!=hashes:
        raise AssertionError("Original input/model changed.")
    (out/"index.html").write_text(
        '<!doctype html><meta charset="utf-8"><h1>Digitized Figure 5 control</h1>'
        '<p>Approximate plotted data, not author raw inputs. No fitting of reconstructed weather or load.</p>'
        '<p><a href="manifest.json">Summary</a> | <a href="selected/results.csv">Selected results</a></p>'
        + "".join(f'<p><a href="cases/{r.case_id}/index.html">{r.case_id}</a>: worst error {r.all_worst_relative_percent:.3f}%</p>'
                  for r in rank.itertuples()))
    print(json.dumps({"selected_case":chosen.case_id,**summary},indent=2),flush=True)
    return summary


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    p.add_argument("--case-settings",type=Path,help="Rerun only a saved digitized-paper case.")
    args=p.parse_args()
    run(args.input,args.output,args.case_settings)
