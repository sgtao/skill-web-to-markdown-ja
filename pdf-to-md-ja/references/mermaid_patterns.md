# Mermaidパターン集（頻出5記法）

Step 3 の構造図変換で使う。まずこの5つで表現できるか試す。
無理なら他のMermaid記法→SVG→ラスタ化へフォールバック。

各記法は ```` ```mermaid ```` コードブロックでMarkdownに埋め込む。
GitHub・VS Code・多くのMarkdownビューアでそのまま描画される。

## 目次
- [flowchart（フロー・関係・循環）](#flowchart)
- [pie（円・割合）](#pie)
- [timeline（年表・沿革）](#timeline)
- [mindmap（階層・分類）](#mindmap)
- [quadrantChart（2軸マトリクス）](#quadrantchart)
- [フォールバックの考え方](#フォールバック)

---

## flowchart

最頻出。順序・分岐・循環・組織の流れなど「ノードとエッジ」全般。
向きは `LR`（左→右）/ `TD`（上→下）から図に合わせて選ぶ。

```mermaid
flowchart LR
  A[IT企業] --> B[DX企業]
  B --> C{全方位の制度改革}
  C --> D[報酬・評価]
  C --> E[人材獲得・育成]
```

循環（今回の「自律と信頼」のような双方向）：

```mermaid
flowchart LR
  社員 -->|新たな行動を起こす| FUJITSU
  FUJITSU -->|成長を促す| 社員
```

- ノードラベルは元の文言をそのまま使う
- 分岐の判断ノードは `{ }`、通常ノードは `[ ]`
- エッジのラベルは `-->|ラベル|` で付ける

---

## pie

円・割合。シンプルな構成比向け。中央テキストや細かい色指定が要るなら
SVG `donut_chart` を使う（pieは非対応）。

```mermaid
pie title 仕向先別売上収益
  "日本" : 64.9
  "欧州" : 18.7
  "アジアパシフィック" : 7.3
  "アメリカ" : 5.4
  "東アジア" : 3.5
  "その他" : 0.2
```

数値はそのまま渡せば割合は自動計算される。

---

## timeline

年表・沿革・ロードマップ。時系列が主題のとき。

```mermaid
timeline
  title 会社沿革
  1935 : 創立
  1992 : 中国拠点設立
  2001 : インド拠点設立
  2009 : ポーランド拠点設立
```

`時点 : 出来事` の形。1時点に複数イベントは ` : ` を続ける。

---

## mindmap

階層・分類・マインドマップ。中心から枝分かれする構造。

```mermaid
mindmap
  root((Fujitsu Uvance))
    Vertical Areas
      Sustainable Manufacturing
      Consumer Experience
      Healthy Living
      Trusted Society
    Horizontal Areas
      Digital Shifts
      Business Applications
      Hybrid IT
```

インデントで階層を表す。中心ノードは `(( ))` で囲む。

---

## quadrantChart

2軸4象限。PPM、優先度マトリクス、ポジショニングマップなど。

```mermaid
quadrantChart
  title 事業ポートフォリオ
  x-axis 市場成長率 低 --> 高
  y-axis 市場シェア 低 --> 高
  quadrant-1 花形
  quadrant-2 問題児
  quadrant-3 負け犬
  quadrant-4 金のなる木
  事業A: [0.3, 0.6]
  事業B: [0.7, 0.8]
```

各項目は `名前: [x, y]`（0〜1の座標）。位置はVisionで読み取る。

---

## フォールバック

頻出5記法で表現できないとき、順に下げる：

1. **他のMermaid記法を検討**（頻出ではないが該当すれば使う）
   - `sequenceDiagram`：時系列のやりとり（誰が誰に何を送るか）
   - `stateDiagram-v2`：状態遷移が主題
   - `gantt`：日程・期間・スケジュール
   - `erDiagram`：テーブル間のリレーション
   - これらは「全部覚える」必要はない。図の主題に合うものがあれば採用する程度でよい

2. **SVG**：Mermaidの自動レイアウトでは崩れる独自配置（座標を厳密に置きたい循環図など）
   → `scripts/svg_figures.py` の `flow_cycle` 等

3. **ラスタ化**：独自デザインで図の再現自体が困難、地図など
   → `pdftoppm -png -r 150 -f N -l N`

判断の軸は一貫して **「図にして認識・理解しやすくなるか」**。
Mermaidで素直に描けるならMermaid、量の正確さが要るならSVG、
再現困難ならラスタ、と段階的に選ぶ。
