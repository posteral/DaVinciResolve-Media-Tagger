from flask import Flask, render_template, jsonify, request, send_file
from io import BytesIO
import threading
import time
import uuid
import resolve_api
import identity_recognition
import identity_registry

app = Flask(__name__)

# Resolve scripting is not thread-safe: serialise every IPC call.
_resolve_lock = threading.Lock()
_resolve_obj = None

# Identity recognition: process-level caches keyed by face_token (uuid string).
# Both are cleared on server restart — confirm handles that gracefully.
_face_crop_cache: dict[str, bytes] = {}   # face_token → JPEG crop bytes
_detection_cache: dict[str, list] = {}    # face_token → mean embedding

# Keyword catalog: populated on first request, refreshed after every Save.
_keyword_catalog: list[str] = []
_catalog_loaded = False
_catalog_lock = threading.Lock()  # guards _keyword_catalog / _catalog_loaded
_catalog_refresh_pending = False   # prevents stacking multiple refresh threads


def _rebuild_folder_cache_bg() -> None:
    """Rebuild the folder clip cache in a background thread after a Save.

    Uses the same polite lock pattern as _refresh_catalog_bg so interactive
    requests are never blocked waiting behind this rebuild."""
    try:
        for _ in range(30):
            acquired = _resolve_lock.acquire(timeout=0.1)
            if acquired:
                try:
                    resolve = _get_resolve()
                    resolve_api.suggest_keywords(resolve)  # populates _folder_cache
                finally:
                    _resolve_lock.release()
                break
            time.sleep(2)
    except Exception:
        pass  # stale cache is fine; next navigate will rebuild on demand


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
            proxy_path = item.GetClipProperty("Proxy Media Path") or ""
            suggestions, debug = resolve_api.suggest_keywords(resolve, current_item=item)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    unsorted = keywords_stored != keywords
    print(f"[clip] stored={keywords_stored!r} sorted={keywords!r} unsorted={unsorted}")
    print(f"[suggestions] {debug}")
    return jsonify({
        "clip": name,
        "keywords": keywords,
        "unsorted": unsorted,
        "file_path": proxy_path,
        "no_proxy": not bool(proxy_path),
        "suggestions": suggestions,
    })


@app.route("/api/clip/thumbnail")
def clip_thumbnail():
    # If the caller already knows the file path, use it directly (no IPC needed).
    file_path = request.args.get("path", "").strip()

    if not file_path:
        # Fall back: grab proxy path under the lock.
        try:
            with _resolve_lock:
                resolve = _get_resolve()
                item = resolve_api.get_selected_media_pool_item(resolve)
                if item is None:
                    return "", 204
                file_path = item.GetClipProperty("Proxy Media Path") or ""
        except Exception:
            return "", 204

    if not file_path:
        return "", 204

    # Extract frame with ffmpeg — no Resolve IPC, safe to run freely.
    png = resolve_api.thumbnail_from_file_path(file_path)
    if png is None:
        return "", 204

    return send_file(BytesIO(png), mimetype="image/png")


@app.route("/api/clip/filmstrip-frame")
def clip_filmstrip_frame():
    """Return a single filmstrip frame by index (0-based) for the given proxy path.
    Runs ffmpeg directly — no Resolve IPC needed."""
    file_path = request.args.get("path", "").strip()
    try:
        index = int(request.args.get("index", "0"))
    except ValueError:
        return "", 400

    if not file_path:
        return "", 204

    percentages = (0.1, 0.3, 0.5, 0.7, 0.9)
    if index < 0 or index >= len(percentages):
        return "", 400

    try:
        ffmpeg = resolve_api._ffmpeg_path()
        ffprobe = resolve_api._ffprobe_path()
    except FileNotFoundError:
        return "", 204

    duration = resolve_api._probe_duration(file_path, ffprobe)
    seek = duration * percentages[index] if duration > 0 else 0.0
    png = resolve_api._extract_frame(file_path, ffmpeg, seek)
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


