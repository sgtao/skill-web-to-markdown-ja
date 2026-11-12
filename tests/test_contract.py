"""P2（入力と契約）のテスト: --file・形式判定・--outdir・--check-env・--help・推定での停止。

外部への通信なし。テスト用のファイルは、その場で作る（小さな pdf・docx・zip）。
"""
import json
import os
import subprocess
import sys
import zipfile

import pytest

import _common
from _common import safe_stem, sniff_format, unique_path
from fetch_convert import decide_format
from html_to_md import absolutize, absolutize_srcset, diagnose, drop_consecutive_duplicates

MINIMAL_PDF = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
               b"2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj\n"
               b"trailer<</Root 1 0 R>>\n%%EOF\n")
OLE_HEAD = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64

_DOCX_TYPES = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-'
    'officedocument.wordprocessingml.document.main+xml"/></Types>')
_DOCX_RELS = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
    'relationships/officeDocument" Target="word/document.xml"/></Relationships>')
_DOCX_BODY = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
    '<w:body><w:p><w:r><w:t>手元の文書の段落です😀</w:t></w:r></w:p></w:body></w:document>')


def make_docx(path):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", _DOCX_TYPES)
        z.writestr("_rels/.rels", _DOCX_RELS)
        z.writestr("word/document.xml", _DOCX_BODY)
    return str(path)


def make_zip(path, member):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(member, "x")
    return str(path)


def _run(scripts_dir, script, *args, stdin=None, cwd=None):
    """スクリプトを別プロセスで動かす。標準出力の既定を cp932 にして Windows を模す。"""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "cp932"
    env.pop("URL_TO_MD_DEBUG", None)
    env.pop("SOFFICE_PATH", None)
    return subprocess.run([sys.executable, os.path.join(scripts_dir, script), *args],
                          capture_output=True, input=stdin, env=env, cwd=cwd)


def _last(r):
    return r.stderr.decode("utf-8").strip().splitlines()[-1]


# ---- 先頭のバイトでの確認 ---------------------------------------------------
def test_sniff_format(tmp_path, fixtures_dir):
    pdf = tmp_path / "a.bin"
    pdf.write_bytes(MINIMAL_PDF)
    ole = tmp_path / "b.bin"
    ole.write_bytes(OLE_HEAD)
    txt = tmp_path / "c.bin"
    txt.write_text("ただの文章", encoding="utf-8")
    assert sniff_format(str(pdf)) == "pdf"
    assert sniff_format(str(ole)) == "ole"
    assert sniff_format(str(txt)) is None
    assert sniff_format(make_docx(tmp_path / "d.bin")) == "docx"
    assert sniff_format(make_zip(tmp_path / "e.bin", "xl/workbook.xml")) == "xlsx"
    assert sniff_format(make_zip(tmp_path / "f.bin", "ppt/presentation.xml")) == "pptx"
    assert sniff_format(make_zip(tmp_path / "g.bin", "other/x.txt")) is None
    assert sniff_format(os.path.join(fixtures_dir, "sjis.html")) == "html"
    assert sniff_format(str(tmp_path / "no-such-file")) is None


# ---- 形式判定の表（名前 × Content-Type × 先頭のバイト）----------------------
XLSX_CT = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@pytest.mark.parametrize("name, ct, sniffed, default, expected", [
    # 拡張子だけで決まる
    ("https://e.com/a.pdf", "", None, "html", "pdf"),
    ("a.docx", "", "docx", None, "docx"),
    ("a.htm", "", "html", None, "html"),
    # 拡張子が無い・合わない → Content-Type
    ("https://e.com/dl?id=1", "application/pdf", None, "html", "pdf"),
    ("https://e.com/dl?id=1", XLSX_CT, "xlsx", "html", "xlsx"),
    ("https://e.com/dl", "application/msword", None, "html", "doc_legacy"),
    # 先頭のバイトが、拡張子や Content-Type より優先される
    ("https://e.com/page.html", "text/html", "pdf", "html", "pdf"),
    ("https://e.com/dl", "application/octet-stream", "pptx", "html", "pptx"),
    ("report.docx", "", "xlsx", None, "xlsx"),
    ("report.pdf", "", "docx", None, "docx"),
    # 旧 Office の入れ物
    ("old.xls", "", "ole", None, "xls_legacy"),
    ("old.docx", "", "ole", None, "office_legacy"),
    ("noext", "", "ole", None, "office_legacy"),
    # 拡張子が無い手元のファイル
    ("noext", "", "html", None, "html"),
    ("noext", "", "pdf", None, "pdf"),
    ("notes.txt", "", None, None, None),
    # URL は、何も分からなければ Web ページとして扱う
    ("https://e.com/page", "", None, "html", "html"),
    ("https://e.com/page", "text/html; charset=utf-8", "html", "html", "html"),
])
def test_decide_format(name, ct, sniffed, default, expected):
    assert decide_format(name, ct, sniffed, default=default) == expected


