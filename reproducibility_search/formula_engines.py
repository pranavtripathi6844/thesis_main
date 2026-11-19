"""Two engines for the teacher designs 1a/2a/3d, each with two hydrogen formulas.

* ``teacher_script``: the professor's NRHES_Model.py executed from its original
  source text, with only the small switch edits listed in ``PATCHES``. With the
  professor's settings and the minimum formula it reproduces the original run
  bit-for-bit (checked by tests and by every search run).
* ``our_model``: the conservation-checked dispatch of paper_reproduction.py. Its
  minimum formula is that module's kernel unchanged; the addition formula is
  Eq. 9 of the paper, H = K_th*P_th + K_e*P_e, with the injected electricity and
  heat actually consumed so the energy balance still closes.

Neither engine changes Excel values, designs, efficiencies or unit prices.
"""
from __future__ import annotations

from dataclasses import asdict

import numpy as np
from numba import njit

import paper_reproduction as pr

TEACHER_FILE = pr.ROOT / "NRHES_Model.py"
ENGINES = ("teacher_script", "our_model")
FORMULAS = ("minimum", "addition")
KE, KTH = pr.KE, pr.KTH

# The professor's own settings, expressed in the grid's vocabulary.
TEACHER_PHYSICS = pr.Physics(tes_charge_mwth=10, tes_discharge_mwth=10, tes_rating="absolute",
                             pv_model="area_efficiency", reactor_2025_mwth=25,
                             inactive_soec="keep_split", soc_switch="start")
TEACHER_FINANCE = pr.Finance()

# (purpose, exact original text, replacement, required number of occurrences).
# Every switch defaults to the original behaviour when its parameter is absent.
PATCHES = (
    ("TES charge limit (overflow update)",
     "    P_max  = p['TES']['P_max']\n",
     "    P_max  = p['TES'].get('P_max_ch', p['TES']['P_max'])\n", 1),
    ("TES charge/discharge limits (storage update)",
     "    P_TES_ch  = max(0.0, min(P_TES_ch,  P_max))\n    P_TES_dis = max(0.0, min(P_TES_dis, P_max))\n",
     "    P_TES_ch  = max(0.0, min(P_TES_ch,  p['TES'].get('P_max_ch', P_max)))\n"
     "    P_TES_dis = max(0.0, min(P_TES_dis, p['TES'].get('P_max_dis', P_max)))\n", 1),
    ("TES discharge limit (dispatch)",
     "P_dis = min(P_dis, p['TES']['P_max'])",
     "P_dis = min(P_dis, p['TES'].get('P_max_dis', p['TES']['P_max']))", 4),
    ("PV interpretation (150 W nameplate scaling)",
     "    P_pv = max(0.0, eta * N_pv * p['PV']['A'] * G_Tot / 1e6)  # MW\n",
     "    P_pv = max(0.0, eta * N_pv * p['PV']['A'] * G_Tot / 1e6) * p['PV'].get('output_scale', 1.0)  # MW\n", 1),
    ("Hydrogen formula: minimum (original) or addition (Eq. 9)",
     "            H2_kg_hour = min(H2_from_e, H2_from_th)\n\n"
     "            P_e_used  = H2_kg_hour / p['HTSE']['K_e']\n"
     "            P_th_used = H2_kg_hour / p['HTSE']['K_th']\n",
     "            if p['HTSE'].get('formula', 'minimum') == 'addition':\n"
     "                H2_kg_hour = H2_from_e + H2_from_th\n"
     "                P_e_used, P_th_used = P_e_htse, P_th_htse\n"
     "            else:\n"
     "                H2_kg_hour = min(H2_from_e, H2_from_th)\n\n"
     "                P_e_used  = H2_kg_hour / p['HTSE']['K_e']\n"
     "                P_th_used = H2_kg_hour / p['HTSE']['K_th']\n", 1),
    ("Allocation when the SOEC cannot run",
     "        else:\n            WER = P_RE_surplus * dt\n"
     "            Q, LF, WEN = TES_update_with_waste(Q, P_nuc_surplus_th, C, dt, p)\n",
     "        elif p['HTSE'].get('inactive_soec', 'keep_split') == 'renewables_first':\n"
     "            return Mode1(P_RE, P_nuc, P_grid, Q, C, p)\n"
     "        else:\n            WER = P_RE_surplus * dt\n"
     "            Q, LF, WEN = TES_update_with_waste(Q, P_nuc_surplus_th, C, dt, p)\n", 1),
    ("Mode 3 TES-factor check (start of hour or end-state guard)",
     "        return Mode2(P_RE, P_nuc, P_grid, Q, C, P_SOEC, P_RE_max, DF, p)\n",
     "        out = Mode2(P_RE, P_nuc, P_grid, Q, C, P_SOEC, P_RE_max, DF, p)\n"
     "        if p['HTSE'].get('soc_switch', 'start') == 'post_guard' and out[5] < TES_factor * C - 1e-10:\n"
     "            return Mode1(P_RE, P_nuc, P_grid, Q, C, p)\n"
     "        return out\n", 1),
    ("Reactor variable-cost basis",
     "    VOM = p['smTMSR']['P_th'] * p['hours_per_year'] * p['econ']['smTMSR_VOM']\n"
     "    FC  = p['smTMSR']['P_th'] * p['hours_per_year'] * p['econ']['smTMSR_fuel']\n",
     "    VOM = p['econ'].get('variable_MW', p['smTMSR']['P_th']) * p['hours_per_year'] * p['econ']['smTMSR_VOM']\n"
     "    FC  = p['econ'].get('variable_MW', p['smTMSR']['P_th']) * p['hours_per_year'] * p['econ']['smTMSR_fuel']\n", 1),
    ("Decommissioning timing",
     "    if p['econ']['smTMSR_lifetime'] <= project_lifetime:\n",
     "    if p['econ'].get('decommissioning', 'retirement') == 'project':\n"
     "        PW_decom = present_worth(capacity_kWe * p['econ']['smTMSR_decom'],\n"
     "                                 i, project_lifetime)\n"
     "    elif p['econ']['smTMSR_lifetime'] <= project_lifetime:\n", 1),
    ("Balance-of-plant proxy (same proxy as our model, teacher NPC conventions)",
     "    return PV_NPC + WT_NPC + TES_NPC + HTSE_NPC + H2_tank_NPC + smTMSR_NPC\n",
     "    total = PV_NPC + WT_NPC + TES_NPC + HTSE_NPC + H2_tank_NPC + smTMSR_NPC\n"
     "    if p['econ'].get('balance_of_plant', 'embedded') == 'separate_rated_proxy':\n"
     "        e_kW = p['smTMSR']['P_e'] * 1000.0\n"
     "        th_kW = p['smTMSR']['P_th'] * 1000.0\n"
     "        project = p['econ']['project_lifetime']\n"
     "        total += (2.0 * calculate_component_NPC(e_kW * 149.0, e_kW * 2.98, i, 20, project)\n"
     "                  + calculate_component_NPC(th_kW * 45.0, th_kW * 0.9, i, 20, project)\n"
     "                  + calculate_component_with_replacement(PV_kW * 700.0, PV_kW * 7.0,\n"
     "                                                         PV_kW * 600.0, 10, i, project))\n"
     "    return total\n", 1),
    ("LCOE energy denominator",
     "    E_load = (total_grid - total_DPS) * 1000.0  # kWh/year\n",
     "    E_load = (total_grid - (total_DPS if p['econ'].get('load_basis', 'served') == 'served'\n"
     "                            else 0.0)) * 1000.0  # kWh/year\n", 1),
)


