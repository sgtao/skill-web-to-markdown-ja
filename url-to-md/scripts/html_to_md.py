#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "trafilatura",
#     "beautifulsoup4",
#     "markitdown[docx,xlsx,pptx]>=0.1.5",
# ]
# ///
"""
html_to_md.py — HTMLを本文抽出し、画像srcを絶対URL化してMarkdownに変換する。

方針（url-to-mdのHTML経路）:
  1. 画像src等を絶対URL化（相対/ルート相対/プロトコル相対の全パターン）
  2. trafilaturaで本文抽出（ナビ・広告・関連記事・フッターを除去）
  3. 画像は ![alt](絶対URL) として参照（PDF変換は不要）

trafilaturaが本文を取りこぼす場合に備え、markitdownフォールバックも持つ。

使い方:
  python3 html_to_md.py <URL> [-o out.md]
  echo "<html>..." | python3 html_to_md.py <URL> --stdin -o out.md
  python3 html_to_md.py --help     # 入力・出力・終了コード・理由コード
"""
import argparse
import os
import re
import shutil
import sys
import tempfile
from urllib.parse import urljoin

from _common import (FORCEABLE, StopReason, contract_epilog, decode_html, fetch,
                     read_html_file, run_main, write_stdout)

_SKIP_URL_PREFIXES = ("data:", "javascript:", "#")


def fetch_html(url):
    """URLのHTMLを取得し、文字コードを判定して文字列で返す。

    curl の出力はバイト列で受ける（Windows の既定 cp932 で読んで壊さないため）。
    失敗は _common.StopReason（理由コードつき）で伝わる。
    """
    tmpdir = tempfile.mkdtemp(prefix="url2md_")
    try:
        raw = os.path.join(tmpdir, "page.html")
        content_type = fetch(url, raw)
        return read_html_file(raw, content_type)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def absolutize_srcset(value, base_url):
    """srcset（「URL 記述子, URL 記述子, …」）の URL を 1 つずつ絶対 URL にする。

    URL の中のカンマ（例: w_300,h_200/a.jpg）は区切りとみなさない。
    区切りは「空白の後のカンマ」か「URL の末尾のカンマ」。
    """
    out, pos, n = [], 0, len(value)
    while pos < n:
        while pos < n and (value[pos].isspace() or value[pos] == ","):
            pos += 1
        if pos >= n:
            break
        start = pos
        while pos < n and not value[pos].isspace():
            pos += 1
        url, descriptor = value[start:pos], ""
        if url.endswith(",") and not url.startswith("data:"):
            url = url.rstrip(",")
        else:
            start = pos
            while pos < n and value[pos] != ",":
                pos += 1
            descriptor = value[start:pos].strip()
        if url and not url.startswith(_SKIP_URL_PREFIXES):
            url = urljoin(base_url, url)
        out.append(f"{url} {descriptor}".strip())
    return ", ".join(out)


def absolutize(html, base_url):
    """img/src, a/href などの相対URLを絶対URLに変換。

    対象: img[src]・img[data-src]・a[href]、および img / source の
    srcset・data-srcset（複数指定を 1 つずつ絶対化する）。
    注: trafilaturaは完全HTML文書で本文判定が働くため、
    <main>等での切り出しはせず文書全体を返す。"""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    for tag, attr in [("img", "src"), ("img", "data-src"), ("a", "href")]:
        for el in soup.find_all(tag):
            val = el.get(attr)
            if val and not val.startswith(_SKIP_URL_PREFIXES):
                el[attr] = urljoin(base_url, val)
    for el in soup.find_all(["img", "source"]):
        for attr in ("srcset", "data-srcset"):
            val = el.get(attr)
            if val:
                el[attr] = absolutize_srcset(val, base_url)
    return str(soup)


def drop_consecutive_duplicates(md, min_len=30):
    """重複して出力されたブロックを除く（離れた所で意図して繰り返された文は残す）。

    除くもの:
      1. 同じ長いブロックが続けて出たもの（1 つにする）
      2. 2 ブロック以上の並びが、そっくりそのまま後ろで繰り返されたもの
         （trafilatura が本文を丸ごと 2 回出すことがある）
    残すもの: 離れた場所に 1 つだけ繰り返される段落（引用・定型文）と、
    min_len 字以下の短いブロック（見出しなど）。
    """
    blocks = md.split("\n\n")
    keys = [b.strip() for b in blocks]
    out, out_keys, i = [], [], 0
    while i < len(blocks):
        key = keys[i]
        # 2. 直前までに出した並びの繰り返しか（長い方から探す）
        skip = 0
        for k in range(min(len(blocks) - i, len(out_keys)), 1, -1):
            run = keys[i:i + k]
            if not any(len(x) > min_len for x in run):
                continue
            if any(out_keys[j:j + k] == run for j in range(len(out_keys) - k + 1)):
                skip = k
                break
        if skip:
            i += skip
            continue
        # 1. 同じ長いブロックが続けて出たもの
        if key and len(key) > min_len and out_keys and key == out_keys[-1]:
            i += 1
            continue
        out.append(blocks[i])
        if key:
            out_keys.append(key)
        i += 1
    return "\n\n".join(out)


_APP_ROOT_IDS = ("root", "app", "__next", "__nuxt", "___gatsby", "svelte")
_JS_NOTICE_RE = re.compile(
    r"enable\s+javascript|javascript\s+(is\s+)?(required|disabled)|requires\s+javascript"
    r"|JavaScript\s*を?\s*(有効|オン)", re.IGNORECASE)
_LOGIN_TITLE_RE = re.compile(
    r"log\s*in|sign\s*in|ログイン|サインイン|会員登録|認証が必要", re.IGNORECASE)
