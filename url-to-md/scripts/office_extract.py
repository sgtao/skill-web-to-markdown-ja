#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "openpyxl",
# ]
# ///
"""
office_extract.py — Office ファイル（docx / xlsx / pptx）から、直接変換では落ちる
「画面で見えている情報」を取り出して補足する。

直接変換（markitdown）で得られるのは文字と表が中心で、グラフ・写真・図形・配置は落ちる。
これを次の 3 つで補う（確定した方針 D-4）。
  1. グラフ    : 種類・題名・系列・項目・数値を取り出して表にする
  2. 画像      : ファイルに埋め込まれた画像を取り出し、どのスライド／シートにあるかの表を出す
  3. 再現できなかったもの: 図形・SmartArt・数式・動画などを「数」で明記する（黙って落とさない）
  あわせて、発表者ノートと、非表示のスライド・シートの有無も出す。

ファイル（ZIP）の中身を直接読むため、python-pptx には依存しない。
グラフの数値がキャッシュに無い xlsx だけ、セルの値を openpyxl で読む（無ければ取り出せない扱い）。

使い方（くわしい契約は --help）:
  uv run office_extract.py <file> [--assets-dir DIR] [--md-dir DIR] [--json]
"""
import argparse
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from posixpath import dirname as pdirname, join as pjoin, normpath

from _common import StopReason, contract_epilog, run_main, sniff_format, write_stdout

REASON_CODES = ("UNSUPPORTED_FORMAT", "EMPTY_CONTENT", "CONVERT_FAILED")

R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"

CHART_TYPE_NAMES = {
    "barChart": "棒グラフ", "bar3DChart": "3D 棒グラフ", "lineChart": "折れ線グラフ",
    "line3DChart": "3D 折れ線グラフ", "pieChart": "円グラフ", "pie3DChart": "3D 円グラフ",
    "ofPieChart": "補助円グラフ付き円グラフ", "doughnutChart": "ドーナツグラフ",
    "areaChart": "面グラフ", "area3DChart": "3D 面グラフ", "scatterChart": "散布図",
    "bubbleChart": "バブルチャート", "radarChart": "レーダーチャート",
    "stockChart": "株価チャート", "surfaceChart": "曲面グラフ", "surface3DChart": "3D 曲面グラフ",
}


def _local(tag):
    """'{ns}name' → 'name'。"""
    return tag.rsplit("}", 1)[-1]


def _children(el, name):
    return [c for c in el if _local(c.tag) == name]


def _child(el, name):
    found = _children(el, name)
    return found[0] if found else None


def _descendants(el, name):
    return [d for d in el.iter() if _local(d.tag) == name]


def _texts(el):
    """要素の中の a:t（文字）を段落ごとにつないで返す。"""
    paras = []
    for p in _descendants(el, "p"):
        text = "".join(t.text or "" for t in _descendants(p, "t"))
        if text.strip():
            paras.append(text.strip())
    return paras


class Package:
    """OOXML（ZIP）を読む。関係（rels）をたどって、部品どうしのつながりを調べる。"""

    def __init__(self, path):
        self.path = path
        self.z = zipfile.ZipFile(path)
        self.names = set(self.z.namelist())
        self._xml = {}

    def xml(self, part):
        if part not in self._xml:
            try:
                self._xml[part] = ET.fromstring(self.z.read(part))
            except (KeyError, ET.ParseError):
                self._xml[part] = None
        return self._xml[part]

    def rels(self, part):
        """部品の関係を {id: (type, 絶対パス)} で返す。"""
        rel_part = pjoin(pdirname(part), "_rels", part.rsplit("/", 1)[-1] + ".rels")
        root = self.xml(rel_part)
        out = {}
        if root is None:
            return out
        for r in root:
            if r.get("TargetMode") == "External":
                continue
            target = r.get("Target", "")
            full = target.lstrip("/") if target.startswith("/") else normpath(
                pjoin(pdirname(part), target))
            out[r.get("Id")] = (r.get("Type", "").rsplit("/", 1)[-1], full)
        return out

    def read(self, part):
        return self.z.read(part)


