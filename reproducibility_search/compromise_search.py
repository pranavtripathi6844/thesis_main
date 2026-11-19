"""Strict compromise calibration, with a unique dashboard for every full case."""
from __future__ import annotations

import argparse
import html
import importlib.metadata
import itertools
import json
import platform
import shutil
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import matplotlib.font_manager as fm
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from PIL import Image, ImageDraw, ImageFont

import grid_search as gs

HERE = Path(__file__).resolve().parent
PHYSICAL_KEYS = {"charge_mwth", "discharge_mwth", "thermal_gate"}
ECONOMIC_KEYS = {"real_rate", "project_years", "variable_cost_basis", "decommissioning"}
PAPER_IMAGES = ("Paper_Fig8_Mode1.png", "Paper_Fig9_Mode2.png", "Paper_Fig10_Mode3.png")


def load_spec(path):
    spec = json.loads(Path(path).read_text())
    for key, allowed in (("physical_grid", PHYSICAL_KEYS), ("economic_grid", ECONOMIC_KEYS)):
        grid = spec[key]
        if set(grid) != allowed or any(not isinstance(v, list) or not v for v in grid.values()):
            raise ValueError(f"{key} must contain only {sorted(allowed)}, with nonempty lists.")
        if any(len({json.dumps(x, sort_keys=True) for x in v}) != len(v) for v in grid.values()):
            raise ValueError("Duplicate grid values are not independent configurations.")
    physical = [gs.Physics(**dict(zip(spec["physical_grid"], x)))
                for x in itertools.product(*spec["physical_grid"].values())]
    economic = [dict(zip(spec["economic_grid"], x))
                for x in itertools.product(*spec["economic_grid"].values())]
    for e in economic:
        gs.Economics(**{k: v for k, v in e.items() if k != "project_years"})
        if isinstance(e["project_years"], bool) or not isinstance(e["project_years"], int) or not 1 <= e["project_years"] <= 60:
            raise ValueError("Project years must be an integer in [1,60].")
    s = spec["selection"]
    if (not 0 <= s["numerical_weight"] <= 1 or not np.isfinite(s["numerical_reference_relative_error"])
            or s["numerical_reference_relative_error"] <= 0):
        raise ValueError("Invalid explicit selection weights/scales.")
    if (not np.isfinite(s["missing_late_hydrogen_feature_penalty"])
            or s["missing_late_hydrogen_feature_penalty"] < 0 or s["shortlist_physical_cases"] < 1):
        raise ValueError("Invalid feature penalty or shortlist size.")
    if not spec["reporting_bands_percent"] or any(not np.isfinite(b) or b <= 0 for b in spec["reporting_bands_percent"]):
        raise ValueError("Reporting bands must be finite and positive.")
    base = json.loads((Path(path).parent / spec["base_config"]).read_text())
    return spec, base, physical, economic


def economics(sim, params, settings):
    return gs.economic_metrics(sim, params, gs.Economics(**{
        k: v for k, v in settings.items() if k != "project_years"
    }), settings["project_years"])


def numerical_errors(rows, training, bands):
    if len(rows) != len(training) or not rows:
        raise ValueError("Every target mode requires exactly one result row.")
    relative = np.array([
        [(row[k] - item["target"][k]) / item["target"][k] for k in gs.METRICS]
        for row, item in zip(rows, training)
    ])
    worst = float(np.abs(relative).max())
    return {
        "numerical_relative_rms": float(np.sqrt(np.mean(relative ** 2))),
        "numerical_relative_mean_absolute": float(np.abs(relative).mean()),
        "minimum_all_metric_band_percent": 100 * worst,
        **{f"within_{b:g}_percent": bool(worst <= b / 100 + 1e-12) for b in bands},
    }


