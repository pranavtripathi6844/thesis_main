"""
Nuclear-Renewable Hybrid Energy System (NRHES) with High Temperature Steam Electrolysis (HTSE)
Based on: "Techno-economic assessment of a smTMSR-based nuclear-renewable hybrid energy system"
Author: George Afful - Sept 2026
"""

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from dataclasses import dataclass, field
from typing import Tuple, Dict

# ====================== SECTION 1: SYSTEM PARAMETERS ======================

@dataclass
class SystemParams:
    """Container for all system parameters"""
    pass

def create_params() -> dict:
    """Create the parameters dictionary matching MATLAB struct"""
    params = {
        'smTMSR': {
            'P_th':   25.0,      # MWth
            'eta_PB': 0.4,       # thermal-to-electric efficiency
        },
        'PV': {
            'P_rated': 150.0,           # W
            'A':       1.658 * 0.834,   # m²
            'eta_r':   0.137,           # reference module efficiency
            'eta_pc':  1.0,             # power conditioning efficiency
            'mu':      0.005,           # temperature coefficient
            'T_ref':   25.0,            # °C
            'NOCT':    47.5,            # °C
        },
        'WT': {
            'P_rated': 1.5,   # MW
            'z_hub':   70.0,  # m
            'z_anem':  10.0,  # m
            'z0':      1.5,   # m
            'v_ci':    3.0,   # m/s
            'v_co':    22.0,  # m/s
            'v_r':     13.0,  # m/s
        },
        'TES': {
            'LF_min':  0.05,
            'LF_max':  1.0,
            'eta_ch':  1.0,
            'eta_dis': 1.0,
            'P_max':   10.0,  # MWth
            'sigma':   0.0,
        },
        'HTSE': {
            'K_th':          1000.0 / 11.15,   # kg H2/MWhth
            'K_e':           1000.0 / 35.1,    # kg H2/MWhe
            'P_e_min_ratio': 0.15,
        },
        'econ': {
            'PV_inv_cost':   1040.0,
            'PV_OM_cost':    9.0,
            'PV_lifetime':   20,
            'WT_inv_cost':   1695.0,
            'WT_OM_cost':    51.0,
            'WT_lifetime':   20,
            'TES_inv_cost':  12.0,
            'TES_OM_cost':   0.24,
            'TES_lifetime':  20,
            'HTSE_inv_cost': 662.0,
            'HTSE_OM_fixed': 58.69,
            'HTSE_lifetime': 5,
            'H2_tank_inv':      400.0,
            'H2_tank_OM':       15.0,
            'H2_tank_lifetime': 20,
            'smTMSR_OCC':      3000.0,
            'smTMSR_FOM':      90.26,
            'smTMSR_VOM':      2.17,
            'smTMSR_fuel':     0.381,
            'smTMSR_decom':    621.0,
            'smTMSR_lifetime': 60,
            'H2_price':        2.75,
            'H2_transport':    0.6909,
            'nominal_discount_rate': 0.06,
            'inflation_rate':        0.02,
            'project_lifetime':      20,
        },
        'dt':             1.0,
        'hours_per_year': 8760,
    }
    # Derived
    params['smTMSR']['P_e'] = params['smTMSR']['P_th'] * params['smTMSR']['eta_PB']
    return params


# ====================== SECTION 3: COMPONENT MODELS ======================

def smTMSR_electric_power(P_smTMSR_th: float, eta_PB: float) -> float:
    """Eq. (1): Electric power output of smTMSR"""
    return P_smTMSR_th * eta_PB


def PV_power_output(N_pv: int, G_Tot: float, T_a: float, p: dict) -> Tuple[float, float]:
    """Eqs. (2)-(4): PV power output"""
    T_cell = T_a + ((p['PV']['NOCT'] - 20.0) / 800.0) * G_Tot
    eta = (p['PV']['eta_r'] * p['PV']['eta_pc'] *
           (1.0 - p['PV']['mu'] * (T_cell - p['PV']['T_ref'])))
    P_pv = max(0.0, eta * N_pv * p['PV']['A'] * G_Tot / 1e6)  # MW
    return P_pv, T_cell


def WT_power_output(V_anem: float, N_wt: int, p: dict) -> float:
    """Eqs. (5)-(6): Wind turbine power output"""
    v_hub = (V_anem * np.log(p['WT']['z_hub'] / p['WT']['z0']) /
             np.log(p['WT']['z_anem'] / p['WT']['z0']))

    if v_hub < p['WT']['v_ci'] or v_hub >= p['WT']['v_co']:
        P_single = 0.0
    elif v_hub < p['WT']['v_r']:
        P_single = (p['WT']['P_rated'] *
                    (v_hub**3 - p['WT']['v_ci']**3) /
                    (p['WT']['v_r']**3 - p['WT']['v_ci']**3))
    else:
        P_single = p['WT']['P_rated']
    return N_wt * P_single


