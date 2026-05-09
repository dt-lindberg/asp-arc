"""Append-only JSONL writer for batch pipeline results."""

import json
import threading
from pathlib import Path


class OutputWriter:
    """Thread-safe JSONL appender.

    * Concurrency
      - The lock matters because batch_pipeline calls write() through
        asyncio.to_thread, so multiple OS threads may hit it at once.
      - Records can exceed PIPE_BUF (4 KiB on Linux), so a bare O_APPEND
        write(2) is not guaranteed atomic — without the lock, two large
        JSON lines can interleave on disk.
    """

    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        self._lock = threading.Lock()

    def write(self, record: dict):
        line = json.dumps(record) + "\n"
        with self._lock, open(self._path, "a", encoding="utf-8") as f:
            f.write(line)
