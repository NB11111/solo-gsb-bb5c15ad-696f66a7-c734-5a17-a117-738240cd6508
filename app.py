"""ICU 文案分支预览器 — Flask 入口。"""
import os

from flask import Flask, jsonify, render_template, request

from icu import analyze

app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False  # 响应中保留中文


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/api/preview")
def preview():
    data = request.get_json(force=True, silent=True) or {}
    template = data.get("template", "")
    locale = data.get("locale", "en")
    variables = data.get("variables", {})
    if not isinstance(template, str):
        return jsonify({"ok": False, "errors": [{
            "message": "template 必须是字符串", "pos": 0, "length": 1,
            "line": 1, "col": 1, "severity": "error"}]}), 400
    if not isinstance(variables, dict):
        variables = {}
    return jsonify(analyze(template, locale, variables))


@app.get("/api/health")
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
