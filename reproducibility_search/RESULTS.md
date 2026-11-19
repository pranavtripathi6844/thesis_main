# Executed experiment: results and limits

## Outcome

The separate runner completed **512 combined grid cases** (32 physical
hypotheses × 16 economic hypotheses), with three fixed-capacity modes per case.
**256 cases passed the physical checks; none reproduced all acceptance targets.**
The original Python, MATLAB and Excel files remained unchanged.

The best physically valid common hypothesis was `P019_E013`:

- TES charge/discharge limits: **25/10 MWth**.
- Hydrogen: **minimum** of electricity-supported and heat-supported production.
- Eligibility: resource-consistent minimum electrical load.
- Above-capacity SOEC operation: cap output, rather than shut down.
- Economics: **8% real discount rate**, thermal-basis reactor variable costs,
  discounted year-60 decommissioning liability.

These are the best settings **within the tested grid**, not independently
verified physical or financial estimates. The fitted rate is at the grid's
upper boundary.

## Fixed-capacity comparison

Targets refer to the selected configurations in the **2025 paper, Table 5**,
not the 2024 battery study. DPSP and EWP below are percentages; LCOE is $/kWh.

| Mode | Metric | Paper | Original implementation | Best physically valid |
|---|---|---:|---:|---:|
| 1 | DPSP (%) | 0.9700 | 0.55765 | 0.05068 |
| 1 | EWP (%) | 4.0200 | 4.63146 | 13.2710 |
| 1 | LCOE ($/kWh) | 0.094012 | 0.068688 | 0.087152 |
| 2 | DPSP (%) | 0.9000 | 0.39695 | 0.16603 |
| 2 | EWP (%) | 3.8000 | 4.66345 | 6.75204 |
| 2 | LCOE ($/kWh) | 0.116200 | 0.083285 | 0.103149 |
| 3 | DPSP (%) | 0.9000 | 1.11899 | 0.08732 |
| 3 | EWP (%) | 0.6200 | 2.46184 | 2.35796 |
| 3 | LCOE ($/kWh) | 0.093100 | 0.065267 | 0.076577 |

Absolute LCOE errors against the paper fell **72.91%, 60.35%, and 40.64%**
for Modes 1–3. Mode 3 EWP error fell **5.64%**. The other five target errors
increased. Lower DPSP is better supply performance, but moving below the paper's
DPSP is not necessarily closer reproduction.

Across all nine targets, the mean squared tolerance-normalized error increased
from **115.788 to 181.481**, or **56.74% worse**. It would be misleading to
describe this as an overall numerical improvement. The original implementation's
apparently closer fit does not establish correct energy conservation.

The held-out published capacities also failed numerical acceptance; their
combined score was **146.217**. They were not used to choose the best hypothesis.

## Why parameter fitting cannot resolve Mode 1

With current inputs, constant reactor output, lossless TES, no hydrogen, and
the implemented available-energy EWP denominator:

- Annual available electricity-equivalent: **117,576.485 MWh**.
- Annual load: **101,306.896 MWh**.
- Largest possible net increase in stored electricity-equivalent: **760 MWh**.
- Minimum annual waste: **15,509.589 MWh**, or **13.1911% EWP**.

That lower bound already exceeds the paper's **4.02%** target, even before
hourly operating restrictions. Economics cannot change this annual balance.
Reproduction therefore requires resolving input provenance, generation
assumptions, or metric definitions, rather than further fitting financial rates.
This is a conditional result for this model and dataset, not proof that the
paper is wrong.

## Exploratory capacity optimization

After selecting the fixed-capacity hypothesis, a separate NSGA-II experiment
ran **20 individuals × 10 generations × 2 seeds × 3 modes**, yielding **1,200
evaluations** and **120 returned non-dominated solutions across the six runs**.
It optimized DPSP, EWP and LCOE, not distance to the paper.

| Mode | Selected PV panels | TES (MWhth) | DPSP (%) | EWP (%) | LCOE ($/kWh) |
|---|---:|---:|---:|---:|---:|
| 1 | 40,144 | 1,983.941 | 0.61904 | 6.54085 | 0.083945 |
| 2 | 74,469 | 1,857.218 | 0.68945 | 5.41762 | 0.102262 |
| 3 | 43,153 | 869.662 | 0.96636 | 0.13694 | 0.080177 |

These use different capacities from the fixed-capacity comparison. They do
not resolve the baseline mismatch or demonstrate optimizer convergence.
NSGA-II also differs from the paper's selected MOPSO method. The exploratory
Mode 3 selection has no late-year hydrogen events, so its low EWP should not
be mistaken for reproducing the paper's hydrogen waveform.

## Validation and saved artifacts

- **16 focused tests passed**, including original-control regression,
  balance/shortage/charging tests, diagnostic hydrogen rejection, cash flows,
  a small end-to-end search, and an NSGA-II smoke test.
- Two full runs produced identical grid results and returned front results.
- Maximum balance residual among valid grid rows: **1.14e-13 MWh**.
- Verified **259 PNG files**, **96 compressed hourly files**, all 512 case
  plot links, finite hourly arrays and unchanged original source/input hashes.
- No original author hourly series was available. Approximate plot features
  and existing image comparisons do not establish hourly waveform agreement.

The final local run is `results/run_20261029_final/` (about **97.4 MiB**).
Open its `index.html` to browse the 512 grid cases. It contains `REPORT.md`,
`improvement_vs_original.csv`, `summary.json`, and `artifact_validation.json`.
The `optimization/` directory contains the front comparison plot, returned
solution plots, every evaluated objective vector, and `selected_vs_paper.csv`.
Plots cover every grid configuration through shared physical/economic images
and every returned NSGA-II front solution; transient GA evaluations are CSV
records, not individual PNGs.

See `README.md` for setup and exact reproduction commands. Generated results
are Git-ignored; retain or distribute the complete run separately when sharing
all plots. This summary remains small enough to version with the code.