def shape_features(sims, base, spec):
    features = gs.plot_features(sims, base["plot_features"])
    tes = float(np.mean([abs(r["z"]) for r in features]))
    # penalty if mode 3 makes no H2 at all late in the year
    missing_late = int(sims[2].metrics["late_H2_active_hours"] == 0)
    return {
        "approximate_TES_feature_score": tes,
        "missing_late_H2": missing_late,
        "shape_feature_score": tes + missing_late * spec["selection"]["missing_late_hydrogen_feature_penalty"],
        "mode1_RE_waste_MWh": sims[0].metrics["WER_MWh"],
    }, features


def score_case(summary, spec, weight=None):
    s = spec["selection"]
    weight = s["numerical_weight"] if weight is None else weight
    # weighted mix of number error and plot shape
    return (weight * summary["numerical_relative_rms"] / s["numerical_reference_relative_error"]
            + (1 - weight) * summary["shape_feature_score"])


def comparison_overview(sims, originals, limits, path, title):
    fields = [("LF", "TES load factor"), ("H2", "Hydrogen (kg/h)"),
              ("DPS", "Shortage (MWh)"), ("WEN", "Nuclear waste (MWhth)"),
              ("WER", "RE waste (MWh)")]
    fig, axes = plt.subplots(5, 3, figsize=(15, 8.4), sharex=True, constrained_layout=True)
    for m, (sim, old) in enumerate(zip(sims, originals)):
        for n, (key, label) in enumerate(fields):
            ax = axes[n, m]
            ax.plot(old.series["Hour"], old.series[key], color="tab:orange", lw=0.5, alpha=0.7, label="Original")
            ax.plot(sim.series["Hour"], sim.series[key], color="tab:blue", lw=0.5, label="Candidate")
            ax.set_xlim(1, 8760)
            ax.set_ylim(0, limits[key])
            ax.grid(alpha=0.2)
            ax.tick_params(labelsize=7)
            if m == 0:
                ax.set_ylabel(label, fontsize=8)
            if n == 0:
                ax.set_title(f"Mode {m + 1}", fontsize=10)
            if n == 4:
                ax.set_xlabel("Hour", fontsize=8)
    axes[0, 0].legend(fontsize=7, loc="upper right")
    fig.suptitle(title + "\nCommon axes across all cases; orange=original, blue=candidate", fontsize=10)
    fig.savefig(path, dpi=100)
    plt.close(fig)


def dashboard(base_image, case, rows, originals, training, path):
    """Reuse only the curve raster; render a unique ID, assumptions and metrics."""
    width, header = base_image.width, 225
    picture = Image.new("RGB", (width, base_image.height + header + 32), "white")
    picture.paste(base_image, (0, header))
    draw = ImageDraw.Draw(picture)
    font_path = fm.findfont("DejaVu Sans")
    font = ImageFont.truetype(font_path, 15)
    title = ImageFont.truetype(font_path, 20)
    draw.text((15, 8), f"{case['case_id']} | Strict compromise calibration | {'PASS' if case['valid'] else 'FAIL'} current physics checks", fill="black", font=title)
    lines = [
        f"TES charge/discharge {case['charge_mwth']:g}/{case['discharge_mwth']:g} MWth; heat gate={case['thermal_gate']}; hydrogen=minimum; SOEC=capped",
        f"Real rate {100 * case['real_rate']:.4g}%; project {case['project_years']} years; reactor variable costs={case['variable_cost_basis']}; decommissioning={case['decommissioning']}",
        f"RMS relative target error={100 * case['numerical_relative_rms']:.2f}%; worst error={case['minimum_all_metric_band_percent']:.2f}%; approximate feature score={case['shape_feature_score']:.3f}",
        "Each triple is PAPER / ORIGINAL / CANDIDATE. DPSP and EWP are percentages; LCOE is $/kWh.",
    ]
    for i, line in enumerate(lines):
        draw.text((15, 40 + i * 24), line, fill="black", font=font)
    for i, (row, old, item) in enumerate(zip(rows, originals, training)):
        chunks = []
        for key in gs.METRICS:
            scale = 1 if key == "LCOE" else 100
            chunks.append(f"{key}: {item['target'][key]*scale:.4g} / {old.metrics[key]*scale:.4g} / {row[key]*scale:.4g}")
        draw.text((15, 141 + i * 24), f"Mode {i+1}   " + "     ".join(chunks), fill="black", font=font)
    draw.text((15, picture.height - 26), "Open index.html beside this PNG for paper figure references and full-size mode plots. No author hourly RMSE is claimed.", fill="black", font=font)
    picture.save(path, compress_level=3)
    picture.close()


