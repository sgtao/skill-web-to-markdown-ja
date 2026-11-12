# YouTube — 概要 + トランスクリプト

YouTube の URL（watch・youtu.be・shorts・live・embed）は、`fetch_convert.py` ではなく
専用のスクリプトで変換する。watch ページを `html_to_md.py` に渡さない（JS 描画で本文が取れない）。

```bash
<RUN> <SKILL_DIR>/scripts/youtube_to_md.py --help
```

読んだうえで、URL を渡して変換する。`<RUN>` と `<SKILL_DIR>` は SKILL.md の定義。

出力の形: `# タイトル` → `## 概要`（説明欄全文）→ `## Transcript`（段落頭に `**[MM:SS]**`）。

字幕の選び方（スクリプトが決める）:
- 動画の元言語の手動字幕 > 元言語の自動生成字幕。**翻訳字幕は使わない**。
- 字幕が無い動画は、概要だけを出力して「字幕がありません」の注記が付く（成功扱い）。

このスクリプトは理由コードを出さない。失敗は終了コード 1 と `[error]` の行で分かる。
`yt-dlp` が無いときは `uv run` なら自動で入る。それ以外は、ユーザーに確認してから入れる。
直せない失敗は、書き換えずに SKILL.md の「失敗したとき」の形で報告して終了する
（理由コードは `UNEXPECTED` として扱う）。

終わったら `workflows/finish.md` へ（`--format youtube` と、チャンネル名・公開日・長さを付ける）。