def _owners(pkg, fmt):
    """スライド・シート・本文の一覧を [(ラベル, 部品, 非表示か)] で返す。"""
    if fmt == "docx":
        return [("本文", "word/document.xml", False)]
    if fmt == "pptx":
        root = pkg.xml("ppt/presentation.xml")
        rels = pkg.rels("ppt/presentation.xml")
        out = []
        lst = _child(root, "sldIdLst") if root is not None else None
        for i, sld in enumerate(lst if lst is not None else [], 1):
            target = rels.get(sld.get(f"{{{R_NS}}}id"))
            if target:
                root_s = pkg.xml(target[1])
                hidden = root_s is not None and root_s.get("show") == "0"
                out.append((f"スライド {i}", target[1], hidden))
        return out
    root = pkg.xml("xl/workbook.xml")
    rels = pkg.rels("xl/workbook.xml")
    out = []
    sheets = _child(root, "sheets") if root is not None else None
    for sh in (sheets if sheets is not None else []):
        target = rels.get(sh.get(f"{{{R_NS}}}id"))
        if target:
            out.append((f"シート「{sh.get('name')}」", target[1],
                        sh.get("state", "visible") != "visible"))
    return out


def _related_parts(pkg, owner):
    """所有者から（描画を 1 段はさんで）たどれる、グラフ・画像・ノート・描画の部品を集める。"""
    found = {"chart": [], "media": [], "notes": [], "drawing": []}
    seen = {owner}
    queue = [(owner, 0)]
    while queue:
        part, depth = queue.pop(0)
        for _rid, (kind, target) in pkg.rels(part).items():
            if target in seen or target not in pkg.names:
                continue
            seen.add(target)
            if kind == "chart":
                found["chart"].append(target)
            elif kind == "image":
                found["media"].append(target)
            elif kind == "notesSlide":
                found["notes"].append(target)
            elif kind == "drawing" and depth < 2:
                found["drawing"].append(target)
                queue.append((target, depth + 1))
    return found


# ---- グラフ -----------------------------------------------------------------
_FORMULA_RE = re.compile(r"^(?:'((?:[^']|'')+)'|([^!]+))!(.+)$")


class _CellResolver:
    """グラフの数値がキャッシュに無い xlsx のために、セルの値を読む（openpyxl があれば）。"""

    def __init__(self, path, enabled):
        self.path, self.enabled, self._wb = path, enabled, None

    def values(self, formula):
        if not self.enabled:
            return None
        try:
            if self._wb is None:
                from openpyxl import load_workbook
                self._wb = load_workbook(self.path, data_only=True)
            m = _FORMULA_RE.match(formula.strip())
            if not m:
                return None
            sheet = (m.group(1) or m.group(2)).replace("''", "'")
            ref = m.group(3).replace("$", "")
            cells = self._wb[sheet][ref]
            if not isinstance(cells, tuple):
                return [cells.value]
            flat = []
            for row in cells:
                flat.extend(row if isinstance(row, tuple) else (row,))
            return [c.value for c in flat]
        except Exception:  # noqa: BLE001 - 読めなければ「取り出せなかった」として扱う
            return None


def _cache_points(ref_el):
    """strRef / numRef / multiLvlStrRef のキャッシュから値のリストを作る（無ければ None）。"""
    if ref_el is None:
        return None
    for el in ref_el.iter():
        if _local(el.tag) in ("strCache", "numCache"):
            count = _child(el, "ptCount")
            n = int(count.get("val")) if count is not None and count.get("val") else 0
            vals = {}
            for pt in _children(el, "pt"):
                v = _child(pt, "v")
                vals[int(pt.get("idx"))] = v.text if v is not None else None
            n = max(n, (max(vals) + 1) if vals else 0)
            return [vals.get(i) for i in range(n)]
    return None


def _formula(ref_el):
    f = _descendants(ref_el, "f") if ref_el is not None else []
    return f[0].text if f else None


def _series_values(ser, name, resolver):
    holder = _child(ser, name)
    if holder is None:
        return None
    ref = next((c for c in holder if _local(c.tag) in ("numRef", "strRef", "multiLvlStrRef")),
               None)
    if ref is None:  # numLit / strLit
        lit = next((c for c in holder if _local(c.tag) in ("numLit", "strLit")), None)
        return _cache_points(lit) if lit is not None else None
    vals = _cache_points(ref)
    if vals is None or all(v is None for v in vals):
        f = _formula(ref)
        vals = resolver.values(f) if f else vals
    return vals


def _number(v):
    """表示用: 数値は 120.0 → 120 に整える。"""
    if v is None:
        return ""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return str(int(f)) if f == int(f) else f"{f:g}"


