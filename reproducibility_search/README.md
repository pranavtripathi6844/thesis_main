# NRHES reproducibility search

These separate experiments **do not edit** `../teacher_implementation/NRHES_Model.py`,
`NRHES_Model.m`, or `InputData.xlsx`.

## Current: only the teacher's three published designs

`teacher_benchmarks_search.py` searches only **1a, 2a, 3d**, against their nine
Table 5 targets and Figures 8–10. It reruns the unchanged teacher main as the
baseline, uses the existing conservation-checked model for candidates, and
calls the original teacher plotting function for the selected candidate.
Excel values and published designs remain fixed. No RL.

See `TEACHER_BENCHMARKS_GUIDE.md` for the staged grid, limitations and validation.
Completed results are in `TEACHER_BENCHMARKS_RESULTS.md`: 2,556 tested settings,
three LCOE matches, but no complete row within 10%.
The previous all-ten-design study remains separate and unchanged.

```sh
.venv/bin/python teacher_benchmarks_search.py --output results/teacher_only_NEW
```

## All-ten-design independent models, unchanged Excel values

`paper_reproduction.py` independently implements both papers' models.
It imports no previous experiment runner. The teacher implementation is called
only for the explicit comparison. All hourly Excel arrays remain read-only,
with exactly the supplied values and order. **No input calibration or RL.**

- 2025: all ten physical designs in Table 5, including HTSE.
- 2024: battery/TES designs in Tables 7–9. Its Table 5 contains algorithm
  statistics, not physical DPSP/LCOE targets, and is exported as reference only.
- A staged physical/financial grid saves a dedicated plot, settings, metrics
  and HTML page for every complete configuration.

See `PAPER_REPRODUCTION_GUIDE.md` for the model, assumptions and commands,
and `PAPER_REPRODUCTION_RESULTS.md` for the completed comparison and limitations.

```sh
.venv/bin/python paper_reproduction.py --output results/paper_first_new
```

## Historical experiments, retained separately

The older runners import the teacher's component functions. Their results and
documentation are preserved; they are not the independent implementation above.

### All-ten-row, two-track follow-up

`table5_calibration.py` compares unchanged Excel inputs with **explicitly
input-calibrated scenarios**, keeping the original files and published designs
untouched. Those calibrated scenarios **are not eligible under the current
unchanged-values restriction**. See `TABLE5_GUIDE.md`.
This follow-up checks **5% and 10% relative
error separately for all 30 Table 5 entries**, not an average-only score.
Calibrated hourly inputs are not author data. No RL is started.
`TABLE5_RESULTS.md` records the completed **3,705-configuration** comparison,
including a separate digitized-Figure-5 source control. **No model yet passes
the 5–10% limit across all ten rows.** The closest calibrated-input scenario
has 45.66% RMS and 94.72% worst relative error, not an accepted reproduction.

```sh
python table5_calibration.py --output results/table5_dual_new
```

The newer `compromise_search.py` tests **3,000 combinations of ambiguous or
unspecified settings only**, with **one dedicated dashboard PNG and comparison
page per complete configuration**. Published physical parameters, capacities,
control settings, prices and Excel data remain fixed. See
`COMPROMISE_GUIDE.md` for ranges, scoring, provenance and limitations.

```sh
python compromise_search.py --output results/strict_compromise_new
python compromise_model.py \
  --selection results/strict_compromise_new/selected_compromise.json \
  --output results/frozen_compromise_new
```

The completed expanded experiment is `results/strict_compromise_20261029/` (not in Git, see the root README).
Its `index.html` browses all cases; its `REPORT.md` reports actual agreement
and error bands. `COMPROMISE_RESULTS.md` records the new findings, including
the remaining large dispatch mismatch. `compromise_model.py` runs just the
frozen provisional model, using the Git-trackable `compromise_selection.json`
or a snapshot from a particular run.
No RL or capacity optimization is included in this expanded experiment.

## Setup and run

From the repository root:

```sh
cd reproducibility_search
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python grid_search.py --output results/grid_search
```

Paths to the model, input and default configuration resolve relative to the
script, not the shell's working directory. Output directories must be new.
Do not rerun into a previous output directory.

To also run the separate, limited-budget NSGA-II experiment:

```sh
python grid_search.py --output results/grid_search_with_nsga2 \
  --optimize --allow-unmatched-optimization
```

`--allow-unmatched-optimization` explicitly labels optimization as exploratory
if no baseline matches the paper. Without that flag, optimization is skipped
when the baseline is unmatched. The default 20 individuals, 10 generations and
two seeds are a **smoke/exploratory budget, not a convergence claim**. Increase
the budget using `--ga-population`, `--ga-generations`, and `--ga-seeds`.
The paper selected MOPSO, so this NSGA-II experiment is not an exact reproduction
of its selected optimizer.

## What is searched?

`search_config.json` contains:

- 32 physical hypotheses: independent 10/25 MWth TES charge and discharge
  limits; minimum/addition hydrogen formulas; current/resource-consistent
  thermal eligibility; cap/above-capacity-shutdown electrolyzer behavior.
- 16 economic hypotheses: four real rates, thermal/electrical reactor variable
  cost units, two explicit decommissioning conventions.
- Three fixed training capacities and nine numerical targets from the 2025
  paper Table 5. Capacities are **not optimized during this grid search**.
- Three additional Table 5 configurations held out from candidate selection.
- Analyst-selected numerical tolerances and approximate TES dips near hour
  4450. These are not guarantees from the authors.

The 25 MWth reactor, original PV/wind parameters, initial TES level, hourly
weather/load and component prices remain fixed. Do not mix targets from the
2024 battery paper into this experiment.

