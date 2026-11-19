# Reproducibility search report

Tested 32 physical x 16 economic = 512 combined cases.
Physically valid: 256. Accepted reproductions: 0.
Best valid candidate: **P019_E013**. This is **not a validated reproduction of the paper**.

Original tolerance-normalized squared-error score: 115.788.
Best valid score: 181.481. Change: -56.74% error reduction (negative = worse).

|Mode|Metric|Paper|Original|Best valid|Error reduction %|
|---|---|---:|---:|---:|---:|
|1|DPSP|0.0097|0.00557652|0.000506763|-122.95|
|1|EWP|0.0402|0.0463146|0.13271|-1412.93|
|1|LCOE|0.094012|0.068688|0.087152|72.91|
|2|DPSP|0.009|0.00396946|0.00166027|-45.90|
|2|EWP|0.038|0.0466345|0.0675204|-241.89|
|2|LCOE|0.1162|0.0832855|0.103149|60.35|
|3|DPSP|0.009|0.0111899|0.000873234|-271.09|
|3|EWP|0.0062|0.0246184|0.0235796|5.64|
|3|LCOE|0.0931|0.0652665|0.0765772|40.64|

**Analytical check:** with current inputs, constant reactor and the current available-energy EWP definition, Mode 1 EWP cannot be below 13.1911%, even allowing maximum final storage. Paper target: 4.0200%.
Thus matching that target requires resolving inputs, generation/metric definitions or other assumptions; economic tuning cannot fix it.

Optimization: `exploratory_completed`.

## Files
- `index.html`: local gallery linking every tested configuration.
- `case_manifest.csv`: every combined case and its shared plots/hourly files.
- `all_results.csv`, `ranking.csv`: full numerical scores and validity.
- `improvement_vs_original.csv`: signed improvement for each of nine targets.
- `holdout_results.csv`: additional published capacities, not used for selection.
- `plot_feature_errors.csv`: approximate paper TES-dip comparisons.
- `P*/mode*.png`: every physical configuration; `P*/economics.png`: every economic case.
- `metrics_comparison.png`, `best_vs_original_mode*.png`: end comparisons.
- `summary.json`: hashes, environment, assumptions, limitations and optimization status.

Original Python and Excel SHA-256 hashes verified unchanged after execution.
No original paper hourly data were supplied. Approximate plot features are not exact waveform validation.
