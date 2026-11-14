"""Profile-scoped plugin files. The agent secret is never written here."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path

_OVERRIDE: Path | None = None
# Capture threads, hooks and tools all write state.json. Every read-modify-write holds this.
_LOCK = threading.RLock()


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
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix="state-", suffix=".tmp")
    with os.fdopen(handle, "w", encoding="utf-8") as out:
        out.write(json.dumps(state, indent=2, sort_keys=True))
    os.replace(temporary, path)


def change_state(change) -> dict:
    """Load, apply ``change(state)`` in place, and save, as one step."""
    with _LOCK:
        state = load_state()
        change(state)
        save_state(state)
        return state


def update_state(**changes) -> dict:
    return change_state(lambda state: state.update(changes))


def append_jsonl(name: str, record: dict) -> None:
    path = data_dir() / name
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


# Warning lines the host appends to the next reply. In memory only: a restart
# loses a pending warning, and the next failure raises it again.
# ponytail: capture failures are global (key ""), shown to whoever is answered
# next. Key them by chat if several people share one gateway and that matters.
_WARNINGS: dict[str, list[str]] = {}


def warn(text: str, session_id: str = "") -> None:
    with _LOCK:
        lines = _WARNINGS.setdefault(str(session_id or ""), [])
        if text not in lines:
            lines.append(text)


def take_warnings(session_id: str = "") -> list[str]:
    with _LOCK:
        lines = _WARNINGS.pop(str(session_id or ""), [])
        for line in _WARNINGS.pop("", []):
            if line not in lines:
                lines.append(line)
        return lines


def journal(kind: str, **record) -> None:
    """One line per capture decision, grounding search, write, and failure."""
    import time

    record = {"kind": kind, **record, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    try:
        with _LOCK:
            append_jsonl("journal.jsonl", record)
    except OSError:
        pass
