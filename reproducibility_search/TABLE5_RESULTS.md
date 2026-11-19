# Table 5 follow-up: both approaches tested

## Outcome

**The requested 5–10% relative-error margin has not been achieved.**
Neither the unchanged-input model nor the input-calibrated scenario reproduces
all three metrics in any complete Table 5 row within 10%.

We evaluated **3,705 complete configurations**, each against all ten published
designs: 764 unchanged-input, 2,415 input-calibrated, and 526 additional
digitized-paper input controls. Every configuration has its own candidate-only
dashboard showing all ten rows, settings, annual results and errors.
Original Python, MATLAB and Excel files, and the previous P044 selection,
remain unchanged. No RL was started.

| Selected approach | RMS relative error, 30 entries | Worst relative error | Entries within 10% | Complete rows within 10% |
|---|---:|---:|---:|---:|
| Unchanged Excel, C00443 | 281.09% | 1014.15% | 10/30 | 0/10 |
| Input-calibrated scenario, C02533 | 45.66% | 94.72% | 10/30 | 0/10 |
| Literal digitized Figure 5 inputs, C00003 | 71.02% | 242.69% | 12/30 | 0/10 |

The calibrated scenario reduces RMS error by **83.76%** relative to the selected
unchanged-input model on the same ten-row benchmark. This is improvement, not
acceptance. All ten LCOE entries are within 10% in both main selections; their
dispatch metrics remain outside the requested band. No tested configuration
passes all 30 entries at either 5% or 10%.

The very large fixed-input error includes the newly tested Mode 3a EWP target
of **0.18%**. It is not directly comparable with earlier scores fitted only to
rows 1a, 2a and 3d. Three reserved rows do not enter this search's fitting
objective, but were inspected earlier and are not pristine external validation.

## Open the results

All paths below are relative to this directory.

- `results/table5_dual_comparison_v1/track_comparison.png`: exact signed
  errors side by side for all ten designs and all three selected approaches.
- `results/table5_dual_comparison_v1/index.html`: all 3,179 main configurations.
- `results/table5_dual_comparison_v1/REPORT.md`: search details and acceptance.
- `results/table5_dual_comparison_v1/selected/input_calibrated/representative_candidate_only.png`:
  full hourly candidate-only plots for rows 1a, 2a and 3d, corresponding to
  paper Figures 8, 9 and 10. The unchanged-input folder has its own equivalent.
- `results/table5_paper_digitized_v1/index.html`: all 526 source-derived controls.
- Each selected folder contains all ten individual row PNGs, hourly NPZ arrays,
  results CSV and separate physics audits.

## Closest numerical scenario, not a validated reproduction

Shared across all rows:

- Area/efficiency PV interpretation.
- Absolute TES charge/discharge limits: **24.6551 / 19.8650 MWth**.
- Real discount rate **9.45708%**, 20-year project, thermal variable-cost units,
  retirement decommissioning.
- Irradiance exponent **2.52713**, load offset **+0.432418 MW**,
  seasonal-load gain **0.756191**, and joint weather shift **+8 hours**.
- Published reactor rating, conversion and storage efficiencies, initial/minimum
  storage, prices, all ten capacities and published control settings unchanged.

These input transformations are explicitly analyst-defined. They are **not**
verified corrections or recovered author weather. They change annual irradiation
from 2,789.44 to 1,644.03 kWh/m² and annual demand from 101,306.90 to
105,094.87 MWh. Different Table 5 capacities/control values remain design inputs,
not independently fitted physical models.

For example:

| Row | Paper → scenario DPSP (%) | Paper → scenario EWP (%) | Paper → scenario LCOE ($/kWh) |
|---|---|---|---|
| 1a | 0.970 → 1.3242 | 4.020 → 2.2418 | 0.094012 → 0.092243 |
| 2a | 0.900 → 1.0940 | 3.800 → 2.0752 | 0.116200 → 0.114083 |
| 3a | 0.410 → 0.7984 | 0.180 → 0.2983 | 0.116400 → 0.116430 |
| 3d | 0.900 → 1.6409 | 0.620 → 0.3504 | 0.093100 → 0.087433 |

