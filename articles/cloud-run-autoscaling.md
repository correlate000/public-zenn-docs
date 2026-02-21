---
title: "Cloud Run オートスケーリング徹底解説 ─ コールドスタート削減とコスト最適化の実践設定"
emoji: "🚀"
type: "tech"
topics: ["cloudrun", "gcp", "インフラ", "docker", "サーバーレス"]
published: false
publication_name: "correlate_dev"
---

## はじめに

Cloud Run はコンテナをサーバーレスで実行できる GCP のマネージドサービスです。インフラ管理が不要で、リクエスト量に応じて自動でスケールアウト・スケールインする点が大きな特徴です。

しかし「スケールが0になるとコールドスタートが遅い」「どこをどう設定すればコストが下がるのか分からない」という声は多く聞かれます。本記事では Cloud Run のスケーリングの仕組みを根本から解説し、コールドスタートを削減しながらコストを最適化するための具体的な設定方法を紹介します。

対象読者は Cloud Run をすでに本番環境で使用しているバックエンドエンジニアや SRE エンジニアを想定しています。

---

## 1. スケーリングの仕組み（リクエストベース vs CPUベース）

Cloud Run のスケーリングには大きく2つのモードがあります。

### リクエストベーススケーリング（デフォルト）

デフォルトのモードです。受信リクエスト数に基づいて、GCP がインスタンス数を自動調整します。

```mermaid
flowchart LR
    subgraph "スケーリング判断ロジック"
        A[リクエスト受信] --> B{既存インスタンスの\n空き concurrency あり？}
        B -- Yes --> C[既存インスタンスにルーティング]
        B -- No --> D[新インスタンス起動]
        D --> E[コールドスタート発生]
        E --> C
    end
    subgraph "スケールダウン"
        F[アイドル状態継続] --> G{min-instances に\n到達したか？}
        G -- No --> H[インスタンスを削除]
        G -- Yes --> I[インスタンスを維持]
    end
```

- `concurrency`（並行リクエスト数）の設定値を超えると新インスタンスが起動します
- 全インスタンスがアイドル状態になると `min-instances` の値まで縮小されます
- `min-instances: 0`（デフォルト）の場合、アイドル時はインスタンスが0になります

### CPUベーススケーリング（CPU常時割り当て時）

`--no-cpu-throttling` フラグを設定すると CPU 常時割り当てモードになり、CPU 使用率に基づいてスケールアウトが行われます。

| 項目 | リクエストベース（デフォルト） | CPUベース（--no-cpu-throttling） |
|------|------|------|
| スケールトリガー | リクエスト数 / concurrency | CPU使用率（デフォルト閾値: 60%） |
| アイドル時のCPU | スロットリングされる | 常時割り当て |
| バックグラウンド処理 | 不可 | 可能 |
| コールドスタート | あり（min=0時） | min>0で完全排除可 |
| 用途 | HTTPリクエスト処理 | Pub/Sub消費、WebSocket |

---

## 2. コールドスタートとは

コールドスタートとは、インスタンス数が0の状態から新規インスタンスが起動し、リクエストを処理できるようになるまでの時間のことです。

```mermaid
sequenceDiagram
    participant C as クライアント
    participant LB as Cloud Run LB
    participant CR as Cloud Run
    participant AR as Artifact Registry

    C->>LB: リクエスト送信
    LB->>CR: リクエスト受付（インスタンス0）
    CR->>AR: コンテナイメージPull
    AR-->>CR: イメージ転送
    CR->>CR: コンテナ起動
    CR->>CR: アプリケーション初期化
    CR->>CR: startup probe 通過
    CR-->>LB: 準備完了
    LB-->>C: レスポンス（コールドスタート分の遅延含む）
```

### コールドスタートが発生する条件

1. インスタンス数が 0 の状態でリクエストが来た場合
2. 既存インスタンスの `concurrency` が上限に達し、新インスタンスが必要な場合

### 言語・フレームワーク別のコールドスタート目安

| 言語 / フレームワーク | 目安のコールドスタート時間 |
|---|---|
| Go（軽量） | 約 0.5〜1 秒 |
| Node.js（Express） | 約 1〜2 秒 |
| Python（FastAPI） | 約 2〜4 秒 |
| Python（Django） | 約 4〜8 秒 |
| Java（Spring Boot） | 約 8〜15 秒 |

