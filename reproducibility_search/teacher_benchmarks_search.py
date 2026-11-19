"""Search only the teacher main's fixed 1a/2a/3d designs, without editing originals.

Uses the existing conservation-checked model, not a modified teacher dispatcher.
Final plots call the original teacher plotting function with candidate arrays.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import html
import itertools
import json
import os
import runpy
import shutil
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfReader
import matplotlib

import paper_reproduction as pr
from verify_paper_reproduction import Links

HERE = Path(__file__).resolve().parent
IDS = ("1a", "2a", "3d")
FIGURES = ("Figure2_Mode1_Results.png", "Figure3_Mode2_Results.png",
           "Figure4_Mode3_Results.png")
PLOT_KEYS = (("LF", "DPS", "WEN"), ("H2", "LF", "DPS", "WEN", "WER"),
             ("H2", "LF", "DPS", "WEN", "WER"))
SIZE = (2700, 1480)
FONT = Path(matplotlib.get_data_path()) / "fonts/ttf/DejaVuSans.ttf"


def csv(path):
    return pd.read_csv(path, float_precision="round_trip")


def load_spec(path):
    spec = json.loads(Path(path).read_text())
    expected = {"description", "benchmark_ids", "physical_grid", "economic_grid",
                "financial_shortlist_dispatches"}
    if set(spec) != expected or spec["benchmark_ids"] != list(IDS):
        raise ValueError("This experiment permits only teacher designs 1a, 2a, 3d.")
    source = pr.load_config(HERE / "paper_reproduction_config.json")
    for key, klass, excluded in (("physical_grid", pr.Physics, {"hydrogen_formula"}),
                                ("economic_grid", pr.Finance, set())):
        grid = spec[key]
        if set(grid) != set(klass.__dataclass_fields__) - excluded:
            raise ValueError(f"Unsupported settings in {key}; inputs/designs cannot be fitted.")
        if any(not isinstance(v, list) or not v or
               len({json.dumps(x, sort_keys=True) for x in v}) != len(v) for v in grid.values()):
            raise ValueError("Grid axes must be nonempty and contain distinct settings.")
        for values in itertools.product(*grid.values()):
            klass(**dict(zip(grid, values)))
    n = spec["financial_shortlist_dispatches"]
    if isinstance(n, bool) or not isinstance(n, int) or n < 1:
        raise ValueError("Invalid financial shortlist size.")
    cases = {c["id"]: c for c in source["benchmarks_2025"]}
    spec["benchmarks"] = [cases[k] for k in IDS]
    expected_targets = ({"DPSP": .0097, "EWP": .0402, "LCOE": .094012},
                        {"DPSP": .0090, "EWP": .0380, "LCOE": .1162},
                        {"DPSP": .0090, "EWP": .0062, "LCOE": .0931})
    if any(c["target"] != t for c, t in zip(spec["benchmarks"], expected_targets)):
        raise ValueError("The teacher's three published target rows must not be changed.")
    spec["source_provenance"] = source["provenance"]
    return spec


@contextlib.contextmanager
def working_directory(path):
    before = Path.cwd()
    try:
        os.chdir(path)
        yield
    finally:
        os.chdir(before)


def source_hashes():
    return {"runner": pr.digest(__file__), "model": pr.digest(pr.__file__),
            "published_design_config": pr.digest(HERE / "paper_reproduction_config.json")}


def assert_scope(rows, cases):
    if [c["id"] for c in cases] != list(IDS) or [r["id"] for r in rows] != list(IDS):
        raise ValueError("All and only 1a, 2a, 3d must be evaluated in teacher order.")


def teacher_baseline(inputs, cases, folder):
    folder.mkdir()
    # his script reads InputData.xlsx from the cwd
    (folder / "InputData.xlsx").symlink_to(pr.ROOT / "InputData.xlsx")
    with working_directory(folder), (folder / "stdout.log").open("w") as log:
        with contextlib.redirect_stdout(log):
            ns = runpy.run_path(str(pr.ROOT / "NRHES_Model.py"), run_name="__main__")
    for k, arr in zip(("hours", "G_Tot", "T_a", "V_anem", "P_grid"),
                      (inputs.hours, inputs.solar, inputs.temperature, inputs.wind, inputs.load)):
        # make sure he read exactly the same hourly data
        np.testing.assert_array_equal(ns[k], arr)
    native = csv(folder / "NRHES_Economic_Results.csv")
    rows = []
    for i, (case, config, result) in enumerate(zip(cases, ns["cfgs"], ns["res"])):
        for ck, tk in (("pv", "N_pv"), ("wind", "N_wt"), ("tes", "C_TES"),
                       ("soec", "P_SOEC"), ("df", "DF"), ("tes_factor", "TES_factor")):
            assert case[ck] == config[tk], (case["id"], ck)
        row = {"id": case["id"], "mode": case["mode"],
               "DPSP": result["DPSP"], "EWP": result["EWP"], "LCOE": result["LCOE"],
               "H2_kg": result["total_H2_kg"], "DPS_MWh": result["total_DPS"],
               "WEN_MWhth": result["total_WEN"], "WER_MWh": result["total_WER"],
               "avg_LF": result["avg_LF"]}
        for k, column in (("DPSP", "DPSP"), ("EWP", "EWP"), ("LCOE", "LCOE_USD_per_kWh")):
            assert row[k] == native.iloc[i][column]
        arrays = {k: v for k, v in result.items() if isinstance(v, np.ndarray)}
        np.savez_compressed(folder / f"row_{case['id']}.npz",
                            Hour=inputs.hours, Load=inputs.load, RE=ns["P_RE_list"][i], **arrays)
        rows.append(row)
    assert_scope(rows, cases)
    pd.DataFrame(rows).to_csv(folder / "results.csv", index=False)
    return rows


def raster(curves):
    """Exact teacher panel order; hourly min/max bins, fixed unclipped axes."""
    image = Image.new("RGB", SIZE, "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(str(FONT), 20)
    # fixed axis tops so plots stay comparable
    maximum = {"LF": 1, "H2": 350, "DPS": 8, "WEN": 50, "WER": 40}
    unit = {"LF": "", "H2": " kg/h", "DPS": " MWh_e/1h",
            "WEN": " MWh_th/1h", "WER": " MWh_e/1h"}
    for mode, (s, keys) in enumerate(zip(curves, PLOT_KEYS)):
        for j, key in enumerate(keys):
            x, y, w, h = mode * 900 + 80, 390 + j * 190, 780, 135
            values = s[key]
            top = maximum[key]
            if not np.isfinite(values).all() or values.min() < -1e-10 or values.max() > top + 1e-8:
                raise ValueError(f"Dashboard would clip {key}.")
            starts = np.linspace(0, len(values), w + 1, dtype=int)[:-1]
            low, high = np.minimum.reduceat(values, starts), np.maximum.reduceat(values, starts)
            draw.text((x, y - 28), f"{key}: 0..{top:g}{unit[key]}", font=font, fill="black")
            draw.rectangle((x, y, x + w, y + h), outline="#aaaaaa")
            for k in range(w):
                draw.line((x + k, y + h - round(high[k] / top * h),
                           x + k, y + h - round(low[k] / top * h)), fill="#1764a3")
            draw.text((x, y + h + 2), "1", font=font, fill="black")
            draw.text((x + w - 65, y + h + 2), "8760 h", font=font, fill="black")
    return image


def dashboard(base, cid, physics, finance, rows, cases, path):
    assert_scope(rows, cases)
    image = base.copy()
    draw = ImageDraw.Draw(image)
    normal, title = (ImageFont.truetype(str(FONT), n) for n in (22, 28))
    score = pr.errors(rows, cases)
    lines = [
        f"{cid} | ONLY TEACHER DESIGNS 1a / 2a / 3d | candidate only; unchanged Excel",
        f"TES reference charge/discharge {physics.tes_charge_mwth:g}/{physics.tes_discharge_mwth:g} MWth; {physics.tes_rating}; PV {physics.pv_model}; reactor {physics.reactor_2025_mwth:g} MWth",
        f"SOEC inactive: {physics.inactive_soec}; switching: {physics.soc_switch}; finance {100*finance.real_rate:.5g}% real / {finance.years}y / {finance.variable_basis} / {finance.load_basis}",
        f"BOP {finance.balance_of_plant}; decom {finance.decommissioning}; RMS {score['rms_relative_percent']:.3f}%; worst {score['worst_relative_percent']:.3f}%; all nine within 10%: {score['all_within_10_percent']}",
    ]
    for i, line in enumerate(lines):
        draw.text((22, 12 + i * 37), line, font=title if i == 0 else normal, fill="black")
    for j, (row, case) in enumerate(zip(rows, cases)):
        x = j * 900 + 25
        draw.text((x, 182), f"Mode {case['mode']} / row {case['id']}: PV {case['pv']}; TES {case['tes']}", font=title, fill="black")
        for i, key in enumerate(pr.METRICS):
            scale = 1 if key == "LCOE" else 100
            e = 100 * (row[key] / case["target"][key] - 1)
            label = f"{key}: {row[key]*scale:.6g} / {case['target'][key]*scale:.6g} ({e:+.2f}%)"
            draw.text((x, 225 + i * 35), label, font=normal,
                      fill="#006428" if abs(e) <= 10 + 1e-9 else "#ac1926")
    draw.text((22, 1425), "DPSP/EWP in %. Curves: every-hour min/max envelopes, not smoothed author data. Exact native-format plots saved for selected model.", font=normal, fill="black")
    image.save(path, compress_level=3)
    image.close()


def native_results(rows, curves):
    return [{**r, "H2_kg_hour": s["H2"], "H2_kg_cum": np.cumsum(s["H2"]),
             "total_H2_kg": r["H2_kg"], "total_DPS": r["DPS_MWh"],
             "total_WEN": r["WEN_MWhth"], "total_WER": r["WER_MWh"],
             **{k: s[k] for k in ("LF", "DPS", "WEN", "WER")}}
            for r, s in zip(rows, curves)]


def export_bundle(inputs, cases, physics, finance, folder):
    rows, curves, audits = pr.evaluate(inputs, cases, physics, finance)
    assert_scope(rows, cases)
    assert all(a["valid"] for a in audits)
    folder.mkdir(parents=True)
    for case, s in zip(cases, curves):
        np.savez_compressed(folder / f"row_{case['id']}.npz", **s)
    pd.DataFrame(rows).to_csv(folder / "results.csv", index=False)
    pr.save_json(folder / "settings.json", {"physics": asdict(physics), "finance": asdict(finance)})
    pr.save_json(folder / "audits.json", audits)
    teacher = runpy.run_path(str(pr.ROOT / "NRHES_Model.py"), run_name="teacher_plotting_only")
    configs = [{"N_pv": c["pv"], "N_wt": c["wind"], "C_TES": c["tes"],
                "P_SOEC": c["soec"], "DF": c["df"], "TES_factor": c["tes_factor"]} for c in cases]
    res = native_results(rows, curves)
    with working_directory(folder), (folder / "plotting.log").open("w") as log:
        with contextlib.redirect_stdout(log):
            teacher["plot_figures"](res, configs, teacher["create_params"](), inputs.hours,
                                    inputs.solar, inputs.temperature, inputs.wind, inputs.load)
            teacher["save_results"](res, configs, teacher["create_params"](), inputs.hours)
    return rows, audits


class Search:
    def __init__(self, inputs, spec, out):
        self.inputs, self.spec, self.out = inputs, spec, out
        self.cases = spec["benchmarks"]
        self.records, self.results, self.physical, self.seen = [], [], {}, set()
        (out / "cases").mkdir()
        (out / "physical").mkdir()

    def record(self, pid, physics, finance, rows, base, stage):
        signature = json.dumps([asdict(physics), asdict(finance)], sort_keys=True)
        if signature in self.seen:
            return
        assert_scope(rows, self.cases)
        cid = f"TB{len(self.records)+1:05d}"
        folder = self.out / "cases" / cid
        folder.mkdir()
        pr.save_json(folder / "settings.json", {"physics": asdict(physics), "finance": asdict(finance)})
        pd.DataFrame(rows).to_csv(folder / "results.csv", index=False)
        dashboard(base, cid, physics, finance, rows, self.cases, folder / "candidate.png")
        (folder / "index.html").write_text(
            '<!doctype html><meta charset="utf-8"><a href="../../index.html">All cases</a>'
            f'<h1>{cid}: only 1a, 2a, 3d</h1><a href="settings.json">Settings</a> | '
            '<a href="results.csv">Results</a><br><img style="max-width:100%" src="candidate.png">')
        summary = pr.errors(rows, self.cases)
        self.records.append({"case_id": cid, "physical_id": pid, "stage": stage,
                             **asdict(physics), **asdict(finance), **summary,
                             "valid": all(r["valid"] for r in rows),
                             "objective": summary["worst_relative_percent"] + .1 * summary["rms_relative_percent"]})
        self.results.extend({"case_id": cid, **r} for r in rows)
        self.seen.add(signature)
        if len(self.records) % 100 == 0:
            print(f"{len(self.records)} complete three-design configurations saved ({stage})", flush=True)

    def run(self):
        grid = self.spec["physical_grid"]
        for i, values in enumerate(itertools.product(*grid.values()), 1):
            physics = pr.Physics(**dict(zip(grid, values)))
            rows, curves, audits = pr.evaluate(self.inputs, self.cases, physics, pr.Finance())
            pid = f"P{i:04d}"
            folder = self.out / "physical" / pid
            folder.mkdir()
            pr.save_json(folder / "audits.json", audits)
            base = raster(curves)
            base.save(folder / "curves.png", compress_level=3)
            fingerprint = hashlib.sha256()
            for s, keys in zip(curves, PLOT_KEYS):
                for key in keys:
                    fingerprint.update(s[key].tobytes())
            score = pr.errors(rows, self.cases, ("DPSP", "EWP"))
            self.physical[pid] = {"physics": physics, "rows": rows,
                                  "valid": all(a["valid"] for a in audits),
                                  "trajectory_fingerprint": fingerprint.hexdigest(),
                                  "physical_objective": score["worst_relative_percent"] + .1 * score["rms_relative_percent"]}
            self.record(pid, physics, pr.Finance(), rows, base, "physical_grid")
            base.close()
        # finance doesn't change dispatch, so only the best physics go to the finance grid
        ranked = sorted((p for p in self.physical if self.physical[p]["valid"]),
                        key=lambda p: (self.physical[p]["physical_objective"], p))
        chosen, seen = [], set()
        for pid in ranked:
            signature = self.physical[pid]["trajectory_fingerprint"]
            # skip settings that give identical curves
            if signature not in seen:
                chosen.append(pid)
                seen.add(signature)
            if len(chosen) >= self.spec["financial_shortlist_dispatches"]:
                break
        if not chosen:
            raise RuntimeError("No physically valid three-design dispatch.")
        pr.save_json(self.out / "financial_shortlist.json", chosen)
        pd.DataFrame([{"physical_id": k, **{f: v for f, v in item.items() if f not in ("rows", "physics")},
                       **asdict(item["physics"])} for k, item in self.physical.items()]).to_csv(
                           self.out / "physical_ranking.csv", index=False)
        grid = self.spec["economic_grid"]
        for pid in chosen:
            item = self.physical[pid]
            physics = item["physics"]
            with Image.open(self.out / "physical" / pid / "curves.png") as image:
                base = image.convert("RGB")
            for values in itertools.product(*grid.values()):
                finance = pr.Finance(**dict(zip(grid, values)))
                rows = [{**r, **pr.economics(r, c, physics, finance)}
                        for r, c in zip(item["rows"], self.cases)]
                for r, c in zip(rows, self.cases):
                    r["LCOE_relative_error_percent"] = 100 * (r["LCOE"] / c["target"]["LCOE"] - 1)
                self.record(pid, physics, finance, rows, base, "financial_grid")
            base.close()
        return pd.DataFrame(self.records).sort_values(
            ["valid", "objective", "case_id"], ascending=[False, True, True])


def comparison_plots(out, cases, teacher, selected):
    cache = pr.EARLY_RUNS / "paper_plot_comparison_20261026"
    pdf = PdfReader(pr.PAPERS / "1 - Techno-economic assessment of a smTMSR-based nuclear–renewable.pdf")
    norm = lambda s: "".join(s.split())
    # Independent check of all nine targets and capacities against original Table 5.
    text = norm(pdf.pages[11].extract_text())
    for row in ("1 0.0097 0.0402 0.094012 60,423 1 2000",
                "2 0.0090 0.0380 0.1162 80,000 1 2000 9.0870 0.5843",
                "3 0.0090 0.0062 0.0931 75,054 1 250 9.5445 0.3245 0.8048"):
        assert norm(row) in text
    folder = out / "comparisons"
    folder.mkdir()
    fonts = {n: ImageFont.truetype(str(FONT), n) for n in (24, 28, 32, 40)}
    provenance = []
    for i, (case, baseline, candidate) in enumerate(zip(cases, teacher, selected)):
        mode, fig, page = i + 1, i + 8, i + 12
        cached = PdfReader(cache / f"Paper_Page{page}_Mode{mode}.pdf")
        assert norm(cached.pages[0].extract_text()) == norm(pdf.pages[page - 1].extract_text())
        paper_name = f"Paper_Fig{fig}_Mode{mode}.png"
        shutil.copyfile(cache / paper_name, folder / paper_name)
        for kind in ("Paper", "Teacher"):
            left_path = folder / paper_name if kind == "Paper" else out / "teacher" / FIGURES[i]
            right_path = out / "selected" / FIGURES[i]
            left = Image.open(left_path).convert("RGB")
            right = Image.open(right_path).convert("RGB")
            assert right.width == 1400
            left = left.resize((1400, round(left.height * 1400 / left.width)), Image.Resampling.LANCZOS)
            height = max(left.height, right.height)
            image = Image.new("RGB", (2940, 200 + height + 135), "white")
            draw = ImageDraw.Draw(image)
            def put(x, y, value, size):
                bounds = draw.textbbox((x, y), value, font=fonts[size])
                assert bounds[2] < 2920 and bounds[3] < image.height - 5
                draw.text((x, y), value, font=fonts[size], fill="black")
            put(40, 20, f"Only teacher designs | Mode {mode}, row {case['id']} | {kind} vs selected grid", 40)
            put(40, 90, f"PAPER | Figure {fig}" if kind == "Paper" else "TEACHER | Unchanged original main", 32)
            put(1500, 90, "SELECTED GRID | Original teacher plotting function", 32)
            lrow = case["target"] if kind == "Paper" else baseline
            for x, row in ((40, lrow), (1500, candidate)):
                put(x, 140, f"DPSP {100*row['DPSP']:.6f}% | EWP {100*row['EWP']:.6f}% | LCOE ${row['LCOE']:.6f}/kWh", 28)
            image.paste(left, (40, 200))
            image.paste(right, (1500, 200))
            np.testing.assert_array_equal(np.asarray(image.crop((1500, 200, 2900, 200 + right.height))), np.asarray(right))
            err = [100 * (candidate[k] / case["target"][k] - 1) for k in pr.METRICS]
            put(40, 215 + height, f"Selected vs paper relative errors: DPSP {err[0]:+.2f}% | EWP {err[1]:+.2f}% | LCOE {err[2]:+.2f}%.", 28)
            put(40, 258 + height, "Vertical ranges differ. MW and MWh per one-hour interval are numerically comparable; nuclear waste stays thermal.", 24)
            put(40, 295 + height, "Selected dispatch is conservation-checked, not original teacher dispatch. No author-hourly curve RMSE is claimed.", 24)
            name = f"{kind}_vs_Selected_Mode{mode}.png"
            image.save(folder / name)
            provenance.append({"file": name, "mode": mode, "left_sha256": pr.digest(left_path),
                               "selected_native_sha256": pr.digest(right_path),
                               "selected_panel_pixels_preserved": True,
                               "cached_paper_page_text_matches_source": True})
    pr.save_json(folder / "provenance.json", provenance)


def reports(out, spec, teacher, selected, ranking, bounds):
    cases = spec["benchmarks"]
    comparison = []
    for label, rows in (("teacher", teacher), ("selected", selected)):
        for r, c in zip(rows, cases):
            for k in pr.METRICS:
                comparison.append({"model": label, "id": c["id"], "metric": k,
                                   "paper": c["target"][k], "actual": r[k],
                                   "relative_error_percent": 100 * (r[k] / c["target"][k] - 1)})
    pd.DataFrame(comparison).to_csv(out / "benchmark_comparison.csv", index=False)
    summaries = [{"model": label, **pr.errors(rows, cases)}
                 for label, rows in (("teacher", teacher), ("selected", selected))]
    pd.DataFrame(summaries).to_csv(out / "comparison_summary.csv", index=False)
    chosen = ranking[ranking.valid].iloc[0]
    lines = ["# Grid search restricted to the teacher's three designs", "",
             "Only 1a, 2a, 3d (2025 Table 5), paper Figures 8–10. All 9 entries are used in selection.",
             "**Excel values/order, capacities, DF, TES factor, efficiencies and published unit prices remain fixed. No RL.**", "",
             f"Complete tested configurations: **{len(ranking)}**. Physical settings: **{sum(ranking.stage == 'physical_grid')}**. Additional finance configurations: **{sum(ranking.stage == 'financial_grid')}**.",
             "Stage A exhausts the physical grid. Stage B tests the full financial grid on four best valid distinct plotted-hourly-trajectory signatures (or the configured smaller shortlist).",
             "This is a staged grid, not the full physical-by-financial Cartesian product. Invalid additive-H2 diagnostics are not included.", "",
             "The fresh baseline executes the original teacher main and its original plotting/saving functions. Candidates use the existing independent conservation-checked model; they are NOT unchanged teacher dispatch.",
             "Candidate native-format figures call the original teacher plotting function, with the exact same panel order, quantities and 0–8760 hour axis. Each tested configuration has a separate candidate-only dashboard.", "",
             f"## Selected tested candidate: {chosen.case_id}", "",
             "Selection minimizes worst relative error + 0.1 × RMS relative error across the nine entries, subject to physical validity.",
             "A best-tested candidate is not necessarily an accepted reproduction. Each entry must independently meet 5% or 10%.", "",
             "| Model | RMS relative error | Worst relative error | Entries within 5% | Entries within 10% | Complete rows within 10% |",
             "|---|---:|---:|---:|---:|---:|"]
    for s in summaries:
        lines.append(f"| {s['model']} | {s['rms_relative_percent']:.6f}% | {s['worst_relative_percent']:.6f}% | {s['metrics_within_5_percent']}/9 | {s['metrics_within_10_percent']}/9 | {s['rows_within_10_percent']}/3 |")
    lines += ["", f"All-nine passing tested cases: 5% = {int((ranking.valid & ranking.all_within_5_percent).sum())}; 10% = {int((ranking.valid & ranking.all_within_10_percent).sum())}.", "",
              "| Row | Metric | Paper | Teacher | Selected | Teacher error | Selected error |",
              "|---|---|---:|---:|---:|---:|---:|"]
    for c, t, s in zip(cases, teacher, selected):
        for k in pr.METRICS:
            lines.append(f"| {c['id']} | {k} | {c['target'][k]:.9f} | {t[k]:.9f} | {s[k]:.9f} | {100*(t[k]/c['target'][k]-1):+.3f}% | {100*(s[k]/c['target'][k]-1):+.3f}% |")
    lines += ["", "DPSP/EWP are fractions in this table; LCOE is USD/kWh. Errors use full-precision values.", "",
              "## Selected shared settings", "", "```json",
              (out / "selected/settings.json").read_text().strip(), "```", "",
              "**These are tested hypotheses, not recovered author settings.** In particular, separate balance-of-plant costs use unverified rated sizing proxies and may double-count reactor OCC. Economic agreement does not establish dispatch agreement.", "",
              "## Exactly the matching plots", "",
              "Paper is left; selected candidate right. The second link for each mode compares the fresh teacher run against the selected candidate. Vertical ranges differ. Hourly interval energies in the teacher and power in the paper are numerically comparable at 1-hour intervals; no thermal-to-electric rescaling is applied to WEN.", ""]
    for mode in (1, 2, 3):
        lines += [f"### Mode {mode}", "",
                  f"![Paper vs selected Mode {mode}](comparisons/Paper_vs_Selected_Mode{mode}.png)", "",
                  f"[Unchanged teacher vs selected, Mode {mode}](comparisons/Teacher_vs_Selected_Mode{mode}.png)", ""]
    lines += ["## Plot diagnostics, not author-hourly curve fitting", "",
              "The paper's original hourly output arrays are unavailable. No exact plotted-curve RMSE or claim of exact shape reproduction is made. Metrics drive selection; the shapes are reviewed separately.",
              "The paper text on p12 reports approximate TES LF near hour 4450 of 0.71 (Mode 1) and 0.88 (Mode 2). Fig10 shows a much deeper Mode 3 midyear dip and later hydrogen activity.",
              "| Row | Teacher LF minimum, hours 4350–4550 | Selected LF minimum, hours 4350–4550 | Teacher last H2 hour | Selected last H2 hour |",
              "|---|---:|---:|---:|---:|"]
    for c, s in zip(cases, selected):
        with np.load(out / "teacher" / f"row_{c['id']}.npz") as a:
            mask = (a["Hour"] >= 4350) & (a["Hour"] <= 4550)
            lf = a["LF"][mask].min()
            active = a["Hour"][a["H2_kg_hour"] > 0]
            last = int(active[-1]) if len(active) else "none"
        lines.append(f"| {c['id']} | {lf:.6f} | {s['LF_dip_4350_4550']:.6f} | {last} | {s['last_H2_hour']} |")
    lines += ["", "## Necessary Mode 1 energy bound", "",
              "For each PV/reactor interpretation, even perfect load service and maximal annual storage accumulation require",
              "`EWP >= max(0, generation - demand - 0.4 * 0.95 * TES_capacity) / generation`.",
              "This is an optimistic necessary bound for the constant-reactor conservative model and supplied inputs, not proof the paper is wrong.", "",
              "```csv", bounds.to_csv(index=False).strip(), "```", "",
              "Finance and TES power tuning cannot evade this annual energy balance. The input sequence is not verified as identical to the authors' raw weather/load.",
              "The teacher console's 138/98.8 t H2 claims are not used as verified benchmarks: Table 5 has no annual H2 column.", "",
              "## Reproduce and validate", "",
              "Use `teacher_benchmarks_search.py --selection <this-run>/selection.json --output <NEW-folder>` for a frozen rerun.",
              "Use `teacher_benchmarks_search.py --validate <this-run> --frozen <frozen-folder>` for complete artifact validation.",
              "Full per-case records are in `index.html`, `ranking.csv`, `all_results.csv`, and `cases/`. All physical audits are in `physical/`.", ""]
    (out / "REPORT.md").write_text("\n".join(lines))
    links = []
    for r in ranking.itertuples():
        links.append(f'<tr><td><a href="cases/{r.case_id}/index.html">{r.case_id}</a></td>'
                     f'<td>{html.escape(r.stage)}</td><td>{r.rms_relative_percent:.3f}%</td>'
                     f'<td>{r.worst_relative_percent:.3f}%</td><td>{r.metrics_within_10_percent}/9</td></tr>')
    (out / "index.html").write_text(
        '<!doctype html><meta charset="utf-8"><title>Only teacher designs</title>'
        '<style>body{font:15px system-ui;margin:20px}table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:6px}</style>'
        '<h1>Only teacher designs 1a, 2a, 3d</h1><a href="REPORT.md">Report and exact plot comparisons</a>'
        '<table><tr><th>Case</th><th>Stage</th><th>RMS error</th><th>Worst error</th><th>Entries within 10%</th></tr>'
        + "".join(links) + "</table>")
    return summaries


def energy_bounds(inputs, cases):
    c = cases[0]
    rows = []
    for model, thermal in itertools.product(("area_efficiency", "nameplate_150W"), (25, 50)):
        panel, wt = pr.renewables(inputs, model)
        generation = float((c["pv"] * panel + c["wind"] * wt).sum() + 8760 * .4 * thermal)
        bound = max(0, generation - inputs.load.sum() - .4 * .95 * c["tes"]) / generation
        rows.append({"id": c["id"], "pv_model": model, "reactor_MWth": thermal,
                     "minimum_EWP": bound, "paper_EWP": c["target"]["EWP"],
                     "within_10_percent_possible": bool(bound <= c["target"]["EWP"] * 1.1)})
    return pd.DataFrame(rows)


def run(config, output):
    spec = load_spec(config)
    out = Path(output).resolve()
    if out.exists():
        raise FileExistsError("Use a new output directory; previous work is preserved.")
    before, code = pr.original_hashes(), source_hashes()
    inputs = pr.load_inputs(pr.ROOT / "InputData.xlsx")
    fingerprint = inputs.fingerprint()
    out.mkdir(parents=True)
    pr.save_json(out / "experiment_config.json", spec)
    cases = spec["benchmarks"]
    print("Fresh unmodified teacher main: only 1a, 2a, 3d.", flush=True)
    teacher = teacher_baseline(inputs, cases, out / "teacher")
    search = Search(inputs, spec, out)
    ranking = search.run()
    ranking.to_csv(out / "ranking.csv", index=False)
    pd.DataFrame(search.results).to_csv(out / "all_results.csv", index=False)
    best = ranking[ranking.valid].iloc[0]
    settings = json.loads((out / "cases" / best.case_id / "settings.json").read_text())
    selected, audits = export_bundle(inputs, cases, pr.Physics(**settings["physics"]),
                                     pr.Finance(**settings["finance"]), out / "selected")
    saved = csv(out / "cases" / best.case_id / "results.csv")
    pd.testing.assert_frame_equal(saved, csv(out / "selected/results.csv"), check_exact=True)
    bounds = energy_bounds(inputs, cases)
    bounds.to_csv(out / "mode1_energy_bounds.csv", index=False)
    comparison_plots(out, cases, teacher, selected)
    summaries = reports(out, spec, teacher, selected, ranking, bounds)
    assert pr.original_hashes() == before and source_hashes() == code
    assert inputs.fingerprint() == fingerprint
    selection = {"settings": settings, "benchmarks": cases, "selected_case": best.case_id,
                 "original_hashes": before, "source_hashes": code, "input_fingerprint": fingerprint}
    pr.save_json(out / "selection.json", selection)
    manifest = {"completed_utc": datetime.now(timezone.utc).isoformat(), **selection,
                "tested_configurations": len(ranking), "physical_configurations": len(search.physical),
                "finance_configurations": int((ranking.stage == "financial_grid").sum()),
                "benchmark_rows": 3, "benchmark_entries": 9, "all_result_rows": len(search.results),
                "summaries": summaries, "Excel_values_unchanged": True, "originals_unchanged": True,
                "max_selected_balance_residual_MWh": max(a["max_balance_residual_MWh"] for a in audits),
                "accepted_within_10_percent": pr.errors(selected, cases)["all_within_10_percent"]}
    pr.save_json(out / "manifest.json", manifest)
    print(json.dumps({"output": str(out), **manifest}, indent=2), flush=True)
    return manifest


def rerun(selection, output):
    saved = json.loads(Path(selection).read_text())
    if saved["original_hashes"] != pr.original_hashes() or saved["source_hashes"] != source_hashes():
        raise ValueError("Protected originals or implementation/config changed.")
    inputs = pr.load_inputs(pr.ROOT / "InputData.xlsx")
    if inputs.fingerprint() != saved["input_fingerprint"]:
        raise ValueError("Hourly input values changed.")
    out = Path(output).resolve()
    if out.exists():
        raise FileExistsError("Use a new output directory.")
    return export_bundle(inputs, saved["benchmarks"], pr.Physics(**saved["settings"]["physics"]),
                         pr.Finance(**saved["settings"]["finance"]), out)


def validate(run_folder, frozen=None):
    out = Path(run_folder).resolve()
    m = json.loads((out / "manifest.json").read_text())
    spec = json.loads((out / "experiment_config.json").read_text())
    cases = spec["benchmarks"]
    assert [c["id"] for c in cases] == list(IDS)
    assert m["original_hashes"] == pr.original_hashes()
    assert m["source_hashes"] == source_hashes()
    inputs = pr.load_inputs(pr.ROOT / "InputData.xlsx")
    assert inputs.fingerprint() == m["input_fingerprint"]
    rank, results = csv(out / "ranking.csv"), csv(out / "all_results.csv")
    assert rank.case_id.is_unique and len(rank) == m["tested_configurations"]
    assert len(results) == len(rank) * 3 == m["all_result_rows"]
    assert set(results.id) == set(IDS)
    nphysical = len(list(itertools.product(*spec["physical_grid"].values())))
    nfinance = len(list(itertools.product(*spec["economic_grid"].values())))
    shortlist = json.loads((out / "financial_shortlist.json").read_text())
    default_in_grid = all(v in spec["economic_grid"][k] for k, v in asdict(pr.Finance()).items())
    assert len(rank) == nphysical + len(shortlist) * (nfinance - int(default_in_grid))
    assert len(list((out / "cases").iterdir())) == len(rank)
    groups = dict(tuple(results.groupby("case_id", sort=False)))
    settings_seen, plots_seen = set(), set()
    def check_image(path, size=None):
        with Image.open(path) as image:
            if size:
                assert image.size == size
            image.verify()
    for r in rank.to_dict("records"):
        folder = out / "cases" / r["case_id"]
        rows = csv(folder / "results.csv")
        assert_scope(rows.to_dict("records"), cases)
        pd.testing.assert_frame_equal(rows, groups[r["case_id"]].drop(columns="case_id").reset_index(drop=True), check_dtype=False)
        settings = json.loads((folder / "settings.json").read_text())
        key = json.dumps(settings, sort_keys=True)
        assert key not in settings_seen
        settings_seen.add(key)
        for k, value in {**settings["physics"], **settings["finance"]}.items():
            if isinstance(value, (int, float)):
                assert np.isclose(value, r[k], rtol=0, atol=1e-12)
            else:
                assert value == r[k]
        summary = pr.errors(rows.to_dict("records"), cases)
        for key, value in summary.items():
            assert np.isclose(value, r[key], rtol=0, atol=1e-9)
        assert np.isclose(r["objective"], summary["worst_relative_percent"] + .1 * summary["rms_relative_percent"], atol=1e-9)
        assert bool(rows.valid.all()) == r["valid"]
        check_image(folder / "candidate.png", SIZE)
        signature = pr.digest(folder / "candidate.png")
        assert signature not in plots_seen
        plots_seen.add(signature)
    physical = list((out / "physical").iterdir())
    assert len(physical) == nphysical == m["physical_configurations"]
    max_residual = 0
    for folder in physical:
        audits = json.loads((folder / "audits.json").read_text())
        assert [a["id"] for a in audits] == list(IDS)
        records = rank[(rank.physical_id == folder.name) & (rank.stage == "physical_grid")]
        assert len(records) == 1 and bool(records.valid.iloc[0]) == all(a["valid"] for a in audits)
        max_residual = max(max_residual, max(a["max_balance_residual_MWh"] for a in audits))
        check_image(folder / "curves.png", SIZE)
    selected = csv(out / "selected/results.csv")
    selected_id = rank[rank.valid].sort_values(["objective", "case_id"]).iloc[0].case_id
    assert selected_id == m["selected_case"]
    pd.testing.assert_frame_equal(selected, csv(out / "cases" / selected_id / "results.csv"), check_exact=True)
    folders = [out / "selected"] + ([Path(frozen).resolve()] if frozen else [])
    for folder in folders:
        settings = json.loads((folder / "settings.json").read_text())
        p, f = pr.Physics(**settings["physics"]), pr.Finance(**settings["finance"])
        rows = csv(folder / "results.csv")
        native = csv(folder / "NRHES_Economic_Results.csv")
        assert_scope(rows.to_dict("records"), cases)
        for i, (c, row) in enumerate(zip(cases, rows.to_dict("records"))):
            with np.load(folder / f"row_{c['id']}.npz") as a:
                assert set(a.files) == set(pr.FIELDS)
                s = {k: a[k] for k in a.files}
            assert all(v.shape == (8760,) and np.isfinite(v).all() for v in s.values())
            np.testing.assert_array_equal(s["Load"], inputs.load)
            np.testing.assert_array_equal(s["Hour"], inputs.hours)
            assert pr.audit(s, c)["valid"]
            derived = pr.summarize(s, p.reactor_2025_mwth)
            derived.update(pr.economics(derived, c, p, f))
            for key in ("DPSP", "EWP", "LCOE", "H2_kg", "tank_kg"):
                assert np.isclose(derived[key], row[key], rtol=1e-12, atol=1e-12)
            # Also exercise the uncompiled hourly implementation at the selected settings.
            _, reference, reference_audit = pr.simulate(inputs, c, p, python_reference=True)
            assert reference_audit["valid"]
            for key in s:
                np.testing.assert_array_equal(s[key], reference[key])
            for k, column in (("DPSP", "DPSP"), ("EWP", "EWP"), ("LCOE", "LCOE_USD_per_kWh")):
                assert native.iloc[i][column] == row[k]
            check_image(folder / FIGURES[i])
    teacher = csv(out / "teacher/results.csv")
    teacher_native = csv(out / "teacher/NRHES_Economic_Results.csv")
    for i, c in enumerate(cases):
        with np.load(out / "teacher" / f"row_{c['id']}.npz") as a:
            assert all(a[k].shape == (8760,) and np.isfinite(a[k]).all() for k in a.files)
            np.testing.assert_array_equal(a["Load"], inputs.load)
            assert np.isclose(a["H2_kg_hour"].sum(), teacher.iloc[i]["H2_kg"], atol=1e-9)
            assert np.isclose(a["DPS"].sum() / inputs.load.sum(), teacher.iloc[i]["DPSP"], rtol=0, atol=1e-14)
            waste = a["WER"].sum() + .4 * a["WEN"].sum()
            assert np.isclose(waste / (a["RE"].sum() + 8760 * 10), teacher.iloc[i]["EWP"], rtol=0, atol=1e-14)
        for key, column in (("DPSP", "DPSP"), ("EWP", "EWP"), ("LCOE", "LCOE_USD_per_kWh")):
            assert teacher.iloc[i][key] == teacher_native.iloc[i][column]
        check_image(out / "teacher" / FIGURES[i])
    comparison = csv(out / "benchmark_comparison.csv")
    assert len(comparison) == 18
    for label, rows in (("teacher", teacher), ("selected", selected)):
        actual = comparison[comparison.model == label]
        for c, row in zip(cases, rows.to_dict("records")):
            for k in pr.METRICS:
                record = actual[(actual.id == c["id"]) & (actual.metric == k)].iloc[0]
                assert record.paper == c["target"][k] and record.actual == row[k]
                assert np.isclose(record.relative_error_percent, 100 * (row[k] / c["target"][k] - 1), rtol=0, atol=1e-10)
    provenance = json.loads((out / "comparisons/provenance.json").read_text())
    assert len(provenance) == 6 and all(r["selected_panel_pixels_preserved"] for r in provenance)
    for entry in provenance:
        path = out / "comparisons" / entry["file"]
        check_image(path)
        right = out / "selected" / FIGURES[entry["mode"] - 1]
        assert pr.digest(right) == entry["selected_native_sha256"]
        with Image.open(path) as combined, Image.open(right) as native_image:
            np.testing.assert_array_equal(
                np.asarray(combined.convert("RGB").crop((1500, 200, 2900, 200 + native_image.height))),
                np.asarray(native_image.convert("RGB")))
    links_checked = 0
    for page in [out / "index.html", *sorted((out / "cases").glob("*/index.html"))]:
        parser = Links()
        parser.feed(page.read_text())
        for link in parser.links:
            assert (page.parent / link).resolve().exists()
            links_checked += 1
    if frozen:
        folder = Path(frozen).resolve()
        assert (folder / "results.csv").read_bytes() == (out / "selected/results.csv").read_bytes()
        assert (folder / "settings.json").read_bytes() == (out / "selected/settings.json").read_bytes()
        for c in cases:
            with np.load(folder / f"row_{c['id']}.npz") as a, np.load(out / "selected" / f"row_{c['id']}.npz") as b:
                for key in a.files:
                    np.testing.assert_array_equal(a[key], b[key])
        for figure in FIGURES:
            assert pr.digest(folder / figure) == pr.digest(out / "selected" / figure)
    assert pr.original_hashes() == m["original_hashes"] and source_hashes() == m["source_hashes"]
    report = {"status": "passed", "complete_configurations": len(rank),
              "unique_settings": len(settings_seen), "unique_candidate_plots": len(plots_seen),
              "physical_configurations": nphysical, "physical_audit_rows": nphysical * 3,
              "result_rows": len(results), "benchmark_ids": list(IDS), "benchmark_entries": 9,
              "HTML_links_checked": links_checked, "plot_comparisons_checked": 6,
              "max_physical_balance_residual_MWh": max_residual,
              "exact_Excel_values_unchanged": True, "protected_originals_unchanged": True,
              "frozen_exact": True if frozen else None}
    pr.save_json(out / "artifact_validation.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=HERE / "teacher_benchmarks_config.json")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--selection", type=Path)
    parser.add_argument("--validate", type=Path)
    parser.add_argument("--frozen", type=Path)
    args = parser.parse_args()
    if args.validate:
        print(json.dumps(validate(args.validate, args.frozen), indent=2))
    elif args.output is None:
        parser.error("--output is required for search or frozen rerun")
    elif args.selection:
        rerun(args.selection, args.output)
    else:
        run(args.config, args.output)


if __name__ == "__main__":
    main()
