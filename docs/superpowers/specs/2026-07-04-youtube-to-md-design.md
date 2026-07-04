# 設計: url-to-md への YouTube 対応追加

日付: 2026-07-04
ステータス: 承認済み

## 目的

YouTube 動画の URL を渡すと、動画の**概要（description 全文）**と
**トランスクリプト（字幕）**を 1 つの Markdown ファイルに変換できるようにする。

参照実装: zcaceres/markdownify-mcp の `youtube-to-markdown` ツール
（markitdown の YouTubeConverter に委譲する方式）。ただし本スキルでは
下記の要件を満たすため markitdown ではなく yt-dlp を採用する。

## 決定済み要件

| 項目 | 決定 |
|------|------|
| トランスクリプト形式 | 段落結合 + 段落頭に `**[MM:SS]**` タイムスタンプ |
| 字幕の言語 | 動画の元言語を優先。手動字幕 > 自動生成字幕。翻訳字幕は使わない |
| 概要 | description 全文をそのまま掲載（切り詰めない） |
| 実装方式 | A案: yt-dlp ベース（`--dump-json` でメタデータ、json3 で字幕） |

### 検討した代替案（不採用）

- **B案: youtube-transcript-api + HTML パース** — 概要全文の取得が
  watch ページの `ytInitialData` 自前パースに依存し壊れやすい。不採用。
- **C案: markitdown[all] 委譲（参照実装と同方式）** — トランスクリプトが
  タイムスタンプなしの連結テキストになり、概要もメタタグ由来の切り詰め版に
  なるため、上記要件と両立しない。不採用。

## アーキテクチャ

既存の「形式ごとに分岐し、スクリプトは疎結合」の構成に YouTube を
第 3 の完結経路として追加する。

```
URL ──> 形式判定
         ├─ youtube URL        → youtube_to_md.py → 概要+Transcript の Markdown（完結）★新規
         ├─ html               → 本文抽出+画像絶対URL化 → Markdown（完結）
         ├─ pdf                → 保存           ┐
         └─ docx/xlsx/pptx     → sofficeでPDF化 ┴→ pdf-to-md-ja へ委譲
```

## コンポーネント

### 1. youtube_to_md.py（新規: url-to-md/scripts/）

CLI:

```bash
python3 scripts/youtube_to_md.py "<YouTube URL>" -o output.md
```

処理フロー:

1. **URL 判定**: `youtube.com/watch?v=` / `youtu.be/` / `youtube.com/shorts/` /
   `youtube.com/live/` / `youtube.com/embed/` のパターンで video ID を抽出。
   該当しなければエラー終了。
2. **メタデータ取得**: `yt-dlp --dump-json --skip-download --no-warnings <URL>`
   を subprocess で実行し JSON を取得。使用フィールド:
   `title` / `channel`（なければ `uploader`）/ `upload_date` / `duration` /
   `description` / `language` / `subtitles` / `automatic_captions` /
   `webpage_url`。
3. **字幕トラック選択**（元言語優先・翻訳字幕は使わない）:
   1. 基準言語 = `language` フィールド。なければ手動字幕の先頭言語、
      それも無ければ `automatic_captions` のうち `-orig` サフィックスを持つ
      トラックの言語（動画の元言語の自動生成字幕）
   2. `subtitles`（手動字幕）に基準言語があればそれを採用
   3. なければ `automatic_captions` の `<lang>-orig`、次いで `<lang>` を採用
   4. どちらも無ければ「字幕なし」として概要のみ出力（エラーにしない）
4. **字幕取得**: 選択したトラックの json3 フォーマット URL をダウンロードし、
   `events[].segs[].utf8` と `tStartMs` から (開始秒, テキスト) の列に展開。
5. **段落結合**: 次の条件で改段落し、段落頭に `**[MM:SS]**`
   （1 時間超は `[H:MM:SS]`）を付与:
   - チャンク間ギャップが約 2 秒超、または
   - 文末記号（。．.!?）で終わり、かつ段落が約 250 字を超えた
   - ハード上限: 約 400 字
