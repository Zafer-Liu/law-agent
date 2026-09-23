"""python -m legalweb 启动开发服务器。"""

import os

from dotenv import load_dotenv

from .app import create_app

load_dotenv()
app = create_app()

if __name__ == "__main__":
    # PORT 供部署平台/预览工具注入；LAW_PORT 优先
    app.run(host="127.0.0.1", port=int(os.getenv("LAW_PORT") or os.getenv("PORT") or "5000"),
            threaded=True)