Economics uses maximum daily hydrogen for tank size, replacements strictly
before project end, full-project operating costs, and no salvage credit.
All stated real rates apply to constant-real annual costs/revenues. The
decommissioning `retirement` case includes the discounted year-60 liability;
`project` excludes liabilities outside the project. These are alternative,
unresolved accounting conventions, not facts established by the paper.
The economic function handles component replacements for longer horizons,
but horizons above reactor life are rejected.

## Validity versus visual/numerical agreement

The new dispatch counts rejected charging heat and recomputes shortage after
limiting actual discharge. It checks electrical, nuclear-heat, storage and
combined balances. It also checks storage/design bounds and hydrogen resource
requirements.

The literal addition formula is retained **only as a diagnostic hypothesis**.
Claimed hydrogen can exceed energy-supported production. Resource deficits
are recorded, not hidden, and these cases cannot be selected as valid.
Likewise, the unchanged original control can be closer numerically while
failing energy conservation. A negative error-reduction percentage means the
valid candidate matches the published numbers **less** closely.

Candidates are ranked using the mean squared, tolerance-normalized error of
all nine targets with one common hypothesis across modes. Acceptance also
requires physical validity, all numerical targets within tolerance, approximate
plot features within tolerance, and the qualitative plot checks. Held-out
results do not change the selected candidate.

There is an additional analytical Mode 1 lower bound. With no hydrogen,
lossless TES and a constant reactor:

```
minimum waste >= generated electricity-equivalent - annual load
                 - maximum possible net increase in storage
```

If this exceeds the paper's EWP target, economic tuning cannot reconcile it.
The bound is conditional on the current inputs, generation equations and
available-energy EWP denominator. It is not a proof that the paper is wrong.

## Plots for every configuration

Each of the 32 physical cases has three mode plots and compressed hourly data.
Each also has an economic plot covering all 16 economic hypotheses.
`case_manifest.csv` maps **every one of the 512 combined cases** to its plots.
Economic cases intentionally share hourly images because economics does not
alter dispatch. Duplicating identical images 16 times would obscure this.

The runner also saves original-control plots, best-versus-original overlays,
paper/original/best metric comparisons, held-out plots, and plots for every
returned NSGA-II non-dominated solution. Transient GA evaluations are recorded
numerically rather than producing thousands of intermediate images.
Open the generated `index.html` locally to browse all 512 cases and their plots.
`optimization/pareto_comparison.png` compares front projections with the paper
and original fixed-capacity results; colors encode LCOE.

No author hourly series were provided. The program does **not** invent paper
curves, compute image-pixel similarity, or claim hourly paper RMSE. Existing
paper/Python image comparisons are in `../early_runs/paper_plot_comparison_20261026/`.
Compare the same units and axes before judging visual agreement.

Optionally supply manually digitized points:

```csv
mode,series,hour,value,uncertainty
1,LF,4450,0.71,0.05
```

```sh
python grid_search.py --output results/with_points \
  --reference-points reference_points.csv
```

Supported series: `LF`, `H2` (kg/h for a one-hour step), `DPS` (MWh),
`WEN` (MWhth), `WER` (MWh). Digitized point errors and error bars are saved
separately, not silently included in baseline selection.

## Main outputs

- `REPORT.md`: results and limitations, including whether anything improved.
- `index.html`: local, portable plot gallery; no server or network required.
- `summary.json`: scores, validation, hashes, versions, analytical bound.
- `all_results.csv`, `ranking.csv`: every combined case and per-mode errors.
- `case_manifest.csv`, `economic_cases.csv`: plot links and assumption lookup.
- `improvement_vs_original.csv`: change in each of nine target errors.
- `plot_feature_errors.csv`: approximate reference-feature comparisons.
- `holdout_results.csv`: out-of-calibration published configurations.
- `best_valid_candidate.json`: reusable selected assumptions; may be unmatched.
- `P*/mode*_hourly.npz`: all hourly outputs and balance residuals.
- `optimization/evaluations.csv`, `pareto_solutions.csv`,
  `selected_vs_paper.csv`: exploratory optimization records.

NSGA-II objectives are DPSP, EWP and LCOE, not distance to the paper.
Its PV decisions are rounded to integers before evaluation; SOEC uses a
fraction of each candidate's maximum renewable output. Physical infeasibility
is a constraint. Final selection uses DPSP < 1%, lowest EWP, and the additional
Mode 3 LCOE <= $0.094/kWh rule. A small optimization run may find no such solution.

## Teacher script vs our model, minimum vs addition formula

`formula_comparison.py` searches only the teacher's designs 1a, 2a and 3d with
two engines and two hydrogen formulas:

- `teacher_script`: the professor's `NRHES_Model.py`, executed from its original
  text with the small switch edits listed in `formula_engines.PATCHES` (also
  written to `teacher_engine_patches.txt` in each run). With the professor's
  settings and the minimum formula it reproduces the original run bit-for-bit.
- `our_model`: the conservation-checked model from `paper_reproduction.py`.
- `minimum`: H2 = min(K_e P_e, K_th P_th) (paper text, teacher code);
  `addition`: H2 = K_e P_e + K_th P_th (paper Eq. 9).

Each engine/formula pair tests the full product of the physical and financial
grids in `teacher_benchmarks_config.json` (1,600 x 240 = 384,000 configurations).

```sh
python formula_comparison.py --output results/NEW
python formula_comparison.py --validate results/NEW
```

`REPORT.md` holds the paper / teacher original / teacher best grid / our best
grid table with a Formula column, and the comparison plots in `comparisons/`.

## Sharing in Git

The repository root keeps the papers in `../paper/` and the professor's files in
`../teacher_implementation/`. The large raw run folders are listed in the root
`.gitignore`; their frozen versions are tracked.