def parse_chart(xml_root, resolver):
    """グラフの XML から、種類・題名・系列・項目・数値を取り出す。

    Returns:
        {"type", "title", "series": [{"name", "categories", "values"}], "complete"}
        complete が False なら、一部の数値を取り出せなかった。
    """
    chart = _child(xml_root, "chart") if xml_root is not None else None
    plot = _child(chart, "plotArea") if chart is not None else None
    if plot is None:
        return {"type": "不明", "title": "", "series": [], "complete": False}
    title_el = _child(chart, "title")
    title = " ".join(_texts(title_el)) if title_el is not None else ""
    plots = [c for c in plot if _local(c.tag).endswith("Chart")]
    names = []
    for pl in plots:
        label = CHART_TYPE_NAMES.get(_local(pl.tag), _local(pl.tag))
        if _local(pl.tag) == "barChart":
            bar_dir = _child(pl, "barDir")
            label = "横棒グラフ" if bar_dir is not None and bar_dir.get("val") == "bar" else "縦棒グラフ"
        names.append(label)
    series, complete = [], True
    for pl in plots:
        for ser in _children(pl, "ser"):
            tx = _child(ser, "tx")
            sname = ""
            if tx is not None:
                tv = _cache_points(tx)
                if tv is None:
                    f = _formula(tx)
                    tv = resolver.values(f) if f else None
                if tv:
                    sname = str(tv[0]) if tv[0] is not None else ""
                else:
                    v = _child(tx, "v")
                    sname = v.text if v is not None and v.text else ""
            is_xy = _local(pl.tag) in ("scatterChart", "bubbleChart")
            cats = _series_values(ser, "xVal" if is_xy else "cat", resolver)
            vals = _series_values(ser, "yVal" if is_xy else "val", resolver)
            if vals is None or all(v is None for v in vals):
                complete = False
            series.append({"name": sname, "categories": cats, "values": vals})
    kind = names[0] if len(set(names)) == 1 else ("複合グラフ（" + "・".join(dict.fromkeys(names)) + "）"
                                               if names else "不明")
    return {"type": kind, "title": title, "series": series, "complete": complete and bool(series)}


# ---- 画像・数え上げ ---------------------------------------------------------
def _parent_map(root):
    return {c: p for p in root.iter() for c in p}


def _image_alts(pkg, part):
    """部品の中の画像（blip）について、{画像の部品: 代替テキスト} を返す。"""
    root = pkg.xml(part)
    if root is None:
        return {}
    rels = pkg.rels(part)
    parents = _parent_map(root)
    alts = {}
    for blip in _descendants(root, "blip"):
        rid = blip.get(f"{{{R_NS}}}embed") or blip.get(f"{{{R_NS}}}link")
        target = rels.get(rid)
        if not target:
            continue
        alt, node = "", blip
        for _ in range(6):
            node = parents.get(node)
            if node is None:
                break
            for d in node.iter():
                if _local(d.tag) in ("cNvPr", "docPr") and d.get("descr"):
                    alt = alt or d.get("descr")
            if alt:
                break
        alts.setdefault(target[1], alt)
    return alts


def count_unreproduced(root):
    """直接変換でも、取り出しでも再現できない要素を数える（おおよその数）。"""
    c = Counter()
    for el in root.iter():
        name = _local(el.tag)
        if name == "sp" or name == "wsp":
            is_textbox = False
            nv = _child(el, "nvSpPr")
            cnv = _child(nv, "cNvSpPr") if nv is not None else None
            if cnv is not None and cnv.get("txBox") == "1":
                is_textbox = True
            placeholder = nv is not None and any(
                _local(d.tag) == "ph" for d in nv.iter())
            if not is_textbox and not placeholder and any(
                    _local(d.tag) in ("prstGeom", "custGeom") for d in el.iter()):
                c["図形（矢印・枠・図解など）"] += 1
        elif name == "cxnSp":
            c["線・コネクタ"] += 1
        elif name == "graphicData":
            uri = el.get("uri", "")
            if "diagram" in uri:
                c["SmartArt"] += 1
        elif name in ("oleObj", "OLEObject"):
            c["埋め込みオブジェクト（OLE）"] += 1
        elif name == "oMath":
            c["数式"] += 1
        elif name in ("videoFile", "audioFile", "wavAudioFile"):
            c["動画・音声"] += 1
        elif name == "pict":
            c["図形（旧形式）"] += 1
    return c


def _slug(label):
    return re.sub(r"[^0-9A-Za-z一-龥ぁ-んァ-ヶ]+", "_", label).strip("_")


