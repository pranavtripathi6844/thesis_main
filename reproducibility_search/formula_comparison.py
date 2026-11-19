"""Grid search on the teacher's designs 1a/2a/3d: two engines x two hydrogen formulas.

Engines: the professor's script (with switch edits only) and our corrected model.
Formulas: minimum (teacher code / paper text) and addition (paper Eq. 9).
Every engine/formula pair tests the full Cartesian product of the same physical and
financial grid. Excel values, designs, efficiencies and unit prices stay fixed.

  python formula_comparison.py --output results/NEW
  python formula_comparison.py --validate results/NEW
"""
from __future__ import annotations

import argparse
import contextlib
import itertools
import json
import runpy
import shutil
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import numpy as np
import pandas as pd
from PIL import Image, ImageDraw, ImageFont

import formula_engines as fe
import paper_reproduction as pr
import teacher_benchmarks_search as tb

HERE = Path(__file__).resolve().parent
CONFIG = HERE / "teacher_benchmarks_config.json"
METRICS = pr.METRICS
FIGURES = tb.FIGURES
FONT_DIR = Path(matplotlib.get_data_path()) / "fonts/ttf"
LABEL = {"teacher_script": "Teacher script", "our_model": "Our model",
         "minimum": "Minimum", "addition": "Addition"}
OBJECTIVE = "worst relative error + 0.1 x RMS relative error over the nine paper entries"


def csv(path):
    return pd.read_csv(path, float_precision="round_trip")


def code_hashes():
    return {"runner": pr.digest(__file__), "engines": pr.digest(fe.__file__),
            "model": pr.digest(pr.__file__), "grid_config": pr.digest(CONFIG)}


def grids(spec):
    physical = [pr.Physics(**dict(zip(spec["physical_grid"], v)))
                for v in itertools.product(*spec["physical_grid"].values())]
    finance = [pr.Finance(**dict(zip(spec["economic_grid"], v)))
               for v in itertools.product(*spec["economic_grid"].values())]
    return physical, finance


def scores(values, cases):
    """values: (..., 3 designs, 3 metrics) -> relative-error summaries."""
    target = np.array([[c["target"][k] for k in METRICS] for c in cases])
    rel = np.abs(values / target - 1)
    flat = rel.reshape(rel.shape[:-2] + (-1,))
    worst, rms = flat.max(-1) * 100, np.sqrt((flat ** 2).mean(-1)) * 100
    # worst error dominates, rms mostly breaks near-ties
    return {"objective": worst + .1 * rms, "worst_relative_percent": worst,
            "rms_relative_percent": rms,
            "entries_within_5_percent": (flat <= .05 + 1e-12).sum(-1),
            "entries_within_10_percent": (flat <= .10 + 1e-12).sum(-1),
            "rows_within_10_percent": (rel <= .10 + 1e-12).all(-1).sum(-1)}


def evaluate_physical(inputs, cases, engine, formula, physics, finance_grid, cache):
    """Dispatch once; LCOE for every financial setting (finance never changes dispatch)."""
    physical, lcoe, valid = [], np.zeros((len(finance_grid), len(cases))), True
    for j, case in enumerate(cases):
        if engine == "teacher_script":
            key = (physics.pv_model, case["id"])
            # renewables only depend on PV model and design
            if key not in cache:
                cache[key] = fe.teacher_renewables(inputs, case, physics)
            r = fe.teacher_simulate(inputs, case, physics, formula, cache[key])
            physical.append({"DPSP": r["DPSP"], "EWP": r["EWP"], "H2_kg": r["total_H2_kg"]})
            for i, f in enumerate(finance_grid):
                lcoe[i, j] = fe.teacher_lcoe(r, case, physics, formula, f)
        else:
            if physics.pv_model not in cache:
                cache[physics.pv_model] = pr.renewables(inputs, physics.pv_model)
            m, _, a = fe.our_simulate(inputs, case, physics, formula, cache[physics.pv_model],
                                      check_reference=True)
            valid &= a["valid"]
            physical.append({"DPSP": m["DPSP"], "EWP": m["EWP"], "H2_kg": m["H2_kg"]})
            for i, f in enumerate(finance_grid):
                lcoe[i, j] = pr.economics(m, case, physics, f)["LCOE"]
    return physical, lcoe, bool(valid)


