from __future__ import annotations

from flask import Flask, render_template, jsonify, request, send_file, Response
from io import BytesIO
import base64
import json
import os
import tempfile
import threading
import time
import uuid
from collections import OrderedDict
import resolve_api
import identity_recognition
import identity_registry
import profiler
import search_index

app = Flask(__name__)

# Pinned keywords — loaded from keywords_config.json if present, else template.
def _load_pinned_keywords() -> list[str]:
    base = os.path.dirname(os.path.abspath(__file__))
    for name in ("keywords_config.json", "keywords_config.template.json"):
        path = os.path.join(base, name)
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    return json.load(f).get("pinned_keywords", [])
            except Exception:
                pass
    return []

_PINNED_KEYWORDS: list[str] = _load_pinned_keywords()

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


from contextlib import contextmanager

_LOCK_TIMEOUT = 5.0  # seconds — interactive requests give up after this long

# Simple LRU thumbnail cache: proxy_path → PNG bytes (max 500 entries).
_THUMB_CACHE: OrderedDict[str, bytes] = OrderedDict()
_THUMB_CACHE_MAX = 500
_thumb_cache_lock = threading.Lock()


def _get_cached_thumbnail(path: str) -> bytes | None:
    with _thumb_cache_lock:
        if path in _THUMB_CACHE:
            _THUMB_CACHE.move_to_end(path)
            return _THUMB_CACHE[path]
    return None


def _put_cached_thumbnail(path: str, data: bytes) -> None:
    with _thumb_cache_lock:
        _THUMB_CACHE[path] = data
        _THUMB_CACHE.move_to_end(path)
        while len(_THUMB_CACHE) > _THUMB_CACHE_MAX:
            _THUMB_CACHE.popitem(last=False)


@contextmanager
def _resolve_lock_timeout():
    """Acquire _resolve_lock with a timeout. Raises RuntimeError if not acquired."""
    acquired = _resolve_lock.acquire(timeout=_LOCK_TIMEOUT)
    if not acquired:
        raise RuntimeError("Resolve is busy — a background operation is taking too long. Try again.")
    try:
        yield
    finally:
        _resolve_lock.release()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/clip")
def clip():
    try:
        with _resolve_lock_timeout():
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
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    unsorted = keywords_stored != keywords
    print(f"[clip] stored={keywords_stored!r} sorted={keywords!r} unsorted={unsorted}")
    return jsonify({
        "clip": name,
        "keywords": keywords,
        "unsorted": unsorted,
        "file_path": proxy_path,
        "no_proxy": not bool(proxy_path),
    })


@app.route("/api/clip/thumbnail")
def clip_thumbnail():
    # If the caller already knows the file path, use it directly (no IPC needed).
    file_path = request.args.get("path", "").strip()

    if not file_path:
        # Fall back: grab proxy path under the lock.
        try:
            with _resolve_lock_timeout():
                resolve = _get_resolve()
                item = resolve_api.get_selected_media_pool_item(resolve)
                if item is None:
                    return "", 204
                file_path = item.GetClipProperty("Proxy Media Path") or ""
        except Exception:
            return "", 204

    if not file_path:
        return "", 204

    # Check cache first.
    cached = _get_cached_thumbnail(file_path)
    if cached is not None:
        return send_file(BytesIO(cached), mimetype="image/png")

    # Extract frame with ffmpeg — no Resolve IPC, safe to run freely.
    png = resolve_api.thumbnail_from_file_path(file_path)
    if png is None:
        return "", 204

    _put_cached_thumbnail(file_path, png)
    return send_file(BytesIO(png), mimetype="image/png")


