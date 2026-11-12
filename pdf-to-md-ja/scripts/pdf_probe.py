#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "pypdf",
#     "pdfplumber",
# ]
# ///
"""
pdf_probe.py — PDF の素性を調べて JSON で出す（変換方針を決める起点）。

poppler（pdfinfo / pdffonts / pdfimages / pdftotext）があればそれを使い、
無ければ Python のライブラリ（pypdf / pdfplumber）で同じ情報を出す。
どの道具で調べたかは JSON の "backends" に入る（縮退したことを報告に書くため）。

入力:
  <pdf>                調べる PDF のファイル
  --text               本文を標準出力に出す（レイアウトを保つ。pdftotext → pdfplumber → pypdf の順）
  --pages 3,5-7        --text の対象ページ（1 始まり。省略時は全ページ）
  --max-pages N        文字・画像の有無を調べるページ数の上限（既定 100。超えた分は調べない）
標準出力:
  既定: JSON（常に UTF-8）。--text のときは本文
標準エラー:
  進捗・警告。失敗時の最後の 3 行は [stop] 理由 / [next] 次の手段 / REASON=<理由コード>
終了コード:
  0 成功 / 2 引数の誤り / 3 PDF を読めない / 4 必要な道具が無い
理由コード:
  PDF_UNREADABLE  壊れている・PDF ではない
  PDF_ENCRYPTED   パスワードで保護されている
  FILE_NOT_FOUND  ファイルが無い
  MISSING_TOOL    poppler も pypdf も無い
JSON の主な項目:
  pages, page_size（1 ページ目。pt）, page_size_variants, creator, producer, title,
  has_text（文字のあるページが 1 つでもあるか）, text_pages（文字のあるページ）,
  fonts（poppler のときだけ。無ければ null）,
  images（[{page, width, height}]。埋め込みのラスタ画像）,
  hints（観測から言えること: slide_deck / slide_size / document_size / no_text / has_raster_images）,
  backends（info / text / images それぞれ何で調べたか）, degraded（調べられなかったこと）
"""
import argparse
import json
import re
import shutil
import subprocess
import sys

SLIDE_CREATORS = re.compile(r"powerpoint|keynote|impress|google slides|canva|beamer", re.I)


class Stop(Exception):
    def __init__(self, code, message, next_step, exit_code=3):
        super().__init__(message)
        self.code, self.message, self.next_step, self.exit_code = code, message, next_step, exit_code


def setup_stdio():
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")


def parse_pages(spec, total):
    """'1,3-5' → [1, 3, 4, 5]（範囲外は捨てる）。None は全ページ。"""
    if not spec:
        return list(range(1, total + 1))
    out = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return [p for p in out if 1 <= p <= total]


def _run(cmd):
    r = subprocess.run(cmd, capture_output=True)
    return r.returncode, r.stdout.decode("utf-8", errors="replace"), r.stderr.decode("utf-8", errors="replace")


# ---- poppler ---------------------------------------------------------------
def info_poppler(path):
    code, out, err = _run(["pdfinfo", "-enc", "UTF-8", path])
    if code != 0:
        raise Stop("PDF_ENCRYPTED" if "ncrypt" in err or "assword" in err else "PDF_UNREADABLE",
                   f"PDF を読めませんでした: {err.strip().splitlines()[-1] if err.strip() else 'pdfinfo の失敗'}",
                   "別の PDF で試すか、PDF を開き直して保存し直してください（パスワードは外してください）")
    kv = {}
    for line in out.splitlines():
        k, _, v = line.partition(":")
        kv[k.strip()] = v.strip()
    size = re.search(r"([\d.]+)\s*x\s*([\d.]+)", kv.get("Page size", ""))
    return {
        "pages": int(kv.get("Pages", "0") or 0),
        "page_size": ({"width_pt": float(size.group(1)), "height_pt": float(size.group(2))}
                      if size else None),
        "page_size_variants": None,
        "creator": kv.get("Creator") or None,
        "producer": kv.get("Producer") or None,
        "title": kv.get("Title") or None,
    }


def fonts_poppler(path):
    code, out, _ = _run(["pdffonts", path])
    if code != 0:
        return None
    return [ln.split()[0] for ln in out.splitlines()[2:] if ln.strip()]


