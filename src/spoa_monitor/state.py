from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from .utils import atomic_json, read_json


STATE_SCHEMA_VERSION = "1.0"


class MonitorState:
    def __init__(self, state_dir: Path):
        self.state_dir = state_dir
        self.path = state_dir / "monitor_state.json"
        self.data: Dict[str, Any] = read_json(
            self.path,
            {"schema_version": STATE_SCHEMA_VERSION, "datasets": {}, "last_run_id": None},
        )
        if self.data.get("schema_version") != STATE_SCHEMA_VERSION:
            raise RuntimeError("Versión de estado del monitor no compatible")

    def dataset(self, key: str) -> Dict[str, Any]:
        return dict(self.data.get("datasets", {}).get(key, {}))

    def update_dataset(self, key: str, value: Dict[str, Any]) -> None:
        self.data.setdefault("datasets", {})[key] = value

    def latest_changed_dataset(self, key: str, payload_sha256: str) -> Dict[str, Any]:
        runs_dir = self.state_dir / "runs"
        if not runs_dir.exists():
            return {}
        for path in sorted(runs_dir.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
            run = read_json(path, {})
            item = run.get("datasets", {}).get(key, {})
            if item.get("payload_sha256") == payload_sha256 and item.get("real_change"):
                return dict(item)
        return {}

    def save(self, run_id: str) -> None:
        self.data["last_run_id"] = run_id
        self.data["updated_at"] = datetime.now(timezone.utc).isoformat()
        atomic_json(self.path, self.data)

    def write_run(self, run_id: str, manifest: Dict[str, Any]) -> Path:
        path = self.state_dir / "runs" / f"{run_id}.json"
        atomic_json(path, manifest)
        return path
