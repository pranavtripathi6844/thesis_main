# NRHES thesis: reproducing Nie et al. before RL

Reproduction of the nuclear-renewable hybrid energy system (smTMSR + PV + wind + TES + SOEC)
from two papers by Nie et al., using the professor's Python model. The goal is a model that
matches the papers closely enough to use later as a reinforcement learning environment.
No RL is in this repository yet.

## Layout

| Folder | Contents |
| --- | --- |
| `paper/` | The two reference papers (2025 NED 440 114138, 2024 ATE 248 123365) |
| `literature/` | Other references read for the thesis |
| `teacher_implementation/` | Professor's `NRHES_Model.py`, `NRHES_Model.m` and `InputData.xlsx`, unchanged |
| `reproducibility_search/` | Our code, tests, configs, guides and results |
| `early_runs/` | First runs of the professor's script and paper/Python plot crops |
| `presentations/` | Slides sent to the professors, slide sources and the next-step plan |

The teacher files are never edited. Every run checks their SHA-256:

```
4434541cf463a82b0bb3cf4d3dc6b7315970d08e03205a232fc3c0981fb151a9  NRHES_Model.py
945462d21e855beaa4427f1fe8a5d4bc78b378ab21305911401416f765227cf9  NRHES_Model.m
54c1d34a147db99d2f7a1e8137527571ba35834ae5c2862cc0a48949b3d2f8e3  InputData.xlsx
```

## Setup

Tested with Python 3.13.5 on macOS.

```sh
cd reproducibility_search
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
PYTHONDONTWRITEBYTECODE=1 MPLBACKEND=Agg python -m unittest discover -s tests
```

The suite has 78 tests and takes under a minute.

## Main result: grid search on the professor's script

`reproducibility_search/formula_comparison.py` runs the professor's script with 11 small
in-memory switches (listed in `results/formula_comparison_final_20261111/teacher_engine_patches.txt`)
and searches 1,600 physical x 240 financial settings for the paper designs 1a, 2a and 3d.
Excel values and the design capacities are not changed. With the professor's own settings the
patched script reproduces his original output exactly.

Most of the settings that matter (TES charge/discharge limits, 150 W panels, BOP proxy,
decommissioning basis) only exist through these switches, so they cannot be reproduced by editing
the parameters in `NRHES_Model.py` alone.

Best configuration per mode (`results/best_per_mode_final_20261111/`), relative error vs paper Table 5:

| Mode | Config | DPSP | EWP | LCOE ($/kWh) |
| --- | --- | --- | --- | --- |
| 1 (1a) | TM-P0521-F157 | 0.823% (-15.2%) | 3.932% (-2.2%) | 0.093901 (-0.1%) |
| 2 (2a) | TM-P0523-F158 | 0.558% (-38.0%) | 4.321% (+13.7%) | 0.116311 (+0.1%) |
| 3 (3d) | TM-P0441-F115 | 0.789% (-12.3%) | 0.688% (+11.0%) | 0.093063 (0.0%) |

Plots for these are the `Best_Mode*.png` files in that folder (compare with paper Figs. 8-10).
One shared configuration for all three modes, TM-P0441-F056, is in
`results/formula_comparison_final_20261111/teacher_script/minimum/best/`.

To rerun:

```sh
python formula_comparison.py --output results/NEW_RUN
python best_per_mode.py --source results/NEW_RUN --output results/NEW_BEST
```

The stored result folders were produced before the files were moved into this layout, so the
`--validate` checks that compare code hashes will report a mismatch for them. Rerunning
`best_per_mode.py` on the stored grid search reproduces the three best results exactly.

## Results not in the repository

These raw run folders (about 3.3 GB, 54,000 files) are left out because of size. Their frozen
or summary versions are included, and they can be regenerated with the commands in the guides:

- `results/paper_first_unchanged_v1` (frozen: `paper_first_unchanged_frozen_v1`)
- `results/strict_compromise_20261029` (frozen: `strict_compromise_20261029_frozen`)
- `results/table5_dual_comparison_v1` (frozen: `table5_*_frozen_v1`)
- `results/table5_paper_digitized_v1` (frozen: `table5_digitized_frozen_v1`)
- `results/teacher_only_20261111` (frozen: `teacher_only_frozen_20261111`)

## Next step

Independent multi-objective optimization (MOPSO, with NSGA-II/III as a check) of each of the
three models, compared with the paper's Table 5, Figs. 7-10. Plan:
`presentations/independent_optimization_plan.txt`.
