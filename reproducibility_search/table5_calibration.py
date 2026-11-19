"""Compare unchanged-input and explicitly input-calibrated Table 5 models.

Numba accelerates the lossless published dispatch only. Exported selections are
independently rerun through grid_search.simulate and its separate energy audits.
Every distinct outer-search configuration has its own candidate-only dashboard.
"""
from __future__ import annotations

import argparse
import copy
import html
import itertools
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import matplotlib.font_manager as fm
import numpy as np
import pandas as pd
from numba import njit
from PIL import Image, ImageDraw, ImageFont
from scipy.optimize import differential_evolution
from scipy.stats import qmc

import grid_search as gs

HERE = Path(__file__).resolve().parent
TEACHER = HERE.parent / "teacher_implementation"
TRACKS = ("fixed_inputs", "input_calibrated")
BASE_FIELDS = ("charge_reference_mwth", "discharge_reference_mwth", "real_rate")
INPUT_FIELDS = ("solar_exponent", "load_offset_mw", "load_seasonal_gain")
PLOT_FIELDS = ("LF", "H2", "DPS", "WEN", "WER")
EXPECTED_IDS = ("1a", "1b", "1c", "2a", "2b", "2c", "3a", "3b", "3c", "3d")


def load_config(path):
    spec = json.loads(Path(path).read_text())
    rows = spec["benchmarks"]
    if [r["id"] for r in rows] != list(EXPECTED_IDS):
        raise ValueError("Require exactly the ten Table 5 rows, in their published order.")
    for r in rows:
        if r["mode"] != int(r["id"][0]):
            raise ValueError("Row ID and mode disagree.")
        if set(r["target"]) != set(gs.METRICS):
            raise ValueError("Every row needs DPSP, EWP and LCOE.")
        if not all(np.isfinite(x) and x > 0 for x in r["target"].values()):
            raise ValueError("Relative-error targets must be finite and strictly positive.")
        c = r["capacity"]
        values = list(c.values())
        static = (np.isfinite(values).all() and 40000 <= c["N_pv"] <= 80000
                  and float(c["N_pv"]).is_integer()
                  and c["N_wt"] == 1 and 250 <= c["C_TES"] <= 2000)
        if r["mode"] > 1:
            static &= c["P_SOEC"] > 0 and 0.2 <= c["DF"] <= 1
        if r["mode"] == 3:
            static &= 0.2 <= c["TES_factor"] <= 1
        if not static:
            raise ValueError("Invalid static Table 5 design.")
    if not set(spec["reserved_rows"]) < set(EXPECTED_IDS):
        raise ValueError("Reserve a proper subset of the benchmark rows.")
    if sorted(spec["acceptance_relative_percent"]) != [5, 10]:
        raise ValueError("This experiment must retain explicit 5% and 10% checks.")
    if not isinstance(spec["project_years"], int) or isinstance(spec["project_years"], bool) or not 1 <= spec["project_years"] <= 60:
        raise ValueError("Project years must be an integer in [1,60].")
    gs.Economics(0.1, spec["variable_cost_basis"], spec["decommissioning"])
    for limits in spec["bounds"].values():
        if len(limits) != 2 or not np.isfinite(limits).all() or limits[0] >= limits[1]:
            raise ValueError("Every parameter requires finite, ordered bounds.")
    b = spec["bounds"]
    if any(b[k][0] <= 0 for k in b if k.startswith(("charge_", "discharge_"))):
        raise ValueError("TES limits must be positive.")
    if b["solar_exponent"][0] < 1 or b["load_seasonal_gain"][0] < 0 or b["real_rate"][0] < 0:
        raise ValueError("Invalid calibrated-input or financial bounds.")
    for search in spec["search"].values():
        if any(isinstance(v, bool) or not isinstance(v, int) or v < 1 for v in search.values()):
            raise ValueError("Search budgets must be positive integers.")
    return spec