# ---- 出力名 -----------------------------------------------------------------
@pytest.mark.parametrize("source, expected", [
    ("https://e.com/dir/report.pdf?x=1", "report"),
    ("https://e.com/dir/%E8%B3%87%E6%96%99.pptx", "資料"),
    ("https://e.com/dl", "dl"),
    ("https://e.com/", "e.com"),
    ("/tmp/dir/slides.v2.pptx", "slides.v2"),
    ("C:\\Users\\a\\資料 1.xlsx", "資料 1"),
    ("https://e.com/a:b*c.pdf", "a_b_c"),
])
def test_safe_stem(source, expected):
    assert safe_stem(source) == expected


def test_unique_path_numbers_same_names(tmp_path):
    d = str(tmp_path / "out")
    first = unique_path(d, "a.pdf")
    assert first == os.path.join(d, "a.pdf")
    open(first, "w").close()
    second = unique_path(d, "a.pdf")
    assert second == os.path.join(d, "a-2.pdf")
    open(second, "w").close()
    assert unique_path(d, "a.pdf") == os.path.join(d, "a-3.pdf")


# ---- --help（契約書）--------------------------------------------------------
COMMON_WORDS = ["入力", "出力", "-o", "標準出力", "標準エラー", "終了コード", "UTF-8"]


@pytest.mark.parametrize("script, words", [
    ("fetch_convert.py", ["--file", "--outdir", "--check-env", "--pdf-passthrough",
                          "--force", "理由コード", "REASON=", "URL_TO_MD_DEBUG",
                          *_common.REASON_MEANINGS]),
    ("html_to_md.py", ["--stdin", "--force", "理由コード", "REASON=", "NO_NETWORK",
                       "HTTP_ERROR", "LOGIN_REQUIRED", "JS_RENDERED", "MISSING_TOOL"]),
    ("youtube_to_md.py", ["--timeout", "yt-dlp"]),
    ("add_frontmatter.py", ["--url", "--format", "理由コード", "EMPTY_CONTENT"]),
])
def test_help_is_the_contract(scripts_dir, script, words):
    r = _run(scripts_dir, script, "--help")
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    text = r.stdout.decode("utf-8")
    for word in COMMON_WORDS + list(words):
        assert word in text, f"{script} --help に「{word}」が無い"
    for code in (0, 1, 2):
        assert f"\n  {code}  " in text


def test_every_reason_code_has_meaning_and_next_step():
    assert set(_common.REASON_MEANINGS) == set(_common.NEXT_STEPS)
    assert set(_common.FORCEABLE) <= set(_common.REASON_MEANINGS)


# ---- 引数の誤り -------------------------------------------------------------
def test_needs_url_or_file(scripts_dir, tmp_path):
    assert _run(scripts_dir, "fetch_convert.py").returncode == 2
    f = tmp_path / "a.html"
    f.write_text("<html><body><p>x</p></body></html>", encoding="utf-8")
    r = _run(scripts_dir, "fetch_convert.py", "https://e.com/", "--file", str(f))
    assert r.returncode == 2
    r = _run(scripts_dir, "fetch_convert.py", "--file", str(tmp_path / "no-such.pdf"))
    assert r.returncode == 2
    assert "見つかりません" in r.stderr.decode("utf-8")


