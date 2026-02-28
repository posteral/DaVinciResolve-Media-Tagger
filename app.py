from flask import Flask, render_template, jsonify, request, send_file
from io import BytesIO
import threading
import time
import resolve_api

app = Flask(__name__)

# Resolve scripting is not thread-safe: serialise every IPC call.
_resolve_lock = threading.Lock()
_resolve_obj = None

# Keyword catalog: populated on first request, refreshed after every Save.
_keyword_catalog: list[str] = []
_catalog_loaded = False
_catalog_lock = threading.Lock()  # guards _keyword_catalog / _catalog_loaded
_catalog_refresh_pending = False   # prevents stacking multiple refresh threads


def _refresh_catalog_bg() -> None:
    """Rebuild the keyword catalog in a background thread.

    Repeatedly tries to acquire _resolve_lock with a short timeout so that
    interactive requests (navigate, clip, save) are never blocked waiting
    behind this walk. Gives up after 30 attempts (~60 s total) to avoid
    spinning forever if Resolve is unresponsive.
    """
    global _keyword_catalog, _catalog_loaded, _catalog_refresh_pending
    try:
        catalog = None
        for _ in range(30):
            acquired = _resolve_lock.acquire(timeout=0.1)
            if acquired:
                try:
                    resolve = _get_resolve()
                    catalog = resolve_api.get_all_project_keywords(resolve)
                finally:
                    _resolve_lock.release()
                break
            # Lock is busy (interactive request in flight) — wait and retry.
            time.sleep(2)

        if catalog is not None:
            with _catalog_lock:
                _keyword_catalog = catalog
                _catalog_loaded = True
    except Exception:
        pass  # stale catalog is fine
    finally:
        with _catalog_lock:
            _catalog_refresh_pending = False


def _get_resolve():
    """Return the cached resolve object. Must be called with _resolve_lock held."""
    global _resolve_obj
    if _resolve_obj is None:
        _resolve_obj = resolve_api.get_resolve()
    return _resolve_obj


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/clip")
def clip():
    try:
        with _resolve_lock:
            resolve = _get_resolve()
            item = resolve_api.get_selected_media_pool_item(resolve)
            if item is None:
                return jsonify({"error": "No clip selected"}), 404
            name = item.GetName() or "<unnamed clip>"
            keywords = resolve_api.get_keywords(item)
            keywords_stored = resolve_api._normalize_keywords(
                item.GetMetadata("Keywords") or item.GetClipProperty("Keywords") or ""
            )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    unsorted = keywords_stored != keywords
    print(f"[clip] stored={keywords_stored!r} sorted={keywords!r} unsorted={unsorted}")
    return jsonify({
        "clip": name,
        "keywords": keywords,
        "unsorted": unsorted,
    })


@app.route("/api/clip/thumbnail")
def clip_thumbnail():
    # If the caller already knows the file path, use it directly (no IPC needed).
    file_path = request.args.get("path", "").strip()

    if not file_path:
        # Fall back: grab file path under the lock.
        try:
            with _resolve_lock:
                resolve = _get_resolve()
                item = resolve_api.get_selected_media_pool_item(resolve)
                if item is None:
                    return "", 204
                file_path = (
                    item.GetClipProperty("Proxy Media Path")
                    or item.GetClipProperty("File Path")
                    or ""
                )
        except Exception:
            return "", 204

    if not file_path:
        return "", 204

    # Extract frame with ffmpeg — no Resolve IPC, safe to run freely.
    png = resolve_api.thumbnail_from_file_path(file_path)
    if png is None:
        return "", 204

    return send_file(BytesIO(png), mimetype="image/png")


@app.route("/api/clip/suggestions")
def clip_suggestions():
    try:
        with _resolve_lock:
            resolve = _get_resolve()
            suggestions, debug = resolve_api.suggest_keywords(resolve)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    print(f"[suggestions] {debug}")
    return jsonify({"suggestions": suggestions})


