"""Reproducibility search for the 2025 NRHES paper; original files stay untouched.

Run ``python grid_search.py --help``. All parameter hypotheses, paper targets,
and acceptance tolerances live in search_config.json, not in plotting code.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import html
import importlib.metadata
import importlib.util
import itertools
import json
import platform
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
METRICS = ("DPSP", "EWP", "LCOE")


@dataclass(frozen=True)
class Physics:
    charge_mwth: float = 10.0
    discharge_mwth: float = 10.0
    hydrogen_formula: str = "minimum"
    thermal_gate: str = "resource"
    overflow: str = "cap"

    def __post_init__(self):
        if not np.isfinite([self.charge_mwth, self.discharge_mwth]).all() or min(self.charge_mwth, self.discharge_mwth) <= 0:
            raise ValueError("TES power limits must be positive.")
        for value, choices in [
            (self.hydrogen_formula, ("minimum", "addition")),
            (self.thermal_gate, ("legacy", "resource")),
            (self.overflow, ("cap", "shutdown")),
        ]:
            if value not in choices:
                raise ValueError(f"Unsupported physical assumption: {value}")


@dataclass(frozen=True)
class Economics:
    real_rate: float = (0.06 - 0.02) / 1.02
    variable_cost_basis: str = "thermal"
    decommissioning: str = "retirement"

    def __post_init__(self):
        if not np.isfinite(self.real_rate) or self.real_rate < 0:
            raise ValueError("Use a finite, nonnegative real discount rate.")
        if self.variable_cost_basis not in ("thermal", "electric"):
            raise ValueError("Unknown variable cost basis.")
        if self.decommissioning not in ("retirement", "project"):
            raise ValueError("Unknown decommissioning convention.")


@dataclass
class Simulation:
    mode: int
    capacity: dict
    series: dict
    metrics: dict
    audit: dict


def save_json(path, value):
    def convert(obj):
        if isinstance(obj, np.generic):
            return obj.item()
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        if isinstance(obj, Path):
            return str(obj)
        raise TypeError(type(obj).__name__)

    Path(path).write_text(json.dumps(value, indent=2, default=convert, allow_nan=False) + "\n")


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_model(path):
    name = "nrhes_original_" + sha256(path)[:12]
    spec = importlib.util.spec_from_file_location(name, path)
    model = importlib.util.module_from_spec(spec)
    sys.modules[name] = model
    previous = sys.dont_write_bytecode
    try:
        # no __pycache__ next to the teacher's file
        sys.dont_write_bytecode = True
        spec.loader.exec_module(model)
    finally:
        sys.dont_write_bytecode = previous
    return model


def load_data(path):
    data = pd.read_excel(path)
    required = ["hours", "G_Tot", "T_a", "V_anem", "P_grid"]
    if not set(required).issubset(data.columns):
        raise ValueError(f"Input must contain {required}")
    data = data[required].astype(float)
    if len(data) != 8760 or not np.array_equal(data.hours, np.arange(1, 8761)):
        raise ValueError("This annual study requires consecutive hours 1 through 8760.")
    if not np.isfinite(data.to_numpy()).all():
        raise ValueError("Missing/nonfinite hourly inputs are not accepted.")
    if (data[["G_Tot", "V_anem", "P_grid"]] < 0).any().any() or data.P_grid.sum() <= 0:
        raise ValueError("Irradiance, wind and demand must be nonnegative; demand must be positive.")
    return data


def renewable_components(model, data, params):
    # one panel and one turbine, scaled by N_pv / N_wt later
    pv = np.array([
        model.PV_power_output(1, g, t, params)[0]
        for g, t in zip(data.G_Tot, data.T_a)
    ])
    wind = np.array([model.WT_power_output(v, 1, params) for v in data.V_anem])
    return pv, wind


def renewable(capacity, components):
    return capacity["N_pv"] * components[0] + capacity["N_wt"] * components[1]


def design_valid(capacity, mode, re_max):
    valid = (
        40000 <= capacity["N_pv"] <= 80000
        and float(capacity["N_pv"]).is_integer()
        and 250 <= capacity["C_TES"] <= 2000
        and capacity["N_wt"] == 1
    )
    if mode > 1:
        valid &= (
            0.01 * re_max <= capacity["P_SOEC"] <= re_max + 1e-10
            and 0.2 <= capacity["DF"] <= 1
        )
    if mode == 3:
        valid &= 0.2 <= capacity["TES_factor"] <= 1
    return bool(valid)


def finish(mode, capacity, arrays, params, tolerance, audit_scope="separate balances"):
    dt = params["dt"]
    eta = params["smTMSR"]["eta_PB"]
    s = arrays
    demand = float(s["Load"].sum() * dt)
    generated = float((s["RE"].sum() + len(s["RE"]) * params["smTMSR"]["P_e"]) * dt)
    active = s["H2"] > 0
    late = s["Hour"] >= 7000
    metrics = {
        "DPSP": float(s["DPS"].sum() / demand),
        "EWP": float((s["WER"].sum() + eta * s["WEN"].sum()) / generated),
        "DPS_MWh": float(s["DPS"].sum()), "WEN_MWhth": float(s["WEN"].sum()),
        "WER_MWh": float(s["WER"].sum()),
        "H2_tonnes": float(s["H2"].sum() * dt / 1000),
        "first_H2_hour": int(s["Hour"][active][0]) if active.any() else None,
        "last_H2_hour": int(s["Hour"][active][-1]) if active.any() else None,
        "late_H2_active_hours": int((active & late).sum()),
        "annual_load_MWh": demand, "annual_generation_equivalent_MWh": generated,
        "avg_LF": float(s["LF"].mean()),
    }
    balance_keys = [k for k in s if k.endswith("_residual")]
    max_residual = max(float(np.abs(s[k]).max()) for k in balance_keys)
    nonnegative = all(float(s[k].min()) >= -tolerance for k in ("DPS", "WER", "WEN", "H2"))
    storage_ok = bool(
        s["LF"].min() >= params["TES"]["LF_min"] / params["TES"]["LF_max"] - tolerance
        and s["LF"].max() <= 1 + tolerance
    )
    resource_ok = all(
        float(s[k].max()) <= tolerance
        for k in ("H2_e_deficit", "H2_th_deficit", "HTSE_power_violation")
        if k in s
    )
    finite = all(np.isfinite(values).all() for values in s.values())
    design_ok = design_valid(capacity, mode, float(s["RE"].max()))
    audit = {
        "valid": bool(max_residual <= tolerance and nonnegative and storage_ok
                      and resource_ok and finite and design_ok),
        "max_balance_residual_MWh": max_residual, "storage_bounds_pass": storage_ok,
        "hydrogen_resources_pass": resource_ok, "design_bounds_pass": design_ok,
        "finite_pass": bool(finite), "audit_scope": audit_scope,
        "unaccounted_positive_MWh_e": float(np.maximum(s["total_residual"], 0).sum()),
        "unaccounted_negative_MWh_e": float(np.maximum(-s["total_residual"], 0).sum()),
    }
    for k in balance_keys:
        audit["max_" + k] = float(np.abs(s[k]).max())
    return Simulation(mode, copy.deepcopy(capacity), arrays, metrics, audit)


def simulate(data, re, params, capacity, mode, physics, tolerance=1e-7):
    """Explicit power allocation; all residuals use MWh over each time step.

    Addition cases keep the claimed hydrogen quantity, cap physical resource
    withdrawals, and expose the unmet hydrogen resource requirements. They are
    NOT silently repaired into a minimum model and cannot pass validation.
    """
    dt = params["dt"]
    if dt != 1:
        raise ValueError("The provided annual dataset and comparison use one-hour steps.")
    th, eta = params["smTMSR"]["P_th"], params["smTMSR"]["eta_PB"]
    nuc = th * eta
    tes, ht = params["TES"], params["HTSE"]
    c = capacity["C_TES"]
    q_min, q_max = c * tes["LF_min"], c * tes["LF_max"]
    q = q_min
    keys = (
        "Hour RE Load Q LF LF_start H2 DPS WER WEN charge discharge storage_loss "
        "H2_e H2_th H2_e_deficit H2_th_deficit HTSE_power_violation "
        "electric_residual heat_residual storage_residual total_residual"
    ).split()
    s = {k: np.zeros(len(data)) for k in keys}
    for i, (hour, load, power) in enumerate(zip(data.hours, data.P_grid, re)):
        old_q = q
        lf_start = old_q / q_max
        self_loss = old_q * tes["sigma"]
        q_available = old_q - self_loss
        active_mode = 2 if mode == 2 or (mode == 3 and lf_start >= capacity["TES_factor"]) else 1
        h = e_req = h_req = e_draw = h_draw = 0.0
        re_grid = min(power, load)
        nuc_grid_e = min(nuc, max(0.0, load - re_grid))
        if active_mode == 2:
            g1, g2 = capacity["DF"] * load, (1 - capacity["DF"]) * load
            if power > g1 and nuc > g2:
                re_grid, nuc_grid_e = g1, g2
                e_surplus, h_surplus = power - g1, th - g2 / eta
                cap = capacity["P_SOEC"]
                e_min = ht["P_e_min_ratio"] * cap
                e_alloc = min(e_surplus, cap)
                h_alloc = min(h_surplus, cap / eta) if physics.thermal_gate == "legacy" else h_surplus
                allowed = cap > 0 and e_alloc >= e_min
                if physics.overflow == "shutdown" and e_surplus > cap:
                    allowed = False
                if physics.thermal_gate == "legacy" and h_alloc < e_min / eta:
                    allowed = False
                if allowed:
                    h_e, h_th = ht["K_e"] * e_alloc, ht["K_th"] * h_alloc
                    h = min(h_e, h_th) if physics.hydrogen_formula == "minimum" else h_e + h_th
                    if physics.thermal_gate == "resource" and h / ht["K_e"] < e_min:
                        h = 0.0
                    e_req, h_req = h / ht["K_e"], h / ht["K_th"]
                    e_draw, h_draw = min(e_req, e_surplus), min(h_req, h_surplus)
        remaining_heat = max(0.0, th - nuc_grid_e / eta - h_draw)
        need_e = max(0.0, load - re_grid - nuc_grid_e)
        ch = dis = 0.0
        if need_e > 0:
            dis = min(
                need_e / eta, physics.discharge_mwth,
                max(0.0, q_available - q_min) * tes["eta_dis"] / dt,
            )
        else:
            ch = min(
                remaining_heat, physics.charge_mwth,
                max(0.0, q_max - q_available) / (tes["eta_ch"] * dt),
            )
        q = q_available + ch * tes["eta_ch"] * dt - dis / tes["eta_dis"] * dt
        dps = max(0.0, need_e - dis * eta) * dt
        wer = max(0.0, power - re_grid - e_draw) * dt
        wen = max(0.0, remaining_heat - ch) * dt
        loss = self_loss + ch * (1 - tes["eta_ch"]) * dt + dis * (1 / tes["eta_dis"] - 1) * dt
        electric = (power + nuc_grid_e + dis * eta - ((load - dps / dt) + e_req + wer / dt)) * dt
        heat = (th - nuc_grid_e / eta - h_req - ch) * dt - wen
        storage = q - old_q - (ch * tes["eta_ch"] - dis / tes["eta_dis"]) * dt + self_loss
        total = ((power + nuc) * dt - (
            load * dt - dps + wer + eta * wen + e_req * dt
            + eta * h_req * dt + eta * (q - old_q + loss)
        ))
        values = [
            hour, power, load, q, q / q_max, lf_start, h, dps, wer, wen, ch, dis, loss,
            e_req, h_req, max(0.0, e_req - e_draw), max(0.0, h_req - h_draw),
            max(0.0, e_req - capacity["P_SOEC"]) if h else 0.0,
            electric, heat, storage, total,
        ]
        for key, value in zip(keys, values):
            s[key][i] = value
    return finish(mode, capacity, s, params, tolerance)


def legacy_simulate(model, data, re, params, capacity, mode, tolerance):
    """Run the original functions verbatim, including original LCOE."""
    q = params["TES"]["LF_min"] * capacity["C_TES"]
    n = len(data)
    s = {k: np.zeros(n) for k in ("Hour", "RE", "Load", "Q", "LF", "LF_start", "H2", "DPS", "WER", "WEN")}
    for i, (hour, load, power) in enumerate(zip(data.hours, data.P_grid, re)):
        s["LF_start"][i] = q / capacity["C_TES"]
        args = (power, params["smTMSR"]["P_e"], load, q, capacity["C_TES"])
        if mode == 1:
            output = model.Mode1(*args, params)
        else:
            extra = (capacity["P_SOEC"], float(re.max()), capacity["DF"])
            output = model.Mode2(*args, *extra, params) if mode == 2 else model.Mode3(
                *args, *extra, capacity["TES_factor"], params
            )
        dps, wer, wen, h, lf, q = output
        for key, value in zip(s, (hour, power, load, q, lf, s["LF_start"][i], h, dps, wer, wen)):
            s[key][i] = value
    dt, eta = params["dt"], params["smTMSR"]["eta_PB"]
    dq = np.diff(np.r_[params["TES"]["LF_min"] * capacity["C_TES"], s["Q"]])
    s["total_residual"] = (
        (re + params["smTMSR"]["P_e"]) * dt
        - (s["Load"] * dt - s["DPS"] + s["WER"] + eta * s["WEN"]
           + eta * dq + s["H2"] * dt / params["HTSE"]["K_e"]
           + eta * s["H2"] * dt / params["HTSE"]["K_th"])
    )
    result = finish(mode, capacity, s, params, tolerance, "combined balance only; original dispatch unchanged")
    result.metrics["LCOE"] = model.calculate_LCOE(
        capacity, params, result.metrics["H2_tonnes"] * 1000,
        result.metrics["DPS_MWh"], float(data.P_grid.sum()), mode,
    )
    return result


def crf(rate, years):
    return 1 / years if rate == 0 else rate * (1 + rate) ** years / ((1 + rate) ** years - 1)


def component_npc(capital, annual_om, lifetime, years, rate):
    # No salvage credit. Replacement is purchased only for service before year n.
    return (
        capital + sum(annual_om / (1 + rate) ** y for y in range(1, years + 1))
        + sum(capital / (1 + rate) ** y for y in range(lifetime, years, lifetime))
    )


def economic_metrics(sim, params, case, years=20):
    e, c, r = params["econ"], sim.capacity, case.real_rate
    if years <= 0 or years > e["smTMSR_lifetime"]:
        raise ValueError("Project years must be positive and not exceed the reactor lifetime.")
    pv_kw, wt_kw, tes_kwh = c["N_pv"] * params["PV"]["P_rated"] / 1000, c["N_wt"] * params["WT"]["P_rated"] * 1000, c["C_TES"] * 1000
    costs = {}
    for label, size, prefix in [("PV", pv_kw, "PV"), ("WT", wt_kw, "WT"), ("TES", tes_kwh, "TES")]:
        costs[label] = component_npc(size * e[prefix + "_inv_cost"], size * e[prefix + "_OM_cost"], e[prefix + "_lifetime"], years, r)
    if sim.mode > 1:
        size = c["P_SOEC"] * 1000
        costs["HTSE"] = component_npc(size * e["HTSE_inv_cost"], size * e["HTSE_OM_fixed"], e["HTSE_lifetime"], years, r)
    else:
        costs["HTSE"] = 0.0
    daily = sim.series["H2"].reshape(-1, 24).sum(axis=1) * params["dt"]
    tank = float(daily.max()) if sim.mode > 1 else 0.0
    costs["tank"] = component_npc(tank * e["H2_tank_inv"], tank * e["H2_tank_OM"], e["H2_tank_lifetime"], years, r)
    kw = params["smTMSR"]["P_e"] * 1000
    output = params["smTMSR"]["P_th" if case.variable_cost_basis == "thermal" else "P_e"]
    om = kw * e["smTMSR_FOM"] + output * params["hours_per_year"] * (e["smTMSR_VOM"] + e["smTMSR_fuel"])
    costs["reactor"] = kw * e["smTMSR_OCC"] + sum(om / (1 + r) ** y for y in range(1, years + 1))
    if case.decommissioning == "retirement" or e["smTMSR_lifetime"] <= years:
        costs["reactor"] += kw * e["smTMSR_decom"] / (1 + r) ** e["smTMSR_lifetime"]
    revenue = sim.metrics["H2_tonnes"] * 1000 * (e["H2_price"] - e["H2_transport"])
    delivered = (sim.metrics["annual_load_MWh"] - sim.metrics["DPS_MWh"]) * 1000
    if delivered <= 0:
        raise ValueError("LCOE undefined when no electricity is delivered.")
    return {
        "LCOE": (sum(costs.values()) * crf(r, years) - revenue) / delivered,
        "TNPC_USD": sum(costs.values()), "tank_kg": tank, "annual_H2_revenue_USD": revenue,
        **{"NPC_" + key: value for key, value in costs.items()},
    }


def target_errors(metrics, target, tolerances):
    result = {}
    for key in METRICS:
        tol = max(tolerances[key]["absolute"], abs(target[key]) * tolerances[key]["relative"])
        difference = metrics[key] - target[key]
        result.update({
            key + "_target": target[key], key + "_difference": difference,
            key + "_relative_error_pct": 100 * difference / target[key],
            key + "_z": difference / tol,
        })
    return result


def score_rows(rows):
    z = np.array([[row[k + "_z"] for k in METRICS] for row in rows])
    return float(np.mean(z * z)), bool((np.abs(z) <= 1).all())


def plot_features(simulations, definitions):
    rows = []
    for feature in definitions:
        sim = next(s for s in simulations if s.mode == feature["mode"])
        mask = (sim.series["Hour"] >= feature["start"]) & (sim.series["Hour"] <= feature["end"])
        actual = float(sim.series[feature["series"]][mask].min())
        z = (actual - feature["target"]) / feature["tolerance"]
        rows.append({**feature, "actual": actual, "z": z, "within_tolerance": abs(z) <= 1})
    return rows


def reference_point_errors(simulations, reference):
    """Optional sparse digitized points, not invented hourly author data."""
    if reference is None:
        return []
    rows = []
    by_mode = {s.mode: s for s in simulations}
    for point in reference.to_dict("records"):
        sim = by_mode[int(point["mode"])]
        series = point["series"]
        actual = float(np.interp(point["hour"], sim.series["Hour"], sim.series[series]))
        rows.append({**point, "actual": actual, "z": (actual - point["value"]) / point["uncertainty"]})
    return rows


def plot_mode(sim, path, title, other=None, reference=None):
    fields = [("LF", "TES load factor"), ("DPS", "Shortage (MWh)"), ("WEN", "Nuclear waste (MWhth)")]
    if sim.mode > 1:
        fields = [("H2", "Hydrogen (kg/h)")] + fields + [("WER", "RE waste (MWh)")]
    fig, axes = plt.subplots(len(fields), 1, figsize=(12, 2.05 * len(fields)), sharex=True)
    for ax, (key, label) in zip(axes, fields):
        ax.plot(sim.series["Hour"], sim.series[key], lw=0.65, label="Candidate")
        if other is not None:
            ax.plot(other.series["Hour"], other.series[key], lw=0.6, alpha=0.65, label="Original code")
        if reference is not None:
            points = reference[(reference["mode"] == sim.mode) & (reference["series"] == key)]
            if len(points):
                ax.errorbar(points.hour, points.value, yerr=points.uncertainty, fmt=".", label="Digitized paper")
        if key == "LF":
            ax.set_ylim(0, 1.05)
            if sim.mode == 3:
                ax.axhline(sim.capacity["TES_factor"], ls="--", color="tab:red", lw=0.7)
        else:
            ax.axhline(0, color="0.6", lw=0.4)
            ax.margins(y=0.07)
        ax.set_ylabel(label)
        ax.grid(alpha=0.25)
        ax.set_xlim(1, 8760)
    if other is not None or reference is not None:
        axes[0].legend(loc="upper right", fontsize=8)
    axes[-1].set_xlabel("Hour (all 8,760 values, including zeros)")
    fig.suptitle(title, fontsize=10)
    fig.tight_layout()
    fig.savefig(path, dpi=100)
    plt.close(fig)


def plot_economics(rows, original, targets, path, title):
    fig, axes = plt.subplots(3, 1, figsize=(12, 8), sharex=True)
    ids = list(dict.fromkeys(row["economic_id"] for row in rows))
    for mode, ax in enumerate(axes, 1):
        values = [r["LCOE"] for r in rows if r["mode"] == mode]
        ax.bar(np.arange(len(ids)), values)
        ax.axhline(targets[mode - 1]["target"]["LCOE"], color="tab:red", ls="--", label="Paper")
        ax.axhline(original[mode - 1].metrics["LCOE"], color="0.35", ls=":", label="Original code")
        ax.set_ylabel(f"Mode {mode}\n$/kWh")
        ax.grid(axis="y", alpha=0.25)
    axes[0].legend(fontsize=8)
    axes[-1].set_xticks(np.arange(len(ids)), ids, rotation=45)
    fig.suptitle(title + " (economic IDs are defined in economic_cases.csv)", fontsize=10)
    fig.tight_layout()
    fig.savefig(path, dpi=100)
    plt.close(fig)


def metric_comparison_plot(original, best_rows, targets, path):
    fig, axes = plt.subplots(1, 3, figsize=(13, 4))
    x = np.arange(3)
    for ax, key in zip(axes, METRICS):
        scale = 100 if key != "LCOE" else 1
        for offset, values, label in [
            (-0.25, [r["target"][key] for r in targets], "Paper"),
            (0, [s.metrics[key] for s in original], "Original code"),
            (0.25, [r[key] for r in best_rows], "Best valid candidate"),
        ]:
            ax.bar(x + offset, np.array(values) * scale, width=0.25, label=label)
        ax.set_xticks(x, ["Mode 1", "Mode 2", "Mode 3"])
        ax.set_title(key + (" (%)" if key != "LCOE" else " ($/kWh)"))
        ax.grid(axis="y", alpha=0.25)
    axes[0].legend(fontsize=7)
    fig.suptitle("Lower distance is not evidence of correct physics; validation is separate")
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)


def write_gallery(out, manifests, best_id, plots_enabled):
    rows = []
    for item in sorted(manifests, key=lambda x: (not x["valid"], x["metric_score"])):
        links = []
        for label, key in [("Mode 1", "mode1_plot"), ("Mode 2", "mode2_plot"),
                           ("Mode 3", "mode3_plot"), ("Economics", "economic_plot")]:
            link = item[key]
            links.append(f'<a href="{html.escape(link, quote=True)}">{label}</a>' if link else "not generated")
        assumptions = ", ".join(str(item[k]) for k in
                                ("charge_mwth", "discharge_mwth", "hydrogen_formula", "thermal_gate", "overflow", "real_rate"))
        rows.append(
            f"<tr><td>{item['case_id']}</td><td>{'valid' if item['valid'] else 'INVALID diagnostic'}</td>"
            f"<td>{item['metric_score']:.4f}</td><td>{html.escape(assumptions)}</td>"
            + "".join(f"<td>{link}</td>" for link in links) + "</tr>"
        )
    comparison = '<p><img src="metrics_comparison.png" alt="Paper, original and best valid metrics" style="max-width:100%"></p>' if plots_enabled else ""
    body = (
        '<!doctype html><html lang="en"><meta charset="utf-8"><title>NRHES search results</title>'
        '<style>body{font:15px system-ui;margin:24px}table{border-collapse:collapse;width:100%}'
        'td,th{border:1px solid #bbb;padding:6px;text-align:left}th{position:sticky;top:0;background:#eee}</style>'
        f'<h1>NRHES grid search</h1><p>Best physically valid candidate: <strong>{best_id}</strong>. '
        'This label does not imply reproduction of the paper; read the report.</p>'
        '<p><a href="REPORT.md">Report</a> | <a href="summary.json">Summary</a> | '
        '<a href="improvement_vs_original.csv">Improvement CSV</a> | <a href="case_manifest.csv">Manifest</a></p>'
        + comparison
        + '<p>Use browser search to find a case ID. Charging/discharging limits, formula, gate, overflow and real rate appear in that order. Economic cases share hourly plots.</p>'
        '<table><thead><tr><th>Case</th><th>Physics</th><th>Score</th><th>Assumptions</th>'
        '<th>Mode 1</th><th>Mode 2</th><th>Mode 3</th><th>Economics</th></tr></thead><tbody>'
        + "".join(rows) + "</tbody></table></html>"
    )
    (out / "index.html").write_text(body)


def energy_lower_bound(data, re, params, capacity):
    """Mode 1, lossless TES, no hydrogen: an analytical consistency check."""
    if any(params["TES"][k] != expected for k, expected in [("sigma", 0), ("eta_ch", 1), ("eta_dis", 1)]):
        return {"applicable": False}
    eta, dt = params["smTMSR"]["eta_PB"], params["dt"]
    production = float((re.sum() + len(re) * params["smTMSR"]["P_e"]) * dt)
    load = float(data.P_grid.sum() * dt)
    maximum_net_store = eta * capacity["C_TES"] * (params["TES"]["LF_max"] - params["TES"]["LF_min"])
    lower = max(0.0, production - load - maximum_net_store)
    return {
        "applicable": True, "generation_MWh_e_equivalent": production,
        "load_MWh": load, "maximum_net_storage_MWh_e_equivalent": maximum_net_store,
        "minimum_possible_waste_MWh_e_equivalent": lower, "minimum_possible_EWP": lower / production,
        "assumptions": "Current inputs and generation equations; constant reactor; lossless TES; no H2; available-energy EWP denominator. Not a claim about alternative paper definitions.",
    }


def decode_genome(x, mode, components):
    c = {"N_pv": int(round(x[0])), "C_TES": float(x[1]), "N_wt": 1, "P_SOEC": 0.0, "DF": 0.0, "TES_factor": 0.0}
    if mode > 1:
        # SOEC bound depends on peak RE, so x[2] is a fraction
        c["P_SOEC"] = float(x[2]) * float(renewable(c, components).max())
        c["DF"] = float(x[3])
    if mode == 3:
        c["TES_factor"] = float(x[4])
    return c


def optimize(data, components, params, physics, economics, settings, args, original, out):
    """Exploratory NSGA-II, not reproduction of the paper's selected MOPSO."""
    from pymoo.algorithms.moo.nsga2 import NSGA2
    from pymoo.core.problem import ElementwiseProblem
    from pymoo.operators.crossover.sbx import SBX
    from pymoo.operators.mutation.pm import PM
    from pymoo.optimize import minimize

    root = out / "optimization"
    root.mkdir()
    history, fronts = [], []
    population = args.ga_population or settings["optimization"]["population"]
    generations = args.ga_generations or settings["optimization"]["generations"]
    seeds = args.ga_seeds if args.ga_seeds is not None else settings["optimization"]["seeds"]
    for mode in (1, 2, 3):
        size = {1: 2, 2: 4, 3: 5}[mode]
        lower = np.array([40000, 250, 0.01, 0.2, 0.2][:size])
        upper = np.array([80000, 2000, 1, 1, 1][:size])
        for seed in seeds:
            print(f"NSGA-II mode={mode}, seed={seed}, population={population}, generations={generations}", flush=True)

            class Problem(ElementwiseProblem):
                def __init__(self):
                    super().__init__(n_var=size, n_obj=3, n_ieq_constr=1, xl=lower, xu=upper)

                def _evaluate(self, x, values, *unused_args, **unused_kwargs):
                    c = decode_genome(x, mode, components)
                    sim = simulate(data, renewable(c, components), params, c, mode, physics, settings["balance_tolerance_mwh"])
                    sim.metrics.update(economic_metrics(sim, params, economics, settings["project_years"]))
                    values["F"] = [sim.metrics[k] for k in METRICS]
                    values["G"] = [0.0 if sim.audit["valid"] else 1.0]
                    history.append({"mode": mode, "seed": seed, "evaluation": len(history) + 1, **c, **sim.metrics, "valid": sim.audit["valid"]})

            algorithm = NSGA2(
                pop_size=population, eliminate_duplicates=True,
                crossover=SBX(prob=0.6, eta=20), mutation=PM(prob=1.0, prob_var=1 / size, eta=20),
            )
            result = minimize(Problem(), algorithm, ("n_gen", generations), seed=int(seed), verbose=False)
            folder = root / f"mode{mode}_seed{seed}"
            folder.mkdir()
            if result.X is None:
                continue
            for i, x in enumerate(np.atleast_2d(result.X), 1):
                c = decode_genome(x, mode, components)
                sim = simulate(data, renewable(c, components), params, c, mode, physics, settings["balance_tolerance_mwh"])
                sim.metrics.update(economic_metrics(sim, params, economics, settings["project_years"]))
                file = folder / f"front_{i:03d}.png"
                if not args.no_plots:
                    plot_mode(sim, file, f"Exploratory NSGA-II Mode {mode}; seed {seed}; non-dominated solution {i}", original[mode - 1])
                fronts.append({"mode": mode, "seed": seed, "solution": i, **c, **sim.metrics,
                               "valid": sim.audit["valid"], "plot": str(file.relative_to(out)) if not args.no_plots else ""})
    pd.DataFrame(history).to_csv(root / "evaluations.csv", index=False)
    pd.DataFrame(fronts).to_csv(root / "pareto_solutions.csv", index=False)
    selected = []
    for mode in (1, 2, 3):
        eligible = [r for r in fronts if r["mode"] == mode and r["valid"] and r["DPSP"] < 0.01 and (mode != 3 or r["LCOE"] <= 0.094)]
        if eligible:
            row = min(eligible, key=lambda r: r["EWP"])
            target = settings["training"][mode - 1]["target"]
            selected.append({**row, **target_errors(row, target, settings["tolerances"])})
    pd.DataFrame(selected).to_csv(root / "selected_vs_paper.csv", index=False)
    if fronts and not args.no_plots:
        frame = pd.DataFrame(fronts)
        fig, axes = plt.subplots(1, 3, figsize=(14, 4), constrained_layout=True)
        norm = matplotlib.colors.Normalize(frame.LCOE.min(), frame.LCOE.max())
        for mode, ax in enumerate(axes, 1):
            points = frame[frame["mode"] == mode]
            scatter = ax.scatter(points.DPSP * 100, points.EWP * 100, c=points.LCOE, norm=norm, s=16)
            target = settings["training"][mode - 1]["target"]
            ax.scatter(target["DPSP"] * 100, target["EWP"] * 100, marker="*", s=100, color="tab:red", label="Paper Table 5")
            ax.scatter(original[mode - 1].metrics["DPSP"] * 100, original[mode - 1].metrics["EWP"] * 100,
                       marker="x", s=40, color="black", label="Original fixed capacities")
            ax.set(title=f"Mode {mode}", xlabel="DPSP (%)", ylabel="EWP (%)")
            ax.grid(alpha=0.25)
        axes[0].legend(fontsize=7)
        fig.colorbar(scatter, ax=axes.tolist(), label="Candidate LCOE ($/kWh)")
        fig.suptitle("Exploratory NSGA-II front projections; color encodes the third objective")
        fig.savefig(root / "pareto_comparison.png", dpi=140)
        plt.close(fig)
    return {"status": "exploratory_completed", "evaluations": len(history), "front_solutions": len(fronts),
            "selected_modes": [r["mode"] for r in selected], "population": population, "generations": generations, "seeds": seeds,
            "warning": "Limited-budget NSGA-II, not demonstrated convergence and not the paper's MOPSO. Front plots cover every returned solution; transient evaluations are in CSV."}


