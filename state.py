"""Profile-scoped plugin files. The agent secret is never written here."""

from __future__ import annotations

import json
import os
from pathlib import Path

_OVERRIDE: Path | None = None


def use_data_dir(path: Path | None) -> None:
    """Tests point state at a temporary directory. None restores the default."""
    global _OVERRIDE
    _OVERRIDE = path


def data_dir() -> Path:
    if _OVERRIDE is not None:
        _OVERRIDE.mkdir(parents=True, exist_ok=True)
        return _OVERRIDE
    try:
        from plugins.plugin_storage import plugin_data_dir

        path = plugin_data_dir("paranoy")
    except Exception:
        home = os.environ.get("HERMES_HOME") or str(Path.home() / ".hermes")
        path = Path(home) / "plugin-data" / "paranoy"
        path.mkdir(parents=True, exist_ok=True)
    return Path(path)


def _state_path() -> Path:
    return data_dir() / "state.json"


def load_state() -> dict:
    path = _state_path()
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def save_state(state: dict) -> None:
    path = _state_path()
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def update_state(**changes) -> dict:
    state = load_state()
    state.update(changes)
    save_state(state)
    return state


def append_jsonl(name: str, record: dict) -> None:
    path = data_dir() / name
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def write_overview(text: str, *, replace: bool) -> Path:
    path = data_dir() / "overview.md"
    if replace or not path.exists():
        path.write_text(text, encoding="utf-8")
    else:
        existing = path.read_text(encoding="utf-8")
        separator = "" if existing.endswith("\n") or not existing else "\n"
        path.write_text(existing + separator + text, encoding="utf-8")
    return path


def append_journal(note: str) -> Path:
    path = data_dir() / "journal.md"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(note.rstrip() + "\n")
    return path


def checkpoint(role: str, record: dict) -> None:
    safe = "".join(char if char.isalnum() or char in "-_" else "_" for char in role or "none")
    directory = data_dir() / "checkpoints"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{safe}.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
