#!/usr/bin/env python3
"""
svg_figures.py — PDFの数値図をSVGで再現する汎用ジェネレータ

段階B（生成レイヤー）専用。数値の読み取り（段階A）はVisionが行い、
ここには確定済みデータを渡す。描画は全て決定的計算で行う。標準ライブラリだけで動く。

提供関数:
  donut_chart(data, center_top, center_main, unit="")   円・ドーナツ（ラベル衝突回避つき）
  bubble_chart(data, title="")                          バブル図（面積∝量）
  flow_cycle(left, right, top, bottom, center_label)    2ノード循環フロー

どの関数も (svg_string, warnings_list) を返す。
- ラベルは XML エスケープする（& < > を含んでよい）。
- viewBox は中身から計算する（width / height を渡したときは、その枠に対して
  はみ出しを検査し、はみ出せば warnings に入れる）。
- warnings が空なら、数値で確かめられる範囲（間隔・はみ出し・重なり）では配置OK。
  目視確認の代わりにはならないが、cairosvg が無い環境ではこれで代える。

CLI:
  svg_figures.py [--outdir DIR]
      自己テスト。サンプル3図を DIR（省略時は一時フォルダ）に出力し、warnings の有無を表示する。
  svg_figures.py --render fig.svg --png fig.png [--width 700]
      SVG を PNG にして目視確認できるようにする（cairosvg がある場合だけ）。
      cairosvg が無いときは、標準エラーの最後の 3 行に [stop] / [next] / REASON=MISSING_TOOL を出して
      終了コード 4 で終わる（その場合は目視を省き、warnings が空であることで代える）。
終了コード: 0 成功 / 2 引数の誤り / 3 SVG を読めない・PNG にできない / 4 cairosvg が無い
"""
import math
from xml.sax.saxutils import escape

PAD = 12  # viewBox の余白（px）


def _esc(text):
    return escape(str(text), {'"': "&quot;"})


def _text_width(text, font_size):
    """文字列の幅の見積もり（px）。全角は 1.0em、半角は 0.6em とする。"""
    return sum(font_size * (1.0 if ord(c) > 0x2E7F else 0.6) for c in str(text))


def _fmt(v):
    return f"{v:,}" if isinstance(v, int) else f"{v:g}"


def _check_frame(bbox, width, height, warnings):
    """bbox=(x0, y0, x1, y1) が 0..width × 0..height に収まるか。"""
    x0, y0, x1, y1 = bbox
    over = []
    if x0 < 0:
        over.append(f"左に {-x0:.0f}px")
    if y0 < 0:
        over.append(f"上に {-y0:.0f}px")
    if x1 > width:
        over.append(f"右に {x1 - width:.0f}px")
    if y1 > height:
        over.append(f"下に {y1 - height:.0f}px")
    if over:
        warnings.append("枠からはみ出し: " + "・".join(over))


def _viewbox(bbox, width, height):
    x0, y0, x1, y1 = bbox
    if width is None or height is None:
        w, h = x1 - x0 + 2 * PAD, y1 - y0 + 2 * PAD
        return f"{x0 - PAD:.0f} {y0 - PAD:.0f} {math.ceil(w)} {math.ceil(h)}"
    return f"0 0 {width} {height}"