def patched_teacher_source(source=None):
    text = TEACHER_FILE.read_text() if source is None else source
    for purpose, old, new, count in PATCHES:
        found = text.count(old)
        # stop if his file changed, a patch would silently miss
        if found != count:
            raise RuntimeError(f"Teacher source changed; '{purpose}' found {found}x, expected {count}x.")
        text = text.replace(old, new)
    return text


_TEACHER = None


def teacher():
    global _TEACHER
    if _TEACHER is None:
        namespace = {"__name__": "teacher_script_with_grid_switches"}
        # file on disk stays untouched, patches only live in memory
        exec(compile(patched_teacher_source(), str(TEACHER_FILE) + " [grid switches]", "exec"), namespace)
        _TEACHER = namespace
    return _TEACHER


def teacher_config(case):
    return {"N_pv": case["pv"], "C_TES": float(case["tes"]), "N_wt": case["wind"],
            "P_SOEC": case["soec"], "DF": case["df"], "TES_factor": case["tes_factor"]}


def teacher_params(physics, formula, finance=None, case=None):
    if formula not in FORMULAS:
        raise ValueError(formula)
    p = teacher()["create_params"]()
    p["smTMSR"]["P_th"] = float(physics.reactor_2025_mwth)
    # keep electric output consistent with the new P_th
    p["smTMSR"]["P_e"] = p["smTMSR"]["P_th"] * p["smTMSR"]["eta_PB"]
    if physics.pv_model == "nameplate_150W":
        # rescale so one panel gives 150 W at 1000 W/m2
        p["PV"]["output_scale"] = p["PV"]["P_rated"] / (p["PV"]["eta_r"] * p["PV"]["A"] * 1000.0)
    p["HTSE"].update(formula=formula, inactive_soec=physics.inactive_soec, soc_switch=physics.soc_switch)
    if case is not None:
        charge, discharge = pr.power_limits(case, physics)
        p["TES"]["P_max_ch"], p["TES"]["P_max_dis"] = float(charge), float(discharge)
    if finance is not None:
        e = p["econ"]
        # his script takes nominal + inflation, so pass the real rate as nominal with 0 inflation
        if finance.real_rate != TEACHER_FINANCE.real_rate:
            e["nominal_discount_rate"], e["inflation_rate"] = float(finance.real_rate), 0.0
        e["project_lifetime"] = finance.years
        if finance.variable_basis == "electric":
            # VOM and fuel per MWe instead of MWth
            e["variable_MW"] = p["smTMSR"]["P_e"]
        e.update(load_basis=finance.load_basis, balance_of_plant=finance.balance_of_plant,
                 decommissioning=finance.decommissioning)
    return p


