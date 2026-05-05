"""Append-only JSONL writer for batch pipeline results."""

import json
from pathlib import Path


class OutputWriter:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._path = path

    def write(self, record: dict):
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
