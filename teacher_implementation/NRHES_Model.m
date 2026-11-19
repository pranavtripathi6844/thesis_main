%% Nuclear-Renewable Hybrid Energy System (NRHES) with High Temperature Steam Electrolysis (HTSE)
% Based on: "Techno-economic assessment of a smTMSR-based nuclear-renewable hybrid energy system"
% Author: George Afful - Sept 2026

clear all; close all; clc;

%% ====================== SECTION 1: SYSTEM PARAMETERS ======================

% smTMSR parameters 
params.smTMSR.P_th   = 25;       % MWth  
params.smTMSR.eta_PB = 0.4;      % thermal-to-electric efficiency 
params.smTMSR.P_e    = params.smTMSR.P_th * params.smTMSR.eta_PB;  % 10 MWe

% PV panel parameters 
params.PV.P_rated = 150;           % W  rated power per panel
params.PV.A       = 1.658 * 0.834; % m²  panel area
params.PV.eta_r   = 0.137;         % reference module efficiency
params.PV.eta_pc  = 1.0;           % power conditioning efficiency
params.PV.mu      = 0.005;         % temperature coefficient (per °C)
params.PV.T_ref   = 25;            % reference cell temperature (°C)
params.PV.NOCT    = 47.5;          % nominal operation cell temperature (°C)

% Wind turbine parameters 
params.WT.P_rated = 1.5;   % MW  rated power per turbine
params.WT.z_hub   = 70;    % m   hub height
params.WT.z_anem  = 10;    % m   anemometer height
params.WT.z0      = 1.5;   % m   surface roughness length
params.WT.v_ci    = 3;     % m/s cut-in wind speed
params.WT.v_co    = 22;    % m/s cut-out wind speed
params.WT.v_r     = 13;    % m/s rated wind speed

% TES parameters 
params.TES.LF_min  = 0.05;  % minimum load factor
params.TES.LF_max  = 1.0;   % maximum load factor
params.TES.eta_ch  = 1.0;   % charging efficiency
params.TES.eta_dis = 1.0;   % discharging efficiency
params.TES.P_max   = 10;    % MWth max charge/discharge power
params.TES.sigma   = 0;     % heat loss rate

% HTSE / SOEC parameters 
params.HTSE.K_th          = 1000 / 11.15 ;  % kg H2/MWhth
params.HTSE.K_e           = 1000 / 35.1 ;   % kg H2/MWhe
params.HTSE.P_e_min_ratio = 0.15;          % SOEC minimum power = 15% × P_SOEC

%% ====================== SECTION 2: ECONOMIC PARAMETERS ======================

params.econ.PV_inv_cost  = 1040;  % $/kW
params.econ.PV_OM_cost   = 9;     % $/kW/yr
params.econ.PV_lifetime  = 20;    % years

params.econ.WT_inv_cost  = 1695;  % $/kW
params.econ.WT_OM_cost   = 51;    % $/kW/yr
params.econ.WT_lifetime  = 20;    % years

params.econ.TES_inv_cost = 12;    % $/kWhth
params.econ.TES_OM_cost  = 0.24;  % $/kWhth/yr
params.econ.TES_lifetime = 20;    % years

params.econ.HTSE_inv_cost = 662;  % $/kW
params.econ.HTSE_OM_fixed = 58.69;% $/kW/yr
params.econ.HTSE_lifetime = 5;    % years

params.econ.H2_tank_inv      = 400;  % $/kg
params.econ.H2_tank_OM       = 15;   % $/kg/yr
params.econ.H2_tank_lifetime = 20;   % years

params.econ.smTMSR_OCC      = 3000;   % $/kWe
params.econ.smTMSR_FOM      = 90.26;  % $/kWe/yr
params.econ.smTMSR_VOM      = 2.17;   % $/MWhth
params.econ.smTMSR_fuel     = 0.381;  % $/MWhth
params.econ.smTMSR_decom    = 621;    % $/kWe
params.econ.smTMSR_lifetime = 60;     % years

params.econ.H2_price     = 2.75;    % $/kg
params.econ.H2_transport = 0.6909;  % $/kg

params.econ.nominal_discount_rate = 0.06;  % 6%
params.econ.inflation_rate        = 0.02;  % 2%
params.econ.project_lifetime      = 20;    % years

params.dt             = 1;     % hour
params.hours_per_year = 8760;

%% ====================== SECTION 3: COMPONENT MODELS ======================

function P_smTMSR_e = smTMSR_electric_power(P_smTMSR_th, eta_PB)
    P_smTMSR_e = P_smTMSR_th * eta_PB;
end

function [P_pv, T_cell] = PV_power_output(N_pv, G_Tot, T_a, p)
    T_cell = T_a + ((p.PV.NOCT - 20) / 800) * G_Tot;
    eta    = p.PV.eta_r * p.PV.eta_pc * (1 - p.PV.mu * (T_cell - p.PV.T_ref));
    P_pv   = max(0, eta * N_pv * p.PV.A * G_Tot / 1e6);  % MW
