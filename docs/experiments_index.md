# Experiments Index

| # | Slug | Hypothesis | Job IDs | Headline | Status |
|---|------|-----------|---------|----------|--------|
| 001 | refinement-loop | Agentic refinement loop (feed Clingo errors + grid diffs back to model) improves puzzle solve rate vs single-shot | smoke: 22481376 (4/4 solved, 2 by refinement), full: 22481916 | 4/4 smoke (promising) | Running full run |

## Next Steps
- Complete exp_001 baseline
- Try syntax-only mini-loop before semantic refinement
- Try tool-use mode (run_clingo + edit_code tools)
