---
name: url-to-md
description: >
  URL または手元・添付のファイル（html / pdf / docx / xlsx / pptx）と YouTube 動画を、
  Markdown に変換するスキル。「このURLをMarkdownにして」「URLの資料をmdに変換」
  「このWebページをMarkdownにして」「リンク先のdocx/xlsx/pptxを変換して」
  「URLのPDFをマークダウンにして」「YouTubeの動画をMarkdownにして」
  「動画の文字起こしをmdに」のほか、「この添付のpptxをMarkdownにして」
  「手元のxlsxをmdに」のように添付ファイル・手元のファイルを渡されたときにも使う。
  HTMLは本文抽出+画像の絶対URL化、YouTubeは概要+字幕で完結する。
  PDF/Officeは図の再現のため、PDF経由なら pdf-to-md-ja に委譲し、
  PDF化できない環境ではスクリプトで直接変換する。
  URLの取得ができないときは、変換を始めずに理由を返して終了する。
---

# url-to-md — URL・ファイルを Markdown に変換する

**このファイルは入口**。次の順で進める。手順の本文は `workflows/` にある。
以降、`<SKILL_DIR>` は **この SKILL.md があるフォルダ**の絶対パス、
スクリプトは `<SKILL_DIR>/scripts/` にある。

## 守ること

1. **スクリプトを書き換えない。** 失敗したら、下の「失敗したとき」の表に従う。
   表に無い失敗は、書き換えずにユーザーへ報告して止まる（書き換えが必要なら、
   それはスキルの不具合として報告する）。
2. **オプションは `--help` が正。** 使う前に、そのスクリプトの `--help` を 1 回読む。
   この SKILL.md とワークフローには、オプションの一覧を書かない。
3. 足りない依存を入れてよいのは、**ユーザーに確認してから**。

## Step 0. 環境を確認する（最初に 1 回）

```bash
<RUN> <SKILL_DIR>/scripts/fetch_convert.py --check-env
```

`<RUN>` は、使えるものを次の順で選ぶ: `uv run` → `python` → `python3` → `py`。
`python` 系は 3.10 以上が必要（`--check-env` の `python` の行で分かる）。
`uv run` なら依存は自動で入る。`python` 系のとき、足りない依存は
`--check-env` の `py:` の行に「無し」と出るので、ユーザーに確認して `pip install` する。

結果で経路を決める。

| 状況 | 進め方 |
|---|---|
| スクリプトを実行できる | 下の「モード」へ |
| スクリプトを実行できない（Claude.ai でコード実行が使えない など） | `workflows/file.md` の「Claude が直接読む」へ。**先に、図は再現しないことと、URL は取得できないことを伝える** |

通信できるかは `--check-env` では分からない。URL を取得して `NO_NETWORK` が返ったら、
**変換を始めず**、下の形で返して終了する。

## モード

| 依頼・入力 | 読むファイル |
|---|---|
| YouTube の URL | `workflows/youtube.md` |
| html・pdf・docx・xlsx・pptx の URL | `workflows/url.md` |
| 添付・手元のファイル | `workflows/file.md` |
| docx / xlsx / pptx を**直接変換**で Markdown にしたとき（`ROUTE=direct`） | 上に加えて `workflows/office-direct.md` |
| PDF が得られたとき（`ROUTE=pdf` ほか） | `pdf-to-md-ja` スキルに PDF のパスを渡す |
| 変換後の仕上げ（フロントマター・確認） | `workflows/finish.md` |

## 失敗したとき

スクリプトは、失敗すると標準エラーの最後の 3 行に `[stop]` 理由・`[next]` 次の手段・
`REASON=<理由コード>` を出す（トレースバックは出ない）。終了コードは `--help` の通り。
`REASON=` を見て、次の表で動く。

| REASON | 動き |
|---|---|
| `NO_NETWORK` | 終了。ファイルとして渡してもらう |
| `UNSUPPORTED_SCHEME` | 終了。http / https の URL を頼む |
| `HTTP_ERROR` | 終了。URL を確認してもらう |
| `LOGIN_REQUIRED` | 終了。保存したファイルを渡してもらう。**ユーザーが明示したときだけ** `--force` で続行 |
| `JS_RENDERED` | 同上 |
| `EMPTY_CONTENT` | 終了。ファイルが空かを確認してもらう |
| `FETCH_FAILED` | 終了。URL・通信を確認してもらう |
| `CONVERT_FAILED` | 終了。変換できなかった旨と `[stop]` の文を伝える |
| `UNSUPPORTED_FORMAT` | 終了。対応形式（html・pdf・docx・xlsx・pptx）を伝える |
| `LEGACY_NEEDS_SOFFICE` | 終了。LibreOffice を入れるか、新しい形式で保存し直してもらう |
| `MISSING_TOOL` | 何が無いかを伝え、**ユーザーに確認してから**入れる。入れられなければ終了 |
| `UNEXPECTED` | スクリプトは直さず、標準エラーを添えて報告して終了 |

終了するときは、**ファイルを書かずに**、返答を次の形にする。

```
変換できませんでした。
- 理由: <理由コード> — <日本語の理由>
- 対象: <URL またはファイル名>
- 次の手段: <1〜2 個>
```

## 変換の種類（frontmatter の `conversion`）

| 値 | 意味 |
|---|---|
| `pdf` | PDF 経由（pdf-to-md-ja）で作った |
| `direct` | スクリプトで直接変換した |
| `model-read` | 添付を Claude が直接読んで起こした（図は再現していない） |

## 関連スキル

- **pdf-to-md-ja**: PDF → Markdown。目次・表の保持、図の SVG / Mermaid 再現。
