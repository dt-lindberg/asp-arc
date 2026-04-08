Need better full audit trail in the streamlit interface. I've made sure the full audit data is available for a new run on three puzzles, this can be used to update the existing streamlit interface.

Here's some information on the full audit data trail, including wish file contains it:

---

Full audit is confirmed. The results file is results/20260407_084639.json.

  Here's what's auditable for every program transformation:

  Per candidate (6 per puzzle):
  - prompt — exact prompt sent to the LLM
  - thinking — model's chain-of-thought
  - response — raw model output
  - program_raw — program as extracted from response (before any fixes)
  - program_final — program after all syntax fixes
  - syntax_fix_details — per-stage fix audit:
    - quick_fix: n_fixes, program_before, program_after
    - rewrite / rewrite_partial: rounds[] each with system_prompt, prompt, thinking, response, program_before,
  program_after, syntax_error_before, syntax_error_after

  Syntax agent (post-selection, if triggered):
  - initial_error, rewrite_rounds, rewrite_details (same per-round schema as above)
  - If multi-turn tool agent: steps[] each with thinking, response, tool_call, tool_result, program_after,
  syntax_error_after

  Refinement loop (per attempt):
  - prompt, thinking, response, program, train_verifications
  - syntax_fixes[] — if a rewrite ran, includes rewrite_details[] with the same per-round schema

  Every program state change — quick_fix, rewrite round, tool edit, or refinement — is documented with before/after
   program snapshots plus the LLM inputs/outputs that caused the change.

---

Update the existing interface to show a clear trail of this data. Make sure it's easy to navigate and provides an intuitive feel for where to find what data. Think about how to structure it, and feel free to change the existing structure if you think there's a better way.
