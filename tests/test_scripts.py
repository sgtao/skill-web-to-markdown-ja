"""スクリプトを実際に動かすテスト（外部への通信なし）。"""
import os
import subprocess
import sys

import pytest

from fetch_convert import detect_format
from html_to_md import absolutize


def _run(scripts_dir, script, *args, stdin=None, console="cp932"):
    """スクリプトを別プロセスで動かす。標準出力の既定を cp932 にして Windows を模す。"""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = console
    env.pop("URL_TO_MD_DEBUG", None)
    return subprocess.run([sys.executable, os.path.join(scripts_dir, script), *args],
                          capture_output=True, input=stdin, env=env)


# ---- 形式判定 ---------------------------------------------------------------
@pytest.mark.parametrize("url, ct, expected", [
    ("https://e.com/a.pdf", "", "pdf"),
    ("https://e.com/a.PDF?x=1", "", "pdf"),
    ("https://e.com/a.docx", "", "docx"),
    ("https://e.com/a.xlsx", "", "xlsx"),
    ("https://e.com/a.pptx", "", "pptx"),
    ("https://e.com/a.doc", "", "doc_legacy"),
    ("https://e.com/a.xls", "", "xls_legacy"),
    ("https://e.com/a.ppt", "", "ppt_legacy"),
    ("https://e.com/dl?id=1", "application/pdf", "pdf"),
    ("https://e.com/dl?id=1",
     "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"),
    ("https://e.com/dl?id=1",
     "application/vnd.openxmlformats-officedocument.presentationml.presentation", "pptx"),
    ("https://e.com/page", "text/html; charset=utf-8", "html"),
    ("https://e.com/page", "", "html"),
])
def test_detect_format(url, ct, expected):
    assert detect_format(url, ct) == expected


# ---- 絶対 URL 化 ------------------------------------------------------------
def test_absolutize():
    html = ('<img src="img/a.png"><img src="/b.png"><img src="//cdn.e.com/c.png">'
            '<img src="data:image/png;base64,AAAA"><a href="#top">x</a>')
    out = absolutize(html, "https://e.com/dir/page.html")
    assert "https://e.com/dir/img/a.png" in out
    assert "https://e.com/b.png" in out
    assert "https://cdn.e.com/c.png" in out
    assert "data:image/png;base64,AAAA" in out
    assert 'href="#top"' in out


# ---- html_to_md.py ----------------------------------------------------------
@pytest.mark.parametrize("path", ["/sjis", "/sjis-header", "/emoji"])
def test_html_to_md_url(server, scripts_dir, path):
    r = _run(scripts_dir, "html_to_md.py", server + path)
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    md = r.stdout.decode("utf-8")
    assert "文字コードの確認用の記事" in md
    assert "二つ目の段落です" in md
    assert "\ufffd" not in md  # 文字化けの置き換え文字が無い


def test_html_to_md_emoji_survives_cp932_console(server, scripts_dir):
    r = _run(scripts_dir, "html_to_md.py", server + "/emoji")
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    assert "😀🚀" in r.stdout.decode("utf-8")


def test_html_to_md_stdin_sjis(scripts_dir, fixtures_dir):
    data = open(os.path.join(fixtures_dir, "sjis.html"), "rb").read()
    r = _run(scripts_dir, "html_to_md.py", "https://e.com/dir/page.html", "--stdin",
             stdin=data)
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    assert "文字コードの確認用の記事" in r.stdout.decode("utf-8")


def test_html_to_md_output_file_is_utf8(server, scripts_dir, tmp_path):
    out = tmp_path / "out.md"
    r = _run(scripts_dir, "html_to_md.py", server + "/emoji", "-o", str(out))
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    assert "😀🚀" in out.read_text(encoding="utf-8")


# ---- fetch_convert.py -------------------------------------------------------
def test_fetch_convert_html(server, scripts_dir):
    r = _run(scripts_dir, "fetch_convert.py", server + "/sjis")
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    assert "文字コードの確認用の記事" in r.stdout.decode("utf-8")


def test_fetch_convert_404(server, scripts_dir):
    r = _run(scripts_dir, "fetch_convert.py", server + "/no-such-page")
    err = r.stderr.decode("utf-8")
    assert r.returncode == 3
    assert err.strip().splitlines()[-1] == "REASON=HTTP_ERROR"
    assert "Traceback" not in err
    assert r.stdout == b""  # エラーページを Markdown にしない


def test_fetch_convert_file_scheme_is_refused(scripts_dir, fixtures_dir):
    url = "file://" + os.path.join(fixtures_dir, "sjis.html")
    r = _run(scripts_dir, "fetch_convert.py", url)
    assert r.returncode == 3
    assert r.stderr.decode("utf-8").strip().splitlines()[-1] == "REASON=UNSUPPORTED_SCHEME"


def test_fetch_convert_youtube_is_guided(scripts_dir):
    r = _run(scripts_dir, "fetch_convert.py", "https://www.youtube.com/watch?v=abcdefghijk")
    assert r.returncode == 0
    assert "youtube_to_md.py" in r.stderr.decode("utf-8")


# ---- add_frontmatter.py -----------------------------------------------------
def test_add_frontmatter_stdout_utf8(scripts_dir, tmp_path):
    body = tmp_path / "body.md"
    body.write_text("# 題名😀\n\n本文の段落です。\n", encoding="utf-8")
    r = _run(scripts_dir, "add_frontmatter.py", str(body),
             "--url", "https://e.com/a", "--format", "html")
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    out = r.stdout.decode("utf-8")
    assert out.startswith("---\n") and "title: 題名😀" in out
