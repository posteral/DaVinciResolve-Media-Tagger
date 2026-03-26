"""M1.1 — ExportMetadata smoke test.

Run with Resolve open and a project loaded:

    .venv/bin/python3 scripts/m1_1_export_metadata_smoke_test.py

Prints:
  - How long ExportMetadata takes
  - Total number of rows in the CSV
  - Column names
  - First 5 rows (truncated for readability)

The CSV is written next to this script as export_metadata_test.csv and
kept so you can inspect it manually afterwards.
"""
from __future__ import annotations

import csv
import sys
import time
from pathlib import Path

# Reuse get_resolve() from resolve_api so we don't duplicate the import logic.
sys.path.insert(0, str(Path(__file__).parent.parent))
import resolve_api

OUTPUT_PATH = Path(__file__).parent / "export_metadata_test.csv"


def main() -> None:
    print("Connecting to Resolve…")
    resolve = resolve_api.get_resolve()
    if resolve is None:
        print("ERROR: Could not connect to Resolve. Is it running with a project open?")
        sys.exit(1)

    pm = resolve.GetProjectManager()
    project = pm.GetCurrentProject()
    if project is None:
        print("ERROR: No project open.")
        sys.exit(1)

    media_pool = project.GetMediaPool()
    if media_pool is None:
        print("ERROR: Could not get MediaPool.")
        sys.exit(1)

    print(f"Project: {project.GetName()!r}")
    print(f"Exporting metadata to {OUTPUT_PATH} …")

    t0 = time.perf_counter()
    ok = media_pool.ExportMetadata(str(OUTPUT_PATH))
    elapsed = time.perf_counter() - t0

    if not ok:
        print(f"ERROR: ExportMetadata returned False after {elapsed:.1f}s")
        sys.exit(1)

    if not OUTPUT_PATH.exists():
        print(f"ERROR: ExportMetadata returned True but {OUTPUT_PATH} was not created.")
        sys.exit(1)

    file_size_kb = OUTPUT_PATH.stat().st_size / 1024
    print(f"Done in {elapsed:.2f}s  ({file_size_kb:.1f} KB)")

    # Parse and report
    with open(OUTPUT_PATH, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        columns = reader.fieldnames or []

    print(f"\nTotal rows  : {len(rows)}")
    print(f"Columns ({len(columns)}): {columns}\n")

    print("First 5 rows:")
    for i, row in enumerate(rows[:5]):
        print(f"  [{i}]")
        for col in columns:
            val = row.get(col, "")
            if val:
                display = val if len(val) <= 80 else val[:77] + "…"
                print(f"      {col}: {display}")
    print()

    # Check for keyword and path columns specifically
    kw_col = next((c for c in columns if "keyword" in c.lower()), None)
    path_col = next((c for c in columns if "path" in c.lower() or "folder" in c.lower()), None)
    proxy_col = next((c for c in columns if "proxy" in c.lower()), None)

    print("Key columns detected:")
    print(f"  Keywords : {kw_col!r}")
    print(f"  Path     : {path_col!r}")
    print(f"  Proxy    : {proxy_col!r}")

    clips_with_keywords = sum(1 for r in rows if r.get(kw_col or "", "").strip())
    print(f"\nClips with keywords: {clips_with_keywords} / {len(rows)}")


if __name__ == "__main__":
    main()