end

function P_wind = WT_power_output(V_anem, N_wt, p)
    v_hub = V_anem * log(p.WT.z_hub / p.WT.z0) / log(p.WT.z_anem / p.WT.z0);

    if v_hub < p.WT.v_ci || v_hub >= p.WT.v_co
        P_single = 0;
    elseif v_hub < p.WT.v_r
        P_single = p.WT.P_rated * (v_hub^3 - p.WT.v_ci^3) / (p.WT.v_r^3 - p.WT.v_ci^3);
    else
        P_single = p.WT.P_rated;
    end
    P_wind = N_wt * P_single;
end

function [Q_TES, LF_TES, WEN_overflow] = TES_update_with_waste(Q_TES_prev, P_TES_ch, C_TES, dt, p)
    eta_ch   = p.TES.eta_ch;
    P_max    = p.TES.P_max;
    Q_max    = p.TES.LF_max * C_TES;
    Q_loss   = Q_TES_prev * (1 - p.TES.sigma);

    P_TES_ch       = max(0, min(P_TES_ch, P_max));
    Q_after_charge = min(Q_loss + P_TES_ch * eta_ch * dt, Q_max);
    WEN_overflow   = max(0, (Q_loss + P_TES_ch * eta_ch * dt) - Q_after_charge);

    Q_TES  = Q_after_charge;
    LF_TES = Q_TES / Q_max;
end

function [Q_TES, LF_TES] = TES_update(Q_TES_prev, P_TES_ch, P_TES_dis, C_TES, dt, p)
    eta_ch  = p.TES.eta_ch;
    eta_dis = p.TES.eta_dis;
    P_max   = p.TES.P_max;
    Q_min   = p.TES.LF_min * C_TES;
    Q_max   = p.TES.LF_max * C_TES;
    Q_loss  = Q_TES_prev * (1 - p.TES.sigma);

    P_TES_ch  = max(0, min(P_TES_ch,  P_max));
    P_TES_dis = max(0, min(P_TES_dis, P_max));

    if P_TES_ch > 0
        Q_TES = min(Q_loss + P_TES_ch * eta_ch * dt, Q_max);
    elseif P_TES_dis > 0
        max_dis_power = max(0, Q_loss - Q_min) / dt;
        P_TES_dis     = min(P_TES_dis, max_dis_power);
        Q_TES         = max(Q_loss - (P_TES_dis / eta_dis) * dt, Q_min);
    else
        Q_TES = Q_loss;
    end

    Q_TES  = max(Q_min, min(Q_max, Q_TES));
    LF_TES = Q_TES / Q_max;
end

%% ====================== SECTION 4: ECONOMIC FUNCTIONS ======================

function real_i = calculate_real_interest_rate(nominal_i, f)
    real_i = (nominal_i - f) / (1 + f);
end

function CRF = calculate_CRF(i, n)
    CRF = i * (1 + i)^n / ((1 + i)^n - 1);
end

function PW = present_worth(F, i, n)
    PW = F / (1 + i)^n;
end

function NPC = calculate_component_NPC(Cap, OM_annual, i, lifetime, project_lifetime)
    NPC = Cap;
    for year = 1:min(lifetime, project_lifetime)
        NPC = NPC + present_worth(OM_annual, i, year);
    end
end

function NPC = calculate_component_with_replacement(Cap, OM_annual, REP_cost, REP_interval, i, project_lifetime)
    NPC = Cap;
    for year = 1:project_lifetime
        NPC = NPC + present_worth(OM_annual, i, year);
    end
    for year = REP_interval:REP_interval:project_lifetime
        NPC = NPC + present_worth(REP_cost, i, year);
    end
end

function NPC_smTMSR = calculate_smTMSR_NPC(p, i, project_lifetime)
    P_e         = p.smTMSR.P_e;
    capacity_kWe = P_e * 1000;   

    OCC       = capacity_kWe * p.econ.smTMSR_OCC;
    FOM       = capacity_kWe * p.econ.smTMSR_FOM;
    VOM       = p.smTMSR.P_th * p.hours_per_year * p.econ.smTMSR_VOM;
    FC        = p.smTMSR.P_th * p.hours_per_year * p.econ.smTMSR_fuel;
    annual_OM = FOM + VOM + FC;

    PW_OM = 0;
    for year = 1:project_lifetime
        PW_OM = PW_OM + present_worth(annual_OM, i, year);
    end

    if p.econ.smTMSR_lifetime <= project_lifetime
        PW_decom = present_worth(capacity_kWe * p.econ.smTMSR_decom, i, p.econ.smTMSR_lifetime);
    else
        PW_decom = 0;
    end

    NPC_smTMSR = OCC + PW_OM + PW_decom;
end

