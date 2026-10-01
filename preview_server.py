# 本地静态预览：复刻 Flask 的 /assets/<name> -> web/<name> 映射
import http.server
import os
import sys

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")


class Handler(http.server.SimpleHTTPRequestHandler):
    def translate_path(self, path):
        if path.startswith("/assets/"):
            return os.path.join(WEB, os.path.relpath(path, "/assets"))
        return os.path.join(WEB, path.lstrip("/"))


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8123
    http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