# ============================================================
# 円・ドーナツグラフ（ラベル衝突回避つき）
# ============================================================
def donut_chart(data, center_top="", center_main="", cx=230, cy=230,
                R=130, W=55, font_size=13, unit="", width=None, height=None):
    """
    data: [(label, value, color), ...]  value は割合でも実数でもよい（合計で正規化）
    center_top / center_main: 中央に表示する文字（任意）
    unit: ラベルの値の後ろに付ける文字。割合なら "%"（既定は付けない）
    width / height: 枠を決めたいときだけ指定する。省略すると中身から viewBox を計算する
    returns: (svg_str, warnings)
    """
    if not data:
        raise ValueError("data が空です")
    if any(v < 0 for _, v, _ in data) or sum(v for _, v, _ in data) <= 0:
        raise ValueError("value は 0 以上で、合計が 0 より大きい必要があります")
    r_in = R - W
    total = sum(v for _, v, _ in data)
    FS, LINE_H = font_size, font_size + 5

    # 各ラベルの理想位置（弧の中点角・外側）
    ang = -90.0
    labels = []
    for label, val, color in data:
        sweep = val / total * 360
        amid = math.radians(ang + sweep / 2)
        side = "R" if math.cos(amid) >= 0 else "L"
        labels.append({
            "text": f"{label} {_fmt(val)}{unit}",
            "side": side,
            "anchor_x": cx + R * math.cos(amid),
            "anchor_y": cy + R * math.sin(amid),
            "ideal_y": cy + (R + 25) * math.sin(amid),
            "x": cx + (R + 25) * math.cos(amid),
        })
        ang += sweep

    # 衝突回避：左右グループ別に、先頭確定→順に最低間隔を保証して押し下げ
    def resolve(group):
        group.sort(key=lambda l: l["ideal_y"])
        for i, l in enumerate(group):
            if i == 0:
                l["y"] = l["ideal_y"]
            else:
                l["y"] = max(l["ideal_y"], group[i - 1]["y"] + LINE_H)
        return group

    right = resolve([l for l in labels if l["side"] == "R"])
    left = resolve([l for l in labels if l["side"] == "L"])

    # 検証
    warnings = []
    for name, grp in [("右", right), ("左", left)]:
        ys = sorted(l["y"] for l in grp)
        for a, b in zip(ys, ys[1:]):
            if b - a < LINE_H - 0.5:
                warnings.append(f"{name}グループ 間隔不足 {b - a:.1f}px (<{LINE_H})")

    # 描画
    arcs = []
    ang = -90.0
    for label, val, color in data:
        sweep = val / total * 360
        if sweep >= 359.99:  # 1 項目だけ：弧が閉じるので 2 つの半円で描く
            arcs.append(
                f'<path d="M {cx} {cy - R} A {R} {R} 0 1 1 {cx} {cy + R} '
                f'A {R} {R} 0 1 1 {cx} {cy - R} Z M {cx} {cy - r_in} '
                f'A {r_in} {r_in} 0 1 0 {cx} {cy + r_in} '
                f'A {r_in} {r_in} 0 1 0 {cx} {cy - r_in} Z" '
                f'fill="{_esc(color)}" fill-rule="evenodd"/>')
            break
        a0, a1 = math.radians(ang), math.radians(ang + sweep)
        xo0, yo0 = cx + R * math.cos(a0), cy + R * math.sin(a0)
        xo1, yo1 = cx + R * math.cos(a1), cy + R * math.sin(a1)
        xi1, yi1 = cx + r_in * math.cos(a1), cy + r_in * math.sin(a1)
        xi0, yi0 = cx + r_in * math.cos(a0), cy + r_in * math.sin(a0)
        large = 1 if sweep > 180 else 0
        arcs.append(
            f'<path d="M {xo0:.2f} {yo0:.2f} A {R} {R} 0 {large} 1 '
            f'{xo1:.2f} {yo1:.2f} L {xi1:.2f} {yi1:.2f} '
            f'A {r_in} {r_in} 0 {large} 0 {xi0:.2f} {yi0:.2f} Z" fill="{_esc(color)}"/>')
        ang += sweep

    x0, y0, x1, y1 = cx - R, cy - R, cx + R, cy + R
    txts = []
    for grp in (right, left):
        for l in grp:
            lx_end = l["x"] + (40 if l["side"] == "R" else -40)
            anchor = "start" if l["side"] == "R" else "end"
            tx = lx_end + (4 if l["side"] == "R" else -4)
            tw = _text_width(l["text"], FS)
            if l["side"] == "R":
                x1 = max(x1, tx + tw)
            else:
                x0 = min(x0, tx - tw)
            y0, y1 = min(y0, l["y"] - FS), max(y1, l["y"] + FS)
            txts.append(
                f'<polyline points="{l["anchor_x"]:.1f},{l["anchor_y"]:.1f} '
                f'{l["x"]:.1f},{l["y"]:.1f} {lx_end:.1f},{l["y"]:.1f}" '
                f'fill="none" stroke="#999" stroke-width="0.8"/>')
            txts.append(
                f'<text x="{tx:.1f}" y="{l["y"]:.1f}" font-size="{FS}" '
                f'text-anchor="{anchor}" dominant-baseline="middle" '
                f'font-family="sans-serif">{_esc(l["text"])}</text>')

    center = ""
    if center_top:
        center += (f'<text x="{cx}" y="{cy-10}" font-size="15" '
                   f'text-anchor="middle" font-family="sans-serif">{_esc(center_top)}</text>')
    if center_main:
        center += (f'<text x="{cx}" y="{cy+18}" font-size="24" font-weight="bold" '
                   f'text-anchor="middle" font-family="sans-serif">{_esc(center_main)}</text>')
    for t, size in ((center_top, 15), (center_main, 24)):
        if t and _text_width(t, size) > 2 * r_in * 0.9:
            warnings.append(f"中央の文字「{t}」が穴に収まらない可能性（穴の直径 {2*r_in}px）")

    bbox = (x0, y0, x1, y1)
    if width is not None and height is not None:
        _check_frame(bbox, width, height, warnings)
    svg = (f'<svg viewBox="{_viewbox(bbox, width, height)}" '
           f'xmlns="http://www.w3.org/2000/svg">\n'
           f'{"".join(arcs)}\n{center}\n{"".join(txts)}\n</svg>')
    return svg, warnings


