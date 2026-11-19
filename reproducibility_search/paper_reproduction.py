"""Independent models of Nie 2025 HTSE and Nie 2024 battery/TES, plus grid search.

No previous experiment module is imported. Only the teacher comparison imports
NRHES_Model.py. Excel values are immutable and never calibrated or reordered.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import importlib.util
import itertools
import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import numpy as np
import pandas as pd
from numba import njit
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent / "teacher_implementation"
PAPERS = HERE.parent / "paper"
EARLY_RUNS = HERE.parent / "early_runs"
METRICS = ("DPSP", "EWP", "LCOE")
FIELDS = ("Hour Load RE Q LF B SOC H2 DPS WER WEN TES_charge TES_discharge "
          "BS_charge BS_discharge H2_e H2_th H2_e_unmet H2_th_unmet HTSE_violation "
          "electric_residual thermal_residual TES_residual BS_residual total_residual").split()
# kg H2 per MWh of electricity / heat, power block efficiency
KE, KTH, ETA = 1000/35.1, 1000/11.15, .4


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save_json(path, value):
    def convert(x):
        if isinstance(x, np.generic):
            return x.item()
        if isinstance(x, np.ndarray):
            return x.tolist()
        raise TypeError(type(x).__name__)
    Path(path).write_text(json.dumps(value, indent=2, default=convert, allow_nan=False)+"\n")


def original_hashes():
    # teacher files and both papers, checked before and after runs
    names = ("NRHES_Model.py", "NRHES_Model.m", "InputData.xlsx",
             "1 - Techno-economic assessment of a smTMSR-based nuclear–renewable.pdf",
             "2 - Multi-objective capacity configuration optimization of a nuclear-renewable hybrid system.pdf")
    return {n:digest((PAPERS if n.endswith(".pdf") else ROOT)/n) for n in names}


@dataclass(frozen=True)
class Physics:
    tes_charge_mwth: float = 25
    tes_discharge_mwth: float = 25
    tes_rating: str = "absolute"
    pv_model: str = "area_efficiency"
    reactor_2025_mwth: float = 25
    inactive_soec: str = "keep_split"
    soc_switch: str = "start"
    hydrogen_formula: str = "minimum"

    def __post_init__(self):
        if not np.isfinite([self.tes_charge_mwth,self.tes_discharge_mwth]).all() or min(self.tes_charge_mwth,self.tes_discharge_mwth)<=0:
            raise ValueError("TES ratings must be finite and positive.")
        choices = {
            "tes_rating":("absolute","charge_scaled","discharge_scaled","both_scaled"),
            "pv_model":("area_efficiency","nameplate_150W"), "reactor_2025_mwth":(25,50),
            "inactive_soec":("keep_split","renewables_first"), "soc_switch":("start","post_guard"),
            "hydrogen_formula":("minimum","additive_diagnostic"),
        }
        for name, allowed in choices.items():
            if getattr(self,name) not in allowed:
                raise ValueError(f"Unsupported {name}: {getattr(self,name)}")


@dataclass(frozen=True)
class Finance:
    # teacher's 6% nominal and 2% inflation, i.e. 3.92% real
    real_rate: float = (.06-.02)/1.02
    years: int = 20
    variable_basis: str = "thermal"
    load_basis: str = "served"
    balance_of_plant: str = "embedded"
    decommissioning: str = "retirement"

    def __post_init__(self):
        if not np.isfinite(self.real_rate) or self.real_rate<0:
            raise ValueError("Invalid real rate.")
        if isinstance(self.years,bool) or not isinstance(self.years,int) or not 1<=self.years<=60:
            raise ValueError("Project years must be an integer in [1,60].")
        for name, allowed in {
            "variable_basis":("thermal","electric"), "load_basis":("served","requested"),
            "balance_of_plant":("embedded","separate_rated_proxy"),
            "decommissioning":("retirement","project")
        }.items():
            if getattr(self,name) not in allowed:
                raise ValueError(f"Unsupported {name}")


@dataclass(frozen=True)
class Inputs:
    hours: np.ndarray
    solar: np.ndarray
    temperature: np.ndarray
    wind: np.ndarray
    load: np.ndarray

    def fingerprint(self):
        return hashlib.sha256(np.column_stack((self.hours,self.solar,self.temperature,self.wind,self.load)).tobytes()).hexdigest()


def load_inputs(path):
    frame = pd.read_excel(path)
    keys = ("hours","G_Tot","T_a","V_anem","P_grid")
    if not set(keys).issubset(frame.columns):
        raise ValueError("Missing required Excel columns.")
    arrays = [frame[k].to_numpy(dtype=float,copy=True) for k in keys]
    if len(frame)!=8760 or not np.array_equal(arrays[0],np.arange(1,8761)):
        raise ValueError("Require consecutive hours 1..8760.")
    if not all(np.isfinite(a).all() for a in arrays) or any((arrays[k]<0).any() for k in (1,3,4)) or arrays[4].sum()<=0:
        raise ValueError("Invalid hourly values.")
    for a in arrays:
        a.flags.writeable = False
    return Inputs(*arrays)


def renewables(inputs, pv_model):
    """Both papers Eqs 2-6, independently implemented in MW."""
    g, ambient = inputs.solar, inputs.temperature
    cell = ambient+(47.5-20)/800*g
    efficiency = .137*(1-.005*(cell-25))  # eta_pc=1 explicitly in both papers.
    panel = np.maximum(0,efficiency*(1.658*.834)*g/1e6)
    if pv_model=="nameplate_150W":
        panel *= 150/(.137*(1.658*.834)*1000)
    elif pv_model!="area_efficiency":
        raise ValueError("Unknown PV convention.")
    hub = inputs.wind*np.log(70/1.5)/np.log(10/1.5)
    turbine = np.zeros(len(hub))
    ramp = (hub>=3)&(hub<13)
    turbine[ramp] = 1.5*(hub[ramp]**3-3**3)/(13**3-3**3)
    turbine[(hub>=13)&(hub<22)] = 1.5
    return panel,turbine


def power_limits(case, physics):
    # scaled limits are relative to the 2000 MWh design
    ratio = case["tes"]/2000
    charge = physics.tes_charge_mwth*(ratio if physics.tes_rating in ("charge_scaled","both_scaled") else 1)
    discharge = physics.tes_discharge_mwth*(ratio if physics.tes_rating in ("discharge_scaled","both_scaled") else 1)
    return charge,discharge


@njit(cache=False)
def _dispatch(load, renewable, paper, mode, thermal, capacity, battery, soec, df,
              factor, charge_limit, discharge_limit, fallback, post_guard, additive):
    """Hourly source-specific allocation, separate stores and explicit residuals."""
    s = np.zeros((len(load),25))
    # TES min LF 5%, battery min SOC 15%
    qmin, bmin = .05*capacity, .15*battery
    q,b = qmin,bmin
    nuclear = .4*thermal
    for i in range(len(load)):
        demand,power,oldq,oldb = load[i],renewable[i],q,b
        # mode 3 only makes H2 while TES is above the TES factor
        active = paper==2025 and (mode==2 or (mode==3 and q/capacity>=factor))
        h=edraw=hdraw=bc=bd=0.0
        for attempt in range(2):
            if paper==2024:
                # Separate-source charging, as described in the battery paper:
                # nuclear covers demand first, excess RE can charge the battery.
                ng = min(nuclear,demand)
                rg = min(power,max(0.0,demand-ng))
            else:
                rg = min(power,demand)
                ng = min(nuclear,max(0.0,demand-rg))
            h=edraw=hdraw=0.0
            # DF splits demand between RE and nuclear while the SOEC runs
            if active and power>df*demand and nuclear>(1-df)*demand:
                rg,ng = df*demand,(1-df)*demand
                eleft,hleft = power-rg,thermal-ng/.4
                ealloc = min(eleft,soec)
                # SOEC needs at least 15% of rated power
                if soec>0 and ealloc>=.15*soec:
                    h = KE*ealloc+KTH*hleft if additive else min(KE*ealloc,KTH*hleft)
                    if h/KE<.15*soec:
                        h=0.0
                    edraw = min(h/KE,eleft,soec)
                    hdraw = min(h/KTH,hleft)
                if h==0 and fallback:
                    rg = min(power,demand)
                    ng = min(nuclear,max(0.0,demand-rg))
            remaining = max(0.0,thermal-ng/.4-hdraw)
            shortage = max(0.0,demand-rg-ng)
            tc=td=bc=bd=0.0
            if shortage>0:
                if paper==2024 and mode==2:
                    bd = min(shortage,10.0,max(0.0,b-bmin)*.96)
                    shortage -= bd
                td = min(shortage/.4,discharge_limit,max(0.0,q-qmin))
                shortage = max(0.0,shortage-.4*td)
                if paper==2024 and mode==1:
                    bd = min(shortage,10.0,max(0.0,b-bmin)*.96)
                    shortage = max(0.0,shortage-bd)
            else:
                tc = min(remaining,charge_limit,max(0.0,capacity-q))
                if paper==2024 and battery>0:
                    bc = min(max(0.0,power-rg),10.0,max(0.0,battery-b)/.96)
            qnext = q+tc-td
            # post_guard: redo the hour without H2 if TES would end below the factor
            if paper==2025 and mode==3 and post_guard and active and qnext<factor*capacity-1e-10:
                active=False
                continue
            break
        q = qnext
        b += .96*bc-bd/.96
        dps = max(0.0,demand-rg-ng-.4*td-bd)
        wer = max(0.0,power-rg-edraw-bc)
        wen = max(0.0,remaining-tc)
        battery_loss = bc*(1-.96)+bd*(1/.96-1)
        electricity = power+ng+.4*td+bd-(demand-dps+wer+bc+edraw)
        heat = thermal-ng/.4-hdraw-tc-wen
        tes_balance = q-oldq-tc+td
        bs_balance = b-oldb-.96*bc+bd/.96
        total = power+nuclear-(demand-dps+wer+.4*wen+edraw+.4*hdraw
                               +.4*(q-oldq)+b-oldb+battery_loss)
        values = (i+1.0,demand,power,q,q/capacity,b,b/battery if battery else 0.0,
                  h,dps,wer,wen,tc,td,bc,bd,edraw,hdraw,
                  max(0.0,h/KE-edraw),max(0.0,h/KTH-hdraw),
                  max(0.0,h/KE-soec),electricity,heat,tes_balance,bs_balance,total)
        for j in range(25):
            s[i,j]=values[j]
    return s


def audit(series, case, tolerance=1e-7):
    residuals = {k:float(np.abs(v).max()) for k,v in series.items() if k.endswith("_residual")}
    resources = max(float(series[k].max()) for k in ("H2_e_unmet","H2_th_unmet","HTSE_violation"))
    finite = all(np.isfinite(v).all() for v in series.values())
    nonnegative = all(series[k].min()>=-tolerance for k in
                      ("Q","B","H2","DPS","WER","WEN","TES_charge","TES_discharge","BS_charge","BS_discharge"))
    storage = bool(series["LF"].min()>=.05-tolerance and series["LF"].max()<=1+tolerance)
    if case["battery"]>0:
        storage &= bool(series["SOC"].min()>=.15-tolerance and series["SOC"].max()<=1+tolerance)
    # Table 2 bounds of the 2025 paper
    design = True
    if case["paper"]==2025:
        design = (40000<=case["pv"]<=80000 and 250<=case["tes"]<=2000 and case["wind"]==1)
        if case["mode"]>1:
            peak = float(series["RE"].max())
            design &= .01*peak<=case["soec"]<=peak+1e-10 and .2<=case["df"]<=1
        if case["mode"]==3:
            design &= .2<=case["tes_factor"]<=1
    return {
        "valid":bool(max(residuals.values())<=tolerance and resources<=tolerance
                     and finite and nonnegative and storage and design),
        "max_balance_residual_MWh":max(residuals.values()),"max_H2_resource_violation":resources,
        "finite":bool(finite),"nonnegative":bool(nonnegative),"storage_bounds":storage,
        "design_bounds":bool(design), **residuals,
    }


def summarize(series, thermal):
    load = float(series["Load"].sum())
    deficit = float(series["DPS"].sum())
    generation = float(series["RE"].sum()+len(series["RE"])*ETA*thermal)
    h2 = series["H2"]
    # tank sized from the max daily H2
    daily = [float(h2[i:i+24].sum()) for i in range(0,len(h2),24)]
    active = np.flatnonzero(h2>0)
    # LF dip near h4450 in paper Figs. 9-10
    window = series["LF"][4349:4550]
    return {
        "DPSP":deficit/load, "EWP":float((series["WER"].sum()+ETA*series["WEN"].sum())/generation),
        "DPS_MWh":deficit,"WEN_MWhth":float(series["WEN"].sum()),"WER_MWh":float(series["WER"].sum()),
        "H2_kg":float(h2.sum()),"tank_kg":max(daily),"annual_load_MWh":load,
        "generation_MWh_e_equivalent":generation,"end_TES_MWhth":float(series["Q"][-1]),
        "peak_RE_MW":float(series["RE"].max()),"avg_LF":float(series["LF"].mean()),
        "LF_dip_4350_4550":float(window.min()) if len(window) else float(series["LF"].min()),
        "first_H2_hour":int(active[0]+1) if len(active) else None,
        "last_H2_hour":int(active[-1]+1) if len(active) else None,
        "H2_hours_7500plus":int((h2[7499:]>0).sum()),
    }


def simulate(inputs, case, physics, components=None, python_reference=False):
    panel,turbine = components if components is not None else renewables(inputs,physics.pv_model)
    re = case["pv"]*panel+case["wind"]*turbine
    thermal = 50 if case["paper"]==2024 else physics.reactor_2025_mwth
    ch,dis = power_limits(case,physics)
    kernel = _dispatch.py_func if python_reference else _dispatch
    matrix = kernel(inputs.load,re,case["paper"],case["mode"],thermal,case["tes"],case["battery"],
                    case["soec"],case["df"],case["tes_factor"],ch,dis,
                    physics.inactive_soec=="renewables_first",physics.soc_switch=="post_guard",
                    physics.hydrogen_formula=="additive_diagnostic")
    series = {k:matrix[:,j] for j,k in enumerate(FIELDS)}
    return summarize(series,thermal),series,audit(series,case)


def crf(rate, years):
    return 1/years if rate==0 else rate/(1-(1+rate)**(-years))


def npc(capital, annual, life, finance, replacement=None):
    annualized = annual/crf(finance.real_rate,finance.years)
    rep = capital if replacement is None else replacement
    return capital+annualized+sum(rep/(1+finance.real_rate)**y for y in range(life,finance.years,life))


def economics(metrics, case, physics, finance):
    """2024 Eqs15-22 / 2025 Eqs12-13; all sizing ambiguities are explicit."""
    # 150 W panels, 1.5 MW turbine, TES in kWh
    pvkw,wtkw,teskwh = case["pv"]*.150,case["wind"]*1500,case["tes"]*1000
    bskwh = case["battery"]*1000
    bskw = 10000 if bskwh else 0
    soec = case["soec"]*1000 if case["paper"]==2025 else 0
    tank = metrics["tank_kg"] if soec else 0
    costs = {
        "PV":npc(pvkw*1040,pvkw*9,20,finance),
        "WT":npc(wtkw*1695,wtkw*51,20,finance),
        "TES":npc(teskwh*12,teskwh*.24,20,finance),
        "BS":npc(bskw*600+bskwh*200,bskwh*4,5,finance,bskwh*500) if bskwh else 0,
        "HTSE":npc(soec*662,soec*58.69,5,finance) if soec else 0,
        "tank":npc(tank*400,tank*15,20,finance) if tank else 0,
        "balance_of_plant":0.,
    }
    thermal = 50 if case["paper"]==2024 else physics.reactor_2025_mwth
    electrical_kw = thermal*ETA*1000
    variable_mw = thermal if finance.variable_basis=="thermal" else thermal*ETA
    om = electrical_kw*90.26+variable_mw*8760*(2.17+.381)
    # retirement: reactor decommissioned at end of its 60 y life
    decom_year = 60 if finance.decommissioning=="retirement" else finance.years
    costs["reactor"] = electrical_kw*3000+om/crf(finance.real_rate,finance.years)+electrical_kw*621/(1+finance.real_rate)**decom_year
    # turbine/compressor, HX and inverter from the 2024 paper; sizes are assumed
    if finance.balance_of_plant=="separate_rated_proxy":
        inverter_kw = max(pvkw,bskw)
        costs["balance_of_plant"] = (
            2*npc(electrical_kw*149,electrical_kw*2.98,20,finance)
            +npc(thermal*1000*45,thermal*1000*.9,20,finance)
            +npc(inverter_kw*700,inverter_kw*7,10,finance,inverter_kw*600))
    # H2 price minus production cost, $/kg
    revenue = metrics["H2_kg"]*(2.75-.6909) if case["paper"]==2025 else 0
    energy = metrics["annual_load_MWh"]-(metrics["DPS_MWh"] if finance.load_basis=="served" else 0)
    if energy<=0:
        raise ValueError("LCOE undefined without positive denominator.")
    total = sum(costs.values())
    return {"LCOE":(total*crf(finance.real_rate,finance.years)-revenue)/(energy*1000),
            "TNPC_USD":total,"H2_revenue_USD_per_year":revenue,
            **{"NPC_"+k:v for k,v in costs.items()}}


def errors(rows, cases, keys=None):
    if len(rows)!=len(cases) or [r["id"] for r in rows]!=[c["id"] for c in cases]:
        raise ValueError("Every result must match its exact published benchmark ID.")
    values=[]
    for r,c in zip(rows,cases):
        for k in (keys or c["target"]):
            target = c["target"][k]
            if not np.isfinite(target) or target<=0 or not np.isfinite(r[k]):
                raise ValueError("Require finite metrics and positive relative-error targets.")
            values.append((r[k]/target-1))
    a=np.abs(values)
    result={"rms_relative_percent":float(np.sqrt(np.mean(a*a))*100),
            "worst_relative_percent":float(a.max()*100)}
    for band in (5,10):
        result[f"all_within_{band}_percent"]=bool(a.max()<=band/100+1e-12)
        result[f"metrics_within_{band}_percent"]=int((a<=band/100+1e-12).sum())
        result[f"rows_within_{band}_percent"]=sum(
            all(abs(r[k]/c["target"][k]-1)<=band/100+1e-12 for k in (keys or c["target"]))
            for r,c in zip(rows,cases))
    return result


def evaluate(inputs,cases,physics,finance):
    components=renewables(inputs,physics.pv_model)
    rows,curves,audits=[],[],[]
    for case in cases:
        m,s,a=simulate(inputs,case,physics,components)
        m.update(economics(m,case,physics,finance))
        m.update(id=case["id"],paper=case["paper"],mode=case["mode"],valid=a["valid"])
        for k,target in case["target"].items():
            m[k+"_target"]=target
            m[k+"_relative_error_percent"]=100*(m[k]/target-1)
        rows.append(m);curves.append(s);audits.append({"id":case["id"],**a})
    return rows,curves,audits


def load_config(path):
    spec=json.loads(Path(path).read_text())
    physical=set(Physics.__dataclass_fields__)-{"hydrogen_formula"}
    economic=set(Finance.__dataclass_fields__)
    for key,fields,klass in (("physical_grid",physical,Physics),("economic_grid",economic,Finance)):
        grid=spec[key]
        if set(grid)!=fields or any(not isinstance(v,list) or not v for v in grid.values()):
            raise ValueError(f"{key} permits only {sorted(fields)}.")
        if any(len({json.dumps(x,sort_keys=True) for x in v})!=len(v) for v in grid.values()):
            raise ValueError("Duplicate grid values are not distinct settings.")
        for values in itertools.product(*grid.values()):
            klass(**dict(zip(grid,values)))
    for year in (2025,2024):
        cases=spec[f"benchmarks_{year}"]
        if len(cases)!=10 or len({c["id"] for c in cases})!=10:
            raise ValueError("Require all ten published design rows from each paper.")
        for c in cases:
            numeric=[c[k] for k in ("pv","wind","tes","battery","soec","df","tes_factor")]
            if (not np.isfinite(numeric).all() or c["pv"]<0 or not float(c["pv"]).is_integer()
                    or c["tes"]<=0 or c["battery"]<0 or c["wind"]<0 or c["paper"]!=year):
                raise ValueError("Invalid benchmark design.")
            if c["mode"] not in ((1,2,3) if year==2025 else (1,2)):
                raise ValueError("Invalid paper-specific mode.")
            expected=set(METRICS) if year==2025 else {"DPSP","LCOE"}
            if set(c["target"])!=expected or not all(np.isfinite(v) and v>0 for v in c["target"].values()):
                raise ValueError("Invalid benchmark metrics.")
    if isinstance(spec["financial_shortlist_dispatches"],bool) or not isinstance(spec["financial_shortlist_dispatches"],int) or spec["financial_shortlist_dispatches"]<1:
        raise ValueError("Invalid financial shortlist size.")
    return spec


def plot_case(series,case,row,physics,finance,path,label):
    keys=["LF","DPS","WEN","WER"]
    if case["paper"]==2025 and case["mode"]>1:
        keys.insert(1,"H2")
    if case["paper"]==2024:
        keys.insert(1,"SOC")
    fig,axs=plt.subplots(len(keys),1,figsize=(12,2*len(keys)),sharex=True)
    units={"LF":"TES load factor","SOC":"Battery SOC","DPS":"Shortage (MWh)",
           "WEN":"Nuclear waste (MWhth)","WER":"RE waste (MWh)","H2":"Hydrogen (kg/h)"}
    for ax,key in zip(axs,keys):
        ax.plot(series["Hour"],series[key],lw=.6)
        ax.set_ylabel(units[key]);ax.set_xlim(1,len(series["Hour"]));ax.grid(alpha=.2)
        if key in ("LF","SOC"):ax.set_ylim(0,1.05)
    axs[-1].set_xlabel("Hour")
    title=(f"{label} | {case['paper']} row {case['id']} | PV={case['pv']}; TES={case['tes']} MWhth; BS={case['battery']} MWh\n"
           f"DPSP={100*row['DPSP']:.5g}%; EWP={100*row['EWP']:.5g}%; LCOE=${row['LCOE']:.6g}/kWh")
    if physics is not None:
        ch,dis=power_limits(case,physics)
        title+=f"\nTES limits={ch:.4g}/{dis:.4g} MWth; PV={physics.pv_model}; rate={100*finance.real_rate:.4g}%; {finance.years}y"
    fig.suptitle(title,fontsize=10)
    fig.tight_layout(rect=(0,0,1,.91))
    fig.savefig(path,dpi=110);plt.close(fig)


def curve_raster(curves,cases,diagnostic=False):
    image=Image.new("RGB",(2500,1630),"white")
    draw=ImageDraw.Draw(image)
    font=ImageFont.truetype(fm.findfont("DejaVu Sans"),16)
    fields=["LF","H2","DPS","WEN","WER"] if cases[0]["paper"]==2025 else ["LF","SOC","DPS","WEN","WER"]
    maxima=[1,5000 if diagnostic else 350,8,50,40] if cases[0]["paper"]==2025 else [1,1,8,50,40]
    labels=[f"{k}: 0..{v:g}" for k,v in zip(fields,maxima)]
    for j,label in enumerate(labels):
        draw.text((690+j*356,252),label,font=font,fill="black")
    for i,(series,c) in enumerate(zip(curves,cases)):
        y=285+i*125
        draw.line((10,y-5,2480,y-5),fill="#cccccc")
        for j,(field,top) in enumerate(zip(fields,maxima)):
            x,w,h=690+j*356,325,103
            values=series[field]
            if values.max()>top+1e-7:
                raise ValueError(f"Dashboard axis would clip {field}.")
            start=np.linspace(0,len(values),w+1,dtype=int)[:-1]
            low=np.minimum.reduceat(values,start);high=np.maximum.reduceat(values,start)
            draw.rectangle((x,y,x+w,y+h),outline="#cccccc")
            for k in range(w):
                draw.line((x+k,y+h-int(high[k]/top*h),x+k,y+h-int(low[k]/top*h)),fill="#1764a3")
    return image


def dashboard(base,cid,physics,finance,rows,cases,summary,path,label):
    image=base.copy();draw=ImageDraw.Draw(image)
    fontpath=fm.findfont("DejaVu Sans")
    normal=ImageFont.truetype(fontpath,19);title=ImageFont.truetype(fontpath,25)
    lines=[
        f"{cid} | {label} | ORIGINAL EXCEL VALUES UNCHANGED",
        f"PV={physics.pv_model}; 2025 reactor={physics.reactor_2025_mwth:g} MWth; TES charge/discharge={physics.tes_charge_mwth:g}/{physics.tes_discharge_mwth:g} reference MWth; rating={physics.tes_rating}",
        f"HTSE off={physics.inactive_soec}; mode3 switch={physics.soc_switch}; H2 formula={physics.hydrogen_formula}; 2024 always uses 50 MWth and battery/TES, without HTSE",
        f"Finance: real rate={100*finance.real_rate:.5g}%; horizon={finance.years}y; variable costs={finance.variable_basis}; E_load={finance.load_basis}; BOP={finance.balance_of_plant}; decom={finance.decommissioning}",
        f"Worst relative error={summary['worst_relative_percent']:.3f}%; RMS={summary['rms_relative_percent']:.3f}%; all within 10%={summary['all_within_10_percent']}; valid={all(r['valid'] for r in rows)}",
        "DPSP/EWP shown in percent; LCOE in $/kWh. Each value: candidate / paper (signed relative error). Curves are candidate only.",
        "Hourly min/max display envelopes include every sample. LF/SOC dimensionless; H2 kg/h; DPS/WER MWh; WEN MWhth. All curves span 1-8760h."
    ]
    for i,line in enumerate(lines):
        draw.text((18,12+i*33),line,font=title if i==0 else normal,fill="black")
    for i,(r,c) in enumerate(zip(rows,cases)):
        y=285+i*125
        draw.text((18,y),f"{c['id']}: PV={c['pv']}, TES={c['tes']}, BS={c['battery']}",font=normal,fill="black")
        for j,key in enumerate(c["target"]):
            scale=1 if key=="LCOE" else 100
            error=100*(r[key]/c["target"][key]-1)
            text=f"{key}: {r[key]*scale:.6g} / {c['target'][key]*scale:.6g} ({error:+.2f}%)"
            draw.text((18,y+27+j*25),text,font=normal,fill="#006428" if abs(error)<=10+1e-9 else "#ac1926")
    draw.text((18,1575),"A numerical fit is not an accepted reproduction without physical audits and compatible inputs. No RL; no author-hourly RMSE claimed.",font=normal,fill="black")
    image.save(path,compress_level=3)
    image.close()


def teacher_comparison(inputs,cases,out):
    """Only this function imports/calls the supplied implementation."""
    spec=importlib.util.spec_from_file_location("paper_reproduction_teacher",ROOT/"NRHES_Model.py")
    teacher=importlib.util.module_from_spec(spec);sys.modules[spec.name]=teacher
    old=sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode=True;spec.loader.exec_module(teacher)
    finally:
        sys.dont_write_bytecode=old
    p=teacher.create_params()
    out.mkdir()
    rows,allseries=[],[]
    for c in cases:
        re=np.array([teacher.PV_power_output(c["pv"],g,t,p)[0]+teacher.WT_power_output(v,c["wind"],p)
                     for g,t,v in zip(inputs.solar,inputs.temperature,inputs.wind)])
        peak=float(re.max())
        q=.05*c["tes"]
        s={k:np.zeros(len(inputs.load)) for k in ("Hour","Load","RE","Q","LF","H2","DPS","WER","WEN","total_residual")}
        for i,(load,power) in enumerate(zip(inputs.load,re)):
            oldq=q
            if c["mode"]==1:
                values=teacher.Mode1(power,10.,load,q,c["tes"],p)
            else:
                args=(power,10.,load,q,c["tes"],c["soec"],peak,c["df"])
                values=teacher.Mode2(*args,p) if c["mode"]==2 else teacher.Mode3(*args,c["tes_factor"],p)
            dps,wer,wen,h,lf,q=values
            residual=power+10-(load-dps+wer+ETA*wen+h/KE+ETA*h/KTH+ETA*(q-oldq))
            for key,v in zip(s,(i+1,load,power,q,lf,h,dps,wer,wen,residual)):
                s[key][i]=v
        metrics=summarize(s,25)
        config={"N_pv":c["pv"],"N_wt":c["wind"],"C_TES":c["tes"],"P_SOEC":c["soec"],
                "DF":c["df"],"TES_factor":c["tes_factor"]}
        metrics["LCOE"]=teacher.calculate_LCOE(config,p,metrics["H2_kg"],metrics["DPS_MWh"],metrics["annual_load_MWh"],c["mode"])
        metrics.update(id=c["id"],paper=2025,mode=c["mode"],
                       max_balance_residual_MWh=float(np.abs(s["total_residual"]).max()),
                       annual_unaccounted_positive_MWh=float(np.maximum(s["total_residual"],0).sum()),
                       annual_unaccounted_negative_MWh=float(np.maximum(-s["total_residual"],0).sum()))
        for k,target in c["target"].items():
            metrics[k+"_target"]=target;metrics[k+"_relative_error_percent"]=100*(metrics[k]/target-1)
        np.savez_compressed(out/f"row_{c['id']}.npz",**s)
        plot_case(s,c,metrics,None,None,out/f"row_{c['id']}.png","Teacher as coded, not conservation-validated")
        rows.append(metrics);allseries.append(s)
    pd.DataFrame(rows).to_csv(out/"results.csv",index=False)
    return rows,allseries


class GridSearch:
    def __init__(self,inputs,spec,out):
        self.inputs,self.spec,self.out=inputs,spec,out
        self.records,self.metric_rows,self.cache,self.physical=[],[],{},{}
        (out/"cases").mkdir();(out/"physical").mkdir()

    def record(self,pid,physics,finance,rows,base,stage):
        key=json.dumps([asdict(physics),asdict(finance)],sort_keys=True)
        if key in self.cache:
            return
        cases=self.spec["benchmarks_2025"]
        summary=errors(rows,cases)
        valid=all(r["valid"] for r in rows)
        cid=f"G{len(self.records)+1:05d}"
        folder=self.out/"cases"/cid;folder.mkdir()
        settings={"physics":asdict(physics),"finance":asdict(finance)}
        save_json(folder/"settings.json",settings)
        pd.DataFrame(rows).to_csv(folder/"results.csv",index=False)
        dashboard(base,cid,physics,finance,rows,cases,summary,folder/"candidate.png","Independent 2025 Table 5 model")
        (folder/"index.html").write_text(
            '<!doctype html><meta charset="utf-8"><a href="../../index.html">All cases</a>'
            f'<h1>{cid}</h1><a href="settings.json">Settings</a> | <a href="results.csv">Results</a>'
            '<br><img style="max-width:100%" src="candidate.png">')
        record={"case_id":cid,"physical_id":pid,"stage":stage,**asdict(physics),**asdict(finance),
                **summary,"valid":valid,
                "objective":summary["worst_relative_percent"]+.1*summary["rms_relative_percent"]}
        self.records.append(record)
        self.metric_rows.extend({"case_id":cid,**r} for r in rows)
        self.cache[key]=cid
        if len(self.records)%100==0:
            print(f"{len(self.records)} complete configurations saved ({stage})",flush=True)

    def run(self):
        grid=self.spec["physical_grid"]
        cases=self.spec["benchmarks_2025"]
        for i,values in enumerate(itertools.product(*grid.values()),1):
            physics=Physics(**dict(zip(grid,values)))
            rows,curves,audits=evaluate(self.inputs,cases,physics,Finance())
            pid=f"P{i:04d}";folder=self.out/"physical"/pid;folder.mkdir()
            base=curve_raster(curves,cases)
            base.save(folder/"curves.png",compress_level=3)
            physical_error=errors(rows,cases,("DPSP","EWP"))
            fingerprint=hashlib.sha256(np.round([[r[k] for k in ("DPSP","EWP","H2_kg","tank_kg","end_TES_MWhth")] for r in rows],10).tobytes()).hexdigest()
            self.physical[pid]={"physics":physics,"rows":rows,"valid":all(a["valid"] for a in audits),
                                "dispatch_fingerprint":fingerprint,
                                "physical_objective":physical_error["worst_relative_percent"]+.1*physical_error["rms_relative_percent"]}
            save_json(folder/"audits.json",audits)
            self.record(pid,physics,Finance(),rows,base,"physical_grid")
            base.close()
        ranked=sorted((p for p in self.physical if self.physical[p]["valid"]),
                      key=lambda p:(self.physical[p]["physical_objective"],p))
        chosen=[];seen=set()
        for pid in ranked:
            fingerprint=self.physical[pid]["dispatch_fingerprint"]
            if fingerprint not in seen:
                chosen.append(pid);seen.add(fingerprint)
            if len(chosen)>=self.spec["financial_shortlist_dispatches"]:
                break
        if not chosen:
            raise RuntimeError("No physically valid dispatch in the grid.")
        save_json(self.out/"financial_shortlist.json",chosen)
        grid=self.spec["economic_grid"]
        for pid in chosen:
            item=self.physical[pid];physics=item["physics"]
            with Image.open(self.out/"physical"/pid/"curves.png") as image:
                base=image.convert("RGB")
            for values in itertools.product(*grid.values()):
                finance=Finance(**dict(zip(grid,values)))
                rows=[{**r,**economics(r,c,physics,finance)} for r,c in zip(item["rows"],cases)]
                for r,c in zip(rows,cases):
                    r["LCOE_relative_error_percent"]=100*(r["LCOE"]/c["target"]["LCOE"]-1)
                self.record(pid,physics,finance,rows,base,"financial_grid")
            base.close()
        # Literal Eq9 sum is retained only as a resource-invalid diagnostic.
        p=Physics(hydrogen_formula="additive_diagnostic")
        rows,curves,audits=evaluate(self.inputs,cases,p,Finance())
        base=curve_raster(curves,cases,diagnostic=True)
        self.record("DIAGNOSTIC",p,Finance(),rows,base,"invalid_additive_diagnostic")
        save_json(self.out/"additive_diagnostic_audits.json",audits)
        base.close()
        return pd.DataFrame(self.records).sort_values(["valid","objective","case_id"],ascending=[False,True,True])


def export_bundle(inputs,cases,physics,finance,out,label):
    out.mkdir(parents=True)
    rows,curves,audits=evaluate(inputs,cases,physics,finance)
    if not all(a["valid"] for a in audits):
        raise AssertionError("Only physically valid models can be exported as selected models.")
    for case,row,s in zip(cases,rows,curves):
        np.savez_compressed(out/f"row_{case['id']}.npz",**s)
        plot_case(s,case,row,physics,finance,out/f"row_{case['id']}.png",label)
    pd.DataFrame(rows).to_csv(out/"results.csv",index=False)
    save_json(out/"audits.json",audits)
    save_json(out/"settings.json",{"physics":asdict(physics),"finance":asdict(finance)})
    base=curve_raster(curves,cases)
    dashboard(base,"SELECTED",physics,finance,rows,cases,errors(rows,cases),out/"candidate.png",label)
    base.close()
    return rows,audits


def feasibility(inputs,spec,out):
    records=[]
    for pv in ("area_efficiency","nameplate_150W"):
        panel,wt=renewables(inputs,pv)
        for thermal in (25,50):
            for c in spec["benchmarks_2025"][:3]:
                generation=float((c["pv"]*panel+c["wind"]*wt).sum()+len(inputs.load)*ETA*thermal)
                maximum_storage=ETA*.95*c["tes"]
                bound=max(0,generation-inputs.load.sum()-maximum_storage)/generation
                records.append({"id":c["id"],"pv_model":pv,"reactor_MWth":thermal,
                                "minimum_possible_EWP":bound,"paper_EWP":c["target"]["EWP"],
                                "within_10percent_possible":bool(bound<=c["target"]["EWP"]*1.1)})
    pd.DataFrame(records).to_csv(out/"mode1_energy_bounds.csv",index=False)
    save_json(out/"paper_2024_input_compatibility.json",{
        "reactor_electric_MW":20,"Excel_load_max_MW":float(inputs.load.max()),
        "reactor_alone_meets_all_hours":bool((inputs.load<=20).all()),
        "paper_2024_source_load_divisor":550,"paper_2025_source_load_divisor":1000,
        "Excel_values_changed":False,"positive_published_DPSP_reproducible_with_these_inputs":not bool((inputs.load<=20).all()),
    })


def comparison_and_report(spec,out,teacher,baseline,selected,ranking,battery_rows):
    records=[]
    for label,rows in (("teacher_as_coded",teacher),("independent_reference",baseline),("selected_grid",selected)):
        for r,c in zip(rows,spec["benchmarks_2025"]):
            for key,target in c["target"].items():
                records.append({"model":label,"id":c["id"],"metric":key,"paper":target,
                                "actual":r[key],"relative_error_percent":100*(r[key]/target-1)})
    pd.DataFrame(records).to_csv(out/"paper_teacher_new_comparison.csv",index=False)
    summaries=[{"model":label,**errors(rows,spec["benchmarks_2025"])}
               for label,rows in (("teacher_as_coded",teacher),("independent_reference",baseline),("selected_grid",selected))]
    pd.DataFrame(summaries).to_csv(out/"comparison_summary.csv",index=False)
    # Exact error table, avoiding confusing overlaid hourly curves.
    image=Image.new("RGB",(2100,1150),"white");d=ImageDraw.Draw(image)
    f=fm.findfont("DejaVu Sans");font=ImageFont.truetype(f,23);title=ImageFont.truetype(f,34)
    d.text((20,18),"2025 Table 5: teacher versus independent model, same Excel values",font=title,fill="black")
    tables=[teacher,baseline,selected]
    for j,item in enumerate(summaries):
        x=160+j*635
        d.text((x,85),item["model"],font=font,fill="black")
        d.text((x,125),f"RMS {item['rms_relative_percent']:.2f}% / worst {item['worst_relative_percent']:.2f}%",font=font,fill="black")
        d.text((x,165),f"Rows within 10%: {item['rows_within_10_percent']}/10",font=font,fill="black")
        for k,key in enumerate(METRICS):d.text((x+k*195,225),key+" error",font=font,fill="black")
    for i,c in enumerate(spec["benchmarks_2025"]):
        y=290+i*65;d.text((20,y),"Row "+c["id"],font=font,fill="black")
        for j,rows in enumerate(tables):
            for k,key in enumerate(METRICS):
                e=100*(rows[i][key]/c["target"][key]-1)
                d.text((160+j*635+k*195,y),f"{e:+.2f}%",font=font,fill="#006428" if abs(e)<=10 else "#ac1926")
    d.text((20,1010),"Errors are relative, not percentage points. Green <=10%; red >10%. All 30 metrics must pass.",font=font,fill="black")
    d.text((20,1060),"Teacher is reproduced as coded; its hourly energy-accounting failures are reported separately. No input calibration.",font=font,fill="black")
    image.save(out/"teacher_vs_paper_first.png",compress_level=3)
    pd.DataFrame(battery_rows).to_csv(out/"paper_2024_all_controls.csv",index=False)
    lines=["# Independent paper-first reproduction and grid search","",
           "**Excel values and order are unchanged, including in memory. No RL is included.**","",
           "## 2025 Table 5: same ten designs, three metrics per design","",
           "| Model | RMS relative error | Worst relative error | Complete rows within 10% |",
           "|---|---:|---:|---:|"]
    for s in summaries:
        lines.append(f"| {s['model']} | {s['rms_relative_percent']:.3f}% | {s['worst_relative_percent']:.3f}% | {s['rows_within_10_percent']}/10 |")
    valid=ranking[ranking.valid]
    lines += ["",f"Tested complete configurations: {len(ranking)}. Physically valid: {len(valid)}.",
              f"All-30 passing cases: 5% = {int(valid.all_within_5_percent.sum())}; 10% = {int(valid.all_within_10_percent.sum())}.","",
              "All ten rows enter this calibration objective. This is benchmark fitting, not held-out validation.",
              "Stage A exhausts the stated physical grid using DPSP/EWP only. Stage B screens the full financial grid for the shortlisted distinct dispatch signatures.",
              "This is a staged search, not the entire physical-by-financial Cartesian product. Both stages save every tested configuration and dedicated plot.","",
              "## 2024 paper: different model and different benchmarks","",
              "The 2024 model includes a 50 MWth reactor, 20 MWe output, battery (96% charge/discharge, 15% minimum/initial) and TES; it does not include HTSE.",
              "Tables 7-9 contain the physical DPSP/LCOE benchmarks. All ten listed rows are run separately.",
              "Table 5 contains four optimization algorithms' HV/convergence statistics, exported unchanged as a reference. A parameter grid does not reproduce those algorithm statistics.",
              "The supplied load is 9-15 MW, below the 2024 reactor's 20 MWe at every hour. Literal unchanged-input DPSP is zero, not a successful match to the paper's positive DPSP values.",
              "The papers reconstruct/scale load differently (550 versus 1000). No conversion or rescaling is applied here. Teacher code has no battery/TES-2024 implementation; its 2024 comparison is not applicable.","",
              "## Interpretation and limitations","",
              "- Main-text 25 MWth and contradictory appendix 50 MWth are explicit 2025 alternatives, not a freely fitted reactor.",
              "- PV conventions, unspecified TES ratings, inactive-SOEC redispatch and within-interval SOC guards are documented hypotheses, not all verified author choices.",
              "- The reference uses area/efficiency, 25 MWth and unrestricted-in-practice 25/25 MWth TES ratings, retained DF allocation and start-of-hour switching.",
              "- All valid H2 production obeys both electrical and thermal requirements. The literal additive Eq9 diagnostic fails resource audits and is never selected.",
              "- Finance changes do not alter dispatch. All component prices, published designs, efficiencies, controls and hourly input values stay fixed.",
              "- Separate PB/inverter costs use explicitly unverified rated sizing proxies and may double-count costs included in reactor OCC. Agreement under a proxy is not economic-model identification.",
              "- Mode 1 optimistic energy bounds, independent of TES ratings/finance, are in mode1_energy_bounds.csv.",
              "- Teacher as-coded results include its original finances. teacher_common_finance.csv separates dispatch differences from economic conventions.",
              "- Separate electricity, heat, TES, battery, resource and total-energy audits apply to every new physical case. Teacher audits can use only its exposed aggregate outputs.",
              "- Saved dashboards are candidate-only min/max envelopes. Selected bundles contain full hourly arrays and individual full-resolution plots.",
              "- No full author-hourly curve RMSE or MOEA convergence replication is claimed.","",
              "## Files","",
              "- index.html: every complete grid configuration and its own plot/settings/results.",
              "- teacher_vs_paper_first.png and paper_teacher_new_comparison.csv: all ten 2025 rows against the actual teacher code.",
              "- selected/: independently implemented selected model, full hourly NPZs, plots and audits.",
              "- paper_first_reference/: independently implemented default, not teacher-derived dispatch.",
              "- paper_2024/: literal battery/TES controls at the published Tables 7-9 designs.",
              "- selection.json: frozen model plus original input/source hashes.",
              "- paper_reproduction_config.json copy: full grids, source provenance, both papers' design/algorithm targets.",
              ""]
    (out/"REPORT.md").write_text("\n".join(lines))
    entries=[]
    for r in ranking.itertuples():
        entries.append(f'<tr><td><a href="cases/{r.case_id}/index.html">{r.case_id}</a></td>'
                       f'<td>{html.escape(r.stage)}</td><td>{r.valid}</td><td>{r.rms_relative_percent:.3f}%</td>'
                       f'<td>{r.worst_relative_percent:.3f}%</td><td>{r.rows_within_10_percent}/10</td></tr>')
    (out/"index.html").write_text(
        '<!doctype html><meta charset="utf-8"><title>Independent paper-first grid</title>'
        '<style>body{font:15px system-ui;margin:20px}td,th{border:1px solid #ccc;padding:6px}table{border-collapse:collapse}</style>'
        '<h1>Independent paper-first models; Excel unchanged</h1>'
        '<a href="REPORT.md">Report</a> | <a href="teacher_vs_paper_first.png">Teacher comparison</a> | '
        '<a href="selected/candidate.png">Selected candidate</a> | <a href="paper_2024_all_controls.csv">2024 controls</a>'
        '<table><tr><th>Case</th><th>Stage</th><th>Physics valid</th><th>RMS error</th><th>Worst error</th><th>Rows within 10%</th></tr>'
        +"".join(entries)+"</table>")
    return summaries


def run(config,output):
    spec=load_config(config);out=Path(output).resolve()
    if out.exists():raise FileExistsError("Use a new output directory.")
    before=original_hashes();inputs=load_inputs(ROOT/"InputData.xlsx");input_fingerprint=inputs.fingerprint()
    out.mkdir(parents=True)
    save_json(out/"paper_reproduction_config.json",spec)
    feasibility(inputs,spec,out)
    pd.DataFrame(spec["algorithm_table5_2024"]).assign(
        reproduction_status="reference_only_MOEA_experiments_not_run").to_csv(out/"paper_2024_Table5_algorithm_reference.csv",index=False)
    print("Running actual teacher functions on all ten 2025 designs.",flush=True)
    teacher,teacher_series=teacher_comparison(inputs,spec["benchmarks_2025"],out/"teacher")
    baseline,_=export_bundle(inputs,spec["benchmarks_2025"],Physics(),Finance(),out/"paper_first_reference","Independent reference, not an accepted reproduction")
    search=GridSearch(inputs,spec,out)
    ranking=search.run()
    ranking.to_csv(out/"ranking.csv",index=False)
    pd.DataFrame(search.metric_rows).to_csv(out/"all_results.csv",index=False)
    chosen=ranking[ranking.valid].iloc[0]
    settings=json.loads((out/"cases"/chosen.case_id/"settings.json").read_text())
    physical,financial=Physics(**settings["physics"]),Finance(**settings["finance"])
    selected,audits=export_bundle(inputs,spec["benchmarks_2025"],physical,financial,out/"selected","Selected grid candidate, not author-validated")
    # Normalize finances only, preserving the teacher's actual dispatch.
    common=[]
    for r,c in zip(teacher,spec["benchmarks_2025"]):
        normalized={**r,**economics(r,c,Physics(),financial)}
        normalized["LCOE_relative_error_percent"]=100*(normalized["LCOE"]/c["target"]["LCOE"]-1)
        common.append(normalized)
    pd.DataFrame(common).to_csv(out/"teacher_common_finance.csv",index=False)
    battery_rows=[]
    for i,(pv,rating) in enumerate(itertools.product(("area_efficiency","nameplate_150W"),(10,50)),1):
        p=Physics(tes_charge_mwth=rating,tes_discharge_mwth=rating,pv_model=pv)
        folder=out/"paper_2024"/f"control_{i}"
        rows,_=export_bundle(inputs,spec["benchmarks_2024"],p,Finance(),folder,
                             "2024 battery/TES, input-incompatible benchmark")
        battery_rows.extend({"control_id":i,**r} for r in rows)
    summaries=comparison_and_report(spec,out,teacher,baseline,selected,ranking,battery_rows)
    if original_hashes()!=before or inputs.fingerprint()!=input_fingerprint:
        raise AssertionError("Original files or in-memory Excel values changed.")
    status="all_30_metrics_within_10_percent_not_author_curve_validated" if errors(selected,spec["benchmarks_2025"])["all_within_10_percent"] else "not_within_requested_margin"
    snapshot={"status":status,"settings":settings,"spec":spec,"selected_case":chosen.case_id,
              "original_hashes":before,"input_values_fingerprint":input_fingerprint,
              "implementation_sha256":digest(__file__),"input_policy":"unaltered_values_and_order",
              "benchmark_metrics":METRICS}
    save_json(out/"selection.json",snapshot)
    save_json(out/"manifest.json",{
        "completed_utc":datetime.now(timezone.utc).isoformat(),"originals_unchanged":True,
        "Excel_values_unchanged_in_memory":True,"original_hashes":before,
        "implementation_sha256":digest(__file__),"input_values_fingerprint":input_fingerprint,
        "grid_cases":len(ranking),"physical_dispatches":len(search.physical),
        "financial_grid_cases":int((ranking.stage=="financial_grid").sum()),
        "all_2025_metric_rows":len(search.metric_rows),"2024_control_rows":len(battery_rows),
        "summaries":summaries,"selected_case":chosen.case_id,"status":status,
        "max_selected_balance_residual_MWh":max(a["max_balance_residual_MWh"] for a in audits),
    })
    print(json.dumps({"output":str(out),"status":status,"selected_case":chosen.case_id,
                      "grid_cases":len(ranking),"comparison":summaries},indent=2),flush=True)
    return snapshot


def rerun(selection,output):
    saved=json.loads(Path(selection).read_text());out=Path(output).resolve()
    if out.exists():raise FileExistsError("Use a new output directory.")
    if saved["implementation_sha256"]!=digest(__file__) or saved["original_hashes"]!=original_hashes():
        raise ValueError("Model, input or paper source hash changed.")
    inputs=load_inputs(ROOT/"InputData.xlsx")
    if inputs.fingerprint()!=saved["input_values_fingerprint"]:
        raise ValueError("Excel values changed.")
    p=Physics(**saved["settings"]["physics"]);f=Finance(**saved["settings"]["finance"])
    return export_bundle(inputs,saved["spec"]["benchmarks_2025"],p,f,out,"Frozen independent model")


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config",type=Path,default=HERE/"paper_reproduction_config.json")
    p.add_argument("--output",type=Path,required=True)
    p.add_argument("--selection",type=Path,help="Rerun a frozen model without grid search.")
    args=p.parse_args()
    if args.selection:rerun(args.selection,args.output)
    else:run(args.config,args.output)


if __name__=="__main__":
    main()