def extract(path, assets_dir=None):
    """Office ファイルから、グラフ・画像・ノート・非表示・再現できなかった要素を取り出す。

    Args:
        path: docx / xlsx / pptx のファイル。
        assets_dir: 取り出した画像を保存するフォルダ（文字列、またはフォルダを返す関数。
            画像があるときだけ作る）。None なら保存せず、一覧だけ作る。

    Returns:
        {"format", "owners": [{"label", "hidden", "notes", "charts", "images",
        "unreproduced"}], "images_dir"}
    """
    fmt = sniff_format(path)
    if fmt not in ("docx", "xlsx", "pptx"):
        raise StopReason("UNSUPPORTED_FORMAT",
                         f"docx / xlsx / pptx ではありません（判定: {fmt or '不明'}）: {path}")
    pkg = Package(path)
    resolver = _CellResolver(path, enabled=(fmt == "xlsx"))
    result = {"format": fmt, "owners": [], "images_dir": None}
    saved = {}

    def save_image(part):
        if assets_dir is None:
            return None
        if part not in saved:
            if result["images_dir"] is None:
                result["images_dir"] = assets_dir() if callable(assets_dir) else assets_dir
                os.makedirs(result["images_dir"], exist_ok=True)
            dest = os.path.join(result["images_dir"], part.rsplit("/", 1)[-1])
            with open(dest, "wb") as f:
                f.write(pkg.read(part))
            saved[part] = dest
        return saved[part]

    for label, part, hidden in _owners(pkg, fmt):
        rel = _related_parts(pkg, part)
        owner = {"label": label, "hidden": hidden, "notes": [], "charts": [],
                 "images": [], "unreproduced": {}}
        for notes_part in rel["notes"]:
            root = pkg.xml(notes_part)
            for sp in (_descendants(root, "sp") if root is not None else []):
                ph = next((d for d in sp.iter() if _local(d.tag) == "ph"), None)
                if ph is not None and ph.get("type") == "body":
                    owner["notes"] += _texts(sp)
        counts = Counter()
        for chart_part in rel["chart"]:
            parsed = parse_chart(pkg.xml(chart_part), resolver)
            owner["charts"].append(parsed)
        alts = {}
        for p in [part] + rel["drawing"]:
            alts.update(_image_alts(pkg, p))
            root = pkg.xml(p)
            if root is not None:
                counts.update(count_unreproduced(root))
        for media in rel["media"]:
            dest = save_image(media)
            owner["images"].append({"name": media.rsplit("/", 1)[-1], "alt": alts.get(media, ""),
                                    "path": dest})
        failed = sum(1 for c in owner["charts"] if not c["complete"])
        if failed:
            counts["数値を取り出せなかったグラフ"] += failed
        owner["unreproduced"] = dict(counts)
        result["owners"].append(owner)
    return result


# ---- Markdown ---------------------------------------------------------------
def _cell(v):
    return str(v).replace("|", "\\|").replace("\n", " ")


def _chart_table(chart):
    """系列を列、項目を行にした Markdown の表を作る。"""
    series = [s for s in chart["series"] if s["values"] is not None]
    if not series:
        return []
    n = max(len(s["values"]) for s in series)
    cats = next((s["categories"] for s in series if s["categories"]), None)
    header = ["項目"] + [s["name"] or f"系列{i + 1}" for i, s in enumerate(series)]
    rows = ["| " + " | ".join(_cell(h) for h in header) + " |",
            "|" + "|".join("---" for _ in header) + "|"]
    for i in range(n):
        label = cats[i] if cats and i < len(cats) and cats[i] is not None else i + 1
        cells = [_cell(_number(label))] + [
            _cell(_number(s["values"][i]) if i < len(s["values"]) else "") for s in series]
        rows.append("| " + " | ".join(cells) + " |")
    return rows


def _image_ref(img, md_dir):
    if not img["path"]:
        return img["name"]
    if md_dir:
        return os.path.relpath(img["path"], md_dir).replace(os.sep, "/")
    return os.path.abspath(img["path"]).replace(os.sep, "/")