これらはイメージサイズや初期化処理量によって大きく変動します。

---

## 3. min-instances 設定（コールドスタート削減とコストのトレードオフ）

`min-instances` は常時起動しておくインスタンスの最小数を設定するパラメータです。

### gcloud コマンド例

```bash
# min-instances を 1 に設定してデプロイ
gcloud run deploy my-service \
  --image asia-northeast1-docker.pkg.dev/YOUR_PROJECT_ID/my-repo/my-app:latest \
  --region asia-northeast1 \
  --min-instances 1 \
  --max-instances 10 \
  --memory 512Mi \
  --cpu 1
```

### service.yaml 設定例

```yaml
apiVersion: serving.knative.dev/v1
kind: Service
metadata:
  name: my-service
  annotations:
    run.googleapis.com/ingress: all
spec:
  template:
    metadata:
      annotations:
        autoscaling.knative.dev/minScale: "1"
        autoscaling.knative.dev/maxScale: "10"
    spec:
      containerConcurrency: 80
      containers:
        - image: asia-northeast1-docker.pkg.dev/YOUR_PROJECT_ID/my-repo/my-app:latest
          resources:
            limits:
              cpu: "1"
              memory: 512Mi
```

```bash
# service.yaml を適用
gcloud run services replace service.yaml --region asia-northeast1
```

### min-instances のコスト（アイドル課金）

`min-instances > 0` に設定した場合、アイドル状態でも課金が発生します。ただしアイドル時の課金レートは通常のリクエスト処理時の **10%** です。

**コスト計算例（asia-northeast1、1vCPU、512MiB、min-instances=1）**

| 状態 | CPU単価 | Memory単価 |
|---|---|---|
| リクエスト処理時 | $0.00002400 / vCPU秒 | $0.00000250 / GiB秒 |
| アイドル時（min-instances課金） | $0.00000240 / vCPU秒 | $0.00000025 / GiB秒 |

24時間アイドルした場合:
- CPU: 0.00000240 × 86,400 = **$0.207/日**
- Memory (512MiB): 0.00000025 × 0.5 × 86,400 = **$0.011/日**
- 合計: 約 **$0.22/日 = $6.6/月**

月額わずか $6.6 でコールドスタートをほぼ排除できます。ユーザー体験への影響が大きいサービスでは投資対効果が高い設定です。

---

## 4. max-instances 設定（コスト上限の制御）

`max-instances` はスケールアウト時のインスタンス数上限を設定します。デフォルトは 100 です。

```bash
gcloud run deploy my-service \
  --image asia-northeast1-docker.pkg.dev/YOUR_PROJECT_ID/my-repo/my-app:latest \
  --region asia-northeast1 \
  --max-instances 20
```

### max-instances 設定時の注意点

- 上限に達するとそれ以上リクエストをさばけなくなります（HTTP 429 が返ります）
- 外部 API のレートリミットや DB のコネクション数上限に合わせて設定するのが効果的です
- バックエンド側のボトルネック（RDBのコネクション数など）を超えないよう、以下の式を参考に設定します

```
max-instances = DB最大コネクション数 ÷ (concurrency × コネクション利用率)
```

例: PostgreSQL のデフォルト最大コネクション数 100、concurrency=10、コネクション利用率 50% の場合
`max-instances = 100 ÷ (10 × 0.5) = 20`

---

## 5. concurrency 設定（並行リクエスト数の最適化）

`concurrency` は1つのインスタンスが同時に処理できるリクエスト数を設定します。デフォルトは 80 です。

```bash
gcloud run deploy my-service \
  --image asia-northeast1-docker.pkg.dev/YOUR_PROJECT_ID/my-repo/my-app:latest \
  --region asia-northeast1 \
  --concurrency 50
```

### concurrency の選び方

| アプリの特性 | 推奨 concurrency | 理由 |
|---|---|---|
| I/Oバウンド（API プロキシ、DB 読み取り等） | 100〜500 | 待機中は CPU を使わないため多数さばける |
| CPUバウンド（画像処理、暗号化等） | 1〜10 | CPU を占有するため並行数を下げてリソースを集中 |
| 混合（一般的なWebアプリ） | 20〜80 | バランスを見て調整 |

