# Teacher-only benchmark search

This separate experiment evaluates **only the three published designs selected
by the original teacher main**: Mode 1 row 1a, Mode 2 row 2a, Mode 3 row 3d.
These correspond to paper Figures 8, 9, 10. It does not evaluate the other
seven designs, optimize capacities or perform RL.

The original teacher main is rerun unchanged as the baseline. Search candidates
use the existing independent, conservation-checked `paper_reproduction.py`.
Neither implementation nor previous experiment outputs are edited.
Excel values and ordering, published design capacities, DF, TES factor,
efficiencies and component unit prices remain fixed.

## Search

`teacher_benchmarks_config.json` keeps the previous discrete uncertainty ranges:
1,600 physical settings and 240 financial settings. Stage A tests every physical
setting with reference finance and ranks the six DPSP/EWP entries. Stage B tests
every financial setting for the four best valid distinct plotted-hourly
trajectory signatures. It produces 2,556 complete configurations when all four
signatures are shortlisted, not the full 384,000 Cartesian product.
The known invalid additive-H2 diagnostic is not part of this experiment.

All candidates use one common settings set across the three modes. The final
objective is worst relative error + 0.1 times RMS relative error across all nine
DPSP/EWP/LCOE entries. Acceptance requires every entry to pass the 5% or 10%
relative-error limit and all energy/resource/state audits to pass.

The alternative PV/25-vs-50-MWth reactor interpretations and financial conventions
are hypotheses, not identified author settings. Separate balance-of-plant
costs use unverified sizing proxies and may double-count reactor OCC.
See `PAPER_REPRODUCTION_GUIDE.md` for the unchanged numerical model and caveats.

## Exact matching plot quantities

Final selected plots call the **original teacher plotting function**:

- Mode 1: LF, DPS, WEN.
- Mode 2: hydrogen, LF, DPS, WEN, WER.
- Mode 3: hydrogen, LF, DPS, WEN, WER, including the published TES threshold.

The 0–8760-hour axis, original layout and plotting styles are retained.
These are candidate arrays, not original teacher simulation results.
Every tested complete configuration also gets its own candidate-only
dashboard/settings/CSV/HTML page, including finance-only variants.
Dashboards preserve every-hour min/max display envelopes on fixed unclipped
scales; selected plots use full hourly arrays and the original plotter.

Paper-vs-selected and teacher-vs-selected images label both sources explicitly.
Native plot vertical ranges may differ. Paper MW values and teacher one-hour
MWh interval values are numerically comparable. WEN stays thermal in both.
Full original author hourly arrays are unavailable; no exact curve RMSE is
claimed. Numerical benchmark improvement is not proof of better graph shapes.

For a separate shape check, the original paper text reports TES LF near
hour 4450 of approximately **0.71, 0.88, 0.088** for Modes 1, 2, 3
(pages 12–13). Page 13 says Mode 3 hydrogen is shut down **most of the time**
after hour 7500, not at every later hour. These are textual/approximate
diagnostics, not author hourly arrays or additional fitted targets.

## Commands

From this directory, always use a new output folder:

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests -v
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python teacher_benchmarks_search.py \
  --output results/teacher_only_NEW
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python teacher_benchmarks_search.py \
  --selection results/teacher_only_NEW/selection.json \
  --output results/teacher_only_NEW_frozen
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python teacher_benchmarks_search.py \
  --validate results/teacher_only_NEW \
  --frozen results/teacher_only_NEW_frozen
```

The run saves a report, exact nine-entry comparison, rankings, all per-case
plots, all physical audits, a fresh literal teacher run, selected hourly arrays,
source/Excel hashes and a frozen selection. Validation checks every case,
each error calculation, all selected hourly values and native plot pixels,
the uncompiled reference implementation, and the exact frozen rerun.
Prior all-ten-design runs and their frozen implementation hash are preserved.