def input_scenario(data, settings):
    """A common scenario for all rows, never a claim of recovered author data."""
    gamma = settings.get("solar_exponent", 1.0)
    offset = settings.get("load_offset_mw", 0.0)
    gain = settings.get("load_seasonal_gain", 1.0)
    shift = settings.get("weather_shift_hours", 0)
    if not np.isfinite([gamma, offset, gain, shift]).all() or gamma < 1 or gain < 0:
        raise ValueError("Invalid input scenario.")
    # +8 h tests a UTC vs local (China) time mismatch
    if isinstance(shift, bool) or shift not in (0, 8):
        raise ValueError("Only the documented 0/+8 hour weather hypotheses are supported.")
    result = data.copy(deep=True)
    g = data.G_Tot.to_numpy()
    if gamma != 1:
        peak = float(g.max())
        g = peak * (g / peak) ** gamma if peak else g.copy()
    result["G_Tot"] = np.roll(g, shift)
    for key in ("T_a", "V_anem"):
        result[key] = np.roll(data[key].to_numpy(), shift)
    if offset != 0 or gain != 1:
        load = data.P_grid.to_numpy()
        daily = load.reshape(-1, 24).mean(axis=1)
        # 31-day moving average of daily load
        seasonal = np.repeat(sum(np.roll(daily, k) for k in range(-15, 16)) / 31, 24)
        result["P_grid"] = load + offset + (gain - 1) * (seasonal - seasonal.mean())
    if not np.isfinite(result.to_numpy()).all() or result.P_grid.min() <= 0:
        raise ValueError("Scenario produces invalid or nonpositive demand.")
    return result


def components(data, params, interpretation, baseline_wind, shift):
    p = params["PV"]
    g, t = data.G_Tot.to_numpy(), data.T_a.to_numpy()
    cell = t + (p["NOCT"] - 20) / 800 * g
    pv = np.maximum(0, p["eta_r"] * p["eta_pc"] * (1 - p["mu"] * (cell - p["T_ref"])) * p["A"] * g / 1e6)
    if interpretation == "nameplate_150W":
        # 150 W nameplate reading of the PV model
        pv *= p["P_rated"] / (p["eta_r"] * p["A"] * 1000)
    elif interpretation != "area_efficiency":
        raise ValueError("Unknown PV interpretation.")
    return pv, np.roll(baseline_wind, shift)


def limits(settings, capacity):
    values = []
    for direction in ("charge", "discharge"):
        # scaled relative to the 2000 MWh design
        scale = capacity / 2000 if settings[f"{direction}_model"] == "capacity_scaled" else 1
        values.append(settings[f"{direction}_reference_mwth"] * scale)
    return values


@njit(cache=False)
def dispatch(load, re, mode, capacity, df, soec, threshold, charge, discharge,
             thermal, eta, minimum, ke, kth, minimum_soec):
    """Lossless/unit-efficiency specialization of gs.simulate, without fastmath."""
    result = np.zeros((len(load), 5))
    qmin = capacity * minimum
    q = qmin
    nuclear = thermal * eta
    for i in range(len(load)):
        power, demand = re[i], load[i]
        mode2 = mode == 2 or (mode == 3 and q / capacity >= threshold)
        re_grid = min(power, demand)
        nuc_grid = min(nuclear, max(0.0, demand - re_grid))
        h = e_draw = h_draw = 0.0
        if mode2:
            g1, g2 = df * demand, (1 - df) * demand
            if power > g1 and nuclear > g2:
                re_grid, nuc_grid = g1, g2
                e_available, h_available = power - g1, thermal - g2 / eta
                e_alloc = min(e_available, soec)
                if e_alloc >= minimum_soec * soec:
                    h = min(ke * e_alloc, kth * h_available)
                    if h / ke < minimum_soec * soec:
                        h = 0.0
                    e_draw, h_draw = h / ke, h / kth
        remaining = max(0.0, thermal - nuc_grid / eta - h_draw)
        need = max(0.0, demand - re_grid - nuc_grid)
        ch = dis = 0.0
        if need > 0:
            dis = min(need / eta, discharge, max(0.0, q - qmin))
        else:
            ch = min(remaining, charge, max(0.0, capacity - q))
        q += ch - dis
        result[i, 0] = q / capacity
        result[i, 1] = h
        result[i, 2] = max(0.0, need - dis * eta)
        result[i, 3] = max(0.0, remaining - ch)
        result[i, 4] = max(0.0, power - re_grid - e_draw)
    return result


def error_summary(rows, benchmarks, reserved):
    if len(rows) != len(benchmarks) or [r["id"] for r in rows] != [r["id"] for r in benchmarks]:
        raise ValueError("Results must match every benchmark ID exactly.")
    errors = np.array([[(r[k] - b["target"][k]) / b["target"][k]
                        for k in gs.METRICS] for r, b in zip(rows, benchmarks)])
    if not np.isfinite(errors).all():
        raise ValueError("Nonfinite comparison metrics.")
    fit = np.array([r["id"] not in reserved for r in benchmarks])
    result = {}
    for label, mask in (("all", np.ones(len(rows), dtype=bool)), ("fit", fit), ("reserved", ~fit)):
        e = errors[mask]
        if not e.size:
            continue
        result[f"{label}_worst_relative_percent"] = float(np.abs(e).max() * 100)
        result[f"{label}_rms_relative_percent"] = float(np.sqrt(np.mean(e * e)) * 100)
        for band in (5, 10):
            result[f"{label}_within_{band}_percent"] = bool(np.abs(e).max() <= band / 100 + 1e-12)
            result[f"{label}_metrics_within_{band}_percent"] = int((np.abs(e) <= band / 100 + 1e-12).sum())
            result[f"{label}_rows_within_{band}_percent"] = int((np.abs(e) <= band / 100 + 1e-12).all(axis=1).sum())
    return result