concurrency を高くすることで同じリクエスト量をより少ないインスタンスでさばけるため、コスト削減につながります。ただし、各リクエストが使用するメモリ量にも注意が必要です。

### concurrency と必要インスタンス数の関係

```
必要インスタンス数 = ceil(同時リクエスト数 ÷ concurrency)
```

例: 同時リクエスト数 200、concurrency=80 の場合 → `ceil(200 ÷ 80) = 3` インスタンス

---

## 6. startup probe / liveness probe（ヘルスチェック設定）

ヘルスチェックを適切に設定することで、コールドスタート時の失敗判定を防ぎ、安定したスケールアウトを実現できます。

### startup probe

コンテナ起動後、アプリケーションが初期化を完了するまで待機するためのヘルスチェックです。startup probe が通過するまで liveness probe はチェックされません。

```yaml
apiVersion: serving.knative.dev/v1
kind: Service
metadata:
  name: my-service
spec:
  template:
    spec:
      containers:
        - image: asia-northeast1-docker.pkg.dev/YOUR_PROJECT_ID/my-repo/my-app:latest
          startupProbe:
            httpGet:
              path: /health
              port: 8080
            initialDelaySeconds: 0   # 最初のチェックまでの待機秒数
            periodSeconds: 10        # チェック間隔（秒）
            failureThreshold: 3      # 失敗許容回数
            timeoutSeconds: 5        # 1回のタイムアウト（秒）
          livenessProbe:
            httpGet:
              path: /health
              port: 8080
            periodSeconds: 15
            failureThreshold: 3
            timeoutSeconds: 5
```

### ヘルスチェックエンドポイントの実装例

**Python（FastAPI）の場合**

```python
from fastapi import FastAPI
import asyncpg

app = FastAPI()

# DB接続プール（起動時に初期化）
pool = None

@app.on_event("startup")
async def startup_event():
    global pool
    pool = await asyncpg.create_pool(
        dsn="postgresql://user:pass@host/db",
        min_size=2,
        max_size=10
    )

@app.get("/health")
async def health_check():
    # DB疎通確認
    async with pool.acquire() as conn:
        await conn.fetchval("SELECT 1")
    return {"status": "ok"}
```

**Node.js（Express）の場合**

```javascript
const express = require("express");
const app = express();

let isReady = false;

// 初期化処理
async function initialize() {
  // DBコネクション確立など
  await setupDatabase();
  isReady = true;
}

app.get("/health", (req, res) => {
  if (!isReady) {
    return res.status(503).json({ status: "initializing" });
  }
  res.json({ status: "ok" });
});

initialize().then(() => {
  app.listen(8080);
});
```

---

## 7. CPU allocation（常時割り当てでコールドスタートを完全排除）

CPU 常時割り当てを `min-instances > 0` と組み合わせることで、コールドスタートを完全に排除できます。

```bash
# CPU常時割り当て + min-instances で常時ウォーム状態を維持
gcloud run deploy my-service \
  --image asia-northeast1-docker.pkg.dev/YOUR_PROJECT_ID/my-repo/my-app:latest \
  --region asia-northeast1 \
  --no-cpu-throttling \
  --min-instances 1 \
  --max-instances 10 \
  --memory 512Mi \
  --cpu 1
```

### CPU 割り当てモードの比較

| 設定 | 費用 | コールドスタート | バックグラウンド処理 |
|---|---|---|---|
| デフォルト（--cpu-throttling、min=0） | 最安 | あり | 不可 |
| min-instances=1（--cpu-throttling） | + アイドル課金10% | ほぼなし | 不可 |
| --no-cpu-throttling + min=1 | + アイドル課金100% | 完全排除 | 可能 |

### CPU 常時割り当て時のコスト

CPU 常時割り当て時のアイドル課金レートはリクエストベースのアイドル時より高い設定になります。

**CPU 常時割り当て、アイドル時の料金（asia-northeast1）**

| リソース | 単価 |
|---|---|
| CPU（常時割り当て） | $0.00001800 / vCPU秒 |
| Memory | $0.00000250 / GiB秒 |