def search(inputs, cases, engine, formula, physical_grid, finance_grid, folder):
    folder.mkdir(parents=True, exist_ok=True)
    n_p, n_f, n_c = len(physical_grid), len(finance_grid), len(cases)
    dispatch = np.zeros((n_p, n_c, 3))
    lcoe = np.zeros((n_p, n_f, n_c))
    valid = np.zeros(n_p, bool)
    cache = {}
    for p, physics in enumerate(physical_grid):
        rows, lcoe[p], valid[p] = evaluate_physical(inputs, cases, engine, formula, physics, finance_grid, cache)
        dispatch[p] = [[r["DPSP"], r["EWP"], r["H2_kg"]] for r in rows]
        if (p + 1) % 200 == 0:
            print(f"  {LABEL[engine]} / {LABEL[formula]}: {p + 1}/{n_p} physical settings", flush=True)
    values = np.empty((n_p, n_f, n_c, 3))
    values[..., 0] = dispatch[:, None, :, 0]
    values[..., 1] = dispatch[:, None, :, 1]
    values[..., 2] = lcoe
    s = scores(values, cases)
    pid, fid = np.meshgrid(np.arange(n_p), np.arange(n_f), indexing="ij")
    prefix = f"{'T' if engine == 'teacher_script' else 'O'}{formula[0].upper()}"
    table = {"config_id": [f"{prefix}-P{a + 1:04d}-F{b + 1:03d}" for a, b in zip(pid.ravel(), fid.ravel())],
             "physical_id": pid.ravel() + 1, "finance_id": fid.ravel() + 1,
             "valid": np.repeat(valid, n_f)}
    table.update({k: v.ravel() for k, v in s.items()})
    for j, c in enumerate(cases):
        for k, key in enumerate(METRICS):
            table[f"{key}_{c['id']}"] = values[:, :, j, k].ravel()
        if c["mode"] > 1:
            table[f"H2_t_{c['id']}"] = np.repeat(dispatch[:, j, 2] / 1000, n_f)
    ranking = pd.DataFrame(table)
    # valid first, then objective, then id
    order = np.lexsort((np.arange(len(ranking)), ranking.objective.to_numpy(), ~ranking.valid.to_numpy()))
    ranking = ranking.iloc[order].reset_index(drop=True)
    ranking.insert(0, "rank", np.arange(1, len(ranking) + 1))
    ranking.to_csv(folder / "ranking.csv.gz", index=False, compression="gzip")
    phys = pd.DataFrame([{"physical_id": i + 1, **{k: v for k, v in asdict(x).items() if k != "hydrogen_formula"}}
                         for i, x in enumerate(physical_grid)])
    fin = pd.DataFrame([{"finance_id": i + 1, **asdict(x)} for i, x in enumerate(finance_grid)])
    top = ranking.head(1000).merge(phys, on="physical_id").merge(fin, on="finance_id").sort_values("rank")
    top.to_csv(folder / "top_1000_with_settings.csv", index=False)
    best = ranking.iloc[0]
    if not best.valid:
        raise RuntimeError(f"No valid configuration for {engine}/{formula}.")
    return ranking, physical_grid[best.physical_id - 1], finance_grid[best.finance_id - 1], int(valid.sum())


