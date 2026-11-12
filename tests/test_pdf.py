"""P4: pdf-to-md-ja のスクリプト（pdf_probe・pdf_render・svg_figures）のテスト。

poppler が無い環境を模すため、shutil.which を None にして Python ライブラリの経路を通す。
"""
import glob
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

import pdf_probe
import pdf_render
import svg_figures
from pdf_fixtures import make_pdf


@pytest.fixture
def no_poppler(monkeypatch):
    """poppler の道具が無いことにする。"""
    real = pdf_probe.shutil.which
    monkeypatch.setattr(pdf_probe.shutil, "which",
                        lambda n, *a, **k: None if n.startswith("pdf") else real(n, *a, **k))
    # pdf_render と pdf_probe は同じ shutil モジュールを見る


def _run(pdf_scripts_dir, script, *args):
    env = {**os.environ, "PYTHONIOENCODING": "cp932"}
    return subprocess.run([sys.executable, os.path.join(pdf_scripts_dir, script), *args],
                          capture_output=True, env=env)


# ---- pdf_probe -------------------------------------------------------------
def test_probe_without_poppler_reports_shape(tmp_path, no_poppler):
    pdf = make_pdf(str(tmp_path / "s.pdf"), pages=2, texts=["Hello", None],
                   info={"Creator": "Microsoft PowerPoint"})
    r = pdf_probe.probe(pdf, 100)
    assert r["pages"] == 2
    assert r["page_size"] == {"width_pt": 960.0, "height_pt": 540.0}
    assert r["creator"] == "Microsoft PowerPoint"
    assert r["has_text"] is True and r["text_pages"] == [1]
    assert r["fonts"] is None
    assert r["backends"] == {"info": "pypdf", "text": "pypdf", "images": "pypdf"}
    assert {"slide_deck", "slide_size"} <= set(r["hints"])
    assert r["degraded"], "poppler が無いことを degraded に書く"


def test_probe_no_text_and_images(tmp_path, no_poppler):
    pdf = make_pdf(str(tmp_path / "i.pdf"), size=(595, 842), image=(4, 3))
    r = pdf_probe.probe(pdf, 100)
    assert r["has_text"] is False
    assert r["images"] == [{"page": 1, "width": 4, "height": 3}]
    assert "no_text" in r["hints"] and "has_raster_images" in r["hints"]
    assert "document_size" in r["hints"]


def test_probe_respects_max_pages(tmp_path, no_poppler):
    pdf = make_pdf(str(tmp_path / "m.pdf"), pages=3, texts=["a", "b", "c"])
    r = pdf_probe.probe(pdf, 2)
    assert r["text_pages"] == [1, 2]
    assert any("先頭 2 ページ" in d for d in r["degraded"])


def test_probe_cli_json_is_utf8(tmp_path, pdf_scripts_dir):
    pdf = make_pdf(str(tmp_path / "s.pdf"), texts=["Hello"])
    p = _run(pdf_scripts_dir, "pdf_probe.py", pdf)
    assert p.returncode == 0
    import json
    assert json.loads(p.stdout.decode("utf-8"))["pages"] == 1


@pytest.mark.parametrize("content, reason", [(b"not a pdf at all", "PDF_UNREADABLE")])
def test_probe_unreadable(tmp_path, pdf_scripts_dir, content, reason):
    f = tmp_path / "bad.pdf"
    f.write_bytes(content)
    p = _run(pdf_scripts_dir, "pdf_probe.py", str(f))
    assert p.returncode == 3
    assert p.stderr.decode().strip().splitlines()[-1] == f"REASON={reason}"
    assert "Traceback" not in p.stderr.decode()


def test_probe_missing_file(tmp_path, pdf_scripts_dir):
    p = _run(pdf_scripts_dir, "pdf_probe.py", str(tmp_path / "none.pdf"))
    assert p.returncode == 3
    assert p.stderr.decode().strip().splitlines()[-1] == "REASON=FILE_NOT_FOUND"


def test_probe_text_option(tmp_path, pdf_scripts_dir):
    pdf = make_pdf(str(tmp_path / "t.pdf"), pages=2, texts=["Alpha", "Beta"])
    p = _run(pdf_scripts_dir, "pdf_probe.py", pdf, "--text", "--pages", "2")
    out = p.stdout.decode("utf-8")
    assert "Beta" in out and "Alpha" not in out


