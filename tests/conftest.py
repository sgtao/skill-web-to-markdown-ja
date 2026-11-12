"""テストの共通設定。url-to-md/scripts を import できるようにし、手元だけで動く HTTP サーバーを用意する。"""
import http.server
import os
import sys
import threading

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "url-to-md", "scripts")
FIXTURES = os.path.join(ROOT, "tests", "fixtures")
sys.path.insert(0, SCRIPTS)


MINIMAL_PDF = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
               b"2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj\n"
               b"trailer<</Root 1 0 R>>\n%%EOF\n")
LOGIN_HTML = ('<html><head><meta charset="utf-8"><title>ログイン</title></head><body>'
              '<h1>会員ログイン</h1><form><input type="text" name="id">'
              '<input type="password" name="pw"><button>送信</button></form></body></html>')
SPA_HTML = ('<html><head><meta charset="utf-8"><title>App</title>'
            '<script src="/static/app.js"></script></head><body>'
            '<noscript>You need to enable JavaScript to run this app.</noscript>'
            '<div id="root"></div></body></html>')


class _Handler(http.server.BaseHTTPRequestHandler):
    """決まったパスだけ返し、ほかは 404 にする。

    /sjis・/sjis-header・/emoji・/empty・/redirect・/files/report.pdf・
    /pdf-no-ext（Content-Type が octet-stream の PDF）・/login・/spa
    """

    def log_message(self, *args):  # テストの出力を汚さない
        pass

    def _send(self, status, body=b"", content_type="text/html"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _fixture(self, name):
        with open(os.path.join(FIXTURES, name), "rb") as f:
            return f.read()

    def do_GET(self):
        if self.path == "/sjis":
            self._send(200, self._fixture("sjis.html"))
        elif self.path == "/sjis-header":
            self._send(200, self._fixture("sjis_no_meta.html"),
                       "text/html; charset=Shift_JIS")
        elif self.path == "/emoji":
            self._send(200, self._fixture("utf8_emoji.html"),
                       "text/html; charset=utf-8")
        elif self.path == "/files/report.pdf":
            self._send(200, MINIMAL_PDF, "application/pdf")
        elif self.path == "/pdf-no-ext":
            self._send(200, MINIMAL_PDF, "application/octet-stream")
        elif self.path == "/login":
            self._send(200, LOGIN_HTML.encode("utf-8"), "text/html; charset=utf-8")
        elif self.path == "/spa":
            self._send(200, SPA_HTML.encode("utf-8"), "text/html; charset=utf-8")
        elif self.path == "/empty":
            self._send(200, b"")
        elif self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/emoji")
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self._send(404, b"<html><body><h1>Not Found</h1></body></html>")

    def do_HEAD(self):  # HEAD を拒むサーバーを模す
        self._send(405)


@pytest.fixture(scope="session")
def server():
    """127.0.0.1 の空きポートで HTTP サーバーを立て、基点の URL を返す。"""
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


@pytest.fixture(scope="session")
def scripts_dir():
    return SCRIPTS


@pytest.fixture(scope="session")
def fixtures_dir():
    return FIXTURES
