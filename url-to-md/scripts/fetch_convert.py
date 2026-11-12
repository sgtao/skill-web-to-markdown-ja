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
fetch_convert.py — URL または手元のファイルの形式を判定して、Markdown に変換する。

取得は curl、変換は markitdown を使う（HTML は本文抽出 + 絶対 URL 化）。
経路（--route。既定は auto）:
  pdf    PDF を作って（PDF はそのまま）、そのパスを標準出力に 1 行で返す。
         変換は pdf-to-md-ja スキルが行う（図の SVG / Mermaid 再現はそちらが担う）
  direct markitdown で直接 Markdown にする。Office は、画面表示から得られる情報
         （グラフの元データ・埋め込み画像・再現できなかったもの）を末尾に補足する
  auto   pdf → pdf、docx / pptx → soffice があれば pdf、無ければ direct、
         xlsx → direct（表が崩れにくい）、html → direct。旧 Office は soffice で新形式にしてから
どの経路になったかは、標準エラーの ROUTE=pdf / ROUTE=direct で分かる。

使い方（くわしい契約は --help）:
  uv run fetch_convert.py <URL> [-o out.md]              # uv（推奨・依存自動解決）
  python3 fetch_convert.py <URL> [-o out.md]             # pip 環境
  uv run fetch_convert.py --file <path> [-o out.md]      # 手元のファイル
  uv run fetch_convert.py <URL> --route pdf|direct [--outdir <dir>]
  python3 fetch_convert.py --check-env                   # 道具の有無（通信しない）

依存:
  curl（システム）, Python: markitdown[docx,xlsx,pptx], trafilatura, beautifulsoup4
  （uv があれば自動解決。無ければ pip install）
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

from _common import (StopReason, check_env, contract_epilog, fetch,
                     find_soffice, format_env, move, read_html_file, run_main,
                     run_soffice, safe_stem, sniff_format, unique_path, write_stdout)

EXT_MAP = {
    ".pdf": "pdf", ".docx": "docx", ".xlsx": "xlsx", ".pptx": "pptx",
    ".html": "html", ".htm": "html", ".xhtml": "html",
    ".doc": "doc_legacy", ".xls": "xls_legacy", ".ppt": "ppt_legacy",
}
CT_MAP = {
    "application/pdf": "pdf",
    "wordprocessingml": "docx",
    "spreadsheetml": "xlsx",
    "presentationml": "pptx",
    "text/html": "html",
    "application/xhtml": "html",
    "application/msword": "doc_legacy",
    "application/vnd.ms-excel": "xls_legacy",
    "application/vnd.ms-powerpoint": "ppt_legacy",
}
LEGACY = ("doc_legacy", "xls_legacy", "ppt_legacy", "office_legacy")
LEGACY_TARGET = {"doc_legacy": "docx", "xls_legacy": "xlsx", "ppt_legacy": "pptx"}
OFFICE = ("docx", "xlsx", "pptx")

REASON_CODES = ("NO_NETWORK", "UNSUPPORTED_SCHEME", "HTTP_ERROR", "LOGIN_REQUIRED",
                "JS_RENDERED", "EMPTY_CONTENT", "FETCH_FAILED", "CONVERT_FAILED",
                "UNSUPPORTED_FORMAT", "LEGACY_NEEDS_SOFFICE", "MISSING_TOOL")
ROUTES = ("auto", "pdf", "direct")


def format_from_name(name):
    """URL またはファイル名の拡張子から形式を決める（分からなければ None）。"""
    name_l = name.lower().split("?")[0].split("#")[0]
    for ext, fmt in EXT_MAP.items():
        if name_l.endswith(ext):
            return fmt
    return None


def format_from_content_type(content_type):
    """Content-Type から形式を決める（分からなければ None）。"""
    ct = (content_type or "").lower()
    for key, fmt in CT_MAP.items():
        if key in ct:
            return fmt
    return None


def detect_format(url, content_type=""):
    """URL 末尾と Content-Type から形式を判定する（中身は見ない）。既定は html。"""
    return format_from_name(url) or format_from_content_type(content_type) or "html"


