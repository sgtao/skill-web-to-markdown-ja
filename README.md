# skill-web-to-markdown

Web上のコンテンツ（HTML / PDF / docx / xlsx / pptx）をMarkdownファイルへ変換する
Claude Code用スキルセット。URLを渡すだけで「取得 → 形式判定 → 変換」まで行い、
表はMarkdownテーブルに、図はSVG / Mermaid / 抽出画像として再現する。

## 収録スキル

| スキル | 役割 |
|--------|------|
| [url-to-md](url-to-md/SKILL.md) | エントリポイント。URLから取得して形式判定。HTMLは本文抽出+画像の絶対URL化で完結、PDF/OfficeはPDFに正規化して pdf-to-md-ja へ委譲 |
| [pdf-to-md-ja](pdf-to-md-ja/SKILL.md) | PDF→Markdown変換。目次・表の構造を保持し、グラフ等の数値図はSVG、構造図はMermaid、写真・地図はラスタ抽出で再現 |

## 処理の全体像

```
URL ──curl──> 形式判定
                ├─ html              → 本文抽出+画像絶対URL化 → Markdown（完結）
                ├─ pdf               → 保存            ┐
                ├─ docx/xlsx/pptx    → sofficeでPDF化  ┴→ pdf-to-md-ja へ委譲
                └─ 旧 doc/xls/ppt    → sofficeで新形式へ変換後、上へ
```

2スキルの連携は **PDFファイルパス1つ** の疎結合インターフェース。
変換結果の先頭には出典・要約などのYAMLフロントマターが付与される。

## インストール

```bash
bash scripts/install.sh          # スキルごとに確認しながら ~/.claude/skills へコピー
bash scripts/install.sh -y       # 確認なしでコピー（既存は削除して上書き）
bash scripts/install.sh --zip    # dist/ にスキルごとの配布用ZIPを生成
```

手動でコピーする場合は `cp -r url-to-md pdf-to-md-ja ~/.claude/skills/` でもよい。

## 使い方

Claude Code でスキル導入後、次のように依頼する：

- 「このURLをMarkdownにして」（Webページ）
- 「URLのPDFをマークダウンにして」（PDF・図の再現つき）
- 「リンク先のdocx/xlsx/pptxを変換して」（Officeドキュメント）

ローカルPDFを直接変換する場合は「このPDFをMarkdownに変換して」で pdf-to-md-ja が単体でも動く。

## 動作環境・依存

Claude Code（Ubuntu等のローカル環境）を想定。

```bash
pip install "markitdown[docx]" trafilatura beautifulsoup4 --break-system-packages
# ほか: curl（必須）, LibreOffice soffice（Office変換時）, poppler-utils（PDF解析時）
```

## ディレクトリ構成

```
├── url-to-md/          # URL取得・形式判定・HTML変換スキル
│   └── scripts/        # fetch_convert.py / html_to_md.py / add_frontmatter.py
├── pdf-to-md-ja/       # PDF→Markdown変換スキル（図のSVG/Mermaid再現）
│   ├── references/     # 図の判定・Mermaidパターン・SVGロジックの詳説
│   └── scripts/        # svg_figures.py（SVGジェネレータ）
└── scripts/
    └── install.sh      # インストール支援スクリプト
```
