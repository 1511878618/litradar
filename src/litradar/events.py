"""The ``--jsonl`` event stream.

With ``--jsonl`` stdout carries exactly one JSON object per line and nothing else.
Front-ends parse this; they never scrape human logs.
"""

from __future__ import annotations

import json
import sys
from typing import Any, TextIO

from . import SCHEMA, __version__
from .util import iso


class Emitter:
    def __init__(self, enabled: bool = False, stream: TextIO | None = None) -> None:
        self.enabled = enabled
        self.stream = stream if stream is not None else sys.stdout
        self.failed = False

    def emit(self, event: str, **fields: Any) -> None:
        if not self.enabled or self.failed:
            return
        payload = {"event": event}
        payload.update({key: value for key, value in fields.items() if value is not None})
        try:
            self.stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self.stream.flush()
        except (OSError, ValueError):
            # A front-end died mid-run.  The report still has to be written, so the
            # stream degrades instead of killing the engine.
            self.failed = True

    def run_start(self, run_id: str, command: str) -> None:
        self.emit(
            "run_start",
            run_id=run_id,
            command=command,
            engine=__version__,
            schema=SCHEMA,
            started_at=iso(),
        )

    def run_end(self, ok: bool, code: int) -> None:
        self.emit("run_end", ok=ok, exit=code)


NULL = Emitter(False)
