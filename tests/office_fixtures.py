"""テスト用の pptx・xlsx・docx を、その場で作る（外部の資料は使わない）。

pptx: 文字・表・棒グラフ・写真・図形・発表者ノートが 1 枚ずつ（2 枚目）＋非表示のスライド（3 枚目）
xlsx: 表 1 枚＋棒グラフ 1 つ＋写真 1 枚のシート、非表示のシート
docx: 見出し・段落・写真 1 枚
"""
import base64
import zipfile

# 1x1 の赤い PNG
PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg==")


def write_png(path):
    with open(path, "wb") as f:
        f.write(PNG_BYTES)
    return str(path)


def make_pptx(path, tmp_dir):
    from pptx import Presentation
    from pptx.chart.data import CategoryChartData
    from pptx.enum.chart import XL_CHART_TYPE
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.util import Inches

    prs = Presentation()
    s1 = prs.slides.add_slide(prs.slide_layouts[0])
    s1.shapes.title.text = "売上報告"
    s1.placeholders[1].text = "第 1 四半期のまとめ"

    s2 = prs.slides.add_slide(prs.slide_layouts[5])
    s2.shapes.title.text = "四半期の売上"
    data = CategoryChartData()
    data.categories = ["東日本", "西日本", "海外"]
    data.add_series("今期", (120, 95, 60))
    data.add_series("前期", (100, 90, 40))
    s2.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(0.5), Inches(1.5),
                        Inches(5), Inches(3.5), data)
    s2.shapes.add_picture(write_png(f"{tmp_dir}/photo.png"), Inches(6), Inches(1.5),
                          Inches(1), Inches(1))
    s2.shapes.add_shape(MSO_SHAPE.RIGHT_ARROW, Inches(6), Inches(3), Inches(1.5), Inches(0.8))
    s2.shapes.add_shape(MSO_SHAPE.OVAL, Inches(6), Inches(4), Inches(1), Inches(1))
    rows = s2.shapes.add_table(2, 2, Inches(0.5), Inches(5.2), Inches(4), Inches(1)).table
    rows.cell(0, 0).text = "指標"
    rows.cell(0, 1).text = "値"
    rows.cell(1, 0).text = "粗利率"
    rows.cell(1, 1).text = "31%"
    s2.notes_slide.notes_text_frame.text = "ここで海外の伸びを強調する。"

    s3 = prs.slides.add_slide(prs.slide_layouts[5])
    s3.shapes.title.text = "非表示の予備スライド"
    s3._element.set("show", "0")
    prs.save(str(path))
    return str(path)


def make_xlsx(path, tmp_dir):
    from openpyxl import Workbook
    from openpyxl.chart import BarChart, Reference
    from openpyxl.drawing.image import Image

    wb = Workbook()
    ws = wb.active
    ws.title = "売上"
    ws.append(["地域", "今期", "前期"])
    for row in (("東日本", 120, 100), ("西日本", 95, 90), ("海外", 60, 40)):
        ws.append(row)
    chart = BarChart()
    chart.title = "地域別の売上"
    chart.add_data(Reference(ws, min_col=2, max_col=3, min_row=1, max_row=4),
                   titles_from_data=True)
    chart.set_categories(Reference(ws, min_col=1, min_row=2, max_row=4))
    ws.add_chart(chart, "F2")
    ws.add_image(Image(write_png(f"{tmp_dir}/photo.png")), "F20")
    hidden = wb.create_sheet("メモ")
    hidden["A1"] = "内部用"
    hidden.sheet_state = "hidden"
    wb.save(str(path))
    return str(path)


def make_docx_with_image(path, tmp_dir):
    types = ('<?xml version="1.0" encoding="UTF-8"?>'
             '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
             '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
             '<Default Extension="xml" ContentType="application/xml"/>'
             '<Default Extension="png" ContentType="image/png"/>'
             '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-'
             'officedocument.wordprocessingml.document.main+xml"/></Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            'relationships/officeDocument" Target="word/document.xml"/></Relationships>')
    doc_rels = ('<?xml version="1.0" encoding="UTF-8"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId5" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                'relationships/image" Target="media/image1.png"/></Relationships>')
    body = ('<?xml version="1.0" encoding="UTF-8"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:body><w:p><w:r><w:t>画像つきの文書の段落です</w:t></w:r></w:p></w:body></w:document>')
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", types)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", body)
        z.writestr("word/_rels/document.xml.rels", doc_rels)
        z.writestr("word/media/image1.png", PNG_BYTES)
    return str(path)
