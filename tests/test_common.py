"""_common.py のテスト（外部への通信なし。HTTP は conftest の手元サーバーだけ）。"""
import os
import subprocess
import sys

import pytest

import _common
from _common import StopReason, check_scheme, decode_html, fetch


# ---- 文字コード -------------------------------------------------------------
def test_decode_sjis_by_meta(fixtures_dir):
    data = open(os.path.join(fixtures_dir, "sjis.html"), "rb").read()
    assert "文字コードの確認用の記事" in decode_html(data)


def test_decode_sjis_by_header(fixtures_dir):
    data = open(os.path.join(fixtures_dir, "sjis_no_meta.html"), "rb").read()
    assert "文字コードの確認用の記事" in decode_html(data, "text/html; charset=Shift_JIS")


def test_decode_sjis_without_any_hint(fixtures_dir):
    data = open(os.path.join(fixtures_dir, "sjis_no_meta.html"), "rb").read()
    assert "文字コードの確認用の記事" in decode_html(data)


def test_decode_utf8_emoji(fixtures_dir):
    data = open(os.path.join(fixtures_dir, "utf8_emoji.html"), "rb").read()
    text = decode_html(data)
    assert "😀🚀" in text and "✅" in text


def test_decode_bom():
    assert decode_html(b"\xef\xbb\xbf<p>\xe3\x81\x82</p>") == "<p>あ</p>"


def test_decode_wrong_declared_charset_falls_back():
    # 宣言は Shift_JIS だが実際は UTF-8 のページ
    data = '<meta charset="Shift_JIS"><p>日本語の本文😀</p>'.encode("utf-8")
    assert "日本語の本文😀" in decode_html(data)


# ---- スキーム ---------------------------------------------------------------
@pytest.mark.parametrize("url", ["http://example.com/", "https://example.com/a?b=1"])
def test_scheme_ok(url):
    check_scheme(url)


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com/a",
                                 "example.com/page", "C:\\Users\\a\\b.html"])
def test_scheme_ng(url):
    with pytest.raises(StopReason) as e:
        check_scheme(url)
    assert e.value.code == "UNSUPPORTED_SCHEME"
    assert e.value.exit_code == 3


# ---- 取得 -------------------------------------------------------------------
def test_fetch_ok_and_content_type(server, tmp_path):
    dest = str(tmp_path / "a.html")
    ct = fetch(server + "/sjis-header", dest)
    assert "Shift_JIS" in ct
    assert os.path.getsize(dest) > 0


def test_fetch_follows_redirect_and_takes_last_content_type(server, tmp_path):
    ct = fetch(server + "/redirect", str(tmp_path / "a.html"))
    assert "utf-8" in ct


def test_fetch_404_is_http_error(server, tmp_path):
    with pytest.raises(StopReason) as e:
        fetch(server + "/no-such-page", str(tmp_path / "a.html"))
    assert e.value.code == "HTTP_ERROR"
    assert "404" in e.value.message


def test_fetch_empty_is_empty_content(server, tmp_path):
    with pytest.raises(StopReason) as e:
        fetch(server + "/empty", str(tmp_path / "a.html"))
    assert e.value.code == "EMPTY_CONTENT"


def test_fetch_unreachable_is_no_network(tmp_path):
    # 127.0.0.1 の閉じたポート（外部には出ない）
    with pytest.raises(StopReason) as e:
        fetch("http://127.0.0.1:9/", str(tmp_path / "a.html"), connect_timeout=3)
    assert e.value.code == "NO_NETWORK"


def test_fetch_without_curl_is_missing_tool(tmp_path, monkeypatch):
    def no_curl(*args, **kwargs):
        raise FileNotFoundError("curl")
    monkeypatch.setattr(_common.subprocess, "run", no_curl)
    with pytest.raises(StopReason) as e:
        fetch("http://127.0.0.1:9/", str(tmp_path / "a.html"))
    assert e.value.code == "MISSING_TOOL"
    assert e.value.exit_code == 4


# ---- 移動 -------------------------------------------------------------------
def test_move_creates_parent_and_overwrites(tmp_path):
    src = tmp_path / "s.txt"
    src.write_text("new", encoding="utf-8")
    dst = tmp_path / "sub" / "d.txt"
    dst.parent.mkdir()
    dst.write_text("old", encoding="utf-8")
    _common.move(str(src), str(dst))
    assert dst.read_text(encoding="utf-8") == "new"
    assert not src.exists()


# ---- 失敗の伝え方 -----------------------------------------------------------
def _run_snippet(code, scripts_dir, encoding_env=None):
    env = dict(os.environ)
    env.pop("URL_TO_MD_DEBUG", None)
    if encoding_env:
        env["PYTHONIOENCODING"] = encoding_env
    else:
        env.pop("PYTHONIOENCODING", None)
    return subprocess.run([sys.executable, "-c", code], cwd=scripts_dir,
                          capture_output=True, env=env)


def test_run_main_stop_reason_exit_code_and_format(scripts_dir):
    code = ("from _common import run_main, StopReason\n"
            "def m(): raise StopReason('HTTP_ERROR', '理由の文')\n"
            "run_main(m)\n")
    r = _run_snippet(code, scripts_dir)
    err = r.stderr.decode("utf-8")
    assert r.returncode == 3
    assert err.strip().splitlines()[-1] == "REASON=HTTP_ERROR"
    assert "理由の文" in err and "Traceback" not in err


def test_run_main_unexpected_error_has_no_traceback(scripts_dir):
    code = ("from _common import run_main\n"
            "def m(): raise ValueError('x')\n"
            "run_main(m)\n")
    r = _run_snippet(code, scripts_dir)
    err = r.stderr.decode("utf-8")
    assert r.returncode == 1
    assert "REASON=UNEXPECTED" in err and "Traceback" not in err


def test_stdout_is_utf8_even_when_console_is_cp932(scripts_dir):
    # Windows の既定（cp932）を模す。絵文字を出しても落ちず、UTF-8 で出る
    code = ("from _common import run_main, write_stdout\n"
            "run_main(lambda: write_stdout('日本語😀✅'))\n")
    r = _run_snippet(code, scripts_dir, encoding_env="cp932")
    assert r.returncode == 0
    assert r.stdout.decode("utf-8") == "日本語😀✅"
