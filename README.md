# skill-web-to-markdown

Web 上のコンテンツ（HTML / PDF / docx / xlsx / pptx / YouTube 動画）や手元のファイルを
Markdown に変換するスキルセット。「取得 → 形式判定 → 変換」まで行い、
表は Markdown テーブルに、図は SVG / Mermaid / 抽出画像として、YouTube は概要＋字幕
（タイムスタンプ付き）として再現する。

## 収録スキル

| スキル | 役割 |
|--------|------|
| [url-to-md](url-to-md/SKILL.md) | エントリポイント。URL または手元のファイルから形式を判定して変換。HTML は本文抽出＋画像の絶対 URL 化、YouTube は概要＋字幕で完結。PDF / Office は LibreOffice があれば PDF にして pdf-to-md-ja へ、無ければスクリプトで直接変換 |
| [pdf-to-md-ja](pdf-to-md-ja/SKILL.md) | PDF → Markdown。目次・表の構造を保持し、グラフ等の数値図は SVG、構造図は Mermaid、写真・地図はラスタ抽出で再現。poppler・cairosvg が無くても動く |

## 対応する環境

| 環境 | 入れ方 | 状態 |
|---|---|---|
| Claude Code（macOS / Linux） | `bash scripts/install.sh` | 動作確認済み（macOS） |
| Claude Code（Windows） | Git Bash で `bash scripts/install.sh`（下の「Windows の準備」） | 実機確認は v0.1.0 の前に実施 |
| Claude.ai（Web） | `bash scripts/install.sh --zip` で作った ZIP を「カスタマイズ > スキル」に登録 | 実機確認は v0.1.0 の前に実施。コード実行が使えない設定のときは Claude が添付ファイルを直接読む（図は再現しない） |
| GitHub Copilot / `.agents` 規約のエージェント | `--target copilot` / `--target agents` | 配置のみ（動作は未確認） |

## 道具の有無でできること

スクリプトは `uv run` で依存ライブラリを自動で入れる。次の外部の道具は**無くても動く**が、できることが変わる。
変換が縮退したときは、スキルが「理由・対象・次の手段」を返答で伝える。

| 道具 | 無いとき |
|---|---|
| uv | `python` / `python3` / `py`（3.10 以上）で実行し、依存は `pip install` で入れる（ユーザーに確認） |
| LibreOffice（`soffice`） | docx / pptx は直接変換になる。図形で描いた図は再現できない（グラフの数値と埋め込み画像は補足として本文に付く）。旧 doc / xls / ppt は変換できない（`LEGACY_NEEDS_SOFFICE`） |
| poppler（`pdftotext` など） | `pypdf` / `pdfplumber` / `pypdfium2` に切り替わる。レイアウトの再現がやや粗くなる |
| cairosvg | SVG を画像にして目で確かめる工程を省き、数値の検査（`warnings`）で代える |
| ネットワーク・ログイン | 取得できないときは `NO_NETWORK` / `LOGIN_REQUIRED` などの理由コードで止まる |

xlsx は常に直接変換（LibreOffice は使わない）。

## 処理の全体像

```
URL / ファイル ─┬─ youtube URL          → youtube_to_md.py → 概要＋Transcript（完結）
                └─ 形式判定
                   ├─ html              → 本文抽出＋画像の絶対 URL 化 → Markdown（完結）
                   ├─ pdf               → pdf-to-md-ja へ
                   ├─ docx / pptx       → soffice があれば PDF 化して pdf-to-md-ja へ／無ければ直接変換
                   ├─ xlsx              → 直接変換
                   └─ 旧 doc/xls/ppt    → soffice で新形式にしてから上へ
```

2 スキルの連携は **PDF ファイルのパス 1 つ**。変換結果の先頭には、出典・変換の種類・要約などの
YAML フロントマターが付く（`conversion` は `pdf` / `direct` / `model-read`）。

## インストール

```bash
bash scripts/install.sh                    # ~/.claude/skills へ（スキルごとに確認）
bash scripts/install.sh --dry-run          # 何もせず、行う操作だけ表示
bash scripts/install.sh -y                 # 確認なしでコピー（既存は削除して上書き）
bash scripts/install.sh --target all       # claude / copilot / agents / antigravity すべてへ
bash scripts/install.sh --scope project --project-dir ~/work/app   # そのプロジェクトの中へ
bash scripts/install.sh --dir /path/to/skills                      # 任意の場所へ
bash scripts/install.sh --uninstall        # 配置先から削除
bash scripts/install.sh --list-targets     # 配置先の一覧
bash scripts/install.sh --zip              # dist/ にスキルごとの ZIP（Claude.ai 用）
```