def images_poppler(path):
    code, out, _ = _run(["pdfimages", "-list", path])
    if code != 0:
        return None
    imgs = []
    for ln in out.splitlines()[2:]:
        f = ln.split()
        if len(f) >= 5 and f[0].isdigit() and f[2] == "image":
            imgs.append({"page": int(f[0]), "width": int(f[3]), "height": int(f[4])})
    return imgs


# ---- Python ライブラリ -------------------------------------------------------
def _pypdf_reader(path):
    try:
        import pypdf
    except ImportError:
        raise Stop("MISSING_TOOL", "poppler も pypdf もありません",
                   "`uv run` で実行するか、`pip install pypdf pdfplumber` を（ユーザーに確認して）実行してください", 4)
    try:
        r = pypdf.PdfReader(path)
        if r.is_encrypted:
            try:
                ok = r.decrypt("")
            except Exception:
                ok = 0
            if not ok:
                raise Stop("PDF_ENCRYPTED", "PDF がパスワードで保護されています",
                           "パスワードを外した PDF を渡してください")
        len(r.pages)
        return r
    except Stop:
        raise
    except Exception as e:
        raise Stop("PDF_UNREADABLE", f"PDF を読めませんでした（{type(e).__name__}）",
                   "別の PDF で試すか、PDF を開き直して保存し直してください")


def info_pypdf(reader):
    meta = reader.metadata or {}
    sizes = {}
    for i, p in enumerate(reader.pages, 1):
        w, h = float(p.mediabox.width), float(p.mediabox.height)
        if (p.get("/Rotate") or 0) % 180 == 90:
            w, h = h, w
        sizes.setdefault((round(w, 1), round(h, 1)), []).append(i)
    first = next(iter(sizes), None)
    return {
        "pages": len(reader.pages),
        "page_size": {"width_pt": first[0], "height_pt": first[1]} if first else None,
        "page_size_variants": len(sizes),
        "creator": str(meta.get("/Creator")) if meta.get("/Creator") else None,
        "producer": str(meta.get("/Producer")) if meta.get("/Producer") else None,
        "title": str(meta.get("/Title")) if meta.get("/Title") else None,
    }


def text_pages_pypdf(reader, limit):
    out = []
    for i, p in enumerate(reader.pages[:limit], 1):
        try:
            if (p.extract_text() or "").strip():
                out.append(i)
        except Exception:
            pass
    return out


def _image_xobjects(resources, depth=0):
    """リソースの中の画像 XObject を（Form の中も 1 段だけ）たどって返す。"""
    found = []
    try:
        xobjs = resources.get("/XObject") if resources else None
        xobjs = xobjs.get_object() if xobjs is not None else {}
    except Exception:
        return found
    for obj in xobjs.values():
        try:
            o = obj.get_object()
            sub = o.get("/Subtype")
            if sub == "/Image":
                found.append({"width": int(o.get("/Width", 0)), "height": int(o.get("/Height", 0))})
            elif sub == "/Form" and depth < 2:
                found += _image_xobjects(o.get("/Resources"), depth + 1)
        except Exception:
            continue
    return found


def images_pypdf(reader, limit):
    imgs = []
    for i, p in enumerate(reader.pages[:limit], 1):
        for im in _image_xobjects(p.get("/Resources")):
            imgs.append({"page": i, **im})
    return imgs


# ---- 本文 ------------------------------------------------------------------
def tidy(text):
    """行末の空白を落とし、前後の空行を削り、3 行以上の連続した空行を 1 行にする。"""
    lines = [ln.rstrip() for ln in text.splitlines()]
    out, blank = [], 0
    for ln in lines:
        blank = blank + 1 if not ln else 0
        if blank <= 1:
            out.append(ln)
    return "\n".join(out).strip("\n")


def extract_text(path, pages, backends):
    if shutil.which("pdftotext"):
        chunks = []
        for p in pages:
            code, out, _ = _run(["pdftotext", "-layout", "-enc", "UTF-8", "-f", str(p), "-l", str(p), path, "-"])
            if code == 0:
                chunks.append(out.rstrip("\f"))
        backends["text"] = "pdftotext"
        return chunks
    try:
        import pdfplumber
        with pdfplumber.open(path) as pdf:
            chunks = [tidy(pdf.pages[p - 1].extract_text(layout=True) or "") for p in pages]
        backends["text"] = "pdfplumber"
        return chunks
    except ImportError:
        pass
    reader = _pypdf_reader(path)
    backends["text"] = "pypdf"
    return [(reader.pages[p - 1].extract_text() or "") for p in pages]