def case_page(folder, case, references, plots):
    pid = case["physical_id"]
    sections = []
    if plots:
        sections.append('<img class="dashboard" src="comparison.png" alt="Case-specific dashboard">')
        for mode in range(1, 4):
            reference = (f'<div><h3>Paper figure {mode+7}</h3><img src="../../references/{PAPER_IMAGES[mode-1]}" alt="Published paper figure"></div>'
                         if references else '<p>Paper image assets were not supplied.</p>')
            sections.append(
                f'<h2>Mode {mode}</h2><div class="pair">{reference}'
                f'<div><h3>Candidate versus original, all hourly values</h3>'
                f'<img src="../../physical/{pid}/mode{mode}.png" alt="Candidate and original mode overlay"></div></div>'
            )
    data = html.escape(json.dumps(case, indent=2))
    body = (
        '<!doctype html><html lang="en"><meta charset="utf-8">'
        f'<title>{case["case_id"]}</title><style>body{{font:15px system-ui;margin:20px}}'
        'img{max-width:100%;height:auto}.dashboard{width:1500px}.pair{display:grid;grid-template-columns:1fr 1fr;gap:20px}'
        '@media(max-width:850px){.pair{grid-template-columns:1fr}}pre{white-space:pre-wrap}</style>'
        f'<h1>{case["case_id"]}</h1><p>Provisional calibration case, not an accepted reproduction. '
        'Paper images are visual references, not digitized author time series.</p>'
        '<p><a href="../../index.html">All cases</a> | <a href="parameters.json">Parameters</a> | <a href="metrics.csv">Metrics</a></p>'
        + "".join(sections) + f"<h2>Complete case record</h2><pre>{data}</pre></html>"
    )
    (folder / "index.html").write_text(body)


def gallery(out, ranks, plots):
    entries = []
    for row in ranks.to_dict("records"):
        cid = row["case_id"]
        png = f' | <a href="cases/{cid}/comparison.png">PNG</a>' if plots else ""
        entries.append(
            f'<tr><td><a href="cases/{cid}/index.html">{cid}</a>{png}</td>'
            f'<td>{row["charge_mwth"]:g}/{row["discharge_mwth"]:g}; {row["thermal_gate"]}</td>'
            f'<td>{row["real_rate"]*100:.3g}%; {row["project_years"]}y; {row["variable_cost_basis"]}; {row["decommissioning"]}</td>'
            f'<td>{row["numerical_relative_rms"]*100:.2f}%</td><td>{row["minimum_all_metric_band_percent"]:.2f}%</td>'
            f'<td>{row["shape_feature_score"]:.3f}</td><td>{row["compromise_score"]:.3f}</td></tr>'
        )
    body = (
        '<!doctype html><html lang="en"><meta charset="utf-8"><title>Strict compromise calibration</title>'
        '<style>body{font:14px system-ui;margin:20px}table{border-collapse:collapse;width:100%}'
        'td,th{border:1px solid #bbb;padding:6px;text-align:left}th{position:sticky;top:0;background:#eee}</style>'
        f'<h1>{len(ranks)} complete calibration configurations</h1>'
        '<p>Every case has a dedicated page and, unless disabled, its own dashboard PNG. Economic-only variants intentionally have identical hourly curves.</p>'
        '<p>Order: current-physics validity, then explicit 50/50 numerical/approximate-feature score. This is not author hourly RMSE or professor-approved acceptance.</p>'
        '<p><a href="REPORT.md">Report</a> | <a href="shortlist.csv">Shortlist</a> | <a href="band_counts.csv">Error bands</a> | '
        '<a href="selected_compromise.json">Provisional selected configuration</a></p>'
        '<input id="filter" placeholder="Filter case IDs or assumptions" style="width:360px;padding:8px">'
        '<table><thead><tr><th>Case</th><th>TES MWth / gate</th><th>Finance</th>'
        '<th>RMS relative error</th><th>Worst target error</th><th>Feature score</th><th>Combined score</th></tr></thead><tbody>'
        + "".join(entries)
        + '</tbody></table><script>document.getElementById("filter").addEventListener("input",function(){'
        'const q=this.value.toLowerCase();document.querySelectorAll("tbody tr").forEach(r=>r.hidden=!r.textContent.toLowerCase().includes(q));});</script></html>'
    )
    (out / "index.html").write_text(body)