6. **Markdown 組み立て**:

```markdown
# {動画タイトル}

## 概要

{description 全文（リンク・改行を保持、そのまま）}

## Transcript

**[00:00]** こんにちは。今日は……

**[01:24]** SKILL.md の構造は……
```

- 字幕なしの場合、Transcript セクションに
  「この動画には字幕がありません」と注記する。
- チャプター一覧は概要欄に含まれることが多いため独立セクションは作らない（YAGNI）。
- stdout に出力ファイルパスを出す（既存スクリプトの流儀に合わせる）。

### 2. fetch_convert.py（変更）

- `detect_format()` の**前段**に URL パターンによる `youtube` 判定を追加
  （Content-Type 判定より先。watch ページは text/html を返すため）。
- 判定結果が `youtube` の場合はダウンロードせず、
  `youtube_to_md.py` を使うよう案内メッセージを出して正常終了（安全網）。
- SKILL.md には「YouTube URL なら fetch_convert を経由せず直接 Step Y へ」
  と記載し、通常経路では fetch_convert を通さない。

### 3. add_frontmatter.py（変更）

- `--format` の選択肢に `youtube` を追加。
- YouTube 固有メタデータ用の任意引数を追加:
  `--channel` / `--published`（YYYY-MM-DD）/ `--duration`（例 `12:34`）。
  指定時のみフロントマターに出力（既存形式には影響なし）。

フロントマター例:

```yaml
---
title: 動画タイトル
source_url: https://www.youtube.com/watch?v=...
source_format: youtube
channel: チャンネル名
published: 2026-05-01
duration: "12:34"
has_figures: false
summary: 200字以内の要約
retrieved_at: ISO8601日時
---
```

## エラー処理

| 状況 | 挙動 |
|------|------|
| yt-dlp 未インストール | `pip install yt-dlp --break-system-packages` の案内を stderr に出して終了コード 1 |
| 非公開・削除・地域制限動画 | yt-dlp のエラーメッセージをそのまま stderr に出して終了コード 1 |
| 字幕なし | エラーにせず概要のみの Markdown を生成し、注記を付ける |
| json3 ダウンロード失敗 | エラーメッセージを出して終了コード 1（部分出力は残さない） |

## ドキュメント更新

- **url-to-md/SKILL.md**:
  - frontmatter description にトリガーフレーズ追加
    （「YouTubeの動画をMarkdownにして」「動画の文字起こしをmdに」
    「YouTubeの概要とトランスクリプトを変換して」等）
  - 処理の全体像の図・対応範囲表に youtube 行を追加
  - ワークフローに YouTube 用 Step を追加
  - 準備セクションに `yt-dlp` を追加
  - 「やってはいけないこと」に追加:
    - YouTube の watch ページを trafilatura / html_to_md.py で処理しない
      （JS レンダリングのため本文が取れない）
    - 翻訳字幕を黙って使わない（元言語の字幕のみ）
- **README.md**（リポジトリ直下）: 全体図・使い方・依存を同期。

## 依存関係

- 追加: `yt-dlp`（pip）。準備セクションの pip install 行に追記する。
- youtube_to_md.py は標準ライブラリ + yt-dlp CLI のみで動作させる
  （yt-dlp を Python API ではなく CLI として subprocess で呼ぶ。
  既存スクリプトが curl / soffice を subprocess で呼ぶ流儀に合わせる）。

## 検証方法

自動テスト基盤は既存スキルに無いため導入しない。実動画 3 種で手動検証する:

1. 日本語・字幕あり動画 → 概要全文と日本語トランスクリプトの段落・タイムスタンプを確認
2. 英語動画 → 元言語（英語）のまま出力されることを確認
3. 字幕なし動画 → 概要のみ + 注記で正常終了することを確認

加えて add_frontmatter.py で `--format youtube` のフロントマターが
正しく付与されることを確認する。
