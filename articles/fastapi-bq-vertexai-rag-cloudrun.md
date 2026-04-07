---
title: "FastAPI + BigQuery + Vertex AI でRAGシステムを作ってCloud Runに本番デプロイするまで"
emoji: "🧠"
type: "tech"
topics: ["fastapi", "bigquery", "vertexai", "cloudrun", "python"]
published: false
status: "draft"
publication_name: "correlate_dev"
---

## はじめに

LangChainやLlamaIndexを使ったRAG実装の記事は多いですが、Vertex AI SDKを直接使いつつ ** 追加インフラゼロ ** でRAGを完成させた実践事例は意外と少ないと感じました。

この記事では、以下の構成でRAGシステムを構築・本番デプロイした際の実装詳細とハマりポイントを共有します。

- **FastAPI (Python 3.12)** — APIサーバー
- **Vertex AI text-embedding-004** — テキスト埋め込み生成
- **BigQuery ML.DISTANCE COSINE** — ベクトル検索（Pinecone/Weaviate不要）
- **Cloud Run** — サーバーレスデプロイ

ユースケースは社会的インパクト測定（SDI診断・ロジックモデル生成）ですが、構成とハマりポイントはドメインを問わず再利用できます。

---

## システム構成

```
クライアント (Next.js)
    ↓ API プロキシ
Cloud Run (FastAPI)
    ├── Vertex AI text-embedding-004  ← 埋め込み生成
    └── BigQuery
        ├── knowledge_chunks           ← ベクトルDB代わり
        └── ML.DISTANCE COSINE        ← 類似度検索
```

Pinecone、Weaviate、Qdrantなどの専用ベクトルDBを使わず、すでに使っているBigQueryだけでベクトル検索を実現できるのが最大のメリットです。小〜中規模のRAGシステム（数万チャンク程度）であれば、これで十分実用に耐えます。

---

## BigQueryテーブル設計

まずベクトルを格納するテーブルを作成します。

```sql
-- knowledge_chunks テーブル
CREATE TABLE IF NOT EXISTS `your-project.your_dataset.knowledge_chunks` (
    chunk_id STRING NOT NULL,
    source_name STRING,
    content STRING,
    embedding ARRAY<FLOAT64>,  -- 768次元（text-embedding-004）
    metadata JSON,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP()
);
```

Vertex AI の `text-embedding-004` は768次元のベクトルを返します。`ARRAY<FLOAT64>` で格納し、`ML.DISTANCE` 関数でコサイン類似度検索します。

---

## コアライブラリ実装

### knowledge_retrieval.py

RAGの中核となるファイルです。埋め込み生成と類似度検索の2つが主な責務です。

```python
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from typing import Optional

from google.cloud import bigquery
from vertexai.language_models import TextEmbeddingModel

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = "text-embedding-004"
BQ_TABLE = "your-project.your_dataset.knowledge_chunks"


def _generate_embedding_sync(text: str) -> list[float]:
    """同期版の埋め込み生成（ThreadPoolExecutor内で呼ぶ）"""
    model = TextEmbeddingModel.from_pretrained(EMBEDDING_MODEL)
    embeddings = model.get_embeddings([text])
    return embeddings[0].values


async def generate_embedding(text: str, timeout: int = 10) -> list[float]:
    """
    Vertex AI Embedding生成（タイムアウト付き）

    ポイント: Vertex AIはネットワーク遅延でブロックする可能性があるため、
    ThreadPoolExecutorでラップしてタイムアウトを設定する。
    タイムアウトなしの場合、Cloud Runのワーカーが枯渇するリスクがある。
    """
    loop = asyncio.get_event_loop()
    with ThreadPoolExecutor(max_workers=1) as executor:
        try:
            future = loop.run_in_executor(executor, _generate_embedding_sync, text)
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            raise RuntimeError(f"Vertex AI embedding timeout ({timeout}s)")


async def retrieve_knowledge(
    query: str,
    top_k: int = 5,
    domain_filter: Optional[str] = None,
) -> list[dict]:
    """
    クエリに近い知識チャンクをBQから取得する

    ML.DISTANCE でコサイン類似度を計算し、距離が小さい順にtop_k件返す。
    距離=0が完全一致、距離=2が完全逆方向。
    """
    query_embedding = await generate_embedding(query)

    bq_client = bigquery.Client()

    # ML.DISTANCE による類似度検索
    sql = """
    SELECT
        chunk_id,
        source_name,
        content,
        metadata,
        ML.DISTANCE(
            embedding,
            @query_embedding,
            'COSINE'
        ) AS distance
    FROM `{table}`
    WHERE ARRAY_LENGTH(embedding) > 0
    ORDER BY distance ASC
    LIMIT @top_k
    """.format(table=BQ_TABLE)

    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ArrayQueryParameter(
                "query_embedding", "FLOAT64", query_embedding
            ),
            bigquery.ScalarQueryParameter("top_k", "INT64", top_k),
        ]
    )

    results = bq_client.query(sql, job_config=job_config).result()
    return [dict(row) for row in results]
```