def teacher_renewables(inputs, case, physics):
    """Hour-by-hour with the teacher's own PV/WT functions, as in run_simulation."""
    t = teacher()
    p = teacher_params(physics, "minimum")
    out = np.zeros(len(inputs.load))
    for i in range(len(out)):
        pv, _ = t["PV_power_output"](case["pv"], inputs.solar[i], inputs.temperature[i], p)
        out[i] = pv + t["WT_power_output"](inputs.wind[i], case["wind"], p)
    return out


def teacher_simulate(inputs, case, physics, formula, renewable=None):
    """Mirror of the teacher's run_simulation loop and metrics for one design."""
    t = teacher()
    p = teacher_params(physics, formula, case=case)
    cfg = teacher_config(case)
    re = teacher_renewables(inputs, case, physics) if renewable is None else renewable
    n, dt = len(re), p["dt"]
    nuclear = t["smTMSR_electric_power"](p["smTMSR"]["P_th"], p["smTMSR"]["eta_PB"])
    re_max = np.max(re)
    # TES starts at LF_min, same as run_simulation
    q = p["TES"]["LF_min"] * cfg["C_TES"]
    r = {k: np.zeros(n) for k in ("DPS", "WER", "WEN", "H2_kg_hour", "LF")}
    load, mode = inputs.load, case["mode"]
    for i in range(n):
        if mode == 1:
            out = t["Mode1"](re[i], nuclear, load[i], q, cfg["C_TES"], p)
        elif mode == 2:
            out = t["Mode2"](re[i], nuclear, load[i], q, cfg["C_TES"], cfg["P_SOEC"], re_max, cfg["DF"], p)
        else:
            out = t["Mode3"](re[i], nuclear, load[i], q, cfg["C_TES"], cfg["P_SOEC"], re_max,
                             cfg["DF"], cfg["TES_factor"], p)
        r["DPS"][i], r["WER"][i], r["WEN"][i], r["H2_kg_hour"][i], r["LF"][i], q = out
    r["H2_kg_cum"] = np.cumsum(r["H2_kg_hour"]) * dt
    total_grid = float(np.sum(load))
    r["total_H2_kg"] = float(np.sum(r["H2_kg_hour"]) * dt)
    r["total_DPS"] = float(np.sum(r["DPS"]))
    r["total_WEN"] = float(np.sum(r["WEN"]))
    r["total_WER"] = float(np.sum(r["WER"]))
    r["avg_LF"] = float(np.mean(r["LF"]))
    r["total_RE"] = float(np.sum(re))
    r["DPSP"] = r["total_DPS"] / total_grid
    # WEN is heat, so convert with eta_PB before adding WER
    r["EWP"] = (r["total_WER"] + r["total_WEN"] * p["smTMSR"]["eta_PB"]) / (r["total_RE"] + nuclear * n * dt)
    r["total_grid"], r["RE"] = total_grid, re
    return r


def teacher_lcoe(result, case, physics, formula, finance):
    p = teacher_params(physics, formula, finance)
    return teacher()["calculate_LCOE"](teacher_config(case), p, result["total_H2_kg"],
                                       result["total_DPS"], result["total_grid"], case["mode"])


