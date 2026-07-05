---
name: url-to-md
description: >
  URLを指定すると、Web上のページ・PDF・Microsoftドキュメント（docx/xlsx/pptx）・
  YouTube動画を取得してMarkdownに変換するスキル。「このURLをMarkdownにして」
  「URLの資料をmdに変換」「このWebページをMarkdownにして」
  「リンク先のdocx/xlsx/pptxを変換して」「URLのPDFをマークダウンにして」
  「YouTubeの動画をMarkdownにして」「動画の文字起こし／トランスクリプトをmdに」
  などのフレーズが含まれる場合に使う。HTMLは本文抽出+画像の絶対URL化、
  YouTubeは概要+字幕（タイムスタンプ付き）で完結し、PDF/Officeは図再現のため
  pdf-to-md-ja スキルへ委譲する。Claude Code（ローカル環境）での実行を想定。
---

# url-to-md — URL指定でドキュメントをMarkdown変換

URLを受け取り「取得 → 形式判定 → 変換」を行う。形式特性に応じて3系統に分岐する：

- **HTML** … 本文抽出 + 画像の絶対URL化で完結（PDF変換は不要）
- **YouTube** … yt-dlpで概要（説明欄全文）+ 字幕を取得しMarkdownに（完結）
- **PDF / Office（docx/xlsx/pptx）** … PDFに正規化して pdf-to-md-ja へ委譲（図を再現）

実行環境は **Claude Code（Ubuntu等のローカル）** を想定。取得は curl。
claude.ai上で curl が制限される場合は web_fetch ツールで代替する。

## 処理の全体像

```
URL ──curl──> 形式判定
          ├─ youtube URL       → youtube_to_md.py → 概要+Transcript（完結）
          ├─ html              → 本文抽出+画像絶対URL化 → Markdown（完結）
          ├─ pdf               → 保存            ┐
          ├─ docx/xlsx/pptx    → sofficeでPDF化  ┴→ pdf-to-md-ja へ委譲
          └─ 旧 doc/xls/ppt    → sofficeで新形式へ変換後、上へ
```

図の扱いは **非対称**。これは媒体特性による自然な設計：
- HTMLの画像は元々Web上にあり**URLが生きている** → 絶対URL参照で十分
- Office/PDFの図はファイル内に埋もれ**参照先が無い** → PDF経由でSVG/Mermaid再現

## 対応範囲

| 入力 | 処理 | 図・表 |
|------|------|--------|
| YouTube動画 | yt-dlpで概要+字幕取得 → 段落+タイムスタンプ | 図なし（テキストのみ） |
| Webページ(HTML) | 本文抽出 + 画像src絶対URL化 | 表○ / 図は `![alt](絶対URL)` |
| PDF | 保存 → pdf-to-md-ja委譲 | 図のSVG/Mermaid再現○ |
| docx/xlsx/pptx | LibreOfficeでPDF化 → pdf-to-md-ja委譲 | 図のSVG/Mermaid再現○ |
| 旧 doc/xls/ppt | LibreOfficeで新形式へ変換後、上へ | 同上 |

## 準備（初回のみ）

**uv がある場合（推奨）**: 追加インストール不要。各スクリプトは PEP 723 で
依存を宣言しており、`uv run scripts/xxx.py` が依存を自動解決する。

**uv が無い場合（pip フォールバック）**:

```bash
pip install "markitdown[docx]" trafilatura beautifulsoup4 yt-dlp --break-system-packages
```

いずれの場合も soffice(LibreOffice) と curl はシステムに用意
（Officeを扱う場合 soffice 必須）。uv の有無は `command -v uv` で判定できる。

## ワークフロー

### Step 0. YouTube — 概要+トランスクリプト（完結経路）

YouTube URL（youtube.com/watch・youtu.be・shorts・live・embed）は
fetch_convert.py を通さず専用スクリプトで直接変換する:

```bash
uv run scripts/youtube_to_md.py "<YouTube URL>" -o video.md
# uvが無い場合: python3 scripts/youtube_to_md.py "<YouTube URL>" -o video.md
```

出力: `# タイトル` → `## 概要`（説明欄全文）→ `## Transcript`
（段落頭に `**[MM:SS]**` タイムスタンプ）。

字幕の選択ルール（スクリプトが自動判断）:
- 動画の元言語の手動字幕 > 元言語の自動生成字幕。**翻訳字幕は使わない**
- 字幕が無い動画は概要のみ出力し「字幕がありません」注記が付く

変換後は Step 5 でフロントマターを付与する（`--format youtube` と
`--channel` `--published` `--duration` を指定）。

### Step 1. 取得と形式判定

```bash
uv run scripts/fetch_convert.py "<URL>" -o output.md          # html等そのまま変換
# uvが無い場合: python3 scripts/fetch_convert.py "<URL>" -o output.md
uv run scripts/fetch_convert.py "<URL>" --pdf-passthrough     # pdf/officeはPDF化して委譲
# uvが無い場合: python3 scripts/fetch_convert.py "<URL>" --pdf-passthrough
```

fetch_convert.py の動作：
1. curl -sIL でヘッダの Content-Type を取得
2. URL末尾の拡張子 + Content-Type で形式判定（detect_format）
3. 形式で分岐（下記）

### Step 2. HTML — 本文抽出 + 画像絶対URL化（完結経路）

