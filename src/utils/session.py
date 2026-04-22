"""
Session: per-puzzle append-only record of everything the agent did.

* Owns the initial attempt, the refinement history, and the final verdict.
* If constructed with an audit_path, flushes its state to disk atomically
  (tmp-file + rename) after every record_* call so results survive crashes.
"""

import json
import os

from utils.eval import build_train_feedback, all_correct


class Session:
    def __init__(
        self,
        puzzle,
        run_id,
        audit_path=None,
    ):
        self.puzzle = puzzle
        self.run_id = run_id
        self._audit_path = audit_path
        self.initial = None
        self.refinements = []

    def _flush(self):
        """Write to temporary directory to guarantee that we don't loose existing
        data and replace it with partial write, rename once writing is finished."""
        if self._audit_path is None:
            return
        tmp = self._audit_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)
        os.replace(tmp, self._audit_path)

    def record_initial(
        self,
        prompt,
        thinking,
        response,
        program,
        train_results,
    ):
        self.initial = {
            "prompt": prompt,
            "thinking": thinking,
            "response": response,
            "program": program,
            "train_verifications": train_results,
            "all_train_correct": all_correct(train_results),
        }
        self._flush()

    def record_refinement(
        self,
        attempt,
        prompt,
        thinking,
        response,
        program,
        train_results,
    ):
        self.refinements.append(
            {
                "attempt": attempt,
                "prompt": prompt,
                "thinking": thinking,
                "response": response,
                "program": program,
                "train_verifications": train_results,
                "all_train_correct": all_correct(train_results),
            }
        )
        self._flush()

    @property
    def all_train_correct(self):
        """True iff the latest attempt (initial or most recent refinement) passes."""
        latest = self.refinements[-1] if self.refinements else self.initial
        return bool(latest and latest["all_train_correct"])

    @property
    def final_correct(self):
        """Alias of all_train_correct; kept so the output JSON carries the same key."""
        return self.all_train_correct

    @property
    def history(self):
        """
        Rebuild the (program, feedback_str) history fed back into reattempt prompts.

        * The list is oldest-first and only contains failed attempts — a correct
          attempt terminates the loop so it never needs to appear as history.
        """
        entries = []
        if self.initial and not self.initial["all_train_correct"]:
            entries.append(
                (
                    self.initial["program"],
                    build_train_feedback(self.initial["train_verifications"]),
                )
            )
        for ref in self.refinements:
            if not ref["all_train_correct"]:
                entries.append(
                    (
                        ref["program"],
                        build_train_feedback(ref["train_verifications"]),
                    )
                )
        return entries

    def to_dict(self):
        """
        Serialize to the output JSON shape.

        * Matches the pre-refactor schema: run_id, puzzle_id, dataset,
          n_train_examples, steps.initial, full_program, train_verifications,
          all_train_correct, refinements, final_correct.
        """
        latest = self.refinements[-1] if self.refinements else self.initial
        full_program = latest["program"] if latest else ""
        train_verifications = latest["train_verifications"] if latest else []

        return {
            "run_id": self.run_id,
            "puzzle_id": self.puzzle["id"],
            "dataset": self.puzzle["dataset"],
            "n_train_examples": len(self.puzzle["train"]),
            "steps": {"initial": self.initial} if self.initial else {},
            "full_program": full_program,
            "train_verifications": train_verifications,
            "all_train_correct": self.all_train_correct,
            "refinements": self.refinements,
            "final_correct": self.final_correct,
        }