def render_markdown(result, md_dir=None, source_name=""):
    """extract の結果を、Markdown の補足（本文の末尾に足す節）にする。

    再現できなかったもの（節の最後）は、何も無くても必ず書く。
    """
    owners = result["owners"]
    out = ["", "---", "", "## 補足: 画面表示から得られる情報（office_extract.py）", "",
           "直接変換では、文字と表だけが本文に入ります。グラフの元データ・埋め込み画像・"
           "再現できなかったものを、以下に補足します。配置・色・書式は再現していません。", ""]
    charts = [(o["label"], c) for o in owners for c in o["charts"]]
    out.append("### グラフ")
    out.append("")
    if not charts:
        out += ["（グラフはありません）", ""]
    for label, chart in charts:
        title = f"「{chart['title']}」" if chart["title"] else "（題名なし）"
        out.append(f"#### {label} / {chart['type']} {title}")
        out.append("")
        table = _chart_table(chart)
        out += (table if table else ["（数値を取り出せませんでした）"]) + [""]
    images = [(o["label"], i) for o in owners for i in o["images"]]
    out += ["### 埋め込み画像", ""]
    if not images:
        out += ["（画像はありません）", ""]
    else:
        out += ["画像として見て、内容を説明文か Mermaid に起こしてください。"
                "本文中の `![…](ファイル名)` は、変換器が付けた仮の名前で、実在のファイルではありません。",
                "", "| 場所 | 画像ファイル | 代替テキスト |", "|---|---|---|"]
        for label, img in images:
            out.append(f"| {_cell(label)} | {_cell(_image_ref(img, md_dir))} | {_cell(img['alt'])} |")
        out.append("")
    notes = [(o["label"], o["notes"]) for o in owners if o["notes"]]
    if notes:
        out += ["### 発表者ノート", ""]
        for label, paras in notes:
            out += [f"- {label}: " + " / ".join(paras)]
        out.append("")
    hidden = [o["label"] for o in owners if o["hidden"]]
    if hidden:
        out += ["### 非表示", "",
                "次は非表示です（本文には含まれています）: " + "、".join(hidden), ""]
    total = Counter()
    for o in owners:
        for k, v in o["unreproduced"].items():
            total[k] += v
    out += ["### この変換で再現できなかったもの", ""]
    if not total:
        out += ["- 数えられた範囲では、図形・SmartArt・数式などはありませんでした。", ""]
    else:
        for o in owners:
            if o["unreproduced"]:
                detail = "、".join(f"{k} {v} 個" for k, v in o["unreproduced"].items())
                out.append(f"- {o['label']}: {detail}")
        out += ["", "上の要素の見た目は、本文にも画像にも含まれていません（PDF 経由なら再現できます）。", ""]
    return "\n".join(out)


def build_parser():
    ap = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "docx / xlsx / pptx から、直接変換では落ちる情報（グラフの元データ・埋め込み画像・\n"
            "発表者ノート・非表示・再現できなかった要素の数）を取り出す。\n"
            "\n"
            "入力:\n"
            "  <file>           docx / xlsx / pptx のファイル（形式は中身で判定する）\n"
            "出力:\n"
            "  標準出力         Markdown の補足（既定）。--json なら JSON\n"
            "  --assets-dir     取り出した画像を保存するフォルダ（画像があるときだけ作る）。\n"
            "                   省略時は画像を保存せず、一覧だけ出す\n"
            "  --md-dir         補足の中の画像のパスを、このフォルダからの相対パスにする。\n"
            "                   省略時は絶対パス"),
        epilog=contract_epilog(
            REASON_CODES, "Markdown の補足（--json のときは JSON）",
            notes=["補足:", "  fetch_convert.py の直接変換は、この補足を本文の末尾に自動で足す。"]))
    ap.add_argument("file", help="docx / xlsx / pptx のファイル")
    ap.add_argument("--assets-dir", default=None, metavar="DIR",
                    help="取り出した画像の保存先（省略時は保存しない）")
    ap.add_argument("--md-dir", default=None, metavar="DIR",
                    help="画像のパスの基点（省略時は絶対パス）")
    ap.add_argument("--json", action="store_true", help="Markdown ではなく JSON で出す")
    return ap


def main():
    ap = build_parser()
    args = ap.parse_args()
    if not os.path.isfile(args.file):
        ap.error(f"ファイルが見つかりません: {args.file}")
    if os.path.getsize(args.file) == 0:
        raise StopReason("EMPTY_CONTENT", f"ファイルが空でした: {args.file}")
    try:
        result = extract(args.file, assets_dir=args.assets_dir)
    except zipfile.BadZipFile:
        raise StopReason("CONVERT_FAILED", f"ファイルを開けませんでした（壊れている可能性）: {args.file}") from None
    if args.json:
        write_stdout(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    else:
        write_stdout(render_markdown(result, md_dir=args.md_dir) + "\n")


if __name__ == "__main__":
    run_main(main)