def export(inputs, cases, engine, formula, physics, finance, folder):
    """Recompute one configuration; save settings, results, arrays and the teacher's own figures."""
    folder.mkdir(parents=True)
    rows, res = [], []
    for case in cases:
        if engine == "teacher_script":
            r = fe.teacher_simulate(inputs, case, physics, formula)
            r["LCOE"] = fe.teacher_lcoe(r, case, physics, formula, finance)
            arrays = {k: r[k] for k in ("DPS", "WER", "WEN", "H2_kg_hour", "LF", "RE")}
            valid, extra = None, {}
        else:
            m, s, a = fe.our_simulate(inputs, case, physics, formula, check_reference=True)
            m.update(pr.economics(m, case, physics, finance))
            r = {"DPSP": m["DPSP"], "EWP": m["EWP"], "LCOE": m["LCOE"], "H2_kg_hour": s["H2"],
                 "H2_kg_cum": np.cumsum(s["H2"]), "total_H2_kg": m["H2_kg"], "total_DPS": m["DPS_MWh"],
                 "total_WEN": m["WEN_MWhth"], "total_WER": m["WER_MWh"], "avg_LF": m["avg_LF"],
                 **{k: s[k] for k in ("LF", "DPS", "WEN", "WER", "RE")}}
            arrays = {k: s[k] for k in pr.FIELDS}
            valid, extra = a["valid"], {"max_balance_residual_MWh": a["max_balance_residual_MWh"]}
        np.savez_compressed(folder / f"row_{case['id']}.npz", **arrays)
        active = np.flatnonzero(r["H2_kg_hour"] > 0)
        row = {"id": case["id"], "mode": case["mode"], "engine": engine, "hydrogen_formula": formula,
               **{k: r[k] for k in METRICS}, "H2_t": r["total_H2_kg"] / 1000,
               "DPS_MWh": r["total_DPS"], "WEN_MWhth": r["total_WEN"], "WER_MWh": r["total_WER"],
               "LF_min_h4350_4550": float(r["LF"][4349:4550].min()),
               "last_H2_hour": int(active[-1] + 1) if len(active) else None,
               "conservation_valid": valid, **extra}
        for k in METRICS:
            row[f"{k}_paper"] = case["target"][k]
            row[f"{k}_relative_error_percent"] = 100 * (r[k] / case["target"][k] - 1)
        rows.append(row)
        res.append(r)
    pd.DataFrame(rows).to_csv(folder / "results.csv", index=False)
    pr.save_json(folder / "settings.json", fe.settings_record(engine, formula, physics, finance))
    # plots come from the teacher's own plot_figures
    original = runpy.run_path(str(fe.TEACHER_FILE), run_name="teacher_plotting_only")
    configs = [fe.teacher_config(c) for c in cases]
    with tb.working_directory(folder), (folder / "plotting.log").open("w") as log:
        with contextlib.redirect_stdout(log):
            original["plot_figures"](res, configs, original["create_params"](), inputs.hours,
                                     inputs.solar, inputs.temperature, inputs.wind, inputs.load)
            original["save_results"](res, configs, original["create_params"](), inputs.hours)
    return rows


def fonts():
    return {n: ImageFont.truetype(str(FONT_DIR / ("DejaVuSans-Bold.ttf" if b else "DejaVuSans.ttf")), n)
            for n, b in ((44, True), (34, True), (27, False), (24, False))}


def comparison_images(out, cases, panels_for):
    folder = out / "comparisons"
    folder.mkdir()
    cache = pr.EARLY_RUNS / "paper_plot_comparison_20261026"
    for i in range(3):
        shutil.copyfile(cache / f"Paper_Fig{8 + i}_Mode{i + 1}.png", folder / f"Paper_Fig{8 + i}_Mode{i + 1}.png")
    f = fonts()
    made = []
    for formula in fe.FORMULAS:
        for i, case in enumerate(cases):
            mode = i + 1
            panels = panels_for(formula, i)
            loaded = []
            for title, path, row in panels:
                im = Image.open(path).convert("RGB")
                if im.width != 1400:
                    im = im.resize((1400, round(im.height * 1400 / im.width)), Image.Resampling.LANCZOS)
                loaded.append((title, im, row))
            head = 150
            heights = [max(loaded[0][1].height, loaded[1][1].height), max(loaded[2][1].height, loaded[3][1].height)]
            canvas = Image.new("RGB", (2900, 130 + 2 * head + sum(heights) + 90), "white")
            d = ImageDraw.Draw(canvas)
            note = ("Mode 1 makes no hydrogen, so both formulas give the same result."
                    if mode == 1 else "")
            d.text((40, 25), f"Mode {mode}, design {case['id']}  |  Hydrogen formula: {LABEL[formula].upper()}",
                   font=f[44], fill="black")
            if note:
                d.text((40, 85), note, font=f[24], fill="#444444")
            y = 130
            for r in range(2):
                for c in range(2):
                    title, im, row = loaded[2 * r + c]
                    x = 40 + c * 1440
                    d.text((x, y + 5), title, font=f[34], fill="#0b3d6e")
                    if row is not None:
                        err = [100 * (row[k] / case["target"][k] - 1) for k in METRICS]
                        line = (f"DPSP {100 * row['DPSP']:.3f}% | EWP {100 * row['EWP']:.3f}% | "
                                f"LCOE ${row['LCOE']:.4f}/kWh")
                        d.text((x, y + 55), line, font=f[27], fill="black")
                        if title.startswith("Paper"):
                            d.text((x, y + 95), "Published Table 5 values", font=f[24], fill="#444444")
                        else:
                            d.text((x, y + 95), f"vs paper: {err[0]:+.1f}% | {err[1]:+.1f}% | {err[2]:+.1f}%",
                                   font=f[24], fill="#444444")
                    canvas.paste(im, (x, y + head))
                y += head + heights[r]
            d.text((40, y + 20), "DPSP/EWP/LCOE errors are relative to the paper value. Paper panel: Figure "
                   f"{8 + i} of the 2025 paper. Other panels: the teacher's original plotting function.",
                   font=f[24], fill="#444444")
            name = f"Mode{mode}_{formula}.png"
            canvas.save(folder / name)
            canvas.close()
            made.append(name)
    return made