def TES_update_with_waste(Q_TES_prev: float, P_TES_ch: float,
                          C_TES: float, dt: float, p: dict) -> Tuple[float, float, float]:
    """TES charging; returns overflow as WEN (MWhth)"""
    eta_ch = p['TES']['eta_ch']
    P_max  = p['TES']['P_max']
    Q_max  = p['TES']['LF_max'] * C_TES
    Q_loss = Q_TES_prev * (1.0 - p['TES']['sigma'])

    P_TES_ch       = max(0.0, min(P_TES_ch, P_max))
    Q_after_charge = min(Q_loss + P_TES_ch * eta_ch * dt, Q_max)
    WEN_overflow   = max(0.0, (Q_loss + P_TES_ch * eta_ch * dt) - Q_after_charge)

    Q_TES  = Q_after_charge
    LF_TES = Q_TES / Q_max
    return Q_TES, LF_TES, WEN_overflow


def TES_update(Q_TES_prev: float, P_TES_ch: float, P_TES_dis: float,
               C_TES: float, dt: float, p: dict) -> Tuple[float, float]:
    """Eqs. (7)-(8): TES charge/discharge update"""
    eta_ch  = p['TES']['eta_ch']
    eta_dis = p['TES']['eta_dis']
    P_max   = p['TES']['P_max']
    Q_min   = p['TES']['LF_min'] * C_TES
    Q_max   = p['TES']['LF_max'] * C_TES
    Q_loss  = Q_TES_prev * (1.0 - p['TES']['sigma'])

    P_TES_ch  = max(0.0, min(P_TES_ch,  P_max))
    P_TES_dis = max(0.0, min(P_TES_dis, P_max))

    if P_TES_ch > 0:
        Q_TES = min(Q_loss + P_TES_ch * eta_ch * dt, Q_max)
    elif P_TES_dis > 0:
        max_dis_power = max(0.0, Q_loss - Q_min) / dt
        P_TES_dis     = min(P_TES_dis, max_dis_power)
        Q_TES         = max(Q_loss - (P_TES_dis / eta_dis) * dt, Q_min)
    else:
        Q_TES = Q_loss

    Q_TES  = max(Q_min, min(Q_max, Q_TES))
    LF_TES = Q_TES / Q_max
    return Q_TES, LF_TES


# ====================== SECTION 4: ECONOMIC FUNCTIONS ======================

def calculate_real_interest_rate(nominal_i: float, f: float) -> float:
    return (nominal_i - f) / (1.0 + f)


def calculate_CRF(i: float, n: int) -> float:
    return i * (1.0 + i) ** n / ((1.0 + i) ** n - 1.0)


def present_worth(F: float, i: float, n: int) -> float:
    return F / (1.0 + i) ** n


def calculate_component_NPC(Cap: float, OM_annual: float, i: float,
                            lifetime: int, project_lifetime: int) -> float:
    NPC = Cap
    for year in range(1, min(lifetime, project_lifetime) + 1):
        NPC += present_worth(OM_annual, i, year)
    return NPC


def calculate_component_with_replacement(Cap: float, OM_annual: float,
                                         REP_cost: float, REP_interval: int,
                                         i: float, project_lifetime: int) -> float:
    NPC = Cap
    for year in range(1, project_lifetime + 1):
        NPC += present_worth(OM_annual, i, year)
    for year in range(REP_interval, project_lifetime + 1, REP_interval):
        NPC += present_worth(REP_cost, i, year)
    return NPC


def calculate_smTMSR_NPC(p: dict, i: float, project_lifetime: int) -> float:
    P_e = p['smTMSR']['P_e']
    capacity_kWe = P_e * 1000.0

    OCC = capacity_kWe * p['econ']['smTMSR_OCC']
    FOM = capacity_kWe * p['econ']['smTMSR_FOM']
    VOM = p['smTMSR']['P_th'] * p['hours_per_year'] * p['econ']['smTMSR_VOM']
    FC  = p['smTMSR']['P_th'] * p['hours_per_year'] * p['econ']['smTMSR_fuel']
    annual_OM = FOM + VOM + FC

    PW_OM = 0.0
    for year in range(1, project_lifetime + 1):
        PW_OM += present_worth(annual_OM, i, year)

    if p['econ']['smTMSR_lifetime'] <= project_lifetime:
        PW_decom = present_worth(capacity_kWe * p['econ']['smTMSR_decom'],
                                 i, p['econ']['smTMSR_lifetime'])
    else:
        PW_decom = 0.0

    return OCC + PW_OM + PW_decom