24時間アイドル（1vCPU、512MiB）の場合:
- CPU: 0.00001800 × 86,400 = **$1.555/日**
- Memory: 0.00000250 × 0.5 × 86,400 = **$0.108/日**
- 合計: 約 **$1.66/日 = $49.8/月**

Pub/Sub 消費や WebSocket 維持が必要なサービスには適していますが、通常の HTTP API には割高な設定です。

---

## 8. Dockerfile の最適化（コールドスタート短縮）

コールドスタート時間を短縮するには、Dockerfile の最適化も重要です。

### イメージサイズ最小化

```dockerfile
# マルチステージビルドでビルド成果物のみを最終イメージに含める
FROM python:3.12-slim AS builder

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

FROM python:3.12-slim

WORKDIR /app
# ビルドステージのpipパッケージをコピー
COPY --from=builder /root/.local /root/.local
COPY . .

ENV PATH=/root/.local/bin:$PATH
ENV PORT=8080

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
```

### アプリケーション起動コストの削減

**Python の場合のポイント**

```python
# main.py
# 1. import は必要最小限に絞る（遅延インポートを活用）
# 2. 起動時の重い処理を非同期化する

from contextlib import asynccontextmanager
from fastapi import FastAPI

# グローバル変数はモジュールロード時に初期化されるため注意
# 重いリソースはlifespan内で初期化する

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 起動時のみ実行（DBプール初期化など）
    app.state.db_pool = await create_db_pool()
    yield
    # シャットダウン時のクリーンアップ
    await app.state.db_pool.close()

app = FastAPI(lifespan=lifespan)
```

**Node.js の場合のポイント**

```javascript
// server.js
// 1. require() は起動時にすべて実行されるため、重いモジュールは遅延ロードを検討
// 2. データベース接続は起動時に確立しておく（コネクションプール）

const { Pool } = require("pg");

// 環境変数からDB設定を取得
const pool = new Pool({
  connectionString: process.env.DATABASE_URL,
  max: 10,             // 最大コネクション数
  idleTimeoutMillis: 30000,
  connectionTimeoutMillis: 2000,
});

// プリウォーム: 起動時にコネクションを確立しておく
pool.connect().then(client => {
  client.release();
  console.log("Database connection established");
});
```

---

## 9. コスト計算の実例（設定パターン別の費用比較）

実際のユースケースを想定したコスト比較を示します。条件は以下のとおりです。

**前提条件**
- リージョン: asia-northeast1
- リクエスト数: 月間 100万リクエスト
- 平均処理時間: 200ms
- インスタンスサイズ: 1vCPU、512MiB
- 1日のうちリクエストがある時間: 12時間（残り12時間はほぼアイドル）

### パターン A: デフォルト設定（min=0、concurrency=80）

```bash
gcloud run deploy my-service \
  --image REGION-docker.pkg.dev/YOUR_PROJECT_ID/my-repo/my-app:latest \
  --region asia-northeast1 \
  --min-instances 0 \
  --max-instances 100 \
  --concurrency 80 \
  --cpu 1 \
  --memory 512Mi
```

| 項目 | 計算 | 月額 |
|---|---|---|
| CPU（リクエスト処理） | 0.00002400 × (100万 × 0.2秒) | $4.80 |
| Memory（リクエスト処理） | 0.00000250 × 0.5 × (100万 × 0.2秒) | $0.25 |
| リクエスト課金 | $0.40 × (100万 ÷ 100万) | $0.40 |
| **合計** | | **約 $5.45/月** |
| コールドスタート | あり（トラフィックが来ていない時間帯） | - |

### パターン B: min-instances=1（コールドスタート削減）

```bash
gcloud run deploy my-service \
  --image REGION-docker.pkg.dev/YOUR_PROJECT_ID/my-repo/my-app:latest \
  --region asia-northeast1 \
  --min-instances 1 \
  --max-instances 10 \
  --concurrency 80 \
  --cpu 1 \
  --memory 512Mi
```