def fmt(key, v):
    return f"{100 * v:.4f}" if key != "LCOE" else f"{v:.6f}"


def build_table(cases, results):
    """results[(column, formula)] -> list of rows in case order."""
    columns = ("teacher_original", "teacher_grid_best", "our_grid_best")
    records = []
    for i, c in enumerate(cases):
        for key in METRICS:
            for formula in fe.FORMULAS:
                rec = {"mode": c["mode"], "design": c["id"], "metric": key, "formula": LABEL[formula],
                       "paper": c["target"][key]}
                for col in columns:
                    rec[col] = results[(col, formula)][i][key]
                    rec[col + "_relative_error_percent"] = 100 * (rec[col] / c["target"][key] - 1)
                records.append(rec)
    return pd.DataFrame(records)


def markdown_table(table):
    lines = ["| Mode | Metric | Formula | Paper | Teacher original | Teacher script, best grid | Our model, best grid |",
             "|---|---|---|---:|---:|---:|---:|"]
    unit = {"DPSP": "DPSP (%)", "EWP": "EWP (%)", "LCOE": "LCOE ($/kWh)"}
    for r in table.itertuples():
        cells = [fmt(r.metric, r.paper)] + [
            f"{fmt(r.metric, getattr(r, c))} ({getattr(r, c + '_relative_error_percent'):+.1f}%)"
            for c in ("teacher_original", "teacher_grid_best", "our_grid_best")]
        lines.append(f"| {r.mode} ({r.design}) | {unit[r.metric]} | {r.formula} | " + " | ".join(cells) + " |")
    return lines


