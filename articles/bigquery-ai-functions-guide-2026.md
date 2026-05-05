---
title: "BigQuery AI関数入門：SQLだけでAI分析を実行する"
emoji: "🔍"
type: "tech"
topics: ["bigquery", "gcp", "ai", "sql"]
published: false
status: "draft"
publication_name: "correlate_dev"
---

## はじめに

2026年1月、BigQueryに大きな変化がありました。`AI.GENERATE` と `AI.GENERATE_TABLE` が General Availability（GA）に移行し、SQLだけでGeminiを直接呼び出せる時代が本格的に到来しました。

これまで BigQuery から生成AIを呼び出すには、`CREATE MODEL` でリモートモデルオブジェクトを作成し、BigQuery接続を構成するという準備が必要でした。新しい AI 関数群ではその手順が不要になり、IAM ロールを1つ付与するだけで即座に使い始めることができます。

本記事では2026年4月時点の最新情報をもとに、BigQuery AI 関数の全容、コピペで動く SQL 例、料金の考え方、そして旧来の `ML.GENERATE_TEXT` との使い分けを整理します。

---

## BigQuery AI関数とは

BigQuery AI 関数は大きく2つのカテゴリに分かれます。

**汎用AI関数（General-purpose）** はモデル・プロンプト・パラメータをすべて自分で制御する関数です。`AI.GENERATE` がその代表で、テキスト・画像・動画・音声・ドキュメントを入力に受け取り、自由なプロンプトでモデルを呼び出せます。

**マネージドAI関数（Managed）** はBigQueryがモデル選択とプロンプト最適化を自動で行う関数です。`AI.CLASSIFY`・`AI.IF`・`AI.SCORE` などが該当し、分類・フィルタリング・スコアリングといった典型的なユースケースを最小限のSQLで実現できます。

