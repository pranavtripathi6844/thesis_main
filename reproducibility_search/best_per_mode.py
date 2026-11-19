"""Best configuration for each mode separately (1a, 2a, 3d) from a finished formula-comparison run.

Candidates: every valid configuration of the four grid searches (teacher script / our model x
minimum / addition) plus the teacher's original run at the professor's settings (both formulas).
Score per mode: worst relative error + 0.1 x RMS relative error over that mode's DPSP, EWP, LCOE.
Exact ties: fewest settings different from the professor's, then the minimum (teacher's own) H2
formula, then configuration ID. Mode 1 makes no hydrogen, so its two formula runs tie exactly.

  python best_per_mode.py --source results/formula_comparison_final_20261111 --output results/NEW
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import numpy as np
import pandas as pd
from PIL import Image, ImageDraw, ImageFont

import formula_comparison as fc
import formula_engines as fe
import paper_reproduction as pr
import teacher_benchmarks_search as tb

METRICS = pr.METRICS
SOURCE_LABEL = {("teacher_script", "minimum"): "Teacher script grid search, minimum H2 formula",
                ("teacher_script", "addition"): "Teacher script grid search, addition H2 formula",
                ("our_model", "minimum"): "Our model grid search, minimum H2 formula",
                ("our_model", "addition"): "Our model grid search, addition H2 formula",
                ("teacher_original", "minimum"): "Teacher's original script and settings, minimum H2 formula",
                ("teacher_original", "addition"): "Teacher's original settings, addition H2 formula"}
PROFESSOR = {**{k: v for k, v in asdict(fe.TEACHER_PHYSICS).items() if k != "hydrogen_formula"},
             **asdict(fe.TEACHER_FINANCE)}


def mode_scores(values, target):
    """values (n, 3 metrics) -> objective, worst %, entries within 10%."""
    rel = np.abs(values / np.asarray(target) - 1)
    worst = rel.max(1) * 100
    # same score as the grid search, but for one mode
    return worst + .1 * np.sqrt((rel ** 2).mean(1)) * 100, worst, (rel <= .10 + 1e-12).sum(1)


def n_different(frame):
    n = np.zeros(len(frame), int)
    for k, v in PROFESSOR.items():
        col = frame[k]
        n += (~np.isclose(col.astype(float), v)) if isinstance(v, float) else (col.astype(str) != str(v))
    return n


def candidates(src, cases, phys, fin):
    frames = []
    for engine in fe.ENGINES:
        for formula in fe.FORMULAS:
            r = fc.csv(src / engine / formula / "ranking.csv.gz")
            r = r[r.valid].merge(phys, on="physical_id").merge(fin, on="finance_id")
            r["engine"], r["formula"] = engine, formula
            frames.append(r)
    # teacher's original run is a candidate too
    for formula, folder in (("minimum", "teacher_original"),
                            ("addition", "teacher_script/addition/professor_settings")):
        row = {"config_id": f"TEACHER-ORIGINAL-{formula.upper()}", "engine": "teacher_original",
               "formula": formula, "physical_id": 0, "finance_id": 0, **PROFESSOR}
        for c, res in zip(cases, fc.csv(src / folder / "results.csv").to_dict("records")):
            row.update({f"{k}_{c['id']}": res[k] for k in METRICS})
        frames.append(pd.DataFrame([row]))
    out = pd.concat(frames, ignore_index=True)
    out["n_changed_from_professor"] = n_different(out)
    return out


def select(cands, cases):
    picks = []
    for c in cases:
        i = c["id"]
        obj, worst, n10 = mode_scores(cands[[f"{k}_{i}" for k in METRICS]].to_numpy(),
                                      [c["target"][k] for k in METRICS])
        frame = cands.assign(objective=obj, worst=worst, within_10=n10,
                             not_minimum=(cands.formula != "minimum").astype(int))
        # ties: fewer changes from professor, then minimum formula
        frame = frame.sort_values(["objective", "n_changed_from_professor", "not_minimum", "config_id"],
                                  kind="stable")
        best = frame.iloc[0]
        ties = int((frame.objective == best.objective).sum())
        per_source = (frame.groupby(["engine", "formula"], sort=False).head(1)
                      [["engine", "formula", "config_id", "objective", "worst", "within_10"]])
        picks.append({"case": c, "best": best, "exact_ties": ties, "per_source": per_source})
    return picks


def fonts():
    d = fc.FONT_DIR
    f = lambda n, b: ImageFont.truetype(str(d / ("DejaVuSans-Bold.ttf" if b else "DejaVuSans.ttf")), n)
    return {"bar": f(40, True), "title": f(34, True), "text": f(25, False), "metric": f(32, True),
            "small": f(22, False)}


def annotated(pick, row, settings, plot_path, out_path):
    c, best = pick["case"], pick["best"]
    f = fonts()
    plot = Image.open(plot_path).convert("RGB")
    plot = plot.resize((1700, round(plot.height * 1700 / plot.width)), Image.Resampling.LANCZOS)
    p, fz = settings["physics"], settings["finance"]
    lines = [f"Physical: TES charge {p['tes_charge_mwth']:g} / discharge {p['tes_discharge_mwth']:g} MWth "
             f"({p['tes_rating']}), PV {p['pv_model']}, reactor {p['reactor_2025_mwth']:g} MWth,",
             f"   SOEC-off allocation {p['inactive_soec']}, Mode 3 check {p['soc_switch']}",
             f"Financial: real rate {100 * fz['real_rate']:.2f}%, {fz['years']} years, variable cost "
             f"{fz['variable_basis']}, LCOE denominator {fz['load_basis']},",
             f"   balance of plant {fz['balance_of_plant']}, decommissioning {fz['decommissioning']}"]
    head = 130 + 44 * len(lines) + 60 + 3 * 46 + 100
    canvas = Image.new("RGB", (1780, head + plot.height + 60), "white")
    d = ImageDraw.Draw(canvas)
    d.rectangle((0, 0, 1780, 70), fill="#0b3d6e")
    src = SOURCE_LABEL[(best.engine, best.formula)]
    d.text((25, 13), f"Best for Mode {c['mode']} ({c['id']})  |  {best.config_id}", font=f["bar"], fill="white")
    d.text((25, 85), f"Source: {src}", font=f["title"], fill="#0b3d6e")
    y = 140
    for line in lines:
        d.text((25, y), line, font=f["text"], fill="black")
        y += 44
    d.text((25, y), "Selected for this mode only. Excel data, capacities, DF, TES factor, efficiencies and unit "
           "prices unchanged.", font=f["small"], fill="#555555")
    y += 50
    for k in METRICS:
        v, t = row[k], c["target"][k]
        err = 100 * (v / t - 1)
        txt = (f"{k}: {100 * v:.4f}%   (paper {100 * t:.2f}%,  error {err:+.1f}%)" if k != "LCOE" else
               f"LCOE: ${v:.6f}/kWh   (paper ${t:.4f}/kWh,  error {err:+.1f}%)")
        d.text((25, y), txt, font=f["metric"], fill="#08705d" if abs(err) <= 10 else "#a53e35")
        y += 46
    extra = f"Lowest TES load factor in hours 4350-4550: {row['LF_min_h4350_4550']:.3f}"
    if c["mode"] > 1:
        extra = f"Hydrogen: {row['H2_t']:.1f} t/year   |   " + extra
    d.text((25, y + 8), extra, font=f["text"], fill="black")
    d.text((25, y + 52), "Green = within 10% of the paper; red = outside 10%. Plot drawn by the teacher's "
           "original plotting function.", font=f["small"], fill="#555555")
    canvas.paste(plot, (40, head))
    d.text((25, head + plot.height + 15), f"Compare with paper Figure {7 + c['mode']}.", font=f["small"],
           fill="#555555")
    canvas.save(out_path)


def run(source, output):
    src, out = Path(source).resolve(), Path(output).resolve()
    if out.exists():
        raise FileExistsError("Use a new output directory; previous work is preserved.")
    spec = json.loads((src / "experiment_config.json").read_text())
    cases = spec["benchmarks"]
    physical_grid, finance_grid = fc.grids(spec)
    phys, fin = fc.csv(src / "physical_settings.csv"), fc.csv(src / "finance_settings.csv")
    before = pr.original_hashes()
    inputs = pr.load_inputs(pr.ROOT / "InputData.xlsx")
    picks = select(candidates(src, cases, phys, fin), cases)
    out.mkdir(parents=True)
    summary, report = [], []
    for pick in picks:
        c, best = pick["case"], pick["best"]
        engine = "teacher_script" if best.engine == "teacher_original" else best.engine
        if best.engine == "teacher_original":
            physics, finance = fe.TEACHER_PHYSICS, fe.TEACHER_FINANCE
        else:
            physics, finance = physical_grid[best.physical_id - 1], finance_grid[best.finance_id - 1]
        folder = out / f"Mode{c['mode']}_{best.config_id}"
        rows = fc.export(inputs, cases, engine, best.formula, physics, finance, folder)
        row = rows[cases.index(c)]
        for k in METRICS:
            # re-simulated value must equal the ranking value
            assert row[k] == best[f"{k}_{c['id']}"], "Re-simulated value must equal the ranking value."
        settings = json.loads((folder / "settings.json").read_text())
        name = f"Best_Mode{c['mode']}_{c['id']}_{best.config_id}.png"
        annotated(pick, row, settings, folder / tb.FIGURES[c["mode"] - 1], out / name)
        rec = {"mode": c["mode"], "design": c["id"], "source": SOURCE_LABEL[(best.engine, best.formula)],
               "config_id": best.config_id, "objective": float(best.objective), "worst_relative_percent": float(best.worst),
               "entries_within_10_percent": int(best.within_10), "exact_ties": pick["exact_ties"],
               "n_changed_from_professor": int(best.n_changed_from_professor),
               **{k: row[k] for k in METRICS}, **{f"{k}_paper": c["target"][k] for k in METRICS},
               **{f"{k}_relative_error_percent": 100 * (row[k] / c["target"][k] - 1) for k in METRICS},
               "H2_t": row["H2_t"], "LF_min_h4350_4550": row["LF_min_h4350_4550"],
               **settings["physics"], **settings["finance"], "plot": name}
        summary.append(rec)
        pick["per_source"].to_csv(out / f"Mode{c['mode']}_best_per_source.csv", index=False)
        report.append(pick["per_source"].assign(mode=c["mode"]))
    pd.DataFrame(summary).to_csv(out / "best_per_mode.csv", index=False)
    pd.concat(report).to_csv(out / "best_per_source_all_modes.csv", index=False)
    assert pr.original_hashes() == before
    pr.save_json(out / "manifest.json", {"source_run": str(src), "selection": __doc__.split("\n\n")[1],
                                         "best": [{k: r[k] for k in ("mode", "config_id", "source")} for r in summary]})
    return summary


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    a = ap.parse_args()
    for r in run(a.source, a.output):
        print(json.dumps({k: r[k] for k in ("mode", "config_id", "source", "DPSP", "EWP", "LCOE",
                                            "exact_ties", "n_changed_from_professor")}))


if __name__ == "__main__":
    main()
