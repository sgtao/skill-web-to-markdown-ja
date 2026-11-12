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

終了コード:
  0 成功 / 1 想定外のエラー / 2 引数の誤り（argparse）/
  3 取得・変換できない（理由コードつき）/ 4 必要な道具が無い
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import urlparse

EXIT_OK = 0
EXIT_UNEXPECTED = 1
EXIT_STOP = 3
EXIT_MISSING_TOOL = 4

USER_AGENT = "Mozilla/5.0"

# 理由コード → ユーザーに勧める次の手段（既定の文面）
NEXT_STEPS = {
    "NO_NETWORK": "この環境から外部に通信できないようです。対象のファイルを保存して、ファイルとして渡してください。",
    "UNSUPPORTED_SCHEME": "http:// または https:// の URL か、ファイルを指定してください。",
    "HTTP_ERROR": "URL を確かめてください。ブラウザで開けるなら、保存（PDF 印刷など）してファイルとして渡してください。",
    "EMPTY_CONTENT": "URL を確かめてください。",
    "FETCH_FAILED": "URL を確かめて、もう一度試してください。",
    "CONVERT_FAILED": "PDF で保存し直して渡してください。",
    "UNSUPPORTED_FORMAT": "PDF か新しい形式（docx / xlsx / pptx）で保存し直して渡してください。",
    "MISSING_TOOL": "表示された道具を導入してから、もう一度試してください。",
}

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
