# 図の判定プロンプト

Step 3 の「問1：量か構造か」で使う、Visionへの問い合わせ形式。
図を含むページ画像に対してこの質問を投げ、返答で分岐する。

## 判定プロンプト

```
この図について、以下をJSONで答えてください。推測でなく図から読み取れる範囲で：

{
  "purpose": "quantity" または "structure",
      // quantity = 数値の大小・割合・面積が主題
      // structure = 項目間の関係・流れ・階層が主題
  "node_count": 箱や項目の数（整数）,
  "connections": {
    "has": true/false,           // 線や矢印があるか
    "kind": "order|transition|hierarchy|correspondence|none"
      // order=順序/フロー, transition=状態遷移,
      // hierarchy=階層/分類, correspondence=対応関係
  },
  "original_form": "diagram" または "text",
      // 元スライドで図として描かれているか、文章/箇条書きか
  "worth_converting": true/false
      // 図にした方が理解しやすいか。
      // 箇条書きで十分・2項目以下の対比などは false
}
```

## 返答からの分岐

```
worth_converting == false
    → 図化しない。テーブルor箇条書きで表現

purpose == "quantity"
    → 円/割合(シンプル)        : Mermaid pie
      ドーナツ(中央テキスト要) : SVG donut_chart
      バブル/面積比較          : SVG bubble_chart

purpose == "structure" かつ worth_converting == true
    connections.kind に応じて頻出5記法から第一候補：
      order        → flowchart
      transition   → flowchart（または stateDiagram）
      hierarchy    → mindmap
      correspondence→ quadrantChart（2軸なら）/ flowchart
    時系列が明確       → timeline
    頻出で無理         → 他のMermaid記法 → SVG → ラスタ化
```

## 判定のコツ

- `node_count <= 2` かつ単純な対比は、図よりテーブルが読みやすい
- 「量」と「構造」の両面を持つ図（例：フロー上に数値）は、主題がどちらかで決める。
  数値が主役ならquantity、流れが主役ならstructure
- 元が文章（original_form == "text"）なのに無理に図化しない。
  「図にして理解しやすくなるか」が満たされて初めて変換する
