"""P3（Office を必ず動く形に）のテスト: 経路の選び方・直接変換の補足・soffice まわり・旧形式。

外部への通信なし。pptx・xlsx・docx はその場で作る。LibreOffice は無くてよい
（soffice の代わりに、引数を記録して出力ファイルを作る偽物のシェルスクリプトを使う）。
"""
import json
import os
import stat
import subprocess
import sys

import pytest

pytest.importorskip("pptx")
pytest.importorskip("openpyxl")

import _common  # noqa: E402
import office_extract  # noqa: E402
from fetch_convert import choose_route  # noqa: E402
from office_fixtures import make_docx_with_image, make_pptx, make_xlsx  # noqa: E402

OLE_HEAD = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64
posix_only = pytest.mark.skipif(sys.platform == "win32", reason="偽の soffice は sh で書いた")


@pytest.fixture(scope="session")
def office(tmp_path_factory):
    d = tmp_path_factory.mktemp("office")
    return {"dir": str(d),
            "pptx": make_pptx(d / "deck.pptx", d),
            "xlsx": make_xlsx(d / "book.xlsx", d),
            "docx": make_docx_with_image(d / "doc.docx", d)}


def _run(scripts_dir, script, *args, env_extra=None, cwd=None):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "cp932"  # Windows の既定を模す
    env.pop("URL_TO_MD_DEBUG", None)
    env["SOFFICE_PATH"] = "/nonexistent/soffice"  # 既定では soffice を見えなくする
    env.update(env_extra or {})
    return subprocess.run([sys.executable, os.path.join(scripts_dir, script), *args],
                          capture_output=True, env=env, cwd=cwd)


def _err(r):
    return r.stderr.decode("utf-8", "replace")


def _last(r):
    return _err(r).strip().splitlines()[-1]


FAKE_SOFFICE = """#!/bin/sh
# 偽の soffice: 引数を $FAKE_SOFFICE_LOG に記録し、--outdir に変換後のファイルを作る
echo "$@" >> "$FAKE_SOFFICE_LOG"
[ -n "$FAKE_SOFFICE_FAIL" ] && { echo "boom" >&2; exit 1; }
while [ $# -gt 0 ]; do
  case "$1" in
    --convert-to) target="$2"; shift 2;;
    --outdir) outdir="$2"; shift 2;;
    -env:*|--*) shift;;
    *) src="$1"; shift;;
  esac
done
name=$(basename "$src"); stem="${name%.*}"
mkdir -p "$outdir"
if [ "$target" = "pdf" ]; then
  printf '%%PDF-1.4\\nfake\\n%%%%EOF\\n' > "$outdir/$stem.pdf"
else
  cp "$FAKE_SOFFICE_COPY" "$outdir/$stem.$target"
fi
"""