scripts/html_to_md.py が担う。PDF変換は不要。

```bash
uv run scripts/html_to_md.py "<URL>" -o out.md
# uvが無い場合: python3 scripts/html_to_md.py "<URL>" -o out.md
```

処理内容：
1. **画像src等を絶対URL化**：BeautifulSoup + urljoin で相対・ルート相対・
   プロトコル相対の全パターンを絶対URLに変換。data: はそのまま
2. **本文抽出**：trafilaturaでナビ・広告・関連記事・フッターを除去
   （favor_precision=True, deduplicate=True）。**完全HTML文書のまま渡す**こと
   （<main>等で切り出すと本文判定が効かず None になる）
3. **フォールバック**：trafilaturaが本文も見出しも画像も取れなかった時のみ
   markitdownで全体変換（ノイズ混入の可能性を明記）
4. **重複除去**：連続する長い重複段落を後処理で除去

結果、図は `![alt](https://…)` のフルパス参照となり、別環境でMarkdownを開いても
画像が表示される。

### Step 3. PDF / Office — PDFに正規化して委譲

--pdf-passthrough を付けると、PDFと判定された場合は保存のみ、
docx/xlsx/pptx は **LibreOfficeでPDF化** し、いずれもPDFパスをstdoutに返す。

```bash
PDF=$(uv run scripts/fetch_convert.py "<URL>" --pdf-passthrough -o work)
# uvが無い場合: PDF=$(python3 scripts/fetch_convert.py "<URL>" --pdf-passthrough -o work)
# soffice --headless --convert-to pdf を内部実行（Officeの場合）
# → $PDF を pdf-to-md-ja スキルで変換（pdfinfo/pdfimages検証 → 図のSVG/Mermaid化）
```

委譲インターフェースは **PDFファイルパス1つ**（疎結合）。
受け取った pdf-to-md-ja がStep1以降を実行し、図つきMarkdownを生成する。

### Step 4. 旧Office形式（doc/xls/ppt）

委譲の前に新形式へ変換：
```bash
soffice --headless --convert-to docx old.doc
```

### Step 5. YAMLフロントマターを付与

変換済みMarkdownの先頭に、出典・メタ情報のヘッダーを付ける。

```bash
uv run scripts/add_frontmatter.py body.md \
    --url "<URL>" --format <html|pdf|docx|xlsx|pptx|youtube> \
    --title "<タイトル>" --summary "<200字以内の要約>" -o final.md
# uvが無い場合: python3 scripts/add_frontmatter.py body.md \
#     --url "<URL>" --format <html|pdf|docx|xlsx|pptx|youtube> \
#     --title "<タイトル>" --summary "<200字以内の要約>" -o final.md
```

ヘッダー項目：

```yaml
---
title: 文書タイトル
source_url: 変換元URL
source_format: html|pdf|docx|xlsx|pptx|youtube
channel: チャンネル名            # youtubeのみ（任意）
published: 公開日 YYYY-MM-DD    # youtubeのみ（任意）
duration: "12:34"               # youtubeのみ（任意）
has_figures: true|false        # SVG/Mermaid/画像を含むか（自動判定）
summary: 200字以内の要約
retrieved_at: ISO8601日時       # 自動
---
```

**自動で埋まる項目**：`source_url` `source_format` `has_figures` `retrieved_at`。
これらは引数とスクリプトのみで確定する。

**内容に依存する項目**：`title` `summary`。
`--title` `--summary` を省略すると、本文先頭の見出しと先頭段落200字から
機械抽出する（フォールバック）。ただし**より的確なタイトル・要約が望ましいので、
Claudeが本文を読んで `--title` `--summary` を明示指定することを推奨**する。
要約は200字以内（超過分は自動で丸める）。

二重付与防止：既にフロントマターがあるMarkdownには付与しない（警告のみ）。

### Step 6. 出力の確認

- HTMLは本文のみか（ナビ・広告が残っていないか）、画像URLが絶対か
- 画像URLは実際に到達可能か（リンク切れでないか）
- Office/PDFは図がSVG/Mermaid/抽出画像として再現されているか
- xlsxはシート見出し、pptxはスライド番号コメントで区切られる
- YouTubeは概要が全文入っているか、Transcriptの段落頭にタイムスタンプがあるか

## やってはいけないこと

- HTMLを <main> 等で切り出してからtrafilaturaに渡す
  （本文判定が効かず None になる。文書全体を渡す）
- HTMLの画像を相対URLのまま残す（別環境でリンク切れ。必ず絶対URL化）
- PDF/Officeの図をmarkitdown簡易変換で済ませる（図が失われる。PDF経由で委譲）
- Office→PDFのsoffice出力先で同名PDFが衝突しないよう、作業ディレクトリを分ける
- 取得失敗・空ファイルのまま変換を続けない（curlのreturncodeとサイズを確認）
- YouTubeのwatchページをhtml_to_md.py（trafilatura）で処理しない
  （JSレンダリングのため本文が取れない。必ずyoutube_to_md.pyを使う）
- 翻訳字幕を黙って使わない（元言語の手動字幕>自動生成字幕のみ）

## 関連スキル

- **pdf-to-md-ja**: PDF→Markdown。目次・表の保持、図のSVG/Mermaid再現。
  本スキルはPDF/Office（PDF化後）の処理をこれに委譲する。