@app.route("/api/clip/ai-suggestion", methods=["GET", "POST"])
def clip_ai_suggestion():
    # Accept both GET (legacy, no catalog) and POST (with JSON body including catalog).
    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        file_path = body.get("path", "").strip()
        existing_keywords = body.get("keywords", [])
        proximity_suggestions = body.get("suggestions", [])
        catalog = body.get("catalog", [])
        if not file_path:
            try:
                with _resolve_lock:
                    resolve = _get_resolve()
                    item = resolve_api.get_selected_media_pool_item(resolve)
                    if item is None:
                        return jsonify({"suggestions": []})
                    file_path = item.GetClipProperty("Proxy Media Path") or ""
                    existing_keywords = resolve_api.get_keywords(item)
            except Exception as exc:
                return jsonify({"error": str(exc)}), 500
    else:
        file_path = request.args.get("path", "").strip()
        existing_keywords = []
        proximity_suggestions = []
        catalog = []
        if not file_path:
            try:
                with _resolve_lock:
                    resolve = _get_resolve()
                    item = resolve_api.get_selected_media_pool_item(resolve)
                    if item is None:
                        return jsonify({"suggestions": []})
                    file_path = item.GetClipProperty("Proxy Media Path") or ""
                    existing_keywords = resolve_api.get_keywords(item)
            except Exception as exc:
                return jsonify({"error": str(exc)}), 500
        else:
            kw_param = request.args.get("keywords", "").strip()
            existing_keywords = [k.strip() for k in kw_param.split(",") if k.strip()]
            sug_param = request.args.get("suggestions", "").strip()
            proximity_suggestions = [k.strip() for k in sug_param.split(",") if k.strip()]

    if not file_path:
        return jsonify({"suggestions": []})

    suggestions = resolve_api.ai_suggest_keywords(
        file_path,
        existing_keywords=existing_keywords,
        proximity_suggestions=proximity_suggestions,
        catalog=catalog,
    )
    print(f"[ai-suggestion] file={file_path!r} existing={existing_keywords!r} proximity={proximity_suggestions!r} catalog_size={len(catalog)} suggestions={suggestions!r}")
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
            proxy_path = item.GetClipProperty("Proxy Media Path") or ""
            suggestions, debug = resolve_api.suggest_keywords(resolve, current_item=item)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    print(f"[navigate] {debug}")
    return jsonify({
        "clip": name,
        "keywords": keywords,
        "unsorted": keywords_stored != keywords,
        "file_path": proxy_path,
        "no_proxy": not bool(proxy_path),
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

    # Invalidate folder cache and rebuild it in background so the next
    # navigate press finds a warm cache with up-to-date keywords.
    resolve_api.invalidate_folder_cache()
    threading.Thread(target=_rebuild_folder_cache_bg, daemon=True).start()

    # Refresh catalog in background — does not block the Save response.
    with _catalog_lock:
        already = _catalog_refresh_pending
        if not already:
            _catalog_refresh_pending = True
    if not already:
        threading.Thread(target=_refresh_catalog_bg, daemon=True).start()

    return jsonify({"clip": name, "keywords": keywords})


@app.route("/api/clip/detect-identities", methods=["POST"])
def detect_identities():
    """Run face detection on the clip's proxy frames. Expensive — runs outside
    _resolve_lock. The caller passes the proxy path in the request body."""
    body = request.get_json(silent=True) or {}
    file_path = body.get("path", "").strip()
    if not file_path:
        return jsonify({"error": "path is required"}), 400

    frames = resolve_api.frames_from_file_path(file_path)
    if not frames:
        return jsonify({"detections": []})

    registry = identity_registry.load_registry()
    detections = identity_recognition.run_detection_pipeline(frames, registry)

    response_detections = []
    for det in detections:
        token = str(uuid.uuid4())
        _face_crop_cache[token] = det["best_crop"]
        _detection_cache[token] = det["mean_embedding"]
        response_detections.append({
            "face_token": token,
            "status": det["status"],
            "identity_id": det["identity_id"],
            "display_name": det["display_name"],
            "keyword_string": det["keyword_string"],
            "distance": det["distance"],
            "occurrence_count": det["occurrence_count"],
        })

    print(f"[detect-identities] path={file_path!r} found={len(response_detections)} face(s)")
    return jsonify({"detections": response_detections})


@app.route("/api/clip/face-crop")
def face_crop():
    """Return the JPEG face crop for a given face_token. 404 if token unknown."""
    token = request.args.get("token", "").strip()
    crop = _face_crop_cache.get(token)
    if crop is None:
        return "", 404
    return send_file(BytesIO(crop), mimetype="image/jpeg")


@app.route("/api/identities")
def list_identities():
    """Return all known identities (lightweight, no embeddings) for the UI."""
    registry = identity_registry.load_registry()
    return jsonify({"identities": identity_registry.list_identities(registry)})


@app.route("/api/identities/confirm", methods=["POST"])
def confirm_identities():
    """Commit user assignments from the review panel.

    For each assignment:
    - is_new_identity=True  → create a new registry entry
    - is_new_identity=False → append embedding to existing entry
    Returns the list of keyword_strings that should be added to the clip."""
    body = request.get_json(silent=True) or {}
    assignments = body.get("assignments", [])
    if not isinstance(assignments, list):
        return jsonify({"error": "assignments must be a list"}), 400

    registry = identity_registry.load_registry()
    keywords_added: list[str] = []

    for assignment in assignments:
        face_token = assignment.get("face_token", "")
        display_name = (assignment.get("display_name") or "").strip()
        keyword_string = (assignment.get("keyword_string") or display_name).strip()
        identity_id = assignment.get("identity_id")
        is_new = assignment.get("is_new_identity", False)
        add_as_keyword = assignment.get("add_as_keyword", True)

        if not display_name:
            continue

        embedding = _detection_cache.get(face_token)
        crop = _face_crop_cache.get(face_token)

        if is_new or not identity_id:
            # Check if a matching name already exists (user may have typed an
            # existing name rather than selecting from the datalist).
            existing = identity_registry.find_identity_by_name(registry, display_name)
            if existing:
                identity_id = existing["identity_id"]
                is_new = False
            else:
                registry, identity_id = identity_registry.add_identity(
                    registry, display_name, keyword_string,
                    embedding if embedding else [],
                    crop,
                )

        if not is_new and identity_id:
            if embedding:
                registry = identity_registry.update_identity_embedding(
                    registry, identity_id, embedding, crop
                )

        if add_as_keyword and keyword_string:
            if keyword_string.lower() not in {k.lower() for k in keywords_added}:
                keywords_added.append(keyword_string)

    try:
        identity_registry.save_registry(registry)
    except Exception as exc:
        return jsonify({"error": f"Failed to save registry: {exc}"}), 500

    print(f"[confirm-identities] keywords_added={keywords_added!r}")
    return jsonify({"keywords_added": keywords_added})


if __name__ == "__main__":
    app.run(debug=False, port=5000, threaded=True)
