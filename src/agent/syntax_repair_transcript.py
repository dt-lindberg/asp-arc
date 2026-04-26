"""Transcript schema and serialization for syntax-repair agent runs.

Defines the per-puzzle transcript shape described in
docs/experiment.syntax_repair_agent.md.  Works in two phases:
    Phase 1 - human-readability: dump JSON, inspect by hand.
    Phase 2 - aggregate metrics: read back for summary CSV.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ToolCall:
    name: str
    args: dict[str, Any]
    result: str


@dataclass
class Round:
    round_idx: int
    prompt_tokens: int
    completion_tokens: int
    thinking: str
    raw_response: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    parse_failures: list[str] = field(default_factory=list)
    program_after: str = ""
    errors_after: list[str] = field(default_factory=list)


@dataclass
class Transcript:
    puzzle_id: str
    source_audit: str
    initial_program: str
    initial_errors: list[str]  # human-readable error strings from Clingo
    halt_reason: str  # fixed|round_cap|no_progress|gave_up|no_tool_calls
    rounds: list[Round] = field(default_factory=list)
    final_program: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["rounds"] = [asdict(r) for r in self.rounds]
        return d

    def to_json(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as fh:
            json.dump(self.to_dict(), fh, indent=2, ensure_ascii=False)
