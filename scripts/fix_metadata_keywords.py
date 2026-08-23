"""fix_metadata_keywords.py — Clean keyword tokens in a DaVinci Resolve ExportMetadata CSV.

Resolves the duplicate Smart Bin problem caused by keywords being written with
leading/trailing spaces (e.g. " Italy" vs "Italy"). For every clip row the script:

  1. Splits the Keywords field on commas.
  2. Strips leading/trailing whitespace from each token.
  3. Drops empty tokens.
  4. Removes case-insensitive duplicates (first occurrence wins).
  5. Sorts alphabetically (case-insensitive), matching the tagger's behaviour.
  6. Re-joins with "," (no space) — the format Resolve imports cleanly.

Input and output are both UTF-16 CSV, the format Resolve uses for ExportMetadata /
ImportMetadata.

Usage
-----
1. In Resolve: File → Export → Metadata... → save as <input_file>
2. Run this script:

       .venv/bin/python3 scripts/fix_metadata_keywords.py <input_file> [output_file]

   If output_file is omitted the fixed file is written next to the input with
   " FIXED" appended to the stem, e.g.:

       export.csv  →  export FIXED.csv

3. In Resolve: File → Import → Timeline Markers / Metadata... → select the output file.

The original file is never modified.
"""
from __future__ import annotations

import csv
import os
import sys
from pathlib import Path


def _fix_keywords(raw: str) -> str:
    tokens = [t.strip() for t in raw.split(",") if t.strip()]
    seen: set[str] = set()
    deduped: list[str] = []
    for t in tokens:
        if t.lower() not in seen:
            seen.add(t.lower())
            deduped.append(t)
    return ",".join(sorted(deduped, key=str.casefold))


def fix_metadata_csv(src: Path, dst: Path) -> int:
    """Read src, fix keywords, write dst. Returns number of rows changed."""
    with open(src, encoding="utf-16", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        rows = list(reader)

    kw_col = next((c for c in fieldnames if c.lower() == "keywords"), None)
    if kw_col is None:
        raise ValueError(f"No 'Keywords' column found in {src}. Columns: {fieldnames}")

    changed = 0
    for row in rows:
        original = row.get(kw_col, "")
        fixed = _fix_keywords(original)
        if fixed != original:
            row[kw_col] = fixed
            changed += 1

    with open(dst, "w", encoding="utf-16", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    return changed


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    src = Path(sys.argv[1])
    if not src.exists():
        print(f"Error: file not found: {src}", file=sys.stderr)
        sys.exit(1)

    dst = Path(sys.argv[2]) if len(sys.argv) >= 3 else src.parent / (src.stem + " FIXED" + src.suffix)

    print(f"Input : {src}  ({src.stat().st_size / 1024:.1f} KB)")
    print(f"Output: {dst}")

    changed = fix_metadata_csv(src, dst)
    total = sum(1 for _ in open(dst, encoding="utf-16")) - 1  # subtract header
    print(f"Done  : {changed} / {total} rows updated  →  {dst.stat().st_size / 1024:.1f} KB")


if __name__ == "__main__":
    main()