def calculate_TNPC(config: dict, p: dict, total_H2_kg: float, mode: int) -> float:
    i = calculate_real_interest_rate(p['econ']['nominal_discount_rate'],
                                     p['econ']['inflation_rate'])

    # PV
    PV_kW  = config['N_pv'] * p['PV']['P_rated'] / 1000.0
    PV_NPC = calculate_component_NPC(PV_kW * p['econ']['PV_inv_cost'],
                                     PV_kW * p['econ']['PV_OM_cost'],
                                     i, p['econ']['PV_lifetime'],
                                     p['econ']['project_lifetime'])

    # WT
    WT_kW  = config['N_wt'] * p['WT']['P_rated'] * 1000.0
    WT_NPC = calculate_component_NPC(WT_kW * p['econ']['WT_inv_cost'],
                                     WT_kW * p['econ']['WT_OM_cost'],
                                     i, p['econ']['WT_lifetime'],
                                     p['econ']['project_lifetime'])

    # TES
    TES_kWh = config['C_TES'] * 1000.0
    TES_NPC = calculate_component_NPC(TES_kWh * p['econ']['TES_inv_cost'],
                                      TES_kWh * p['econ']['TES_OM_cost'],
                                      i, p['econ']['TES_lifetime'],
                                      p['econ']['project_lifetime'])

    # HTSE (modes 2 & 3 only)
    if mode > 1 and config['P_SOEC'] > 0:
        HTSE_kW  = config['P_SOEC'] * 1000.0
        HTSE_NPC = calculate_component_with_replacement(
            HTSE_kW * p['econ']['HTSE_inv_cost'],
            HTSE_kW * p['econ']['HTSE_OM_fixed'],
            HTSE_kW * p['econ']['HTSE_inv_cost'],
            p['econ']['HTSE_lifetime'], i, p['econ']['project_lifetime'])
    else:
        HTSE_NPC = 0.0

    # H2 tank
    if mode > 1 and total_H2_kg > 0:
        H2_tank_kg = total_H2_kg / 365.0
        H2_tank_NPC = calculate_component_NPC(
            H2_tank_kg * p['econ']['H2_tank_inv'],
            H2_tank_kg * p['econ']['H2_tank_OM'],
            i, p['econ']['H2_tank_lifetime'],
            p['econ']['project_lifetime'])
    else:
        H2_tank_NPC = 0.0

    smTMSR_NPC = calculate_smTMSR_NPC(p, i, p['econ']['project_lifetime'])
    return PV_NPC + WT_NPC + TES_NPC + HTSE_NPC + H2_tank_NPC + smTMSR_NPC


def calculate_LCOE(config: dict, p: dict, total_H2_kg: float,
                   total_DPS: float, total_grid: float, mode: int) -> float:
    i    = calculate_real_interest_rate(p['econ']['nominal_discount_rate'],
                                        p['econ']['inflation_rate'])
    TNPC = calculate_TNPC(config, p, total_H2_kg, mode)
    CRF  = calculate_CRF(i, p['econ']['project_lifetime'])

    if mode > 1 and total_H2_kg > 0:
        H2_revenue = total_H2_kg * (p['econ']['H2_price'] - p['econ']['H2_transport'])
    else:
        H2_revenue = 0.0

    E_load = (total_grid - total_DPS) * 1000.0  # kWh/year
    return (TNPC * CRF - H2_revenue) / E_load


# ====================== SECTION 5: MODE 1 ======================

def Mode1(P_RE: float, P_nuc: float, P_grid: float, Q: float,
          C: float, p: dict) -> Tuple[float, float, float, float, float, float]:
    """Renewable energy prioritized; smTMSR compensates deficit"""
    DPS = 0.0; WER = 0.0; WEN = 0.0; H2_kg_hour = 0.0
    dt = p['dt']

    if P_RE > P_grid:
        WER = (P_RE - P_grid) * dt
        P_nuc_th_rem = p['smTMSR']['P_th']
        Q, LF, WEN = TES_update_with_waste(Q, P_nuc_th_rem, C, dt, p)

    elif P_RE < P_grid:
        deficit = P_grid - P_RE

        if P_nuc > deficit:
            P_nuc_th_rem = p['smTMSR']['P_th'] - deficit / p['smTMSR']['eta_PB']
            Q, LF, WEN = TES_update_with_waste(Q, max(0.0, P_nuc_th_rem), C, dt, p)

        elif abs(P_nuc - deficit) < 1e-6:
            Q, LF = TES_update(Q, 0.0, 0.0, C, dt, p)

        else:
            need    = deficit - P_nuc
            need_th = need / p['smTMSR']['eta_PB']
            max_dis_power = max(0.0, Q - p['TES']['LF_min'] * C) / dt

            if max_dis_power >= need_th - 1e-6:
                P_dis = need_th
            else:
                P_dis = max_dis_power
                DPS   = (need - P_dis * p['smTMSR']['eta_PB']) * dt

            P_dis = min(P_dis, p['TES']['P_max'])
            Q, LF = TES_update(Q, 0.0, P_dis, C, dt, p)

    else:
        Q, LF, WEN = TES_update_with_waste(Q, p['smTMSR']['P_th'], C, dt, p)

    return DPS, WER, WEN, H2_kg_hour, LF, Q


# ====================== SECTION 6: MODE 2 ======================

