# Independent reproduction with unchanged Excel values

Completed 31 October 2026. Run: `results/paper_first_unchanged_v1/`.
Frozen check: `results/paper_first_unchanged_frozen_v1/`.

**The separate Python implementation and grid search are complete, but no
configuration reproduces all 2025 Table 5 entries within 5% or 10% relative
error. The supplied inputs are also incompatible with the 2024 positive
DPSP benchmarks. No Excel values were changed, and no RL was started.**

## 1. What was implemented and executed

- `paper_reproduction.py`: independent component, dispatch, economics,
  balance-audit and plotting implementations. No earlier experiment runner
  is imported. Actual teacher functions are imported only for the comparison.
- All ten 2025 Table 5 designs, with each row's own published PV, wind, TES,
  SOEC, DF and TES threshold. One common modelling hypothesis across rows.
- The separate 2024 battery/TES model, at ten listed physical rows from
  Tables 7–9, under four labelled controls.
- 1,600 physical/default-finance settings, 956 additional financial settings
  across four shortlisted annual-summary signatures, and one invalid
  additive-H2 diagnostic: **2,557 complete configurations**.
- A dedicated candidate-only PNG, settings JSON, results CSV and HTML page
  for every complete configuration. Finance-only variants have their own
  complete dashboards. There are 25,570 design-result rows.

This is a staged physical/financial search, **not** all 384,000 possible
physical-by-financial pairs. All ten 2025 rows enter selection; none is
held out. The 2024 controls are literal comparisons, not a separate full
calibration grid or capacity optimization.

## 2. Teacher versus independent implementation, 2025 Table 5

The following errors use the same 30 published targets and unchanged inputs.
They are relative errors, not percentage-point differences.

| Model | RMS relative error | Worst relative error | Entries within 5% | Entries within 10% | Complete rows within 10% |
|---|---:|---:|---:|---:|---:|
| Actual teacher Python, as coded | 297.794% | 1019.786% | 0/30 | 0/30 | 0/10 |
| Independent reference | 297.166% | 1085.145% | 0/30 | 0/30 | 0/10 |
| Selected grid candidate G01734 | 226.704% | 842.704% | 10/30 | 10/30 | 0/10 |

The selected candidate reduces joint RMS error by **23.87%** versus the
teacher, but **does not improve every metric**:

| Metric, ten entries each | Teacher RMS error | Selected RMS error | Selected entries within 10% |
|---|---:|---:|---:|
| DPSP | 41.822% | 89.684% | 0/10 |
| EWP | 513.360% | 382.278% | 0/10 |
| LCOE | 27.503% | 1.967% | 10/10 |

All ten selected LCOEs are within 5%, with maximum absolute relative error
2.286%. **None of the selected DPSP or EWP entries is within 10%.**
Lower simulated shortage is better reliability, but worse agreement when
the published shortage is higher.

Selection minimizes worst relative error plus 0.1 times RMS relative error.
It is not pure RMS minimization. G01035 has slightly lower RMS, 225.702%,
but worse maximum error, 855.712%, and therefore is not selected.

### All ten published values versus G01734

Each cell is **paper → independently simulated**. DPSP and EWP are percentages;
LCOE is dollars/kWh. Stored CSVs retain full precision and signed errors.

| Row | DPSP (%) | EWP (%) | LCOE ($/kWh) |
|---|---:|---:|---:|
| 1a | 0.9700 → 0.050676 | 4.0200 → 9.073150 | 0.094012 → 0.096083 |
| 1b | 0.9600 → 0.050676 | 4.0300 → 9.091048 | 0.094027 → 0.096106 |
| 1c | 0.9900 → 0.050676 | 4.3100 → 9.424250 | 0.090800 → 0.092309 |
| 2a | 0.9000 → 0.104856 | 3.8000 → 7.063287 | 0.116200 → 0.118745 |
| 2b | 0.8600 → 0.083168 | 3.8400 → 7.152379 | 0.114600 → 0.117107 |
| 2c | 0.9000 → 0.050676 | 3.9000 → 7.800814 | 0.108400 → 0.110878 |
| 3a | 0.4100 → 0.050676 | 0.1800 → 1.696866 | 0.116400 → 0.118589 |
| 3b | 0.7000 → 0.050676 | 0.2100 → 1.743557 | 0.107200 → 0.107230 |
| 3c | 0.9500 → 0.206067 | 0.2200 → 0.875275 | 0.099500 → 0.097728 |
| 3d | 0.9000 → 0.192102 | 0.6200 → 2.122680 | 0.093100 → 0.091086 |