def report(out, cases, table, summary, bests, counts, images):
    lines = ["# Teacher designs 1a / 2a / 3d: teacher script vs our model, minimum vs addition", "",
             "Only the three designs the professor's script runs: Mode 1 solution 1 (1a), Mode 2 solution 1 (2a), Mode 3 solution 4 (3d).",
             "**Excel values and order, PV/WT/TES/SOEC capacities, DF, TES factor, efficiencies and unit prices are fixed.**",
             "Only the settings changed in earlier work are searched (TES power limits and scaling, PV interpretation, reactor power, SOEC-off allocation, Mode 3 switch check, and six finance conventions).", "",
             f"Each engine/formula pair tests the full product of {counts['physical']} physical x {counts['finance']} financial settings = **{counts['per_pair']:,} configurations** ({4 * counts['per_pair']:,} in total).",
             f"Selection: lowest {OBJECTIVE}. One shared setting applies to all three designs.", "",
             "## Formulas", "",
             "- **Minimum** (paper p5 text, teacher code): H2 = min(K_e x P_e, K_th x P_th).",
             "- **Addition** (paper Eq. 9): H2 = K_e x P_e + K_th x P_th.",
             "- Teacher script, addition: the teacher's line `min(...)` becomes a sum, and the electricity and heat it sends to the SOEC are consumed. The teacher's own heat rule sends up to P_SOEC/0.4 MWth.",
             "- Our model, addition: heat is sent in the SOEC's heat-to-electricity requirement ratio (11.15/35.1), limited by heat available, so the energy balance closes. With enough heat this doubles hydrogen compared with minimum.",
             "- Mode 1 has no hydrogen production, so its results do not depend on the formula.", "",
             "## Results table", "",
             "DPSP and EWP in %, LCOE in $/kWh. Brackets: relative error versus the paper. The paper has one value per entry; it is repeated for both formulas.",
             "'Teacher original' is the professor's script at the professor's settings (minimum = unchanged original run; addition = only the formula switched).", ""]
    lines += markdown_table(table)
    lines += ["", "## Summary of each column", "",
              "| Column | Formula | Worst relative error | RMS relative error | Entries within 10% | Designs with all three within 10% |",
              "|---|---|---:|---:|---:|---:|"]
    for s in summary:
        lines.append(f"| {s['column']} | {s['formula']} | {s['worst_relative_percent']:.2f}% | {s['rms_relative_percent']:.2f}% | {s['entries_within_10_percent']}/9 | {s['rows_within_10_percent']}/3 |")
    lines += ["", "## Best settings", "",
              "| Setting | " + " | ".join(f"{LABEL[e]}, {LABEL[f]}" for e, f in bests) + " |",
              "|---|" + "---|" * len(bests)]
    keys = [k for k in asdict(pr.Physics()) if k != "hydrogen_formula"] + list(asdict(pr.Finance()))
    for k in keys:
        vals = []
        for item in bests.values():
            v = {**asdict(item["physics"]), **asdict(item["finance"])}[k]
            vals.append(f"{100 * v:.3f}%" if k == "real_rate" else str(v))
        lines.append(f"| {k} | " + " | ".join(vals) + " |")
    lines.append("| configuration ID | " + " | ".join(item["config_id"] for item in bests.values()) + " |")
    lines += ["", "Professor's settings: TES 10/10 MWth absolute, PV area/efficiency, 25 MWth, keep split, start-of-hour check, 3.92% real, 20 y, thermal variable cost, served load, embedded BOP, retirement decommissioning. They are inside the grid.",
              "The separate balance-of-plant option is an unverified sizing proxy and may double-count costs.",
              "The teacher-script engine keeps the teacher's own accounting (including its known issues); only our model is conservation-audited.", "",
              "## Plots", "",
              "Each image: paper figure (top left), teacher script at professor's settings (top right), teacher script best grid (bottom left), our model best grid (bottom right). All non-paper panels are drawn by the teacher's original `plot_figures`.", ""]
    for name in images:
        lines += [f"### {name[:-4].replace('_', ', ')}", "", f"![{name}](comparisons/{name})", ""]
    lines += ["## Files", "",
              "- `comparison_table.csv` full-precision table; `summary.csv` column summaries.",
              "- `<engine>/<formula>/ranking.csv.gz` every configuration; `top_1000_with_settings.csv`; `best/` settings, results, hourly arrays and teacher-style figures.",
              "- `physical_settings.csv`, `finance_settings.csv` decode `physical_id`/`finance_id`.",
              "- `teacher_original/` fresh unmodified teacher run. `teacher_script/addition/professor_settings/` same settings, addition formula.",
              "- `teacher_engine_patches.txt` every edit applied to the teacher script.", ""]
    (out / "REPORT.md").write_text("\n".join(lines))