function TNPC = calculate_TNPC(config, p, total_H2_kg, mode)
    i = calculate_real_interest_rate(p.econ.nominal_discount_rate, p.econ.inflation_rate);

    PV_kW  = config.N_pv * p.PV.P_rated / 1000;
    PV_NPC = calculate_component_NPC(PV_kW * p.econ.PV_inv_cost, PV_kW * p.econ.PV_OM_cost, ...
                                     i, p.econ.PV_lifetime, p.econ.project_lifetime);

    WT_kW  = config.N_wt * p.WT.P_rated * 1000;
    WT_NPC = calculate_component_NPC(WT_kW * p.econ.WT_inv_cost, WT_kW * p.econ.WT_OM_cost, ...
                                     i, p.econ.WT_lifetime, p.econ.project_lifetime);

    TES_kWh = config.C_TES * 1000;
    TES_NPC = calculate_component_NPC(TES_kWh * p.econ.TES_inv_cost, TES_kWh * p.econ.TES_OM_cost, ...
                                      i, p.econ.TES_lifetime, p.econ.project_lifetime);

    if mode > 1 && config.P_SOEC > 0
        HTSE_kW  = config.P_SOEC * 1000;
        HTSE_NPC = calculate_component_with_replacement(HTSE_kW * p.econ.HTSE_inv_cost, ...
                       HTSE_kW * p.econ.HTSE_OM_fixed, HTSE_kW * p.econ.HTSE_inv_cost, ...
                       p.econ.HTSE_lifetime, i, p.econ.project_lifetime);
    else
        HTSE_NPC = 0;
    end

    if mode > 1 && total_H2_kg > 0
        H2_tank_kg  = total_H2_kg / 365;
        H2_tank_NPC = calculate_component_NPC(H2_tank_kg * p.econ.H2_tank_inv, ...
                          H2_tank_kg * p.econ.H2_tank_OM, i, p.econ.H2_tank_lifetime, ...
                          p.econ.project_lifetime);
    else
        H2_tank_NPC = 0;
    end

    smTMSR_NPC = calculate_smTMSR_NPC(p, i, p.econ.project_lifetime);
    TNPC = PV_NPC + WT_NPC + TES_NPC + HTSE_NPC + H2_tank_NPC + smTMSR_NPC;
end

function LCOE = calculate_LCOE(config, p, total_H2_kg, total_DPS, total_grid, mode)
    i    = calculate_real_interest_rate(p.econ.nominal_discount_rate, p.econ.inflation_rate);
    TNPC = calculate_TNPC(config, p, total_H2_kg, mode);
    CRF  = calculate_CRF(i, p.econ.project_lifetime);

    if mode > 1 && total_H2_kg > 0
        H2_revenue = total_H2_kg * (p.econ.H2_price - p.econ.H2_transport);
    else
        H2_revenue = 0;
    end

    E_load = (total_grid - total_DPS) * 1000;  % kWh/year
    LCOE   = (TNPC * CRF - H2_revenue) / E_load;
end

%% ====================== SECTION 5: MODE 1 ======================
% Renewable energy prioritized; smTMSR compensates deficit; TES buffers nuclear thermal.

function [DPS, WER, WEN, H2_kg_hour, LF, Q] = Mode1(P_RE, P_nuc, P_grid, Q, C, p)
    DPS = 0; WER = 0; WEN = 0; H2_kg_hour = 0;
    dt  = p.dt;

    if P_RE > P_grid
        WER          = (P_RE - P_grid) * dt;
        P_nuc_th_rem = p.smTMSR.P_th;
        [Q, LF, WEN] = TES_update_with_waste(Q, P_nuc_th_rem, C, dt, p);

    elseif P_RE < P_grid
        deficit = P_grid - P_RE;

        if P_nuc > deficit
            P_nuc_th_rem = p.smTMSR.P_th - deficit / p.smTMSR.eta_PB;
            [Q, LF, WEN] = TES_update_with_waste(Q, max(0, P_nuc_th_rem), C, dt, p);

        elseif abs(P_nuc - deficit) < 1e-6
            [Q, LF] = TES_update(Q, 0, 0, C, dt, p);

        else
            need    = deficit - P_nuc;
            need_th = need / p.smTMSR.eta_PB;

            % max_dis_power = min(p.TES.P_max, max(0, Q - p.TES.LF_min * C) / dt);
            max_dis_power = max(0, Q - p.TES.LF_min * C) / dt;

            if max_dis_power >= need_th - 1e-6
                P_dis = need_th;
            else
                P_dis = max_dis_power;
                DPS   = (need - P_dis * p.smTMSR.eta_PB) * dt;
            end

            P_dis   = min(P_dis, p.TES.P_max);
            [Q, LF] = TES_update(Q, 0, P_dis, C, dt, p);
        end

    else
        [Q, LF, WEN] = TES_update_with_waste(Q, p.smTMSR.P_th, C, dt, p);
    end
end

%% ====================== SECTION 6: MODE 2 ======================
% Demand split by DF: RE covers DF×P_grid, nuclear covers (1-DF)×P_grid.
% Surplus from both sides feeds SOEC for H2 production using Eq. (9).

