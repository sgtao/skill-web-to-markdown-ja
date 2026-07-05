#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "yt-dlp",
# ]
# ///
"""youtube_to_md.py — YouTube動画の概要とトランスクリプトをMarkdownに変換する。

yt-dlp でメタデータ（タイトル・チャンネル・概要全文）と字幕を取得し、
段落頭タイムスタンプ付きの Markdown を生成する。

字幕の選択ルール（翻訳字幕は使わない）:
  1. 動画の元言語の手動字幕
  2. 元言語の自動生成字幕（<lang>-orig → <lang>）
  3. どちらも無ければ概要のみ出力（字幕なし注記つき）

使い方:
  uv run youtube_to_md.py "<YouTube URL>" -o out.md    # uv（推奨・依存自動解決）
  python3 youtube_to_md.py "<YouTube URL>" -o out.md   # pip環境（要: pip install yt-dlp）

yt-dlp は `python -m yt_dlp` として同一インタープリタで呼ぶため、
uv run のエフェメラル環境でも pip 環境でも PATH に依存せず動く。
"""
import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request

YOUTUBE_URL_RE = re.compile(
    r"(?:https?://)?(?:www\.|m\.)?"
    r"(?:youtube\.com/(?:watch\?[^\s]*v=|shorts/|live/|embed/)|youtu\.be/)"
    r"[\w-]{6,}"
)


def _install_hint() -> str:
    """yt-dlp未導入時の案内文を、このスクリプト自身の絶対パスで組み立てる。

    呼び出し時のカレントディレクトリに関係なく正しいパスを示すため、
    `scripts/youtube_to_md.py` のような相対パスは使わない。
    """
    this_script = os.path.abspath(__file__)
    return (
        "yt-dlp が見つかりません。次のいずれかで実行してください:\n"
        f'  uv run "{this_script}" <URL> -o out.md   # uvが依存を自動解決\n'
        "  pip install yt-dlp --break-system-packages        # pip環境\n"
    )


def is_youtube_url(url: str) -> bool:
    """URLがYouTube動画を指すか判定する。

    Args:
        url: 判定対象のURL。

    Returns:
        YouTube動画URL（watch/shorts/live/embed/youtu.be）なら True。
    """
    return bool(YOUTUBE_URL_RE.search(url))


def fetch_video_info(url: str, timeout: int = 120) -> dict:
    """yt-dlp でメタデータJSONを取得する。

    Args:
        url: YouTube動画URL。
        timeout: yt-dlp のタイムアウト秒。

    Returns:
        yt-dlp --dump-json の出力をパースした辞書。

    Raises:
        RuntimeError: yt-dlp 未導入・取得失敗時。
    """
    cmd = [sys.executable, "-m", "yt_dlp",
           "--dump-json", "--skip-download", "--no-warnings", url]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"yt-dlpがタイムアウトしました（{timeout}秒）: {e}") from e
    except OSError as e:
        raise RuntimeError(f"yt-dlpの実行に失敗しました: {e}") from e
    if r.returncode != 0:
        if "No module named" in r.stderr:
            raise RuntimeError(_install_hint())
        raise RuntimeError(f"yt-dlp失敗: {r.stderr.strip()[-500:]}")
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"yt-dlpの出力(JSON)のパース失敗: {e}") from e


def _json3_url(tracks: list[dict]) -> str | None:
    """字幕トラック一覧から json3 形式のURLを選ぶ（無ければ fmt=json3 を付与）。"""
    for t in tracks:
        if t.get("ext") == "json3":
            return t.get("url")
    if tracks and tracks[0].get("url"):
        url = tracks[0]["url"]
        sep = "&" if "?" in url else "?"
        return url + sep + "fmt=json3"
    return None


