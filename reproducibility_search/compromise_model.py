"""Run the frozen compromise configuration without repeating the grid search."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

import grid_search as gs
from compromise_search import economics

HERE = Path(__file__).resolve().parent


def run_selected(selection_path, model_path, input_path, output, plots=True):
    selection_path, model_path, input_path, output = map(Path, (selection_path, model_path, input_path, output))
    selection = json.loads(selection_path.read_text())
    # frozen result is only valid for the exact same files
    if gs.sha256(model_path) != selection["model_sha256"] or gs.sha256(input_path) != selection["input_sha256"]:
        raise ValueError("Model or Excel differs from the frozen calibration; recalibration is required.")
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite {output}")
    model = gs.load_model(model_path)
    params = selection["fixed_params"]
    if model.create_params() != params:
        raise ValueError("Frozen physical parameters do not match the original model.")
    physics = gs.Physics(**selection["physics"])
    data = gs.load_data(input_path)
    components = gs.renewable_components(model, data, params)
    output.mkdir(parents=True)
    rows = []
    for item in selection["training"]:
        mode, c = item["mode"], item["config"]
        # capacities must be the teacher's CFG1-3
        if c != getattr(model, f"CFG{mode}"):
            raise ValueError("Frozen capacities differ from the original fixed-capacity benchmark.")
        sim = gs.simulate(data, gs.renewable(c, components), params, c, mode, physics, selection["balance_tolerance_mwh"])
        if not sim.audit["valid"]:
            raise RuntimeError(f"Frozen model failed physics checks in mode {mode}")
        rows.append({"mode": mode, **sim.metrics, **economics(sim, params, selection["economics"]), **sim.audit})
        np.savez_compressed(output / f"mode{mode}_hourly.npz", **sim.series)
        if plots:
            gs.plot_mode(sim, output / f"mode{mode}.png", f"Frozen provisional compromise, Mode {mode}")
    pd.DataFrame(rows).to_csv(output / "results.csv", index=False)
    gs.save_json(output / "selection.json", selection)
    return rows


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--selection", type=Path, required=True)
    p.add_argument("--model", type=Path, default=HERE.parent / "teacher_implementation" / "NRHES_Model.py")
    p.add_argument("--input", type=Path, default=HERE.parent / "teacher_implementation" / "InputData.xlsx")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--no-plots", action="store_true")
    a = p.parse_args()
    rows = run_selected(a.selection.resolve(), a.model.resolve(), a.input.resolve(), a.output.resolve(), not a.no_plots)
    print(pd.DataFrame(rows)[["mode", "DPSP", "EWP", "LCOE", "H2_tonnes"]].to_string(index=False))


if __name__ == "__main__":
    main()