def test_parse_pages():
    assert pdf_probe.parse_pages("1,3-4,9", 5) == [1, 3, 4]
    assert pdf_probe.parse_pages(None, 3) == [1, 2, 3]


# ---- pdf_render ------------------------------------------------------------
def test_render_without_poppler_uses_pypdfium2(tmp_path, no_poppler, capsys):
    pdf = make_pdf(str(tmp_path / "r.pdf"), pages=2, texts=["Hello", "World"], size=(300, 200))
    assert pdf_render.main([pdf, "--pages", "2", "--dpi", "72", "--outdir", str(tmp_path / "o")]) == 0
    captured = capsys.readouterr()
    paths = captured.out.split()
    assert len(paths) == 1 and paths[0].endswith("r_p002.png") and os.path.getsize(paths[0]) > 0
    assert "RENDERER=pypdfium2" in captured.err
    from PIL import Image
    assert Image.open(paths[0]).size == (300, 200)


def test_extract_images_without_poppler(tmp_path, no_poppler, capsys):
    pdf = make_pdf(str(tmp_path / "e.pdf"), image=(4, 3))
    assert pdf_render.main([pdf, "--extract-images", "--outdir", str(tmp_path / "o")]) == 0
    captured = capsys.readouterr()
    assert "EXTRACTOR=pypdf" in captured.err
    assert captured.out.split()[0].endswith(".png")


def test_extract_images_none_is_reported(tmp_path, no_poppler, capsys):
    pdf = make_pdf(str(tmp_path / "n.pdf"), texts=["Hello"])
    assert pdf_render.main([pdf, "--extract-images", "--outdir", str(tmp_path / "o")]) == 3
    assert capsys.readouterr().err.strip().splitlines()[-1] == "REASON=NO_IMAGES"


def test_render_pages_out_of_range(tmp_path, capsys):
    pdf = make_pdf(str(tmp_path / "x.pdf"))
    assert pdf_render.main([pdf, "--pages", "9", "--outdir", str(tmp_path / "o")]) == 3
    assert "REASON=PDF_UNREADABLE" in capsys.readouterr().err


# ---- svg_figures -----------------------------------------------------------
DATA = [("A&B", 50, "#111"), ("<C>", 30, "#222"), ('D"E', 20, "#333")]


def _parse(svg):
    return ET.fromstring(svg)  # 整形式の XML でなければ例外


def test_labels_are_escaped_and_xml_is_valid():
    svg, _ = svg_figures.donut_chart(DATA, center_top="売上&利益", center_main="<1>")
    _parse(svg)
    assert "A&amp;B" in svg and "&lt;C&gt;" in svg and "<C>" not in svg
    svg, _ = svg_figures.bubble_chart([("X&Y", 10, 100, 100), ("<Z>", 5, 300, 100)], title="a<b")
    _parse(svg)
    svg, _ = svg_figures.flow_cycle("a&b", "<c>", "d", "e", center_label="&")
    _parse(svg)


def test_viewbox_is_computed_from_content():
    svg, w = svg_figures.donut_chart(DATA)
    vb = [float(x) for x in re.search(r'viewBox="([^"]+)"', svg).group(1).split()]
    assert vb[2] > 2 * 130 and vb[3] > 2 * 130
    assert not w
    # 長いラベルを足すと、viewBox の幅が広がる
    wide = DATA + [("とても長いラベルのテスト項目", 10, "#444")]
    vb2 = [float(x) for x in re.search(r'viewBox="([^"]+)"',
           svg_figures.donut_chart(wide)[0]).group(1).split()]
    assert vb2[2] > vb[2]


def test_unit_is_explicit_not_guessed():
    svg, _ = svg_figures.donut_chart([("a", 60.5, "#1"), ("b", 39.5, "#2")])
    assert "a 60.5<" in svg and "%" not in svg
    svg, _ = svg_figures.donut_chart([("a", 60.5, "#1"), ("b", 39.5, "#2")], unit="%")
    assert "a 60.5%<" in svg
    # 合計が 100 でも、unit を渡さなければ % は付かない
    svg, _ = svg_figures.donut_chart([("a", 60, "#1"), ("b", 40, "#2")])
    assert "%" not in svg


