"""テスト用の PDF を、その場で作る（外部の道具に頼らない）。"""


def _build(objects, info=None):
    """objects: {番号: バイト列}（番号は 1 から連続）。Catalog は 1 番、Info は最後に足す。"""
    objs = dict(objects)
    info_no = None
    if info:
        info_no = max(objs) + 1
        body = "".join(f"/{k} ({v})" for k, v in info.items())
        objs[info_no] = f"<<{body}>>".encode("latin-1")
    out = bytearray(b"%PDF-1.4\n")
    offsets = {}
    for no in sorted(objs):
        offsets[no] = len(out)
        out += f"{no} 0 obj\n".encode() + objs[no] + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {max(objs) + 1}\n0000000000 65535 f \n".encode()
    for no in range(1, max(objs) + 1):
        out += f"{offsets[no]:010d} 00000 n \n".encode()
    trailer = f"trailer\n<</Size {max(objs) + 1}/Root 1 0 R"
    if info_no:
        trailer += f"/Info {info_no} 0 R"
    out += (trailer + f">>\nstartxref\n{xref}\n%%EOF\n").encode()
    return bytes(out)


def make_pdf(path, pages=1, size=(960, 540), texts=None, image=None, info=None):
    """texts: ページごとの文字列（英数字のみ。None のページは空白）。
    image: (幅, 高さ) を渡すと、各ページに塗りつぶしの RGB 画像を 1 つ置く。"""
    texts = texts or [None] * pages
    kids = " ".join(f"{3 + 3 * i} 0 R" for i in range(pages))
    objs = {1: b"<</Type/Catalog/Pages 2 0 R>>",
            2: f"<</Type/Pages/Kids[{kids}]/Count {pages}>>".encode()}
    font_no = 3 + 3 * pages
    img_no = font_no + 1
    for i in range(pages):
        page_no, cont_no = 3 + 3 * i, 4 + 3 * i
        stream = b""
        if texts[i]:
            stream += f"BT /F1 24 Tf 72 400 Td ({texts[i]}) Tj ET\n".encode()
        res = f"/Font<</F1 {font_no} 0 R>>"
        if image:
            stream += b"q 100 0 0 100 100 100 cm /Im1 Do Q\n"
            res += f"/XObject<</Im1 {img_no} 0 R>>"
        objs[page_no] = (f"<</Type/Page/Parent 2 0 R/MediaBox[0 0 {size[0]} {size[1]}]"
                         f"/Contents {cont_no} 0 R/Resources<<{res}>>>>").encode()
        objs[cont_no] = f"<</Length {len(stream)}>>\nstream\n".encode() + stream + b"endstream"
        objs[5 + 3 * i] = b"null"  # 番号を詰めるための空き
    objs[font_no] = b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>"
    if image:
        w, h = image
        data = bytes([200, 30, 30]) * (w * h)
        objs[img_no] = (f"<</Type/XObject/Subtype/Image/Width {w}/Height {h}"
                        f"/ColorSpace/DeviceRGB/BitsPerComponent 8/Length {len(data)}>>\nstream\n"
                        ).encode() + data + b"\nendstream"
    for no in range(1, max(objs) + 1):
        objs.setdefault(no, b"null")
    with open(path, "wb") as f:
        f.write(_build(objs, info))
    return path