def Mode2(P_RE: float, P_nuc: float, P_grid: float, Q: float, C: float,
          P_SOEC: float, P_RE_max: float, DF: float, p: dict
          ) -> Tuple[float, float, float, float, float, float]:
    """Demand split: RE covers DF×P_grid, nuclear covers (1-DF)×P_grid"""
    DPS = 0.0; WER = 0.0; WEN = 0.0; H2_kg_hour = 0.0
    dt = p['dt']

    P_grid1 = DF * P_grid
    P_grid2 = (1.0 - DF) * P_grid
    SOEC_min_power = p['HTSE']['P_e_min_ratio'] * P_SOEC
    SOEC_max_power = P_SOEC

    if P_RE > P_grid1 and P_nuc > P_grid2:
        # Case 1: Both RE and nuclear have surplus
        P_RE_surplus     = P_RE - P_grid1
        P_nuc_surplus_e  = P_nuc - P_grid2
        P_nuc_surplus_th = P_nuc_surplus_e / p['smTMSR']['eta_PB']

        P_e_htse  = min(P_RE_surplus, SOEC_max_power)
        P_th_htse = min(P_nuc_surplus_th, SOEC_max_power / p['smTMSR']['eta_PB'])

        if P_e_htse >= SOEC_min_power and P_th_htse >= SOEC_min_power / p['smTMSR']['eta_PB']:
            H2_from_e  = p['HTSE']['K_e'] * P_e_htse
            H2_from_th = p['HTSE']['K_th'] * P_th_htse
            H2_kg_hour = min(H2_from_e, H2_from_th)

            P_e_used  = H2_kg_hour / p['HTSE']['K_e']
            P_th_used = H2_kg_hour / p['HTSE']['K_th']

            WER          = max(0.0, P_RE_surplus - P_e_used) * dt
            P_nuc_th_rem = max(0.0, P_nuc_surplus_th - P_th_used)

            if P_nuc_th_rem > 1e-6:
                Q, LF, WEN = TES_update_with_waste(Q, P_nuc_th_rem, C, dt, p)
            else:
                LF  = Q / C
                WEN = 0.0
        else:
            WER = P_RE_surplus * dt
            Q, LF, WEN = TES_update_with_waste(Q, P_nuc_surplus_th, C, dt, p)

    elif P_RE > P_grid1 and P_nuc < P_grid2:
        # Case 2: RE surplus but nuclear deficit
        P_RE_surplus    = P_RE - P_grid1
        nuclear_deficit = P_grid2 - P_nuc

        if P_RE_surplus >= nuclear_deficit:
            WER = (P_RE_surplus - nuclear_deficit) * dt
            Q, LF = TES_update(Q, 0.0, 0.0, C, dt, p)
        else:
            need    = nuclear_deficit - P_RE_surplus
            need_th = need / p['smTMSR']['eta_PB']
            max_dis_power = max(0.0, Q - p['TES']['LF_min'] * C) / dt

            if max_dis_power >= need_th - 1e-6:
                P_dis = need_th
            else:
                P_dis = max_dis_power
                DPS   = (need - P_dis * p['smTMSR']['eta_PB']) * dt
            P_dis = min(P_dis, p['TES']['P_max'])
            Q, LF = TES_update(Q, 0.0, P_dis, C, dt, p)

    elif P_RE < P_grid1 and P_nuc > P_grid2:
        # Case 3: RE deficit but nuclear surplus
        RE_deficit      = P_grid1 - P_RE
        P_nuc_surplus_e = P_nuc - P_grid2

        if P_nuc_surplus_e >= RE_deficit:
            excess_th = (P_nuc_surplus_e - RE_deficit) / p['smTMSR']['eta_PB']
            if excess_th > 1e-6:
                Q, LF, WEN = TES_update_with_waste(Q, excess_th, C, dt, p)
            else:
                Q, LF = TES_update(Q, 0.0, 0.0, C, dt, p)
        else:
            need    = RE_deficit - P_nuc_surplus_e
            need_th = need / p['smTMSR']['eta_PB']
            max_dis_power = max(0.0, Q - p['TES']['LF_min'] * C) / dt

            if max_dis_power >= need_th - 1e-6:
                P_dis = need_th
            else:
                P_dis = max_dis_power
                DPS   = (need - P_dis * p['smTMSR']['eta_PB']) * dt
            P_dis = min(P_dis, p['TES']['P_max'])
            Q, LF = TES_update(Q, 0.0, P_dis, C, dt, p)

    elif P_RE < P_grid1 and P_nuc < P_grid2:
        # Case 4: Both in deficit
        total_deficit = (P_grid1 - P_RE) + (P_grid2 - P_nuc)
        need_th = total_deficit / p['smTMSR']['eta_PB']
        max_dis_power = max(0.0, Q - p['TES']['LF_min'] * C) / dt

        if max_dis_power >= need_th - 1e-6:
            P_dis = need_th
        else:
            P_dis = max_dis_power
            DPS   = (total_deficit - P_dis * p['smTMSR']['eta_PB']) * dt
        P_dis = min(P_dis, p['TES']['P_max'])
        Q, LF = TES_update(Q, 0.0, P_dis, C, dt, p)

    else:
        Q, LF = TES_update(Q, 0.0, 0.0, C, dt, p)

    return DPS, WER, WEN, H2_kg_hour, LF, Q