---

## ルーター実装

### routers/knowledge.py — チャンク管理API

チャンクの追加・一覧取得のエンドポイントです。

```python
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from lib.knowledge_retrieval import generate_embedding, retrieve_knowledge

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


def verify_admin_token(x_admin_token: str = Header(...)):
    """
    X-Admin-Token 認証

    Cloud Run の Authorization ヘッダーは IAM 認証で使われるため、
    管理系APIの認証には別ヘッダーを使う（競合回避）。
    """
    if x_admin_token != settings.ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid admin token")


class AddChunkRequest(BaseModel):
    chunk_id: str
    source_name: str
    content: str
    metadata: dict = {}


class ExpertReviewRequest(BaseModel):
    target_id: str
    # Literal型で入力時点から不正値をブロック
    target_type: Literal["logic_model", "sdi_assessment"]
    status: Literal["pending", "approved", "rejected", "revised"]
    comment: str


@router.post("/chunks")
async def add_chunk(
    body: AddChunkRequest,
    _=Depends(verify_admin_token),
):
    """
    知識チャンクを追加する

    注意: generate_embedding にもタイムアウトを設定すること。
    retrieve_knowledge だけ対策してこちらを忘れると、
    チャンク追加時に Cloud Run ワーカーが枯渇する（DAレビューで発見）。
    """
    try:
        embedding = await generate_embedding(body.content, timeout=10)
    except RuntimeError as e:
        raise HTTPException(status_code=504, detail=str(e))

    bq_client = bigquery.Client()
    rows = [{
        "chunk_id": body.chunk_id,
        "source_name": body.source_name,
        "content": body.content,
        "embedding": embedding,
        "metadata": json.dumps(body.metadata),
    }]
    errors = bq_client.insert_rows_json(BQ_TABLE, rows)
    if errors:
        raise HTTPException(status_code=500, detail=str(errors))

    return {"status": "ok", "chunk_id": body.chunk_id}


@router.get("/chunks")
async def list_chunks(_=Depends(verify_admin_token)):
    """チャンク一覧取得（BQエラーハンドリング付き）"""
    bq_client = bigquery.Client()
    try:
        results = bq_client.query(
            f"SELECT chunk_id, source_name, created_at FROM `{BQ_TABLE}` ORDER BY created_at DESC"
        ).result()
        return [dict(row) for row in results]
    except Exception as e:
        logger.error(f"BQ list_chunks error: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch chunks")
```

---

## シードスクリプトの冪等性設計

知識ベースの初期データ投入スクリプトです。 ** 必ず冪等性ガードを先に実装してください。 **

