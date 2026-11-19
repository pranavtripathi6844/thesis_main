# Strict compromise-model calibration

This is the second calibration experiment. The goal is a documented compromise
with the 2025 HTSE paper, before any RL, noise, malfunctions or capacity
optimization. Only ambiguous or unspecified settings may vary.

## Run

After the environment setup in `README.md`:

```sh
python -m unittest discover -s tests -v
python compromise_search.py --output results/strict_compromise_new
```

The completed local experiment uses `results/strict_compromise_20261029/`.
Open its `index.html`. Never reuse an existing output directory.

## What changes, and why?

`compromise_config.json` contains the complete ranges and provenance:

| Setting | Values | Reason it is searched |
|---|---|---|
| TES charging limit | 5, 10, 15, 20, 25 MWth | Eqs.19–20 give symbolic TES limits, with no located numeric value. |
| TES discharging limit | 5, 7.5, 10, 15, 25 MWth | Same ambiguity; 7.5 resolves the low-discharge region. |
| Extra thermal eligibility gate | Original / resource-consistent | The original code adds a heat gate beyond the stated minimum electrical input. |
| Real discount rate | 2%, 3.9215686%, 6%, 8%, 10% | Financial rate values were not located; the middle value is the original 6% nominal / 2% inflation combination. |
| Project horizon | 20, 30, 40 years | The financial equations define a horizon without a located numerical value. |
| Reactor variable-cost units | Thermal / electrical MWh | Appendix A3 lists $/MWh without explicitly identifying which. |
| Decommissioning boundary | Discounted retirement liability / project boundary | Explicit alternative accounting treatments; not equally established by the papers. |

That gives **50 physical × 60 financial = 3,000 full configurations**.
Each physical configuration is simulated for all three published modes.
Financial variants reuse identical dispatch arrays, then recompute financial
results independently.

The 10 MW maximum in Appendix A2 belongs to the **battery**, not TES.
Search bounds are analyst choices, not newly discovered equipment ratings.
The original 60-year reactor retirement assumption is retained and remains
unsourced.

## What stays fixed?

- The original Excel time series, with no scaling, shifts, noise or resampling.
- The 25 MWth main-text reactor value and 40% conversion efficiency.
- Original PV/wind equations and all their published parameters.
- Lossless TES, unit charging/discharging efficiency, initial/minimum 5% TES.
- Every selected Table 5 capacity, demand factor and TES threshold.
- Published component costs and lifetimes, hydrogen prices and SOEC minimum.
- Joint heat/electricity-limited hydrogen, capped at SOEC electrical capacity.

The 2025 appendix's contradictory 50 MWth reactor value is not explored.
The printed additive hydrogen equation is not selectable because its literal
interpretation previously failed the resource checks.

Sources: 2025 paper pp.5, 8–10 and Appendix A2/A3 pp.16–17; inherited financial
equations in the 2024 paper p.6. The 2024 battery system's results are not mixed
into this model's numerical targets.

## A separate graph for every complete configuration

Every case has its own directory:

```text
cases/P001_E001/
    comparison.png
    index.html
    parameters.json
    metrics.csv
```

The PNG shows:

- Complete physical and financial assumptions and the unique case ID.
- All nine paper/original/candidate numerical comparisons.
- TES, hydrogen, shortage, nuclear waste and renewable waste curves for all
  three modes, over all 8,760 hours.
- Consistent axes across cases, with original and candidate curves overlaid.

The case's HTML page also shows the published paper figure beside each
candidate/original mode overlay. The paper references come from the existing
`../early_runs/paper_plot_comparison_20261026/` crops; use `--paper-images` if stored
elsewhere. If these files are absent, the run records that fact rather than
inventing curves.

**Financial-only changes cannot change hourly curves.** Their dashboards are
separate and their LCOE values differ, but identical dispatch is expected.
Do not interpret repeated curves as missing simulations.

Every physical case also retains full-size mode plots and compressed hourly
arrays in `physical/Pxxx/`. The gallery searches IDs and assumptions.

## How the compromise is shortlisted

No professor-approved threshold exists yet. Report all nine targets in
exploratory **10%, 20%, 30%, 50% relative-error bands**, without hiding absolute
errors. A case is in a band only if all nine target errors are within it.

The default *provisional* ranking is:

```text
0.5 × (RMS of nine relative numerical errors / 0.25)
+ 0.5 × approximate feature score
```

The feature score uses only the three approximate TES dips already recorded
in `search_config.json`, plus a penalty if Mode 3 has no hydrogen events in
hours 7000–8760. It is **not whole-curve similarity**, image-pixel agreement
or an RMSE against author hourly data. Human inspection of the saved plots
remains necessary.

- `shortlist.csv` keeps the best financial case for each of five distinct
  physical configurations.
- `selection_weight_sensitivity.csv` shows the chosen case at numerical
  weights 0, 0.25, 0.5, 0.75 and 1.
- `band_counts.csv` reports how many valid cases meet each numerical band.
- `improvement_vs_original.csv` reports the signed change in each target error.
- `REPORT.md` records limitations and explicitly identifies any failed bands.

These weights and bands are transparent analyst choices, not evidence that
a candidate has been accepted. Held-out published capacities are checked
after selection; they are not independent weather validation.

## Reuse the selected configuration

The compact, Git-trackable `compromise_selection.json` is identical to the
selected run's snapshot. To use it without distributing the entire gallery:

```sh
python compromise_model.py --selection compromise_selection.json \
  --output results/frozen_compromise_new
```

Or select the snapshot from a particular experiment:

```sh
python compromise_model.py \
  --selection results/strict_compromise_20261029/selected_compromise.json \
  --output results/frozen_compromise_new
```

This runs only the frozen provisional model, not the grid. It produces three
plots, hourly arrays and `results.csv`. It verifies source/input hashes,
checks fixed capacities and parameters, and refuses to overwrite outputs.
Actual TES power limits come from `physics`; the `fixed_params` record retains
the original legacy `TES.P_max` for provenance.

`selected_compromise.json` freezes assumptions; it does not certify that the
paper was reproduced. The constant-generation Mode 1 balance bound remains
a diagnostic. If no reasonably narrow numerical band is achieved, report
that limitation rather than renaming a large discrepancy an acceptable fit.

## Sharing

Source code, tests, configuration and this guide are suitable for Git.
Generated dashboards can be large and remain under ignored `results/`.
Distribute the complete run as a separate artifact when all plots are needed.
The gallery is offline; no server, upload or external service is required.
Review permissions before sharing Excel inputs or paper figure crops.