def decide_format(name, content_type="", sniffed=None, default="html"):
    """名前（拡張子）・Content-Type・先頭のバイトの 3 つから、形式を決める。

    順番:
      1. 先頭のバイトで pdf / docx / xlsx / pptx と確かめられたら、それを採る
         （拡張子や Content-Type と食い違っても、中身を信じる）
      2. 先頭のバイトが旧 Office の入れ物（ole）なら旧形式
         （拡張子が doc / xls / ppt ならその形式、ほかは office_legacy）
      3. 拡張子 → Content-Type → 先頭のバイトが html なら html → default

    Args:
        name: URL またはファイル名。
        content_type: HTTP の Content-Type（ファイルのときは空）。
        sniffed: _common.sniff_format の結果。
        default: どれでも決まらないときの形式（None なら None を返す）。
    """
    by_name = format_from_name(name)
    if sniffed in ("pdf", "docx", "xlsx", "pptx"):
        return sniffed
    if sniffed == "ole":
        return by_name if by_name in LEGACY else "office_legacy"
    guessed = by_name or format_from_content_type(content_type)
    if guessed:
        return guessed
    if sniffed == "html":
        return "html"
    return default


def _convert_with_markitdown(path, fmt):
    """markitdown で変換する。追加の依存が足りないときは MISSING_TOOL にする。"""
    try:
        from markitdown import MarkItDown
    except ImportError:
        raise StopReason(
            "MISSING_TOOL", "Python のパッケージ markitdown が見つかりません。",
            "uv run で実行するか、pip install 'markitdown[docx,xlsx,pptx]' を行ってください。") from None
    try:
        return MarkItDown().convert(path).text_content
    except Exception as e:  # noqa: BLE001 - markitdown の例外の型は版で変わる
        name = type(e).__name__
        if "MissingDependency" in name or "MissingDependency" in str(e):
            hint = ("PDF は --pdf-passthrough を付けて pdf-to-md-ja スキルで変換してください。"
                    if fmt == "pdf" else
                    "PDF で保存し直して渡すか、--pdf-passthrough（LibreOffice が必要）を"
                    "使ってください。")
            raise StopReason(
                "MISSING_TOOL",
                f"{fmt} を直接変換するための Python の依存（markitdown[{fmt}]）が"
                "入っていません。", hint) from None
        raise StopReason("CONVERT_FAILED",
                         f"{fmt} の変換に失敗しました: {name}: {str(e)[:200]}") from None


def convert(path, fmt, url=None, content_type="", force=False):
    """Markdown に変換する。HTML は本文抽出 + 絶対 URL 化の経路へ。"""
    if fmt in LEGACY:
        raise StopReason(
            "UNSUPPORTED_FORMAT",
            f"旧形式（{fmt}）はそのままでは変換できません。",
            "新しい形式（docx / xlsx / pptx）か PDF で保存し直して渡してください"
            "（LibreOffice があれば soffice --headless --convert-to "
            "<docx|xlsx|pptx> で変換できます）。")
    if fmt == "html":
        # HTMLは本文抽出+画像src絶対URL化（html_to_md.py と同じロジック）
        from html_to_md import absolutize, check_html, to_markdown
        html = read_html_file(path, content_type)  # 文字コードを判定して読む
        check_html(html, force=force)  # ログイン画面・JS 描画らしいときは止める
        if not url:  # 手元のファイルは基点の URL が無いので、相対パスはそのまま残す
            return to_markdown(html, None)
        return to_markdown(absolutize(html, url), url)
    return _convert_with_markitdown(path, fmt)


def require_soffice():
    """soffice の場所を返す。無ければ MISSING_TOOL で止める。"""
    soffice = find_soffice()
    if soffice is None:
        raise StopReason(
            "MISSING_TOOL",
            "LibreOffice（soffice）が見つかりません。Office ファイルを PDF にできません。",
            "LibreOffice を導入する（場所は環境変数 SOFFICE_PATH でも指定できる）か、"
            "PDF で保存し直して渡してください。--pdf-passthrough を外せば、"
            "文字と表だけの直接変換を試せます。")
    return soffice


def office_to_pdf(soffice, src, outdir, stem, tmpdir):
    """soffice で Office ファイルを PDF にし、outdir に連番つきの名前で置く。"""
    made = run_soffice(soffice, src, "pdf", os.path.join(tmpdir, "soffice_out"), tmpdir)
    return move(made, unique_path(outdir, stem + ".pdf"))


