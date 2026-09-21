"""ICU 文案分支预览器 —— Flask 应用。"""
from __future__ import annotations

import os

from flask import Flask, jsonify, render_template, request

from .icu_parser import parse_icu
from .renderer import render_icu


def create_app() -> Flask:
    app = Flask(__name__, static_folder="static", template_folder="templates")

    def _linecol(source: str, offset: int) -> tuple[int, int]:
        line, col = 1, 1
        for i, ch in enumerate(source):
            if i >= offset:
                break
            if ch == "\n":
                line += 1
                col = 1
            else:
                col += 1
        return line, col

    def _build_payload(data: dict) -> dict:
        source = data.get("template", "") or ""
        locale = data.get("locale", "en") or "en"
        samples = data.get("variables") or {}
        if not isinstance(samples, dict):
            samples = {}
        samples = {str(k): str(v) for k, v in samples.items()}

        ast, parse_errors = parse_icu(source)
        result = render_icu(ast, parse_errors, source, locale, samples)

        # 错误按位置去重 (同一 offset+message)
        seen: set[tuple] = set()
        errors = []
        for e in result.errors:
            key = (e.get("offset"), e.get("message"))
            if key in seen:
                continue
            seen.add(key)
            line, col = _linecol(source, e.get("offset", 0))
            errors.append({
                "line": e.get("line") or line,
                "col": e.get("col") or col,
                "offset": e.get("offset", 0),
                "end": e.get("end", 0),
                "message": e["message"],
            })
        errors.sort(key=lambda e: e["offset"])

        warnings = [{
            "message": w["message"],
            "line": w.get("line", 0),
            "col": w.get("col", 0),
            "offset": w.get("offset", 0),
            "end": w.get("end", 0),
        } for w in result.warnings]

        variables = []
        for v in result.variables:
            line, col = _linecol(source, v.offset)
            variables.append({
                "name": v.name,
                "type": v.type,
                "used_in": v.used_in,
                "sample": v.sample,
                "line": line,
                "col": col,
                "offset": v.offset,
            })

        branches = []
        for b in result.branches:
            line, col = _linecol(source, b.key_start)
            branches.append({
                "id": b.id,
                "depth": b.depth,
                "covered": b.covered,
                "conditions": [
                    {
                        "var": c.var,
                        "kind": c.kind,
                        "matcher": c.matcher,
                        "value": c.value,
                        "label": c.label,
                        "offset": c.key_start,
                    }
                    for c in b.conditions
                ],
                "condition_text": " 且 ".join(c.label for c in b.conditions) or "(顶层文本)",
                "text": b.text,
                "line": line,
                "col": col,
                "offset": b.key_start,
                "end": b.key_end,
                "arg_start": b.arg_start,
                "arg_end": b.arg_end,
            })

        return {
            "ok": not errors,
            "rendered": result.text,
            "locale_input": result.locale,
            "locale_resolved": result.resolved_locale,
            "errors": errors,
            "warnings": warnings,
            "variables": variables,
            "branches": branches,
            "coverage": {
                "total": len(branches),
                "covered": sum(1 for b in branches if b["covered"]),
            },
        }

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.post("/api/preview")
    def api_preview():
        data = request.get_json(silent=True) or {}
        return jsonify(_build_payload(data))

    @app.post("/api/export")
    def api_export():
        data = request.get_json(silent=True) or {}
        payload = _build_payload(data)
        # 导出 JSON 不含内部位置信息的精简版 + 完整版
        export = {
            "template": data.get("template", ""),
            "locale": payload["locale_input"],
            "locale_resolved": payload["locale_resolved"],
            "variables": {v["name"]: v["sample"] for v in payload["variables"]},
            "rendered": payload["rendered"],
            "branches": [
                {
                    "conditions": b["condition_text"],
                    "rendered": b["text"],
                    "covered": b["covered"],
                }
                for b in payload["branches"]
            ],
            "coverage": payload["coverage"],
            "errors": [
                {"line": e["line"], "col": e["col"], "message": e["message"]}
                for e in payload["errors"]
            ],
        }
        resp = jsonify(export)
        resp.headers["Content-Disposition"] = "attachment; filename=icu-preview.json"
        return resp

    @app.get("/healthz")
    def healthz():
        return jsonify({"status": "ok"})

    return app


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    create_app().run(host="0.0.0.0", port=port, debug=True)