また、2026年以降は **End User Credentials（EUC）** の仕組みにより、BigQuery接続オブジェクトを作成しなくてもAI関数を実行できます。IAMで `Vertex AI User` ロールを付与するだけで `connection_id` パラメータが不要になりました（[公式ドキュメント: Set permissions for generative AI functions](https://docs.cloud.google.com/bigquery/docs/permissions-for-ai-functions)）。

---

## セットアップ ： EUCを使った最速の始め方

```sql
-- EUC利用時: 以下のIAMロールをユーザーに付与するだけで利用可能
-- roles/aiplatform.user（Vertex AI User）
--
-- プロジェクトオーナーはこの設定も不要
-- connection_id パラメータは完全にオプション
```

従来の `ML.GENERATE_TEXT` では Cloud Resource 接続の作成 → サービスアカウントへのロール付与 → `CREATE MODEL` の3ステップが必要でした。EUC によりこの手順が1ステップに短縮されています。

---

## 関数一覧クイックリファレンス（2026年4月時点）

### 汎用AI関数

| 関数名 | 説明 | 出力型 | GA/Preview |
|--------|------|--------|------------|
| `AI.GENERATE` | テキスト・画像・動画・音声・ドキュメントから任意のテキストまたは構造化データを生成 | STRING / STRUCT | 【GA】（2026/01） |
| `AI.GENERATE_TABLE` | AI.GENERATE のTable-Valued Function版。構造化出力に特化 | TABLE | 【GA】（2026/01） |
| `AI.GENERATE_TEXT` | テキスト生成（マルチモーダル対応。リモートモデル使用） | STRING | GA（旧来） |
| `AI.GENERATE_EMBEDDING` | テキスト・画像データのEmbeddingベクトルを生成 | ARRAY\<FLOAT64\> | GA（旧来） |
| `AI.GENERATE_BOOL` | プロンプトに対してBOOL値を返す特化型 | BOOL | Preview / GA移行中 |
| `AI.GENERATE_INT` | プロンプトに対してINT64値を返す特化型 | INT64 | Preview / GA移行中 |

出典: [New BigQuery gen AI functions for better data analysis | Google Cloud Blog](https://cloud.google.com/blog/products/data-analytics/new-bigquery-gen-ai-functions-for-better-data-analysis)

### マネージドAI関数

| 関数名 | 説明 | 出力型 | GA/Preview |
|--------|------|--------|------------|
| `AI.CLASSIFY` | テキストをユーザー定義カテゴリに分類 | STRUCT | 【Public Preview】（2025/11） |
| `AI.IF` | 自然言語条件でフィルタリング・結合条件を記述 | BOOL | 【Public Preview】（2025/11） |
| `AI.SCORE` | 自然言語ルーブリックに基づいてスコアリング | FLOAT64 | 【Public Preview】（2025/11） |
| `AI.AGG` | 集約操作を自然言語で記述（GROUP BY等と組み合わせ） | STRING | 【Public Preview】 |
| `AI.EMBED` | テキスト・画像のEmbeddingを生成（マネージド版） | ARRAY\<FLOAT64\> | 【Preview】 |
| `AI.SIMILARITY` | 2つの入力のコサイン類似度を計算 | FLOAT64 | 【Preview】 |
| `AI.SEARCH` | 自律的埋め込みを利用したセマンティック検索 | TABLE | 【Preview】 |

出典: [SQL reimagined for the AI era with BigQuery AI functions | Google Cloud Blog](https://cloud.google.com/blog/products/data-analytics/sql-reimagined-for-the-ai-era-with-bigquery-ai-functions)

:::message alert
マネージドAI関数（AI.CLASSIFY、AI.IF、AI.SCORE等）はPublic Preview段階です。本番環境での利用はSLAが保証されず、自己責任での運用となります。GA前提のシステムへの組み込みは慎重に判断してください。
:::

---

## 実践SQL例

### 1. テキスト要約（AI.GENERATE）

BigQuery公開データセットのBBCニュース記事を日本語で1文に要約します。

```sql
SELECT
  title,
  AI.GENERATE(
    CONCAT('以下のニュース記事を日本語で1文に要約してください: ', body)
  ).result AS summary
FROM `bigquery-public-data.bbc_news.fulltext`
LIMIT 10;
```

`endpoint` を省略した場合、デフォルトモデルは **Gemini 2.5 Flash** が自動選択されます（[公式ドキュメント: The AI.GENERATE function](https://docs.cloud.google.com/bigquery/docs/reference/standard-sql/bigqueryml-syntax-ai-generate)）。

### 2. 感情分析（AI.GENERATE + endpointパラメータ）

モデルを明示的に指定した感情分析の例です。

```sql
SELECT
  review,
  AI.GENERATE(
    CONCAT(
      'この商品レビューの感情を positive / negative / neutral のいずれか1語で答えてください。レビュー: ',
      review
    ),
    endpoint => 'gemini-2.5-flash'
  ).result AS sentiment
FROM `mydataset.product_reviews`
LIMIT 100;
```

### 3. 構造化出力（OUTPUT_SCHEMAを使った情報抽出）

`output_schema` パラメータを使うと、生成結果をSTRUCT型で受け取ることができます。

```sql
-- 患者データから構造化情報を抽出
SELECT
  extracted.name,
  extracted.age,
  extracted.phone_number
FROM (
  SELECT
    AI.GENERATE(
      patient_description,
      output_schema => 'name STRING, age INT64, phone_number STRING'
    ) AS extracted
  FROM `mydataset.patient_data`
);
```

住所の正規化など、データクレンジング用途にも応用できます。

```sql
-- 非標準的な住所表記を構造化して正規化
SELECT
  raw_address,
  AI.GENERATE(
    CONCAT(
      '以下の住所を都道府県・市区町村・番地の形式に正規化してください。住所のみを返してください: ',
      raw_address
    ),
    endpoint => 'gemini-2.5-flash',
    output_schema => 'prefecture STRING, city STRING, street STRING'
  ) AS normalized_address
FROM `mydataset.raw_customer_data`
WHERE raw_address IS NOT NULL;
```

### 4. 自動分類（AI.CLASSIFY）： Public Preview

カテゴリ一覧を渡すだけで自動分類します。

```sql
-- BBCニュースをカテゴリに分類
SELECT
  title,
  AI.CLASSIFY(
    body,
    categories => ['tech', 'sport', 'business', 'politics', 'entertainment', 'other']
  ) AS category
FROM `bigquery-public-data.bbc_news.fulltext`
LIMIT 100;
```

### 5. 自然言語フィルタリング（AI.IF）： Public Preview

WHERE句に自然言語条件を直接書けます。SQLエンジニア以外にも読みやすいクエリになります。

```sql
-- ネガティブなカスタマーレビューのみを抽出
SELECT *
FROM `mydataset.customer_reviews`
WHERE AI.IF('このレビューは商品またはサービスへの不満を表している', review_text);

-- 画像データのフィルタリング（マルチモーダル）
SELECT *
FROM `mydataset.product_images`
WHERE AI.IF('この画像は屋外で撮影されたものである', image_uri);
```

### 6. スコアリング（AI.SCORE）： Public Preview

自然言語で記述したルーブリックに基づいてスコアを付与します。採用・コンテンツ評価・優先度付けなどに活用できます。

```sql
SELECT
  candidate_name,
  resume_text,
  AI.SCORE(
    resume_text,
    rubric => 'Python・BigQuery・機械学習の実務経験を持ち、プロダクトオーナーとのコミュニケーションができるエンジニアとしての適性'
  ) AS relevance_score
FROM `mydataset.candidates`
ORDER BY relevance_score DESC
LIMIT 20;
```

### 7. 集約分析（AI.AGG）： Public Preview

GROUP BY と組み合わせ、カテゴリごとにAI要約を生成できます。

```sql
-- カテゴリごとにレビューを要約
SELECT
  product_category,
  AI.AGG(
    review_text,
    'このカテゴリの全レビューから主な不満点を3点箇条書きで要約してください'
  ) AS review_summary
FROM `mydataset.product_reviews`
GROUP BY product_category;
```

### 8. ベクトル検索（AI.EMBED + VECTOR_SEARCH）： Preview

Embeddingを生成してRAG（Retrieval-Augmented Generation）的な検索パイプラインを構築できます。

```sql
-- Step 1: ドキュメントテーブルにEmbeddingを生成
CREATE OR REPLACE TABLE `mydataset.documents_with_embeddings` AS
SELECT
  doc_id,
  content,
  AI.EMBED(content).result AS embedding
FROM `mydataset.documents`;

-- Step 2: VECTOR_SEARCH でベクトル検索
SELECT
  query.query_text,
  base.doc_id,
  base.content,
  distance
FROM VECTOR_SEARCH(
  TABLE `mydataset.documents_with_embeddings`,
  'embedding',
  (SELECT AI.EMBED('BigQueryでAI分析を実行する方法').result AS embedding),
  top_k => 5
);
```

### 9. 自律的埋め込み（AI.SEARCH）： Preview

テーブル定義時に Embedding 列を `GENERATED ALWAYS AS` で定義すると、INSERT のたびに自動的に Embedding が生成されます。ETL パイプラインを別途用意する必要がなくなります（[公式ドキュメント: Autonomous embedding generation](https://docs.cloud.google.com/bigquery/docs/autonomous-embedding-generation)）。

```sql
-- Step 1: 自律的埋め込み付きテーブルを作成
CREATE TABLE `mydataset.products` (
  name STRING,
  description STRING,
  description_embedding STRUCT<result ARRAY<FLOAT64>, status STRING>
    GENERATED ALWAYS AS (
      AI.EMBED(
        description,
        endpoint => 'text-embedding-005'
      )
    ) STORED OPTIONS(asynchronous = TRUE)
);

-- Step 2: データ挿入（Embeddingは自動生成）
INSERT INTO `mydataset.products` (name, description) VALUES
  ('ラウンジチェア', 'くつろぎのための快適な椅子'),
  ('スーパースリンガーズ', 'ファミリー向けの楽しいボードゲーム'),
  ('百科事典セット', '詳細な知識を収録した全集');

-- Step 3: セマンティック検索
SELECT *
FROM AI.SEARCH(
  TABLE `mydataset.products`,
  'description',
  '楽しいおもちゃ'
);
```

---

## 料金体系

BigQuery AI 関数の料金は「BigQuery ML のデータ処理料金」と「Vertex AI のモデル呼び出し料金」の2層構造です。

### BigQuery ML データ処理料金
- AI 関数を含むクエリはBigQuery MLのデータ処理料金が発生します（$6.25/TB）

### Vertex AI モデル呼び出し料金

Gemini 2.0以降のモデルを使う場合、AI.GENERATE の呼び出しは **Batch API レート** で自動的に課金されます。これはオンデマンドAPIより50%安い料金です（[公式: Choose a text generation function](https://docs.cloud.google.com/bigquery/docs/choose-text-generation-function)）。

| モデル | 入力トークン単価 | 出力トークン単価 | Batch API（自動適用） |
|--------|-----------------|-----------------|----------------------|
| Gemini 2.5 Flash | $0.30 / 百万トークン | $2.50 / 百万トークン | 入力 $0.15 / 出力 $1.25 |

出典: [Vertex AI Pricing | Google Cloud](https://cloud.google.com/vertex-ai/generative-ai/pricing)

### 処理行数の目安

| 条件（6時間ジョブ） | 処理可能行数 |
|--------------------|------------|
| 入力2,000トークン・出力50トークン | 約760万行 |
| 入力10,000トークン・出力3,000トークン | 約100万行 |
| 最新スケーラビリティ改善後（LLMモデル） | 数千万行（100倍以上向上） |
| 最新スケーラビリティ改善後（埋め込みモデル） | 数億行（30倍以上向上） |

出典: [BigQuery enhancements to boost gen AI inference | Google Cloud Blog](https://cloud.google.com/blog/products/data-analytics/bigquery-enhancements-to-boost-gen-ai-inference)

### マネージドAI関数の料金
`AI.IF` / `AI.CLASSIFY` / `AI.SCORE` はPreview期間中のため、具体的な課金体系は公式ドキュメントで最新情報をご確認ください。BigQueryが内部でモデルを自動選択するため、コスト最適化が自動的に行われます。

### Gemini in BigQuery コア機能
SQLアシスト・クエリ補完等のGemini in BigQueryのコア機能は追加料金なしで利用できます。AI関数（AI.GENERATE等）のVertex AI呼び出し料金とは別物です。

---

## ML.GENERATE_TEXT との比較

新機能との使い分けを表にまとめます。

| 項目 | ML.GENERATE_TEXT（旧・レガシー） | AI.GENERATE（新・推奨） |
|------|----------------------------------|------------------------|
| リリース時期 | 2023年〜 | 2025年〜（GA: 2026/01） |
| CREATE MODEL | 【必要】 | 【不要】 |
| モデル指定 | リモートモデルオブジェクト | `endpoint` パラメータで直接指定 |
| デフォルトモデル | なし（モデル作成必須） | 【gemini-2.5-flash】（自動） |
| 入力形式 | テキストのみ | テキスト・画像・動画・音声・ドキュメント |
| 構造化出力 | 非対応 | `output_schema` で対応 |
| EUC対応 | 非対応（接続必須） | 【対応】（接続オプション） |
| Batch API課金 | 非対応 | 【自動適用】（Gemini 2.0以降） |
| 外部モデル | Claude / Llama / Mistral | Claude / Llama / Mistral |
| 推奨度 | 非推奨（レガシー） | 【推奨】 |

出典: [Generative AI overview | BigQuery | Google Cloud Documentation](https://docs.cloud.google.com/bigquery/docs/generative-ai-overview)

旧来関数の書き方（参考用）：

```sql
-- ML.GENERATE_TEXT: 旧来の方法（参考）
CREATE OR REPLACE MODEL `mydataset.gemini_model`
  REMOTE WITH CONNECTION `us-central1.my_connection`
  OPTIONS (endpoint = 'gemini-2.0-flash');

SELECT *
FROM ML.GENERATE_TEXT(
  MODEL `mydataset.gemini_model`,
  (
    SELECT CONCAT('感情分析してください（positive/negative）: ', review) AS prompt
    FROM `mydataset.reviews`
    LIMIT 10
  ),
  STRUCT(
    0.2 AS temperature,
    100 AS max_output_tokens
  )
);
```

**移行の判断基準**

- 新規開発: `AI.GENERATE` を使う
- 既存の `ML.GENERATE_TEXT` コード: `AI.GENERATE` または `AI.GENERATE_TEXT` に段階的移行
- 外部モデル（Anthropic Claude等）をリモートモデルとして管理したい場合のみ `CREATE MODEL` が引き続き有効

---

## 制約事項と注意点

### リージョン制約

`AI.GENERATE` はUS・EUのマルチリージョン、およびGeminiモデルをサポートするすべてのリージョンで利用可能です（[公式: The AI.GENERATE function](https://docs.cloud.google.com/bigquery/docs/reference/standard-sql/bigqueryml-syntax-ai-generate)）。

- **USマルチリージョン** データセット → ジョブロケーションに `us-central1` を使用
- **EUマルチリージョン** データセット → ジョブロケーションに `europe-west4` を使用
- **Claude / Llama / Mistral** の外部モデル: EUマルチリージョンで `eu-west2` と `eu-west6` は非対応
- **アジアリージョン（asia-northeast1等）**: 2026年4月時点でのAI関数対応状況は未確認です。東京リージョンでの利用を検討する場合は公式ドキュメントを個別に確認してください

出典: [BigQuery locations | Google Cloud Documentation](https://docs.cloud.google.com/bigquery/docs/locations)

### EUCの制限事項

EUC（End User Credentials）でのAI関数実行はクエリジョブが **48時間以上かかる場合には利用不可** です。大規模バッチジョブでは BigQuery Connection（サービスアカウント）を使用してください。

### スループット・クォータ

- Vertex AI Gemini モデルはトークン/分（TPM）クォータあり
- Dynamic Shared Quota（DSQ）は事前定義の上限なし（ただし保証なし）
- 本番環境での高スループット要件には Provisioned Throughput の購入を推奨

出典: [BigQuery enhancements to boost gen AI inference | Google Cloud Blog](https://cloud.google.com/blog/products/data-analytics/bigquery-enhancements-to-boost-gen-ai-inference)

### Preview機能の利用について

`AI.CLASSIFY`・`AI.IF`・`AI.SCORE`・`AI.EMBED`・`AI.SEARCH` はいずれもPreview段階です。SLAの保証がなく、APIの仕様変更が発生する可能性があります。本番環境での利用は自己責任での判断が必要。

---

## まとめ

BigQuery AI 関数の主なポイントをまとめます。

- **`AI.GENERATE` が2026年1月にGA**。CREATE MODEL 不要・EUC対応で、最速でSQLからGeminiを呼び出せる
- **汎用AI関数（AI.GENERATE）** はモデル・プロンプトを自由に制御、**マネージドAI関数（AI.CLASSIFY等）** はBigQueryが最適化を自動化
- **Batch API料金が自動適用**（Gemini 2.0以降）。大量データの処理コストを従来比50%削減
- **マネージドAI関数はPublic Preview** 段階のためSLA保証なし。本番利用には注意が必要
- **アジアリージョン（asia-northeast1）対応状況は未確認**。国内データのみを扱う場合は確認が必要

次のステップとしては、`AI.GENERATE` でまず感情分析や要約のプロトタイプを作り、`AI.CLASSIFY` や `AI.IF` のGA移行を待ちながらマネージドAI関数の評価を進めるのがよいでしょう。

---

## 参考リンク

- [Introduction to AI in BigQuery](https://docs.cloud.google.com/bigquery/docs/ai-introduction)
- [The AI.GENERATE function](https://docs.cloud.google.com/bigquery/docs/reference/standard-sql/bigqueryml-syntax-ai-generate)
- [Choose a text generation function](https://docs.cloud.google.com/bigquery/docs/choose-text-generation-function)
- [Set permissions for generative AI functions](https://docs.cloud.google.com/bigquery/docs/permissions-for-ai-functions)
- [Vertex AI Pricing](https://cloud.google.com/vertex-ai/generative-ai/pricing)
- [New BigQuery gen AI functions for better data analysis | Google Cloud Blog](https://cloud.google.com/blog/products/data-analytics/new-bigquery-gen-ai-functions-for-better-data-analysis)
- [SQL reimagined for the AI era with BigQuery AI functions | Google Cloud Blog](https://cloud.google.com/blog/products/data-analytics/sql-reimagined-for-the-ai-era-with-bigquery-ai-functions)
- [BigQuery enhancements to boost gen AI inference | Google Cloud Blog](https://cloud.google.com/blog/products/data-analytics/bigquery-enhancements-to-boost-gen-ai-inference)
- [Autonomous embedding generation](https://docs.cloud.google.com/bigquery/docs/autonomous-embedding-generation)