def run(args):
    settings = json.loads(args.config.read_text())
    if not args.no_plots and args.output.exists():
        raise FileExistsError(f"Refusing to overwrite an existing run: {args.output}")
    args.output.mkdir(parents=True, exist_ok=False)
    out = args.output
    source_hash, input_hash = sha256(args.model), sha256(args.input)
    search_hash, config_hash = sha256(Path(__file__)), sha256(args.config)
    matlab_path = args.model.with_suffix(".m")
    matlab_hash = sha256(matlab_path) if matlab_path.exists() else None
    model = load_model(args.model)
    params = model.create_params()
    data = load_data(args.input)
    components = renewable_components(model, data, params)
    tolerance = settings["balance_tolerance_mwh"]
    reference = pd.read_csv(args.reference_points) if args.reference_points else None
    if reference is not None:
        if not {"mode", "series", "hour", "value", "uncertainty"}.issubset(reference.columns):
            raise ValueError("Reference points need mode,series,hour,value,uncertainty.")
        if not np.isfinite(reference[["mode", "hour", "value", "uncertainty"]]).all().all() or (reference.uncertainty <= 0).any():
            raise ValueError("Reference points must be finite with positive uncertainty.")
        allowed_series = {"LF", "DPS", "WER", "WEN", "H2"}
        if not set(reference["series"]).issubset(allowed_series) or not reference["mode"].isin([1, 2, 3]).all() or not reference.hour.between(1, 8760).all():
            raise ValueError("Reference series/modes/hours are outside this comparison.")
    save_json(out / "run_config.json", settings)
    original, original_rows = [], []
    for item in settings["training"]:
        mode, c = item["mode"], item["config"]
        original_capacity = getattr(model, f"CFG{mode}")
        if original_capacity != c:
            raise ValueError("Training capacities must match current CFG1/2/3 for a fair original comparison.")
        sim = legacy_simulate(model, data, renewable(c, components), params, c, mode, tolerance)
        original.append(sim)
        original_rows.append({"mode": mode, **sim.metrics, **sim.audit, **target_errors(sim.metrics, item["target"], settings["tolerances"])})
        if not args.no_plots:
            plot_mode(sim, out / f"original_mode{mode}.png", f"Unchanged original implementation, Mode {mode}")
    original_score, original_match = score_rows(original_rows)
    pd.DataFrame(original_rows).to_csv(out / "original_results.csv", index=False)
    bounds = energy_lower_bound(data, renewable(settings["training"][0]["config"], components), params, settings["training"][0]["config"])
    bounds["paper_Mode1_EWP"] = settings["training"][0]["target"]["EWP"]
    save_json(out / "mode1_energy_lower_bound.json", bounds)
    pg, eg = settings["physical_grid"], settings["economic_grid"]
    physics_cases = [Physics(**dict(zip(pg, values))) for values in itertools.product(*pg.values())]
    economics_cases = [Economics(**dict(zip(eg, values))) for values in itertools.product(*eg.values())]
    pd.DataFrame([{"economic_id": f"E{i:03d}", **asdict(e)} for i, e in enumerate(economics_cases, 1)]).to_csv(out / "economic_cases.csv", index=False)
    physical, results, ranking, manifests, feature_rows = {}, [], [], [], []
    for pi, physics in enumerate(physics_cases, 1):
        pid = f"P{pi:03d}"
        print(f"Physical case {pi}/{len(physics_cases)}: {asdict(physics)}", flush=True)
        folder = out / pid
        folder.mkdir()
        simulations = []
        for item in settings["training"]:
            mode, c = item["mode"], item["config"]
            sim = simulate(data, renewable(c, components), params, c, mode, physics, tolerance)
            simulations.append(sim)
            np.savez_compressed(folder / f"mode{mode}_hourly.npz", **sim.series)
            if not args.no_plots:
                label = "VALID" if sim.audit["valid"] else "INVALID: diagnostic only"
                plot_mode(sim, folder / f"mode{mode}.png", f"{pid} Mode {mode}; {label}\n{asdict(physics)}", reference=reference)
        features = plot_features(simulations, settings["plot_features"])
        feature_rows.extend({"physical_id": pid, **r} for r in features)
        point_rows = reference_point_errors(simulations, reference)
        if point_rows:
            pd.DataFrame(point_rows).to_csv(folder / "digitized_point_errors.csv", index=False)
        feature_match = all(r["within_tolerance"] for r in features)
        # These qualitative author statements are not invented hourly targets.
        qualitative_match = simulations[0].metrics["WER_MWh"] <= 1.0 and simulations[2].metrics["late_H2_active_hours"] > 0
        valid = all(s.audit["valid"] for s in simulations)
        physical[pid] = (physics, simulations)
        economic_plot_rows = []
        for ei, economics in enumerate(economics_cases, 1):
            eid, cid = f"E{ei:03d}", f"{pid}_E{ei:03d}"
            rows = []
            for sim, item in zip(simulations, settings["training"]):
                metrics = {**sim.metrics, **economic_metrics(sim, params, economics, settings["project_years"])}
                row = {"case_id": cid, "physical_id": pid, "economic_id": eid, "mode": sim.mode,
                       **asdict(physics), **asdict(economics), **metrics, **sim.audit,
                       **target_errors(metrics, item["target"], settings["tolerances"])}
                rows.append(row)
            score, match = score_rows(rows)
            results.extend(rows)
            economic_plot_rows.extend(rows)
            summary = {"case_id": cid, "physical_id": pid, "economic_id": eid,
                       **asdict(physics), **asdict(economics), "valid": valid,
                       "metric_score": score, "numerical_targets_pass": match,
                       "approximate_plot_features_pass": feature_match, "qualitative_plot_checks_pass": qualitative_match,
                       "accepted_baseline": valid and match and feature_match and qualitative_match}
            ranking.append(summary)
            manifests.append({**summary,
                              **{f"mode{m}_plot": f"{pid}/mode{m}.png" if not args.no_plots else "" for m in (1, 2, 3)},
                              "economic_plot": f"{pid}/economics.png" if not args.no_plots else "",
                              "hourly_files": ";".join(f"{pid}/mode{m}_hourly.npz" for m in (1, 2, 3))})
        if not args.no_plots:
            plot_economics(economic_plot_rows, original, settings["training"], folder / "economics.png", f"{pid}: all economic configurations")
    frame = pd.DataFrame(results)
    ranks = pd.DataFrame(ranking).sort_values(["valid", "metric_score"], ascending=[False, True])
    frame.to_csv(out / "all_results.csv", index=False)
    ranks.to_csv(out / "ranking.csv", index=False)
    pd.DataFrame(manifests).to_csv(out / "case_manifest.csv", index=False)
    pd.DataFrame(feature_rows).to_csv(out / "plot_feature_errors.csv", index=False)
    valid_ranks = ranks[ranks.valid]
    if valid_ranks.empty:
        raise RuntimeError("No physically valid candidate. Inspect diagnostics; no baseline selected.")
    accepted = valid_ranks[valid_ranks.accepted_baseline]
    best = (accepted if len(accepted) else valid_ranks).iloc[0].to_dict()
    best_physics, best_sims = physical[best["physical_id"]]
    best_economics = economics_cases[int(best["economic_id"][1:]) - 1]
    best_rows = frame[frame.case_id == best["case_id"]].sort_values("mode").to_dict("records")
    improvement = []
    for old, new, item in zip(original_rows, best_rows, settings["training"]):
        for key in METRICS:
            before, after = abs(old[key] - item["target"][key]), abs(new[key] - item["target"][key])
            improvement.append({"mode": item["mode"], "metric": key, "paper": item["target"][key],
                                "original": old[key], "best_valid": new[key], "original_absolute_error": before,
                                "best_absolute_error": after, "error_reduction_percent": 100 * (before - after) / before if before else None})
    pd.DataFrame(improvement).to_csv(out / "improvement_vs_original.csv", index=False)
    heldout = []
    for item in settings["holdout"]:
        c, mode = item["config"], item["mode"]
        sim = simulate(data, renewable(c, components), params, c, mode, best_physics, tolerance)
        metrics = {**sim.metrics, **economic_metrics(sim, params, best_economics, settings["project_years"])}
        heldout.append({"mode": mode, **c, **metrics, **sim.audit, **target_errors(metrics, item["target"], settings["tolerances"])})
        if not args.no_plots:
            plot_mode(sim, out / f"holdout_mode{mode}.png", f"Held-out Table 5 configuration: Mode {mode}; not used for selection")
    pd.DataFrame(heldout).to_csv(out / "holdout_results.csv", index=False)
    if not args.no_plots:
        metric_comparison_plot(original, best_rows, settings["training"], out / "metrics_comparison.png")
        for sim, old in zip(best_sims, original):
            plot_mode(sim, out / f"best_vs_original_mode{sim.mode}.png",
                      f"Best valid candidate {best['case_id']} versus original code; Mode {sim.mode}", old, reference)
    optimization = {"status": "not_requested"}
    if args.optimize:
        if best["accepted_baseline"] or args.allow_unmatched_optimization:
            optimization = optimize(data, components, params, best_physics, best_economics, settings, args, original, out)
            optimization["baseline_matched_paper"] = bool(best["accepted_baseline"])
        else:
            optimization = {"status": "skipped_unmatched_baseline", "warning": "Use --allow-unmatched-optimization only for explicitly exploratory optimization."}
    if sha256(args.model) != source_hash or sha256(args.input) != input_hash:
        raise RuntimeError("Original model or input changed during the run.")
    if matlab_hash is not None and sha256(matlab_path) != matlab_hash:
        raise RuntimeError("Original MATLAB file changed during the run.")
    if sha256(Path(__file__)) != search_hash or sha256(args.config) != config_hash:
        raise RuntimeError("Search script or configuration changed during execution.")
    versions = {}
    for package in ("numpy", "pandas", "matplotlib", "openpyxl", "pymoo"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = "not installed"
    summary = {
        "created_utc": datetime.now(timezone.utc).isoformat(), "python": platform.python_version(), "versions": versions,
        "original_model_sha256": source_hash, "input_sha256": input_hash,
        "original_matlab_sha256": matlab_hash, "search_script_sha256": search_hash,
        "search_config_sha256": config_hash,
        "physical_cases": len(physics_cases), "economic_cases": len(economics_cases), "combined_cases": len(ranks),
        "valid_combined_cases": len(valid_ranks), "accepted_baselines": len(accepted),
        "original_metric_score": original_score, "original_numerical_targets_pass": original_match,
        "original_physics_pass": all(s.audit["valid"] for s in original), "best_valid_candidate": best,
        "score_reduction_percent": 100 * (original_score - best["metric_score"]) / original_score if original_score else None,
        "heldout_metric_score": score_rows(heldout)[0], "heldout_numerical_targets_pass": score_rows(heldout)[1],
        "energy_lower_bound": bounds, "optimization": optimization,
        "paper_hourly_RMSE": None,
        "limitations": ["No original author hourly series; no image-pixel fitting or claimed hourly paper RMSE.",
                        "Model assumptions and capacities are not tuned together during baseline identification.",
                        "Negative error reduction means a worse numerical match, not an improvement.",
                        "Addition diagnostics may look closer but cannot be accepted if energy resources fail.",
                        "Plot-feature thresholds and metric tolerances are analyst-selected, not author guarantees.",
                        "Shared hourly plots are intentional: economic variants have identical dispatch.",
                        "No salvage credit; decommissioning convention is an explicit unresolved economic hypothesis."],
    }
    save_json(out / "summary.json", summary)
    save_json(out / "best_valid_candidate.json", {"physics": asdict(best_physics), "economics": asdict(best_economics), "selection": best})
    lines = [
        "# Reproducibility search report", "",
        f"Tested {len(physics_cases)} physical x {len(economics_cases)} economic = {len(ranks)} combined cases.",
        f"Physically valid: {len(valid_ranks)}. Accepted reproductions: {len(accepted)}.",
        f"Best valid candidate: **{best['case_id']}**. This is " + ("an accepted baseline." if best["accepted_baseline"] else "**not a validated reproduction of the paper**."),
        "", f"Original tolerance-normalized squared-error score: {original_score:.6g}.",
        f"Best valid score: {best['metric_score']:.6g}. Change: {summary['score_reduction_percent']:.2f}% error reduction (negative = worse).",
        "", "|Mode|Metric|Paper|Original|Best valid|Error reduction %|", "|---|---|---:|---:|---:|---:|",
    ]
    for row in improvement:
        change = row["error_reduction_percent"]
        lines.append(f"|{row['mode']}|{row['metric']}|{row['paper']:.6g}|{row['original']:.6g}|{row['best_valid']:.6g}|{change:.2f}|" if change is not None else f"|{row['mode']}|{row['metric']}|{row['paper']:.6g}|{row['original']:.6g}|{row['best_valid']:.6g}|n/a|")
    if bounds["applicable"]:
        lines += ["", f"**Analytical check:** with current inputs, constant reactor and the current available-energy EWP definition, Mode 1 EWP cannot be below {bounds['minimum_possible_EWP']:.4%}, even allowing maximum final storage. Paper target: {bounds['paper_Mode1_EWP']:.4%}.",
                  "Thus matching that target requires resolving inputs, generation/metric definitions or other assumptions; economic tuning cannot fix it."]
    lines += ["", f"Optimization: `{optimization['status']}`.", "",
              "## Files", "- `index.html`: local gallery linking every tested configuration.",
              "- `case_manifest.csv`: every combined case and its shared plots/hourly files.",
              "- `all_results.csv`, `ranking.csv`: full numerical scores and validity.",
              "- `improvement_vs_original.csv`: signed improvement for each of nine targets.",
              "- `holdout_results.csv`: additional published capacities, not used for selection.",
              "- `plot_feature_errors.csv`: approximate paper TES-dip comparisons.",
              "- `P*/mode*.png`: every physical configuration; `P*/economics.png`: every economic case.",
              "- `metrics_comparison.png`, `best_vs_original_mode*.png`: end comparisons.",
              "- `summary.json`: hashes, environment, assumptions, limitations and optimization status.",
              "", "Original Python and Excel SHA-256 hashes verified unchanged after execution.",
              "No original paper hourly data were supplied. Approximate plot features are not exact waveform validation."]
    (out / "REPORT.md").write_text("\n".join(lines) + "\n")
    write_gallery(out, manifests, best["case_id"], not args.no_plots)
    print(json.dumps({"output": str(out), "best_valid": best["case_id"], "accepted": len(accepted),
                      "original_score": original_score, "best_valid_score": best["metric_score"],
                      "score_reduction_percent": summary["score_reduction_percent"], "optimization": optimization}, indent=2), flush=True)
    return summary


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=HERE / "search_config.json")
    parser.add_argument("--model", type=Path, default=HERE.parent / "teacher_implementation" / "NRHES_Model.py")
    parser.add_argument("--input", type=Path, default=HERE.parent / "teacher_implementation" / "InputData.xlsx")
    parser.add_argument("--output", type=Path, default=HERE / "results" / datetime.now().strftime("run_%Y%m%d_%H%M%S"))
    parser.add_argument("--reference-points", type=Path, help="Optional digitized CSV: mode,series,hour,value,uncertainty")
    parser.add_argument("--no-plots", action="store_true", help="Tests/headless numerical runs only; default saves all grid plots.")
    parser.add_argument("--optimize", action="store_true", help="Run separate exploratory NSGA-II after model selection.")
    parser.add_argument("--allow-unmatched-optimization", action="store_true", help="Explicitly allow exploratory optimization when no baseline matches.")
    parser.add_argument("--ga-population", type=int)
    parser.add_argument("--ga-generations", type=int)
    parser.add_argument("--ga-seeds", nargs="+", type=int)
    args = parser.parse_args(argv)
    if (args.ga_population is not None and args.ga_population < 4) or (args.ga_generations is not None and args.ga_generations < 2):
        parser.error("Use population >=4 and generations >=2.")
    return args


if __name__ == "__main__":
    run(parse_args())