# ============================================================
# バブル図（面積 ∝ 量）
# ============================================================
def bubble_chart(data, title="", width=None, height=None, r_max=70,
                 color="#1565C0", background="#F5F7FA"):
    """
    data: [(label, value, x, y), ...]  x,y は中心の座標（Visionで決める）
    面積を量に比例させるため半径は √(量比) を取る。
    width / height: 枠を決めたいときだけ指定する。省略すると中身から viewBox を計算する
    returns: (svg_str, warnings)  warnings は「枠からはみ出し」「バブルの重なり」
    """
    if not data:
        raise ValueError("data が空です")
    if any(v < 0 for _, v, _, _ in data):
        raise ValueError("value は 0 以上である必要があります")
    max_v = max(v for _, v, _, _ in data)
    if max_v <= 0:
        raise ValueError("value の最大が 0 です")
    warnings = []
    items = []
    for label, v, x, y in data:
        r = r_max * math.sqrt(v / max_v)
        items.append((label, v, x, y, r))

    # 重なり：中心間の距離が半径の和より小さい
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            a, b = items[i], items[j]
            d = math.hypot(a[2] - b[2], a[3] - b[3])
            if d < a[4] + b[4]:
                warnings.append(f"バブルが重なっている: 「{a[0]}」と「{b[0]}」"
                                f"（中心間 {d:.0f}px < 半径の和 {a[4] + b[4]:.0f}px）")

    circles = []
    x0 = y0 = math.inf
    x1 = y1 = -math.inf
    for label, v, x, y, r in items:
        fs = 13 if r > 30 else 11
        x0, y0 = min(x0, x - r), min(y0, y - r)
        x1, y1 = max(x1, x + r), max(y1, y + r)
        circles.append(
            f'<circle cx="{x}" cy="{y}" r="{r:.1f}" fill="{_esc(color)}" '
            f'fill-opacity="0.6" stroke="{_esc(color)}" stroke-width="1.5"/>'
            f'<text x="{x}" y="{y-3:.0f}" font-size="{fs}" font-weight="bold" '
            f'fill="#fff" text-anchor="middle" font-family="sans-serif">{_esc(label)}</text>'
            f'<text x="{x}" y="{y+13:.0f}" font-size="11" fill="#fff" '
            f'text-anchor="middle" font-family="sans-serif">{_fmt(v)}</text>')
        tw = max(_text_width(label, fs), _text_width(_fmt(v), 11))
        if tw > 2 * r:
            warnings.append(f"ラベル「{label}」が円より広い可能性"
                            f"（文字 約{tw:.0f}px > 直径 {2*r:.0f}px）")
    if title:
        y0 = min(y0, 28 - 14)
    bbox = (x0, y0, x1, y1)
    if width is not None and height is not None:
        _check_frame(bbox, width, height, warnings)
        vb = f"0 0 {width} {height}"
        bg = f'<rect width="{width}" height="{height}" fill="{_esc(background)}"/>'
        title_x = width // 2
    else:
        vb = _viewbox(bbox, None, None)
        vx, vy, vw, vh = (float(t) for t in vb.split())
        bg = (f'<rect x="{vx:.0f}" y="{vy:.0f}" width="{vw:.0f}" height="{vh:.0f}" '
              f'fill="{_esc(background)}"/>')
        title_x = vx + vw / 2
    head = (f'<text x="{title_x:.0f}" y="28" font-size="14" font-weight="bold" '
            f'text-anchor="middle" font-family="sans-serif">{_esc(title)}</text>'
            ) if title else ""
    svg = (f'<svg viewBox="{vb}" xmlns="http://www.w3.org/2000/svg">\n'
           f'{bg}\n{head}\n{"".join(circles)}\n</svg>')
    return svg, warnings