def evaluate(raw, params, wind, spec, settings):
    if params["dt"] != 1 or any(params["TES"][k] != v for k, v in
            {"sigma": 0, "eta_ch": 1, "eta_dis": 1, "LF_max": 1}.items()):
        raise ValueError("Accelerated dispatch supports only the published lossless hourly TES.")
    if settings["track"] == "fixed_inputs" and any(settings[k] != v for k, v in
            {"solar_exponent": 1, "load_offset_mw": 0, "load_seasonal_gain": 1, "weather_shift_hours": 0}.items()):
        raise ValueError("The unchanged-input control cannot adjust hourly data.")
    data = input_scenario(raw, settings)
    pv, wt = components(data, params, settings["pv_interpretation"], wind, settings["weather_shift_hours"])
    load = data.P_grid.to_numpy()
    rows, curves = [], []
    ecase = gs.Economics(settings["real_rate"], spec["variable_cost_basis"], spec["decommissioning"])
    for b in spec["benchmarks"]:
        c, mode = b["capacity"], b["mode"]
        re = c["N_pv"] * pv + c["N_wt"] * wt
        ch, dis = limits(settings, c["C_TES"])
        a = dispatch(load, re, mode, c["C_TES"], c.get("DF", 0), c.get("P_SOEC", 0),
                     c.get("TES_factor", 0), ch, dis, params["smTMSR"]["P_th"],
                     params["smTMSR"]["eta_PB"], params["TES"]["LF_min"],
                     params["HTSE"]["K_e"], params["HTSE"]["K_th"], params["HTSE"]["P_e_min_ratio"])
        demand = float(load.sum())
        dps = float(a[:, 2].sum())
        m = {
            "DPSP": dps / demand,
            "EWP": float((a[:, 4].sum() + params["smTMSR"]["eta_PB"] * a[:, 3].sum())
                         / (re.sum() + len(load) * params["smTMSR"]["P_e"])),
            "H2_tonnes": float(a[:, 1].sum() / 1000), "DPS_MWh": dps,
            "annual_load_MWh": demand, "WEN_MWhth": float(a[:, 3].sum()),
            "WER_MWh": float(a[:, 4].sum()), "LF_dip_4350_4550": float(a[4349:4550, 0].min()),
            "peak_RE_MW": float(re.max()), "charge_mwth": ch, "discharge_mwth": dis,
            "design_valid": gs.design_valid(c, mode, float(re.max())),
        }
        dummy = gs.Simulation(mode, c, {"H2": a[:, 1]}, m, {})
        m.update(gs.economic_metrics(dummy, params, ecase, spec["project_years"]))
        m.update({"id": b["id"], "mode": mode})
        for key in gs.METRICS:
            m[key + "_target"] = b["target"][key]
            m[key + "_relative_error_percent"] = 100 * (m[key] / b["target"][key] - 1)
        rows.append(m)
        curves.append(a)
    summary = error_summary(rows, spec["benchmarks"], spec["reserved_rows"])
    summary["design_valid"] = all(r["design_valid"] for r in rows)
    # Reserved rows never influence the fitting objective or branch selection.
    objective = (summary["fit_worst_relative_percent"] + 0.1 * summary["fit_rms_relative_percent"]) / 100
    if not summary["design_valid"]:
        violations = [max(0, b["capacity"].get("P_SOEC", 0) / r["peak_RE_MW"] - 1)
                      for r, b in zip(rows, spec["benchmarks"])]
        objective += 100 + sum(violations)
    return data, rows, curves, summary, float(objective)