JS_TEXT_MAX = 200      # 見える文字がこれ未満なら「本文がほとんど無い」とみなす
LOGIN_TEXT_MAX = 1500  # 見える文字がこれ未満で、パスワード欄があればログイン画面とみなす


def diagnose(html):
    """ログイン画面らしい・JS 描画らしい、を推定する。

    推定なので、確かな手がかりがそろったときだけ理由コードを返す（迷ったら None）。

    Returns:
        (理由コード, 理由の文) か、問題が無ければ None。
        理由コードは "LOGIN_REQUIRED" か "JS_RENDERED"。
    """
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    has_password = soup.find("input", attrs={"type": re.compile("^password$", re.I)}) is not None
    noscript_text = " ".join(t.get_text(" ", strip=True) for t in soup.find_all("noscript"))
    empty_app_root = any(
        (el := soup.find(id=i)) is not None and len(el.get_text(strip=True)) < 20
        for i in _APP_ROOT_IDS)
    has_script = soup.find("script") is not None
    for el in soup(["script", "style", "noscript", "template", "svg"]):
        el.decompose()
    body = soup.body or soup
    visible = len(re.sub(r"\s+", "", body.get_text(" ", strip=True)))

    if has_password and visible < LOGIN_TEXT_MAX:
        return ("LOGIN_REQUIRED",
                f"ログイン画面らしいページです（パスワードの入力欄があり、本文が {visible} 字）。")
    if _LOGIN_TITLE_RE.search(title) and visible < JS_TEXT_MAX:
        return ("LOGIN_REQUIRED",
                f"ログイン画面らしいページです（題名が「{title[:40]}」で、本文が {visible} 字）。")
    if visible < JS_TEXT_MAX and has_script and (
            empty_app_root or _JS_NOTICE_RE.search(noscript_text)):
        return ("JS_RENDERED",
                f"本文がほとんど取れません（{visible} 字）。"
                "JavaScript で描画するページらしいです。")
    return None


def check_html(html, force=False):
    """diagnose の結果が出たら止める。force のときは警告だけ出して続ける。

    Raises:
        StopReason: LOGIN_REQUIRED / JS_RENDERED（force でないとき）。
    """
    found = diagnose(html)
    if found is None:
        return
    code, message = found
    assert code in FORCEABLE
    if force:
        print(f"[warn] {message}（--force により続行。REASON={code} 相当）", file=sys.stderr)
        return
    raise StopReason(code, message)


def to_markdown(html, base_url):
    """本文抽出してMarkdown化。失敗時はmarkitdownにフォールバック。"""
    import trafilatura
    md = trafilatura.extract(
        html, output_format="markdown",
        include_images=True, include_tables=True,
        include_links=True, url=base_url,
        deduplicate=True, favor_precision=True)
    # 本文が極端に短く、見出しも画像も無い場合のみフォールバック
    if not md or (len(md.strip()) < 40 and "#" not in (md or "")
                  and "![" not in (md or "")):
        from markitdown import MarkItDown
        with tempfile.NamedTemporaryFile("w", suffix=".html",
                                         delete=False, encoding="utf-8") as f:
            f.write(html)
            tmp = f.name
        try:
            md = MarkItDown().convert(tmp).text_content
        finally:
            os.unlink(tmp)
        print("[info] trafilatura不発→markitdownにフォールバック（ノイズ混入の可能性）",
              file=sys.stderr)
    # 連続する長い重複ブロック（同一段落・表行）だけを除去
    return drop_consecutive_duplicates(md) if md else md


REASON_CODES = ("NO_NETWORK", "UNSUPPORTED_SCHEME", "HTTP_ERROR", "LOGIN_REQUIRED",
                "JS_RENDERED", "EMPTY_CONTENT", "FETCH_FAILED", "MISSING_TOOL")


def build_parser():
    ap = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "HTML を本文抽出して Markdown にする（画像・リンクは絶対 URL にする）。\n"
            "\n"
            "入力:\n"
            "  <URL>            http / https のページを curl で取得する\n"
            "  <URL> --stdin    HTML を標準入力から受け取る（URL は相対パスの基点に使う）\n"
            "出力:\n"
            "  -o <file>        Markdown を書くファイル（UTF-8）。省略時は標準出力"),
        epilog=contract_epilog(
            REASON_CODES, "Markdown の本文（-o を付けたときは何も出さない）",
            notes=["補足:",
                   "  PDF・Office・手元のファイルは fetch_convert.py を使う。"]))
    ap.add_argument("url", help="ページの URL（--stdin のときは相対パスの基点）")
    ap.add_argument("-o", "--output", help="Markdown の出力ファイル（省略時は標準出力）")
    ap.add_argument("--stdin", action="store_true",
                    help="HTML を標準入力から受け取る（取得しない）")
    ap.add_argument("--force", action="store_true",
                    help="LOGIN_REQUIRED・JS_RENDERED（推定）で止めずに続行する")
    return ap


def main():
    args = build_parser().parse_args()

    # 標準入力もバイト列で受けて、文字コードを判定する
    html = (decode_html(sys.stdin.buffer.read()) if args.stdin
            else fetch_html(args.url))
    if not html.strip():
        raise StopReason("EMPTY_CONTENT", "HTML が空でした。")
    check_html(html, force=args.force)
    print(f"[1] 画像src等を絶対URL化（base={args.url}）", file=sys.stderr)
    html = absolutize(html, args.url)
    print("[2] 本文抽出 + Markdown化", file=sys.stderr)
    md = to_markdown(html, args.url)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(md)
        print(f"[3] 出力: {args.output}", file=sys.stderr)
    else:
        write_stdout(md + "\n")


if __name__ == "__main__":
    run_main(main)
