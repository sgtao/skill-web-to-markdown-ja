#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
add_frontmatter.py — 変換済みMarkdownにYAMLフロントマターを付与する。

ヘッダー項目:
  title         文書タイトル（本文先頭の見出し or 明示指定）
  source_url    変換元URL
  source_format html|pdf|docx|xlsx|pptx
  has_figures   SVG/Mermaid/画像を含むか（本文から自動判定）
  summary       200文字以内の要約（明示指定 or 先頭本文から自動抽出）
  retrieved_at  取得日時（ISO8601, 自動）

自動で確実に埋まるもの: source_url, source_format, has_figures, retrieved_at
内容に依存するもの: title, summary
  → CLIで --title / --summary を渡せばそれを使う。
    省略時は本文から機械抽出（titleは先頭見出し、summaryは先頭段落200字）。
    より良い要約・タイトルが必要なら、呼び出し側(Claude)が本文を読んで指定する。

使い方:
  python3 add_frontmatter.py body.md --url <URL> --format pdf [-o out.md]
  python3 add_frontmatter.py body.md --url <URL> --format html \
      --title "タイトル" --summary "要約文" -o out.md
"""
import argparse
import datetime
import re
import sys


def detect_has_figures(md):
    """SVG/Mermaid/画像参照のいずれかを含むか。"""
    if "<svg" in md:
        return True
    if re.search(r"```mermaid", md):
        return True
    if re.search(r"!\[[^\]]*\]\([^)]+\)", md):  # ![alt](url)
        return True
    return False


def extract_title(md):
    """本文先頭のレベル1見出しをタイトルに。無ければ最初の見出し。"""
    m = re.search(r"^#\s+(.+)$", md, re.MULTILINE)
    if m:
        return m.group(1).strip()
    m = re.search(r"^#{1,6}\s+(.+)$", md, re.MULTILINE)
    return m.group(1).strip() if m else "(無題)"


def extract_summary(md, limit=200):
    """先頭の本文段落から200字以内の要約を機械抽出（フォールバック用）。
    見出し・表・画像・コードブロックを除いた最初の散文を使う。"""
    lines = md.splitlines()
    buf = []
    in_code = False
    for ln in lines:
        s = ln.strip()
        if s.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        if not s:
            if buf:
                break
            continue
        if s.startswith("#") or s.startswith("|") or s.startswith("![") \
           or s.startswith("<") or s.startswith(">"):
            continue
        buf.append(s)
    text = " ".join(buf).strip()
    text = re.sub(r"\s+", " ", text)
    if len(text) > limit:
        text = text[:limit - 1].rstrip() + "…"  # …込みでlimit字以内
    return text or "(要約なし)"


def yaml_escape(s):
    """YAML値として安全化。コロン・引用符を含む場合はダブルクオート。"""
    s = s.replace("\n", " ").strip()
    if any(c in s for c in [":", "#", '"', "'", "[", "]", "{", "}"]) \
       or s.startswith(("- ", "? ", "& ", "* ")):
        return '"' + s.replace('"', '\\"') + '"'
    return s


def build_frontmatter(title, url, fmt, has_fig, summary, when=None):
    when = when or datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    lines = [
        "---",
        f"title: {yaml_escape(title)}",
        f"source_url: {yaml_escape(url)}",
        f"source_format: {fmt}",
        f"has_figures: {'true' if has_fig else 'false'}",
        f"summary: {yaml_escape(summary)}",
        f"retrieved_at: {when}",
        "---",
        "",
    ]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("body", help="変換済みMarkdownファイル")
    ap.add_argument("--url", required=True)
    ap.add_argument("--format", required=True,
                    choices=["html", "pdf", "docx", "xlsx", "pptx"])
    ap.add_argument("--title", default=None, help="省略時は本文先頭見出し")
    ap.add_argument("--summary", default=None, help="省略時は先頭段落200字")
    ap.add_argument("--retrieved-at", default=None)
    ap.add_argument("-o", "--output", default=None)
    args = ap.parse_args()

    with open(args.body, encoding="utf-8") as f:
        md = f.read()

    # 既にフロントマターがある場合は二重付与しない
    if md.lstrip().startswith("---"):
        print("[warn] 既にフロントマターがあります。スキップ。", file=sys.stderr)
        out = md
    else:
        title = args.title or extract_title(md)
        summary = args.summary or extract_summary(md)
        if len(summary) > 200:
            summary = summary[:199].rstrip() + "…"  # CLI指定要約も200字に丸める
        has_fig = detect_has_figures(md)
        fm = build_frontmatter(title, args.url, args.format, has_fig,
                               summary, args.retrieved_at)
        out = fm + md

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(out)
        print(f"[ok] {args.output}", file=sys.stderr)
    else:
        sys.stdout.write(out)


if __name__ == "__main__":
    main()