def protected_hashes(args, config):
    paths = [args.model, args.model.with_suffix(".m"), args.input, args.config,
             config, Path(__file__), HERE / "grid_search.py", HERE / "compromise_model.py"]
    return {str(p): gs.sha256(p) for p in paths if p.exists()}


def run(args):
    spec, base, physical, financial = load_spec(args.config)
    if args.output.exists():
        raise FileExistsError(f"Refusing to overwrite {args.output}")
    model = gs.load_model(args.model)
    params, data = model.create_params(), gs.load_data(args.input)
    for item in base["training"]:
        if item["config"] != getattr(model, f"CFG{item['mode']}"):
            raise ValueError("Published baseline capacities must match the original model.")
    if params["TES"]["sigma"] != 0 or params["TES"]["eta_ch"] != 1 or params["TES"]["eta_dis"] != 1:
        raise ValueError("Strict experiment requires the published lossless, unit-efficiency TES.")
    base_path = (args.config.parent / spec["base_config"]).resolve()
    hashes = protected_hashes(args, base_path)
    args.output.mkdir(parents=True)
    out = args.output
    (out / "physical").mkdir()
    (out / "cases").mkdir()
    gs.save_json(out / "run_config.json", {"search": spec, "published_targets": base, "fixed_params": params})
    components = gs.renewable_components(model, data, params)
    tolerance = base["balance_tolerance_mwh"]
    originals = [gs.legacy_simulate(model, data, gs.renewable(i["config"], components), params,
                                  i["config"], i["mode"], tolerance) for i in base["training"]]
    old_rows = [{"mode": s.mode, **s.metrics, **s.audit} for s in originals]
    pd.DataFrame(old_rows).to_csv(out / "original_results.csv", index=False)
    old_num = numerical_errors(old_rows, base["training"], spec["reporting_bands_percent"])
    old_shape, _ = shape_features(originals, base, spec)
    old_quality = {**old_num, **old_shape}
    old_quality["compromise_score"] = score_case(old_quality, spec)
    references = all((args.paper_images / p).is_file() for p in PAPER_IMAGES)
    if not args.no_plots and references:
        (out / "references").mkdir()
        for p in PAPER_IMAGES:
            shutil.copyfile(args.paper_images / p, out / "references" / p)
    if not args.no_plots:
        for s in originals:
            gs.plot_mode(s, out / f"original_mode{s.mode}.png", f"Original implementation, Mode {s.mode}")
    maxima = max(gs.renewable(i["config"], components).max() for i in base["training"])
    limits = {"LF": 1.05, "H2": max(i["config"]["P_SOEC"] for i in base["training"]) * params["HTSE"]["K_e"] * 1.05,
              "DPS": max(1, data.P_grid.max() - params["smTMSR"]["P_e"]) * 1.05,
              "WEN": params["smTMSR"]["P_th"] * 1.05, "WER": maxima * 1.05}
    all_rows, summaries, feature_rows = [], [], []
    for pi, physics in enumerate(physical, 1):
        pid = f"P{pi:03d}"
        print(f"Physical {pi}/{len(physical)}, {len(financial)} financial combinations: {asdict(physics)}", flush=True)
        folder = out / "physical" / pid
        folder.mkdir()
        sims = [gs.simulate(data, gs.renewable(i["config"], components), params, i["config"], i["mode"], physics, tolerance)
                for i in base["training"]]
        features, anchors = shape_features(sims, base, spec)
        feature_rows.extend({"physical_id": pid, **r} for r in anchors)
        base_image = None
        for s, old in zip(sims, originals):
            np.savez_compressed(folder / f"mode{s.mode}_hourly.npz", **s.series)
            if not args.no_plots:
                gs.plot_mode(s, folder / f"mode{s.mode}.png", f"{pid}, Mode {s.mode}: candidate versus original", other=old)
        if not args.no_plots:
            comparison_overview(sims, originals, limits, folder / "overview.png", f"{pid}: {asdict(physics)}")
            with Image.open(folder / "overview.png") as im:
                base_image = im.convert("RGB")
        for ei, econ in enumerate(financial, 1):
            cid = f"{pid}_E{ei:03d}"
            rows = [{
                "case_id": cid, "physical_id": pid, "economic_id": f"E{ei:03d}",
                "mode": s.mode, **asdict(physics), **econ, **s.metrics,
                **economics(s, params, econ), **s.audit,
            } for s in sims]
            for row, item in zip(rows, base["training"]):
                for k in gs.METRICS:
                    row[k + "_target"] = item["target"][k]
                    row[k + "_absolute_error"] = abs(row[k] - item["target"][k])
                    row[k + "_relative_error_percent"] = 100 * (row[k] - item["target"][k]) / item["target"][k]
            quality = numerical_errors(rows, base["training"], spec["reporting_bands_percent"])
            summary = {"case_id": cid, "physical_id": pid, "economic_id": f"E{ei:03d}",
                       **asdict(physics), **econ, **quality, **features,
                       "valid": all(s.audit["valid"] for s in sims)}
            summary["compromise_score"] = score_case(summary, spec)
            all_rows.extend(rows)
            summaries.append(summary)
            case_folder = out / "cases" / cid
            case_folder.mkdir()
            gs.save_json(case_folder / "parameters.json", summary)
            pd.DataFrame(rows).to_csv(case_folder / "metrics.csv", index=False)
            if not args.no_plots:
                dashboard(base_image, summary, rows, originals, base["training"], case_folder / "comparison.png")
            case_page(case_folder, summary, references, not args.no_plots)
        if base_image:
            base_image.close()
    frame = pd.DataFrame(all_rows)
    ranks = pd.DataFrame(summaries).sort_values(["valid", "compromise_score", "case_id"], ascending=[False, True, True])
    valid = ranks[ranks.valid]
    if valid.empty:
        raise RuntimeError("No case passes current physics checks; nothing selected.")
    best = valid.iloc[0].to_dict()
    weights = []
    for w in (0, 0.25, 0.5, 0.75, 1):
        chosen = min(valid.to_dict("records"), key=lambda x: (score_case(x, spec, w), x["numerical_relative_rms"], x["case_id"]))
        weights.append({"numerical_weight": w, "selected_case": chosen["case_id"], "score": score_case(chosen, spec, w)})
    shortlist = valid.drop_duplicates("physical_id").head(spec["selection"]["shortlist_physical_cases"])
    band_rows = [{"band_percent": b, "valid_cases_with_all_nine_targets_in_band": int(valid[f"within_{b:g}_percent"].sum())}
                 for b in spec["reporting_bands_percent"]]
    selected_rows = frame[frame.case_id == best["case_id"]].sort_values("mode").to_dict("records")
    selected_physics = gs.Physics(**{k: best[k] for k in ("charge_mwth", "discharge_mwth", "hydrogen_formula", "thermal_gate", "overflow")})
    selected_econ = {k: best[k] for k in sorted(ECONOMIC_KEYS)}
    selection = {
        "status": "provisional_compromise_not_professor_approved",
        "selected_case": best, "physics": asdict(selected_physics), "economics": selected_econ,
        "fixed_params": params, "training": base["training"],
        "model_sha256": gs.sha256(args.model), "input_sha256": gs.sha256(args.input),
        "balance_tolerance_mwh": tolerance, "search_selection_rule": spec["selection"],
        "limitations": ["Approximate features are not full waveform agreement.",
                        "Reactor retirement age 60 years is retained, unsourced.",
                        "No reported band is an agreed acceptance threshold.",
                        "Current physics checks are energy/resource/storage checks, not detailed plant validation."],
    }
    gs.save_json(out / "selected_compromise.json", selection)
    frame.to_csv(out / "all_results.csv", index=False)
    ranks.to_csv(out / "ranking.csv", index=False)
    shortlist.to_csv(out / "shortlist.csv", index=False)
    pd.DataFrame(weights).to_csv(out / "selection_weight_sensitivity.csv", index=False)
    pd.DataFrame(feature_rows).to_csv(out / "approximate_plot_features.csv", index=False)
    pd.DataFrame(band_rows).to_csv(out / "band_counts.csv", index=False)
    pd.DataFrame([{**c, "dashboard": f"cases/{c['case_id']}/comparison.png" if not args.no_plots else "",
                   "page": f"cases/{c['case_id']}/index.html"} for c in summaries]).to_csv(out / "case_manifest.csv", index=False)
    improvement = []
    for row, old, item in zip(selected_rows, old_rows, base["training"]):
        for k in gs.METRICS:
            before, after = abs(old[k] - item["target"][k]), abs(row[k] - item["target"][k])
            improvement.append({"mode": row["mode"], "metric": k, "paper": item["target"][k],
                                "original": old[k], "selected": row[k], "original_absolute_error": before,
                                "selected_absolute_error": after, "error_reduction_percent": 100 * (before-after)/before if before else None})
    pd.DataFrame(improvement).to_csv(out / "improvement_vs_original.csv", index=False)
    heldout = []
    for i in base["holdout"]:
        s = gs.simulate(data, gs.renewable(i["config"], components), params, i["config"], i["mode"], selected_physics, tolerance)
        heldout.append({"mode": s.mode, **s.metrics, **economics(s, params, selected_econ), **s.audit})
    pd.DataFrame(heldout).to_csv(out / "holdout_results.csv", index=False)
    if not args.no_plots:
        gs.metric_comparison_plot(originals, selected_rows, base["training"], out / "selected_metrics_comparison.png")
    bound = gs.energy_lower_bound(data, gs.renewable(base["training"][0]["config"], components), params, base["training"][0]["config"])
    for path, expected in hashes.items():
        if gs.sha256(Path(path)) != expected:
            raise RuntimeError(f"Protected source/input changed: {path}")
    summary = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "physical_cases": len(physical), "economic_cases": len(financial),
        "combined_cases": len(ranks), "valid_cases": len(valid),
        "dedicated_dashboard_count": 0 if args.no_plots else len(ranks),
        "selected_case": best, "original_quality": old_quality,
        "band_counts": band_rows, "minimum_achievable_all_metric_band_percent": float(valid.minimum_all_metric_band_percent.min()),
        "mode1_energy_lower_bound": bound, "protected_hashes": hashes,
        "python": platform.python_version(),
        "versions": {n: importlib.metadata.version(n) for n in ("numpy", "pandas", "matplotlib", "Pillow", "openpyxl")},
        "paper_reference_images_present": references,
        "protected_sources_unchanged": True,
    }
    gs.save_json(out / "summary.json", summary)
    lines = [
        "# Strict compromise-model calibration", "",
        f"Tested **{len(ranks)} complete configurations**: {len(physical)} physical × {len(financial)} financial.",
        f"Current physics checks passed: **{len(valid)}**. Dedicated dashboards: **{summary['dedicated_dashboard_count']}**.",
        f"Provisional balanced selection: **{best['case_id']}**, not a professor-approved reproduction.", "",
        "## Scope",
        "Only ambiguous or unspecified settings vary. No published costs, efficiencies, initial storage, Table 5 capacities/control factors or Excel series were changed.",
        "The 25 MWth main-text reactor value is retained; the contradictory appendix 50 MWth is not used.",
        "The original 60-year retirement assumption remains unsourced. Financial conventions are hypotheses.", "",
        "## Selection",
        "Score = 0.5 × (RMS of nine relative numerical errors / 0.25) + 0.5 × approximate shape-feature score.",
        "The feature score is mean absolute TES-anchor error / its stated uncertainty, plus 1 if late-year Mode 3 hydrogen is absent.",
        "These are analyst-selected weights and three approximate anchors, NOT full waveform similarity.",
        "See selection_weight_sensitivity.csv and shortlist.csv before accepting a compromise.", "",
        f"Selected RMS numerical error: **{100*best['numerical_relative_rms']:.2f}%**; original: **{100*old_quality['numerical_relative_rms']:.2f}%**.",
        f"Selected worst numerical target error: **{best['minimum_all_metric_band_percent']:.2f}%**.",
        f"Smallest all-nine relative band achieved anywhere in the grid: **{summary['minimum_achievable_all_metric_band_percent']:.2f}%**.", "",
        "|Reporting band|Valid cases with ALL nine targets within band|", "|---:|---:|",
        *[f"|{r['band_percent']}%|{r['valid_cases_with_all_nine_targets_in_band']}|" for r in band_rows], "",
        "## Individual target changes", "|Mode|Metric|Paper|Original|Selected|Absolute-error reduction %|", "|---|---|---:|---:|---:|---:|",
        *[f"|{r['mode']}|{r['metric']}|{r['paper']:.7g}|{r['original']:.7g}|{r['selected']:.7g}|{r['error_reduction_percent']:.2f}|" for r in improvement], "",
        "DPSP/EWP in this table are fractions, not percentages. Negative error reduction means a worse fit.", "",
        "## Limitation that the grid cannot remove",
        f"Mode 1 conservation lower bound: **{100*bound['minimum_possible_EWP']:.4f}% EWP**, versus paper **{100*base['training'][0]['target']['EWP']:.2f}%**.",
        "This is conditional on the unchanged inputs, generation and waste denominator. Do not widen acceptance merely to hide this discrepancy.", "",
        "## Browse and reuse",
        "Open index.html. Every full configuration has cases/ID/comparison.png, index.html, parameters.json and metrics.csv.",
        "Case pages show the published figure beside the candidate/original overlay for each mode. Shared curves across financial variants are intentional.",
        "All hourly arrays are under physical/Pxxx/. No author hourly RMSE is claimed.",
        "selected_compromise.json freezes the provisional model. Run compromise_model.py --selection <this file> --output <new folder>.",
        "Held-out published capacities were not used for selection; they are not independent weather validation.",
    ]
    (out / "REPORT.md").write_text("\n".join(lines) + "\n")
    gallery(out, ranks, not args.no_plots)
    print(json.dumps({"output": str(out), "cases": len(ranks), "selected": best,
                      "minimum_all_metric_band_percent": summary["minimum_achievable_all_metric_band_percent"]}, indent=2), flush=True)
    return summary


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", type=Path, default=HERE / "compromise_config.json")
    p.add_argument("--model", type=Path, default=HERE.parent / "teacher_implementation" / "NRHES_Model.py")
    p.add_argument("--input", type=Path, default=HERE.parent / "teacher_implementation" / "InputData.xlsx")
    p.add_argument("--paper-images", type=Path, default=HERE.parent / "early_runs" / "paper_plot_comparison_20261026")
    p.add_argument("--output", type=Path, default=HERE / "results" / ("compromise_" + datetime.now().strftime("%Y%m%d_%H%M%S")))
    p.add_argument("--no-plots", action="store_true")
    args = p.parse_args(argv)
    for k in ("config", "model", "input", "paper_images", "output"):
        setattr(args, k, getattr(args, k).resolve())
    return args


if __name__ == "__main__":
    run(parse_args())