def select_caption_track(info: dict) -> tuple[str, str, str] | None:
    """字幕トラックを選択する（元言語優先・手動>自動・翻訳不使用）。

    Args:
        info: fetch_video_info の戻り値。

    Returns:
        (言語コード, json3のURL, 種別 "manual"|"auto")。字幕なしは None。
    """
    subs: dict = info.get("subtitles") or {}
    autos: dict = info.get("automatic_captions") or {}
    lang = info.get("language")
    if not lang and subs:
        lang = next(iter(subs))
    if not lang:
        orig_keys = [k for k in autos if k.endswith("-orig")]
        if orig_keys:
            lang = orig_keys[0][: -len("-orig")]
    if not lang:
        return None
    if lang in subs:
        url = _json3_url(subs[lang])
        if url:
            return (lang, url, "manual")
    for key in (f"{lang}-orig", lang):
        if key in autos:
            url = _json3_url(autos[key])
            if url:
                return (lang, url, "auto")
    return None


def download_json3(url: str, timeout: int = 60) -> dict:
    """json3字幕をダウンロードしてパースする。

    Args:
        url: json3形式の字幕URL。
        timeout: HTTPタイムアウト秒。

    Returns:
        json3の辞書（events を含む）。

    Raises:
        RuntimeError: 取得・パース失敗時。
    """
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read().decode("utf-8")
    except Exception as e:  # noqa: BLE001 - ネットワーク系は一括で報告
        raise RuntimeError(f"字幕ダウンロード失敗: {e}") from e
    try:
        return json.loads(data)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"字幕(json3)のパース失敗: {e}") from e


def parse_json3(data: dict) -> list[tuple[float, float, str]]:
    """json3のeventsを (開始秒, 長さ秒, テキスト) の列に展開する。

    連続する同一テキスト（自動生成字幕のロール表示による重複）は1件に統合し、
    終了時刻は重複イベントの終了時刻まで延長する（タイムスタンプのずれ防止）。

    Args:
        data: download_json3 の戻り値。

    Returns:
        (start, duration, text) タプルのリスト。
    """
    chunks: list[tuple[float, float, str]] = []
    for ev in data.get("events", []):
        segs = ev.get("segs")
        if not segs:
            continue
        text = "".join(s.get("utf8", "") for s in segs)
        text = text.replace("\n", " ").strip()
        if not text:
            continue
        start = ev.get("tStartMs", 0) / 1000.0
        dur = ev.get("dDurationMs", 0) / 1000.0
        if chunks and chunks[-1][2] == text:
            prev_start, prev_dur, prev_text = chunks[-1]
            new_end = max(prev_start + prev_dur, start + dur)
            chunks[-1] = (prev_start, new_end - prev_start, prev_text)
            continue
        chunks.append((start, dur, text))
    return chunks


def merge_paragraphs(
    chunks: list[tuple[float, float, str]],
    gap_sec: float = 2.0,
    soft_len: int = 250,
    hard_len: int = 400,
) -> list[tuple[float, str]]:
    """字幕チャンクを意味のまとまりで段落に結合する。

    改段落の条件:
      - 前チャンク終了から gap_sec 秒を超える間があく
      - 段落が soft_len 字以上かつ文末記号で終わっている
      - 段落が hard_len 字に達した（強制）

    Args:
        chunks: parse_json3 の戻り値。
        gap_sec: 改段落とみなす無音ギャップ秒。
        soft_len: 文末での改段落を許す最小文字数。
        hard_len: 強制改段落の文字数。

    Returns:
        (段落開始秒, 段落テキスト) のリスト。
    """
    paragraphs: list[tuple[float, str]] = []
    cur_start: float | None = None
    cur_text = ""
    prev_end: float | None = None
    for start, dur, text in chunks:
        if cur_start is None:
            cur_start, cur_text = start, text
        else:
            gap = start - prev_end
            need_break = (
                gap > gap_sec
                or len(cur_text) >= hard_len
                or (len(cur_text) >= soft_len and cur_text[-1] in "。．.!?！？")
            )
            if need_break:
                paragraphs.append((cur_start, cur_text))
                cur_start, cur_text = start, text
            else:
                sep = " " if (cur_text[-1].isascii() and text[:1].isascii()) else ""
                cur_text = cur_text + sep + text
        prev_end = start + dur
    if cur_start is not None and cur_text:
        paragraphs.append((cur_start, cur_text))
    return paragraphs