def dashboard(path, case_id, settings, rows, curves, summary, spec):
    """Hourly min/max envelopes per display pixel retain short events."""
    width, height, left, step, top, row_height = 2500, 1700, 680, 354, 295, 133
    im = Image.new("RGB", (width, height), "white")
    d = ImageDraw.Draw(im)
    fontpath = fm.findfont("DejaVu Sans")
    small, normal, title = (ImageFont.truetype(fontpath, x) for x in (16, 19, 27))
    d.text((20, 12), f"{case_id} | {settings['track']} | ALL TEN TABLE 5 DESIGNS", font=title, fill="black")
    warning = ("UNCHANGED EXCEL INPUTS" if settings["track"] == "fixed_inputs"
               else "INPUT-CALIBRATED SCENARIO, NOT AUTHOR DATA")
    d.text((20, 52), warning, font=normal, fill="#954000")
    lines = [
        f"PV={settings['pv_interpretation']}; charge={settings['charge_model']} {settings['charge_reference_mwth']:.5g}; discharge={settings['discharge_model']} {settings['discharge_reference_mwth']:.5g} MWth at reference 2000 MWhth",
        f"Shared real rate={100*settings['real_rate']:.5g}%; project={spec['project_years']}y; variable costs={spec['variable_cost_basis']}; decom={spec['decommissioning']}; resource-min H2; initial/min TES=5%",
        f"Solar exponent={settings['solar_exponent']:.6g}; load offset={settings['load_offset_mw']:.6g} MW; seasonal gain={settings['load_seasonal_gain']:.6g}; all weather shifted +{settings['weather_shift_hours']}h",
        f"All 30 metrics: worst relative error={summary['all_worst_relative_percent']:.3f}%; RMS={summary['all_rms_relative_percent']:.3f}%; 10% pass={summary['all_within_10_percent']}; design bounds={summary['design_valid']}",
        "Left: candidate / paper (signed relative error). DPSP and EWP in %, LCOE in $/kWh. * = reserved from fitting, seen in earlier experiments.",
        "Right: candidate-only hourly envelopes (min/max in each pixel bin); all plots span hours 1-8760. Each column has a common vertical scale."
    ]
    for i, line in enumerate(lines):
        d.text((20, 88 + 28*i), line, font=normal if i < 4 else small, fill="black")
    labels = ("TES LF [0,1]", "H2 [0,350 kg/h]", "DPS [0,8 MWh]", "Nuclear waste [0,25 MWhth]", "RE waste [0,20 MWh]")
    for j, label in enumerate(labels):
        d.text((left + step*j, top-28), label, font=small, fill="black")
    ymax = (1, 350, 8, 25, 20)
    for i, (r, a) in enumerate(zip(rows, curves)):
        y = top + i*row_height
        d.line((10, y-6, width-15, y-6), fill="#cccccc")
        d.text((20, y), f"Row {r['id']}" + (" *" if r.get("reserved") else ""), font=normal, fill="black")
        for n, key in enumerate(gs.METRICS):
            scale = 1 if key == "LCOE" else 100
            error = r[key+"_relative_error_percent"]
            text = f"{key}: {r[key]*scale:.6g} / {r[key+'_target']*scale:.6g} ({error:+.2f}%)"
            d.text((20, y+27+n*25), text, font=normal, fill="#006428" if abs(error) <= 10+1e-10 else "#ac1926")
        for j in range(5):
            x, w, h = left+step*j, 326, 103
            d.rectangle((x, y+3, x+w, y+3+h), outline="#cccccc")
            for tick in (w//2,):
                d.line((x+tick,y+3,x+tick,y+3+h),fill="#eeeeee")
            # Pad to equal-size bins; NaN padding is excluded from extrema.
            bins = np.array_split(a[:, j], w)
            low = np.array([v.min() for v in bins])
            high = np.array([v.max() for v in bins])
            if high.max() > ymax[j] + 1e-8:
                raise ValueError(f"Plot axis would hide an event in {PLOT_FIELDS[j]}.")
            for k in range(w):
                d.line((x+k, y+3+h-int(high[k]/ymax[j]*h),
                        x+k, y+3+h-int(low[k]/ymax[j]*h)), fill="#1764a3", width=1)
    d.text((20, 1650), "Acceptance requires all three metrics in all ten rows AND independent energy/resource/design audits. Annual matching is not author-curve validation.", font=small, fill="black")
    im.save(path, compress_level=3)


def branches(track):
    for pv, ch, dis, shift in itertools.product(
            ("area_efficiency", "nameplate_150W"),
            ("absolute", "capacity_scaled"), ("absolute", "capacity_scaled"),
            (0,) if track == "fixed_inputs" else (0, 8)):
        yield {"track": track, "pv_interpretation": pv, "charge_model": ch,
               "discharge_model": dis, "weather_shift_hours": shift}


def fields_bounds(branch, spec):
    names = list(BASE_FIELDS)
    keys = [f"charge_{branch['charge_model']}", f"discharge_{branch['discharge_model']}", "real_rate"]
    if branch["track"] == "input_calibrated":
        names += list(INPUT_FIELDS)
        keys += list(INPUT_FIELDS)
    return names, [spec["bounds"][k] for k in keys]


def settings_for(branch, names, vector):
    return {**branch, "solar_exponent": 1.0, "load_offset_mw": 0.0,
            "load_seasonal_gain": 1.0, **dict(zip(names, map(float, vector)))}


class Experiment:
    def __init__(self, raw, params, wind, spec, out):
        self.raw, self.params, self.wind, self.spec, self.out = raw, params, wind, spec, out
        self.records, self.rows, self.cache = [], [], {}
        (out / "cases").mkdir(parents=True)

    def evaluate(self, settings, stage):
        key = json.dumps(settings, sort_keys=True)
        if key in self.cache:
            return self.cache[key]
        _, rows, curves, summary, objective = evaluate(self.raw, self.params, self.wind, self.spec, settings)
        cid = f"C{len(self.records)+1:05d}"
        folder = self.out / "cases" / cid
        folder.mkdir()
        for r in rows:
            r["reserved"] = r["id"] in self.spec["reserved_rows"]
        record = {"case_id": cid, "stage": stage, **settings, **summary, "objective": objective}
        dashboard(folder / "candidate.png", cid, settings, rows, curves, summary, self.spec)
        gs.save_json(folder / "settings.json", settings)
        pd.DataFrame(rows).to_csv(folder / "metrics.csv", index=False)
        (folder / "index.html").write_text(
            f'<!doctype html><meta charset="utf-8"><title>{cid}</title><a href="../../index.html">All cases</a>'
            f'<h1>{cid}: {settings["track"]}</h1><a href="settings.json">Settings</a> | '
            '<a href="metrics.csv">Metrics</a><br><img style="max-width:100%" src="candidate.png">'
        )
        self.records.append(record)
        self.rows.extend({"case_id": cid, "track": settings["track"], **r} for r in rows)
        self.cache[key] = objective
        if len(self.records) % 100 == 0:
            print(f"{len(self.records)} distinct configurations saved; latest={cid}/{settings['track']}", flush=True)
        return objective

    def search(self, track):
        search = self.spec["search"][track]
        branch_results = []
        for i, branch in enumerate(branches(track)):
            names, bounds = fields_bounds(branch, self.spec)
            lo, hi = np.array(bounds).T
            sample = qmc.Sobol(len(names), scramble=True, seed=self.spec["seed"]+i)
            vectors = lo + sample.random_base2(search["coarse_power"]) * (hi-lo)
            # Include the prior provisional settings as a paired control.
            anchor = [25.0, 7.5, 0.1] + ([1.0, 0.0, 1.0] if track == "input_calibrated" else [])
            vectors = np.vstack((np.clip(anchor, lo, hi), vectors))
            scores = [self.evaluate(settings_for(branch, names, v), "coarse") for v in vectors]
            best = int(np.argmin(scores))
            branch_results.append((scores[best], i, branch, vectors[best]))
        for _, i, branch, initial in sorted(branch_results, key=lambda x: (x[0], x[1]))[:search["refine_branches"]]:
            names, bounds = fields_bounds(branch, self.spec)
            print(f"Refining {track} branch {i}: {branch}", flush=True)
            differential_evolution(
                lambda x: self.evaluate(settings_for(branch, names, x), "refine"),
                bounds, seed=self.spec["seed"]+100+i, popsize=search["popsize"],
                maxiter=search["generations"], x0=initial, polish=False,
                workers=1, updating="immediate", tol=0, atol=0,
            )


def protected_hashes(root):
    return {name: gs.sha256(root / name) for name in ("NRHES_Model.py", "NRHES_Model.m", "InputData.xlsx")}


def export_selection(raw, params, wind, spec, settings, folder, hashes):
    folder.mkdir(parents=True)
    data, fast_rows, _, summary, _ = evaluate(raw, params, wind, spec, settings)
    pv, wt = components(data, params, settings["pv_interpretation"], wind, settings["weather_shift_hours"])
    rows, audits, figures = [], [], []
    for b, expected in zip(spec["benchmarks"], fast_rows):
        c = b["capacity"]
        physics = gs.Physics(*limits(settings, c["C_TES"]))
        sim = gs.simulate(data, c["N_pv"]*pv+c["N_wt"]*wt, params, c, b["mode"], physics)
        sim.metrics.update(gs.economic_metrics(sim, params, gs.Economics(
            settings["real_rate"], spec["variable_cost_basis"], spec["decommissioning"]), spec["project_years"]))
        differences = {k: abs(sim.metrics[k]-expected[k]) for k in gs.METRICS}
        if max(differences.values()) > 1e-10 or not sim.audit["valid"]:
            raise AssertionError(f"Independent reference validation failed: {b['id']}: {sim.audit}, {differences}")
        np.savez_compressed(folder / f"row_{b['id']}.npz", **sim.series)
        plot_title = (
            f"Table 5 row {b['id']} | {settings['track']} | {settings['pv_interpretation']} | candidate only\n"
            f"PV={c['N_pv']}; TES={c['C_TES']} MWhth; charge/discharge={physics.charge_mwth:.4g}/{physics.discharge_mwth:.4g} MWth; "
            f"rate={settings['real_rate']*100:.4g}%; project={spec['project_years']}y\n"
            f"Solar exponent={settings['solar_exponent']:.4g}; load offset={settings['load_offset_mw']:.4g} MW; "
            f"seasonal gain={settings['load_seasonal_gain']:.4g}; weather shift=+{settings['weather_shift_hours']}h\n"
            f"DPSP={sim.metrics['DPSP']*100:.5g}%; EWP={sim.metrics['EWP']*100:.5g}%; "
            f"LCOE=${sim.metrics['LCOE']:.6g}/kWh; H2={sim.metrics['H2_tonnes']:.5g} t/y"
        )
        gs.plot_mode(sim, folder / f"row_{b['id']}.png", plot_title)
        rows.append({"id": b["id"], "reserved": b["id"] in spec["reserved_rows"], **sim.metrics,
                     **{k+"_relative_error_percent": expected[k+"_relative_error_percent"] for k in gs.METRICS}})
        audits.append({"id": b["id"], **sim.audit, "fast_reference_absolute_differences": differences})
        active = sim.series["H2"] > 0
        figures.append({"id": b["id"], "LF_min_4350_4550": float(sim.series["LF"][4349:4550].min()),
                        "late_H2_hours_7500plus": int((active & (sim.series["Hour"] >= 7500)).sum())})
    data.to_csv(folder / "scenario_inputs.csv", index=False)
    pd.DataFrame(rows).to_csv(folder / "results.csv", index=False)
    gs.save_json(folder / "audits.json", audits)
    pd.DataFrame(figures).to_csv(folder / "figure_diagnostics.csv", index=False)
    # Explicitly report all input changes, including the unchanged control.
    input_stats = {"solar_kWh_m2": float(data.G_Tot.sum()/1000),
                   "original_solar_kWh_m2": float(raw.G_Tot.sum()/1000),
                   "annual_load_MWh": float(data.P_grid.sum()),
                   "original_annual_load_MWh": float(raw.P_grid.sum()),
                   "load_min_MW": float(data.P_grid.min()), "load_max_MW": float(data.P_grid.max()),
                   "changed_hours": {k: int((data[k].to_numpy()!=raw[k].to_numpy()).sum())
                                     for k in ("G_Tot","T_a","V_anem","P_grid")}}
    gs.save_json(folder / "input_changes.json", input_stats)
    selection = {
        "status": "annual_targets_met_not_author_curve_validated" if summary["all_within_10_percent"] else "not_within_requested_margin",
        "settings": settings, "summary": summary, "spec": spec,
        "protected_hashes": hashes, "fixed_params": params,
        "implementation_hashes": {p: gs.sha256(HERE / p) for p in ("grid_search.py", "table5_calibration.py")},
        "source_inputs": "scenario_inputs.csv is reconstructed/calibrated only if track=input_calibrated; it is not author data."
    }
    gs.save_json(folder / "selection.json", selection)
    return summary


def preflight(raw, params, wind, spec, out):
    records = []
    for pv_kind in ("area_efficiency", "nameplate_150W"):
        pv, wt = components(raw, params, pv_kind, wind, 0)
        for b in spec["benchmarks"][:3]:
            c = b["capacity"]
            bound = gs.energy_lower_bound(raw, c["N_pv"]*pv+c["N_wt"]*wt, params, c)
            records.append({"id": b["id"], "pv_interpretation": pv_kind, **bound,
                            "paper_EWP": b["target"]["EWP"],
                            "EWP_10percent_upper_limit": b["target"]["EWP"]*1.1,
                            "10percent_fit_possible_by_energy_budget": bound["minimum_possible_EWP"] <= b["target"]["EWP"]*1.1})
    pd.DataFrame(records).to_csv(out / "fixed_input_energy_bounds.csv", index=False)


def write_report(experiment, selections, out):
    frame = pd.DataFrame(experiment.records)
    rank = frame.sort_values(["design_valid", "objective", "case_id"], ascending=[False, True, True])
    rank.to_csv(out / "ranking.csv", index=False)
    pd.DataFrame(experiment.rows).to_csv(out / "all_metrics.csv", index=False)
    comparison = pd.DataFrame([{"track": t, "case_id": c["case_id"], **c["summary"]} for t,c in selections.items()])
    comparison.to_csv(out / "track_comparison.csv", index=False)
    lines = [
        "# Table 5: unchanged inputs versus input-calibrated scenarios", "",
        "Both tracks retain the published ten designs and shared component parameters. No RL is included.",
        "The input-calibrated track is an analyst-defined scenario, not recovered author meteorology or an independent reproduction.", "",
        "## Acceptance and selection", "",
        "Error is |candidate / paper - 1| separately for DPSP, EWP and LCOE. All 30 entries must pass the stated band.",
        "Physics/design audits are separate and mandatory. Five or ten percentage points are NOT used as tolerances.",
        "Fit uses seven rows; 1b, 2b and 3c are reserved from this objective but were seen in earlier work, not pristine external validation.",
        "Selection minimizes the worst fitting error plus 0.1 times fitting RMS. Reserved rows do not select candidates.",
        "Plot-shape features do not enter the fit. Annual agreement alone is not proof of figure agreement.", "",
        "| Track | Configurations | Worst all-row error | RMS all-row error | Metrics within 10% | Rows within 10% | All 30 within 5% / 10% |",
        "|---|---:|---:|---:|---:|---:|---|"
    ]
    for t,c in selections.items():
        s = c["summary"]
        lines.append(f"| {t} | {int((frame.track==t).sum())} | {s['all_worst_relative_percent']:.3f}% | {s['all_rms_relative_percent']:.3f}% | {s['all_metrics_within_10_percent']}/30 | {s['all_rows_within_10_percent']}/10 | {s['all_within_5_percent']} / {s['all_within_10_percent']} |")
        lines += ["", f"### Selected {t}: {c['case_id']}", "", "```json", json.dumps(c["settings"], indent=2), "```"]
    lines += [
        "", "## What the comparison can and cannot establish", "",
        "- Fixed-input Mode 1 lower bounds are in `fixed_input_energy_bounds.csv`. These are optimistic energy-budget bounds, independent of financing and TES power ratings.",
        "- Each tested outer configuration has `cases/Cxxxxx/settings.json`, `metrics.csv`, and its own candidate-only dashboard, including all ten designs.",
        "- Selection files, complete hourly NPZ arrays, full-size individual row plots, changed-input CSVs and independent reference audits are in `selected/<track>/`.",
        "- The optimizer's seven fitting rows and three reserved rows use identical transformed hourly inputs and shared settings; capacities and published control settings differ exactly as Table 5 specifies.",
        "- Input reconstruction uses a bounded peak-preserving irradiance exponent, common demand offset/seasonal gain, and a 0/+8-hour cyclic weather shift. These are unverified analyst scenarios, not validated corrections.",
        "- Both PV conventions and absolute/capacity-scaled TES ratings are screened. Only the strongest coarse branches are refined. This is a bounded search, not proof of a global optimum or of impossibility under every reconstructed input.",
        "- Project horizon 20 years, thermal variable-cost units, and retirement decommissioning are held common. No published cost, efficiency, reactor rating, initial state, Table 5 capacity or control threshold is fitted.",
        "- Dashboard min/max envelopes include every hourly sample but are display reductions. Individual selected row plots show the full hourly series.",
        "- Figure diagnostics include TES minima around hours 4350-4550 and late hydrogen events. No author hourly curve RMSE can be computed without author data.",
        "", "## Passing cases among all tested configurations", ""
    ]
    for t in TRACKS:
        f = frame[(frame.track==t) & frame.design_valid]
        lines.append(f"- {t}: {int(f.all_within_5_percent.sum())} pass 5%; {int(f.all_within_10_percent.sum())} pass 10% (all ten rows).")
    (out / "REPORT.md").write_text("\n".join(lines)+"\n")
    entries = []
    for r in rank.to_dict("records"):
        cid = r["case_id"]
        entries.append(f'<tr><td><a href="cases/{cid}/index.html">{cid}</a></td><td>{r["track"]}</td>'
                       f'<td>{r["objective"]:.5g}</td><td>{r["all_worst_relative_percent"]:.3f}%</td>'
                       f'<td>{r["all_rows_within_10_percent"]}/10</td><td>{r["all_within_10_percent"]}</td></tr>')
    (out / "index.html").write_text(
        '<!doctype html><meta charset="utf-8"><title>All ten Table 5 rows, two tracks</title>'
        '<style>body{font:15px system-ui;margin:20px}td,th{border:1px solid #ccc;padding:7px}table{border-collapse:collapse}</style>'
        '<h1>Table 5 comparison: all tested configurations</h1><p>Input-calibrated scenarios are not author data.</p>'
        '<p><a href="REPORT.md">Report</a> | <a href="track_comparison.csv">Track comparison</a> | '
        '<a href="all_metrics.csv">All numerical results</a> | <a href="selected/fixed_inputs/selection.json">Fixed selection</a> | '
        '<a href="selected/input_calibrated/selection.json">Calibrated-input selection</a></p>'
        '<p>Ordered by design validity, then fitting objective. Each case has its own candidate-only plot and all ten rows.</p>'
        '<table><tr><th>Case</th><th>Track</th><th>Fit objective</th><th>Worst error, all rows</th><th>Rows within 10%</th><th>All pass 10%</th></tr>'
        + "".join(entries) + '</table>'
    )


def run(config, output):
    spec = load_config(config)
    out = Path(output).resolve()
    if out.exists():
        raise FileExistsError("Use a new output directory; previous results are never overwritten.")
    root = TEACHER
    hashes = protected_hashes(root)
    model = gs.load_model(root / "NRHES_Model.py")
    params = model.create_params()
    raw = gs.load_data(root / "InputData.xlsx")
    _, wind = gs.renewable_components(model, raw, params)
    out.mkdir(parents=True)
    gs.save_json(out / "config.json", spec)
    preflight(raw, params, wind, spec, out)
    experiment = Experiment(raw, params, wind, spec, out)
    selections = {}
    for track in TRACKS:
        print(f"Starting {track}", flush=True)
        experiment.search(track)
        valid = [r for r in experiment.records if r["track"] == track and r["design_valid"]]
        if not valid:
            raise RuntimeError(f"No design-valid configuration in {track}")
        chosen = min(valid, key=lambda r: (r["objective"], r["case_id"]))
        settings = {k: chosen[k] for k in (*BASE_FIELDS, *INPUT_FIELDS, "track", "pv_interpretation",
                                          "charge_model", "discharge_model", "weather_shift_hours")}
        summary = export_selection(raw, params, wind, spec, settings, out / "selected" / track, hashes)
        selections[track] = {"case_id": chosen["case_id"], "settings": settings, "summary": summary}
        # Save a complete checkpoint after each track.
        pd.DataFrame(experiment.records).to_csv(out / "checkpoint_cases.csv", index=False)
        print(f"Selected {track}: {chosen['case_id']}; worst all-row error={summary['all_worst_relative_percent']:.4f}%", flush=True)
    if protected_hashes(root) != hashes:
        raise AssertionError("An original file changed during the experiment.")
    write_report(experiment, selections, out)
    gs.save_json(out / "manifest.json", {
        "completed_utc": datetime.now(timezone.utc).isoformat(),
        "protected_hashes": hashes, "originals_unchanged": True,
        "configuration_count": len(experiment.records),
        "metric_row_count": len(experiment.rows),
        "dashboard_count": len(list((out/"cases").glob("*/candidate.png"))),
        "config_sha256": gs.sha256(config),
        "implementation_hashes": {p: gs.sha256(HERE / p) for p in ("table5_calibration.py","grid_search.py")},
        "selections": selections
    })
    print(f"Completed {len(experiment.records)} configurations: {out}", flush=True)
    return selections


def rerun_selection(selection, output):
    saved = json.loads(Path(selection).read_text())
    if protected_hashes(TEACHER) != saved["protected_hashes"]:
        raise ValueError("Original model or input hash changed.")
    for name, expected in saved["implementation_hashes"].items():
        if gs.sha256(HERE / name) != expected:
            raise ValueError(f"Implementation changed: {name}")
    out = Path(output).resolve()
    if out.exists():
        raise FileExistsError("Use a new output directory.")
    params = gs.load_model(TEACHER / "NRHES_Model.py").create_params()
    if params != saved["fixed_params"]:
        raise ValueError("Published/common parameters changed.")
    raw = gs.load_data(TEACHER / "InputData.xlsx")
    _, wind = gs.renewable_components(gs.load_model(TEACHER / "NRHES_Model.py"), raw, params)
    return export_selection(raw, params, wind, saved["spec"], saved["settings"], out, saved["protected_hashes"])


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", type=Path, default=HERE / "table5_config.json")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--selection", type=Path, help="Rerun a frozen all-ten-row selection instead of searching.")
    args = p.parse_args()
    if args.selection:
        rerun_selection(args.selection, args.output)
    else:
        run(args.config, args.output)


if __name__ == "__main__":
    main()
