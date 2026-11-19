# All-ten-row, two-track comparison

Run from this directory after installing `requirements.txt`:

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests -v
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python table5_calibration.py \
  --output results/table5_dual_new
```

An output directory must be new. Original Python, MATLAB and Excel files are
hash-protected, not edited. Older candidates remain available.

## Two tracks

1. **Fixed inputs:** exactly the supplied hourly Excel series.
2. **Input-calibrated scenario:** the same input series with shared, bounded
   transformations. It is **not recovered author data** and cannot establish
   an independent reproduction.

Both use the exact ten Table 5 capacities and control settings. Common settings
are shared across modes and rows; there are no independently tuned per-row data.
The published reactor rating, efficiencies, storage initial/minimum state,
prices and device designs do not change.

The config documents bounds and provenance. Input scenarios test a
peak-preserving irradiance exponent, a common load offset, and adjustment to
the zero-mean 31-day circular-smoothed seasonal load. The weather shift is
0 or +8 hours, applied together to irradiance, temperature and wind. The latter
tests a possible UTC/local-time mismatch: the supplied mean solar profile peaks
around hour 4-5 while load peaks around noon/evening. Metadata does **not**
confirm that hypothesis. Cyclic year-end wrapping is an explicit limitation.

The irradiance exponent preserves peak and zero irradiance. This matters:
reducing the peak indiscriminately can make Table 5 SOEC ratings exceed the
allowed peak renewable output. Every candidate checks that constraint.

## Search and acceptance

Sobol screening covers both PV interpretations, absolute/capacity-scaled TES
charge/discharge limits, and applicable time shifts. Differential evolution
refines the strongest branches within the configured budget. Finance shares a
20-year horizon, thermal variable-cost basis and retirement decommissioning;
the real rate is fitted. This is not an exhaustive search of all economics.

The fitting objective is worst relative error plus 0.1 times RMS relative error
on seven rows. Rows 1b, 2b and 3c do not influence fitting/selection. Those rows
were inspected in earlier experiments, so they are not pristine holdouts.
Both training and reserved errors are reported.

For acceptance, **each of DPSP, EWP and LCOE in all ten rows** must have absolute
relative error at most 5% or 10%. All 30 entries must pass. No percentage-point
tolerance, widened band, or average-only acceptance is used. An accelerated
dispatch evaluates candidates; selected models are independently rerun through
the full reference model, including separate electricity, heat, storage,
hydrogen-resource and total-energy audits. Disagreement stops export.

Annual matching does not validate paper figures. Selected candidates also
export TES minima near hour 4450 and late hydrogen events. Without the authors'
hourly series, no full author-curve RMSE is claimed.

## Browse and reuse

- `index.html`: every tested complete configuration, its settings and plot.
- `cases/Cxxxxx/candidate.png`: candidate-only plots for all ten rows,
  with settings, annual values, paper targets and relative errors.
  Min/max per-pixel envelopes retain brief hourly events; column scales are
  common across candidates.
- `all_metrics.csv`, `ranking.csv`, `track_comparison.csv`, `REPORT.md`.
- `fixed_input_energy_bounds.csv`: optimistic Mode 1 feasibility bounds.
- `selected/<track>/`: frozen settings, full reference audits, all ten
  hourly NPZ arrays, individual full-resolution row plots, changed-input
  statistics and the exact scenario input CSV.

Rerun either frozen selection, with no search:

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python table5_calibration.py \
  --selection results/table5_dual_new/selected/input_calibrated/selection.json \
  --output results/table5_frozen_new
```

All source/model/input hashes must match. Scenario adjustments are reproduced
from the original Excel, not silently written into it. Every distinct optimizer
configuration is recorded and plotted; repeated identical evaluations reuse the
same case. Full hourly arrays are retained for selected models rather than for
every rejected search point. No RL, malfunction model or published component
parameter fitting is included.

## Digitized Figure 5 source control

`figure5_reconstruction.py` extracts approximate hourly inputs directly from
the PDF's vector curves. It calibrates against labelled ticks, not padded frame
limits, and retains vertex/axis provenance and uncertainty. The result is
digitized plotted data, **not the authors' original hourly dataset**.

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python figure5_reconstruction.py \
  --output results/figure5_reconstructed_new
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python paper_input_check.py \
  --input results/figure5_reconstructed_new/figure5_inputs.csv \
  --output results/paper_input_control_new
```

The second command tests both PV interpretations and shared TES ratings/rate,
without tuning or shifting the reconstructed meteorology or demand. Every
configuration has a dedicated plot. Only the selected model retains complete
audited hourly arrays and full-size individual row plots.

For a no-search rerun, add `--case-settings table5_digitized_settings.json`.
That runner checks the reconstructed input hash. Do not use the generic
`table5_calibration.py --selection` entry point for digitized-source settings.

`TABLE5_RESULTS.md` records actual outcomes and the limits of each approach.