function [DPS, WER, WEN, H2_kg_hour, LF, Q] = Mode2(P_RE, P_nuc, P_grid, Q, C, P_SOEC, P_RE_max, DF, p)
   
    DPS = 0; WER = 0; WEN = 0; H2_kg_hour = 0;
    dt = p.dt;

    P_grid1        = DF * P_grid;
    P_grid2        = (1 - DF) * P_grid;
    SOEC_min_power = p.HTSE.P_e_min_ratio * P_SOEC;
    SOEC_max_power = P_SOEC;

    if P_RE > P_grid1 && P_nuc > P_grid2
        % Case 1: Both RE and nuclear have surplus → feed SOEC
        P_RE_surplus     = P_RE - P_grid1;
        P_nuc_surplus_e  = P_nuc - P_grid2;
        P_nuc_surplus_th = P_nuc_surplus_e / p.smTMSR.eta_PB;

        P_e_htse  = min(P_RE_surplus, SOEC_max_power);
        P_th_htse = min(P_nuc_surplus_th, SOEC_max_power / p.smTMSR.eta_PB);

        if P_e_htse >= SOEC_min_power && P_th_htse >= SOEC_min_power / p.smTMSR.eta_PB
            % Eq. (9): H2 production limited by minimum of electrical and thermal inputs
            H2_from_e  = p.HTSE.K_e * P_e_htse;
            H2_from_th = p.HTSE.K_th * P_th_htse;
            H2_kg_hour = min(H2_from_e, H2_from_th);

            P_e_used  = H2_kg_hour / p.HTSE.K_e;
            P_th_used = H2_kg_hour / p.HTSE.K_th;

            WER          = max(0, P_RE_surplus - P_e_used) * dt;
            P_nuc_th_rem = max(0, P_nuc_surplus_th - P_th_used);

            if P_nuc_th_rem > 1e-6
                [Q, LF, WEN] = TES_update_with_waste(Q, P_nuc_th_rem, C, dt, p);
            else
                LF  = Q / C;
                WEN = 0;
            end
        else
            WER          = P_RE_surplus * dt;
            [Q, LF, WEN] = TES_update_with_waste(Q, P_nuc_surplus_th, C, dt, p);
        end

    elseif P_RE > P_grid1 && P_nuc < P_grid2
        % Case 2: RE surplus but nuclear deficit
        P_RE_surplus    = P_RE - P_grid1;
        nuclear_deficit = P_grid2 - P_nuc;

        if P_RE_surplus >= nuclear_deficit
            WER     = (P_RE_surplus - nuclear_deficit) * dt;
            [Q, LF] = TES_update(Q, 0, 0, C, dt, p);
        else
            need    = nuclear_deficit - P_RE_surplus;
            need_th = need / p.smTMSR.eta_PB;

            max_dis_power = max(0, Q - p.TES.LF_min * C) / dt;
            % max_dis_power = min(p.TES.P_max, max(0, Q - p.TES.LF_min * C) / dt);
            if max_dis_power >= need_th - 1e-6
                P_dis = need_th;
            else
                P_dis = max_dis_power;
                DPS   = (need - P_dis * p.smTMSR.eta_PB) * dt;
            end
            P_dis   = min(P_dis, p.TES.P_max);
            [Q, LF] = TES_update(Q, 0, P_dis, C, dt, p);
        end

    elseif P_RE < P_grid1 && P_nuc > P_grid2
        % Case 3: RE deficit but nuclear surplus
        RE_deficit      = P_grid1 - P_RE;
        P_nuc_surplus_e = P_nuc - P_grid2;

        if P_nuc_surplus_e >= RE_deficit
            excess_th = (P_nuc_surplus_e - RE_deficit) / p.smTMSR.eta_PB;
            if excess_th > 1e-6
                [Q, LF, WEN] = TES_update_with_waste(Q, excess_th, C, dt, p);
            else
                [Q, LF] = TES_update(Q, 0, 0, C, dt, p);
            end
        else
            need    = RE_deficit - P_nuc_surplus_e;
            need_th = need / p.smTMSR.eta_PB;

            max_dis_power = max(0, Q - p.TES.LF_min * C) / dt;
            % max_dis_power = min(p.TES.P_max, max(0, Q - p.TES.LF_min * C) / dt);

            if max_dis_power >= need_th - 1e-6
                P_dis = need_th;
            else
                P_dis = max_dis_power;
                DPS   = (need - P_dis * p.smTMSR.eta_PB) * dt;
            end
            P_dis   = min(P_dis, p.TES.P_max);
            [Q, LF] = TES_update(Q, 0, P_dis, C, dt, p);
        end

    elseif P_RE < P_grid1 && P_nuc < P_grid2
        % Case 4: Both RE and nuclear in deficit → TES discharges
        total_deficit = (P_grid1 - P_RE) + (P_grid2 - P_nuc);
        need_th       = total_deficit / p.smTMSR.eta_PB;

        max_dis_power = max(0, Q - p.TES.LF_min * C) / dt;
        % max_dis_power = min(p.TES.P_max, max(0, Q - p.TES.LF_min * C) / dt);

        if max_dis_power >= need_th - 1e-6
            P_dis = need_th;
        else
            P_dis = max_dis_power;
            DPS   = (total_deficit - P_dis * p.smTMSR.eta_PB) * dt;
        end
        P_dis   = min(P_dis, p.TES.P_max);
        [Q, LF] = TES_update(Q, 0, P_dis, C, dt, p);

    else
        [Q, LF] = TES_update(Q, 0, 0, C, dt, p);
    end