| 項目 | 計算 | 月額 |
|---|---|---|
| CPU（リクエスト処理） | $4.80 | $4.80 |
| Memory（リクエスト処理） | $0.25 | $0.25 |
| CPU（アイドル、10%課金） | 0.00000240 × (12h × 3600s × 30日) | $3.11 |
| Memory（アイドル、10%課金） | 0.00000025 × 0.5 × (12h × 3600s × 30日) | $0.16 |
| リクエスト課金 | $0.40 | $0.40 |
| **合計** | | **約 $8.72/月** |
| コールドスタート | ほぼなし | - |

### パターン C: CPU常時割り当て + min=1（コールドスタート完全排除）

```bash
gcloud run deploy my-service \
  --image REGION-docker.pkg.dev/YOUR_PROJECT_ID/my-repo/my-app:latest \
  --region asia-northeast1 \
  --no-cpu-throttling \
  --min-instances 1 \
  --max-instances 10 \
  --concurrency 80 \
  --cpu 1 \
  --memory 512Mi
```

| 項目 | 計算 | 月額 |
|---|---|---|
| CPU（常時割り当て、アクティブ） | 0.00001800 × (100万 × 0.2秒) | $3.60 |
| CPU（アイドル） | 0.00001800 × (12h × 3600s × 30日) | $23.33 |
| Memory | 0.00000250 × 0.5 × (全時間) | 約 $1.62 |
| リクエスト課金 | $0.40 | $0.40 |
| **合計** | | **約 $29/月** |
| コールドスタート | なし | - |

### パターン比較まとめ

| パターン | 月額費用 | コールドスタート | 適したユースケース |
|---|---|---|---|
| A（デフォルト） | $5.45 | あり | 開発/ステージング環境 |
| B（min=1） | $8.72 | ほぼなし | 一般的な本番APIサーバー |
| C（CPU常時割り当て） | $29 | なし | Pub/Sub消費、WebSocket |

---

## 10. まとめ（ユースケース別推奨設定一覧）

### ユースケース別の推奨設定

| ユースケース | min-instances | max-instances | concurrency | CPU割り当て | 理由 |
|---|---|---|---|---|---|
| 開発・ステージング環境 | 0 | 5 | 80 | throttling | コスト最小化優先 |
| 一般的な本番 API | 1〜2 | 20〜50 | 50〜80 | throttling | コールドスタート削減とコストのバランス |
| レスポンスタイム重視（SLA あり） | 2〜5 | 50〜100 | 80 | throttling | 複数インスタンス常時起動で応答性確保 |
| Pub/Sub コンシューマー | 1 | 10 | 1〜10 | no-throttling | バックグラウンド処理に対応 |
| バッチ処理 API | 0 | 5 | 1 | throttling | 並行実行数が少なくてよい |
| 高トラフィック API | 3〜5 | 100 | 80〜200 | throttling | スパイク耐性と常時応答性を確保 |

### 設定の全体像（gcloud コマンド完全版）

```bash
# スケーリング設定
# --min-instances: コールドスタート削減
# --max-instances: コスト上限制御
# --concurrency: 1インスタンスの並行リクエスト数
# --cpu-throttling: デフォルト（リクエスト時のみCPU割り当て）
# --timeout: リクエストタイムアウト（秒）
# --allow-unauthenticated: パブリックアクセスを許可する場合のみ設定

gcloud run deploy my-service \
  --image asia-northeast1-docker.pkg.dev/YOUR_PROJECT_ID/my-repo/my-app:latest \
  --region asia-northeast1 \
  --platform managed \
  --min-instances 1 \
  --max-instances 20 \
  --concurrency 80 \
  --cpu 1 \
  --memory 512Mi \
  --cpu-throttling \
  --timeout 300 \
  --ingress all \
  --allow-unauthenticated
```

### チューニングの考え方

1. **まずデフォルト設定で動作確認**する（min=0、concurrency=80）
2. **Cloud Monitoring でコールドスタート頻度を計測**する（`container/billable_instance_time` メトリクス）
3. **コールドスタートが問題になる場合** → `min-instances=1` を設定し、月額コスト増分と UX 改善を比較して判断する
4. **max-instances は DB コネクション数の上限に合わせる**
5. **concurrency はアプリの特性（I/OバウンドかCPUバウンドか）を見て調整**する

Cloud Run のスケーリング設定は「コスト最小化」と「レスポンスタイム最小化」のトレードオフです。本記事の計算例を参考に、自身のサービスの要件に合った設定を見つけてください。
