# NVARC parquet ↔ grid mismatch ("grid file missing" warnings)

## Symptom

Pipelines that sample `(puzzle_name1, puzzle_name2)` from
`/scratch-shared/dlindberg/nvarc_data/{inputs,outputs}/*.parquet` and look up
the corresponding grid at
`grids/<p1>/<p1>_<p2>.json` skip a meaningful fraction of rows with:

```
WARNING ... Skipping <p1>/<p2>: grid file missing
```

## Cause

The Kaggle dataset `sorokin/nvarc-synthetic-puzzles` ships at least two
splits:

- `nvarc_training/` — mixes built only from ARC-train puzzles.
- `nvarc_full/` — the superset, including mixes that involve ARC validation
  puzzles (i.e. validation-derived data).

`nvarc-data-download/download.py` deliberately keeps only `nvarc_training/`
to avoid validation contamination — see `download.py:150`:

```python
src = cached / "nvarc_training"
shutil.move(str(src), str(config.GRIDS_DIR))
```

The companion artifacts dataset `sorokin/nvarc-artifacts-puzzles` (the
parquets) is **not** split-filtered. Rows reference pairs from both splits
mixed together; the parquet schema has no split column.

So when a sampler draws a row whose `(p1, p2)` belongs to the
`nvarc_full`-only set, the local file is absent — by design.

## Evidence

On `outputs/data-00000.parquet` (684 unique pairs):

- Every distinct `p1` (436) and `p2` (450) ID has a matching subfolder under
  `grids/`. Puzzle IDs themselves are fine.
- 578/684 (84.5%) pair files exist locally; 106 (15.5%) are missing.
- 0 of the 106 misses match if `(p1, p2)` is flipped — not a directionality
  bug.

The first 10k entries of the Kaggle file listing are all under `nvarc_full/`
(alphabetically before `nvarc_training/`), confirming `nvarc_full` is the
larger split.

## What to do

**Nothing in the download repo needs changing.** Skipping missing pairs is
the correct behaviour and preserves validation hygiene.

In samplers, two reasonable patterns:

1. **Skip-on-miss at draw time** (current behaviour). Simple; wastes ~15% of
   draws. Fine for warning-tolerant pipelines.
2. **Pre-filter the parquet once.** Build a set of locally-present pair
   filenames by `os.scandir`-ing the 717 grid subfolders (cheap — one read
   per subfolder), then keep only matching parquet rows. Avoids wasted
   draws and gives deterministic sample-set sizes.

Sketch for option 2:

```python
present = set()
for sub in os.scandir(GRIDS_DIR):
    if sub.is_dir():
        for f in os.scandir(sub.path):
            if f.name.endswith(".json"):
                present.add(f.name[:-5])  # "<p1>_<p2>"

key = df["puzzle_name1"].astype(str) + "_" + df["puzzle_name2"].astype(str)
df = df[key.isin(present)]
```

## Out of scope

- **Downloading `nvarc_full/`.** Would resolve all misses but reintroduces
  the validation contamination that motivated the training-only download in
  the first place. Don't do this for evaluation runs.
- **Asking the dataset author for a split column on the artifacts
  parquets.** Would let us pre-filter without filesystem stats. Not
  blocking.
