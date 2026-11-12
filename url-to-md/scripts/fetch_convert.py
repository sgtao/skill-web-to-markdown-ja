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
fetch_convert.py — URLからファイルを取得し、形式を判定してMarkdownに変換する。

Claude Code（Ubuntu）想定。取得は curl、変換は markitdown を使う。
PDF だけは markitdown の簡易変換に頼らず、pdf-to-md-ja スキルへ委譲することを推奨
（図のSVG/Mermaid再現はそちらが担う）。本スクリプトの --pdf-passthrough で
PDFはダウンロードのみ行い、変換は呼び出し側に委ねる。

使い方:
  uv run fetch_convert.py <URL> [-o out.md]          # uv（推奨・依存自動解決）
  python3 fetch_convert.py <URL> [-o out.md]         # pip環境
  uv run fetch_convert.py <URL> --pdf-passthrough    # PDFは保存だけして委譲

依存:
  curl（システム）, Python: markitdown[docx], trafilatura, beautifulsoup4
  （uv があれば自動解決。無ければ pip install）
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile

from _common import StopReason, fetch, move, read_html_file, run_main, write_stdout


def detect_format(url, content_type=""):
    """URL末尾とContent-Typeから形式を判定。"""
    url_l = url.lower().split("?")[0]
    ext_map = {
        ".pdf": "pdf", ".docx": "docx", ".xlsx": "xlsx", ".pptx": "pptx",
        ".html": "html", ".htm": "html",
        ".doc": "doc_legacy", ".xls": "xls_legacy", ".ppt": "ppt_legacy",
    }
    for ext, fmt in ext_map.items():
        if url_l.endswith(ext):
            return fmt
    ct = content_type.lower()
    ct_map = {
        "application/pdf": "pdf",
        "wordprocessingml": "docx",
        "spreadsheetml": "xlsx",
        "presentationml": "pptx",
        "text/html": "html",
    }
    for key, fmt in ct_map.items():
        if key in ct:
            return fmt
    return "html"  # 既定はWebページ


def convert(path, fmt, url=None, content_type=""):
    """markitdownでMarkdownに変換。HTMLは本文抽出+絶対URL化経路へ。"""
    legacy = {"doc_legacy", "xls_legacy", "ppt_legacy"}
    if fmt in legacy:
        raise StopReason(
            "UNSUPPORTED_FORMAT",
            f"旧形式（{fmt}）はそのままでは変換できません。",
            "新しい形式（docx / xlsx / pptx）か PDF で保存し直して渡してください"
            "（LibreOffice があれば soffice --headless --convert-to "
            "<docx|xlsx|pptx> で変換できます）。")
    if fmt == "html":
        # HTMLは本文抽出+画像src絶対URL化（html_to_md.py と同じロジック）
        from html_to_md import absolutize, to_markdown
        html = read_html_file(path, content_type)  # 文字コードを判定して読む
        base = url or "https://example.com/"
        return to_markdown(absolutize(html, base), base)
    from markitdown import MarkItDown
    return MarkItDown().convert(path).text_content


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("-o", "--output", default=None)
    ap.add_argument("--pdf-passthrough", action="store_true",
                    help="PDFは保存のみ。変換はpdf-to-md-jaへ委譲")
    args = ap.parse_args()

    # YouTube はダウンロード不要の専用経路（youtube_to_md.py へ誘導する安全網）
    from youtube_to_md import is_youtube_url
    if is_youtube_url(args.url):
        script_dir = os.path.dirname(os.path.abspath(__file__))
        youtube_script = os.path.join(script_dir, "youtube_to_md.py")
        print("[判定] YouTube URL です。専用スクリプトを使ってください:", file=sys.stderr)
        print(f'  uv run "{youtube_script}" "{args.url}" -o out.md', file=sys.stderr)
        print(f'  （uvが無い場合: python3 "{youtube_script}" ...）', file=sys.stderr)
        return

    tmpdir = tempfile.mkdtemp(prefix="url2md_")
    try:
        _run(args, tmpdir)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)  # 一時フォルダを残さない


def _run(args, tmpdir):
    """取得 → 形式判定 → 分岐（main から一時フォルダつきで呼ばれる）。"""
    raw = os.path.join(tmpdir, "downloaded")
    print(f"[1] 取得: {args.url}", file=sys.stderr)
    ct = fetch(args.url, raw)
    fmt = detect_format(args.url, ct)
    print(f"[2] 形式判定: {fmt} (Content-Type: {ct or 'なし'})", file=sys.stderr)

    if fmt == "pdf" and args.pdf_passthrough:
        # PDFは委譲：拡張子を付けて保存先を知らせるだけ
        pdf_path = (args.output or "downloaded") + ".pdf" \
            if not (args.output or "").endswith(".pdf") else args.output
        pdf_path = pdf_path or os.path.join(os.getcwd(), "downloaded.pdf")
        move(raw, pdf_path)  # ドライブをまたいでも動く
        print(f"[3] PDF保存: {pdf_path}", file=sys.stderr)
        print(f"→ pdf-to-md-ja スキルで変換してください: {pdf_path}", file=sys.stderr)
        print(pdf_path)  # stdoutにはパスだけ
        return

    if fmt in ("docx", "xlsx", "pptx") and args.pdf_passthrough:
        # Officeは PDF化 → pdf-to-md-ja へ委譲（図再現のため）
        src = raw + "." + fmt
        move(raw, src)
        outdir = os.path.dirname(os.path.abspath(args.output or "out")) or os.getcwd()
        try:
            r = subprocess.run(
                ["soffice", "--headless", "--convert-to", "pdf", "--outdir", outdir, src],
                capture_output=True, timeout=180)
        except FileNotFoundError:
            raise StopReason(
                "MISSING_TOOL",
                "LibreOffice（soffice）が見つかりません。Office ファイルを PDF にできません。",
                "LibreOffice を導入して soffice に PATH を通すか、"
                "PDF で保存し直して渡してください。") from None
        pdf_path = os.path.join(outdir, os.path.basename(src).rsplit(".", 1)[0] + ".pdf")
        if r.returncode != 0 or not os.path.exists(pdf_path):
            detail = r.stderr.decode("utf-8", errors="replace")[:200]
            raise StopReason("CONVERT_FAILED", f"soffice での PDF 化に失敗しました: {detail}",
                             "PDF で保存し直して渡してください。")
        print(f"[3] {fmt}→PDF: {pdf_path}", file=sys.stderr)
        print(f"→ pdf-to-md-ja スキルで変換してください: {pdf_path}", file=sys.stderr)
        print(pdf_path)
        return

    print(f"[3] 変換中...", file=sys.stderr)
    text = convert(raw, fmt, url=args.url, content_type=ct)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"[4] 出力: {args.output}", file=sys.stderr)
    else:
        write_stdout(text + "\n")


if __name__ == "__main__":
    run_main(main)