@pytest.fixture
def fake_soffice(tmp_path):
    path = tmp_path / "fake_soffice"
    path.write_text(FAKE_SOFFICE, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "soffice.log"
    return {"env": {"SOFFICE_PATH": str(path), "FAKE_SOFFICE_LOG": str(log)}, "log": log}


# ---- 経路の選び方 -----------------------------------------------------------
@pytest.mark.parametrize("fmt, route, has_soffice, expected", [
    ("docx", "auto", True, "pdf"), ("docx", "auto", False, "direct"),
    ("pptx", "auto", True, "pdf"), ("pptx", "auto", False, "direct"),
    ("xlsx", "auto", True, "direct"), ("xlsx", "auto", False, "direct"),   # Excel は直接が既定
    ("pdf", "auto", True, "pdf"), ("pdf", "auto", False, "pdf"),
    ("html", "auto", True, "direct"), ("html", "pdf", True, "direct"),
    ("xlsx", "pdf", True, "pdf"), ("pptx", "direct", True, "direct"),
    ("pptx", "pdf", False, "pdf"),   # 経路は pdf のまま。soffice が無ければ後で MISSING_TOOL
])
def test_choose_route(fmt, route, has_soffice, expected):
    assert choose_route(fmt, route, has_soffice) == expected


# ---- office_extract: pptx ---------------------------------------------------
def test_extract_pptx(office, tmp_path):
    r = office_extract.extract(office["pptx"], assets_dir=str(tmp_path / "assets"))
    assert r["format"] == "pptx"
    labels = [o["label"] for o in r["owners"]]
    assert labels == ["スライド 1", "スライド 2", "スライド 3"]
    s2 = r["owners"][1]
    chart = s2["charts"][0]
    assert chart["type"] == "縦棒グラフ" and chart["complete"]
    assert [s["name"] for s in chart["series"]] == ["今期", "前期"]
    assert chart["series"][0]["categories"] == ["東日本", "西日本", "海外"]
    assert [float(v) for v in chart["series"][0]["values"]] == [120, 95, 60]
    assert s2["images"][0]["alt"] == "photo.png"
    assert os.path.getsize(s2["images"][0]["path"]) > 0
    assert s2["notes"] == ["ここで海外の伸びを強調する。"]
    assert s2["unreproduced"] == {"図形（矢印・枠・図解など）": 2}
    assert [o["hidden"] for o in r["owners"]] == [False, False, True]


def test_extract_without_assets_dir_writes_nothing(office, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    r = office_extract.extract(office["pptx"])
    assert r["images_dir"] is None and r["owners"][1]["images"][0]["path"] is None
    assert os.listdir(tmp_path) == []


def test_extract_assets_dir_is_created_only_when_needed(office, tmp_path):
    made = []

    def lazy():
        made.append(1)
        return str(tmp_path / "a")
    office_extract.extract(office["xlsx"], assets_dir=lazy)
    assert made == [1]
    plain = tmp_path / "plain.xlsx"
    from openpyxl import Workbook
    wb = Workbook()
    wb.active["A1"] = "x"
    wb.save(str(plain))
    office_extract.extract(str(plain), assets_dir=lazy)
    assert made == [1]  # 画像が無いので呼ばれない


# ---- office_extract: xlsx（グラフの数値がキャッシュに無い）------------------
def test_extract_xlsx_resolves_chart_values_from_cells(office):
    r = office_extract.extract(office["xlsx"])
    sheet = r["owners"][0]
    assert sheet["label"] == "シート「売上」"
    chart = sheet["charts"][0]
    assert chart["title"] == "地域別の売上" and chart["complete"]
    assert [s["name"] for s in chart["series"]] == ["今期", "前期"]
    assert [float(v) for v in chart["series"][1]["values"]] == [100, 90, 40]
    assert sheet["images"][0]["alt"] == "Picture"
    hidden = r["owners"][1]
    assert hidden["label"] == "シート「メモ」" and hidden["hidden"] is True


def test_extract_docx_image(office, tmp_path):
    r = office_extract.extract(office["docx"], assets_dir=str(tmp_path / "a"))
    body = r["owners"][0]
    assert body["label"] == "本文" and body["charts"] == []
    assert body["images"][0]["name"] == "image1.png"


def test_chart_types_pie_and_bar(tmp_path):
    from pptx import Presentation
    from pptx.chart.data import CategoryChartData
    from pptx.enum.chart import XL_CHART_TYPE
    from pptx.util import Inches
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[5])
    data = CategoryChartData()
    data.categories = ["A", "B"]
    data.add_series("構成比", (0.25, 0.75))
    for kind in (XL_CHART_TYPE.PIE, XL_CHART_TYPE.BAR_CLUSTERED, XL_CHART_TYPE.LINE):
        gf = slide.shapes.add_chart(kind, Inches(1), Inches(1), Inches(4), Inches(3), data)
        if kind == XL_CHART_TYPE.PIE:
            gf.chart.has_title = True
            gf.chart.chart_title.text_frame.text = "構成比の図"
    path = str(tmp_path / "c.pptx")
    prs.save(path)
    charts = office_extract.extract(path)["owners"][0]["charts"]
    assert [c["type"] for c in charts] == ["円グラフ", "横棒グラフ", "折れ線グラフ"]
    assert charts[0]["title"] == "構成比の図"
    assert [float(v) for v in charts[0]["series"][0]["values"]] == [0.25, 0.75]


def test_extract_rejects_non_office(tmp_path):
    f = tmp_path / "a.docx"
    f.write_text("ただの文章", encoding="utf-8")
    with pytest.raises(_common.StopReason) as e:
        office_extract.extract(str(f))
    assert e.value.code == "UNSUPPORTED_FORMAT"


def test_render_markdown_always_states_unreproduced(office):
    md = office_extract.render_markdown(office_extract.extract(office["docx"]))
    assert "### この変換で再現できなかったもの" in md and "ありませんでした" in md
    md = office_extract.render_markdown(office_extract.extract(office["pptx"]))
    assert "スライド 2: 図形（矢印・枠・図解など） 2 個" in md


def test_chart_values_missing_are_reported_not_hidden(office, tmp_path, monkeypatch):
    # openpyxl が読めない環境を模す: 数値を取り出せないグラフは、数えて明記する
    monkeypatch.setattr(office_extract._CellResolver, "values", lambda self, f: None)
    r = office_extract.extract(office["xlsx"])
    assert r["owners"][0]["unreproduced"] == {"数値を取り出せなかったグラフ": 1}
    md = office_extract.render_markdown(r)
    assert "数値を取り出せませんでした" in md and "数値を取り出せなかったグラフ 1 個" in md


# ---- 直接変換（soffice が見えない状態）--------------------------------------
@pytest.mark.parametrize("key", ["pptx", "xlsx"])
def test_direct_conversion_without_soffice(scripts_dir, office, tmp_path, key):
    out = tmp_path / "result.md"
    r = _run(scripts_dir, "fetch_convert.py", "--file", office[key], "-o", str(out),
             cwd=str(tmp_path))
    assert r.returncode == 0, _err(r)
    assert "ROUTE=direct" in _err(r) and r.stdout == b""
    md = out.read_text(encoding="utf-8")
    assert "| 東日本 | 120" in md                          # グラフの数値が表で出る
    assert "## 補足: 画面表示から得られる情報" in md
    assert "### この変換で再現できなかったもの" in md        # 必ず明記する
    assets = tmp_path / "result_assets"
    assert (assets / "image1.png").is_file()
    assert "| result_assets/image1.png |" in md             # -o からの相対パス
    assert "メモ" in md if key == "xlsx" else "スライド 3" in md  # 非表示の注記


def test_direct_pptx_has_notes_hidden_and_shapes(scripts_dir, office):
    r = _run(scripts_dir, "fetch_convert.py", "--file", office["pptx"], "--outdir",
             os.path.join(office["dir"], "o1"))
    assert r.returncode == 0, _err(r)
    md = r.stdout.decode("utf-8")
    assert "ここで海外の伸びを強調する。" in md and "非表示" in md
    assert "図形（矢印・枠・図解など） 2 個" in md
    assert os.path.join(office["dir"], "o1", "deck_assets", "image1.png").replace(os.sep, "/") in md


def test_direct_without_outdir_puts_images_in_temp(scripts_dir, office):
    r = _run(scripts_dir, "fetch_convert.py", "--file", office["xlsx"])
    assert r.returncode == 0, _err(r)
    assert "一時フォルダ" in _err(r)
    path = [ln for ln in r.stdout.decode("utf-8").splitlines() if "image1.png" in ln][0]
    image = path.split("|")[2].strip()
    try:
        assert os.path.isabs(image) and os.path.getsize(image) > 0
    finally:
        os.remove(image)
        os.rmdir(os.path.dirname(image))


def test_no_supplement_flag(scripts_dir, office, tmp_path):
    r = _run(scripts_dir, "fetch_convert.py", "--file", office["pptx"], "--no-supplement",
             "--outdir", str(tmp_path))
    assert r.returncode == 0, _err(r)
    assert "補足" not in r.stdout.decode("utf-8") and list(tmp_path.iterdir()) == []


def test_supplement_failure_is_stated_in_the_markdown(scripts_dir, office, tmp_path):
    # 補足の中で例外が起きても、本文は出し、補足が無いことを本文に書く
    code = ("import sys; sys.path.insert(0, %r)\n"
            "import office_extract, fetch_convert\n"
            "def boom(*a, **k): raise RuntimeError('x')\n"
            "office_extract.extract = boom\n"
            "sys.argv = ['fetch_convert.py', '--file', %r, '--route', 'direct']\n"
            "fetch_convert.run_main(fetch_convert.main)\n" % (scripts_dir, office["pptx"]))
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, env=env)
    assert r.returncode == 0, _err(r)
    assert "補足を作れませんでした" in r.stdout.decode("utf-8")
    assert "[warn]" in _err(r)