def format_ts(seconds: float) -> str:
    """秒を MM:SS（1時間超は H:MM:SS）表記にする。"""
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m:02d}:{sec:02d}"


def build_markdown(
    info: dict,
    paragraphs: list[tuple[float, str]] | None,
    caption_kind: str | None,
) -> str:
    """タイトル・概要・Transcript から Markdown 本文を組み立てる。

    Args:
        info: fetch_video_info の戻り値。
        paragraphs: merge_paragraphs の戻り値。字幕なしは None。
        caption_kind: "manual" | "auto" | None。

    Returns:
        Markdown 文字列（フロントマターなし・本文のみ）。
    """
    lines: list[str] = [f"# {info.get('title') or '(無題)'}", "", "## 概要", ""]
    desc = (info.get("description") or "").strip()
    lines.append(desc if desc else "（概要欄は空です）")
    lines += ["", "## Transcript", ""]
    if paragraphs is None:
        lines.append("この動画には字幕がありません。")
    else:
        if caption_kind == "auto":
            lines += ["> 自動生成字幕のため、固有名詞や句読点に誤りを含む場合があります。", ""]
        for start, text in paragraphs:
            lines.append(f"**[{format_ts(start)}]** {text}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    """CLIエントリポイント。"""
    ap = argparse.ArgumentParser(
        description="YouTube動画の概要+トランスクリプトをMarkdownに変換")
    ap.add_argument("url", help="YouTube動画のURL")
    ap.add_argument("-o", "--output", default=None,
                    help="出力Markdownパス（省略時はstdout）")
    ap.add_argument("--timeout", type=int, default=120,
                    help="yt-dlpのタイムアウト秒（既定120）")
    args = ap.parse_args()

    if not is_youtube_url(args.url):
        print(f"[error] YouTube動画のURLではありません: {args.url}", file=sys.stderr)
        sys.exit(1)

    try:
        print("[1] メタデータ取得中...", file=sys.stderr)
        info = fetch_video_info(args.url, timeout=args.timeout)
        channel = info.get("channel") or info.get("uploader") or ""
        upload_date = info.get("upload_date")  # yt-dlpは "YYYYMMDD" 形式の文字列
        published = (f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:8]}"
                     if upload_date else "")
        duration_sec = info.get("duration")
        duration_str = format_ts(duration_sec) if isinstance(duration_sec, (int, float)) else ""
        print(f"[1] channel={channel!r} published={published!r} duration={duration_str!r}",
              file=sys.stderr)
        track = select_caption_track(info)
        paragraphs: list[tuple[float, str]] | None = None
        kind: str | None = None
        if track is None:
            print("[2] 字幕トラックなし（概要のみ出力）", file=sys.stderr)
        else:
            lang, url, kind = track
            label = "手動" if kind == "manual" else "自動生成"
            print(f"[2] 字幕トラック: {lang}（{label}）", file=sys.stderr)
            chunks = parse_json3(download_json3(url))
            paragraphs = merge_paragraphs(chunks)
            print(f"[3] 段落数: {len(paragraphs)}", file=sys.stderr)
        md = build_markdown(info, paragraphs, kind)
    except RuntimeError as e:
        print(f"[error] {e}", file=sys.stderr)
        sys.exit(1)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(md)
        print(f"[ok] 出力: {args.output}", file=sys.stderr)
        print(args.output)  # stdoutにはパスだけ（既存スクリプトの流儀）
    else:
        sys.stdout.write(md)


if __name__ == "__main__":
    main()