# ====================== SECTION 7: MODE 3 ======================

def Mode3(P_RE: float, P_nuc: float, P_grid: float, Q: float, C: float,
          P_SOEC: float, P_RE_max: float, DF: float, TES_factor: float, p: dict
          ) -> Tuple[float, float, float, float, float, float]:
    """HTSE with TES limiting condition"""
    if (Q / C) >= TES_factor:
        return Mode2(P_RE, P_nuc, P_grid, Q, C, P_SOEC, P_RE_max, DF, p)
    else:
        return Mode1(P_RE, P_nuc, P_grid, Q, C, p)


# ====================== SECTION 8: LOAD INPUT DATA ======================

def load_input_data(filename: str = 'InputData.xlsx') -> Tuple[np.ndarray, ...]:
    """Load hourly input data from Excel"""
    print("=" * 40)
    print("NRHES SIMULATION MODEL")
    print("=" * 40)
    print()
    print(f"Loading {filename}...")

    df = pd.read_excel(filename)
    hours  = df['hours'].values.flatten()
    G_Tot  = df['G_Tot'].values.flatten()
    T_a    = df['T_a'].values.flatten()
    V_anem = df['V_anem'].values.flatten()
    P_grid = df['P_grid'].values.flatten()

    print(f"Loaded {len(hours)} hours of data.")
    return hours, G_Tot, T_a, V_anem, P_grid


# ====================== SECTION 9: CONFIGURATIONS ======================

CFG1 = {'N_pv': 60423, 'C_TES': 2000.0, 'N_wt': 1,
        'P_SOEC': 0.0, 'DF': 0.0, 'TES_factor': 0.0}

CFG2 = {'N_pv': 80000, 'C_TES': 2000.0, 'N_wt': 1,
        'P_SOEC': 9.0870, 'DF': 0.5843, 'TES_factor': 0.0}

CFG3 = {'N_pv': 75054, 'C_TES': 250.0, 'N_wt': 1,
        'P_SOEC': 9.5445, 'DF': 0.3245, 'TES_factor': 0.8048}


# ====================== SECTION 10: RUN SIMULATIONS ======================

def run_simulation():
    """Main simulation"""
    params = create_params()
    hours, G_Tot, T_a, V_anem, P_grid = load_input_data()

    T_sim = len(hours)
    P_nuc = smTMSR_electric_power(params['smTMSR']['P_th'],
                                  params['smTMSR']['eta_PB'])
    print(f"smTMSR: P_th = {params['smTMSR']['P_th']:.0f} MWth  |  "
          f"P_e = {P_nuc:.0f} MWe")

    print("\n--- Configurations from Table 5 ---")
    cfgs = [CFG1, CFG2, CFG3]

    # --- Compute RE output for each config ---
    print("\n--- Computing renewable energy output ---")
    P_RE_list = []
    for cfg in cfgs:
        P_RE = np.zeros(T_sim)
        for t in range(T_sim):
            P_pv, _ = PV_power_output(cfg['N_pv'], G_Tot[t], T_a[t], params)
            P_wind  = WT_power_output(V_anem[t], cfg['N_wt'], params)
            P_RE[t] = P_pv + P_wind
        P_RE_list.append(P_RE)

    P_RE_max_list = [np.max(P_RE) for P_RE in P_RE_list]

    # --- Run simulations ---
    res = []
    for mode_idx, cfg in enumerate(cfgs, start=1):
        print(f"Running Mode {mode_idx}...")
        Q_TES = params['TES']['LF_min'] * cfg['C_TES']

        r = {
            'DPS': np.zeros(T_sim), 'WER': np.zeros(T_sim),
            'WEN': np.zeros(T_sim), 'H2_kg_hour': np.zeros(T_sim),
            'LF': np.zeros(T_sim),
        }

        for t in range(T_sim):
            if mode_idx == 1:
                out = Mode1(P_RE_list[0][t], P_nuc, P_grid[t],
                            Q_TES, cfg['C_TES'], params)
            elif mode_idx == 2:
                out = Mode2(P_RE_list[1][t], P_nuc, P_grid[t],
                            Q_TES, cfg['C_TES'], cfg['P_SOEC'],
                            P_RE_max_list[1], cfg['DF'], params)
            else:
                out = Mode3(P_RE_list[2][t], P_nuc, P_grid[t],
                            Q_TES, cfg['C_TES'], cfg['P_SOEC'],
                            P_RE_max_list[2], cfg['DF'],
                            cfg['TES_factor'], params)

            r['DPS'][t], r['WER'][t], r['WEN'][t], \
                r['H2_kg_hour'][t], r['LF'][t], Q_TES = out

        r['H2_kg_cum'] = np.cumsum(r['H2_kg_hour']) * params['dt']
        res.append(r)

    # --- Performance metrics ---
    print("\n--- Computing performance metrics ---")
    total_grid    = float(np.sum(P_grid))
    total_nuclear = P_nuc * T_sim * params['dt']

    for m, r in enumerate(res):
        r['total_H2_kg'] = float(np.sum(r['H2_kg_hour']) * params['dt'])
        r['total_DPS']   = float(np.sum(r['DPS']))
        r['total_WEN']   = float(np.sum(r['WEN']))
        r['total_WER']   = float(np.sum(r['WER']))
        r['avg_LF']      = float(np.mean(r['LF']))
        r['total_RE']    = float(np.sum(P_RE_list[m]))

        r['DPSP'] = r['total_DPS'] / total_grid
        wasted_MWhe = r['total_WER'] + r['total_WEN'] * params['smTMSR']['eta_PB']
        r['EWP'] = wasted_MWhe / (r['total_RE'] + total_nuclear)

        r['LCOE'] = calculate_LCOE(cfgs[m], params, r['total_H2_kg'],
                                   r['total_DPS'], total_grid, m + 1)

    # --- Print summary ---
    print()
    print("=" * 95)
    print(f"{'Mode':<8}  {'DPSP':>8}  {'EWP':>8}  {'LCOE($/kWh)':>12}  "
          f"{'H2(tonnes)':>14}  {'DPS(MWhe)':>10}  {'Avg LF':>12}")
    print("-" * 95)
    labels = ['Mode 1', 'Mode 2', 'Mode 3']
    for m, r in enumerate(res):
        print(f"{labels[m]:<8}  {r['DPSP']:>8.4f}  {r['EWP']:>8.4f}  "
              f"{r['LCOE']:>12.4f}  {r['total_H2_kg']/1000:>14.1f}  "
              f"{r['total_DPS']:>10.1f}  {r['avg_LF']:>12.3f}")
    print("=" * 95)

    print("\nPaper Table 5 targets:")
    print("  Mode 1: DPSP=0.0097  EWP=0.0402  LCOE=$0.0940/kWh")
    print("  Mode 2: DPSP=0.0090  EWP=0.0380  LCOE=$0.1162/kWh  H2=138.0 tonnes")
    print("  Mode 3: DPSP=0.0090  EWP=0.0062  LCOE=$0.0931/kWh  H2=98.8 tonnes")

    return res, cfgs, params, hours, G_Tot, T_a, V_anem, P_grid, P_RE_list


