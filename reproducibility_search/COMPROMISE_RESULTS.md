# Strict calibration results, 29 October 2026

## What was delivered

**3,000 full configurations** were tested, varying only ambiguous/unspecified
settings, with **3,000 individually identified PNG dashboards and comparison
pages**. Published component costs, efficiencies, Table 5 capacities and
controller settings, and all Excel samples stayed fixed.

The original Python, MATLAB and Excel hashes remain unchanged. No RL, noise,
malfunctions or capacity optimization was introduced.

Browse `results/strict_compromise_20261029/index.html`. Each case page places
the paper figures beside candidate/original overlays. Financial variants
have separate dashboards and costs, but correctly share hourly trajectories.
See `COMPROMISE_GUIDE.md` for the ranges and their provenance.

## Provisional compromise, not an accepted reproduction

The explicit balanced numerical/approximate-feature ranking selects
**P044_E049**:

- TES charge/discharge: **25 / 7.5 MWth**.
- Hydrogen: joint-resource minimum, resource-consistent eligibility, capped SOEC.
- Real discount rate: **10%**, project horizon **20 years**.
- Reactor variable costs: thermal-MWh basis.
- Decommissioning: discounted retirement liability, retaining the original
  unsourced 60-year retirement assumption.

The rate is at the upper searched boundary. This is a fitted assumption,
not an independently established financial rate.

The same case wins at numerical weights 0.25, 0.5, 0.75 and 1.
Pure approximate-feature selection instead picks `P005_E051`, whose numerical
fit is very poor. These features do not measure full visual agreement.

| Mode | Metric | Paper | Original | Selected |
|---|---|---:|---:|---:|
| 1 | DPSP (%) | 0.9700 | 0.55765 | 0.49057 |
| 1 | EWP (%) | 4.0200 | 4.63146 | 13.64726 |
| 1 | LCOE ($/kWh) | 0.094012 | 0.068688 | 0.097712 |
| 2 | DPSP (%) | 0.9000 | 0.39695 | 0.48991 |
| 2 | EWP (%) | 3.8000 | 4.66345 | 6.79166 |
| 2 | LCOE ($/kWh) | 0.116200 | 0.083285 | 0.115078 |
| 3 | DPSP (%) | 0.9000 | 1.11899 | 0.49711 |
| 3 | EWP (%) | 0.6200 | 2.46184 | 2.32683 |
| 3 | LCOE ($/kWh) | 0.093100 | 0.065267 | 0.084951 |

All three LCOEs are within **10%** of the paper: errors **3.94%, 0.97%,
8.75%**. Their absolute errors improved **85.39%, 96.59%, 70.72%** relative
to the original. Mode 2 DPSP and Mode 3 EWP also moved closer.

However, RMS relative error across all nine targets increased from **103.79%
to 127.35%**, a **22.70% deterioration**. The explicitly weighted combined
numerical/feature score improved only **1.06%**. Neither number supports a
claim of a strong overall reproduction.

## Plot agreement is still incomplete

The selected case restores late-year Mode 3 hydrogen: **262 active hours
after hour 7000**, versus zero in the original. This is only a qualitative
feature, not evidence of matching the paper's event timings.

The approximate TES minima in hours 4350–4550 still differ substantially:

| Mode | Approximate paper LF | Selected LF |
|---|---:|---:|
| 1 | 0.710 | 0.96983 |
| 2 | 0.880 | 0.97019 |
| 3 | 0.088 | 0.75104 |

Visual inspection of the saved references also shows earlier storage
saturation and more persistent hydrogen activity in the selected case.
Do not describe its curves as validated matches.

## No narrow overall error band was reached

**Zero cases** meet all nine targets within any of the exploratory **10%,
20%, 30% or 50% relative-error bands**. The smallest all-nine band achieved
anywhere is approximately **270.93%** (`P042_E001`). These are relative
errors, not percentage-point differences.

This is not a sensible acceptance threshold to adopt after the fact.
The original itself also fails every listed all-nine band.

The annual Mode 1 energy-balance lower bound remains **13.1911% EWP**,
versus **4.02%** in the paper, under the unchanged generation, Excel data,
constant reactor and current waste definition. Section 2.2.1 of the 2025
paper explicitly specifies constant reactor power; introducing load-following
would therefore change a published assumption.

The next useful investigation is the input/generation and metric mapping,
especially whether the supplied series reproduce the paper's reconstructed
load and renewable generation. More financial tuning cannot change those
hourly trajectories. Resolving provenance or interpretation is different
from silently scaling the data to fit.

## Reusable model and validation

`compromise_selection.json` is a compact, Git-trackable snapshot identical to
the selected run configuration. Execute it without the 3,000-case search:

```sh
python compromise_model.py --selection compromise_selection.json \
  --output results/frozen_compromise_new
```

`fixed_params` preserves the original parameter record. Actual TES power
limits are explicitly supplied by the separate `physics` section, not the
legacy `fixed_params.TES.P_max` field.

The independently executed frozen model is in
`results/strict_compromise_20261029_frozen/`. Its DPSP, EWP, LCOE and hydrogen
match the selected search rows to numerical tolerance.

Artifact verification checked:

- **3,000 unique dashboard PNGs**, **3,000 case pages**, **3,207 total PNGs**.
- **36,004 local links**, with no missing targets.
- **150 hourly datasets**, each 8,760 hours, with finite values, TES power
  and storage bounds, and energy balances.
- Maximum energy residual: **1.14e-13 MWh**.
- Unchanged protected sources/input and identical dispatch across
  financial-only variants.

The full run occupies approximately **918.6 MiB** and remains Git-ignored.
Version the compact selection, code, tests and reports; distribute the
complete gallery separately when all figures are needed.