end

%% ====================== SECTION 7: MODE 3 ======================
% HTSE with TES limiting condition: H2 production only when TES LF >= TES_factor.

function [DPS, WER, WEN, H2_kg_hour, LF, Q] = Mode3(P_RE, P_nuc, P_grid, Q, C, P_SOEC, P_RE_max, DF, TES_factor, p)
    if (Q / C) >= TES_factor
        [DPS, WER, WEN, H2_kg_hour, LF, Q] = Mode2(P_RE, P_nuc, P_grid, Q, C, P_SOEC, P_RE_max, DF, p);
    else
        [DPS, WER, WEN, H2_kg_hour, LF, Q] = Mode1(P_RE, P_nuc, P_grid, Q, C, p);
    end
end

%% ====================== SECTION 8: LOAD INPUT DATA ======================

fprintf('========================================\n');
fprintf('NRHES SIMULATION MODEL\n');
fprintf('========================================\n\n');

fprintf('Loading InputData.xlsx...\n');
if ~exist('InputData.xlsx', 'file')
    error('InputData.xlsx not found in working directory.');
end

data_raw = readtable('InputData.xlsx');
hours  = data_raw.hours(:);
G_Tot  = data_raw.G_Tot(:);
T_a    = data_raw.T_a(:);
V_anem = data_raw.V_anem(:);
P_grid = data_raw.P_grid(:);

T_sim = length(hours);
fprintf('Loaded %d hours of data.\n', T_sim);

P_nuc = smTMSR_electric_power(params.smTMSR.P_th, params.smTMSR.eta_PB);
fprintf('smTMSR: P_th = %.0f MWth  |  P_e = %.0f MWe\n', params.smTMSR.P_th, P_nuc);

%% ====================== SECTION 9: CONFIGURATIONS ======================
% From Table 5: solutions with lowest EWP under DPSP < 1%

fprintf('\n--- Configurations from Table 5 ---\n');

cfg1.N_pv = 60423;  cfg1.C_TES = 2000; cfg1.N_wt = 1;
cfg1.P_SOEC = 0;    cfg1.DF = 0;       cfg1.TES_factor = 0;

cfg2.N_pv = 80000;  cfg2.C_TES = 2000;   cfg2.N_wt = 1;
cfg2.P_SOEC = 9.0870; cfg2.DF = 0.5843;  cfg2.TES_factor = 0;

cfg3.N_pv = 75054;  cfg3.C_TES = 250;    cfg3.N_wt = 1;
cfg3.P_SOEC = 9.5445; cfg3.DF = 0.3245;  cfg3.TES_factor = 0.8048;

%% ====================== SECTION 10: RUN SIMULATIONS ======================

fprintf('\n--- Computing renewable energy output ---\n');

P_RE1 = zeros(1, T_sim);
P_RE2 = zeros(1, T_sim);
P_RE3 = zeros(1, T_sim);

for t = 1:T_sim
    [P_pv1, ~] = PV_power_output(cfg1.N_pv, G_Tot(t), T_a(t), params);
    P_RE1(t)   = P_pv1 + WT_power_output(V_anem(t), cfg1.N_wt, params);

    [P_pv2, ~] = PV_power_output(cfg2.N_pv, G_Tot(t), T_a(t), params);
    P_RE2(t)   = P_pv2 + WT_power_output(V_anem(t), cfg2.N_wt, params);

    [P_pv3, ~] = PV_power_output(cfg3.N_pv, G_Tot(t), T_a(t), params);
    P_RE3(t)   = P_pv3 + WT_power_output(V_anem(t), cfg3.N_wt, params);
end

P_RE_max2 = max(P_RE2);
P_RE_max3 = max(P_RE3);

% Mode 1
fprintf('Running Mode 1...\n');
res = struct();
Q_TES = params.TES.LF_min * cfg1.C_TES;
res(1).DPS = zeros(1,T_sim); res(1).WER = zeros(1,T_sim);
res(1).WEN = zeros(1,T_sim); res(1).H2_kg_hour = zeros(1,T_sim);
res(1).LF  = zeros(1,T_sim);
for t = 1:T_sim
    [res(1).DPS(t), res(1).WER(t), res(1).WEN(t), res(1).H2_kg_hour(t), res(1).LF(t), Q_TES] = ...
        Mode1(P_RE1(t), P_nuc, P_grid(t), Q_TES, cfg1.C_TES, params);
