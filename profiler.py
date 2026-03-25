"""
profiler.py — session timing accumulator for DaVinciResolve-Media-Tagger.

Records per-request timing for navigate, filmstrip, and their sub-phases.
Call dump() or hit GET /api/profiler/report to write a JSON report.

Usage in app.py:
    import profiler
    profiler.record_navigate(total_ms, resolve_ipc_ms, timing)
    profiler.record_filmstrip(total_ms, probe_ms, extract_ms, encode_ms, frames)
    profiler.record_filmstrip_cache_hit()
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

_lock = threading.Lock()

_session: dict[str, Any] = {
    "started_at": datetime.now().isoformat(timespec="seconds"),
    "navigate": [],       # [{total_ms, resolve_ipc_ms, folder_cache_ms, get_keywords_ms}]
    "suggest_bg": [],     # [suggest_ms] — background suggest_keywords timings
    "filmstrip": [],      # [{total_ms, probe_ms, extract_ms, encode_ms, frames}]
    "filmstrip_cache_hits": 0,
}


def record_navigate(
    total_ms: float,
    resolve_ipc_ms: float,
    timing: dict[str, float] | None = None,
) -> None:
    entry: dict[str, Any] = {
        "total_ms": round(total_ms),
        "resolve_ipc_ms": round(resolve_ipc_ms),
        "folder_cache_ms": round((timing or {}).get("folder_cache_ms", 0)),
        "get_keywords_ms": round((timing or {}).get("get_keywords_ms", 0)),
    }
    with _lock:
        _session["navigate"].append(entry)


def record_suggest_bg(suggest_ms: float) -> None:
    with _lock:
        _session["suggest_bg"].append(round(suggest_ms))


def record_filmstrip(
    total_ms: float,
    probe_ms: float,
    extract_ms: float,
    encode_ms: float,
    frames: int,
) -> None:
    with _lock:
        _session["filmstrip"].append({
            "total_ms": round(total_ms),
            "probe_ms": round(probe_ms),
            "extract_ms": round(extract_ms),
            "encode_ms": round(encode_ms),
            "frames": frames,
        })


def record_filmstrip_cache_hit() -> None:
    with _lock:
        _session["filmstrip_cache_hits"] += 1


def _stats(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    values = sorted(values)
    n = len(values)
    return {
        "n": n,
        "min_ms": round(min(values)),
        "max_ms": round(max(values)),
        "avg_ms": round(sum(values) / n),
        "p50_ms": round(values[n // 2]),
        "p90_ms": round(values[int(n * 0.9)]),
        "p95_ms": round(values[int(n * 0.95)]),
    }


def summary() -> dict:
    with _lock:
        nav = _session["navigate"]
        suggest_bg = _session["suggest_bg"]
        film = _session["filmstrip"]
        hits = _session["filmstrip_cache_hits"]

    report: dict[str, Any] = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "session_started_at": _session["started_at"],
        "navigate": {
            **_stats([r["total_ms"] for r in nav]),
            "resolve_ipc": _stats([r["resolve_ipc_ms"] for r in nav]),
            "overhead": _stats([r["total_ms"] - r["resolve_ipc_ms"] for r in nav]),
            "folder_cache": _stats([r["folder_cache_ms"] for r in nav]),
            "get_keywords": _stats([r["get_keywords_ms"] for r in nav]),
        },
        "suggest_bg": _stats(suggest_bg),
        "filmstrip": {
            **_stats([r["total_ms"] for r in film]),
            "probe": _stats([r["probe_ms"] for r in film]),
            "extract": _stats([r["extract_ms"] for r in film]),
            "encode": _stats([r["encode_ms"] for r in film]),
            "cache_hits": hits,
            "cache_hit_rate": f"{hits / (hits + len(film)) * 100:.1f}%" if (hits + len(film)) > 0 else "n/a",
        },
        "raw": {
            "navigate": nav,
            "suggest_bg": suggest_bg,
            "filmstrip": film,
        },
    }
    return report


def dump(output_path: str | None = None) -> str:
    """Write the profiling report to a JSON file. Returns the file path."""
    report = summary()
    if output_path is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            f"profile_{ts}.json",
        )
    Path(output_path).write_text(json.dumps(report, indent=2))
    return output_path