Worst selected error: row 3a EWP, **+842.704%**.

Full teacher/reference/selected comparisons:
`results/paper_first_unchanged_v1/paper_teacher_new_comparison.csv` and
`results/paper_first_unchanged_v1/teacher_vs_paper_first.png`.

### Frozen candidate settings, not identified author settings

| Setting | G01734 |
|---|---|
| TES charge / discharge | 25 / 10 MWth, absolute ratings |
| PV output | Normalized 150 W nameplate interpretation |
| Reactor | Constant 25 MWth, 0.4 electrical efficiency |
| Ineligible SOEC | Redispatch renewable electricity first |
| Mode 3 switching | Start-of-hour TES state |
| H2 formula | Minimum electrical/thermal resource-supported production |
| Real discount rate / horizon | 8% / 40 years |
| Reactor variable-cost units | Thermal |
| LCOE denominator | Requested demand |
| Balance of plant | Separate rated sizing proxy |
| Decommissioning | Discounted year-60 retirement liability |

The 40-year horizon and separate balance-of-plant sizing are unverified
economic assumptions, not recovered author parameters. The proxy may
double-count reactor OCC. Reactor physical life remains 60 years.

Replacing only the teacher's financial conventions, while preserving its
actual dispatch, also brings all ten LCOEs within 5% (LCOE RMS error 1.883%).
See `teacher_common_finance.csv`. Thus LCOE agreement alone does not establish
that the new dispatch, cost breakdown, or assumed horizon is the author's model.

### Plot behaviour is also not reproduced

For rows 1a, 2a and 3d, the selected minimum TES fractions over hours
4350–4550 are respectively **0.9694, 0.9699 and 0.7584**. Approximate
paper-figure features previously read are about 0.71, 0.88 and 0.088.
Those are visual features, not exact author-hourly measurements.
The selected 3d model has 59 H2-producing hours from hour 7500 onward.
No full author-hourly curve RMSE or exact shape agreement is claimed.

## 3. Why finance or TES power tuning cannot fix Mode 1 here

With the supplied inputs, constant reactor output, no hydrogen in Mode 1
and the paper's lossless TES, an optimistic annual energy balance gives

`EWP >= max(0, generation - demand - 0.4 * 0.95 * TES_capacity) / generation`.

This generously permits perfect load service and ends with TES completely
full, even if power limits or the actual hourly sequence prevent that.
Any shortage increases, rather than reduces, the required waste.

| Mode 1 row | Paper EWP | Largest EWP allowed at 10% relative error | Minimum possible, 150 W PV | Minimum possible, area PV |
|---|---:|---:|---:|---:|
| 1a | 4.020% | 4.422% | 8.432% | 13.191% |
| 1b | 4.030% | 4.433% | 8.454% | 13.216% |
| 1c | 4.310% | 4.741% | 8.843% | 13.639% |

These bounds use the 25 MWth main-text reactor. The contradictory 50 MWth
appendix interpretation makes the bounds still higher. All twelve
row/PV/reactor combinations fail the necessary 10% condition.

Consequently, under these explicit generation/storage assumptions,
unchanged input values and published capacities, **no TES power or finance
setting can reproduce all targets**, regardless of whether the entire
financial Cartesian product is searched. This is a conditional
input/model compatibility result, not proof that the paper itself is wrong.

## 4. Comparison with the 2024 battery/TES paper

This paper's physical targets are in **Tables 7–9, not Table 5**.
Its reactor produces 20 MWe, while the unchanged Excel load is 9–15 MW.
The reactor alone therefore meets every hourly demand, and every one of
the 40 controls gives **DPSP = 0**. Every positive published DPSP has
100% relative error. No complete physical row passes.

Both papers describe different source-load divisors, 550 versus 1000.
No load conversion or rescaling was applied.

The table shows all ten listed physical rows. LCOE is identical across the
four controls because capacities, fixed-cost assumptions and served demand
are the same, hydrogen is absent, and all shortages are zero. Renewable
interpretation/TES power can change curves without changing those costs.

