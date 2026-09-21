"""生产启动入口: waitress 托管 Flask。"""
from waitress import serve

from app import create_app

application = create_app()

if __name__ == "__main__":
    import os

    port = int(os.environ.get("PORT", "8000"))
    print(f"ICU 文案分支预览器启动: http://0.0.0.0:{port}")
    serve(application, host="0.0.0.0", port=port, threads=8)