# ====================== SECTION 12: FIGURES ======================

def plot_figures(res, cfgs, params, hours, G_Tot, T_a, V_anem, P_grid):
    """Generate all figures"""

    # Figure 1: Annual input data
    fig1, axs = plt.subplots(4, 1, figsize=(14, 10))
    axs[0].plot(hours, G_Tot, 'b-', linewidth=0.8)
    axs[0].set_ylabel('Solar Radiation (W/m²)')
    axs[0].set_title('(a) Hourly Solar Irradiance')
    axs[0].grid(True); axs[0].set_xlim([0, 8760])

    axs[1].plot(hours, T_a, 'r-', linewidth=0.8)
    axs[1].set_ylabel('Temperature (°C)')
    axs[1].set_title('(b) Hourly Ambient Temperature')
    axs[1].grid(True); axs[1].set_xlim([0, 8760])

    axs[2].plot(hours, V_anem, 'g-', linewidth=0.8)
    axs[2].set_ylabel('Wind Speed (m/s)')
    axs[2].set_title('(c) Hourly Wind Speed')
    axs[2].grid(True); axs[2].set_xlim([0, 8760])

    axs[3].plot(hours, P_grid, 'm-', linewidth=0.8)
    axs[3].set_xlabel('Time (Hours)')
    axs[3].set_ylabel('Grid Demand (MW)')
    axs[3].set_title('(d) Hourly Grid Power Demand')
    axs[3].grid(True); axs[3].set_xlim([0, 8760])

    fig1.suptitle('Figure 1: Annual Hourly Input Data — Wuwei City, Gansu',
                  fontsize=14, fontweight='bold')
    fig1.tight_layout()
    fig1.savefig('Figure1_Annual_Data.png', dpi=100)
    plt.close(fig1)

    # Figure 2: Mode 1
    fig2, axs = plt.subplots(3, 1, figsize=(14, 8))
    axs[0].plot(hours, res[0]['LF'], 'b-', linewidth=0.5)
    axs[0].axhline(0.05, color='r', linestyle='--', label='LF_min')
    axs[0].axhline(1.0, color='r', linestyle='--', label='LF_max')
    axs[0].set_ylabel('TES Load Factor')
    axs[0].set_ylim([0, 1.05]); axs[0].grid(True); axs[0].set_xlim([0, 8760])
    axs[0].set_title(f"Mode 1 TES LF  |  DPSP={res[0]['DPSP']:.4f}  "
                     f"EWP={res[0]['EWP']:.4f}  LCOE=${res[0]['LCOE']:.4f}/kWh")

    idx = np.where(res[0]['DPS'] > 0.001)[0]
    if len(idx) > 0:
        axs[1].stem(hours[idx], res[0]['DPS'][idx], 'r.', markerfmt='r.',
                    basefmt=' ')
    axs[1].set_ylabel('DPS (MWhe)'); axs[1].grid(True); axs[1].set_xlim([0, 8760])
    axs[1].set_title(f"Deficiency of Power Supply (Total: {res[0]['total_DPS']:.1f} MWhe)")

    idx = np.where(res[0]['WEN'] > 0.001)[0]
    if len(idx) > 0:
        axs[2].stem(hours[idx], res[0]['WEN'][idx], 'g.', markerfmt='g.',
                    basefmt=' ')
    axs[2].set_xlabel('Time (Hours)'); axs[2].set_ylabel('WEN (MWhth)')
    axs[2].grid(True); axs[2].set_xlim([0, 8760])
    axs[2].set_title(f"Wasted Nuclear Energy (Total: {res[0]['total_WEN']:.1f} MWhth)")

    fig2.suptitle('Figure 2: Mode 1 Results', fontsize=14, fontweight='bold')
    fig2.tight_layout()
    fig2.savefig('Figure2_Mode1_Results.png', dpi=100)
    plt.close(fig2)

    # Figure 3: Mode 2
    fig3, axs = plt.subplots(5, 1, figsize=(14, 12))
    axs[0].plot(hours, res[1]['H2_kg_hour'], 'b-', linewidth=0.5)
    axs[0].set_ylabel('H₂ (kg/h)'); axs[0].grid(True); axs[0].set_xlim([0, 8760])
    axs[0].set_title(f"H₂ Production Rate (Total: {res[1]['total_H2_kg']:.0f} kg "
                     f"= {res[1]['total_H2_kg']/1000:.1f} tonnes)")

    axs[1].plot(hours, res[1]['LF'], 'b-', linewidth=0.5)
    axs[1].axhline(0.05, color='r', linestyle='--')
    axs[1].axhline(1.0, color='r', linestyle='--')
    axs[1].set_ylabel('TES LF'); axs[1].set_ylim([0, 1.05])
    axs[1].grid(True); axs[1].set_xlim([0, 8760])
    axs[1].set_title(f"Mode 2 TES LF  |  DPSP={res[1]['DPSP']:.4f}  "
                     f"EWP={res[1]['EWP']:.4f}  LCOE=${res[1]['LCOE']:.4f}/kWh")

    idx = np.where(res[1]['DPS'] > 0.001)[0]
    if len(idx) > 0:
        axs[2].stem(hours[idx], res[1]['DPS'][idx], 'r.', basefmt=' ')
    axs[2].set_ylabel('DPS (MWhe)'); axs[2].grid(True); axs[2].set_xlim([0, 8760])
    axs[2].set_title(f"DPS (Total: {res[1]['total_DPS']:.1f} MWhe)")

    idx = np.where(res[1]['WEN'] > 0.001)[0]
    if len(idx) > 0:
        axs[3].stem(hours[idx], res[1]['WEN'][idx], 'g.', basefmt=' ')
    axs[3].set_ylabel('WEN (MWhth)'); axs[3].grid(True); axs[3].set_xlim([0, 8760])
    axs[3].set_title(f"WEN (Total: {res[1]['total_WEN']:.1f} MWhth)")

    idx = np.where(res[1]['WER'] > 0.001)[0]
    if len(idx) > 0:
        axs[4].stem(hours[idx], res[1]['WER'][idx], 'b.', basefmt=' ')
    axs[4].set_xlabel('Time (Hours)'); axs[4].set_ylabel('WER (MWhe)')
    axs[4].grid(True); axs[4].set_xlim([0, 8760])
    axs[4].set_title(f"WER (Total: {res[1]['total_WER']:.1f} MWhe)")

    fig3.suptitle('Figure 3: Mode 2 Results', fontsize=14, fontweight='bold')
    fig3.tight_layout()
    fig3.savefig('Figure3_Mode2_Results.png', dpi=100)
    plt.close(fig3)

    # Figure 4: Mode 3
    fig4, axs = plt.subplots(5, 1, figsize=(14, 12))
    axs[0].plot(hours, res[2]['H2_kg_hour'], 'b-', linewidth=0.5)
    axs[0].set_ylabel('H₂ (kg/h)'); axs[0].grid(True); axs[0].set_xlim([0, 8760])
    axs[0].set_title(f"H₂ Production Rate (Total: {res[2]['total_H2_kg']:.0f} kg "
                     f"= {res[2]['total_H2_kg']/1000:.1f} tonnes)")

    axs[1].plot(hours, res[2]['LF'], 'b-', linewidth=0.5)
    axs[1].axhline(cfgs[2]['TES_factor'], color='k', linestyle='--',
                   linewidth=1.5, label=f"TES_factor={cfgs[2]['TES_factor']:.4f}")
    axs[1].axhline(0.05, color='r', linestyle=':')
    axs[1].axhline(1.0, color='r', linestyle=':')
    axs[1].set_ylabel('TES LF'); axs[1].set_ylim([0, 1.05])
    axs[1].grid(True); axs[1].set_xlim([0, 8760])
    axs[1].set_title(f"Mode 3 TES LF  |  DPSP={res[2]['DPSP']:.4f}  "
                     f"EWP={res[2]['EWP']:.4f}  LCOE=${res[2]['LCOE']:.4f}/kWh  "
                     f"TES_factor={cfgs[2]['TES_factor']:.4f}")

    idx = np.where(res[2]['DPS'] > 0.001)[0]
    if len(idx) > 0:
        axs[2].stem(hours[idx], res[2]['DPS'][idx], 'r.', basefmt=' ')
    axs[2].set_ylabel('DPS (MWhe)'); axs[2].grid(True); axs[2].set_xlim([0, 8760])
    axs[2].set_title(f"DPS (Total: {res[2]['total_DPS']:.1f} MWhe)")

    idx = np.where(res[2]['WEN'] > 0.001)[0]
    if len(idx) > 0:
        axs[3].stem(hours[idx], res[2]['WEN'][idx], 'g.', basefmt=' ')
    axs[3].set_ylabel('WEN (MWhth)'); axs[3].grid(True); axs[3].set_xlim([0, 8760])
    axs[3].set_title(f"WEN (Total: {res[2]['total_WEN']:.1f} MWhth)")

    idx = np.where(res[2]['WER'] > 0.001)[0]
    if len(idx) > 0:
        axs[4].stem(hours[idx], res[2]['WER'][idx], 'b.', basefmt=' ')
    axs[4].set_xlabel('Time (Hours)'); axs[4].set_ylabel('WER (MWhe)')
    axs[4].grid(True); axs[4].set_xlim([0, 8760])
    axs[4].set_title(f"WER (Total: {res[2]['total_WER']:.1f} MWhe)")

    fig4.suptitle('Figure 4: Mode 3 Results', fontsize=14, fontweight='bold')
    fig4.tight_layout()
    fig4.savefig('Figure4_Mode3_Results.png', dpi=100)
    plt.close(fig4)

    print("\nFigures saved.")


