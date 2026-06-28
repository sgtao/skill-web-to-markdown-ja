---
name: url-to-md
description: >
  URLを指定すると、Web上のページ・PDF・Microsoftドキュメント（docx/xlsx/pptx）を
  取得してMarkdownに変換するスキル。「このURLをMarkdownにして」「URLの資料をmdに変換」
  「このWebページをMarkdownにして」「リンク先のdocx/xlsx/pptxを変換して」
  「URLのPDFをマークダウンにして」などのフレーズが含まれる場合に使う。
  HTMLは本文抽出+画像の絶対URL化で完結し、PDF/Officeは図再現のため
  pdf-to-md-ja スキルへ委譲する。Claude Code（ローカル環境）での実行を想定。
---

# url-to-md — URL指定でドキュメントをMarkdown変換

URLを受け取り「取得 → 形式判定 → 変換」を行う。形式特性に応じて2系統に分岐する：

- **HTML** … 本文抽出 + 画像の絶対URL化で完結（PDF変換は不要）
- **PDF / Office（docx/xlsx/pptx）** … PDFに正規化して pdf-to-md-ja へ委譲（図を再現）

実行環境は **Claude Code（Ubuntu等のローカル）** を想定。取得は curl。
claude.ai上で curl が制限される場合は web_fetch ツールで代替する。

## 処理の全体像

```
URL ──curl──> 形式判定
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
| Webページ(HTML) | 本文抽出 + 画像src絶対URL化 | 表○ / 図は `![alt](絶対URL)` |
| PDF | 保存 → pdf-to-md-ja委譲 | 図のSVG/Mermaid再現○ |
| docx/xlsx/pptx | LibreOfficeでPDF化 → pdf-to-md-ja委譲 | 図のSVG/Mermaid再現○ |
| 旧 doc/xls/ppt | LibreOfficeで新形式へ変換後、上へ | 同上 |

## 準備（初回のみ）

```bash
pip install "markitdown[docx]" trafilatura beautifulsoup4 --break-system-packages
# soffice(LibreOffice) と curl はシステムに用意（Officeを扱う場合 soffice 必須）
```

## ワークフロー

### Step 1. 取得と形式判定

```bash
python3 scripts/fetch_convert.py "<URL>" -o output.md          # html等そのまま変換
python3 scripts/fetch_convert.py "<URL>" --pdf-passthrough     # pdf/officeはPDF化して委譲
```

fetch_convert.py の動作：
1. curl -sIL でヘッダの Content-Type を取得
2. URL末尾の拡張子 + Content-Type で形式判定（detect_format）
3. 形式で分岐（下記）

### Step 2. HTML — 本文抽出 + 画像絶対URL化（完結経路）

scripts/html_to_md.py が担う。PDF変換は不要。

```bash
python3 scripts/html_to_md.py "<URL>" -o out.md
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
PDF=$(python3 scripts/fetch_convert.py "<URL>" --pdf-passthrough -o work)
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

### Step 5. 出力の確認

- HTMLは本文のみか（ナビ・広告が残っていないか）、画像URLが絶対か
- 画像URLは実際に到達可能か（リンク切れでないか）
- Office/PDFは図がSVG/Mermaid/抽出画像として再現されているか
- xlsxはシート見出し、pptxはスライド番号コメントで区切られる

## やってはいけないこと

- HTMLを <main> 等で切り出してからtrafilaturaに渡す
  （本文判定が効かず None になる。文書全体を渡す）
- HTMLの画像を相対URLのまま残す（別環境でリンク切れ。必ず絶対URL化）
- PDF/Officeの図をmarkitdown簡易変換で済ませる（図が失われる。PDF経由で委譲）
- Office→PDFのsoffice出力先で同名PDFが衝突しないよう、作業ディレクトリを分ける
- 取得失敗・空ファイルのまま変換を続けない（curlのreturncodeとサイズを確認）

## 関連スキル

- **pdf-to-md-ja**: PDF→Markdown。目次・表の保持、図のSVG/Mermaid再現。
  本スキルはPDF/Office（PDF化後）の処理をこれに委譲する。