# ---- 観測 → 意味 ------------------------------------------------------------
def make_hints(result):
    hints = []
    creator = f"{result.get('creator') or ''} {result.get('producer') or ''}"
    if SLIDE_CREATORS.search(creator):
        hints.append("slide_deck")
    size = result.get("page_size")
    if size:
        w, h = size["width_pt"], size["height_pt"]
        if w > h and (abs(w / h - 16 / 9) < 0.05 or abs(w / h - 4 / 3) < 0.05):
            hints.append("slide_size")
        elif h > w and 560 <= w <= 640 and 780 <= h <= 860:  # A4・Letter の縦
            hints.append("document_size")
    if not result["has_text"]:
        hints.append("no_text")
    if result["images"]:
        hints.append("has_raster_images")
    return hints


def probe(path, max_pages):
    import os
    if not os.path.isfile(path):
        raise Stop("FILE_NOT_FOUND", f"ファイルがありません: {path}", "パスを確かめてください")
    backends, degraded = {}, []
    reader = None
    if shutil.which("pdfinfo"):
        result = info_poppler(path)
        backends["info"] = "pdfinfo"
    else:
        reader = _pypdf_reader(path)
        result = info_pypdf(reader)
        backends["info"] = "pypdf"
        degraded.append("pdfinfo が無いので pypdf で調べた（ページの大きさは全ページを調べた）")
    limit = max_pages
    if result["pages"] > limit:
        degraded.append(f"{result['pages']} ページのうち先頭 {limit} ページだけ文字・画像を調べた（--max-pages で変更）")

    fonts = fonts_poppler(path) if shutil.which("pdffonts") else None
    if fonts is not None:
        has_text, text_pages = bool(fonts), None
        backends["text"] = "pdffonts"
    else:
        reader = reader or _pypdf_reader(path)
        text_pages = text_pages_pypdf(reader, limit)
        has_text = bool(text_pages)
        backends["text"] = "pypdf"
        degraded.append("pdffonts が無いので、文字の有無は pypdf の本文抽出で調べた（フォントの一覧は無し）")
    imgs = images_poppler(path) if shutil.which("pdfimages") else None
    if imgs is not None:
        backends["images"] = "pdfimages"
    else:
        reader = reader or _pypdf_reader(path)
        imgs = images_pypdf(reader, limit)
        backends["images"] = "pypdf"
        degraded.append("pdfimages が無いので、画像は pypdf で数えた（位置は無し。ベクター図は出ない）")

    result.update({"file": path, "has_text": has_text, "text_pages": text_pages,
                   "fonts": fonts, "images": imgs})
    result["hints"] = make_hints(result)
    result["backends"] = backends
    result["degraded"] = degraded
    return result


def main(argv=None):
    setup_stdio()
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdf", help="調べる PDF")
    ap.add_argument("--text", action="store_true", help="JSON の代わりに本文を出す")
    ap.add_argument("--pages", help="--text の対象ページ（例: 3,5-7。既定は全ページ）")
    ap.add_argument("--max-pages", type=int, default=100,
                    help="文字・画像の有無を調べるページ数の上限（既定 100）")
    args = ap.parse_args(argv)
    try:
        result = probe(args.pdf, args.max_pages)
        if args.text:
            pages = parse_pages(args.pages, result["pages"])
            backends = {}
            chunks = extract_text(args.pdf, pages, backends)
            if backends.get("text") != "pdftotext":
                print(f"[warn] pdftotext が無いので {backends.get('text')} で本文を取り出した"
                      "（段組みの再現は劣る）", file=sys.stderr)
            for p, c in zip(pages, chunks):
                print(f"<!-- page {p} -->")
                print(c.rstrip())
                print()
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Stop as e:
        print(f"[stop] {e.message}", file=sys.stderr)
        print(f"[next] {e.next_step}", file=sys.stderr)
        print(f"REASON={e.code}", file=sys.stderr)
        return e.exit_code


if __name__ == "__main__":
    sys.exit(main())