end

% Mode 2
fprintf('Running Mode 2...\n');
Q_TES = params.TES.LF_min * cfg2.C_TES;
res(2).DPS = zeros(1,T_sim); res(2).WER = zeros(1,T_sim);
res(2).WEN = zeros(1,T_sim); res(2).H2_kg_hour = zeros(1,T_sim);
res(2).LF  = zeros(1,T_sim);
for t = 1:T_sim
    [res(2).DPS(t), res(2).WER(t), res(2).WEN(t), res(2).H2_kg_hour(t), res(2).LF(t), Q_TES] = ...
        Mode2(P_RE2(t), P_nuc, P_grid(t), Q_TES, cfg2.C_TES, cfg2.P_SOEC, P_RE_max2, cfg2.DF, params);
end

% Mode 3
fprintf('Running Mode 3...\n');
Q_TES = params.TES.LF_min * cfg3.C_TES;
res(3).DPS = zeros(1,T_sim); res(3).WER = zeros(1,T_sim);
res(3).WEN = zeros(1,T_sim); res(3).H2_kg_hour = zeros(1,T_sim);
res(3).LF  = zeros(1,T_sim);
for t = 1:T_sim
    [res(3).DPS(t), res(3).WER(t), res(3).WEN(t), res(3).H2_kg_hour(t), res(3).LF(t), Q_TES] = ...
        Mode3(P_RE3(t), P_nuc, P_grid(t), Q_TES, cfg3.C_TES, cfg3.P_SOEC, P_RE_max3, cfg3.DF, cfg3.TES_factor, params);
end

for mode = 1:3
    res(mode).H2_kg_cum = cumsum(res(mode).H2_kg_hour) * params.dt;
end

%% ====================== SECTION 11: PERFORMANCE METRICS ======================

fprintf('\n--- Computing performance metrics ---\n');

total_grid    = sum(P_grid);
total_nuclear = P_nuc * T_sim * params.dt;

for mode = 1:3
    res(mode).total_H2_kg = sum(res(mode).H2_kg_hour) * params.dt;
    res(mode).total_DPS   = sum(res(mode).DPS);
    res(mode).total_WEN   = sum(res(mode).WEN);
    res(mode).total_WER   = sum(res(mode).WER);
    res(mode).avg_LF      = mean(res(mode).LF);

    switch mode
        case 1;  res(mode).total_RE = sum(P_RE1);
        case 2;  res(mode).total_RE = sum(P_RE2);
        case 3;  res(mode).total_RE = sum(P_RE3);
    end

    res(mode).DPSP = res(mode).total_DPS / total_grid;

    wasted_MWhe = res(mode).total_WER + res(mode).total_WEN * params.smTMSR.eta_PB;
    res(mode).EWP = wasted_MWhe / (res(mode).total_RE + total_nuclear);

    switch mode
        case 1;  res(mode).LCOE = calculate_LCOE(cfg1, params, res(mode).total_H2_kg, res(mode).total_DPS, total_grid, mode);
        case 2;  res(mode).LCOE = calculate_LCOE(cfg2, params, res(mode).total_H2_kg, res(mode).total_DPS, total_grid, mode);
        case 3;  res(mode).LCOE = calculate_LCOE(cfg3, params, res(mode).total_H2_kg, res(mode).total_DPS, total_grid, mode);
    end
end

% Print summary
fprintf('\n%s\n', repmat('=', 1, 95));
fprintf('%-8s  %8s  %8s  %12s  %14s  %10s  %12s\n', ...
    'Mode','DPSP','EWP','LCOE($/kWh)','H2(tonnes)','DPS(MWhe)','Avg LF');
fprintf('%s\n', repmat('-', 1, 95));
labels = {'Mode 1','Mode 2','Mode 3'};
for m = 1:3
    fprintf('%-8s  %8.4f  %8.4f  %12.4f  %14.1f  %10.1f  %12.3f\n', labels{m}, ...
        res(m).DPSP, res(m).EWP, res(m).LCOE, res(m).total_H2_kg/1000, ...
        res(m).total_DPS, res(m).avg_LF);
end
fprintf('%s\n', repmat('=', 1, 95));

fprintf('\nPaper Table 5 targets:\n');
fprintf('  Mode 1: DPSP=0.0097  EWP=0.0402  LCOE=$0.0940/kWh\n');
fprintf('  Mode 2: DPSP=0.0090  EWP=0.0380  LCOE=$0.1162/kWh  H2=138.0 tonnes\n');
fprintf('  Mode 3: DPSP=0.0090  EWP=0.0062  LCOE=$0.0931/kWh  H2=98.8 tonnes\n');

%% ====================== SECTION 12: FIGURES ======================

