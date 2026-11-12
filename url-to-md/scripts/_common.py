#!/usr/bin/env python3
"""_common.py — url-to-md のスクリプトが共通で使う土台（標準ライブラリのみ）。

ここに集めているもの:
  - setup_stdio()   標準出力・標準エラーを UTF-8 にする（Windows の cp932 対策）
  - StopReason      「取得・変換できない」を理由コードつきで伝える例外
  - run_main()      main を包み、失敗を「理由コード＋理由＋次の手段」で表示する
  - check_scheme()  http / https 以外の URL を止める
  - fetch()         curl で取得する（バイト列で受け、HTTP ステータスを確かめる）
  - decode_html()   HTML のバイト列を、文字コードを判定して文字列にする
  - move()          ドライブ（ファイルシステム）をまたいでも動く移動
  - contract_epilog()  --help の末尾に出す「契約」（標準出力・終了コード・理由コード）
  - sniff_format()  ファイルの先頭のバイトから形式を確かめる
  - unique_path()   同名のファイルがあれば連番を付けた出力先を返す
  - find_soffice() / check_env()  道具の有無を調べる（外部には接続しない）

終了コード:
  0 成功 / 1 想定外のエラー / 2 引数の誤り（argparse）/
  3 取得・変換できない（理由コードつき）/ 4 必要な道具が無い
"""
from __future__ import annotations

import glob
import importlib.util
import os
import pathlib
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from urllib.parse import unquote, urlparse

EXIT_OK = 0
EXIT_UNEXPECTED = 1
EXIT_USAGE = 2
EXIT_STOP = 3
EXIT_MISSING_TOOL = 4

USER_AGENT = "Mozilla/5.0"

# 理由コード → ユーザーに勧める次の手段（既定の文面）
NEXT_STEPS = {
    "NO_NETWORK": "この環境から外部に通信できないようです。対象のファイルを保存して、ファイルとして渡してください。",
    "UNSUPPORTED_SCHEME": "http:// または https:// の URL か、ファイルを指定してください。",
    "HTTP_ERROR": "URL を確かめてください。ブラウザで開けるなら、保存（PDF 印刷など）してファイルとして渡してください。",
    "LOGIN_REQUIRED": "ブラウザで開いて保存（PDF 印刷など）し、ファイルとして渡してください。"
                      "誤判定なら --force を付けて続行できます。",
    "JS_RENDERED": "ブラウザで開いて保存（PDF 印刷など）し、ファイルとして渡してください。"
                   "誤判定なら --force を付けて続行できます。",
    "EMPTY_CONTENT": "URL またはファイルを確かめてください。",
    "FETCH_FAILED": "URL を確かめて、もう一度試してください。",
    "CONVERT_FAILED": "PDF で保存し直して渡してください。",
    "UNSUPPORTED_FORMAT": "PDF か新しい形式（docx / xlsx / pptx）で保存し直して渡してください。",
    "LEGACY_NEEDS_SOFFICE": "新しい形式（docx / xlsx / pptx）で保存し直して渡してください。"
                            "LibreOffice（soffice）を導入すれば、旧形式も自動で変換します。",
    "MISSING_TOOL": "表示された道具を導入してから、もう一度試してください。",
}

# 理由コード → 意味（--help に出す。終了コード 3・4 のときに REASON=<コード> で表示する）
REASON_MEANINGS = {
    "NO_NETWORK": "この環境から対象の URL に接続できない",
    "UNSUPPORTED_SCHEME": "http / https 以外の URL",
    "HTTP_ERROR": "サーバーがエラーを返した（404・403・500 など）",
    "LOGIN_REQUIRED": "ログイン画面・有料記事の壁らしい（推定。--force で続行できる）",
    "JS_RENDERED": "本文がほとんど取れない。JavaScript で描画するページらしい"
                   "（推定。--force で続行できる）",
    "EMPTY_CONTENT": "取得したファイル・渡されたファイルが空",
    "FETCH_FAILED": "取得に失敗した（上のどれにも当たらない curl の失敗）",
    "CONVERT_FAILED": "変換に失敗した（soffice での PDF 化など）",
    "UNSUPPORTED_FORMAT": "対応していない形式",
    "LEGACY_NEEDS_SOFFICE": "旧 Office（doc / xls / ppt）で、LibreOffice（soffice）が無い",
    "MISSING_TOOL": "必要な道具が無い（何が無いかを併記。終了コード 4）",
}
# 続行（--force）を認める理由コード。どちらも推定で止めているため
FORCEABLE = ("LOGIN_REQUIRED", "JS_RENDERED")