`-t` は `--target` の別名。環境変数 `CLAUDE_SKILLS_DIR` で Claude Code・ユーザースコープの配置先を変えられる
（ほかの配置先は `--dir` を使う）。手動で入れるなら `cp -r url-to-md pdf-to-md-ja ~/.claude/skills/` でもよい。

## Windows の準備

`install.sh` は bash スクリプト。**Git for Windows に付く Git Bash** で実行する。

1. [Git for Windows](https://gitforwindows.org/) を入れ、Git Bash を開く。
2. `echo $HOME` が `/c/Users/<名前>`（= `C:\Users\<名前>`）になっていることを確かめる。
   Claude Code が読む `~/.claude/skills` はこの下にある。別の場所を指すときは `--dir` か `CLAUDE_SKILLS_DIR` で指定する。
3. [uv](https://docs.astral.sh/uv/) を入れる（PowerShell: `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`）。
4. `--zip` を使うときは `zip` コマンドが要る。Git Bash には付いていない。
   無ければ ZIP 生成は止まる（そのときは PowerShell の `Compress-Archive` で、`SKILL.md` がトップに来るよう各スキルのフォルダを固めてもよい）。
5. LibreOffice が無くても pptx / docx は直接変換で動く。図形の図まで再現したいときだけ入れる。

改行コードは `.gitattributes`（`*.sh text eol=lf`）で LF に固定している。
`git clone` 後に `bad interpreter` や `\r` のエラーが出たら、改行が CRLF になっている。

## 使い方

スキル導入後、Claude Code で次のように頼む。

- 「このURLをMarkdownにして」（Web ページ）
- 「URLのPDFをマークダウンにして」（PDF・図の再現つき）
- 「リンク先のdocx/xlsx/pptxを変換して」「このpptxをMarkdownにして」（Office）
- 「このYouTube動画をMarkdownにして」（概要＋トランスクリプト）
- 「このPDFをMarkdownに変換して」（pdf-to-md-ja 単体）

最初の変換の前に、スキルが `--check-env` で環境を一度だけ確かめる。
スクリプトの書き換えはしない（オプションは各スクリプトの `--help` が正）。

## スクリプトを直接使う

`uv run` は依存を自動で解決する（pip 環境なら `python3` に読み替え）。
オプションの一覧は、それぞれ `--help`。

```bash
uv run url-to-md/scripts/fetch_convert.py --check-env                       # 環境の確認
uv run url-to-md/scripts/fetch_convert.py "<URL>" -o out.md                 # 形式を自動判定して変換
uv run url-to-md/scripts/fetch_convert.py --file deck.pptx --route direct -o deck.md
uv run url-to-md/scripts/html_to_md.py "https://example.com/article" -o article.md
uv run url-to-md/scripts/youtube_to_md.py "https://www.youtube.com/watch?v=XXXX" -o video.md
uv run url-to-md/scripts/add_frontmatter.py body.md --url "<URL>" --format html --conversion direct -o final.md
uv run pdf-to-md-ja/scripts/pdf_probe.py doc.pdf --text                     # PDF の素性と本文
uv run pdf-to-md-ja/scripts/pdf_render.py doc.pdf --pages 3 --outdir pages  # ページを PNG に
uv run pdf-to-md-ja/scripts/svg_figures.py --outdir figs                    # 見本の SVG を出す
```

失敗したときは、標準エラーの最後の 3 行（`[stop]` / `[next]` / `REASON=<理由コード>`）と終了コード
（0 成功 / 1・3 失敗 / 2 引数の誤り / 4 必要な道具が無い）で原因が分かる。

## テスト

```bash
uv run --python 3.12 --with pytest pytest -q
```

## ディレクトリ構成

```
├── url-to-md/            # 取得・形式判定・HTML/Office/YouTube の変換
│   ├── SKILL.md          # エントリポイント（短い）
│   ├── workflows/        # url / file / youtube / office-direct / finish の手順
│   └── scripts/          # fetch_convert / html_to_md / youtube_to_md / add_frontmatter
├── pdf-to-md-ja/         # PDF → Markdown（図の SVG / Mermaid 再現）
│   ├── references/       # 図の判定・Mermaid パターン・SVG ロジック
│   └── scripts/          # pdf_probe / pdf_render / svg_figures
├── scripts/install.sh    # インストール支援（Claude Code / Copilot / .agents / Antigravity、ZIP）
├── tests/                # pytest
├── CHANGELOG
└── .gitattributes        # *.sh は LF
```