# ---- --check-env ------------------------------------------------------------
def test_check_env_json_and_lines(scripts_dir, monkeypatch):
    r = _run(scripts_dir, "fetch_convert.py", "--check-env")
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    env = json.loads(r.stdout.decode("utf-8"))
    assert env["python_ok"] is True and env["network"] == "not_checked"
    for tool in ("curl", "soffice", "uv", "pdfinfo", "pdftotext", "pdftoppm"):
        assert tool in env["tools"]
    assert "cairosvg" in env["modules"] and env["modules"]["bs4"] is True
    err = r.stderr.decode("utf-8")
    assert "curl" in err and "soffice" in err and "調べていない" in err


def test_check_env_does_not_run_anything(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("check_env は外部のコマンドを実行しない")
    monkeypatch.setattr(_common.subprocess, "run", boom)
    assert "tools" in _common.check_env()


def test_find_soffice_env_var(tmp_path, monkeypatch):
    fake = tmp_path / "soffice"
    fake.write_text("", encoding="utf-8")
    monkeypatch.setenv("SOFFICE_PATH", str(fake))
    assert _common.find_soffice() == str(fake)


# ---- --file -----------------------------------------------------------------
def test_file_html_sjis(scripts_dir, fixtures_dir):
    r = _run(scripts_dir, "fetch_convert.py", "--file",
             os.path.join(fixtures_dir, "sjis.html"))
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    md = r.stdout.decode("utf-8")
    assert "文字コードの確認用の記事" in md and "\ufffd" not in md


def test_file_html_output_creates_parent(scripts_dir, fixtures_dir, tmp_path):
    out = tmp_path / "new" / "out.md"
    r = _run(scripts_dir, "fetch_convert.py", "--file",
             os.path.join(fixtures_dir, "utf8_emoji.html"), "-o", str(out))
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    assert r.stdout == b""
    assert "😀🚀" in out.read_text(encoding="utf-8")


def test_file_docx(scripts_dir, tmp_path):
    r = _run(scripts_dir, "fetch_convert.py", "--file", make_docx(tmp_path / "a.docx"))
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    assert "手元の文書の段落です😀" in r.stdout.decode("utf-8")


def test_file_docx_with_wrong_extension_follows_content(scripts_dir, tmp_path):
    r = _run(scripts_dir, "fetch_convert.py", "--file", make_docx(tmp_path / "a.pdf"))
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    assert "手元の文書の段落です" in r.stdout.decode("utf-8")
    assert "中身は docx" in r.stderr.decode("utf-8")


def test_file_pdf_passthrough_returns_the_file_itself(scripts_dir, tmp_path):
    pdf = tmp_path / "資料.pdf"
    pdf.write_bytes(MINIMAL_PDF)
    r = _run(scripts_dir, "fetch_convert.py", "--file", str(pdf), "--pdf-passthrough")
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    assert r.stdout.decode("utf-8").strip() == str(pdf)
    assert pdf.read_bytes() == MINIMAL_PDF


def test_file_empty_and_unknown(scripts_dir, tmp_path):
    empty = tmp_path / "a.html"
    empty.write_bytes(b"")
    r = _run(scripts_dir, "fetch_convert.py", "--file", str(empty))
    assert r.returncode == 3 and _last(r) == "REASON=EMPTY_CONTENT"
    unknown = tmp_path / "a.dat"
    unknown.write_bytes(b"\x00\x01\x02\x03")
    r = _run(scripts_dir, "fetch_convert.py", "--file", str(unknown))
    assert r.returncode == 3 and _last(r) == "REASON=UNSUPPORTED_FORMAT"


def test_file_legacy_office_is_stopped(scripts_dir, tmp_path):
    old = tmp_path / "a.xls"
    old.write_bytes(OLE_HEAD)
    r = _run(scripts_dir, "fetch_convert.py", "--file", str(old))
    assert r.returncode == 3
    assert _last(r) in ("REASON=LEGACY_NEEDS_SOFFICE", "REASON=CONVERT_FAILED")


def test_office_passthrough_without_soffice_is_missing_tool(scripts_dir, tmp_path,
                                                             monkeypatch):
    if _common.find_soffice():
        pytest.skip("この環境には soffice がある")
    outdir = tmp_path / "out"
    r = _run(scripts_dir, "fetch_convert.py", "--file", make_docx(tmp_path / "a.docx"),
             "--pdf-passthrough", "--outdir", str(outdir))
    assert r.returncode == 4 and _last(r) == "REASON=MISSING_TOOL"
    assert r.stdout == b"" and not outdir.exists()


# ---- --outdir と同名の回避（URL の PDF）-------------------------------------
def test_pdf_passthrough_twice_does_not_overwrite(server, scripts_dir, tmp_path):
    outdir = tmp_path / "pdfs"
    paths = []
    for _ in range(2):
        r = _run(scripts_dir, "fetch_convert.py", server + "/files/report.pdf",
                 "--pdf-passthrough", "--outdir", str(outdir), cwd=str(tmp_path))
        assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
        paths.append(r.stdout.decode("utf-8").strip())
    assert [os.path.basename(p) for p in paths] == ["report.pdf", "report-2.pdf"]
    assert all(open(p, "rb").read().startswith(b"%PDF") for p in paths)
    assert sorted(os.listdir(tmp_path)) == ["pdfs"]  # 実行した場所には何も置かない


def test_pdf_passthrough_ignores_dash_o_for_pdf(server, scripts_dir, tmp_path):
    outdir = tmp_path / "pdfs"
    r = _run(scripts_dir, "fetch_convert.py", server + "/files/report.pdf",
             "--pdf-passthrough", "--outdir", str(outdir), "-o", str(tmp_path / "x.md"),
             cwd=str(tmp_path))
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    assert r.stdout.decode("utf-8").strip() == str(outdir / "report.pdf")
    assert not (tmp_path / "x.md").exists() and not (tmp_path / "x.md.pdf").exists()


def test_pdf_passthrough_without_outdir_uses_temp_dir(server, scripts_dir, tmp_path):
    r = _run(scripts_dir, "fetch_convert.py", server + "/pdf-no-ext",
             "--pdf-passthrough", cwd=str(tmp_path))
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    path = r.stdout.decode("utf-8").strip()
    try:
        assert os.path.basename(path) == "pdf-no-ext.pdf"  # 中身で pdf と分かる
        assert "一時フォルダ" in r.stderr.decode("utf-8")
        assert os.listdir(tmp_path) == []
    finally:
        os.remove(path)
        os.rmdir(os.path.dirname(path))


# ---- ログイン画面・JS 描画の推定 --------------------------------------------
LOGIN_HTML = ('<html><head><title>ログイン</title></head><body><h1>会員ログイン</h1>'
              '<form><input type="text" name="id"><input type="password" name="pw">'
              '<button>送信</button></form></body></html>')
SPA_HTML = ('<html><head><title>App</title><script src="/static/app.js"></script></head>'
            '<body><noscript>You need to enable JavaScript to run this app.</noscript>'
            '<div id="root"></div></body></html>')
SHORT_HTML = '<html><body><h1>お知らせ</h1><p>本日は休業です。</p></body></html>'
ARTICLE_WITH_LOGIN = ('<html><body><form><input type="password"></form><article>'
                      + "<p>これは十分に長い本文の段落です。</p>" * 120 + '</article></body></html>')


def test_diagnose():
    assert diagnose(LOGIN_HTML)[0] == "LOGIN_REQUIRED"
    assert diagnose(SPA_HTML)[0] == "JS_RENDERED"
    assert diagnose(SHORT_HTML) is None          # 短いだけのページは止めない
    assert diagnose(ARTICLE_WITH_LOGIN) is None  # 本文があれば、ログイン欄があっても止めない


def test_diagnose_passes_fixtures(fixtures_dir):
    for name in ("sjis.html", "sjis_no_meta.html", "utf8_emoji.html"):
        html = _common.read_html_file(os.path.join(fixtures_dir, name))
        assert diagnose(html) is None


@pytest.mark.parametrize("path, code", [("/login", "LOGIN_REQUIRED"), ("/spa", "JS_RENDERED")])
def test_stops_on_login_and_js_pages(server, scripts_dir, path, code):
    for script in ("fetch_convert.py", "html_to_md.py"):
        r = _run(scripts_dir, script, server + path)
        err = r.stderr.decode("utf-8")
        assert r.returncode == 3 and _last(r) == f"REASON={code}", err
        assert "らしい" in err and "--force" in err
        assert r.stdout == b""


@pytest.mark.parametrize("path", ["/login", "/spa"])
def test_force_continues(server, scripts_dir, path):
    for script in ("fetch_convert.py", "html_to_md.py"):
        r = _run(scripts_dir, script, server + path, "--force")
        assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
        assert "--force により続行" in r.stderr.decode("utf-8")


def test_force_does_not_override_other_reasons(server, scripts_dir):
    r = _run(scripts_dir, "fetch_convert.py", server + "/no-such-page", "--force")
    assert r.returncode == 3 and _last(r) == "REASON=HTTP_ERROR"


def test_html_to_md_stdin_empty(scripts_dir):
    r = _run(scripts_dir, "html_to_md.py", "https://e.com/", "--stdin", stdin=b"  \n")
    assert r.returncode == 3 and _last(r) == "REASON=EMPTY_CONTENT"


# ---- srcset の絶対化（解説メモの指摘 4）-------------------------------------
@pytest.mark.parametrize("value, expected", [
    ("a.jpg 1x, b.jpg 2x", "https://e.com/d/a.jpg 1x, https://e.com/d/b.jpg 2x"),
    ("/img/s.png 480w,/img/l.png 800w", "https://e.com/img/s.png 480w, https://e.com/img/l.png 800w"),
    ("//cdn.e.com/a.jpg", "https://cdn.e.com/a.jpg"),
    ("https://x.com/w_300,h_200/a.jpg 1x", "https://x.com/w_300,h_200/a.jpg 1x"),
    ("a.jpg, b.jpg 2x", "https://e.com/d/a.jpg, https://e.com/d/b.jpg 2x"),
    ("data:image/png;base64,AAAA 1x", "data:image/png;base64,AAAA 1x"),
    ("", ""),
])
def test_absolutize_srcset(value, expected):
    assert absolutize_srcset(value, "https://e.com/d/page.html") == expected


def test_absolutize_handles_img_and_source_srcset():
    html = ('<picture><source srcset="a.webp 1x, b.webp 2x">'
            '<img src="a.jpg" srcset="a.jpg 1x, /b.jpg 2x" data-srcset="c.jpg 3x"></picture>')
    out = absolutize(html, "https://e.com/d/")
    assert 'srcset="https://e.com/d/a.webp 1x, https://e.com/d/b.webp 2x"' in out
    assert 'srcset="https://e.com/d/a.jpg 1x, https://e.com/b.jpg 2x"' in out
    assert 'data-srcset="https://e.com/d/c.jpg 3x"' in out
    assert 'src="https://e.com/d/a.jpg"' in out


# ---- 重複除去（解説メモの指摘 5）--------------------------------------------
LONG_A = "これは三十字を超える長さの段落で、重複の確認に使うための文章です。"
LONG_B = "こちらは別の段落で、やはり三十字を超える長さを持たせてある文章です。"


def test_drop_only_consecutive_duplicates():
    assert len(LONG_A) > 30 and len(LONG_B) > 30
    md = "\n\n".join([LONG_A, LONG_A, LONG_B, LONG_A, "## 見出し", "## 見出し"])
    out = drop_consecutive_duplicates(md).split("\n\n")
    # 続けて出た LONG_A は 1 つに。離れた LONG_A と、短い見出しの繰り返しは残す
    assert out == [LONG_A, LONG_B, LONG_A, "## 見出し", "## 見出し"]


def test_drop_repeated_run_of_blocks():
    # 本文が丸ごと 2 回出る（trafilatura で実際に起きる）→ 後ろを除く
    body = [ "# 題名", LONG_A, LONG_B, "![図](https://e.com/a.png)"]
    out = drop_consecutive_duplicates("\n\n".join(body + body)).split("\n\n")
    assert out == body
    # 1 つだけ離れて繰り返される段落は残す
    md = "\n\n".join([LONG_A, "## 中", LONG_B, LONG_A])
    assert drop_consecutive_duplicates(md).split("\n\n") == [LONG_A, "## 中", LONG_B, LONG_A]


def test_html_to_md_does_not_repeat_the_article(scripts_dir, fixtures_dir):
    data = open(os.path.join(fixtures_dir, "utf8_emoji.html"), "rb").read()
    r = _run(scripts_dir, "html_to_md.py", "https://e.com/d/p.html", "--stdin", stdin=data)
    md = r.stdout.decode("utf-8")
    assert md.count("二つ目の段落です") == 1 and md.count("三つ目の段落です") == 1
