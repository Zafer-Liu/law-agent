# 本地静态预览：复刻 Flask 的 /assets/<name> -> web/<name> 映射
import http.server
import os
import sys

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")


class Handler(http.server.SimpleHTTPRequestHandler):
    # Windows 注册表可能把 .js 注册成 text/plain，模块脚本会被浏览器 MIME 检查拒绝
    extensions_map = {
        **http.server.SimpleHTTPRequestHandler.extensions_map,
        ".js": "text/javascript",
        ".mjs": "text/javascript",
    }

    def translate_path(self, path):
        path = path.split('?', 1)[0].split('#', 1)[0]  # 基类同款：剥掉查询串/锚点
        if path.startswith("/assets/"):
            return os.path.join(WEB, os.path.relpath(path, "/assets"))
        return os.path.join(WEB, path.lstrip("/"))


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8123
    http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