# curl の終了コードのうち「通信できない」とみなすもの
#   5: プロキシを解決できない / 6: ホスト名を解決できない / 7: 接続できない / 28: 時間切れ
_CURL_NO_NETWORK = {5, 6, 7, 28}


class StopReason(Exception):
    """取得・変換できないことを、理由コードつきで伝える。

    Args:
        code: 理由コード（例: "HTTP_ERROR"）。
        message: 日本語の理由。
        next_step: ユーザーに勧める次の手段。省略時は理由コードの既定の文面。
        exit_code: 終了コード。省略時は 3（MISSING_TOOL は 4）。
    """

    def __init__(self, code: str, message: str, next_step: str | None = None,
                 exit_code: int | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.next_step = next_step or NEXT_STEPS.get(code, "")
        if exit_code is None:
            exit_code = EXIT_MISSING_TOOL if code == "MISSING_TOOL" else EXIT_STOP
        self.exit_code = exit_code


def setup_stdio() -> None:
    """標準出力・標準エラーを UTF-8 にする。

    Windows の既定（cp932）では、絵文字などを print した時点で
    UnicodeEncodeError になる。表せない文字は置き換えて、落ちないようにする。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def report_stop(e: StopReason) -> None:
    """StopReason を標準エラーに表示する（最後の 3 行が決まった形）。"""
    print(f"[stop] {e.message}", file=sys.stderr)
    if e.next_step:
        print(f"[next] {e.next_step}", file=sys.stderr)
    print(f"REASON={e.code}", file=sys.stderr)


def run_main(main_func) -> None:
    """main を包んで実行する。失敗はトレースバックではなく理由で伝える。

    環境変数 URL_TO_MD_DEBUG=1 のときだけ、想定外のエラーのトレースバックを出す。
    """
    setup_stdio()
    try:
        main_func()
    except StopReason as e:
        report_stop(e)
        sys.exit(e.exit_code)
    except KeyboardInterrupt:
        print("[stop] 中断されました。", file=sys.stderr)
        sys.exit(130)
    except Exception as e:  # noqa: BLE001 - 最後の受け口
        if os.environ.get("URL_TO_MD_DEBUG"):
            raise
        print(f"[error] 想定外のエラー: {type(e).__name__}: {e}", file=sys.stderr)
        print("[next] スクリプトは書き換えずに、この表示をそのまま報告してください"
              "（詳しい表示は環境変数 URL_TO_MD_DEBUG=1）。", file=sys.stderr)
        print("REASON=UNEXPECTED", file=sys.stderr)
        sys.exit(EXIT_UNEXPECTED)


def check_scheme(url: str) -> None:
    """http / https 以外の URL を止める。

    Raises:
        StopReason: UNSUPPORTED_SCHEME。
    """
    scheme = urlparse(url).scheme.lower()
    if scheme not in ("http", "https"):
        shown = scheme or "(なし)"
        raise StopReason("UNSUPPORTED_SCHEME",
                         f"対応していない種類の URL です（スキーム: {shown}）: {url}")


def _last_content_type(header_text: str) -> str:
    """リダイレクトを含むヘッダの記録から、最後の Content-Type を取り出す。"""
    content_type = ""
    for line in header_text.splitlines():
        if line.lower().startswith("content-type:"):
            content_type = line.split(":", 1)[1].strip()
    return content_type


def fetch(url: str, dest: str, timeout: int = 120,
          connect_timeout: int = 15) -> str:
    """curl で URL を取得して dest に保存し、Content-Type を返す。

    出力はバイト列のまま保存する（文字コードの判定は decode_html が行う）。
    HEAD は使わず、1 回の GET でヘッダも受け取る（HEAD を拒むサーバーがあるため）。

    Args:
        url: 取得する URL（http / https）。
        dest: 保存先のパス。
        timeout: 全体の制限時間（秒）。
        connect_timeout: 接続までの制限時間（秒）。

    Returns:
        最後の応答の Content-Type（無ければ空文字列）。

    Raises:
        StopReason: UNSUPPORTED_SCHEME / MISSING_TOOL / NO_NETWORK /
            FETCH_FAILED / HTTP_ERROR / EMPTY_CONTENT。
    """
    check_scheme(url)
    fd, header_path = tempfile.mkstemp(prefix="url2md_hdr_")
    os.close(fd)
    try:
        cmd = ["curl", "-sL", "-A", USER_AGENT,
               "--connect-timeout", str(connect_timeout),
               "--max-time", str(timeout),
               "-D", header_path, "-o", dest, "-w", "%{http_code}", url]
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=timeout + 30)
        except FileNotFoundError:
            raise StopReason("MISSING_TOOL", "curl が見つかりません。") from None
        except subprocess.TimeoutExpired:
            raise StopReason("NO_NETWORK",
                             f"取得が時間切れになりました（{timeout} 秒）: {url}") from None
        stderr = r.stderr.decode("utf-8", errors="replace").strip()
        if r.returncode in _CURL_NO_NETWORK:
            raise StopReason(
                "NO_NETWORK",
                f"この URL に接続できませんでした（curl の終了コード {r.returncode}）: {url}")
        if r.returncode != 0:
            raise StopReason(
                "FETCH_FAILED",
                f"取得に失敗しました（curl の終了コード {r.returncode}）: {stderr[:200]}")
        status_text = r.stdout.decode("ascii", errors="replace").strip()
        status = int(status_text) if status_text.isdigit() else 0
        if status >= 400:
            raise StopReason("HTTP_ERROR",
                             f"サーバーがエラーを返しました（HTTP {status}）: {url}")
        if not os.path.exists(dest) or os.path.getsize(dest) == 0:
            raise StopReason("EMPTY_CONTENT", f"取得したファイルが空でした: {url}")
        with open(header_path, encoding="latin-1") as f:
            return _last_content_type(f.read())
    finally:
        try:
            os.unlink(header_path)
        except OSError:
            pass


_CHARSET_ALIASES = {
    "shift_jis": "cp932", "shift-jis": "cp932", "sjis": "cp932",
    "x-sjis": "cp932", "windows-31j": "cp932", "ms932": "cp932",
    "ms_kanji": "cp932", "x-euc-jp": "euc_jp",
}
_META_CHARSET_RE = re.compile(
    rb"""<meta[^>]+charset\s*=\s*["']?\s*([A-Za-z0-9_\-]+)""", re.IGNORECASE)
_HEADER_CHARSET_RE = re.compile(r"charset\s*=\s*[\"']?([A-Za-z0-9_\-]+)",
                                re.IGNORECASE)


def _try_decode(data: bytes, charset: str | None) -> str | None:
    """指定の文字コードで読めれば文字列を返す。読めなければ None。"""
    if not charset:
        return None
    name = _CHARSET_ALIASES.get(charset.lower(), charset)
    try:
        return data.decode(name)
    except (LookupError, UnicodeDecodeError):
        return None


def detect_charset(data: bytes, content_type: str = "") -> str | None:
    """HTML の文字コード名を「HTTP ヘッダ → meta タグ」の順で探す（無ければ None）。"""
    m = _HEADER_CHARSET_RE.search(content_type or "")
    if m:
        return m.group(1)
    m = _META_CHARSET_RE.search(data[:4096])
    if m:
        return m.group(1).decode("ascii", errors="replace")
    return None


def decode_html(data: bytes, content_type: str = "") -> str:
    """HTML のバイト列を文字列にする。

    順番: BOM → HTTP ヘッダの charset → meta タグの charset → UTF-8 →
    charset-normalizer（入っていれば）→ cp932 → UTF-8（読めない所は置き換え）。
    """
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", errors="replace")
    text = _try_decode(data, detect_charset(data, content_type))
    if text is not None:
        return text
    text = _try_decode(data, "utf-8")
    if text is not None:
        return text
    try:
        from charset_normalizer import from_bytes
        best = from_bytes(data).best()
        if best is not None:
            return str(best)
    except ImportError:
        pass
    text = _try_decode(data, "cp932")
    if text is not None:
        return text
    return data.decode("utf-8", errors="replace")


def read_html_file(path: str, content_type: str = "") -> str:
    """HTML ファイルを、文字コードを判定して読む。"""
    with open(path, "rb") as f:
        return decode_html(f.read(), content_type)


def move(src: str, dst: str) -> str:
    """ファイルを移す。ドライブ（ファイルシステム）をまたいでも動く。"""
    parent = os.path.dirname(os.path.abspath(dst))
    os.makedirs(parent, exist_ok=True)
    if os.path.exists(dst):
        os.remove(dst)
    shutil.move(src, dst)
    return dst


def write_stdout(text: str) -> None:
    """結果を標準出力に書く（UTF-8。改行は足さない）。"""
    sys.stdout.write(text)
    sys.stdout.flush()


# ---- --help に出す契約 ------------------------------------------------------
def contract_epilog(reason_codes=(), stdout: str = "", exit_codes=None,
                    notes=()) -> str:
    """--help の末尾（epilog）に出す、スクリプトの契約の文章を作る。

    Args:
        reason_codes: このスクリプトが出しうる理由コード。空なら理由コードの節を出さない。
        stdout: 標準出力に何を出すかの説明。
        exit_codes: 終了コード → 意味。省略時は 0〜4 の標準の割り当て。
        notes: 末尾に足す補足の行。
    """
    if exit_codes is None:
        exit_codes = {
            0: "成功",
            1: "想定外のエラー（REASON=UNEXPECTED。スクリプトは書き換えずに報告する）",
            2: "引数の誤り",
            3: "取得・変換できない（理由コードつき）",
            4: "必要な道具が無い（REASON=MISSING_TOOL）",
        }
    lines = ["標準出力:", f"  {stdout}（常に UTF-8）",
             "標準エラー:", "  進捗とログ（常に UTF-8）",
             "終了コード:"]
    lines += [f"  {code}  {text}" for code, text in exit_codes.items()]
    if reason_codes:
        lines += ["失敗時の表示（標準エラーの最後の 3 行）:",
                  "  [stop] 理由 / [next] 次の手段 / REASON=<理由コード>",
                  "  トレースバックは出さない（環境変数 URL_TO_MD_DEBUG=1 のときだけ出す）",
                  "理由コード:"]
        width = max(len(c) for c in reason_codes)
        lines += [f"  {c.ljust(width)}  {REASON_MEANINGS[c]}" for c in reason_codes]
    lines += list(notes)
    return "\n".join(lines)


# ---- 形式の確認 -------------------------------------------------------------
_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_ZIP_DIRS = (("word/", "docx"), ("xl/", "xlsx"), ("ppt/", "pptx"))


def sniff_format(path: str) -> str | None:
    """ファイルの先頭のバイトから形式を確かめる。

    Returns:
        "pdf" / "docx" / "xlsx" / "pptx" / "ole"（旧 Office の入れ物）/ "html" /
        分からなければ None。
    """
    try:
        with open(path, "rb") as f:
            head = f.read(2048)
    except OSError:
        return None
    if head.lstrip()[:5] == b"%PDF-":
        return "pdf"
    if head.startswith(_OLE_MAGIC):
        return "ole"
    if head.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(path) as z:
                names = z.namelist()
        except (zipfile.BadZipFile, OSError):
            return None
        for prefix, fmt in _ZIP_DIRS:
            if any(n.startswith(prefix) for n in names):
                return fmt
        return None
    low = head.lower()
    if b"<!doctype html" in low or b"<html" in low or b"<head" in low or b"<body" in low:
        return "html"
    return None


# ---- 出力先の名前 -----------------------------------------------------------
_UNSAFE_NAME_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')


def safe_stem(source: str, default: str = "download") -> str:
    """URL またはパスから、出力ファイル名の元になる名前（拡張子なし）を作る。

    Windows で使えない文字は _ に置き換える。名前が取れなければ default を返す。
    """
    parsed = urlparse(source)
    if parsed.scheme in ("http", "https"):
        name = unquote(parsed.path.rstrip("/").rsplit("/", 1)[-1])
        stem = os.path.splitext(name)[0] if name else parsed.netloc
    else:
        name = re.split(r"[\\/]", source.rstrip("/\\"))[-1]
        stem = os.path.splitext(name)[0]
    stem = _UNSAFE_NAME_RE.sub("_", stem).strip(" ._")
    return stem[:80] or default


def unique_path(directory: str, filename: str) -> str:
    """directory の中で、まだ無いファイル名のパスを返す（フォルダは作る）。

    同名があれば「名前-2.拡張子」「名前-3.拡張子」…と連番を付ける。
    """
    os.makedirs(directory, exist_ok=True)
    stem, ext = os.path.splitext(filename)
    candidate = os.path.join(directory, filename)
    n = 2
    while os.path.exists(candidate):
        candidate = os.path.join(directory, f"{stem}-{n}{ext}")
        n += 1
    return candidate


# ---- 道具の有無 -------------------------------------------------------------
def find_soffice() -> str | None:
    """LibreOffice（soffice）の場所を探す。見つからなければ None。

    順番: 環境変数 SOFFICE_PATH → PATH 上の soffice / libreoffice → 既定の場所。
    """
    env = os.environ.get("SOFFICE_PATH")
    if env and os.path.isfile(env):
        return env
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    candidates = []
    for var in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        base = os.environ.get(var)
        if base:
            candidates.append(os.path.join(base, "LibreOffice", "program", "soffice.exe"))
    candidates += ["/Applications/LibreOffice.app/Contents/MacOS/soffice",
                   "/usr/bin/soffice", "/usr/local/bin/soffice", "/snap/bin/libreoffice"]
    candidates += sorted(glob.glob("/opt/libreoffice*/program/soffice"))
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


SOFFICE_TIMEOUT = 180


def run_soffice(soffice: str, src: str, target: str, outdir: str,
                workdir: str, timeout: int = SOFFICE_TIMEOUT) -> str:
    """soffice で src を target の形式（pdf / docx / xlsx / pptx）に変換する。

    専用のプロファイル（workdir の中）を使うので、LibreOffice を開いたままでも
    変換でき、同時に動かしても衝突しない。

    Args:
        soffice: soffice の場所（find_soffice の結果）。
        src: 変換元のファイル。
        target: 変換先の形式。
        outdir: 変換後のファイルを置くフォルダ（無ければ作る）。
        workdir: 専用プロファイルを置く一時フォルダ。
        timeout: 制限時間（秒）。

    Returns:
        変換後のファイルのパス。

    Raises:
        StopReason: MISSING_TOOL（soffice を実行できない）/ CONVERT_FAILED。
    """
    os.makedirs(outdir, exist_ok=True)
    profile = pathlib.Path(workdir, "lo_profile").resolve().as_uri()
    cmd = [soffice, f"-env:UserInstallation={profile}", "--headless", "--norestore",
           "--convert-to", target, "--outdir", outdir, src]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except (FileNotFoundError, PermissionError):
        raise StopReason("MISSING_TOOL",
                         f"LibreOffice（soffice）を実行できません: {soffice}") from None
    except subprocess.TimeoutExpired:
        raise StopReason("CONVERT_FAILED",
                         f"soffice での変換が時間切れになりました（{timeout} 秒）。") from None
    made = os.path.join(outdir, os.path.splitext(os.path.basename(src))[0] + "." + target)
    if r.returncode != 0 or not os.path.exists(made):
        detail = (r.stderr or r.stdout).decode("utf-8", errors="replace").strip()[:200]
        raise StopReason("CONVERT_FAILED",
                         f"soffice で {target} に変換できませんでした: {detail}")
    return made


def _has_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def check_env() -> dict:
    """道具の有無を調べて辞書で返す。外部には接続しない。

    通信できるかは調べない（対象の URL を取得してみて判定する）。
    """
    tools = {"curl": shutil.which("curl"), "soffice": find_soffice(),
             "uv": shutil.which("uv")}
    for name in ("pdfinfo", "pdftotext", "pdftoppm", "pdfimages"):
        tools[name] = shutil.which(name)
    modules = {name: _has_module(name) for name in (
        "markitdown", "trafilatura", "bs4", "charset_normalizer", "yt_dlp",
        "openpyxl", "pptx", "cairosvg")}
    return {
        "python": platform.python_version(),
        "python_ok": sys.version_info >= (3, 10),
        "platform": sys.platform,
        "stdout_encoding": getattr(sys.stdout, "encoding", None),
        "tools": tools,
        "modules": modules,
        "network": "not_checked",
    }


def format_env(env: dict) -> list[str]:
    """check_env の結果を、人が読める 1 行ずつの文字列にする。"""
    ok = "OK" if env["python_ok"] else "NG（3.10 以上が必要）"
    lines = [f"{'python'.ljust(21)}: {env['python']} {ok}（{env['platform']}）"]
    for name, path in env["tools"].items():
        lines.append(f"{name.ljust(21)}: {path or '無し'}")
    for name, found in env["modules"].items():
        lines.append(f"{('py:' + name).ljust(21)}: {'あり' if found else '無し'}")
    lines.append("network".ljust(21) + ": 調べていない（対象の URL を取得してみて判定する）")
    return lines