@app.route("/api/clip/filmstrip")
def clip_filmstrip():
    """Return all filmstrip frames for a clip in a single ffmpeg pass.
    Responds with JSON: {"frames": ["<base64 PNG>", ...]}
    Runs ffmpeg directly — no Resolve IPC needed."""
    file_path = request.args.get("path", "").strip()
    if not file_path:
        return jsonify({"frames": []}), 204

    t0 = time.perf_counter()
    frames, probe_ms, extract_ms = resolve_api.frames_from_file_path_timed(file_path)
    t_ffmpeg = time.perf_counter() - t0
    encoded = [base64.b64encode(f).decode() for f in frames]
    t_encode = (time.perf_counter() - t0 - t_ffmpeg) * 1000
    t_total = t_ffmpeg * 1000 + t_encode
    print(f"[filmstrip] frames={len(frames)} probe={probe_ms:.0f}ms extract={extract_ms:.0f}ms encode={t_encode:.0f}ms total={t_total:.0f}ms")
    profiler.record_filmstrip(t_total, probe_ms, extract_ms, t_encode, len(frames))
    return jsonify({"frames": encoded})


@app.route("/api/clip/suggestions")
def clip_suggestions():
    media_id = request.args.get("media_id", "").strip()
    # keywords param: comma-separated list sent by the client so server-side
    # dedup works correctly even when suggestions are served from the cache.
    kw_param = request.args.get("keywords", "").strip()
    current_keywords = [k.strip() for k in kw_param.split(",") if k.strip()] if kw_param else []

    # Fast path: try cache-only computation first (zero Resolve IPC).
    if media_id:
        cached = resolve_api.get_cached_suggestions(media_id)
        if cached is not None:
            return jsonify({"suggestions": cached})
        # _folder_cache is warm — compute from it without holding the lock.
        from_cache = resolve_api.suggest_keywords_from_cache(media_id, current_keywords)
        if from_cache is not None:
            print(f"[suggestions] served from folder cache for {media_id!r}")
            return jsonify({"suggestions": from_cache})

    # Slow path: cache cold — need Resolve IPC.
    try:
        with _resolve_lock_timeout():
            resolve = _get_resolve()
            suggestions, debug = resolve_api.suggest_keywords(resolve)
    except Exception as exc:
        # Lock timeout or Resolve error — return empty rather than 500
        # so the UI degrades gracefully instead of showing an error.
        print(f"[suggestions] fallback to empty: {exc}")
        return jsonify({"suggestions": []})

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
                with _resolve_lock_timeout():
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
                with _resolve_lock_timeout():
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

    t0 = time.perf_counter()
    try:
        with _resolve_lock_timeout():
            t_lock = time.perf_counter()
            nav_timing = {}
            nav_timing["lock_wait_ms"] = (t_lock - t0) * 1000
            resolve = _get_resolve()
            item, nav_timing = resolve_api.navigate_clip(resolve, direction)
            nav_timing["lock_wait_ms"] = (t_lock - t0) * 1000
            if item is None:
                return jsonify({"error": "No more clips"}), 404
            t_post0 = time.perf_counter()
            name = item.GetName() or "<unnamed clip>"
            media_id = item.GetMediaId()
            keywords = resolve_api.get_keywords(item)
            keywords_stored = resolve_api._normalize_keywords(
                item.GetMetadata("Keywords") or item.GetClipProperty("Keywords") or ""
            )
            proxy_path = item.GetClipProperty("Proxy Media Path") or ""
            nav_timing["post_nav_ipc_ms"] = (time.perf_counter() - t_post0) * 1000
            nav_timing["get_keywords_ms"] = nav_timing["post_nav_ipc_ms"]  # keep compat
            t_ipc = time.perf_counter() - t_lock
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    t_total = time.perf_counter() - t0

    # Pre-warm proximity suggestions in background so _last_suggestions is ready
    # before the browser requests /api/clip/suggestions. Uses lock-with-timeout
    # so it never blocks an interactive request.
    def _suggest_bg(captured_id=media_id, captured_kws=keywords):
        t_s = time.perf_counter()
        # Cache-only: navigate_clip() already populated _folder_cache, so this
        # succeeds in the vast majority of navigations with zero Resolve IPC.
        # If the clip isn't found (e.g. first-ever load with cold cache), we
        # give up rather than acquire the lock — the suggestions endpoint has
        # its own fallback and pre-warming is best-effort only.
        suggestions = resolve_api.suggest_keywords_from_cache(captured_id, captured_kws)
        suggest_ms = (time.perf_counter() - t_s) * 1000
        if suggestions is not None:
            profiler.record_suggest_bg(suggest_ms)
            print(f"[navigate] suggest_bg={suggest_ms:.0f}ms (cache)")
        else:
            print(f"[navigate] suggest_bg skipped — cache miss, suggestions deferred")

    threading.Thread(target=_suggest_bg, daemon=True).start()

    print(
        f"[navigate] clip={name!r} total={t_total*1000:.0f}ms"
        f" lock_wait={nav_timing.get('lock_wait_ms', 0):.0f}ms"
        f" pre_ipc={nav_timing.get('pre_ipc_ms', 0):.0f}ms"
        f" get_selected_pre={nav_timing.get('get_selected_ms_pre', 0):.0f}ms"
        f" resolve_folder={nav_timing.get('resolve_folder_ms', 0):.0f}ms"
        f" folder_cache={nav_timing.get('folder_cache_ms', 0):.0f}ms"
        f" {'MISS' if nav_timing.get('cache_miss') else 'hit'}"
        f" set_selected={nav_timing.get('set_selected_ms', 0):.0f}ms"
        f" post_nav_ipc={nav_timing.get('post_nav_ipc_ms', 0):.0f}ms"
    )
    profiler.record_navigate(t_total * 1000, t_ipc * 1000, nav_timing)
    return jsonify({
        "clip": name,
        "media_id": media_id,
        "keywords": keywords,
        "unsorted": keywords_stored != keywords,
        "file_path": proxy_path,
        "no_proxy": not bool(proxy_path),
    })


