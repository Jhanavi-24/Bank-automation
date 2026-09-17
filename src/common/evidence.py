"""
Structured logging + evidence capture, shared by discovery and replay runs.

Every run gets its own directory under evidence/<run_type>_<run_id>/ containing:
  - log.jsonl       one JSON object per line: what happened and why, in order
  - screenshots/    at least one richer signal on failure/escalation
  - result.json     the final structured outcome

Secrets are redacted before anything touches disk (see src/safety/redaction.py) --
this module is the single choke point every event passes through, so that's
enforced once rather than at every call site.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from src.safety.redaction import redact_params, redact_text


class EvidenceRecorder:
    def __init__(self, evidence_root: str, run_type: str, run_id: Optional[str] = None,
                 secret_values: Optional[List[str]] = None):
        self.run_id = run_id or f"{int(time.time())}-{uuid.uuid4().hex[:6]}"
        self.run_type = run_type
        self.dir = Path(evidence_root) / f"{run_type}_{self.run_id}"
        self.screenshots_dir = self.dir / "screenshots"
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.dir / "log.jsonl"
        self._secret_values = secret_values or []
        self._seq = 0

    def _redact(self, obj: Any) -> Any:
        if isinstance(obj, dict):
            obj = redact_params(obj, sensitive_names=[])
            return {k: self._redact(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self._redact(v) for v in obj]
        if isinstance(obj, str):
            return redact_text(obj, self._secret_values)
        return obj

    def log(self, event_type: str, **fields: Any) -> Dict[str, Any]:
        self._seq += 1
        record = {
            "seq": self._seq,
            "ts": time.time(),
            "run_id": self.run_id,
            "run_type": self.run_type,
            "event": event_type,
            **self._redact(fields),
        }
        with open(self.log_path, "a") as f:
            f.write(json.dumps(record, default=str) + "\n")
        return record

    def screenshot(self, page, name: str) -> str:
        path = self.screenshots_dir / f"{self._seq:03d}_{name}.png"
        try:
            page.screenshot(path=str(path))
        except Exception as e:  # noqa: BLE001 -- evidence capture must never crash the run
            self.log("screenshot_failed", name=name, error=str(e))
            return ""
        return str(path)

    def write_result(self, result: Dict[str, Any]) -> str:
        path = self.dir / "result.json"
        with open(path, "w") as f:
            json.dump(self._redact(result), f, indent=2, default=str)
        return str(path)

    def copy_artifact(self, artifact_json: str) -> str:
        path = self.dir / "artifact.json"
        with open(path, "w") as f:
            f.write(artifact_json)
        return str(path)