def choose_route(fmt, route, has_soffice):
    """経路（"pdf" か "direct"）を決める。

    Args:
        fmt: 形式（pdf / html / docx / xlsx / pptx）。旧 Office は新形式にしてから呼ぶ。
        route: auto / pdf / direct。
        has_soffice: soffice が見つかったか。

    Returns:
        "pdf" か "direct"。route が pdf で soffice が必要なのに無いときは、
        後の処理で MISSING_TOOL になる（ここでは決めるだけ）。
    """
    if fmt == "html":
        return "direct"  # HTML は PDF にしない
    if route in ("pdf", "direct"):
        return route
    if fmt == "pdf":
        return "pdf"
    if fmt in ("docx", "pptx"):
        return "pdf" if has_soffice else "direct"
    return "direct"  # xlsx は表が崩れにくい直接変換を既定にする


def build_parser():
    ap = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "URL または手元のファイルの形式を判定して、Markdown に変換する。\n"
            "\n"
            "入力（どちらか 1 つ。--check-env のときは不要）:\n"
            "  <URL>            http / https の URL を curl で取得する\n"
            "  --file <path>    手元のファイル（html / pdf / docx / xlsx / pptx）\n"
            "  形式は、拡張子 → Content-Type の順で決め、先頭のバイト（%PDF、ZIP の中の\n"
            "  word/ xl/ ppt/）で確かめる。食い違うときは中身を採る。\n"
            "出力:\n"
            "  -o <file>        Markdown を書くファイル（UTF-8）。省略時は標準出力。\n"
            "                   PDF の置き場所には使わない\n"
            "  --outdir <dir>   PDF と、取り出した画像（<名前>_assets/）の置き場所。省略時は\n"
            "                   -o と同じ場所（画像）／一時フォルダ（PDF）。ファイル名は入力の\n"
            "                   名前から作り、同名があれば -2, -3 … と連番を付ける\n"
            "  YouTube の URL は変換せず、youtube_to_md.py の使い方を標準エラーに出す。"),
        epilog=contract_epilog(
            REASON_CODES,
            "結果だけ。ROUTE=direct なら Markdown の本文（-o を付けたときは何も出さない）、\n"
            "  ROUTE=pdf なら PDF のパス 1 行、--check-env なら JSON",
            notes=["補足:",
                   "  標準エラーの ROUTE=pdf / ROUTE=direct で、どちらの経路になったかを知らせる。",
                   "  Office の直接変換は、本文の末尾に「補足: 画面表示から得られる情報」を足す。",
                   "  soffice は環境変数 SOFFICE_PATH でも指定できる（--check-env で見つけた場所を確認）。",
                   "  --check-env は外部に接続しない。通信できるかは、対象の URL を取得して",
                   "  みて判定する（できなければ REASON=NO_NETWORK）。",
                   "  --force で続行できるのは LOGIN_REQUIRED・JS_RENDERED だけ。"]))
    ap.add_argument("url", nargs="?", help="取得する URL（http / https）")
    ap.add_argument("--file", metavar="PATH", help="手元のファイルを変換する（URL の代わり）")
    ap.add_argument("-o", "--output", default=None, metavar="FILE",
                    help="Markdown の出力ファイル（省略時は標準出力）")
    ap.add_argument("--outdir", default=None, metavar="DIR",
                    help="PDF と取り出した画像の置き場所（省略時は一時フォルダ／-o と同じ場所）")
    ap.add_argument("--route", choices=ROUTES, default="auto",
                    help="pdf: PDF のパスを返す（変換は pdf-to-md-ja）／direct: 直接 Markdown に／"
                         "auto（既定）: 形式ごとに選ぶ（上の説明を参照）")
    ap.add_argument("--no-supplement", action="store_true",
                    help="Office の直接変換で、画面情報の補足（グラフ・画像・再現できなかったもの）を足さない")
    ap.add_argument("--pdf-passthrough", action="store_true",
                    help="--route pdf と同じ（互換のために残している。新しい手順では --route を使う）")
    ap.add_argument("--force", action="store_true",
                    help="LOGIN_REQUIRED・JS_RENDERED（推定）で止めずに続行する")
    ap.add_argument("--check-env", action="store_true",
                    help="道具の有無を調べて終了する（標準出力に JSON、標準エラーに 1 行ずつ）")
    return ap


