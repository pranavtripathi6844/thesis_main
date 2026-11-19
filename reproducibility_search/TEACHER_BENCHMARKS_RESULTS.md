# Completed teacher-only search: 1a, 2a, 3d

Run: `results/teacher_only_20261111/`.
Frozen main selection: `results/teacher_only_frozen_20261111/`.

## Outcome

**No complete benchmark row, and no complete nine-entry configuration, meets
the 5% or 10% relative-error requirement.** Restricting the study to the
teacher's three designs does not solve the physical reproduction mismatch.

The staged search evaluated **2,556 complete settings**:
1,600 physical/default-finance settings and 956 additional financial variants
on four distinct shortlisted plotted-hourly trajectories. Every setting has
its own candidate-only PNG, settings JSON, results CSV and HTML page.
This is not an exhaustive physical-by-financial Cartesian search.

The teacher main was rerun unchanged as the baseline. Candidates use the
existing conservation-checked independent implementation, with the exact
original Excel arrays and fixed published designs. No RL or new capacities.

## Improvement is limited, and depends on the criterion

| Model | RMS relative error, 9 entries | Worst relative error | Entries within 5% / 10% | Complete rows within 10% |
|---|---:|---:|---:|---:|
| Original teacher main | 103.786231% | 297.071373% | 0 / 0 | 0/3 |
| Declared minimax selection, TB01730 | 107.964148% | 242.367764% | 3 / 3 | 0/3 |
| Lowest-RMS tested alternative, TB02212 | 99.766720% | 244.442032% | 3 / 3 | 0/3 |

- The declared selection improves the worst error by **18.41%**, but makes
  RMS error **4.03% worse**. Do not describe it as an overall RMS improvement.
- The lowest-RMS alternative improves RMS by **3.87%**, still far from
  acceptable agreement. It does not replace the declared minimax selection.
- The three passing metrics in both cases are **LCOE only**.
- Across every tested configuration, **no DPSP or EWP entry reaches 10%**.

The final objective was fixed as worst relative error plus 0.1 times RMS,
using all nine entries and requiring physical validity.
This is in-sample benchmark fitting, not held-out validation.

## Main selection: exact relative errors

| Fixed design | DPSP error | EWP error | LCOE error |
|---|---:|---:|---:|
| 1a | -94.775638% | +125.700255% | +2.254455% |
| 2a | -88.349296% | +85.875971% | +2.297148% |
| 3d | -78.655364% | +242.367764% | -1.974766% |

Those three approximately 2% errors are electricity-cost errors, one per
mode. They are not one error for each of DPSP/EWP/LCOE.
Negative DPSP error means less unmet demand than the paper, not a close
reproduction.

Main settings: TES 25/10 MWth absolute charge/discharge, 150-W PV
interpretation, 25-MWth reactor, renewable-first redispatch when SOEC is
inactive, start-of-hour threshold, resource-limited hydrogen; finance 8% real,
40 years, thermal variable costs, served-load denominator, separate rated
balance-of-plant proxy, retirement decommissioning.

TB02212 instead uses 7.5-MWth TES discharge and requested-load denominator.
Other settings are the same. Its individual LCOE errors are +2.202636%,
+2.189883%, -2.340546%.

These financial settings are hypotheses, not identified author assumptions.
The balance-of-plant proxy may double-count items in reactor OCC. Matching
LCOE does not establish that dispatch or finance has been recovered correctly.

## Exact requested plot sets

Selected figures call the original teacher plotting function, with unchanged
panel quantities/order and 0–8760-hour axis:
Mode 1 LF/DPS/WEN; Modes 2/3 H2/LF/DPS/WEN/WER.
The data are the candidate's arrays, not original teacher dispatch.

`comparisons/Paper_vs_Selected_Mode1/2/3.png` compare Figures 8–10 with
the main selection. `comparisons/Teacher_vs_Selected_Mode1/2/3.png`
compare the fresh original teacher run with the same selection.
Native plot vertical ranges differ and are labelled explicitly.

The selected LF minima near hour 4450 are 0.969384, 0.969934, 0.758359,
against the paper's approximate 0.71, 0.88, 0.088. These dips remain poorly
matched. Mode 2 renewable waste becomes effectively zero, unlike the paper.
Mode 3 has later H2 activity than the teacher baseline, but the original author
hourly output arrays are unavailable for an independent curve RMSE.

## Remaining Mode 1 input/model barrier

The most optimistic annual Mode 1 energy bound among the tested PV/reactor
interpretations is **8.432260% EWP**, versus the paper's **4.02%**
(10%-relative upper limit 4.422%).
TES power and finance cannot fix that conservative annual energy balance
under the unchanged supplied inputs and constant-reactor assumptions.
This conditional mismatch is not proof that the paper is wrong.

## Validation and files

- All **71 tests passed**; full log in `tests_71.log`.
- All 2,556 settings/plots, 7,668 result rows, 4,800 physical audits,
  12,781 HTML links and six comparison images passed artifact validation.
- Original 8,760 input rows and values, teacher Python/MATLAB files and both
  PDFs are unchanged. The prior independent implementation hash is unchanged.
- Main selection has an exact fresh-process frozen rerun, including every
  hourly array and native PNG.
- Lowest-RMS alternative agrees exactly with its original grid row and
  uncompiled hourly reference; see `rms_best/alternative_validation.json`.
- Maximum physical energy residual: `1.1368683772161603e-13` MWh.

Full report: `results/teacher_only_20261111/REPORT.md`.
Exact comparisons: `benchmark_comparison.csv`.
Per-cell attainability across the grid: `individual_metric_attainability.csv`.
Main frozen selection: `selection.json`; RMS alternative: `rms_best_selection.json`.
Previous experiments and all-ten-design results remain separate and untouched.
