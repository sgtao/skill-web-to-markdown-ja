#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "trafilatura",
#     "beautifulsoup4",
#     "markitdown[docx]>=0.1.5",
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
"""
import argparse
import subprocess
import sys
from urllib.parse import urljoin


def fetch_html(url):
    r = subprocess.run(
        ["curl", "-sL", "-A", "Mozilla/5.0", url],
        capture_output=True, text=True, timeout=120)
    if r.returncode != 0 or not r.stdout:
        raise RuntimeError(f"curl失敗または空: {r.stderr[:200]}")
    return r.stdout


def absolutize(html, base_url):
    """img/src, a/href などの相対URLを絶対URLに変換。
    注: trafilaturaは完全HTML文書で本文判定が働くため、
    <main>等での切り出しはせず文書全体を返す。"""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    for tag, attr in [("img", "src"), ("img", "data-src"),
                      ("a", "href"), ("source", "srcset")]:
        for el in soup.find_all(tag):
            val = el.get(attr)
            if val and not val.startswith(("data:", "javascript:", "#")):
                el[attr] = urljoin(base_url, val)
    return str(soup)


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
        import tempfile, os
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
    # 連続する重複ブロック（同一段落・表行）を除去
    if md:
        seen, out_lines = set(), []
        for block in md.split("\n\n"):
            key = block.strip()
            if key and len(key) > 30 and key in seen:
                continue  # 長い段落の重複のみ除去（短い見出し等は残す）
            seen.add(key)
            out_lines.append(block)
        md = "\n\n".join(out_lines)
    return md


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("-o", "--output")
    ap.add_argument("--stdin", action="store_true",
                    help="HTMLを標準入力から受け取る（curl省略）")
    args = ap.parse_args()

    html = sys.stdin.read() if args.stdin else fetch_html(args.url)
    print(f"[1] 画像src等を絶対URL化（base={args.url}）", file=sys.stderr)
    html = absolutize(html, args.url)
    print("[2] 本文抽出 + Markdown化", file=sys.stderr)
    md = to_markdown(html, args.url)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(md)
        print(f"[3] 出力: {args.output}", file=sys.stderr)
    else:
        print(md)


if __name__ == "__main__":
    main()