@app.route("/api/profiler/report", methods=["GET"])
def profiler_report():
    """Return current profiling summary as JSON."""
    return jsonify(profiler.summary())


@app.route("/api/profiler/dump", methods=["POST"])
def profiler_dump():
    """Write profiling report to a timestamped JSON file next to app.py."""
    path = profiler.dump()
    print(f"[profiler] report written to {path}")
    return jsonify({"path": path})


@app.route("/api/profiler/filmstrip-cache-hit", methods=["POST"])
def profiler_filmstrip_cache_hit():
    """Record a client-side filmstrip cache hit."""
    profiler.record_filmstrip_cache_hit()
    return "", 204


@app.route("/api/clip/neighbours", methods=["GET"])
def clip_neighbours():
    """Return prev/next proxy paths for a clip, read from cache only — no Resolve IPC."""
    media_id = request.args.get("media_id", "")
    if not media_id:
        return jsonify({"prev_path": "", "next_path": ""})
    prev_path, next_path = resolve_api.get_neighbours(media_id)
    return jsonify({"prev_path": prev_path, "next_path": next_path})


@app.route("/api/clip/keywords", methods=["POST"])
def set_keywords():
    global _catalog_refresh_pending
    body = request.get_json(silent=True) or {}
    keywords = body.get("keywords")
    if not isinstance(keywords, list):
        return jsonify({"error": "keywords must be a list"}), 400
    # Normalise at the ingest boundary: strip whitespace, drop empty tokens, dedup.
    keywords = resolve_api._dedup_keywords(resolve_api._normalize_keywords(keywords))

    try:
        with _resolve_lock_timeout():
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
            "candidates": det.get("candidates", []),
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


@app.route("/api/config/pinned-keywords")
def pinned_keywords():
    return jsonify({"pinned_keywords": _PINNED_KEYWORDS})


# ---------------------------------------------------------------------------
# Shot Finder — M1.3: index build + status
# ---------------------------------------------------------------------------

_SEARCH_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "search.db")


@app.route("/search/api/status")
def search_status():
    return jsonify(search_index.get_status(_SEARCH_DB_PATH))


