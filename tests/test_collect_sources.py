"""collect_sources.py の検査（合格条件 5）。見本は、このファイルの中で作る（バイナリの見本を置かない）。

実行: uv run --python 3.12 --with pytest python -m pytest -q tests/test_collect_sources.py
"""
import hashlib
import io
import json
import os
import stat
import subprocess
import sys
import zipfile

import pytest

from conftest import SCRIPTS

SCRIPT = os.path.join(SCRIPTS, "collect_sources.py")
sys.path.insert(0, SCRIPTS)
import collect_sources as cs  # noqa: E402

JA = "これは日本語の本文です。解説メモの入力として使う、ごく短い文章です。"


def run(*args, cwd=None):
    r = subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, cwd=cwd)
    return r.returncode, r.stdout.decode("utf-8"), r.stderr.decode("utf-8")


def reason(err):
    for line in err.splitlines():
        if line.startswith("REASON="):
            return line.split("=", 1)[1]
    return None


def load(out):
    return json.loads((out / ".work" / "sources.json").read_text(encoding="utf-8"))


def prep(src, out, *extra):
    return run("prepare", str(src), "--out-dir", str(out), "--slides", "6",
               "--audience", "初めて読む人", *extra)


def pad(text, n=60):
    """30 字未満で止まらないよう、本文を伸ばす。"""
    return (text + " ") * (n // len(text) + 1)


# ---- 文字コード（5 種）---------------------------------------------------------
ENCODINGS = {
    "utf8.txt": JA.encode("utf-8"),
    "utf8bom.txt": b"\xef\xbb\xbf" + JA.encode("utf-8"),
    "utf16bom.txt": b"\xff\xfe" + JA.encode("utf-16-le"),
    "cp932.txt": JA.encode("cp932"),
    "eucjp.txt": JA.encode("euc_jp"),
}


def test_five_encodings_decode_without_mojibake(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    for name, data in ENCODINGS.items():
        (src / name).write_bytes(data)
    out = tmp_path / "out"
    code, _, err = prep(src, out)
    assert code == 0, err
    data = load(out)
    by_path = {i["path"]: i for i in data["items"]}
    assert set(by_path) == set(ENCODINGS), "5 種の文字コードの見本が全部対象になる"
    for name in ENCODINGS:
        md = (out / by_path[name]["md_path"]).read_text(encoding="utf-8")
        assert JA in md, f"{name} が文字化けしている"


def test_utf16_without_bom_is_binary_and_skipped(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "u16.txt").write_bytes(JA.encode("utf-16-le"))
    (src / "ok.txt").write_text(pad(JA), encoding="utf-8")
    out = tmp_path / "out"
    assert prep(src, out)[0] == 0
    data = load(out)
    assert [i["path"] for i in data["items"]] == ["ok.txt"]
    # 日本語だけの BOM 無し UTF-16 には NUL が出ないので、NUL の判定ではなく文字コードの判定で飛ぶ
    skipped = {s["path"]: s["reason"] for s in data["skipped"]}
    assert skipped["u16.txt"] in ("BINARY", "ENCODING_UNKNOWN")


def test_unknown_encoding_is_skipped(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "bad.txt").write_bytes(b"\x81\x20\xff\xfe\xfd" * 20)
    (src / "ok.txt").write_text(pad(JA), encoding="utf-8")
    out = tmp_path / "out"
    assert prep(src, out)[0] == 0
    skipped = {s["path"]: s["reason"] for s in load(out)["skipped"]}
    assert skipped.get("bad.txt") in ("ENCODING_UNKNOWN", "BINARY")


# ---- 読まないもの ----------------------------------------------------------------
def test_secrets_management_dirs_and_hidden_are_skipped(tmp_path):
    src = tmp_path / "src"
    for d in (".git", "node_modules", "__pycache__", "dist", "build", "secrets"):
        (src / d).mkdir(parents=True)
        (src / d / "x.txt").write_text(pad(JA), encoding="utf-8")
    for f in (".env", "id_rsa", "credentials.json", "server.pem", ".DS_Store", "._x.txt"):
        (src / f).write_text(pad(JA), encoding="utf-8")
    (src / "keep.txt").write_text(pad(JA), encoding="utf-8")
    out = tmp_path / "out"
    assert prep(src, out)[0] == 0
    data = load(out)
    assert [i["path"] for i in data["items"]] == ["keep.txt"]
    reasons = {s["path"]: s["reason"] for s in data["skipped"]}
    assert reasons[".env"] == "SECRET" and reasons["secrets/x.txt"] == "SECRET"
    assert reasons[".git/x.txt"] == "MANAGEMENT_DIR" and reasons["dist/x.txt"] == "MANAGEMENT_DIR"


def test_symlink_is_not_followed(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (tmp_path / "outside.txt").write_text(pad(JA), encoding="utf-8")
    os.symlink(tmp_path / "outside.txt", src / "link.txt")
    (src / "keep.txt").write_text(pad(JA), encoding="utf-8")
    out = tmp_path / "out"
    assert prep(src, out)[0] == 0
    data = load(out)
    assert [i["path"] for i in data["items"]] == ["keep.txt"]
    assert {"path": "link.txt", "reason": "SYMLINK"} in data["skipped"]


def test_too_short_and_too_large_text_are_skipped(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "short.txt").write_text("短い", encoding="utf-8")
    (src / "big.txt").write_text("あ" * (2 * 1024 * 1024 + 10), encoding="utf-8")
    (src / "ok.txt").write_text(pad(JA), encoding="utf-8")
    out = tmp_path / "out"
    assert prep(src, out)[0] == 0
    skipped = {s["path"]: s["reason"] for s in load(out)["skipped"]}
    assert skipped["short.txt"] == "TOO_SHORT" and skipped["big.txt"] == "TOO_LARGE"


def test_direct_secret_argument_stops(tmp_path):
    f = tmp_path / ".env"
    f.write_text(pad(JA), encoding="utf-8")
    code, _, err = prep(f, tmp_path / "out")
    assert code == 3 and reason(err) == "SECRET_FILE"


def test_output_folder_with_sources_json_is_not_read(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text(pad(JA), encoding="utf-8")
    out = src / "out"  # 入力の中に出力先を作る
    assert prep(src, out)[0] == 0
    assert prep(src, out)[0] == 0  # 2 回目でも出力先の中身を読まない
    assert [i["path"] for i in load(out)["items"]] == ["a.txt"]


# ---- slug・ID・並び ------------------------------------------------------------
def test_slug_and_collision_and_length(tmp_path):
    src = tmp_path / "src"
    (src / "src").mkdir(parents=True)
    (src / "src" / "main.py").write_text(pad("print('x')  # " + JA), encoding="utf-8")
    (src / "A.txt").write_text(pad(JA), encoding="utf-8")
    (src / "long").mkdir()
    (src / "long" / ("n" * 120 + ".txt")).write_text(pad(JA), encoding="utf-8")
    out = tmp_path / "out"
    assert prep(src, out)[0] == 0
    slugs = {i["path"]: i["slug"] for i in load(out)["items"]}
    assert slugs["src/main.py"] == "src__main.py"
    assert all(len(s) <= 80 for s in slugs.values())
    assert slugs["A.txt"] == "A.txt"


def test_case_insensitive_collision_gets_hash_suffix(tmp_path):
    assert cs.make_slugs(["A.txt", "a.txt"])[0] != cs.make_slugs(["A.txt", "a.txt"])[1]
    assert cs.make_slugs(["A.txt", "a.txt"])[0] == "A.txt"


def test_ids_follow_relative_path_order(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    for n in ("c.txt", "a.txt", "b.txt"):
        (src / n).write_text(pad(JA), encoding="utf-8")
    out = tmp_path / "out"
    assert prep(src, out)[0] == 0
    items = load(out)["items"]
    assert [i["path"] for i in items] == ["a.txt", "b.txt", "c.txt"]
    assert [i["id"] for i in items] == [1, 2, 3]


# ---- 包み方 ---------------------------------------------------------------------
def test_code_fence_is_longer_than_inner_backticks(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text("x = '```'\n" + "print(1)\n" * 10, encoding="utf-8")
    out = tmp_path / "out"
    assert prep(src, out)[0] == 0
    md = (out / load(out)["items"][0]["md_path"]).read_text(encoding="utf-8")
    assert "````python" in md and "\n````\n" in md


def test_subtitle_becomes_timed_transcript(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "s.vtt").write_text(
        "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\n" + JA + "\n\n"
        "00:01:05.000 --> 00:01:07.000\n" + "二つ目の字幕です。" + "\n", encoding="utf-8")
    out = tmp_path / "out"
    assert prep(src, out)[0] == 0
    item = load(out)["items"][0]
    md = (out / item["md_path"]).read_text(encoding="utf-8")
    assert item["kind"] == "subtitle"
    assert "**[00:01]**" in md and "**[01:05]**" in md
    assert "## 概要" in md and "## Transcript" in md
    assert "source_format: subtitle" in md


def test_long_text_without_headings_gets_at_most_12_sections(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "long.txt").write_text(("本文の一段落です。" * 30 + "\n\n") * 400, encoding="utf-8")
    out = tmp_path / "out"
    assert prep(src, out)[0] == 0
    md = (out / load(out)["items"][0]["md_path"]).read_text(encoding="utf-8")
    assert md.count("\n## ") <= 12


def test_over_200k_chars_is_marked(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "huge.txt").write_text(("あいうえお。" * 40 + "\n") * 1000, encoding="utf-8")
    out = tmp_path / "out"
    assert prep(src, out)[0] == 0
    assert load(out)["items"][0]["over_estimate"] is True


# ---- ZIP（安全検査 9 種）-------------------------------------------------------
def _zip(path, members):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in members:
            z.writestr(name, data)
    return path


def _zip_with_info(path, name, data, external_attr=None):
    info = zipfile.ZipInfo(name)
    if external_attr is not None:
        info.external_attr = external_attr
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(info, data)
    return path


def _encrypt_flag(path):
    b = bytearray(path.read_bytes())
    for sig, off in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        i = b.find(sig)
        while i >= 0:
            b[i + off] |= 0x01
            i = b.find(sig, i + 1)
    path.write_bytes(bytes(b))


# 名前: (期待する理由コード, 見本を作る関数)。危険な ZIP の 9 種のうち、件数・容量超過以外の 7 種
DANGEROUS = {
    "traversal": ("ZIP_UNSAFE", lambda p: _zip(p, [("../evil.txt", pad(JA))])),
    "absolute": ("ZIP_UNSAFE", lambda p: _zip(p, [("/tmp/evil.txt", pad(JA))])),
    "drive": ("ZIP_UNSAFE", lambda p: _zip(p, [("C:/evil.txt", pad(JA))])),
    "link": ("ZIP_UNSAFE", lambda p: _zip_with_info(p, "link", "/etc/passwd",
                                                    external_attr=(stat.S_IFLNK | 0o777) << 16)),
    "encrypted": ("ZIP_ENCRYPTED", lambda p: (_zip(p, [("a.txt", pad(JA))]), _encrypt_flag(p))),
    "invalid": ("ZIP_INVALID", lambda p: p.write_bytes(b"not a zip at all" * 10)),
    "ratio": ("ZIP_RATIO", lambda p: _zip(p, [("zeros.txt", b"\0" * (11 * 1024 * 1024))])),
}


@pytest.mark.parametrize("name", sorted(DANGEROUS))
def test_dangerous_zip_stops_and_writes_nothing_outside(tmp_path, name):
    code, build = DANGEROUS[name]
    z = tmp_path / "pack.zip"
    build(z)
    out = tmp_path / "out"
    rc, _, err = prep(z, out)
    assert rc == 3 and reason(err) == code, err
    assert not (tmp_path / "evil.txt").exists()
    assert not any(p.name in ("evil.txt", "passwd") for p in tmp_path.rglob("*"))


def test_zip_too_large_by_bytes_and_entries(tmp_path, monkeypatch):
    # 上限は関数の実行時に読まれるので、同じプロセスの中で変えて確かめる
    z = _zip(tmp_path / "pack.zip", [("a.txt", pad(JA)), ("b.txt", pad(JA))])
    monkeypatch.setattr(cs, "ZIP_MAX_TOTAL_BYTES", 10)
    with pytest.raises(cs.StopReason) as e:
        cs.scan_zip(z, "pack.zip", is_arg=True)
    assert e.value.code == "ZIP_TOO_LARGE"
    monkeypatch.setattr(cs, "ZIP_MAX_TOTAL_BYTES", 500 * 1024 * 1024)
    monkeypatch.setattr(cs, "ZIP_MAX_ENTRIES", 1)
    with pytest.raises(cs.StopReason) as e:
        cs.scan_zip(z, "pack.zip", is_arg=True)
    assert e.value.code == "ZIP_TOO_LARGE"


def test_valid_zip_extracts_and_keeps_original(tmp_path):
    z = _zip(tmp_path / "pack.zip", [("docs/ja.txt", pad(JA)), ("code/x.py", "print(1)\n" * 6)])
    before = hashlib.sha256(z.read_bytes()).hexdigest()
    out = tmp_path / "out"
    assert prep(z, out)[0] == 0
    items = load(out)["items"]
    assert [i["slug"] for i in items] == ["pack__code__x.py", "pack__docs__ja.txt"]
    assert hashlib.sha256(z.read_bytes()).hexdigest() == before
    assert (out / ".work" / "unzipped" / "pack" / "docs" / "ja.txt").exists()


def test_zip_inside_folder_fails_only_that_item(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    DANGEROUS["traversal"][1](src / "bad.zip")
    (src / "ok.txt").write_text(pad(JA), encoding="utf-8")
    out = tmp_path / "out"
    rc, _, err = prep(src, out)
    assert rc == 0, err
    data = load(out)
    assert [i["path"] for i in data["items"]] == ["ok.txt"]
    assert any(s["path"] == "bad.zip" and s["reason"] == "ZIP_UNSAFE" for s in data["skipped"])


def test_zip_in_zip_is_skipped(tmp_path):
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as z:
        z.writestr("i.txt", pad(JA))
    z = _zip(tmp_path / "pack.zip", [("nested.zip", inner.getvalue()), ("a.txt", pad(JA))])
    out = tmp_path / "out"
    assert prep(z, out)[0] == 0
    data = load(out)
    assert [i["path"] for i in data["items"]] == ["pack.zip/a.txt"]
    assert any(s["path"] == "pack.zip/nested.zip" and s["reason"] == "ZIP_IN_ZIP" for s in data["skipped"])


# ---- 動き（4.5・4.7）・再開・index ----------------------------------------------
def test_plan_reports_max_files_and_no_output_written(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    for i in range(7):
        (src / f"f{i}.txt").write_text(pad(JA), encoding="utf-8")
    out = tmp_path / "out"
    rc, stdout, _ = run("plan", str(src), "--out-dir", str(out), "--max-files", "5")
    assert rc == 0
    plan = json.loads(stdout)
    cand = plan["candidates"][0]
    assert cand["pending"] == 7 and cand["needs_choice"] is True
    assert not out.exists(), "plan は何も書かない"


def test_prepare_over_max_files_stops_without_limit(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    for i in range(6):
        (src / f"f{i}.txt").write_text(pad(JA), encoding="utf-8")
    out = tmp_path / "out"
    rc, _, err = prep(src, out)
    assert rc == 3 and reason(err) == "MAX_FILES_REQUIRED"
    assert not (out / ".work" / "sources.json").exists(), "止まるときは何も作らない"


def test_prepare_limit_takes_first_n_and_rest_stays_pending(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    for n in ("a", "b", "c"):
        (src / f"{n}.txt").write_text(pad(JA), encoding="utf-8")
    out = tmp_path / "out"
    assert prep(src, out, "--limit", "2")[0] == 0
    items = load(out)["items"]
    assert [(i["path"], i["status"]) for i in items] == [
        ("a.txt", "prepared"), ("b.txt", "prepared"), ("c.txt", "pending")]


def test_zero_sources_stops_with_reason(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / ".env").write_text(pad(JA), encoding="utf-8")
    rc, _, err = prep(src, tmp_path / "out")
    assert rc == 3 and reason(err) == "NO_SOURCE_FILES"


def test_mark_transitions_and_resume_skips_done(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text(pad(JA), encoding="utf-8")
    (src / "b.pdf").write_bytes(b"%PDF-1.4 fake")
    out = tmp_path / "out"
    assert prep(src, out)[0] == 0
    items = load(out)["items"]
    b_id = next(i["id"] for i in items if i["path"] == "b.pdf")
    a_id = next(i["id"] for i in items if i["path"] == "a.txt")
    st = {i["path"]: i["status"] for i in items}
    assert st == {"a.txt": "prepared", "b.pdf": "pending"}, "文字の変換は pending のまま"
    assert run("mark", "--out-dir", str(out), "--id", str(b_id), "--status", "docs_done")[0] == 2, \
        "pending → docs_done は飛ばせない（BAD_TRANSITION）"
    assert run("mark", "--out-dir", str(out), "--id", str(b_id), "--status", "prepared")[0] == 2, \
        "PDF の prepared は --md-path が要る"
    assert run("mark", "--out-dir", str(out), "--id", str(b_id), "--status", "prepared",
               "--md-path", ".work/md/b.md")[0] == 0
    for stt in ("docs_done", "done"):
        assert run("mark", "--out-dir", str(out), "--id", str(a_id), "--status", stt)[0] == 0
    assert run("mark", "--out-dir", str(out), "--id", str(b_id), "--status", "failed")[0] == 2, \
        "failed には --reason が要る"
    assert run("mark", "--out-dir", str(out), "--id", str(b_id), "--status", "failed",
               "--reason", "CONVERT_FAILED")[0] == 0
    # 2 回目の prepare は done を飛ばす（a.txt は done のまま）
    assert prep(src, out)[0] == 0
    st = {i["path"]: i["status"] for i in load(out)["items"]}
    assert st["a.txt"] == "done"


def test_index_lists_items_and_skips_when_two_or_more(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text(pad(JA), encoding="utf-8")
    (src / "b.txt").write_text(pad(JA), encoding="utf-8")
    (src / ".env").write_text(pad(JA), encoding="utf-8")
    out = tmp_path / "out"
    assert prep(src, out)[0] == 0
    assert run("index", "--out-dir", str(out))[0] == 0
    text = (out / "00_index.md").read_text(encoding="utf-8")
    assert "a.txt" in text and "b.txt" in text and ".env" in text and "SECRET" in text
    assert "](a.txt/explained/README.md)" in text


# ---- 4.1・4.5・4.7 の残りの行 ---------------------------------------------------------
def test_ambiguous_source_dirs_stop_for_prepare_and_both_listed_by_plan(tmp_path):
    for n in ("01_source", "01_sources"):
        (tmp_path / n).mkdir()
        (tmp_path / n / "a.txt").write_text(pad(JA), encoding="utf-8")
    out = tmp_path / "out"
    rc, _, err = run("prepare", "--out-dir", str(out), cwd=str(tmp_path))
    assert rc == 3 and reason(err) == "AMBIGUOUS_SOURCE_DIR"
    rc, stdout, _ = run("plan", "--out-dir", str(out), cwd=str(tmp_path))
    plan = json.loads(stdout)
    assert plan["ambiguous"] is True and len(plan["candidates"]) == 2


def test_no_input_stops(tmp_path):
    rc, _, err = run("prepare", "--out-dir", str(tmp_path / "out"), cwd=str(tmp_path))
    assert rc == 3 and reason(err) == "NO_INPUT"


def test_cwd_source_dir_is_used_when_no_argument(tmp_path):
    (tmp_path / "01_source").mkdir()
    (tmp_path / "01_source" / "a.txt").write_text(pad(JA), encoding="utf-8")
    out = tmp_path / "out"
    rc, _, err = run("prepare", "--out-dir", str(out), cwd=str(tmp_path))
    assert rc == 0, err
    assert [i["path"] for i in load(out)["items"]] == ["a.txt"]


def test_output_conflict_when_params_differ(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text(pad(JA), encoding="utf-8")
    out = tmp_path / "out"
    assert prep(src, out)[0] == 0
    rc, _, err = run("prepare", str(src), "--out-dir", str(out), "--slides", "8")
    assert rc == 3 and reason(err) == "OUTPUT_CONFLICT"


def test_output_folder_without_sources_json_is_conflict(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text(pad(JA), encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()
    (out / "notes.txt").write_text("別の中身", encoding="utf-8")
    rc, _, err = prep(src, out)
    assert rc == 3 and reason(err) == "OUTPUT_CONFLICT"


def test_changed_file_is_redone_on_resume(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text(pad(JA), encoding="utf-8")
    out = tmp_path / "out"
    assert prep(src, out)[0] == 0
    a_id = load(out)["items"][0]["id"]
    run("mark", "--out-dir", str(out), "--id", str(a_id), "--status", "docs_done")
    run("mark", "--out-dir", str(out), "--id", str(a_id), "--status", "done")
    (src / "a.txt").write_text(pad(JA + "変更後"), encoding="utf-8")
    assert prep(src, out)[0] == 0
    assert load(out)["items"][0]["status"] == "prepared", "入力が変わったら done を引き継がない"


def test_url_input_is_one_item_and_resume_keeps_it_pending(tmp_path):
    url = "https://www.youtube.com/watch?v=abcDEF12345"
    out = tmp_path / "out"
    rc, stdout, _ = run("plan", url, "--out-dir", str(out))
    cand = json.loads(stdout)["candidates"][0]
    assert cand["items"][0]["kind"] == "url" and cand["items"][0]["route"] == "youtube"
    assert not out.exists()
    assert prep(url, out)[0] == 0
    item = load(out)["items"][0]
    assert item["status"] == "pending" and item["md_path"] is None
    assert prep(url, out)[0] == 0, "同じ URL の再実行は、同じ入力として続きから"


# ---- 見直しで足した検査（展開の実サイズ、失敗の範囲、index の表）-----------------------
def test_extraction_counts_actual_bytes_and_leaves_nothing(tmp_path, monkeypatch):
    """申告のサイズが小さくても、書き出すときの実際のサイズが上限を超えれば止め、展開先を残さない。"""
    z = _zip(tmp_path / "pack.zip", [("a.txt", pad(JA)), ("b.txt", pad(JA))])
    entries, _ = cs.scan_zip(z, "pack.zip", is_arg=True)
    monkeypatch.setattr(cs, "ZIP_MAX_TOTAL_BYTES", 5)  # 検査の後に上限を下げる＝申告と実際が食い違う状況
    out = tmp_path / "out"
    failed = cs.extract_zips(out, tmp_path, entries)
    assert list(failed) == ["pack.zip"] and failed["pack.zip"].code == "ZIP_TOO_LARGE"
    assert not (out / ".work" / "unzipped" / "pack").exists()
    assert all(e["disk"] is None for e in entries)


def test_zip_member_hash_is_streamed_for_documents(tmp_path):
    z = _zip(tmp_path / "pack.zip", [("doc.pdf", b"%PDF-1.4 " + b"x" * 5000), ("a.txt", pad(JA))])
    entries, _ = cs.scan_zip(z, "pack.zip", is_arg=True)
    pdf = next(e for e in entries if e["path"].endswith("doc.pdf"))
    assert pdf["sha256"] == hashlib.sha256(b"%PDF-1.4 " + b"x" * 5000).hexdigest()


def test_index_escapes_pipe_in_paths(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a|b.txt").write_text(pad(JA), encoding="utf-8")
    (src / "c.txt").write_text(pad(JA), encoding="utf-8")
    out = tmp_path / "out"
    assert prep(src, out)[0] == 0
    assert run("index", "--out-dir", str(out))[0] == 0
    rows = [ln for ln in (out / "00_index.md").read_text(encoding="utf-8").splitlines() if "a\\|b.txt" in ln]
    assert rows and rows[0].count("|") - rows[0].count("\\|") == 5, "表の列は 4 つ（区切り 5 本）のまま"
