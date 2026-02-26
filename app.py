from flask import Flask, render_template, jsonify, request
import resolve_api

app = Flask(__name__)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/clip")
def clip():
    try:
        resolve = resolve_api.get_resolve()
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    item = resolve_api.get_selected_media_pool_item(resolve)
    if item is None:
        return jsonify({"error": "No clip selected"}), 404

    return jsonify({
        "clip": item.GetName() or "<unnamed clip>",
        "keywords": resolve_api.get_keywords(item),
    })


@app.route("/api/clip/keywords", methods=["POST"])
def set_keywords():
    body = request.get_json(silent=True) or {}
    keywords = body.get("keywords")
    if not isinstance(keywords, list):
        return jsonify({"error": "keywords must be a list"}), 400

    try:
        resolve = resolve_api.get_resolve()
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    item = resolve_api.get_selected_media_pool_item(resolve)
    if item is None:
        return jsonify({"error": "No clip selected"}), 404

    ok = resolve_api.set_keywords(item, keywords)
    if not ok:
        return jsonify({"error": "Resolve rejected the write. Check External Scripting is enabled."}), 500

    return jsonify({"clip": item.GetName() or "<unnamed clip>", "keywords": keywords})


if __name__ == "__main__":
    app.run(debug=False, port=5000)