```python
#!/usr/bin/env python3
"""seed_knowledge.py — 知識チャンク初期投入スクリプト"""

from google.cloud import bigquery

KNOWLEDGE_DATA = [
    {
        "chunk_id": "kellogg-logic-model-01",
        "source_name": "Kellogg Logic Model Guide",
        "content": "ロジックモデルは投入資源（Input）、活動（Activity）、産出（Output）、成果（Outcome）、インパクト（Impact）の因果連鎖を可視化するフレームワークである。",
    },
    # ... 他のチャンク
]


def seed(dry_run: bool = False):
    bq_client = bigquery.Client()

    # 冪等性ガード: 既存チャンクのIDを先に取得
    existing_ids = set()
    try:
        result = bq_client.query(
            f"SELECT chunk_id FROM `{BQ_TABLE}`"
        ).result()
        existing_ids = {row["chunk_id"] for row in result}
    except Exception:
        pass  # テーブルが空の場合は無視

    rows_to_insert = []
    for item in KNOWLEDGE_DATA:
        if item["chunk_id"] in existing_ids:
            print(f"  SKIP (already exists): {item['chunk_id']}")
            continue
        rows_to_insert.append(item)

    if dry_run:
        print(f"Dry run: {len(rows_to_insert)} chunks to insert")
        return

    # 埋め込み生成 & INSERT
    for item in rows_to_insert:
        embedding = generate_embedding_sync(item["content"])
        errors = bq_client.insert_rows_json(BQ_TABLE, [{**item, "embedding": embedding}])
        if errors:
            print(f"  ERROR: {item['chunk_id']}: {errors}")
        else:
            print(f"  OK: {item['chunk_id']}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    seed(dry_run=args.dry_run)
```

冪等性ガードを後から追加したため、今回は26行の重複が発生しました。BQのstreaming insertは **INSERT後90分はDELETE不可 ** という制約があるため、重複の事後削除もすぐにはできません。 ** シードスクリプトは最初から冪等性を設計する ** ことが鉄則だ。

---

## ハマりポイント集

実際の構築で詰まった点をまとめます。

### 1. IAM権限: roles/aiplatform.user が必要

Cloud Runのサービスアカウントにこの権限がないと、Vertex AI Embedding呼び出し時に `403 PERMISSION_DENIED` が返ります。

```bash
# Cloud Runが使うサービスアカウントに権限付与
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:your-sa@your-project.iam.gserviceaccount.com" \
  --role="roles/aiplatform.user"

# Vertex AI API の有効化も忘れずに
gcloud services enable aiplatform.googleapis.com
```

### 2. X-Admin-Token vs Authorization ヘッダーの競合

Cloud Runの認証（`--no-allow-unauthenticated`設定時）では、`Authorization: Bearer <OIDC_TOKEN>` ヘッダーを使います。管理系APIで `Authorization: Bearer <ADMIN_TOKEN>` を使うと **Cloud RunのIAM認証と競合 ** して予期しない動作になります。

解決策は専用ヘッダーを使うことです。

```python
# NG: Authorization ヘッダーは Cloud Run が使う
def verify_admin_token(authorization: str = Header(...)):
    token = authorization.replace("Bearer ", "")
    ...

# OK: X-Admin-Token ヘッダーを使う
def verify_admin_token(x_admin_token: str = Header(...)):
    if x_admin_token != settings.ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid admin token")
```

### 3. Vertex AI EmbeddingによるCloud Runワーカー枯渇

Vertex AIのEmbedding APIはネットワーク遅延で60秒以上ブロックすることがあります。非同期FastAPIでも、`run_in_executor` なしで同期関数を呼ぶとイベントループがブロックします。

さらに重要なのは、`retrieve_knowledge`（検索時）だけ対策して `add_chunk`（追加時）を忘れるパターンです。どちらも同じ `generate_embedding()` を呼ぶため、 ** 両方にタイムアウトが必要 ** です。

```python
# 全ての generate_embedding 呼び出しに ThreadPoolExecutor + タイムアウト
async def generate_embedding(text: str, timeout: int = 10) -> list[float]:
    loop = asyncio.get_event_loop()
    with ThreadPoolExecutor(max_workers=1) as executor:
        try:
            future = loop.run_in_executor(executor, _generate_embedding_sync, text)
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            raise RuntimeError(f"Vertex AI embedding timeout ({timeout}s)")
```

### 4. BQ Streaming Bufferの90分DELETE制約

BigQueryのStreaming Insertは便利ですが、 **INSERT後約90分はDELETE不可 ** という制約があります。

```sql
-- これがストリーミングバッファ期間中は失敗する
DELETE FROM `your_dataset.knowledge_chunks`
WHERE chunk_id = 'some-chunk-id';
-- Error: UPDATE or DELETE statement over table ... would affect rows in the streaming buffer
```

対策はシードスクリプトで重複チェックを先に行うこと（前述の冪等性ガード）です。どうしてもDELETEが必要な場合は90分待つか、テーブルを再作成するしかありません。