@app.route("/api/clip/ai-suggestion")
def clip_ai_suggestion():
    # If the caller already knows the file path, use it directly (no IPC needed).
    file_path = request.args.get("path", "").strip()
    existing_keywords: list[str] = []

    if not file_path:
        try:
            with _resolve_lock:
                resolve = _get_resolve()
                item = resolve_api.get_selected_media_pool_item(resolve)
                if item is None:
                    return jsonify({"suggestions": []})
                file_path = (
                    item.GetClipProperty("Proxy Media Path")
                    or item.GetClipProperty("File Path")
                    or ""
                )
                existing_keywords = resolve_api.get_keywords(item)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500
    else:
        # Keywords were already fetched by the navigate route; caller passes them
        # as a comma-separated query param so we don't need the lock at all.
        kw_param = request.args.get("keywords", "").strip()
        existing_keywords = [k.strip() for k in kw_param.split(",") if k.strip()]

    if not file_path:
        return jsonify({"suggestions": []})

    suggestions = resolve_api.ai_suggest_keywords(file_path, existing_keywords=existing_keywords)
    print(f"[ai-suggestion] file={file_path!r} existing={existing_keywords!r} suggestions={suggestions!r}")
    return jsonify({"suggestions": suggestions})


@app.route("/api/keywords/catalog")
def keywords_catalog():
    global _catalog_refresh_pending
    with _catalog_lock:
        loaded = _catalog_loaded
        pending = _catalog_refresh_pending
        catalog = list(_keyword_catalog)
    if not loaded and not pending:
        with _catalog_lock:
            _catalog_refresh_pending = True
        threading.Thread(target=_refresh_catalog_bg, daemon=True).start()
    return jsonify({"keywords": catalog})


@app.route("/api/clip/navigate", methods=["POST"])
def navigate_clip():
    body = request.get_json(silent=True) or {}
    direction_str = body.get("direction")
    if direction_str == "next":
        direction = 1
    elif direction_str == "prev":
        direction = -1
    else:
        return jsonify({"error": "direction must be 'next' or 'prev'"}), 400

    try:
        with _resolve_lock:
            resolve = _get_resolve()
            item = resolve_api.navigate_clip(resolve, direction)
            if item is None:
                return jsonify({"error": "No more clips"}), 404
            name = item.GetName() or "<unnamed clip>"
            keywords = resolve_api.get_keywords(item)
            keywords_stored = resolve_api._normalize_keywords(
                item.GetMetadata("Keywords") or item.GetClipProperty("Keywords") or ""
            )
            file_path = (
                item.GetClipProperty("Proxy Media Path")
                or item.GetClipProperty("File Path")
                or ""
            )
            suggestions, debug = resolve_api.suggest_keywords(resolve, current_item=item)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    print(f"[navigate] {debug}")
    return jsonify({
        "clip": name,
        "keywords": keywords,
        "unsorted": keywords_stored != keywords,
        "file_path": file_path,
        "suggestions": suggestions,
    })


@app.route("/api/clip/keywords", methods=["POST"])
def set_keywords():
    global _catalog_refresh_pending
    body = request.get_json(silent=True) or {}
    keywords = body.get("keywords")
    if not isinstance(keywords, list):
        return jsonify({"error": "keywords must be a list"}), 400

    try:
        with _resolve_lock:
            resolve = _get_resolve()
            item = resolve_api.get_selected_media_pool_item(resolve)
            if item is None:
                return jsonify({"error": "No clip selected"}), 404
            ok = resolve_api.set_keywords(item, keywords)
            name = item.GetName() or "<unnamed clip>"
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    if not ok:
        return jsonify({"error": "Resolve rejected the write. Check External Scripting is enabled."}), 500

    # Refresh catalog in background — does not block the Save response.
    with _catalog_lock:
        already = _catalog_refresh_pending
        if not already:
            _catalog_refresh_pending = True
    if not already:
        threading.Thread(target=_refresh_catalog_bg, daemon=True).start()

    return jsonify({"clip": name, "keywords": keywords})


if __name__ == "__main__":
    app.run(debug=False, port=5000, threaded=True)