Approximate TES minima around hours 4350–4550 improve to **0.7347 / 0.8681**
for 1a/2a, compared with earlier visual anchors near 0.71/0.88. Row 3d remains
at **0.3245**, versus an approximate paper anchor near 0.088. Annual cost fitting
and partial shape agreement therefore do not establish Figure 10 reproduction.
These anchors are approximate plot features, not author hourly observations.

## What the source reconstruction adds

`figure5_reconstruction.py` extracts blue vector strokes directly from page 8,
composes PDF transforms, removes repeated stroke endpoints, and assigns ordered
hour indices. Collinear points omitted by the PDF are interpolated.

High-resolution inspection established that the automatic plot frames are padded.
The final reconstruction regresses on **labelled ticks**, not frame limits.
The preliminary 1,776 kWh/m² estimate was discarded; its directory is marked
`SUPERSEDED.md` and is not used by any selected model.

Final approximate Figure 5 inputs:

| Quantity | Supplied Excel | Digitized Figure 5 |
|---|---:|---:|
| Annual irradiation (kWh/m²) | 2789.44 | **1925.41** |
| Annual grid demand (MWh) | 101306.90 | **103199.11** |

The irradiation estimate is about **31.0% lower** than the supplied Excel.
This is substantive evidence of an input discrepancy, not proof that every
digitized hourly value is exact. Ordered point assignment differs from the
continuous PDF x-coordinate by up to about 0.94 hour; missing vertices and
ordinate quantization also limit precision.

The additional source control does **not** tune or shift digitized weather/load.
It screens shared TES ratings and real rate only. Its best fitting selection is
the 25/25 MWth, 10% rate area/efficiency control. It still misses Table 5.
This is a more source-grounded reference than an arbitrary adjusted weather
series, even though its numerical fit is worse than the synthetic calibration.

## Reuse

Git-shareable snapshots:

- `table5_fixed_selection.json`
- `table5_input_calibrated_selection.json`
- `table5_digitized_settings.json`

The first two were rerun independently and reproduced **all ten results exactly**:

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python table5_calibration.py \
  --selection table5_input_calibrated_selection.json \
  --output results/calibrated_replay_new
```

Use `table5_fixed_selection.json` for the unchanged-input model.
For the digitized-paper case, first reconstruct the common inputs, then use its
dedicated runner:

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python figure5_reconstruction.py \
  --output results/figure5_rebuilt
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python paper_input_check.py \
  --input results/figure5_rebuilt/figure5_inputs.csv \
  --case-settings table5_digitized_settings.json \
  --output results/digitized_replay_new
```

## Validation and next decision

- **41 regression tests passed.**
- 3,705 unique candidate dashboard PNGs checked, with settings/results per case.
- Main run: 31,790 row records, 15,900 local links and 20 selected hourly files checked.
- Source control: all 526 case dashboards and ten selected hourly files checked.
- All 30 selected hourly simulations pass separate electricity, heat, storage,
  hydrogen-resource and design checks. Maximum main-run balance residual is
  about **1.14 × 10⁻¹³ MWh**.
- Protected original-file hashes unchanged; no widened acceptance bands.
- Numerical optimization is budget-limited, not a proof of global optimality.
  No full author-curve RMSE or independent reproduction is claimed.

**Recommendation:** keep these as diagnostic models, not an accepted thesis
reproduction. The next useful evidence is the authors' hourly inputs and exact
dispatch implementation, especially TES rejection/shortage accounting, HTSE
eligibility/allocation, and the conflicting PV specifications. Those can resolve
the remaining physical mismatch without inventing per-row inputs or sacrificing
energy balance. Do not start RL on the claim that Table 5 has been reproduced.
