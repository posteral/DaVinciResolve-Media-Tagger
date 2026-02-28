from flask import Flask, render_template, jsonify, request, send_file
from io import BytesIO
import threading
import resolve_api

app = Flask(__name__)

# Resolve scripting is not thread-safe: serialise every IPC call.
_resolve_lock = threading.Lock()
_resolve_obj = None


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
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    return jsonify({"clip": name, "keywords": keywords})


@app.route("/api/clip/thumbnail")
def clip_thumbnail():
    # Step 1: grab file path under the lock (fast IPC call).
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

    # Step 2: extract frame with ffmpeg — no Resolve IPC, safe to run freely.
    png = resolve_api.thumbnail_from_file_path(file_path)
    if png is None:
        return "", 204

    return send_file(BytesIO(png), mimetype="image/png")


@app.route("/api/clip/suggestions")
def clip_suggestions():
    try:
        with _resolve_lock:
            resolve = _get_resolve()
            suggestions = resolve_api.suggest_keywords(resolve)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    return jsonify({"suggestions": suggestions})


@app.route("/api/clip/ai-suggestion")
def clip_ai_suggestion():
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

    if not file_path:
        return jsonify({"suggestions": []})

    suggestions = resolve_api.ai_suggest_keywords(file_path, existing_keywords=existing_keywords)
    print(f"[ai-suggestion] file={file_path!r} existing={existing_keywords!r} suggestions={suggestions!r}")
    return jsonify({"suggestions": suggestions})


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
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    return jsonify({"clip": name, "keywords": keywords})


@app.route("/api/clip/keywords", methods=["POST"])
def set_keywords():
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

    return jsonify({"clip": name, "keywords": keywords})


if __name__ == "__main__":
    app.run(debug=False, port=5000, threaded=True)