% Figure 1: Annual input data
fig1 = figure('Position', [50, 50, 1400, 1000]);
subplot(4,1,1); plot(hours, G_Tot,   'b-', 'LineWidth', 0.8);
  ylabel('Solar Radiation (W/m^2)'); grid on; xlim([0,8760]); 
  title('(a) Hourly Solar Irradiance');
subplot(4,1,2); plot(hours, T_a,     'r-', 'LineWidth', 0.8);
  ylabel('Temperature (\circC)');    grid on; xlim([0,8760]); 
  title('(b) Hourly Ambient Temperature');
subplot(4,1,3); plot(hours, V_anem,  'g-', 'LineWidth', 0.8);
  ylabel('Wind Speed (m/s)');        grid on; xlim([0,8760]); 
  title('(c) Hourly Wind Speed');
subplot(4,1,4); plot(hours, P_grid,  'm-', 'LineWidth', 0.8);
  xlabel('Time (Hours)'); ylabel('Grid Demand (MW)'); grid on; xlim([0,8760]);
  title('(d) Hourly Grid Power Demand');
sgtitle('Figure 1: Annual Hourly Input Data — Wuwei City, Gansu', ...
    'FontSize', 14, 'FontWeight', 'bold');
saveas(fig1, 'Figure1_Annual_Data.png');

% Figure 2: Mode 1
fig2 = figure('Position', [50, 50, 1400, 800]);
subplot(3,1,1);
  plot(hours, res(1).LF, 'b-', 'LineWidth', 0.5);
  yline(0.05, 'r--', 'LF_{min}'); yline(1.0, 'r--', 'LF_{max}');
  ylabel('TES Load Factor'); grid on; xlim([0,8760]); ylim([0,1.05]);
  title(sprintf('Mode 1 TES LF  |  DPSP=%.4f  EWP=%.4f  LCOE=$%.4f/kWh', ...
      res(1).DPSP, res(1).EWP, res(1).LCOE));
subplot(3,1,2);
  idx = find(res(1).DPS > 0.001);
  if ~isempty(idx); stem(hours(idx), res(1).DPS(idx), 'r.', 'MarkerSize', 2); end
  ylabel('DPS (MWhe)'); grid on; xlim([0,8760]);
  title(sprintf('Deficiency of Power Supply (Total: %.1f MWhe)', res(1).total_DPS));
subplot(3,1,3);
  idx = find(res(1).WEN > 0.001);
  if ~isempty(idx); stem(hours(idx), res(1).WEN(idx), 'g.', 'MarkerSize', 2); end
  xlabel('Time (Hours)'); ylabel('WEN (MWhth)'); grid on; xlim([0,8760]);
  title(sprintf('Wasted Nuclear Energy (Total: %.1f MWhth)', res(1).total_WEN));
sgtitle('Figure 2: Mode 1 Results', 'FontSize', 14, 'FontWeight', 'bold');
saveas(fig2, 'Figure2_Mode1_Results.png');

% Figure 3: Mode 2
fig3 = figure('Position', [100, 100, 1400, 1000]);
subplot(5,1,1);
  plot(hours, res(2).H2_kg_hour, 'b-', 'LineWidth', 0.5);
  ylabel('H_2 (kg/h)'); grid on; xlim([0,8760]);
  title(sprintf('H_2 Production Rate (Total: %.0f kg = %.1f tonnes)', ...
      res(2).total_H2_kg, res(2).total_H2_kg/1000));
subplot(5,1,2);
  plot(hours, res(2).LF, 'b-', 'LineWidth', 0.5);
  yline(0.05,'r--','LF_{min}'); yline(1.0,'r--','LF_{max}');
  ylabel('TES LF'); grid on; xlim([0,8760]); ylim([0,1.05]);
  title(sprintf('Mode 2 TES LF  |  DPSP=%.4f  EWP=%.4f  LCOE=$%.4f/kWh', ...
      res(2).DPSP, res(2).EWP, res(2).LCOE));
subplot(5,1,3);
  idx = find(res(2).DPS > 0.001);
  if ~isempty(idx); stem(hours(idx), res(2).DPS(idx), 'r.', 'MarkerSize', 2); end
  ylabel('DPS (MWhe)'); grid on; xlim([0,8760]);
  title(sprintf('DPS (Total: %.1f MWhe)', res(2).total_DPS));
subplot(5,1,4);
  idx = find(res(2).WEN > 0.001);
  if ~isempty(idx); stem(hours(idx), res(2).WEN(idx), 'g.', 'MarkerSize', 2); end
  ylabel('WEN (MWhth)'); grid on; xlim([0,8760]);
  title(sprintf('WEN (Total: %.1f MWhth)', res(2).total_WEN));
subplot(5,1,5);
  idx = find(res(2).WER > 0.001);
  if ~isempty(idx); stem(hours(idx), res(2).WER(idx), 'b.', 'MarkerSize', 2); end
  xlabel('Time (Hours)'); ylabel('WER (MWhe)'); grid on; xlim([0,8760]);
  title(sprintf('WER (Total: %.1f MWhe)', res(2).total_WER));