# ====================== SECTION 13: SAVE RESULTS ======================

def save_results(res, cfgs, params, hours):
    """Save results to files"""

    # CSV: Hydrogen production data
    h2_df = pd.DataFrame({
        'Hour': hours,
        'Mode2_H2_kg_per_hour': res[1]['H2_kg_hour'],
        'Mode2_H2_kg_cumulative': res[1]['H2_kg_cum'],
        'Mode3_H2_kg_per_hour': res[2]['H2_kg_hour'],
        'Mode3_H2_kg_cumulative': res[2]['H2_kg_cum'],
    })
    h2_df.to_csv('Hydrogen_Production_Data.csv', index=False)

    # CSV: Economic results summary
    summary = pd.DataFrame({
        'Mode':                  ['Mode1', 'Mode2', 'Mode3'],
        'LCOE_USD_per_kWh':      [res[0]['LCOE'], res[1]['LCOE'], res[2]['LCOE']],
        'DPSP':                  [res[0]['DPSP'], res[1]['DPSP'], res[2]['DPSP']],
        'EWP':                   [res[0]['EWP'], res[1]['EWP'], res[2]['EWP']],
        'H2_Production_tonnes':  [res[0]['total_H2_kg']/1000,
                                  res[1]['total_H2_kg']/1000,
                                  res[2]['total_H2_kg']/1000],
        'DPS_MWhe':              [res[0]['total_DPS'], res[1]['total_DPS'],
                                  res[2]['total_DPS']],
        'WEN_MWhth':             [res[0]['total_WEN'], res[1]['total_WEN'],
                                  res[2]['total_WEN']],
        'WER_MWhe':              [res[0]['total_WER'], res[1]['total_WER'],
                                  res[2]['total_WER']],
        'Avg_LF':                [res[0]['avg_LF'], res[1]['avg_LF'],
                                  res[2]['avg_LF']],
    })
    summary.to_csv('NRHES_Economic_Results.csv', index=False)

    print("\nResults saved to:")
    print("  - Hydrogen_Production_Data.csv")
    print("  - NRHES_Economic_Results.csv")


# ====================== MAIN ======================

if __name__ == '__main__':
    res, cfgs, params, hours, G_Tot, T_a, V_anem, P_grid, P_RE_list = run_simulation()

    # Plot and save
    plot_figures(res, cfgs, params, hours, G_Tot, T_a, V_anem, P_grid)
    save_results(res, cfgs, params, hours)

    print("\n=== SIMULATION COMPLETE ===")
    print("Results saved to:")
    print("  - Hydrogen_Production_Data.csv")
    print("  - NRHES_Economic_Results.csv")
    print("  - Figure1_Annual_Data.png")
    print("  - Figure2_Mode1_Results.png")
    print("  - Figure3_Mode2_Results.png")
    print("  - Figure4_Mode3_Results.png")