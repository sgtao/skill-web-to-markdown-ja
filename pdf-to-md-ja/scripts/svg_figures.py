#!/usr/bin/env python3
"""
svg_figures.py — PDFの数値図をSVGで再現する汎用ジェネレータ

段階B（生成レイヤー）専用。数値の読み取り（段階A）はVisionが行い、
ここには確定済みデータを渡す。描画は全て決定的計算で行う。

提供関数:
  donut_chart(data, center_text=None)  円・ドーナツ（ラベル衝突回避つき）
  bubble_chart(data, with_map=None)    バブル図（面積∝量）
  flow_cycle(left, right, top, bottom) 2ノード循環フロー

各関数は (svg_string, warnings_list) を返す。warnings が空なら配置OK。

CLIテスト: python3 svg_figures.py  → サンプル3図を /tmp に出力
"""
import math


# ============================================================
# 円・ドーナツグラフ（ラベル衝突回避つき）
# ============================================================
def donut_chart(data, center_top="", center_main="", cx=230, cy=230,
                R=130, W=55, font_size=13):
    """
    data: [(label, value, color), ...]  value は割合でも実数でもよい（合計で正規化）
    center_top / center_main: 中央に表示する文字（任意）
    returns: (svg_str, warnings)
    """
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
            "text": f"{label} {val}%" if total == 100 or all(
                isinstance(v, float) for _, v, _ in data) else f"{label} {val}",
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
        a0, a1 = math.radians(ang), math.radians(ang + sweep)
        xo0, yo0 = cx + R * math.cos(a0), cy + R * math.sin(a0)
        xo1, yo1 = cx + R * math.cos(a1), cy + R * math.sin(a1)
        xi1, yi1 = cx + r_in * math.cos(a1), cy + r_in * math.sin(a1)
        xi0, yi0 = cx + r_in * math.cos(a0), cy + r_in * math.sin(a0)
        large = 1 if sweep > 180 else 0
        arcs.append(
            f'<path d="M {xo0:.2f} {yo0:.2f} A {R} {R} 0 {large} 1 '
            f'{xo1:.2f} {yo1:.2f} L {xi1:.2f} {yi1:.2f} '
            f'A {r_in} {r_in} 0 {large} 0 {xi0:.2f} {yi0:.2f} Z" fill="{color}"/>')
        ang += sweep

    txts = []
    for grp in (right, left):
        for l in grp:
            lx_end = l["x"] + (40 if l["side"] == "R" else -40)
            anchor = "start" if l["side"] == "R" else "end"
            tx = lx_end + (4 if l["side"] == "R" else -4)
            txts.append(
                f'<polyline points="{l["anchor_x"]:.1f},{l["anchor_y"]:.1f} '
                f'{l["x"]:.1f},{l["y"]:.1f} {lx_end:.1f},{l["y"]:.1f}" '
                f'fill="none" stroke="#999" stroke-width="0.8"/>')
            txts.append(
                f'<text x="{tx:.1f}" y="{l["y"]:.1f}" font-size="{FS}" '
                f'text-anchor="{anchor}" dominant-baseline="middle" '
                f'font-family="sans-serif">{l["text"]}</text>')

    center = ""
    if center_top:
        center += (f'<text x="{cx}" y="{cy-10}" font-size="15" '
                   f'text-anchor="middle" font-family="sans-serif">{center_top}</text>')
    if center_main:
        center += (f'<text x="{cx}" y="{cy+18}" font-size="24" font-weight="bold" '
                   f'text-anchor="middle" font-family="sans-serif">{center_main}</text>')

    svg = (f'<svg viewBox="0 0 540 460" xmlns="http://www.w3.org/2000/svg">\n'
           f'{"".join(arcs)}\n{center}\n{"".join(txts)}\n</svg>')
    return svg, warnings