@app.route("/search/api/build", methods=["POST"])
def search_build():
    with _resolve_lock:
        resolve = resolve_api.get_resolve()
        if resolve is None:
            return jsonify({"error": "Resolve not available"}), 503

        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            csv_path = f.name

        try:
            project_name = resolve_api.get_project_name(resolve)
            ok = resolve_api.export_metadata(resolve, csv_path)
            if not ok:
                return jsonify({"error": "ExportMetadata failed"}), 500
            clips = search_index.parse_export_csv(csv_path)
            proxy_map = resolve_api.collect_proxy_paths(resolve)
        finally:
            try:
                os.unlink(csv_path)
            except OSError:
                pass

    # Attach proxy paths to clip dicts.
    # Key is (file_name, clip_dir) to correctly disambiguate clips that share a
    # filename but live in different folders (e.g. two C0040.MP4 from different shoots).
    for c in clips:
        key = (c["file_name"], c["clip_dir"].rstrip("/\\"))
        c["proxy_path"] = proxy_map.get(key)

    search_index.build_index(_SEARCH_DB_PATH, clips, project_name=project_name)
    print(f"[search] index built: {len(clips)} clips from {project_name!r}")
    return jsonify(search_index.get_status(_SEARCH_DB_PATH))


@app.route("/search")
def search_page():
    return render_template("search.html")


@app.route("/search/api/select", methods=["POST"])
def search_select():
    data = request.get_json(silent=True) or {}
    file_name = (data.get("file_name") or "").strip()
    clip_dir = (data.get("clip_dir") or "").strip()
    if not file_name or not clip_dir:
        return jsonify({"ok": False, "error": "file_name and clip_dir required"}), 400
    with _resolve_lock:
        resolve = resolve_api.get_resolve()
        if resolve is None:
            return jsonify({"ok": False, "error": "Resolve not available"}), 503
        result = resolve_api.select_clip_in_resolve(resolve, file_name, clip_dir)
    return jsonify(result)


@app.route("/search/api/show_in_finder", methods=["POST"])
def search_show_in_finder():
    import subprocess, os
    data = request.get_json(silent=True) or {}
    file_name = (data.get("file_name") or "").strip()
    clip_dir = (data.get("clip_dir") or "").strip()
    if not file_name or not clip_dir:
        return jsonify({"ok": False, "error": "file_name and clip_dir required"}), 400
    full_path = os.path.join(clip_dir, file_name)
    subprocess.Popen(["open", "-R", full_path])
    return jsonify({"ok": True})


@app.route("/search/api/histogram")
def search_histogram():
    q = request.args.get("q", "").strip()
    return jsonify(search_index.search_histogram(_SEARCH_DB_PATH, q))


@app.route("/search/api/keywords")
def search_keywords():
    return jsonify({"keywords": search_index.get_all_keywords(_SEARCH_DB_PATH)})


@app.route("/search/api/query")
def search_query():
    q = request.args.get("q", "").strip()
    date_from = request.args.get("date_from", "").strip() or None
    date_to = request.args.get("date_to", "").strip() or None
    try:
        limit = min(int(request.args.get("limit", 50)), 200)
        offset = int(request.args.get("offset", 0))
    except ValueError:
        return jsonify({"error": "invalid limit/offset"}), 400
    return jsonify(search_index.search_clips(
        _SEARCH_DB_PATH, q, limit, offset,
        date_from=date_from, date_to=date_to,
    ))


@app.route("/player")
def player_page():
    path = request.args.get("path", "").strip()
    if not path or not os.path.isfile(path):
        return "File not found", 404
    return render_template("player.html", path=path, filename=os.path.basename(path))


@app.route("/api/clip/proxy")
def clip_proxy():
    """Transcode proxy to H.264/AAC fragmented MP4 via ffmpeg and stream to browser.

    Accepts ?t=<seconds> to seek before transcoding, enabling scrubber support
    without needing byte-range seeks on the original file.
    """
    import subprocess
    path = request.args.get("path", "").strip()
    if not path or not os.path.isfile(path):
        return "File not found", 404

    try:
        start = float(request.args.get("t", 0))
    except ValueError:
        start = 0.0

    cmd = [
        "ffmpeg",
        "-ss", str(start),
        "-i", path,
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "frag_keyframe+empty_moov+default_base_moof",
        "-f", "mp4",
        "pipe:1",
    ]

    def generate():
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            while True:
                chunk = proc.stdout.read(65536)
                if not chunk:
                    break
                yield chunk
        finally:
            proc.kill()
            proc.wait()

    return Response(generate(), mimetype="video/mp4", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })


if __name__ == "__main__":
    app.run(debug=False, port=5001, threaded=True)