# ---- soffice の経路 ---------------------------------------------------------
@posix_only
def test_auto_route_uses_pdf_when_soffice_exists(scripts_dir, office, fake_soffice, tmp_path):
    outdir = tmp_path / "pdfs"
    r = _run(scripts_dir, "fetch_convert.py", "--file", office["pptx"], "--outdir", str(outdir),
             env_extra={**fake_soffice["env"], "FAKE_SOFFICE_COPY": office["pptx"]})
    assert r.returncode == 0, _err(r)
    assert "ROUTE=pdf" in _err(r)
    assert r.stdout.decode("utf-8").strip() == str(outdir / "deck.pdf")
    log = fake_soffice["log"].read_text(encoding="utf-8")
    assert "-env:UserInstallation=file:///" in log          # 専用プロファイル
    assert "--headless" in log and "--convert-to pdf" in log


@posix_only
def test_xlsx_stays_direct_even_with_soffice(scripts_dir, office, fake_soffice, tmp_path):
    r = _run(scripts_dir, "fetch_convert.py", "--file", office["xlsx"], "--outdir", str(tmp_path),
             env_extra=fake_soffice["env"])
    assert r.returncode == 0, _err(r)
    assert "ROUTE=direct" in _err(r) and "| 東日本 | 120" in r.stdout.decode("utf-8")
    assert not fake_soffice["log"].exists()                  # soffice は呼んでいない