def run(output, config=CONFIG):
    spec = tb.load_spec(config)
    out = Path(output).resolve()
    if out.exists():
        raise FileExistsError("Use a new output directory; previous work is preserved.")
    before, code = pr.original_hashes(), code_hashes()
    inputs = pr.load_inputs(pr.ROOT / "InputData.xlsx")
    fingerprint = inputs.fingerprint()
    cases = spec["benchmarks"]
    physical_grid, finance_grid = grids(spec)
    out.mkdir(parents=True)
    pr.save_json(out / "experiment_config.json", spec)
    (out / "teacher_engine_patches.txt").write_text("\n\n".join(
        f"[{p}] x{n}\n--- original\n{o}--- replacement\n{r}" for p, o, r, n in fe.PATCHES))
    pd.DataFrame([{"physical_id": i + 1, **{k: v for k, v in asdict(x).items() if k != "hydrogen_formula"}}
                  for i, x in enumerate(physical_grid)]).to_csv(out / "physical_settings.csv", index=False)
    pd.DataFrame([{"finance_id": i + 1, **asdict(x)} for i, x in enumerate(finance_grid)]).to_csv(
        out / "finance_settings.csv", index=False)

    print("Fresh unmodified teacher run.", flush=True)
    original = tb.teacher_baseline(inputs, cases, out / "teacher_original")
    check = export(inputs, cases, "teacher_script", "minimum", fe.TEACHER_PHYSICS, fe.TEACHER_FINANCE,
                   out / "teacher_script/minimum/professor_settings")
    for case, a, b in zip(cases, original, check):
        assert all(a[k] == b[k] for k in METRICS), "Teacher engine must equal the original run exactly."
        with np.load(out / "teacher_original" / f"row_{case['id']}.npz") as x, \
                np.load(out / "teacher_script/minimum/professor_settings" / f"row_{case['id']}.npz") as y:
            for k in ("DPS", "WER", "WEN", "H2_kg_hour", "LF", "RE"):
                np.testing.assert_array_equal(x[k], y[k])
    results = {("teacher_original", "minimum"): check,
               ("teacher_original", "addition"): export(
                   inputs, cases, "teacher_script", "addition", fe.TEACHER_PHYSICS, fe.TEACHER_FINANCE,
                   out / "teacher_script/addition/professor_settings")}

    bests, valid_counts = {}, {}
    for engine in fe.ENGINES:
        for formula in fe.FORMULAS:
            print(f"Grid: {LABEL[engine]} / {LABEL[formula]}", flush=True)
            folder = out / engine / formula
            ranking, physics, finance, nvalid = search(inputs, cases, engine, formula, physical_grid,
                                                       finance_grid, folder)
            rows = export(inputs, cases, engine, formula, physics, finance, folder / "best")
            top = ranking.iloc[0]
            for c, r in zip(cases, rows):
                for k in METRICS:
                    assert r[k] == top[f"{k}_{c['id']}"], "Exported best must equal its ranking row."
            column = "teacher_grid_best" if engine == "teacher_script" else "our_grid_best"
            results[(column, formula)] = rows
            bests[(engine, formula)] = {"config_id": top.config_id, "physics": physics, "finance": finance,
                                        "objective": float(top.objective)}
            valid_counts[f"{engine}/{formula}"] = nvalid

    table = build_table(cases, results)
    table.to_csv(out / "comparison_table.csv", index=False)
    summary = []
    for col in ("teacher_original", "teacher_grid_best", "our_grid_best"):
        for formula in fe.FORMULAS:
            vals = np.array([[r[k] for k in METRICS] for r in results[(col, formula)]])
            s = scores(vals, cases)
            summary.append({"column": col, "formula": LABEL[formula],
                            **{k: int(v) if k.startswith(("entries", "rows")) else float(v) for k, v in s.items()}})
    pd.DataFrame(summary).to_csv(out / "summary.csv", index=False)

    def panels_for(formula, i):
        prof = (out / "teacher_original" if formula == "minimum"
                else out / "teacher_script/addition/professor_settings")
        return [(f"Paper, Figure {8 + i}", out / "comparisons" / f"Paper_Fig{8 + i}_Mode{i + 1}.png",
                 cases[i]["target"]),
                ("Teacher script, professor's settings", prof / FIGURES[i], results[("teacher_original", formula)][i]),
                ("Teacher script, best grid", out / "teacher_script" / formula / "best" / FIGURES[i],
                 results[("teacher_grid_best", formula)][i]),
                ("Our model, best grid", out / "our_model" / formula / "best" / FIGURES[i],
                 results[("our_grid_best", formula)][i])]
    images = comparison_images(out, cases, panels_for)
    counts = {"physical": len(physical_grid), "finance": len(finance_grid),
              "per_pair": len(physical_grid) * len(finance_grid)}
    report(out, cases, table, summary, bests, counts, images)
    assert pr.original_hashes() == before and code_hashes() == code and inputs.fingerprint() == fingerprint
    manifest = {"completed_utc": datetime.now(timezone.utc).isoformat(), "original_hashes": before,
                "code_hashes": code, "input_fingerprint": fingerprint, "benchmarks": [c["id"] for c in cases],
                "configurations_per_engine_formula": counts["per_pair"],
                "physically_valid_settings": valid_counts,
                "best": {f"{e}/{f}": {"config_id": b["config_id"], "objective": b["objective"],
                                      **fe.settings_record(e, f, b["physics"], b["finance"])}
                         for (e, f), b in bests.items()},
                "teacher_engine_equals_original_run": True, "Excel_values_unchanged": True,
                "comparison_images": images}
    pr.save_json(out / "manifest.json", manifest)
    print(json.dumps({"output": str(out), "best": manifest["best"]}, indent=2), flush=True)
    return manifest