# ============================================================
# バブル図（面積 ∝ 量）
# ============================================================
def bubble_chart(data, title="", width=760, height=400, r_max=70):
    """
    data: [(label, value, x, y), ...]  x,y は配置座標（Visionで決める）
    面積を量に比例させるため半径は √(量比) を取る。
    returns: (svg_str, warnings=[])
    """
    max_v = max(v for _, v, _, _ in data)
    circles = []
    for label, v, x, y in data:
        r = r_max * math.sqrt(v / max_v)
        fs = 13 if r > 30 else 11
        circles.append(
            f'<circle cx="{x}" cy="{y}" r="{r:.1f}" fill="#1565C0" '
            f'fill-opacity="0.6" stroke="#0D47A1" stroke-width="1.5"/>'
            f'<text x="{x}" y="{y-3:.0f}" font-size="{fs}" font-weight="bold" '
            f'fill="#fff" text-anchor="middle" font-family="sans-serif">{label}</text>'
            f'<text x="{x}" y="{y+13:.0f}" font-size="11" fill="#fff" '
            f'text-anchor="middle" font-family="sans-serif">{v:,}</text>')
    head = (f'<text x="{width//2}" y="28" font-size="14" font-weight="bold" '
            f'text-anchor="middle" font-family="sans-serif">{title}</text>') if title else ""
    svg = (f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">\n'
           f'<rect width="{width}" height="{height}" fill="#F5F7FA"/>\n'
           f'{head}\n{"".join(circles)}\n</svg>')
    return svg, []


# ============================================================
# 2ノード循環フロー
# ============================================================
def flow_cycle(left_label, right_label, top_label, bottom_label,
               accent="#E60012"):
    """社員⇄会社 のような2ノード循環図。多階層フローはMermaidを使うこと。"""
    svg = f'''<svg viewBox="0 0 600 320" xmlns="http://www.w3.org/2000/svg">
<defs><marker id="arrow" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto">
<path d="M0,0 L8,3 L0,6 Z" fill="{accent}"/></marker></defs>
<polygon points="90,80 150,50 210,80 210,170 150,200 90,170" fill="#FCE9EA" stroke="{accent}" stroke-width="1.5"/>
<text x="150" y="130" font-size="20" font-weight="bold" text-anchor="middle" font-family="sans-serif">{left_label}</text>
<polygon points="390,80 450,50 510,80 510,170 450,200 390,170" fill="#FCE9EA" stroke="{accent}" stroke-width="1.5"/>
<text x="450" y="130" font-size="18" font-weight="bold" text-anchor="middle" font-family="sans-serif">{right_label}</text>
<path d="M 215 95 Q 300 55 385 95" fill="none" stroke="{accent}" stroke-width="2.5" marker-end="url(#arrow)"/>
<text x="300" y="58" font-size="12" fill="{accent}" font-weight="bold" text-anchor="middle" font-family="sans-serif">{top_label}</text>
<path d="M 385 160 Q 300 205 215 160" fill="none" stroke="#999" stroke-width="2.5" marker-end="url(#arrow)"/>
<text x="300" y="232" font-size="12" fill="{accent}" font-weight="bold" text-anchor="middle" font-family="sans-serif">{bottom_label}</text>
<text x="300" y="130" font-size="15" text-anchor="middle" font-family="sans-serif">循環</text>
</svg>'''
    return svg, []


if __name__ == "__main__":
    # サンプル出力（自己テスト）
    donut, w1 = donut_chart(
        [("日本", 64.9, "#1A0DAB"), ("欧州", 18.7, "#1A3FD4"),
         ("アジアパシフィック", 7.3, "#1565C0"), ("アメリカ", 5.4, "#1E88E5"),
         ("東アジア", 3.5, "#039BE5"), ("その他", 0.2, "#00BCD4")],
        center_top="売上収益", center_main="37,560")
    bubble, w2 = bubble_chart(
        [("日本", 67000, 480, 210), ("欧州", 19000, 150, 180),
         ("APAC", 19000, 290, 330), ("東アジア", 4000, 360, 175),
         ("米", 4000, 660, 205)], title="地域別人数（面積∝人数）")
    flow, w3 = flow_cycle("社員", "会社", "貢献", "成長を促す")
    for name, (svg, warn) in [("donut", (donut, w1)), ("bubble", (bubble, w2)),
                               ("flow", (flow, w3))]:
        open(f"/tmp/_test_{name}.svg", "w").write(svg)
        status = "✅ OK" if not warn else "❌ " + "; ".join(warn)
        print(f"[{name}] {status}  → /tmp/_test_{name}.svg")
