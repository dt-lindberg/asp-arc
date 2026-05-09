# Archived

Older pipeline versions kept for reference and reproducibility.

## batch_pipeline.py (multi-candidate variant)

The previous batch pipeline. For each puzzle, it generated `N_CANDIDATES`
parallel completions (vLLM `n` parameter) and ran refinement on each
candidate sequentially. Replaced by `src/batch_pipeline.py`, which generates
a single candidate per puzzle and only spends compute on refinement for
puzzles that actually need it.

This file is retained for comparing throughput / pass-rate against the new
pipeline, not for active use.