@posix_only
def test_route_pdf_forces_pdf_for_xlsx_and_route_direct_for_pptx(scripts_dir, office, fake_soffice,
                                                                  tmp_path):
    env = {**fake_soffice["env"], "FAKE_SOFFICE_COPY": office["xlsx"]}
    r = _run(scripts_dir, "fetch_convert.py", "--file", office["xlsx"], "--route", "pdf",
             "--outdir", str(tmp_path), env_extra=env)
    assert r.returncode == 0 and "ROUTE=pdf" in _err(r)
    r = _run(scripts_dir, "fetch_convert.py", "--file", office["pptx"], "--route", "direct",
             "--outdir", str(tmp_path), env_extra=env)
    assert r.returncode == 0 and "ROUTE=direct" in _err(r)


@posix_only
def test_pdf_passthrough_is_an_alias_of_route_pdf(scripts_dir, office, fake_soffice, tmp_path):
    r = _run(scripts_dir, "fetch_convert.py", "--file", office["docx"], "--pdf-passthrough",
             "--outdir", str(tmp_path), env_extra=fake_soffice["env"])
    assert r.returncode == 0, _err(r)
    assert "ROUTE=pdf" in _err(r) and r.stdout.decode("utf-8").strip().endswith("doc.pdf")


@posix_only
def test_soffice_failure_is_convert_failed(scripts_dir, office, fake_soffice, tmp_path):
    r = _run(scripts_dir, "fetch_convert.py", "--file", office["pptx"], "--outdir", str(tmp_path),
             env_extra={**fake_soffice["env"], "FAKE_SOFFICE_FAIL": "1"})
    assert r.returncode == 3 and _last(r) == "REASON=CONVERT_FAILED"
    assert "boom" in _err(r) and "Traceback" not in _err(r)


def test_route_pdf_without_soffice_is_missing_tool(scripts_dir, office, tmp_path):
    if _common.find_soffice():
        pytest.skip("この環境には soffice がある")
    r = _run(scripts_dir, "fetch_convert.py", "--file", office["pptx"], "--route", "pdf",
             "--outdir", str(tmp_path / "o"), env_extra={"SOFFICE_PATH": ""})
    assert r.returncode == 4 and _last(r) == "REASON=MISSING_TOOL"
    assert r.stdout == b"" and not (tmp_path / "o").exists()


# ---- 旧形式 -----------------------------------------------------------------
def test_legacy_without_soffice(scripts_dir, tmp_path):
    if _common.find_soffice():
        pytest.skip("この環境には soffice がある")
    old = tmp_path / "old.xls"
    old.write_bytes(OLE_HEAD)
    r = _run(scripts_dir, "fetch_convert.py", "--file", str(old), env_extra={"SOFFICE_PATH": ""})
    assert r.returncode == 3 and _last(r) == "REASON=LEGACY_NEEDS_SOFFICE"
    assert "新しい形式" in _err(r) and r.stdout == b""


