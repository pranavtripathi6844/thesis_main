"""Recover approximate common inputs from the 2025 paper's vector Figure 5.

These are digitized plotted inputs, not the authors' original data. PDF coordinate
quantization and removed collinear vertices make exact hourly recovery impossible.
Ordered nearest-hour matching retains multiple vertices at quantized identical x.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from pypdf import PdfReader

import grid_search as gs

HERE = Path(__file__).resolve().parent
PAPER = HERE.parent / "paper" / "1 - Techno-economic assessment of a smTMSR-based nuclear–renewable.pdf"
# Axis calibration uses labelled ticks, NOT the padded automatic plot frames.
# A shared x frame uses the consistent lower three axes; the solar frame differs
# by about 0.06 points. That is within the documented ~2-hour x uncertainty.
XFRAME = (156.6796, 485.6450)
AXES = {
    "G_Tot": (320, 375),
    "T_a": (245, 296),
    "V_anem": (170, 216),
    "P_grid": (92, 138),
}
TICKS = {
    "G_Tot": [(326.8806,0),(340.7654,400),(354.7185,800),(368.6033,1200)],
    "T_a": [(248.7674,-20),(262.6546,0),(276.6111,20),(290.4983,40)],
    "V_anem": [(174.3792,0),(189.4358,4),(204.4920,8)],
    "P_grid": [(99.4417,10),(113.3982,12),(127.2854,14)],
}


def ordinate(name, y):
    ticks = np.array(TICKS[name])
    slope, intercept = np.polyfit(ticks[:,0],ticks[:,1],1)
    return slope*np.asarray(y)+intercept


def vector_paths(page):
    matrix, stack, points, color = np.eye(3), [], [], None
    paths = []
    for operands, op in page.get_contents().operations:
        if op == b"q":
            stack.append((matrix.copy(), color))
        elif op == b"Q":
            matrix, color = stack.pop()
        elif op == b"cm":
            a,b,c,d,e,f = map(float, operands)
            matrix = matrix @ np.array([[a,c,e],[b,d,f],[0,0,1.]])
        elif op in (b"RG", b"SC", b"SCN"):
            color = tuple(str(v) for v in operands)
        elif op in (b"m", b"l"):
            points.append((matrix @ [float(operands[0]),float(operands[1]),1])[:2])
        elif op in (b"S", b"s", b"f", b"f*", b"F", b"n", b"B", b"B*"):
            # keep only the long blue data curves
            if len(points) > 100 and color == ("0","0","1"):
                paths.append(np.array(points))
            points = []
    return paths


def ordered_hours(x, n=8760):
    """Assign strictly increasing integer hours despite ~1.8h x quantization."""
    approximate = (x-XFRAME[0]) / (XFRAME[1]-XFRAME[0])*n
    candidates = np.floor(approximate).astype(int)[:,None] + np.arange(-3,5)[None,:]
    valid = (candidates >= 1) & (candidates <= n)
    valid[0] &= candidates[0] == 1
    valid[-1] &= candidates[-1] == n
    cost = (candidates-approximate[:,None])**2
    cost[~valid] = np.inf
    previous = np.full_like(candidates, -1)
    # dynamic programming for strictly increasing hours
    for j in range(1,len(x)):
        pair = np.where(candidates[j-1][:,None] < candidates[j][None,:], cost[j-1][:,None], np.inf)
        best = pair.argmin(axis=0)
        cost[j] += pair[best,np.arange(candidates.shape[1])]
        previous[j] = best
    index = int(cost[-1].argmin())
    if not np.isfinite(cost[-1,index]):
        raise ValueError("No monotonic hourly assignment; check vector geometry.")
    hours = np.empty(len(x),dtype=int)
    for j in range(len(x)-1,-1,-1):
        hours[j] = candidates[j,index]
        index = previous[j,index]
    if not (np.diff(hours)>0).all():
        raise AssertionError("Nonmonotonic recovery.")
    return hours, approximate


def reconstruct(output):
    out = Path(output).resolve()
    if out.exists():
        raise FileExistsError("Use a new reconstruction directory.")
    source_hash = gs.sha256(PAPER)
    paths = vector_paths(PdfReader(PAPER).pages[7])
    groups = {k: [] for k in AXES}
    for a in paths:
        for name, (lower,upper,*_) in AXES.items():
            if a[:,1].min() >= lower and a[:,1].max() <= upper:
                groups[name].append(a)
    data = {"hours": np.arange(1,8761)}
    report = {}
    raw_vertices = []
    for name, chunks in groups.items():
        if not chunks:
            raise ValueError(f"No vector curve found for {name}")
        joined = chunks[0].copy()
        for chunk in chunks[1:]:
            # PDF splits strokes at 700 vertices and repeats the last endpoint.
            start = 1 if np.max(np.abs(joined[-1]-chunk[0])) < .002 else 0
            joined = np.vstack((joined,chunk[start:]))
        hours, approximate = ordered_hours(joined[:,0])
        values = ordinate(name,joined[:,1])
        if name in ("G_Tot","V_anem"):
            values = np.maximum(0,values)
        recovered = np.interp(data["hours"],hours,values)
        data[name] = recovered
        report[name] = {
            "vector_vertices": len(joined), "interpolated_missing_hours": 8760-len(joined),
            "maximum_hour_assignment_difference": float(np.abs(hours-approximate).max()),
            "min": float(recovered.min()), "max": float(recovered.max()),
            "annual_sum": float(recovered.sum()),
            "labelled_ticks_pdf_y_and_value": TICKS[name],
            "maximum_tick_fit_residual": float(np.abs(ordinate(name,np.array(TICKS[name])[:,0])
                                                     -np.array(TICKS[name])[:,1]).max()),
        }
        raw_vertices.extend({"series":name,"x_pdf":x,"y_pdf":y,"assigned_hour":h,"recovered_value":v}
                            for (x,y),h,v in zip(joined,hours,values))
    frame = pd.DataFrame(data)[["hours","G_Tot","T_a","V_anem","P_grid"]]
    if len(frame)!=8760 or not np.isfinite(frame.to_numpy()).all() or frame.P_grid.min()<=0:
        raise AssertionError("Invalid reconstructed inputs.")
    out.mkdir(parents=True)
    frame.to_csv(out/"figure5_inputs.csv",index=False)
    pd.DataFrame(raw_vertices).to_csv(out/"vector_vertices.csv",index=False)
    gs.save_json(out/"provenance.json",{
        "status":"approximate_vector_digitization_not_author_raw_data",
        "paper_sha256":source_hash,"paper_page":8,"figure":5,
        "method":"Blue vector strokes, composed PDF affine transforms, repeated chunk endpoints removed; ordered nearest-hour assignment; linear interpolation of omitted collinear vertices.",
        "x_frame_pdf_points":XFRAME,"series":report,
        "limitations":[
            "PDF coordinates are quantized. Hour assignments are approximate, not recovered timestamps.",
            "The plot omits some collinear vertices; missing hours are interpolated.",
            "Axis calibration and source rendering precision introduce ordinate uncertainty.",
            "No weather/load fitting to Table 5 targets is performed in this extraction.",
            "Neither author data identity nor exact curve values can be claimed."
        ]})
    print(frame.agg(["min","max","mean"]).to_string())
    print(report)
    return frame


if __name__=="__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output",type=Path,required=True)
    reconstruct(p.parse_args().output)