# ============================================================
# 2ノード循環フロー
# ============================================================
def flow_cycle(left_label, right_label, top_label, bottom_label,
               center_label="", accent="#1565C0"):
    """2ノードが循環する図（例: A ⇄ B）。多階層フローは Mermaid を使うこと。
    returns: (svg_str, warnings)  warnings はノード・矢印のラベルが入りきらない可能性"""
    warnings = []
    for name, text, size, limit in (
            ("左ノード", left_label, 20, 120), ("右ノード", right_label, 18, 120),
            ("上の矢印", top_label, 12, 170), ("下の矢印", bottom_label, 12, 170)):
        w = _text_width(text, size)
        if w > limit:
            warnings.append(f"{name}のラベル「{text}」が入りきらない可能性"
                            f"（文字 約{w:.0f}px > {limit}px）")
    centre = (f'<text x="300" y="130" font-size="15" text-anchor="middle" '
              f'font-family="sans-serif">{_esc(center_label)}</text>') if center_label else ""
    acc = _esc(accent)
    svg = f'''<svg viewBox="0 0 600 320" xmlns="http://www.w3.org/2000/svg">
<defs><marker id="arrow" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto">
<path d="M0,0 L8,3 L0,6 Z" fill="{acc}"/></marker></defs>
<polygon points="90,80 150,50 210,80 210,170 150,200 90,170" fill="#EEF3FB" stroke="{acc}" stroke-width="1.5"/>
<text x="150" y="130" font-size="20" font-weight="bold" text-anchor="middle" font-family="sans-serif">{_esc(left_label)}</text>
<polygon points="390,80 450,50 510,80 510,170 450,200 390,170" fill="#EEF3FB" stroke="{acc}" stroke-width="1.5"/>
<text x="450" y="130" font-size="18" font-weight="bold" text-anchor="middle" font-family="sans-serif">{_esc(right_label)}</text>
<path d="M 215 95 Q 300 55 385 95" fill="none" stroke="{acc}" stroke-width="2.5" marker-end="url(#arrow)"/>
<text x="300" y="58" font-size="12" fill="{acc}" font-weight="bold" text-anchor="middle" font-family="sans-serif">{_esc(top_label)}</text>
<path d="M 385 160 Q 300 205 215 160" fill="none" stroke="#999" stroke-width="2.5" marker-end="url(#arrow)"/>
<text x="300" y="232" font-size="12" fill="{acc}" font-weight="bold" text-anchor="middle" font-family="sans-serif">{_esc(bottom_label)}</text>
{centre}
</svg>'''
    return svg, warnings


def _selftest(outdir=None):
    import os
    import tempfile
    outdir = outdir or tempfile.gettempdir()  # 一時フォルダは OS に合わせる（Windows に /tmp は無い）
    donut, w1 = donut_chart(
        [("項目A", 62.5, "#1A237E"), ("項目B", 20.0, "#1565C0"),
         ("項目C", 8.0, "#1E88E5"), ("項目D", 5.5, "#039BE5"),
         ("項目E", 3.5, "#00ACC1"), ("その他", 0.5, "#80DEEA")],
        center_top="合計", center_main="1,000", unit="%")
    bubble, w2 = bubble_chart(
        [("A", 60000, 450, 210), ("B", 20000, 160, 180),
         ("C", 20000, 290, 330), ("D", 4000, 350, 130),
         ("E", 4000, 640, 205)], title="サンプル（面積∝量）",
        width=760, height=400)
    flow, w3 = flow_cycle("左", "右", "行き", "戻り", center_label="循環")
    results = []
    for name, (svg, warn) in [("donut", (donut, w1)), ("bubble", (bubble, w2)),
                               ("flow", (flow, w3))]:
        path = os.path.join(outdir, f"_test_{name}.svg")
        with open(path, "w", encoding="utf-8") as f:  # SVG は UTF-8 で書く
            f.write(svg)
        results.append((name, warn, path))
    return results


def _render(svg_path, png_path, width):
    import sys

    def stop(code, msg, nxt, rc):
        print(f"[stop] {msg}", file=sys.stderr)
        print(f"[next] {nxt}", file=sys.stderr)
        print(f"REASON={code}", file=sys.stderr)
        return rc
    try:
        import cairosvg
    except (ImportError, OSError):  # cairo の DLL が無いときは OSError
        return stop("MISSING_TOOL", "cairosvg が使えないため、SVG を画像にできません",
                    "目視確認は省き、warnings が空であることで代えて、返答に「目視は省略した」と書いてください", 4)
    try:
        cairosvg.svg2png(url=svg_path, write_to=png_path, output_width=width)
    except Exception as e:
        return stop("CONVERT_FAILED", f"SVG を PNG にできませんでした（{type(e).__name__}）",
                    "SVG が正しい XML か確かめてください", 3)
    print(png_path)
    return 0


if __name__ == "__main__":
    import argparse
    import sys
    # Windows の cp932 でも日本語のパスや文字を表示できるようにする
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="サンプル3図を出力して、warnings の有無を表示する（自己テスト）")
    ap.add_argument("--outdir", help="SVG の出力先フォルダ（省略時は一時フォルダ）")
    ap.add_argument("--render", metavar="SVG", help="この SVG を PNG にする（--png が必要）")
    ap.add_argument("--png", help="--render の出力先 PNG")
    ap.add_argument("--width", type=int, default=700, help="--render の PNG の幅（既定 700）")
    args = ap.parse_args()
    if args.render:
        if not args.png:
            ap.error("--render には --png が必要です")
        sys.exit(_render(args.render, args.png, args.width))
    for name, warn, path in _selftest(args.outdir):
        status = "OK" if not warn else "NG " + "; ".join(warn)
        print(f"[{name}] {status}  -> {path}")
