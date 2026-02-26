from flask import Flask, render_template, jsonify
import main

app = Flask(__name__)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/clip")
def clip():
    try:
        resolve = main.get_resolve()
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    item = main.get_selected_media_pool_item(resolve)
    if item is None:
        return jsonify({"error": "No clip selected"}), 404

    return jsonify({
        "clip": item.GetName() or "<unnamed clip>",
        "keywords": main.get_keywords(item),
    })


if __name__ == "__main__":
    app.run(debug=False, port=5000)
