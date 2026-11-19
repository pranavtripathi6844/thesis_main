# Independent paper reproduction, with unchanged Excel values

## Scope

`paper_reproduction.py` is a separate, paper-first implementation. It imports
neither the older grid/calibration runners nor their physics. Only
`teacher_comparison()` imports `NRHES_Model.py`, to execute the actual supplied
functions as a control. Original files and earlier experiments are preserved.
No RL or capacity optimization is included.

| Paper | Physical model | Compared tables |
|---|---|---|
| Nie et al., Nuclear Engineering and Design 440 (2025), 114138 | Nuclear/PV/wind/TES/HTSE | All ten Table 5 designs, 30 DPSP/EWP/LCOE targets |
| Nie et al., Applied Thermal Engineering 248 (2024), 123365 | Nuclear/PV/wind/battery/TES, no HTSE | Ten listed rows from Tables 7–9 |

The 2024 Table 5 gives HV/convergence statistics for four optimization
algorithms. It is exported as a **reference, not a reproduced result**.
This grid does not execute those repeated algorithm experiments.

## Run

From `reproducibility_search/`, use the existing environment or install
`requirements.txt` in a virtual environment. Every output directory must be new.

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests -v

PYTHONDONTWRITEBYTECODE=1 .venv/bin/python paper_reproduction.py \
  --config paper_reproduction_config.json \
  --output results/paper_first_new

PYTHONDONTWRITEBYTECODE=1 .venv/bin/python paper_reproduction.py \
  --selection results/paper_first_new/selection.json \
  --output results/paper_first_new_frozen

PYTHONDONTWRITEBYTECODE=1 .venv/bin/python verify_paper_reproduction.py \
  --run results/paper_first_new \
  --frozen results/paper_first_new_frozen
