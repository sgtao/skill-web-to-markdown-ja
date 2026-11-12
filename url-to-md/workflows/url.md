# URL（html・pdf・docx・xlsx・pptx）

前提: SKILL.md の Step 0 を済ませ、スクリプトを実行できる。`<RUN>` と `<SKILL_DIR>` は SKILL.md の定義。
YouTube の URL はここではなく `workflows/youtube.md`。

## 1. `--help` を読む

```bash
<RUN> <SKILL_DIR>/scripts/fetch_convert.py --help
```

## 2. 変換する

`fetch_convert.py` に URL を渡す。形式は、拡張子・Content-Type・中身から自動で判定され、
**どの経路になったかは標準エラーの `ROUTE=` で分かる**。結果は次の 2 通り。

| `ROUTE=` | 標準出力 | 次にすること |
|---|---|---|
| `direct` | Markdown の本文（`-o` を付けたときはファイルに書く） | html はそのまま `workflows/finish.md` へ。docx / xlsx / pptx は `workflows/office-direct.md` も読む |
| `pdf` | PDF のパス 1 行 | そのパスを **pdf-to-md-ja** に渡して Markdown にする（図を SVG / Mermaid で再現）。終わったら `workflows/finish.md` へ |

経路の既定（`--route auto`）:
- html → `direct`
- pdf → `pdf`
- docx・pptx → LibreOffice（soffice）があれば `pdf`、無ければ `direct`
- xlsx → `direct`（表が崩れにくいため）
- 旧 doc・xls・ppt → soffice で新形式にしてから上へ。soffice が無ければ `LEGACY_NEEDS_SOFFICE` で終了

ユーザーが経路を指定したとき（「図を再現して」「PDF を経由しないで」など）は `--route` で選ぶ。
**Markdown の本文がほしいのに PDF のパスが返ってきたら**、`--route direct` でやり直す。

## 3. 結果の見方

- PDF の置き場所は `--outdir`。同名の PDF があれば `-2`、`-3` が付く。標準出力のパスをそのまま使う。
- 画像を取り出したときは、Markdown の隣の `<名前>_assets/` に入る。Markdown と一緒に渡す。
- `-o` は Markdown 専用。PDF の置き場所には使わない。

失敗したときは SKILL.md の「失敗したとき」に従う。
