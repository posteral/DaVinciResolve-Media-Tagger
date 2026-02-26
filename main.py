from __future__ import annotations

import argparse
import json
import sys

from resolve_api import (
    get_resolve,
    get_selected_media_pool_item,
    get_keywords,
    merge_keywords,
    set_keywords,
    format_output,
    _normalize_keywords,
    _error,
)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read or write keyword metadata on the selected DaVinci Resolve clip."
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--set",
        metavar="KEYWORDS",
        dest="set_keywords",
        help='Set keywords, replacing all existing ones. Comma-separated. e.g. "k1,k2"',
    )
    group.add_argument(
        "--replace",
        metavar="KEYWORDS",
        dest="replace_keywords",
        help="Alias for --set.",
    )
    group.add_argument(
        "--append",
        metavar="KEYWORDS",
        dest="append_keywords",
        help="Append keywords to existing ones. Comma-separated.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be written without actually writing.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Output in JSON format.",
    )
    return parser


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()

    # Determine write mode and incoming keywords
    if args.set_keywords is not None:
        write_mode: str | None = "set"
        incoming_raw: str = args.set_keywords
    elif args.replace_keywords is not None:
        write_mode = "set"
        incoming_raw = args.replace_keywords
    elif args.append_keywords is not None:
        write_mode = "append"
        incoming_raw = args.append_keywords
    else:
        write_mode = None
        incoming_raw = ""

    try:
        resolve = get_resolve()
    except Exception as exc:
        _error(str(exc), args.as_json)
        return 1

    media_pool_item = get_selected_media_pool_item(resolve)
    if media_pool_item is None:
        _error(
            "No selected clip found. Select a clip in the timeline (or media pool) and run again.",
            args.as_json,
        )
        return 1

    clip_name = media_pool_item.GetName() or "<unnamed clip>"
    existing = get_keywords(media_pool_item)

    if write_mode is not None:
        incoming = _normalize_keywords(incoming_raw)
        new_keywords = merge_keywords(existing, incoming, write_mode)

        if args.dry_run:
            if args.as_json:
                print(json.dumps({"clip": clip_name, "dry_run": True, "keywords": new_keywords}))
            else:
                print(f"Clip: {clip_name}")
                print("[dry-run] Keywords would be set to:")
                if new_keywords:
                    for kw in new_keywords:
                        print(f"- {kw}")
                else:
                    print("(none)")
        else:
            ok = set_keywords(media_pool_item, new_keywords)
            if not ok:
                _error("Failed to write keywords to Resolve. Check that External Scripting is enabled.", args.as_json)
                return 1
            if args.as_json:
                print(json.dumps({"clip": clip_name, "keywords": new_keywords, "written": True}))
            else:
                print(f"Clip: {clip_name}")
                print("Keywords written:")
                if new_keywords:
                    for kw in new_keywords:
                        print(f"- {kw}")
                else:
                    print("(none)")
    else:
        print(format_output(clip_name, existing, args.as_json))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
