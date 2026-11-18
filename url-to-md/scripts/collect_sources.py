#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""collect_sources.py — 入力（フォルダ・ファイル・ZIP・URL）を、解説の処理の単位に分けて準備する。

標準ライブラリだけで動く。`url-to-md` の collection モード（workflows/collection.md）から呼ぶ。

使い方:
  collect_sources.py plan    [<入力>] [--source-dir DIR] [--cwd DIR] [--out-dir DIR]
                             [--max-files N] [--slides N] [--audience TEXT]
  collect_sources.py prepare [<入力>] [--source-dir DIR] [--cwd DIR] --out-dir DIR
                             [--limit N] [--max-files N] [--slides N] [--audience TEXT]
  collect_sources.py mark    --out-dir DIR --id N --status STATUS [--md-path PATH] [--reason CODE]
  collect_sources.py index   --out-dir DIR

  plan     何も書かず、対象の一覧・出力先の状態・見積もりを JSON で返す。
  prepare  展開と Markdown への包みを行い、<出力先>/.work/sources.json を書く。
  mark     1 件の状態を進める（pending → prepared → docs_done → done。どこからでも failed）。
  index    2 件以上のとき、<出力先>/00_index.md を作る。

標準出力: JSON（UTF-8）。標準エラー: 進捗。失敗は REASON=<理由コード> を最後に出す。
終了コード: 0 正常 / 2 引数の誤り・状態の遷移の誤り / 3 理由コードつきの停止 / 1 想定外。
"""
from __future__ import annotations

import argparse
import codecs
import datetime
import fnmatch
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import unicodedata
import zipfile
from pathlib import Path, PurePosixPath

from _common import StopReason, run_main

SCHEMA = 1
DEFAULT_MAX_FILES = 5
ZIP_MAX_TOTAL_BYTES = 500 * 1024 * 1024
ZIP_MAX_ENTRIES = 2000
ZIP_RATIO_MIN_SIZE = 10 * 1024 * 1024
ZIP_RATIO_LIMIT = 200
TEXT_MAX_BYTES = 2 * 1024 * 1024
MIN_BODY_CHARS = 30
OVER_ESTIMATE_CHARS = 200_000
MAX_SECTIONS = 12
TEXT_MIN_SECTION = 2000
CODE_MIN_SECTION_LINES = 200
SLUG_MAX = 80
STATUS_ORDER = ["pending", "prepared", "docs_done", "done"]

SECRET_PATTERNS = [".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx",
                   "id_rsa*", "id_ed25519*", "credentials*"]
SECRET_DIRS = {"secrets"}
MANAGEMENT_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv",
                   "dist", "build", ".next", "__MACOSX"}
MD_EXT = {".md", ".markdown", ".mdx"}
TEXT_EXT = {".txt", ".text", ".log", ".rst", ".adoc", ".org", ".tex", ".csv", ".tsv"}
SUB_EXT = {".vtt", ".srt"}
CODE_EXT = {
    ".c": "c", ".h": "c", ".cpp": "cpp", ".cs": "csharp", ".java": "java", ".kt": "kotlin",
    ".go": "go", ".rs": "rust", ".py": "python", ".rb": "ruby", ".php": "php",
    ".js": "javascript", ".jsx": "jsx", ".ts": "typescript", ".tsx": "tsx", ".vue": "vue",
    ".svelte": "svelte", ".swift": "swift", ".sh": "bash", ".bash": "bash", ".zsh": "zsh",
    ".ps1": "powershell", ".sql": "sql", ".r": "r", ".lua": "lua", ".pl": "perl",
    ".css": "css", ".scss": "scss", ".xml": "xml", ".json": "json", ".jsonl": "json",
    ".yaml": "yaml", ".yml": "yaml", ".toml": "toml", ".ini": "ini", ".cfg": "ini",
    ".ipynb": "json",
}
CODE_NAMES = {"Makefile": "makefile", "Dockerfile": "dockerfile"}
CONVERT_EXT = {".pdf", ".docx", ".xlsx", ".pptx", ".html", ".htm", ".xhtml",
               ".doc", ".xls", ".ppt"}
BINARY_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".tif", ".tiff",
              ".mp3", ".mp4", ".mov", ".avi", ".wav", ".m4a", ".flac", ".mkv",
              ".exe", ".dll", ".so", ".dylib", ".bin", ".class", ".pyc", ".woff", ".woff2"}
JA_RE = re.compile(r"[぀-ゟ゠-ヿ一-鿿]")
TAG_RE = re.compile(r"<[^>]+>")
UNSAFE_NAME_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f\s]')


class SkipFile(Exception):
    """1 件を読まないことを、理由コードで伝える（処理は続ける）。"""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


# ---- 名前・slug ---------------------------------------------------------------
def slug_of(src: str) -> str:
    s = src.replace("\\", "/").replace("/", "__")
    s = UNSAFE_NAME_RE.sub("_", s)
    return s[:SLUG_MAX]


def make_slugs(sources: list[str]) -> list[str]:
    """入力の順に slug を作る。大文字と小文字を無視して重なるときは、元のパスの SHA-1 の先頭 6 字を付ける。"""
    out, seen = [], set()
    for src in sources:
        base = slug_of(src)
        cand = base
        if cand.lower() in seen:
            h = hashlib.sha1(src.encode("utf-8")).hexdigest()[:6]
            cand = base[: SLUG_MAX - 7] + "-" + h
        seen.add(cand.lower())
        out.append(cand)
    return out


def input_name_of(path_or_url: str) -> str:
    if re.match(r"^https?://", path_or_url):
        m = re.search(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{6,})", path_or_url)
        if m and ("youtu" in path_or_url):
            return m.group(1)
        rest = re.sub(r"^https?://", "", path_or_url)
        return slug_of(re.sub(r"[?#].*$", "", rest))[:SLUG_MAX].rstrip("_") or "url"
    p = Path(path_or_url)
    if p.is_dir() and p.name in ("01_source", "01_sources"):
        return p.parent.name or "source"
    return p.stem if p.is_file() else (p.name or "source")


# ---- 読むかどうか ---------------------------------------------------------------
def is_secret(parts: tuple) -> bool:
    name = parts[-1].lower()
    if any(fnmatch.fnmatch(name, pat) for pat in SECRET_PATTERNS):
        return True
    return any(p.lower() in SECRET_DIRS for p in parts[:-1])


def is_output_dir(p: Path) -> bool:
    return (p / ".work" / "sources.json").is_file()


def ja_ratio(text: str) -> float:
    chars = [c for c in text if not c.isspace()]
    if not chars:
        return 0.0
    return len(JA_RE.findall(text)) / len(chars)


def decode_text(data: bytes) -> tuple[str | None, str | None]:
    """(文字列, None) か (None, 理由コード)。UTF-8 にそろえる（4.3）。"""
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        try:
            return data.decode("utf-16"), None
        except UnicodeDecodeError:
            return None, "ENCODING_UNKNOWN"
    if b"\x00" in data:
        return None, "BINARY"
    try:
        return data.decode("utf-8-sig"), None
    except UnicodeDecodeError:
        pass
    cands = []
    for enc in ("cp932", "euc_jp"):
        try:
            t = data.decode(enc)
        except UnicodeDecodeError:
            continue
        cands.append((ja_ratio(t), t))
    if not cands:
        return None, "ENCODING_UNKNOWN"
    cands.sort(key=lambda x: x[0], reverse=True)
    best_r, best_t = cands[0]
    if best_r < 0.3 or (len(cands) == 2 and cands[1][0] == best_r and cands[1][1] != best_t):
        return None, "ENCODING_UNKNOWN"
    return best_t, None


def classify(rel: str, size: int, read, sha_of) -> dict:
    """1 件の種類と経路を決める。読まないものは SkipFile を投げる。"""
    name = PurePosixPath(rel).name
    ext = PurePosixPath(name).suffix.lower()
    if ext in CONVERT_EXT:
        return {"kind": "document", "route": "convert", "source_format": None,
                "text": None, "over_estimate": False, "sha256": sha_of()}
    if ext in BINARY_EXT:
        raise SkipFile("BINARY")
    if size > TEXT_MAX_BYTES:
        raise SkipFile("TOO_LARGE")
    data = read()
    text, why = decode_text(data)
    if why:
        raise SkipFile(why)
    if len(text.strip()) < MIN_BODY_CHARS:
        raise SkipFile("TOO_SHORT")
    if ext in MD_EXT:
        kind, route, fmt = "markdown", "copy", None
    elif ext in SUB_EXT:
        kind, route, fmt = "subtitle", "wrap", "subtitle"
    elif name in CODE_NAMES or ext in CODE_EXT:
        kind, route, fmt = "code", "wrap", "code"
    else:
        kind, route, fmt = "text", "wrap", "text"
    return {"kind": kind, "route": route, "source_format": fmt, "text": text,
            "over_estimate": len(text) > OVER_ESTIMATE_CHARS,
            "sha256": hashlib.sha256(data).hexdigest()}


def sha_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def classify_disk(full: Path, rel: str) -> dict:
    return classify(rel, full.stat().st_size, read=full.read_bytes,
                    sha_of=lambda: sha_file(full))


# ---- ZIP ------------------------------------------------------------------------
def _zip_name(info: zipfile.ZipInfo) -> str:
    if info.flag_bits & 0x800:
        return info.filename
    raw = info.orig_filename.encode("cp437", errors="replace")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp932", errors="replace")


def _unsafe_name(name: str) -> bool:
    n = name.replace("\\", "/")
    if n.startswith("/") or re.match(r"^[A-Za-z]:", n):
        return True
    return ".." in PurePosixPath(n).parts


def _sha_member(zf: zipfile.ZipFile, info: zipfile.ZipInfo) -> str:
    h = hashlib.sha256()
    with zf.open(info) as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def scan_zip(zpath: Path, rel_zip: str, is_arg: bool) -> tuple[list[dict], list[dict]]:
    """ZIP を検査して (項目, 飛ばしたもの) を返す。危険な ZIP は StopReason（全体を止める）。

    フォルダの中の ZIP は、呼び出し側が StopReason を「その ZIP だけの失敗」に変える。
    """
    try:
        zf = zipfile.ZipFile(zpath)
    except (zipfile.BadZipFile, OSError):
        raise StopReason("ZIP_INVALID", f"ZIP として読めない: {rel_zip}")
    entries, skipped = [], []
    base = slug_of(rel_zip[:-4] if rel_zip.lower().endswith(".zip") else rel_zip)
    with zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if len(zf.infolist()) > ZIP_MAX_ENTRIES:
            raise StopReason("ZIP_TOO_LARGE", f"項目が多すぎる（上限 {ZIP_MAX_ENTRIES} 件）: {rel_zip}")
        if sum(i.file_size for i in infos) > ZIP_MAX_TOTAL_BYTES:
            raise StopReason("ZIP_TOO_LARGE", f"展開後の合計が上限を超える: {rel_zip}")
        members = []
        for info in infos:
            if info.flag_bits & 0x1:
                raise StopReason("ZIP_ENCRYPTED", f"暗号化された ZIP: {rel_zip}")
            if stat.S_ISLNK((info.external_attr >> 16) & 0xFFFF):
                raise StopReason("ZIP_UNSAFE", f"リンクの項目がある: {rel_zip}")
            name = _zip_name(info)
            if _unsafe_name(name):
                raise StopReason("ZIP_UNSAFE", f"展開先の外を指す名前がある: {rel_zip}")
            if info.file_size >= ZIP_RATIO_MIN_SIZE and (
                    info.compress_size == 0 or info.file_size / info.compress_size > ZIP_RATIO_LIMIT):
                raise StopReason("ZIP_RATIO", f"圧縮率が上限を超える項目がある: {rel_zip}")
            members.append((name.replace("\\", "/"), info))
        for name, info in sorted(members, key=lambda x: x[0]):
            path = f"{rel_zip}/{name}"
            if name.lower().endswith(".zip"):
                skipped.append({"path": path, "reason": "ZIP_IN_ZIP"})
                continue
            try:
                res = classify(path, info.file_size, read=lambda i=info: zf.read(i),
                               sha_of=lambda i=info: _sha_member(zf, i))
            except SkipFile as e:
                skipped.append({"path": path, "reason": e.reason})
                continue
            res.update({"path": path, "slug_src": f"{base}/{name}", "origin": "zip",
                        "member": name, "zip_rel": rel_zip, "disk": None})
            entries.append(res)
    return entries, skipped


# ---- フォルダ ---------------------------------------------------------------------
def scan_folder(root: Path) -> tuple[list[dict], list[dict]]:
    entries, skipped = [], []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dp = Path(dirpath)
        keep = []
        for d in dirnames:
            full = dp / d
            r = full.relative_to(root).as_posix()
            if full.is_symlink():
                skipped.append({"path": r, "reason": "SYMLINK"})
            elif is_output_dir(full):
                skipped.append({"path": r, "reason": "OUTPUT_DIR"})
            else:
                keep.append(d)
        dirnames[:] = keep
        for f in filenames:
            full = dp / f
            r = full.relative_to(root).as_posix()
            parts = PurePosixPath(r).parts
            if full.is_symlink():
                skipped.append({"path": r, "reason": "SYMLINK"})
                continue
            if is_secret(parts):
                skipped.append({"path": r, "reason": "SECRET"})
                continue
            if any(p in MANAGEMENT_DIRS for p in parts[:-1]):
                skipped.append({"path": r, "reason": "MANAGEMENT_DIR"})
                continue
            if any(p.startswith(".") for p in parts) or parts[-1].startswith("._"):
                skipped.append({"path": r, "reason": "HIDDEN"})
                continue
            if full.suffix.lower() == ".zip":
                try:
                    e, s = scan_zip(full, r, is_arg=False)
                    entries += e
                    skipped += s
                except StopReason as e:
                    skipped.append({"path": r, "reason": e.code})
                continue
            try:
                res = classify_disk(full, r)
            except SkipFile as e:
                skipped.append({"path": r, "reason": e.reason})
                continue
            res.update({"path": r, "slug_src": r, "origin": "dir", "member": None,
                        "disk": str(full)})
            entries.append(res)
    return entries, skipped


def scan_single(p: Path) -> tuple[list[dict], list[dict]]:
    """引数で渡された 1 つのファイル（秘密情報は SECRET_FILE で止まる）。"""
    if is_secret((p.name,)):
        raise StopReason("SECRET_FILE", f"秘密情報らしいファイルは読まない: {p.name}")
    if p.suffix.lower() == ".zip":
        return scan_zip(p, p.name, is_arg=True)
    try:
        res = classify_disk(p, p.name)
    except SkipFile as e:
        raise StopReason(e.reason, f"このファイルは読めない（{e.reason}）: {p.name}")
    res.update({"path": p.name, "slug_src": p.name, "origin": "dir", "member": None,
                "disk": str(p)})
    return [res], []


def scan_url(url: str) -> dict:
    yt = "youtube" in url and bool(re.search(r"(?:v=|youtu\.be/)", url))
    text = url
    return {"path": url, "slug_src": input_name_of(url), "kind": "url",
            "route": "youtube" if yt else "url", "source_format": None, "text": None,
            "over_estimate": False, "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "origin": "url", "member": None, "disk": None}


# ---- 入力の決め方（4.1） -------------------------------------------------------------
def candidates(args) -> list[dict]:
    """入力の候補（{kind, root, name}）の一覧。引数が無ければ、今いるフォルダの 01_source・01_sources。"""
    cwd = Path(args.cwd).resolve()
    if args.input:
        if re.match(r"^https?://", args.input):
            return [{"kind": "url", "root": args.input, "name": input_name_of(args.input)}]
        p = Path(args.input)
        p = (cwd / p).resolve() if not p.is_absolute() else p.resolve()
        if not p.exists():
            raise StopReason("INPUT_NOT_FOUND", f"入力が見つからない: {args.input}")
        kind = "folder" if p.is_dir() else "file"
        return [{"kind": kind, "root": str(p), "name": input_name_of(str(p))}]
    if args.source_dir:
        p = Path(args.source_dir)
        p = (cwd / p).resolve() if not p.is_absolute() else p.resolve()
        if not p.is_dir():
            raise StopReason("INPUT_NOT_FOUND", f"--source-dir が見つからない: {args.source_dir}")
        return [{"kind": "folder", "root": str(p), "name": input_name_of(str(p))}]
    found = [cwd / n for n in ("01_source", "01_sources") if (cwd / n).is_dir()]
    if not found:
        raise StopReason("NO_INPUT", "入力が無い（引数・--source-dir・01_source/01_sources のどれも無い）",
                         next_step="入力を 1 行で尋ねてください（ファイル、フォルダ、ZIP、URL のどれか）。")
    return [{"kind": "folder", "root": str(p), "name": input_name_of(str(p))} for p in found]


def scan(cand: dict) -> tuple[list[dict], list[dict]]:
    if cand["kind"] == "url":
        return [scan_url(cand["root"])], []
    if cand["kind"] == "file":
        return scan_single(Path(cand["root"]))
    return scan_folder(Path(cand["root"]))


def params_hash(slides, audience) -> str:
    norm = {"slides": slides, "audience": unicodedata.normalize("NFKC", (audience or "").strip())}
    return hashlib.sha256(json.dumps(norm, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def input_key(cand: dict) -> str:
    return cand["root"]


def out_state(out_dir: Path | None, key: str, phash: str) -> tuple[str, dict | None]:
    if out_dir is None or not out_dir.exists() or not any(out_dir.iterdir()):
        return "new", None
    sj = out_dir / ".work" / "sources.json"
    if not sj.is_file():
        return "conflict", None
    prev = json.loads(sj.read_text(encoding="utf-8"))
    if prev.get("input") == key and prev.get("params_sha256") == phash:
        return "resume", prev
    return "conflict", prev


def build_items(entries: list[dict], prev: dict | None) -> list[dict]:
    """並びは相対パスの昇順。前回の記録と sha256 が同じなら、状態を引き継ぐ（done は飛ばす）。"""
    entries = sorted(entries, key=lambda e: e["path"])
    slugs = make_slugs([e["slug_src"] for e in entries])
    prev_map = {i["path"]: i for i in (prev or {}).get("items", [])}
    items = []
    for n, (e, slug) in enumerate(zip(entries, slugs), 1):
        item = {"id": n, "slug": slug, "path": e["path"], "kind": e["kind"], "route": e["route"],
                "source_format": e["source_format"], "sha256": e["sha256"], "origin": e["origin"],
                "over_estimate": e["over_estimate"], "md_path": None, "file": e["disk"],
                "status": "pending", "stage": None, "reason": None, "_text": e["text"],
                "_member": e["member"]}
        old = prev_map.get(e["path"])
        if old and old.get("sha256") == e["sha256"] and old.get("status") in ("prepared", "docs_done", "done"):
            item.update({"status": old["status"], "stage": old["stage"], "md_path": old["md_path"]})
        items.append(item)
    return items


def public(item: dict) -> dict:
    return {k: v for k, v in item.items() if not k.startswith("_")}


def pending_count(items: list[dict]) -> int:
    return sum(1 for i in items if i["status"] != "done")


# ---- 包み方（4.3） --------------------------------------------------------------------
def _fm(title: str, path: str, fmt: str, sha: str) -> str:
    now = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    return ("---\n"
            f"title: {json.dumps(title, ensure_ascii=False)}\n"
            f"source_path: {json.dumps(path, ensure_ascii=False)}\n"
            f"source_format: {fmt}\n"
            f"source_sha256: {sha}\n"
            f"retrieved_at: {now}\n"
            "---\n\n")


def _merge_to(chunks: list, n: int) -> list:
    while len(chunks) > n:
        chunks[-2:] = [chunks[-2] + chunks[-1]]
    return chunks


def _fence(lang: str, lines: list[str]) -> str:
    body = "\n".join(lines)
    longest = max([len(m.group(0)) for m in re.finditer(r"`+", body)] or [0])
    f = "`" * max(3, longest + 1)
    return f"{f}{lang}\n{body}\n{f}"


def wrap_text(title: str, text: str) -> str:
    if any(re.match(r"^#{1,6} ", ln) for ln in text.splitlines()):
        return f"# {title}\n\n{text.strip()}\n"
    paras = [p for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    total = sum(len(p) for p in paras)
    size = max(TEXT_MIN_SECTION, -(-total // MAX_SECTIONS))
    chunks, cur, n = [], [], 0
    for p in paras:
        if cur and n + len(p) > size:
            chunks.append(cur)
            cur, n = [], 0
        cur.append(p)
        n += len(p)
    if cur:
        chunks.append(cur)
    chunks = _merge_to(chunks, MAX_SECTIONS)
    body = "\n\n".join(f"## 節 {i}\n\n" + "\n\n".join(c) for i, c in enumerate(chunks, 1))
    return f"# {title}\n\n{body}\n"


def wrap_code(title: str, text: str, lang: str) -> str:
    lines = text.rstrip("\n").split("\n")
    n = len(lines)
    size = max(CODE_MIN_SECTION_LINES, -(-n // MAX_SECTIONS))
    if n <= size:
        return f"# {title}\n\n{_fence(lang, lines)}\n"
    parts = []
    for i, start in enumerate(range(0, n, size), 1):
        c = lines[start:start + size]
        parts.append(f"## 節 {i}（{start + 1}–{start + len(c)} 行）\n\n{_fence(lang, c)}")
    return f"# {title}\n\n" + "\n\n".join(parts) + "\n"


def _seconds(t: str) -> int:
    total = 0
    for part in t.replace(",", ".").split(".")[0].split(":"):
        total = total * 60 + int(part)
    return total


def parse_cues(text: str) -> list[tuple[int, str]]:
    cues, lines, i = [], text.splitlines(), 0
    while i < len(lines):
        if "-->" in lines[i]:
            start = lines[i].split("-->")[0].strip().split()[-1]
            i += 1
            buf = []
            while i < len(lines) and lines[i].strip():
                buf.append(TAG_RE.sub("", lines[i]).strip())
                i += 1
            txt = " ".join(b for b in buf if b)
            if txt and (not cues or cues[-1][1] != txt):
                cues.append((_seconds(start), txt))
        else:
            i += 1
    return cues


def wrap_subtitle(title: str, text: str) -> str:
    rows = [f"**[{s // 60:02d}:{s % 60:02d}]** {t}" for s, t in parse_cues(text)]
    return (f"# {title}\n\n## 概要\n\n字幕から作った文字起こしです。内容の整理は解説メモで行います。\n\n"
            "## Transcript\n\n" + "\n\n".join(rows) + "\n")


def build_md(item: dict, text: str) -> str:
    if item["route"] == "copy":
        return text
    title = Path(item["path"]).stem if item["kind"] != "code" else PurePosixPath(item["path"]).name
    if item["kind"] == "subtitle":
        body = wrap_subtitle(title, text)
    elif item["kind"] == "code":
        lang = CODE_EXT.get(Path(item["path"]).suffix.lower(), CODE_NAMES.get(PurePosixPath(item["path"]).name, ""))
        body = wrap_code(title, text, lang)
    else:
        body = wrap_text(title, text)
    return _fm(title, item["path"], item["source_format"], item["sha256"]) + body


# ---- ZIP の展開（prepare だけが書く）------------------------------------------------
def extract_zips(out_dir: Path, base_dir: Path, entries: list[dict]) -> dict:
    """ZIP の項目を <出力先>/.work/unzipped/<ZIP の名前>/ に展開する（元の ZIP は変えない）。

    書き出すときの実際のサイズも数える（申告より大きければ ZIP_TOO_LARGE）。失敗した ZIP は
    展開先ごと消し、{ZIP の場所: StopReason} で返す。成功した項目には entry["disk"] を入れる。
    """
    groups: dict[str, list[dict]] = {}
    for e in entries:
        if e["origin"] == "zip" and not e.get("disk"):
            groups.setdefault(e["zip_rel"], []).append(e)
    failed: dict[str, StopReason] = {}
    for zip_rel, members in groups.items():
        base = slug_of(zip_rel[:-4] if zip_rel.lower().endswith(".zip") else zip_rel)
        dest = out_dir / ".work" / "unzipped" / base
        total = 0
        try:
            with zipfile.ZipFile(base_dir / zip_rel) as zf:
                infos = {_zip_name(i).replace("\\", "/"): i for i in zf.infolist() if not i.is_dir()}
                for e in members:
                    target = dest.joinpath(*PurePosixPath(e["member"]).parts)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(infos[e["member"]]) as src, open(target, "wb") as dst:
                        for chunk in iter(lambda: src.read(1 << 20), b""):
                            total += len(chunk)
                            if total > ZIP_MAX_TOTAL_BYTES:
                                raise StopReason("ZIP_TOO_LARGE", f"書き出した実際のサイズが上限を超えた: {zip_rel}")
                            dst.write(chunk)
                    e["disk"] = str(target)
        except StopReason as exc:
            failed[zip_rel] = exc
        except (zipfile.BadZipFile, KeyError, OSError):
            failed[zip_rel] = StopReason("ZIP_INVALID", f"ZIP を展開できない: {zip_rel}")
        if zip_rel in failed:
            shutil.rmtree(dest, ignore_errors=True)
            for e in members:
                e["disk"] = None
    return failed


# ---- 出力 ----------------------------------------------------------------------------
def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def load_sources(out_dir: Path) -> dict:
    sj = out_dir / ".work" / "sources.json"
    if not sj.is_file():
        raise StopReason("NO_SOURCES_JSON", f"sources.json が無い: {sj}", exit_code=2,
                         next_step="先に prepare を実行してください。")
    return json.loads(sj.read_text(encoding="utf-8"))


def cmd_plan(args) -> dict:
    cands = candidates(args)
    out_root = Path(args.out_dir).resolve() if args.out_dir else None
    result = {"max_files": args.max_files, "candidates": []}
    for cand in cands:
        entries, skipped = scan(cand)
        phash = params_hash(args.slides, args.audience)
        out_dir = out_root or (Path(args.cwd).resolve() / "explain-out" / cand["name"])
        state, prev = out_state(out_dir, input_key(cand), phash)
        items = build_items(entries, prev if state == "resume" else None)
        pend = pending_count(items)
        result["candidates"].append({
            "kind": cand["kind"], "root": cand["root"], "name": cand["name"],
            "out": {"dir": str(out_dir), "state": state},
            "items": [public(i) for i in items], "skipped": skipped,
            "pending": pend, "needs_choice": pend > args.max_files,
            "estimate": {"items": pend, "usd": [round(1.27 * pend, 2), round(2.07 * pend, 2)],
                         "minutes": [4 * pend, 9 * pend]},
        })
    if len(cands) > 1:
        result["ambiguous"] = True
    return result


def cmd_prepare(args) -> dict:
    cands = candidates(args)
    if len(cands) > 1:
        raise StopReason("AMBIGUOUS_SOURCE_DIR", "01_source と 01_sources の両方がある",
                         next_step="--source-dir で使う方を指定してください。")
    cand = cands[0]
    out_dir = Path(args.out_dir).resolve()
    phash = params_hash(args.slides, args.audience)
    state, prev = out_state(out_dir, input_key(cand), phash)
    if state == "conflict":
        raise StopReason("OUTPUT_CONFLICT", "出力先に別の入力・指定の記録がある",
                         next_step="別の出力先を --out で指定して、もう一度実行してください。")
    entries, skipped = scan(cand)
    items = build_items(entries, prev if state == "resume" else None)
    if not items:
        raise StopReason("NO_SOURCE_FILES", "読める対象が 0 件（飛ばした理由は sources.json の skipped）")
    pend = pending_count(items)
    if args.limit is None and pend > args.max_files:
        raise StopReason("MAX_FILES_REQUIRED",
                         f"未完了が {pend} 件で、上限 {args.max_files} 件を超える",
                         next_step="--limit N（先頭から N 件）か --max-files を指定してください。")
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_entries = [e for e in entries if e["origin"] == "zip"]
    if zip_entries:
        # 項目の path は「ZIP の場所（入力フォルダからの相対）/ 中の名前」。展開の基準は入力の親
        base_dir = Path(cand["root"]) if cand["kind"] == "folder" else Path(cand["root"]).parent
        failed = extract_zips(out_dir, base_dir, zip_entries)
        if failed:
            if cand["kind"] == "file":  # 入力の ZIP そのものが失敗なら、全体を止める（展開先は消してある）
                raise next(iter(failed.values()))
            for zip_rel, exc in failed.items():  # フォルダの中の ZIP は、その ZIP だけ飛ばして続ける
                skipped.append({"path": zip_rel, "reason": exc.code})
            entries = [e for e in entries if e.get("zip_rel") not in failed]
        items = build_items(entries, prev if state == "resume" else None)
        if not items:
            raise StopReason("NO_SOURCE_FILES", "読める対象が 0 件（飛ばした理由は sources.json の skipped）")
    targets = []
    for item in items:
        if item["status"] == "done":
            continue
        if args.limit is not None and len(targets) >= args.limit:
            break
        targets.append(item)
    for item in targets:
        if item["route"] in ("wrap", "copy"):
            text = item["_text"]
            if text is None and item["file"]:
                text = Path(item["file"]).read_text(encoding="utf-8")
            md = build_md(item, text)
            rel = f".work/md/{item['slug']}.md"
            p = out_dir / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(md, encoding="utf-8", newline="\n")
            item.update({"md_path": rel, "status": "prepared", "stage": "prepared"})
    data = {"schema": SCHEMA, "input": input_key(cand), "input_name": cand["name"],
            "params_sha256": phash, "items": [public(i) for i in items],
            "skipped": skipped}
    write_json(out_dir / ".work" / "sources.json", data)
    counts = {s: sum(1 for i in items if i["status"] == s) for s in STATUS_ORDER + ["failed"]}
    return {"out_dir": str(out_dir), "state": state, "counts": counts,
            "skipped": len(skipped), "targets": [i["id"] for i in targets]}


def cmd_mark(args) -> dict:
    out_dir = Path(args.out_dir).resolve()
    data = load_sources(out_dir)
    item = next((i for i in data["items"] if i["id"] == args.id), None)
    if item is None:
        raise StopReason("NO_SUCH_ITEM", f"id {args.id} が無い", exit_code=2)
    cur = item["status"]
    if args.status == "failed":
        if not args.reason:
            raise StopReason("BAD_ARGS", "failed には --reason が要る", exit_code=2)
        if cur == "done":
            raise StopReason("BAD_TRANSITION", "done の項目は失敗にできない", exit_code=2)
        item.update({"status": "failed", "reason": args.reason})
    else:
        nxt = STATUS_ORDER.index(args.status) if args.status in STATUS_ORDER else None
        if cur not in STATUS_ORDER or nxt is None or nxt != STATUS_ORDER.index(cur) + 1:
            raise StopReason("BAD_TRANSITION", f"{cur} から {args.status} へは進めない", exit_code=2)
        if args.status == "prepared" and item["route"] == "convert":
            if not args.md_path:
                raise StopReason("BAD_ARGS", "変換した PDF などは --md-path が要る", exit_code=2)
            item["md_path"] = args.md_path
        item.update({"status": args.status, "stage": args.status, "reason": None})
    write_json(out_dir / ".work" / "sources.json", data)
    return {"id": item["id"], "status": item["status"], "stage": item["stage"]}


def cmd_index(args) -> dict:
    out_dir = Path(args.out_dir).resolve()
    data = load_sources(out_dir)
    items, skipped = data["items"], data["skipped"]
    if len(items) < 2:
        return {"written": False, "reason": "ONE_ITEM"}
    rows = ["| # | 状態 | 元のパス | 解説メモ |", "|---|---|---|---|"]
    for i in items:
        status = i["status"] + (f"（{i['reason']}）" if i["status"] == "failed" else "")
        rows.append(f"| {i['id']} | {status} | `{i['path'].replace('|', '\\|')}` | [{i['slug']}]({i['slug']}/explained/README.md) |")
    sk = [f"- `{s['path']}`（{s['reason']}）" for s in skipped] or ["（なし）"]
    text = ("# 一覧\n\n"
            f"入力: {data['input_name']}（{len(items)} 件）\n\n"
            + "\n".join(rows) + "\n\n## 飛ばしたもの\n\n" + "\n".join(sk) + "\n")
    (out_dir / "00_index.md").write_text(text, encoding="utf-8", newline="\n")
    return {"written": True, "path": str(out_dir / "00_index.md"), "items": len(items)}


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, out_required: bool):
        p.add_argument("input", nargs="?", help="入力（フォルダ・ファイル・ZIP・URL）")
        p.add_argument("--source-dir", help="参照元のフォルダ")
        p.add_argument("--cwd", default=os.getcwd(), help="今いるフォルダ（既定は現在の場所）")
        p.add_argument("--out-dir", required=out_required, help="出力先")
        p.add_argument("--max-files", type=int, default=DEFAULT_MAX_FILES)
        p.add_argument("--slides", type=int, default=6)
        p.add_argument("--audience", default="初めて読む人")

    p = sub.add_parser("plan")
    common(p, out_required=False)
    p = sub.add_parser("prepare")
    common(p, out_required=True)
    p.add_argument("--limit", type=int, help="相対パスの昇順で先頭から N 件を処理する")
    p = sub.add_parser("mark")
    p.add_argument("--out-dir", required=True)
    p.add_argument("--id", type=int, required=True)
    p.add_argument("--status", required=True, choices=["prepared", "docs_done", "done", "failed"])
    p.add_argument("--md-path")
    p.add_argument("--reason")
    p = sub.add_parser("index")
    p.add_argument("--out-dir", required=True)
    return ap


def main() -> None:
    args = build_parser().parse_args()
    fn = {"plan": cmd_plan, "prepare": cmd_prepare, "mark": cmd_mark, "index": cmd_index}[args.cmd]
    result = fn(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    run_main(main)