def main():
    ap = build_parser()
    args = ap.parse_args()

    if args.check_env:
        env = check_env()
        for line in format_env(env):
            print(line, file=sys.stderr)
        write_stdout(json.dumps(env, ensure_ascii=False, indent=2) + "\n")
        return
    if bool(args.url) == bool(args.file):
        ap.error("URL か --file <path> のどちらか 1 つを指定してください。")
    if args.file and not os.path.isfile(args.file):
        ap.error(f"ファイルが見つかりません: {args.file}")

    # YouTube はダウンロード不要の専用経路（youtube_to_md.py へ誘導する安全網）
    from youtube_to_md import is_youtube_url
    if args.url and is_youtube_url(args.url):
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


def _resolve_outdir(args):
    """PDF の置き場所を決める（省略時は一時フォルダを作って知らせる）。"""
    if args.outdir:
        os.makedirs(args.outdir, exist_ok=True)
        return os.path.abspath(args.outdir)
    outdir = tempfile.mkdtemp(prefix="url2md_out_")
    print(f"[info] --outdir の指定が無いので、一時フォルダに置きます: {outdir}",
          file=sys.stderr)
    return outdir


def _announce_route(route):
    print(f"ROUTE={route}", file=sys.stderr)


def _passthrough(args, path, fmt, source, tmpdir):
    """経路 pdf: PDF のパスを標準出力に 1 行で返す（変換は pdf-to-md-ja）。"""
    if args.output:
        print("[info] -o は Markdown の出力先です。PDF の置き場所は --outdir で指定します"
              "（今回の -o は使いません）。", file=sys.stderr)
    stem = safe_stem(source)
    if fmt == "pdf" and args.file:
        pdf_path = os.path.abspath(args.file)  # 手元の PDF は写さず、そのまま使う
        print(f"[3] 手元の PDF をそのまま使います: {pdf_path}", file=sys.stderr)
    elif fmt == "pdf":
        pdf_path = move(path, unique_path(_resolve_outdir(args), stem + ".pdf"))
        print(f"[3] PDF保存: {pdf_path}", file=sys.stderr)
    else:
        # Officeは PDF化 → pdf-to-md-ja へ委譲（図再現のため）
        soffice = require_soffice()  # 無ければ、何も作らずにここで止まる
        src = os.path.join(tmpdir, f"{stem}.{fmt}")
        if args.file or path != src:
            shutil.copyfile(path, src)  # 元のファイルは動かさない
        pdf_path = office_to_pdf(soffice, src, _resolve_outdir(args), stem, tmpdir)
        print(f"[3] {fmt}→PDF: {pdf_path}", file=sys.stderr)
    print(f"→ pdf-to-md-ja スキルで変換してください: {pdf_path}", file=sys.stderr)
    write_stdout(pdf_path + "\n")  # stdoutにはパスだけ


def _upgrade_legacy(path, fmt, source, tmpdir):
    """旧 Office（doc / xls / ppt）を soffice で新形式にする。

    Returns:
        (新形式のファイルのパス, 新しい形式)。

    Raises:
        StopReason: LEGACY_NEEDS_SOFFICE（soffice が無い）/ UNSUPPORTED_FORMAT
            （種類が分からない旧 Office）/ CONVERT_FAILED。
    """
    soffice = find_soffice()
    if soffice is None:
        raise StopReason(
            "LEGACY_NEEDS_SOFFICE",
            f"旧形式の Office ファイルです（{fmt}）。変換に LibreOffice（soffice）が必要ですが、"
            "見つかりません。")
    target = LEGACY_TARGET.get(fmt)
    if target is None:
        raise StopReason(
            "UNSUPPORTED_FORMAT",
            "旧形式の Office ファイルですが、種類（doc / xls / ppt）が分かりません。",
            "拡張子（.doc / .xls / .ppt）を付けて渡すか、新しい形式で保存し直してください。")
    ext = {"docx": "doc", "xlsx": "xls", "pptx": "ppt"}[target]
    src = os.path.join(tmpdir, f"{safe_stem(source)}.{ext}")
    shutil.copyfile(path, src)
    made = run_soffice(soffice, src, target, os.path.join(tmpdir, "legacy_out"), tmpdir)
    print(f"[info] 旧形式（{fmt}）を soffice で {target} にしました。", file=sys.stderr)
    return made, target


