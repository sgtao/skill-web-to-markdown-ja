# skill-web-to-markdown

Web上のコンテンツ（HTML / PDF / docx / xlsx / pptx / YouTube動画）をMarkdownファイルへ
変換するClaude Code用スキルセット。URLを渡すだけで「取得 → 形式判定 → 変換」まで行い、
表はMarkdownテーブルに、図はSVG / Mermaid / 抽出画像として、YouTubeは概要+字幕
（タイムスタンプ付き）として再現する。

## 収録スキル

| スキル | 役割 |
|--------|------|
| [url-to-md](url-to-md/SKILL.md) | エントリポイント。URLから取得して形式判定。HTMLは本文抽出+画像の絶対URL化、YouTubeは概要+字幕の取得で完結、PDF/OfficeはPDFに正規化して pdf-to-md-ja へ委譲 |
| [pdf-to-md-ja](pdf-to-md-ja/SKILL.md) | PDF→Markdown変換。目次・表の構造を保持し、グラフ等の数値図はSVG、構造図はMermaid、写真・地図はラスタ抽出で再現 |

## 処理の全体像

```
URL ─┬─ youtube URL             → youtube_to_md.py（curl不使用）→ 概要+Transcript（完結）
     └─curl──> 形式判定
               ├─ html              → 本文抽出+画像絶対URL化 → Markdown（完結）
               ├─ pdf               → 保存            ┐
               ├─ docx/xlsx/pptx    → sofficeでPDF化  ┴→ pdf-to-md-ja へ委譲
               └─ 旧 doc/xls/ppt    → sofficeで新形式へ変換後、上へ
```

2スキルの連携は **PDFファイルパス1つ** の疎結合インターフェース。
変換結果の先頭には出典・要約などのYAMLフロントマターが付与される。

## インストール

```bash
bash scripts/install.sh                   # スキルごとに確認しながら ~/.claude/skills へコピー
bash scripts/install.sh -t copilot        # GitHub Copilot 用（~/.copilot/skills）へコピー
bash scripts/install.sh -t claude,agents  # 複数指定（カンマ区切り or -t の繰り返し）
bash scripts/install.sh -t all -y         # 全ターゲットへ確認なしでコピー（既存は削除して上書き）
bash scripts/install.sh --zip             # dist/ にスキルごとの配布用ZIPを生成
```

| ターゲット (`-t`) | インストール先 | 対象 | 上書き用の環境変数 |
|---|---|---|---|
| `claude`（既定） | `~/.claude/skills` | Claude Code | `CLAUDE_SKILLS_DIR` |
| `copilot` | `~/.copilot/skills` | GitHub Copilot | `COPILOT_SKILLS_DIR` |
| `agents` | `~/.agents/skills` | `.agents` 規約に対応するエージェント | `AGENTS_SKILLS_DIR` |
| `all` | 上の3つすべて | | |

プロジェクト単位に入れる場合は環境変数で指定する
（例: `COPILOT_SKILLS_DIR=.github/skills bash scripts/install.sh -t copilot`、
`AGENTS_SKILLS_DIR=.agents/skills bash scripts/install.sh -t agents`）。

手動でコピーする場合は `cp -r url-to-md pdf-to-md-ja ~/.claude/skills/` でもよい。

## 使い方

Claude Code でスキル導入後、次のように依頼する：

- 「このURLをMarkdownにして」（Webページ）
- 「URLのPDFをマークダウンにして」（PDF・図の再現つき）
- 「リンク先のdocx/xlsx/pptxを変換して」（Officeドキュメント）
- 「このYouTube動画をMarkdownにして」（概要+トランスクリプト・タイムスタンプ付き）

ローカルPDFを直接変換する場合は「このPDFをMarkdownに変換して」で pdf-to-md-ja が単体でも動く。

## 動作環境・依存

Claude Code（Ubuntu等のローカル環境）を想定。依存管理は **uv を推奨**
（無ければ pip にフォールバック）。

**uv（推奨）**: 各スクリプトは PEP 723 で依存を宣言済み。`uv run` が自動解決する。

```bash
# uv 本体のインストール（未導入の場合）
curl -LsSf https://astral.sh/uv/install.sh | sh

# 開発用に .venv を作る場合（任意）
uv sync
```

**pip フォールバック**:

```bash
pip install "markitdown[docx]" trafilatura beautifulsoup4 yt-dlp --break-system-packages
```

ほか: curl（必須）, LibreOffice soffice（Office変換時）, poppler-utils（PDF解析時）

## CLI Usage（スキルを介さず直接使う）

各スクリプトは単体のCLIとしても使える。`uv run` は依存を自動解決する
（pip環境なら `python3` に読み替え）。

### YouTube動画 → 概要+トランスクリプト

```bash
uv run url-to-md/scripts/youtube_to_md.py "https://www.youtube.com/watch?v=XXXX" -o video.md
```

字幕は動画の元言語（手動字幕 > 自動生成）。段落頭に `**[MM:SS]**` が付く。

### Webページ → Markdown（本文抽出+画像絶対URL化）

```bash
uv run url-to-md/scripts/html_to_md.py "https://example.com/article" -o article.md
```

### URL → 形式自動判定つき変換（PDF/Officeは委譲用に保存）

```bash
uv run url-to-md/scripts/fetch_convert.py "<URL>" -o out.md
uv run url-to-md/scripts/fetch_convert.py "<URL>" --pdf-passthrough   # PDF/Office→PDF保存
```

### YAMLフロントマター付与

```bash
uv run url-to-md/scripts/add_frontmatter.py body.md \
    --url "<URL>" --format html --title "タイトル" --summary "要約" -o final.md
# YouTube: --format youtube --channel "ch名" --published 2026-07-01 --duration "12:34"
```

※ `pdf-to-md-ja/scripts/svg_figures.py` はCLIではなく、Claudeが関数を呼ぶ
SVG生成ライブラリ（標準ライブラリのみ）。

## ディレクトリ構成

```
├── url-to-md/          # URL取得・形式判定・HTML変換スキル
│   └── scripts/        # fetch_convert.py / html_to_md.py / youtube_to_md.py / add_frontmatter.py
├── pdf-to-md-ja/       # PDF→Markdown変換スキル（図のSVG/Mermaid再現）
│   ├── references/     # 図の判定・Mermaidパターン・SVGロジックの詳説
│   └── scripts/        # svg_figures.py（SVGジェネレータ）
└── scripts/
    └── install.sh      # インストール支援スクリプト（Claude Code / GitHub Copilot / .agents 対応）
```