sgtitle('Figure 3: Mode 2 Results', 'FontSize', 14, 'FontWeight', 'bold');
saveas(fig3, 'Figure3_Mode2_Results.png');

% Figure 4: Mode 3
fig4 = figure('Position', [150, 150, 1400, 1000]);
subplot(5,1,1);
  plot(hours, res(3).H2_kg_hour, 'b-', 'LineWidth', 0.5);
  ylabel('H_2 (kg/h)'); grid on; xlim([0,8760]);
  title(sprintf('H_2 Production Rate (Total: %.0f kg = %.1f tonnes)', ...
      res(3).total_H2_kg, res(3).total_H2_kg/1000));
subplot(5,1,2);
  plot(hours, res(3).LF, 'b-', 'LineWidth', 0.5); hold on;
  yline(cfg3.TES_factor, 'k--', 'LineWidth', 1.5); hold off;
  yline(0.05,'r:','LF_{min}'); yline(1.0,'r:','LF_{max}');
  ylabel('TES LF'); grid on; xlim([0,8760]); ylim([0,1.05]);
  title(sprintf('Mode 3 TES LF  |  DPSP=%.4f  EWP=%.4f  LCOE=$%.4f/kWh  TES_{factor}=%.4f', ...
      res(3).DPSP, res(3).EWP, res(3).LCOE, cfg3.TES_factor));
subplot(5,1,3);
  idx = find(res(3).DPS > 0.001);
  if ~isempty(idx); stem(hours(idx), res(3).DPS(idx), 'r.', 'MarkerSize', 2); end
  ylabel('DPS (MWhe)'); grid on; xlim([0,8760]);
  title(sprintf('DPS (Total: %.1f MWhe)', res(3).total_DPS));
subplot(5,1,4);
  idx = find(res(3).WEN > 0.001);
  if ~isempty(idx); stem(hours(idx), res(3).WEN(idx), 'g.', 'MarkerSize', 2); end
  ylabel('WEN (MWhth)'); grid on; xlim([0,8760]);
  title(sprintf('WEN (Total: %.1f MWhth)', res(3).total_WEN));
subplot(5,1,5);
  idx = find(res(3).WER > 0.001);
  if ~isempty(idx); stem(hours(idx), res(3).WER(idx), 'b.', 'MarkerSize', 2); end
  xlabel('Time (Hours)'); ylabel('WER (MWhe)'); grid on; xlim([0,8760]);
  title(sprintf('WER (Total: %.1f MWhe)', res(3).total_WER));
sgtitle('Figure 4: Mode 3 Results', 'FontSize', 14, 'FontWeight', 'bold');
saveas(fig4, 'Figure4_Mode3_Results.png');

%% ====================== SECTION 13: SAVE RESULTS ======================

save('NRHES_Results.mat', 'res', 'cfg1', 'cfg2', 'cfg3', 'params', 'hours');

h2_table = table(hours, ...
    res(2).H2_kg_hour', res(2).H2_kg_cum', ...
    res(3).H2_kg_hour', res(3).H2_kg_cum', ...
    'VariableNames', {'Hour','Mode2_H2_kg_per_hour','Mode2_H2_kg_cumulative', ...
                      'Mode3_H2_kg_per_hour','Mode3_H2_kg_cumulative'});
writetable(h2_table, 'Hydrogen_Production_Data.csv');

results_table = table({'Mode1';'Mode2';'Mode3'}, ...
    [res(1).LCOE; res(2).LCOE; res(3).LCOE], ...
    [res(1).DPSP; res(2).DPSP; res(3).DPSP], ...
    [res(1).EWP;  res(2).EWP;  res(3).EWP], ...
    [res(1).total_H2_kg/1000; res(2).total_H2_kg/1000; res(3).total_H2_kg/1000], ...
    [res(1).total_DPS; res(2).total_DPS; res(3).total_DPS], ...
    [res(1).total_WEN; res(2).total_WEN; res(3).total_WEN], ...
    [res(1).total_WER; res(2).total_WER; res(3).total_WER], ...
    [res(1).avg_LF; res(2).avg_LF; res(3).avg_LF], ...
    'VariableNames', {'Mode','LCOE_USD_per_kWh','DPSP','EWP', ...
                      'H2_Production_tonnes','DPS_MWhe','WEN_MWhth','WER_MWhe','Avg_LF'});
writetable(results_table, 'NRHES_Economic_Results.csv');

fprintf('\n=== SIMULATION COMPLETE ===\n');
fprintf('Results saved to:\n');
fprintf('  - NRHES_Results.mat\n');
fprintf('  - Hydrogen_Production_Data.csv\n');
fprintf('  - NRHES_Economic_Results.csv\n');
fprintf('  - Figure1_Annual_Data.png\n');
fprintf('  - Figure2_Mode1_Results.png\n');
fprintf('  - Figure3_Mode2_Results.png\n');
fprintf('  - Figure4_Mode3_Results.png\n');