```

Script-relative source paths work regardless of the shell directory.
Frozen reruns verify the implementation, both PDF sources, teacher Python,
teacher MATLAB, Excel file and exact hourly-value fingerprint.
Changing a source intentionally requires a fresh run, not editing a snapshot.

## Input policy

The five columns `hours`, `G_Tot`, `T_a`, `V_anem`, and `P_grid` are read
directly from `InputData.xlsx`, in their original order. There must be 8,760
finite hourly records. Arrays are read-only. File hashes and an array-byte
fingerprint are checked before and after the experiment.

There is **no** rescaling, exponent, offset, time shift, smoothing, digitized
input substitution or mode-dependent weather. Earlier adjusted-input and
Figure-5-digitized studies are historical evidence, not eligible results here.

## Independent physics

- PV and wind follow the papers' component equations, with conditioning
  efficiency 1. The area/efficiency equation implies approximately 189.44 W
  per panel at STC, while the appendix says 150 W. Both interpretations are
  explicitly labelled; neither changes irradiance. Economic PV sizing always
  uses the published 150 W nameplate.
- The 2025 reference uses constant 25 MWth and electrical efficiency 0.4.
  A separate 50 MWth interpretation tests its contradictory appendix.
  The 2024 model **always** uses its published 50 MWth / 20 MWe.
- TES starts and remains above 5% capacity, with unit charge/discharge
  efficiencies and zero self-discharge. Rejected charging heat becomes waste.
  Actual discharge power limits are applied before computing unmet demand.
- In 2025 Mode 1, RE covers demand first. Eligible Mode 2 retains the published
  DF split, then uses surplus electrical and nuclear thermal resources for
  hydrogen. Mode 3 enables that allocation at the published TES threshold.
  No TES heat is assigned to hydrogen.
- Valid hydrogen obeys **both** 35.1 kWhe/kg and 11.15 kWhth/kg, the SOEC
  capacity, and its 15% minimum operating power. The smaller resource-supported
  quantity is used. The contradictory additive Eq. 9 is exported separately,
  with its unmet resource claims, and cannot be selected.
- In 2024, nuclear electricity serves demand first, excess RE charges the
  battery, and spare nuclear heat charges TES. Shortages use TES then battery
  in Mode 1, battery then TES in Mode 2. The battery has 10 MW power, 96%
  charge/discharge efficiencies and 15% initial/minimum SOC.
- Audits check electricity, reactor heat, TES, battery, combined energy,
  hydrogen resources and state/design bounds. Battery losses are explicit.
  Teacher outputs support only an aggregate residual audit; conservation is
  not inferred from an attractive teacher-paper numerical match.

DPSP is unmet demand divided by requested demand. EWP is discarded
electric-equivalent energy divided by gross electric-equivalent generation.
LCOE uses component net present costs, annualization and net hydrogen revenue.
Tank size is the **maximum daily hydrogen mass**, not the annual daily mean.
Replacement occurs strictly before project end, with no salvage credit.

## Staged grid and assumptions

The config contains all published designs and targets. Capacities, DF,
TES factors, component prices, efficiencies and input values stay fixed.
One common hypothesis applies to all ten 2025 rows.

| Physical axis | Values |
|---|---|
| Reference TES charge, MWth | 5, 10, 15, 25, 50 |
| Reference TES discharge, MWth | 5, 7.5, 10, 15, 25 |
| TES rating interpretation | Absolute; charge, discharge, or both proportional to TES capacity / 2000 |
| PV output interpretation | Area/efficiency; normalized 150 W nameplate |
| 2025 reactor, MWth | 25 main text; 50 appendix |
| Ineligible SOEC | Retain DF split; redispatch RE first |
| Mode 3 switch | Start-of-hour; additional end-state guard |

Stage A exhausts **1,600 physical settings**. It ranks physical agreement by
worst relative DPSP/EWP error plus 0.1 times their RMS relative error.
The four best valid, distinct *annual-summary signatures* are shortlisted.
The signatures contain DPSP, EWP, hydrogen, tank size and final TES, rounded
to ten decimals; they are not full-hourly trajectory hashes.

Stage B screens **240 financial settings** for each shortlisted dispatch:
five real rates (3.9215686%, 6%, 8%, 10%, 12%), 20/30/40-year horizons,
thermal/electrical variable-cost units, served/requested LCOE denominators,
embedded/separate balance-of-plant costs and retirement/project-end
decommissioning accounting. Published unit prices are never fitted.

The reference real rate is `(0.06 - 0.02) / 1.02`. Separate balance-of-plant
costs use unverified rated sizing proxies and may double-count reactor OCC.
Project-end decommissioning is an alternative accounting scenario, not
evidence that the paper retires the reactor early. These alternatives must
not be presented as identified author settings.

With four shortlisted signatures, there are **2,557 complete configurations**:
1,600 physical/default-finance, 956 additional financial, and one invalid
additive-H2 diagnostic. Already-saved settings are not counted twice.
This is **not** the full 384,000 physical-by-financial Cartesian product.
Equal trajectories under different settings remain separately labelled cases.
The extra SOC guard can be redundant because eligible H2 operation uses
surplus resources and does not discharge TES.

Final selection minimizes worst relative error plus 0.1 times RMS relative
error across **all 30** 2025 entries. All ten rows participate in selection;
there is no held-out validation claim. Acceptance requires physical validity
and each individual entry within 5% or 10% relative error. A low average,
an improved LCOE, or the best grid rank is not sufficient.

The 2024 comparison runs four literal controls (two PV interpretations and
10/50 MWth TES ratings) at all ten listed designs, using reference finances.
It is not a second complete grid or a capacity re-optimization.

## Input compatibility and interpretation

For 2024, the unchanged Excel demand is only 9–15 MW, below the reactor's
20 MWe every hour. Positive published DPSP therefore cannot be reproduced.
The papers use different source-load divisors, 550 and 1000. No rescaling
is applied to hide this incompatibility.

For 2025 Mode 1, an optimistic annual energy balance bounds the minimum
possible EWP even with unlimited TES power and perfect load supply.
`mode1_energy_bounds.csv` tests both PV and reactor interpretations at
all three Mode 1 designs. This conditional bound concerns the supplied
inputs and stated constant-reactor model, not proof that the paper is wrong.

## Outputs

- `index.html`: every tested case, its candidate-only plot and exact settings.
- `cases/G*/`: dedicated PNG, settings JSON, results CSV and HTML.
  Finance-only variants also receive their own complete dashboard.
- `physical/P*/`: all-row curve envelopes and full audits for each physical
  setting. Dashboards preserve hourly minima/maxima in each display bin.
- `selected/`, `paper_first_reference/`, `teacher/`: all ten row results,
  hourly NPZ arrays and individual plots; selected/reference include audits.
- `teacher_vs_paper_first.png`, `paper_teacher_new_comparison.csv`:
  exact all-row errors without confusing hourly overlays.
- `teacher_common_finance.csv`: teacher dispatch with the same financial
  conventions as the selected model, separately from teacher-as-coded costs.
- `paper_2024/`, `paper_2024_all_controls.csv`: 40 battery/TES controls.
- `selection.json`, `manifest.json`, `ranking.csv`, `all_results.csv`,
  `REPORT.md` and `artifact_validation.json`: reproducibility evidence.

The validator checks every dashboard/settings/results bundle, physical audit,
HTML link, full-hourly model bundle and optional exact frozen rerun.
Author-hourly curve RMSE and 2024 algorithm convergence are not claimed.