### 5. Pydantic Literal型でBQデータ品質を守る

`str` 型のフィールドはあらゆる文字列を受け付けるため、不正な値がBQに入ると下流クエリが壊れます。固定値のフィールドには `Literal` 型を使います。

```python
from typing import Literal
from pydantic import BaseModel

# NG: 任意の文字列を受け付ける
class ExpertReviewRequest(BaseModel):
    target_type: str
    status: str

# OK: 入力時点で不正値をブロック
class ExpertReviewRequest(BaseModel):
    target_type: Literal["logic_model", "sdi_assessment"]
    status: Literal["pending", "approved", "rejected", "revised"]
```

FastAPIのOpenAPIドキュメントにも列挙値が反映されるので、APIドキュメントとしての品質も上がります。

---

## Cloud Run デプロイ

```dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# google-cloud-bigquery と vertexai は別途インストール
# requirements.txt に含める
# google-cloud-bigquery==3.x.x
# google-cloud-aiplatform==1.x.x

COPY . .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
```

```bash
# ビルド & デプロイ
gcloud builds submit --tag gcr.io/YOUR_PROJECT/correlate-api

gcloud run deploy correlate-api \
  --image gcr.io/YOUR_PROJECT/correlate-api \
  --region asia-northeast1 \
  --service-account your-sa@your-project.iam.gserviceaccount.com \
  --no-allow-unauthenticated \
  --memory 1Gi \
  --cpu 1 \
  --concurrency 80 \
  --timeout 60
```

`--no-allow-unauthenticated` を設定する場合、クライアント側はOIDCトークンを取得してリクエストする必要があります。Next.jsからCloud Runを呼ぶ場合はAPIプロキシ経由でサービスアカウントキーを使う方法が簡単です。

---

## RAGの動作確認

シードデータを投入したら、実際に検索してみます。

```bash
# チャンク投入（冪等性ガード付き）
python3 scripts/seed_knowledge.py --direct

# 確認
curl -X GET "https://your-cloud-run-url/knowledge/chunks" \
  -H "X-Admin-Token: your-admin-token"
```

```bash
# RAG検索テスト（SDI診断の例）
curl -X POST "https://your-cloud-run-url/sdi/assess" \
  -H "Content-Type: application/json" \
  -d '{
    "q1": "高齢者の社会的孤立を防ぐコミュニティ活動を支援しています",
    "q2": "活動参加者数と生活満足度を指標にしています",
    "q3": "行政・NPO・住民の協働体制を構築しています",
    "q4": "外部評価機関によるインパクト測定を年次で実施",
    "q5": "行政担当者・当事者・家族・地域住民"
  }'
```

レスポンスに `knowledge_context` フィールドが含まれていれば、RAGが正常に動作しています。

---

## まとめ

| ポイント | 内容 |
|---------|------|
| ベクトルDB不要 | BQ ML.DISTANCE COSINE だけで小〜中規模RAGは十分 |
| IAM権限 | roles/aiplatform.user を必ず付与 |
| 認証ヘッダー | 管理APIは X-Admin-Token で（Authorization と競合回避） |
| タイムアウト | generate_embedding を呼ぶ全箇所に ThreadPoolExecutor(10s) |
| 冪等性 | シードスクリプトは最初から check-before-insert パターンで |
| BQ制約 | Streaming insert後90分はDELETE不可を前提に設計 |
| バリデーション | 固定値フィールドは Pydantic Literal型 |

Pineconeなどの専用ベクトルDBは大規模（数百万チャンク以上）になると必要ですが、RAGシステムを始めるならBQだけで構築して、スケールの問題が出てから移行するアプローチが現実的だと感じました。

追加のチャンク投入やRAG品質改善については、別途記事にする予定です。

---

## 参考

- [Vertex AI Text Embedding API](https://cloud.google.com/vertex-ai/docs/generative-ai/embeddings/get-text-embeddings)
- [BigQuery ML.DISTANCE 関数](https://cloud.google.com/bigquery/docs/reference/standard-sql/distance-functions)
- [Cloud Run サービスアカウント設定](https://cloud.google.com/run/docs/securing/service-identity)
- [FastAPI + asyncio Best Practices](https://fastapi.tiangolo.com/async/)