@njit(cache=False)
def _dispatch(load, renewable, paper, mode, thermal, capacity, battery, soec, df,
              factor, charge_limit, discharge_limit, fallback, post_guard, additive):
    """paper_reproduction._dispatch with a conserving Eq. 9 addition branch."""
    s = np.zeros((len(load), 25))
    qmin, bmin = .05*capacity, .15*battery
    q, b = qmin, bmin
    nuclear = .4*thermal
    for i in range(len(load)):
        demand, power, oldq, oldb = load[i], renewable[i], q, b
        active = paper == 2025 and (mode == 2 or (mode == 3 and q/capacity >= factor))
        h = edraw = hdraw = bc = bd = 0.0
        eav = hav = 0.0
        for attempt in range(2):
            if paper == 2024:
                ng = min(nuclear, demand)
                rg = min(power, max(0.0, demand-ng))
            else:
                rg = min(power, demand)
                ng = min(nuclear, max(0.0, demand-rg))
            h = edraw = hdraw = 0.0
            eav = hav = 0.0
            if active and power > df*demand and nuclear > (1-df)*demand:
                rg, ng = df*demand, (1-df)*demand
                eleft, hleft = power-rg, thermal-ng/.4
                eav, hav = eleft, hleft
                ealloc = min(eleft, soec)
                if soec > 0 and ealloc >= .15*soec:
                    if additive:
                        # Heat injected in the electrolyser's heat:electricity
                        # requirement ratio, limited by the heat available.
                        hdraw = min(hleft, KE*ealloc/KTH)
                        edraw = ealloc
                        h = KE*edraw+KTH*hdraw
                    else:
                        h = min(KE*ealloc, KTH*hleft)
                        if h/KE < .15*soec:
                            h = 0.0
                        edraw = min(h/KE, eleft, soec)
                        hdraw = min(h/KTH, hleft)
                if h == 0 and fallback:
                    rg = min(power, demand)
                    ng = min(nuclear, max(0.0, demand-rg))
            remaining = max(0.0, thermal-ng/.4-hdraw)
            shortage = max(0.0, demand-rg-ng)
            tc = td = bc = bd = 0.0
            if shortage > 0:
                if paper == 2024 and mode == 2:
                    bd = min(shortage, 10.0, max(0.0, b-bmin)*.96)
                    shortage -= bd
                td = min(shortage/.4, discharge_limit, max(0.0, q-qmin))
                shortage = max(0.0, shortage-.4*td)
                if paper == 2024 and mode == 1:
                    bd = min(shortage, 10.0, max(0.0, b-bmin)*.96)
                    shortage = max(0.0, shortage-bd)
            else:
                tc = min(remaining, charge_limit, max(0.0, capacity-q))
                if paper == 2024 and battery > 0:
                    bc = min(max(0.0, power-rg), 10.0, max(0.0, battery-b)/.96)
            qnext = q+tc-td
            if paper == 2025 and mode == 3 and post_guard and active and qnext < factor*capacity-1e-10:
                active = False
                continue
            break
        q = qnext
        b += .96*bc-bd/.96
        dps = max(0.0, demand-rg-ng-.4*td-bd)
        wer = max(0.0, power-rg-edraw-bc)
        wen = max(0.0, remaining-tc)
        battery_loss = bc*(1-.96)+bd*(1/.96-1)
        electricity = power+ng+.4*td+bd-(demand-dps+wer+bc+edraw)
        heat = thermal-ng/.4-hdraw-tc-wen
        tes_balance = q-oldq-tc+td
        bs_balance = b-oldb-.96*bc+bd/.96
        total = power+nuclear-(demand-dps+wer+.4*wen+edraw+.4*hdraw
                               +.4*(q-oldq)+b-oldb+battery_loss)
        if additive:
            unmet_e, unmet_th, over = max(0.0, edraw-eav), max(0.0, hdraw-hav), max(0.0, edraw-soec)
        else:
            unmet_e, unmet_th, over = max(0.0, h/KE-edraw), max(0.0, h/KTH-hdraw), max(0.0, h/KE-soec)
        values = (i+1.0, demand, power, q, q/capacity, b, b/battery if battery else 0.0,
                  h, dps, wer, wen, tc, td, bc, bd, edraw, hdraw,
                  unmet_e, unmet_th, over, electricity, heat, tes_balance, bs_balance, total)
        for j in range(25):
            s[i, j] = values[j]
    return s


def our_simulate(inputs, case, physics, formula, components=None, check_reference=False):
    if formula not in FORMULAS:
        raise ValueError(formula)
    panel, turbine = components if components is not None else pr.renewables(inputs, physics.pv_model)
    re = case["pv"]*panel+case["wind"]*turbine
    thermal = physics.reactor_2025_mwth
    charge, discharge = pr.power_limits(case, physics)
    args = (inputs.load, re, case["paper"], case["mode"], thermal, case["tes"], case["battery"],
            case["soec"], case["df"], case["tes_factor"], charge, discharge,
            physics.inactive_soec == "renewables_first", physics.soc_switch == "post_guard")
    matrix = _dispatch(*args, formula == "addition")
    # minimum branch must match the original kernel exactly
    if check_reference and formula == "minimum":
        if not np.array_equal(matrix, pr._dispatch(*args, False)):
            raise AssertionError("Minimum-formula kernel differs from paper_reproduction.")
    series = {k: matrix[:, j] for j, k in enumerate(pr.FIELDS)}
    return pr.summarize(series, thermal), series, pr.audit(series, case)


def settings_record(engine, formula, physics, finance):
    return {"engine": engine, "hydrogen_formula": formula,
            "physics": {k: v for k, v in asdict(physics).items() if k != "hydrogen_formula"},
            "finance": asdict(finance)}