| Row | Paper DPSP (%) | New DPSP (%) | Paper LCOE | New LCOE |
|---|---:|---:|---:|---:|
| T7_M1 | 0.538 | 0 | 0.1022 | 0.152256 |
| T7_M2 | 0.930 | 0 | 0.0894 | 0.132554 |
| T8_5 | 4.829 | 0 | 0.0543 | 0.075425 |
| T8_3 | 2.996 | 0 | 0.0631 | 0.090955 |
| T8_1 | 0.985 | 0 | 0.0883 | 0.131483 |
| T8_01 | 0.099 | 0 | 0.2573 | 0.430523 |
| T9_5 | 4.849 | 0 | 0.0543 | 0.075398 |
| T9_3 | 2.942 | 0 | 0.0633 | 0.092263 |
| T9_1 | 0.930 | 0 | 0.0894 | 0.132554 |
| T9_01 | 0.075 | 0 | 0.1165 | 0.173888 |

T7_M2 and T9_1 intentionally repeat the same published design.
LCOE RMS relative error is 48.451%; none is within 10% under the reference
finances. These controls use a 20-year horizon, 3.9215686% real rate,
embedded balance of plant and year-60 decommissioning.

The 2024 Table 5 HV/convergence statistics for NSGA-II, MOEA/D, MOPSO and
SPEA2 are exported as `paper_2024_Table5_algorithm_reference.csv`,
explicitly marked `reference_only_MOEA_experiments_not_run`.
A staged parameter grid is not a reproduction of those algorithm experiments.
The teacher Python has no corresponding battery/TES implementation,
so a teacher-versus-2024-dispatch result is not fabricated.

## 5. Physical validity and artifact checks

`verify_paper_reproduction.py` passed:

- 2,557 unique complete settings and 2,557 unique candidate dashboard PNGs.
- 25,570 design-result rows; 1,600 physical cases and 16,000 physical audits.
- 12,789 local HTML links resolve.
- 80 hourly NPZs checked, each containing 8,760 finite samples, with exact
  original demand/hour arrays. These include teacher, both new models and
  the frozen selected rerun.
- Largest new-model hourly energy residual: approximately
  `2.24e-13` in the respective electrical/thermal energy units, well below
  the `1e-7` audit threshold.
- Selected and frozen results/settings CSV/JSON and every hourly array
  agree **exactly**, not just within a numerical tolerance.
- Excel file hash and the exact in-memory hourly-value fingerprint match.
  Teacher Python, teacher MATLAB and both source PDFs also remain unchanged.
- All ten teacher designs fail the exposed aggregate balance check.
  For example, teacher 1a has up to 6 MWh electric-equivalent hourly
  imbalance and about 11,424.52 MWh positive annual unaccounted energy.
  The teacher's closer numerical results cannot be treated as physically
  validated reproduction.
- The literal additive H2 equation diagnostic fails resource checks and
  is never selectable.

The full regression suite passed **64 tests**. An additional expanded
compiled-versus-Python reference check covers all twenty listed designs
across both papers. MATLAB was hash-checked, not executed.
Runtime versions are recorded in the run's `environment.json`.

## 6. Reproduce and inspect

Git-shareable files:

- `paper_reproduction.py`
- `paper_reproduction_config.json`
- `paper_reproduction_selection.json`
- `verify_paper_reproduction.py`
- `tests/test_paper_reproduction.py`
- `PAPER_REPRODUCTION_GUIDE.md` and this report.

The durable selection contains the exact common settings, published
benchmark rows, implementation hash and protected-source/input hashes.
Original source/input/PDF files must still be available unchanged.
No repository was created, committed or published.

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python paper_reproduction.py \
  --selection paper_reproduction_selection.json \
  --output results/paper_first_frozen_new
```

Open `results/paper_first_unchanged_v1/index.html` for every case.
The selected full-year dashboard is
`results/paper_first_unchanged_v1/selected/candidate.png`.
Each selected row also has its own full-resolution plot and hourly NPZ.

Earlier input-calibrated results, including the 45.66% RMS scenario, are
**not eligible** under the current unchanged-values restriction. They remain
preserved and are not merged into this comparison.

The appropriate next scientific step is to discuss the demonstrated
input/model compatibility barriers with the supervisor and seek author
input/model clarification. Starting RL or fitting explicit efficiencies
would not resolve the current reproduction evidence.