def _assets_plan(args, source):
    """画像の保存先を決める。

    Returns:
        (保存先を返す関数, 補足の中の画像パスの基点)。
        -o があれば「Markdown と同じ場所の <名前>_assets/」で、相対パスにする。
        --outdir があれば「その中の <名前>_assets/」。どちらも無ければ一時フォルダ。
    """
    if args.output:
        base = os.path.dirname(os.path.abspath(args.output))
        name = os.path.splitext(os.path.basename(args.output))[0]
        return (lambda: os.path.join(base, name + "_assets")), base
    stem = safe_stem(source)
    if args.outdir:
        return (lambda: os.path.join(os.path.abspath(args.outdir), stem + "_assets")), None

    def temp_dir():
        d = tempfile.mkdtemp(prefix="url2md_assets_")
        print(f"[info] --outdir・-o の指定が無いので、画像は一時フォルダに置きます: {d}",
              file=sys.stderr)
        return d
    return temp_dir, None


def _supplement(args, path, source):
    """Office の直接変換に足す補足（グラフ・画像・再現できなかったもの）。

    補足を作れなかったときも、黙らずにその旨を本文に書く。
    """
    try:
        import office_extract
        assets, md_dir = _assets_plan(args, source)
        result = office_extract.extract(path, assets_dir=assets)
        if result["images_dir"]:
            print(f"[info] 埋め込み画像を保存しました: {result['images_dir']}", file=sys.stderr)
        return office_extract.render_markdown(result, md_dir=md_dir)
    except Exception as e:  # noqa: BLE001 - 補足の失敗で、本文の変換まで捨てない
        if os.environ.get("URL_TO_MD_DEBUG"):
            raise
        print(f"[warn] 画面情報の補足を作れませんでした: {type(e).__name__}: {e}",
              file=sys.stderr)
        return ("\n---\n\n## 補足: 画面表示から得られる情報\n\n"
                f"補足を作れませんでした（{type(e).__name__}）。グラフ・画像・図形は"
                "この Markdown に含まれていません。\n")


def _write_markdown(args, text):
    if args.output:
        parent = os.path.dirname(os.path.abspath(args.output))
        os.makedirs(parent, exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"[4] 出力: {args.output}", file=sys.stderr)
    else:
        write_stdout(text + "\n")


def _run(args, tmpdir):
    """取得 → 形式判定 → 経路の決定 → 変換（main から一時フォルダつきで呼ばれる）。"""
    if args.file:
        path, source, ct = args.file, args.file, ""
        print(f"[1] ファイル: {path}", file=sys.stderr)
        if os.path.getsize(path) == 0:
            raise StopReason("EMPTY_CONTENT", f"ファイルが空でした: {path}")
        default = None
    else:
        path, source = os.path.join(tmpdir, "downloaded"), args.url
        print(f"[1] 取得: {args.url}", file=sys.stderr)
        ct = fetch(args.url, path)
        default = "html"  # 既定はWebページ
    sniffed = sniff_format(path)
    fmt = decide_format(source, ct, sniffed, default=default)
    if fmt is None:
        raise StopReason(
            "UNSUPPORTED_FORMAT",
            f"対応していない形式のファイルです: {os.path.basename(path)}",
            "html / pdf / docx / xlsx / pptx のどれかで保存し直して渡してください。")
    named = format_from_name(source)
    if named and named != fmt:
        print(f"[warn] 拡張子は {named} ですが、中身は {fmt} でした。中身に合わせます。",
              file=sys.stderr)
    print(f"[2] 形式判定: {fmt} (Content-Type: {ct or 'なし'} / 先頭のバイト: "
          f"{sniffed or '不明'})", file=sys.stderr)

    if fmt in LEGACY:
        path, fmt = _upgrade_legacy(path, fmt, source, tmpdir)

    requested = "pdf" if args.pdf_passthrough else args.route
    route = choose_route(fmt, requested, find_soffice() is not None)
    _announce_route(route)
    if route == "pdf":
        _passthrough(args, path, fmt, source, tmpdir)
        return

    print("[3] 変換中...", file=sys.stderr)
    text = convert(path, fmt, url=args.url or None, content_type=ct, force=args.force)
    if fmt in OFFICE and not args.no_supplement:
        text = text.rstrip("\n") + "\n" + _supplement(args, path, source)
    _write_markdown(args, text)


if __name__ == "__main__":
    run_main(main)