def validate(run_folder):
    out = Path(run_folder).resolve()
    m = json.loads((out / "manifest.json").read_text())
    spec = json.loads((out / "experiment_config.json").read_text())
    cases = spec["benchmarks"]
    assert [c["id"] for c in cases] == list(tb.IDS)
    # fails if the code changed after the run
    assert m["original_hashes"] == pr.original_hashes() and m["code_hashes"] == code_hashes()
    inputs = pr.load_inputs(pr.ROOT / "InputData.xlsx")
    assert inputs.fingerprint() == m["input_fingerprint"]
    physical_grid, finance_grid = grids(spec)
    expected = len(physical_grid) * len(finance_grid)
    phys, fin = csv(out / "physical_settings.csv"), csv(out / "finance_settings.csv")
    assert len(phys) == len(physical_grid) and len(fin) == len(finance_grid)
    checked = {}
    table = csv(out / "comparison_table.csv")
    for engine in fe.ENGINES:
        for formula in fe.FORMULAS:
            folder = out / engine / formula
            ranking = csv(folder / "ranking.csv.gz")
            assert len(ranking) == expected == m["configurations_per_engine_formula"]
            assert ranking.config_id.is_unique and set(ranking.physical_id) == set(range(1, len(physical_grid) + 1))
            assert (ranking.groupby("physical_id").size() == len(finance_grid)).all()
            values = np.stack([ranking[[f"{k}_{c['id']}" for k in METRICS]].to_numpy() for c in cases], 1)
            s = scores(values, cases)
            assert np.allclose(s["objective"], ranking.objective, rtol=0, atol=1e-9)
            v = ranking[ranking.valid]
            assert v.objective.min() == ranking.iloc[0].objective and bool(ranking.iloc[0].valid)
            best = ranking.iloc[0]
            settings = json.loads((folder / "best/settings.json").read_text())
            physics = pr.Physics(**settings["physics"])
            finance = pr.Finance(**settings["finance"])
            assert physics == physical_grid[best.physical_id - 1] and finance == finance_grid[best.finance_id - 1]
            assert m["best"][f"{engine}/{formula}"]["config_id"] == best.config_id
            saved = csv(folder / "best/results.csv")
            column = "teacher_grid_best" if engine == "teacher_script" else "our_grid_best"
            for c, row in zip(cases, saved.to_dict("records")):
                # Fresh recomputation of the selected configuration.
                if engine == "teacher_script":
                    r = fe.teacher_simulate(inputs, c, physics, formula)
                    fresh = {"DPSP": r["DPSP"], "EWP": r["EWP"],
                             "LCOE": fe.teacher_lcoe(r, c, physics, formula, finance)}
                else:
                    mm, series, a = fe.our_simulate(inputs, c, physics, formula, check_reference=True)
                    assert a["valid"]
                    fresh = {"DPSP": mm["DPSP"], "EWP": mm["EWP"], "LCOE": pr.economics(mm, c, physics, finance)["LCOE"]}
                for k in METRICS:
                    assert fresh[k] == row[k] == best[f"{k}_{c['id']}"]
                    t = table[(table.design == c["id"]) & (table.metric == k) & (table.formula == LABEL[formula])]
                    assert len(t) == 1 and t[column].iloc[0] == row[k] and t.paper.iloc[0] == c["target"][k]
            for fig in FIGURES:
                with Image.open(folder / "best" / fig) as im:
                    im.verify()
            checked[f"{engine}/{formula}"] = {"configurations": len(ranking), "best": best.config_id}
    original = csv(out / "teacher_original/results.csv")
    prof = csv(out / "teacher_script/minimum/professor_settings/results.csv")
    for k in METRICS:
        assert (original[k].to_numpy() == prof[k].to_numpy()).all()
        t = table[(table.metric == k) & (table.formula == "Minimum")].sort_values("mode")
        assert (t.teacher_original.to_numpy() == original[k].to_numpy()).all()
    for name in m["comparison_images"]:
        with Image.open(out / "comparisons" / name) as im:
            im.verify()
    assert len(m["comparison_images"]) == 6 and len(table) == 18
    report = {"status": "passed", "checked": checked, "table_rows": len(table),
              "comparison_images": len(m["comparison_images"]),
              "teacher_engine_equals_original_run": True, "protected_originals_unchanged": True,
              "exact_Excel_values_unchanged": True}
    pr.save_json(out / "artifact_validation.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--validate", type=Path)
    args = parser.parse_args()
    if args.validate:
        print(json.dumps(validate(args.validate), indent=2))
    elif args.output:
        run(args.output, args.config)
    else:
        parser.error("--output or --validate is required")


if __name__ == "__main__":
    main()