@posix_only
@pytest.mark.parametrize("name, copy_key, expect", [
    ("old.xls", "xlsx", "| 東日本 | 120"),
    ("old.ppt", "pptx", "ここで海外の伸びを強調する。"),
])
def test_legacy_is_upgraded_with_soffice(scripts_dir, office, fake_soffice, tmp_path, name,
                                         copy_key, expect):
    old = tmp_path / name
    old.write_bytes(OLE_HEAD)
    r = _run(scripts_dir, "fetch_convert.py", "--file", str(old), "--outdir", str(tmp_path / "o"),
             env_extra={**fake_soffice["env"], "FAKE_SOFFICE_COPY": office[copy_key]})
    assert r.returncode == 0, _err(r)
    assert "旧形式" in _err(r)
    assert "--convert-to " + copy_key in fake_soffice["log"].read_text(encoding="utf-8")
    if copy_key == "xlsx":
        assert "ROUTE=direct" in _err(r) and expect in r.stdout.decode("utf-8")
    else:  # pptx は soffice があるので PDF の経路
        assert "ROUTE=pdf" in _err(r) and r.stdout.decode("utf-8").strip().endswith(".pdf")


@posix_only
def test_unknown_ole_with_soffice_is_unsupported(scripts_dir, office, fake_soffice, tmp_path):
    f = tmp_path / "mystery"
    f.write_bytes(OLE_HEAD)
    r = _run(scripts_dir, "fetch_convert.py", "--file", str(f), env_extra=fake_soffice["env"])
    assert r.returncode == 3 and _last(r) == "REASON=UNSUPPORTED_FORMAT"


# ---- 契約 -------------------------------------------------------------------
def test_help_mentions_route_and_new_reason(scripts_dir):
    text = _run(scripts_dir, "fetch_convert.py", "--help").stdout.decode("utf-8")
    for word in ("--route", "--no-supplement", "ROUTE=pdf", "ROUTE=direct",
                 "LEGACY_NEEDS_SOFFICE", "SOFFICE_PATH", "_assets/"):
        assert word in text, word


def test_office_extract_cli_and_help(scripts_dir, office, tmp_path):
    text = _run(scripts_dir, "office_extract.py", "--help").stdout.decode("utf-8")
    for word in ("--assets-dir", "--md-dir", "--json", "終了コード", "REASON=", "UTF-8"):
        assert word in text, word
    r = _run(scripts_dir, "office_extract.py", office["pptx"], "--json")
    assert r.returncode == 0, _err(r)
    data = json.loads(r.stdout.decode("utf-8"))
    assert data["format"] == "pptx" and len(data["owners"]) == 3
    r = _run(scripts_dir, "office_extract.py", str(tmp_path / "no-such.pptx"))
    assert r.returncode == 2
    bad = tmp_path / "x.pptx"
    bad.write_text("not a zip", encoding="utf-8")
    r = _run(scripts_dir, "office_extract.py", str(bad))
    assert r.returncode == 3 and _last(r) == "REASON=UNSUPPORTED_FORMAT"


def test_frontmatter_conversion(scripts_dir, tmp_path):
    body = tmp_path / "b.md"
    body.write_text("# 題\n\n本文の段落です。\n", encoding="utf-8")
    base = ["add_frontmatter.py", str(body), "--url", "https://e.com/a", "--format", "pptx"]
    for value in ("pdf", "direct", "model-read"):
        r = _run(scripts_dir, *base, "--conversion", value)
        assert r.returncode == 0 and f"\nconversion: {value}\n" in r.stdout.decode("utf-8")
    assert "conversion:" not in _run(scripts_dir, *base).stdout.decode("utf-8")
    assert _run(scripts_dir, *base, "--conversion", "other").returncode == 2


# ---- soffice の探索 ---------------------------------------------------------
def test_find_soffice_order(tmp_path, monkeypatch):
    fake_env = tmp_path / "env_soffice"
    fake_env.write_text("", encoding="utf-8")
    on_path = tmp_path / "bin"
    on_path.mkdir()
    exe = on_path / ("soffice.exe" if sys.platform == "win32" else "soffice")
    exe.write_text("", encoding="utf-8")
    exe.chmod(0o755)
    monkeypatch.setenv("PATH", str(on_path))
    monkeypatch.setenv("SOFFICE_PATH", str(fake_env))
    assert _common.find_soffice() == str(fake_env)           # 環境変数が先
    monkeypatch.setenv("SOFFICE_PATH", str(tmp_path / "missing"))
    assert _common.find_soffice() == str(exe)                # 次に PATH
