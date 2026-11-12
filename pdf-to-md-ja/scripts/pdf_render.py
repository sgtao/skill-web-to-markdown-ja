#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "pypdfium2",
#     "pypdf",
#     "pillow",
# ]
# ///
"""
pdf_render.py — PDF のページを PNG にする／埋め込み画像を取り出す。

ページの画像化は pdftoppm（poppler）があればそれを使い、無ければ pypdfium2 を使う。
画像の取り出しは pdfimages（poppler）があればそれを使い、無ければ pypdf（と Pillow）を使う。
どちらを使ったかは標準エラーの RENDERER= / EXTRACTOR= で分かる（縮退の報告に使う）。

入力:
  <pdf>                  対象の PDF
  --pages 3,5-7          対象ページ（1 始まり。省略時は先頭から --max-pages ページ）
  --dpi N                ページの画像化の解像度（既定 150）
  --extract-images       ページの画像化ではなく、埋め込みのラスタ画像（写真・地図）を取り出す
  --outdir DIR           出力先（省略時は一時フォルダを作る）
  --max-pages N          --pages 省略時の上限（既定 20）
標準出力:
  書き出したファイルのパスを 1 行に 1 つ（常に UTF-8）
標準エラー:
  進捗・警告。失敗時の最後の 3 行は [stop] 理由 / [next] 次の手段 / REASON=<理由コード>
終了コード:
  0 成功 / 2 引数の誤り / 3 失敗 / 4 必要な道具が無い
理由コード:
  PDF_UNREADABLE  壊れている・PDF ではない・ページの指定が範囲外
  FILE_NOT_FOUND  ファイルが無い
  NO_IMAGES       --extract-images で、取り出せる画像が無い（ベクター図は出ない）
  MISSING_TOOL    pdftoppm も pypdfium2 も無い（画像の取り出しは pdfimages も pypdf も無い）
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile

from pdf_probe import Stop, parse_pages, setup_stdio


def page_count(path):
    try:
        import pypdf
        return len(pypdf.PdfReader(path).pages)
    except ImportError:
        pass
    try:
        import pypdfium2 as pdfium
        return len(pdfium.PdfDocument(path))
    except ImportError:
        pass
    if shutil.which("pdfinfo"):
        r = subprocess.run(["pdfinfo", path], capture_output=True)
        for line in r.stdout.decode("utf-8", "replace").splitlines():
            if line.startswith("Pages:"):
                return int(line.split(":")[1])
    raise Stop("MISSING_TOOL", "ページ数を調べる道具（pypdf・pypdfium2・pdfinfo）がありません",
               "`uv run` で実行するか、`pip install pypdfium2 pypdf pillow` を（ユーザーに確認して）実行してください", 4)


def render_pages(path, pages, dpi, outdir, stem):
    out = []
    if shutil.which("pdftoppm"):
        print("RENDERER=pdftoppm", file=sys.stderr)
        for p in pages:
            prefix = os.path.join(outdir, f"{stem}_p{p:03d}")
            r = subprocess.run(["pdftoppm", "-png", "-r", str(dpi), "-f", str(p), "-l", str(p),
                                "-singlefile", path, prefix], capture_output=True)
            if r.returncode != 0:
                raise Stop("PDF_UNREADABLE", f"{p} ページ目を画像にできませんでした",
                           "PDF を開き直して保存し直すか、別の PDF で試してください")
            out.append(prefix + ".png")
        return out
    try:
        import pypdfium2 as pdfium
    except ImportError:
        raise Stop("MISSING_TOOL", "ページを画像にする道具（pdftoppm・pypdfium2）がありません",
                   "`uv run` で実行するか、`pip install pypdfium2 pillow` を（ユーザーに確認して）実行してください", 4)
    print("RENDERER=pypdfium2", file=sys.stderr)
    try:
        doc = pdfium.PdfDocument(path)
        for p in pages:
            img = doc[p - 1].render(scale=dpi / 72).to_pil()
            dest = os.path.join(outdir, f"{stem}_p{p:03d}.png")
            img.save(dest)
            out.append(dest)
    except Exception as e:
        raise Stop("PDF_UNREADABLE", f"ページを画像にできませんでした（{type(e).__name__}: {e}）",
                   "PDF を開き直して保存し直すか、別の PDF で試してください")
    return out


def extract_images(path, pages, outdir, stem):
    out = []
    if shutil.which("pdfimages"):
        print("EXTRACTOR=pdfimages", file=sys.stderr)
        for p in pages:
            prefix = os.path.join(outdir, f"{stem}_p{p:03d}_img")
            subprocess.run(["pdfimages", "-png", "-f", str(p), "-l", str(p), path, prefix],
                           capture_output=True)
        out = sorted(os.path.join(outdir, f) for f in os.listdir(outdir)
                     if f.startswith(f"{stem}_p") and "_img" in f and f.endswith(".png"))
    else:
        try:
            import pypdf
            import PIL  # noqa: F401  画像のデコードに必要
        except ImportError:
            raise Stop("MISSING_TOOL", "画像を取り出す道具（pdfimages、または pypdf と Pillow）がありません",
                       "`uv run` で実行するか、`pip install pypdf pillow` を（ユーザーに確認して）実行してください", 4)
        print("EXTRACTOR=pypdf", file=sys.stderr)
        reader = pypdf.PdfReader(path)
        for p in pages:
            try:
                images = list(reader.pages[p - 1].images)
            except Exception as e:
                print(f"[warn] {p} ページ目の画像を読めませんでした（{type(e).__name__}）", file=sys.stderr)
                continue
            for n, im in enumerate(images, 1):
                dest = os.path.join(outdir, f"{stem}_p{p:03d}_img{n:02d}.png")
                try:
                    im.image.convert("RGBA" if im.image.mode in ("LA", "RGBA", "P") else "RGB").save(dest)
                    out.append(dest)
                except Exception as e:
                    print(f"[warn] {p} ページ目の画像 {n} を保存できませんでした（{type(e).__name__}）",
                          file=sys.stderr)
    if not out:
        raise Stop("NO_IMAGES", "取り出せるラスタ画像がありませんでした",
                   "ベクターで描かれた図は取り出せません。ページを画像にして（--extract-images なしで）使ってください")
    return out


def main(argv=None):
    setup_stdio()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdf", help="対象の PDF")
    ap.add_argument("--pages", help="対象ページ（例: 3,5-7）")
    ap.add_argument("--dpi", type=int, default=150, help="ページの画像化の解像度（既定 150）")
    ap.add_argument("--extract-images", action="store_true", help="埋め込みのラスタ画像を取り出す")
    ap.add_argument("--outdir", help="出力先（省略時は一時フォルダ）")
    ap.add_argument("--max-pages", type=int, default=20, help="--pages 省略時の上限（既定 20）")
    args = ap.parse_args(argv)
    try:
        if not os.path.isfile(args.pdf):
            raise Stop("FILE_NOT_FOUND", f"ファイルがありません: {args.pdf}", "パスを確かめてください")
        total = page_count(args.pdf)
        if args.pages:
            pages = parse_pages(args.pages, total)
            if not pages:
                raise Stop("PDF_UNREADABLE", f"--pages {args.pages} は範囲外です（全 {total} ページ）",
                           "ページの指定を直してください")
        else:
            pages = list(range(1, min(total, args.max_pages) + 1))
            if total > args.max_pages:
                print(f"[warn] 全 {total} ページのうち先頭 {args.max_pages} ページだけ処理します（--pages で指定）",
                      file=sys.stderr)
        outdir = args.outdir or tempfile.mkdtemp(prefix="pdf_render_")
        os.makedirs(outdir, exist_ok=True)
        stem = os.path.splitext(os.path.basename(args.pdf))[0] or "page"
        paths = (extract_images(args.pdf, pages, outdir, stem) if args.extract_images
                 else render_pages(args.pdf, pages, args.dpi, outdir, stem))
        for p in paths:
            print(p)
        return 0
    except Stop as e:
        print(f"[stop] {e.message}", file=sys.stderr)
        print(f"[next] {e.next_step}", file=sys.stderr)
        print(f"REASON={e.code}", file=sys.stderr)
        return e.exit_code


if __name__ == "__main__":
    sys.exit(main())
