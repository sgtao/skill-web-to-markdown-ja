# 仕上げ — フロントマターと出力の確認

## 1. フロントマターを付ける

```bash
<RUN> <SKILL_DIR>/scripts/add_frontmatter.py --help
```

読んだうえで、変換済みの Markdown に付ける。`--url` には元の URL（ファイルのときはそのパス）、
`--format` には元の形式、`--conversion` には SKILL.md の「変換の種類」の値を渡す。
`--title` と `--summary` は、**本文を読んで的確なものを明示する**（省略すると本文から機械抽出される）。
要約は 200 字以内。すでにフロントマターがある Markdown には付かない（警告だけ）。

項目: `title` `source_url` `source_format` `conversion` `has_figures` `summary` `retrieved_at`
（YouTube のみ、任意で `channel` `published` `duration`）。

## 2. 出力を確認する

- html: 本文だけか（ナビ・広告が残っていないか）。画像の URL が絶対か。
- 本文が同じ段落の繰り返しになっていないか。
- pdf 経由: 図が SVG / Mermaid / 抽出画像として再現されているか。
- 直接変換: 末尾に「この変換で再現できなかったもの」があるか。
- xlsx はシート見出し、pptx はスライド番号のコメントで区切られているか。
- YouTube: 概要が全文入っているか。Transcript の段落頭にタイムスタンプがあるか。

## 3. 報告する

返答には、出力したファイルのパス、`conversion` の値、再現できなかったものを書く。
