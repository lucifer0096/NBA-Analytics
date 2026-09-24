# R: exploratory analysis

R owns **exploration only** — the production pipeline (collector → model →
optimizer → dashboard) is Python on purpose, so `requirements.txt`, the test
suite, and CI stay single-language. These scripts read the *same artifacts*
the Python pipeline writes, which keeps exploration and production honest:

| Script | Reads | Does |
|---|---|---|
| `eda.R` | `data/processed/historical_games.parquet` (via **arrow**) | season-over-season scoring trends, rest/B2B effects, position splits, opportunity-vs-production scatter with a naive-baseline reference line |

## Running

```r
install.packages(c("arrow", "dplyr", "ggplot2", "tidyr"))
setwd("<path-to>/NBA-Analytics")
source("r/eda.R")          # writes PNGs to r/output/
```

`r/output/` is gitignored (generated). If the parquet doesn't exist yet,
build it first on the Python side:

```bash
python src/model/load_historical.py     # data/raw/* -> data/processed/historical_games.parquet
```

> Note: `Rscript` is not installed in this project's development container,
> so these scripts are written to spec but have not been executed here —
> treat a first local run as the smoke test.

## Why arrow/parquet instead of re-parsing raw JSON

`historical_games.parquet` is already the unified, scored, deduped table
(one row per player-game, `fantasy_points` under the default weights,
positions joined). Re-deriving that from ~20k raw box-score files in R would
just be a second implementation of `load_historical.py` to keep in sync.