def test_frame_overflow_is_warned():
    _, w = svg_figures.donut_chart(DATA, width=200, height=200)
    assert any("はみ出し" in m for m in w)
    _, w = svg_figures.bubble_chart([("A", 10, 20, 20)], width=100, height=100, r_max=70)
    assert any("はみ出し" in m for m in w)
    _, w = svg_figures.bubble_chart([("A", 10, 150, 150)], width=300, height=300, r_max=70)
    assert not any("はみ出し" in m for m in w)


def test_bubble_overlap_is_warned():
    _, w = svg_figures.bubble_chart([("A", 100, 100, 100), ("B", 100, 150, 100)])
    assert any("重なって" in m for m in w)
    _, w = svg_figures.bubble_chart([("A", 100, 100, 100), ("B", 100, 400, 100)])
    assert not any("重なって" in m for m in w)


def test_bubble_area_is_proportional():
    svg, _ = svg_figures.bubble_chart([("A", 400, 100, 100), ("B", 100, 400, 100)], r_max=80)
    r = [float(x) for x in re.findall(r'<circle[^>]* r="([\d.]+)"', svg)]
    assert r[0] == pytest.approx(80, abs=0.1) and r[1] == pytest.approx(40, abs=0.1)


def test_invalid_data_is_rejected():
    for bad in ([], [("a", 0, "#1")], [("a", -1, "#1"), ("b", 5, "#2")]):
        with pytest.raises(ValueError):
            svg_figures.donut_chart(bad)
    with pytest.raises(ValueError):
        svg_figures.bubble_chart([])


def test_dense_donut_labels_do_not_collide():
    data = [(f"項目{i}", 1, "#123") for i in range(14)]
    _, w = svg_figures.donut_chart(data)
    assert not any("間隔不足" in m for m in w)


def test_single_item_donut_is_valid():
    svg, _ = svg_figures.donut_chart([("only", 10, "#1")])
    _parse(svg)


def test_selftest_writes_utf8_svg(tmp_path):
    results = svg_figures._selftest(str(tmp_path))
    assert [r[0] for r in results] == ["donut", "bubble", "flow"]
    for _, warn, path in results:
        assert warn == []
        _parse(open(path, encoding="utf-8").read())


# ---- SKILL.md --------------------------------------------------------------
def test_pdf_skill_md_options_exist_in_help(pdf_scripts_dir):
    skill = os.path.dirname(pdf_scripts_dir)
    helps = ""
    for s in ("pdf_probe.py", "pdf_render.py", "svg_figures.py"):
        r = _run(pdf_scripts_dir, s, "--help")
        assert r.returncode == 0, s
        helps += r.stdout.decode("utf-8", "replace")
    texts = [open(os.path.join(skill, "SKILL.md"), encoding="utf-8").read()]
    texts += [open(p, encoding="utf-8").read() for p in glob.glob(os.path.join(skill, "references", "*.md"))]
    missing = [o for t in texts for o in sorted(set(re.findall(r"(?<![\w-])--[a-z][a-z-]+", t)))
               if o not in helps and o not in ("--help",)]
    assert not missing, missing


def test_pdf_skill_has_no_tmp_or_hardcoded_python(pdf_scripts_dir):
    skill = os.path.dirname(pdf_scripts_dir)
    for p in [os.path.join(skill, "SKILL.md")] + glob.glob(os.path.join(skill, "references", "*.md")):
        t = open(p, encoding="utf-8").read()
        assert "/tmp" not in t, p
        assert "python3 " not in t, p
        assert "Mermai**" not in t, p
        assert "✅" not in t and "❌" not in t, p


def test_render_option_without_cairosvg_stops_cleanly(tmp_path, pdf_scripts_dir):
    svg = tmp_path / "f.svg"
    svg.write_text(svg_figures.flow_cycle("a", "b", "c", "d")[0], encoding="utf-8")
    p = _run(pdf_scripts_dir, "svg_figures.py", "--render", str(svg), "--png", str(tmp_path / "f.png"))
    try:
        import cairosvg  # noqa: F401
        assert p.returncode == 0 and (tmp_path / "f.png").exists()
    except (ImportError, OSError):
        assert p.returncode == 4
        assert p.stderr.decode().strip().splitlines()[-1] == "REASON=MISSING_TOOL"
        assert "Traceback" not in p.stderr.decode()


def test_tidy_text():
    assert pdf_probe.tidy("   \n\n  a  \n\n\n\n b\n  \n") == "  a\n\n b"
