---
title: "Cloud Run コールドスタート対策完全ガイド ─ 原因分析からDockerfile最適化・CPU Boostまで"
emoji: "🥶"
type: "tech"
topics: ["cloudrun", "gcp", "docker", "パフォーマンス", "python"]
published: false
publication_name: "correlate_dev"
---

## はじめに

Cloud Run でAPIを公開していると、しばらく使われていなかった後の最初のリクエストだけ極端に遅くなる現象を経験することがあります。これがコールドスタートです。

たとえば、夜間に誰もアクセスしていないサービスに朝一番でリクエストが来ると、インスタンスの起動からアプリケーションの初期化まで一通りの処理が走るため、通常のレスポンスタイムが100ms程度でも、コールドスタート時には3〜5秒かかることがあります。ユーザーが体感する「遅さ」として直結するため、本番APIでは看過できない問題です。

本記事は、Cloud Runのオートスケーリング全体を扱う姉妹記事「[Cloud Run オートスケーリング徹底解説](/articles/cloud-run-autoscaling)」の深掘り編として、コールドスタートの発生メカニズムと削減テクニックに特化して解説します。

対象読者は Cloud Run を本番環境で運用しているバックエンドエンジニアや SRE エンジニアを想定しています。

---

## 1. コールドスタートのメカニズム

コールドスタートはひとつの処理ではなく、複数のフェーズが順番に実行される一連のプロセスです。

```mermaid
gantt
    title コールドスタートのタイムライン
    dateFormat  X
    axisFormat %s秒
    section GCPインフラ
    スケジューリング（インスタンス割り当て）  : 0, 200
    section コンテナランタイム
    イメージPull（Artifact Registry → インスタンス）    : 200, 700
    コンテナ起動（ランタイム初期化）         : 700, 900
    section アプリケーション
    ライブラリロード・インポート処理          : 900, 1800
    DB接続・初期化処理                      : 1800, 2800
    startup probe 通過                      : 2800, 3000
```

各フェーズの特徴は以下のとおりです。

| フェーズ | 処理内容 | 目安の時間 | 最適化余地 |
|---|---|---|---|
| スケジューリング | GCPインフラ側でのインスタンス割り当て | 50〜200ms | なし（GCP側） |
| イメージPull | Artifact Registryからのイメージ取得 | 200〜500ms | イメージサイズ削減で短縮可 |
| コンテナ起動 | ランタイム初期化、環境変数読み込み | 100〜300ms | ベースイメージ選択で影響 |
| アプリ初期化 | ライブラリロード、DB接続確立 | 500ms〜数秒 | 最も最適化インパクト大 |
| startup probe通過 | ヘルスチェック成功までの待機 | 設定値に依存 | probe設定で調整可 |

アプリ側でコントロールできるのはフェーズ2〜4です。特にアプリ初期化フェーズはアプリケーションコードの作り方次第で大きく変わります。

### コールドスタートが発生する条件

コールドスタートが発生するのは以下の2つのケースです。

1. インスタンス数が0の状態でリクエストが届いた場合（`min-instances: 0` のデフォルト設定時に発生）
2. 既存インスタンスの `concurrency` が上限に達し、新規インスタンスの起動が必要になった場合

---

## 2. 計測方法

対策を打つ前に、まず現状のコールドスタートの頻度と時間を計測します。

### Cloud Monitoring でのレイテンシ分布確認

Cloud Monitoring の Metrics Explorer で以下のメトリクスを確認します。

- メトリクス名: `run.googleapis.com/request_latencies`
- 集計: パーセンタイル（p50, p95, p99）を並べて表示

p50（中央値）と p99（上位1%の遅い側）の差が大きい場合は、コールドスタートが一部のリクエストに影響している可能性があります。たとえば p50 = 80ms、p99 = 4,500ms のような乖離があれば、コールドスタートが p99 を押し上げていると判断できます。

### gcloud ログでのコールドスタート確認

```bash
# コールドスタート関連ログをフィルタリング
gcloud logging read \
  'resource.type="cloud_run_revision" AND labels."run.googleapis.com/startupLatency":*' \
  --project=YOUR_PROJECT_ID \
  --format="table(timestamp, labels.run.googleapis.com/startupLatency)" \
  --limit=50
```

`startupLatency` ラベルはコールドスタート時のみ付与されます。このログが頻繁に出ている場合は対策が必要です。

### クライアントサイドでの簡易計測

本番環境での計測が難しい場合は、以下のようにシェルスクリプトで初回・2回目のレイテンシを比較します。

```bash
#!/bin/bash
SERVICE_URL="https://your-service-url.run.app/health"

echo "=== 1回目（コールドスタートあり）==="
time curl -s -o /dev/null -w "HTTP %{http_code} / %{time_total}s\n" "$SERVICE_URL"

sleep 1

echo "=== 2回目（ウォーム）==="
time curl -s -o /dev/null -w "HTTP %{http_code} / %{time_total}s\n" "$SERVICE_URL"
```

---

## 3. Dockerfileの最適化

コールドスタートのフェーズ2（イメージPull）を短縮する最も直接的な方法がイメージサイズの削減です。

### マルチステージビルドでイメージを最小化

ビルド時のみ必要なツール（コンパイラ、ビルドツール）を最終イメージから除外します。

```dockerfile
# ---- Python (FastAPI) のマルチステージビルド ----

# ビルドステージ: 依存パッケージをインストール
FROM python:3.12-slim AS builder

WORKDIR /app
COPY requirements.txt .

# --user フラグで /root/.local にインストール（最終イメージにコピーしやすくする）
RUN pip install --no-cache-dir --user -r requirements.txt

# ---- 実行ステージ: ビルド成果物のみコピー ----
FROM python:3.12-slim

WORKDIR /app

# ビルドステージのパッケージのみコピー（pipやsetuptoolsは不要）
COPY --from=builder /root/.local /root/.local

# アプリケーションコード
COPY . .

ENV PATH=/root/.local/bin:$PATH
ENV PORT=8080

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
```

### レイヤーキャッシュを活かす COPY の順序

Dockerfileのレイヤーは変更があった行以降がすべて再ビルドされます。変更頻度の低いファイルを先にコピーすることで、キャッシュが効く範囲を最大化できます。

```dockerfile
# 悪い例: ソースコードと依存関係を同時にコピー
# → コードを1行変えるたびに pip install が再実行される
COPY . .
RUN pip install -r requirements.txt

# 良い例: 依存関係ファイルを先にコピー
# → requirements.txt が変わらない限り pip install はキャッシュを使用
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
```

### .dockerignore で不要ファイルを除外

`.dockerignore` を設定することでイメージに含まれるファイルを絞り込めます。

```text
# .dockerignore
.git
.github
__pycache__
*.pyc
*.pyo
*.pyd
.Python
.env
.env.*
node_modules
.venv
venv
*.egg-info
dist
build
.DS_Store
*.log
tests/
docs/
*.md
```

ファイルサイズへの直接的な影響は小さいですが、ビルドコンテキストの転送時間を削減できます。

---

## 4. スタートアップ処理の最適化

フェーズ4（アプリ初期化）はアプリケーションコードの書き方で最も大きく変えられる部分です。

### 遅延初期化パターン（Python FastAPI）

起動時に重い処理を実行するのではなく、最初のリクエスト時まで初期化を遅らせます。

```python
# main.py
from contextlib import asynccontextmanager
from fastapi import FastAPI
import asyncpg

# アプリケーション状態を保持するグローバル変数
_db_pool = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    """起動・終了時のライフサイクル管理"""
    global _db_pool
    # DB接続プールはlifespan内で初期化（startup probeが通過した後に実行）
    _db_pool = await asyncpg.create_pool(
        dsn="postgresql://user:pass@host/db",
        min_size=1,   # 起動時の確立コネクション数を最小にする
        max_size=10,
        command_timeout=10,
    )
    yield
    # シャットダウン時にクリーンアップ
    if _db_pool:
        await _db_pool.close()

app = FastAPI(lifespan=lifespan)

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.get("/items/{item_id}")
async def get_item(item_id: int):
    async with _db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM items WHERE id = $1", item_id)
    return dict(row)
```

### インポート時間の測定と遅延インポート（Python）

Python は `import` 文を実行するたびにモジュールをロードするため、起動時のインポートが多いほど初期化時間が長くなります。

```bash
# インポート時間を計測する
python -X importtime -c "import main" 2> import_times.log
sort -t '|' -k2 -rn import_times.log | head -20
```

重いライブラリのインポートを関数内に移動させることで起動時間を短縮できます。

```python
# 悪い例: モジュールトップレベルで重いライブラリをインポート
import tensorflow as tf  # 起動時に数秒かかる

# 良い例: 実際に使う関数の中でインポート（遅延インポート）
def predict(data):
    import tensorflow as tf  # 最初の呼び出し時のみロード
    model = tf.keras.models.load_model("model.h5")
    return model.predict(data)
```

### Node.js での最適化

```javascript
// server.js
const express = require("express");

const app = express();

// DB接続プールは起動時に確立（最初のリクエストを待たずに準備）
const { Pool } = require("pg");
const pool = new Pool({
  connectionString: process.env.DATABASE_URL,
  max: 10,
  idleTimeoutMillis: 30_000,
  connectionTimeoutMillis: 3_000,
});

// プリウォーム: 起動直後にコネクションを1本確立しておく
pool.connect()
  .then((client) => {
    client.release();
    console.log("DB connection pool ready");
  })
  .catch((err) => {
    console.error("DB connection failed at startup:", err.message);
    // コネクション失敗でもサービスは起動を続ける（後続リクエストで再試行）
  });

app.get("/health", (req, res) => {
  res.json({ status: "ok" });
});

app.listen(process.env.PORT || 8080, () => {
  console.log("Server started");
});
```

---

## 5. min-instances 設定

コールドスタートを根本から回避する最もシンプルな設定です。インスタンスを常時1台以上起動しておくことで、リクエストが来た時点ですでにウォーム状態が維持されています。

```bash
gcloud run deploy my-service \
  --image asia-northeast1-docker.pkg.dev/YOUR_PROJECT_ID/my-repo/my-app:latest \
  --region asia-northeast1 \
  --min-instances 1 \
  --max-instances 20
```

アイドル時のコスト（asia-northeast1、1vCPU、512MiB）は月額 $6〜7 程度です。ユーザー体験への影響が大きいサービスでは費用対効果が高い選択肢です。

コストの詳細な比較は姉妹記事「Cloud Run オートスケーリング徹底解説」に掲載しています。

### 夜間トラフィックがない場合の代替案

min-instances を常時1に設定するのではなく、Cloud Scheduler から定期的にウォームアップリクエストを送る方法もあります。

```bash
# Cloud Scheduler でヘルスチェックエンドポイントを5分毎にping
gcloud scheduler jobs create http warm-up-my-service \
  --location asia-northeast1 \
  --schedule "*/5 * * * *" \
  --uri "https://my-service-xxx-an.a.run.app/health" \
  --http-method GET \
  --oidc-service-account-email my-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com
```

ただし Cloud Scheduler のリクエスト間隔（最小1分）の間にインスタンスがスケールダウンする可能性があるため、完全なコールドスタート排除にはなりません。

---

## 6. Startup CPU Boost

2022年にGAとなった機能です。コンテナの起動フェーズ中のみCPUを一時的に強化し、初期化処理を高速化します。

特徴：
- 起動フェーズ中のみ2vCPUが割り当てられる（通常設定が1vCPUの場合でも）
- 起動完了後は通常のCPU設定に戻る
- 追加費用なし

設定方法は gcloud コマンドまたは service.yaml の2通りです。

```bash
# gcloud コマンドでの設定
gcloud run deploy my-service \
  --image asia-northeast1-docker.pkg.dev/YOUR_PROJECT_ID/my-repo/my-app:latest \
  --region asia-northeast1 \
  --cpu-boost
```

```yaml
# service.yaml での設定
apiVersion: serving.knative.dev/v1
kind: Service
metadata:
  name: my-service
spec:
  template:
    metadata:
      annotations:
        run.googleapis.com/startup-cpu-boost: "true"
    spec:
      containers:
        - image: asia-northeast1-docker.pkg.dev/YOUR_PROJECT_ID/my-repo/my-app:latest
          resources:
            limits:
              cpu: "1"
              memory: 512Mi
```

Python や Java などインタプリタ・JVMを使う言語は起動時の CPU 消費が多く、効果が出やすい傾向にあります。Go のような静的コンパイル言語では効果が限定的です。

---

## 7. CPU allocation 常時割り当て

`--no-cpu-throttling` フラグを設定すると、リクエスト処理中以外もCPUが割り当てられ続けます。これにより起動直後からフルパワーで初期化処理が実行されるため、コールドスタート時間が短縮されます。

```bash
gcloud run deploy my-service \
  --image asia-northeast1-docker.pkg.dev/YOUR_PROJECT_ID/my-repo/my-app:latest \
  --region asia-northeast1 \
  --no-cpu-throttling \
  --min-instances 1
```

ただし min-instances を1以上に設定しないとコールドスタート自体は発生し続けます。また、アイドル時間も CPU 課金が発生するため、コストが大幅に増加します。バックグラウンドタスク（Pub/Sub消費、WebSocket維持）が必要なサービスを除き、通常の HTTP API には over-spec になる場合が多いです。

---

## 8. 言語別の最適化ポイント

### Python

Python のコールドスタートが遅い主な原因は起動時のインポート処理です。

```python
# 軽量なFastAPIを使ったミニマムな起動構成
# 不要な依存を削減し、起動時インポートを最小限に絞る

# requirements.txt の例（最小構成）
# fastapi==0.115.0
# uvicorn[standard]==0.32.0
# asyncpg==0.30.0          ← 非同期DB接続
# pydantic-settings==2.6.0 ← 設定管理

from fastapi import FastAPI
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    database_url: str
    debug: bool = False

    class Config:
        env_file = ".env"

# 設定はモジュールトップで初期化してよい（軽量）
settings = Settings()
app = FastAPI(debug=settings.debug)
```

uvicorn は `--workers 1` で起動します。Cloud Run は水平スケールするため、1インスタンスにワーカーを複数立てる必要はありません。

### Node.js

```javascript
// 起動時間最適化のポイント
// 1. ESM (import/export) より CJS (require) の方が起動が速いケースがある
// 2. node_modules のサイズが大きいと起動が遅くなる → production依存のみインストール

// package.json での本番ビルド
// "scripts": {
//   "build": "npm ci --only=production"
// }

// HTTP/2 を使って接続の再利用を有効化
const http2 = require("http2");
// または axios や fetch でHTTP/2クライアントを使用
```

### Go

Go はコンパイル済みバイナリのため、他の言語と比べてコールドスタートが大幅に短いです。それでも以下の点を意識すると効果があります。

```go
package main

import (
    "context"
    "database/sql"
    "log"
    "net/http"
    "os"
    "time"

    _ "github.com/lib/pq"
)

var db *sql.DB

func main() {
    // DB接続プールを起動時に確立
    var err error
    db, err = sql.Open("postgres", os.Getenv("DATABASE_URL"))
    if err != nil {
        log.Fatalf("failed to open db: %v", err)
    }

    // プール設定: コネクション数を適切に設定
    db.SetMaxOpenConns(10)
    db.SetMaxIdleConns(5)
    db.SetConnMaxLifetime(5 * time.Minute)

    // 起動確認のpingを実行
    ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
    defer cancel()
    if err = db.PingContext(ctx); err != nil {
        log.Fatalf("db ping failed: %v", err)
    }

    http.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
        w.Write([]byte(`{"status":"ok"}`))
    })

    port := os.Getenv("PORT")
    if port == "" {
        port = "8080"
    }
    log.Fatal(http.ListenAndServe(":"+port, nil))
}
```

---

## 9. まとめ（施策別の効果と導入コスト比較）

コールドスタート対策は「追加費用なしでできる最適化」と「インフラ設定の変更」に大別されます。

| 施策 | 効果 | 導入難易度 | 追加費用 | 備考 |
|---|---|---|---|---|
| Dockerfileのレイヤー最適化 | 中（起動20〜40%短縮） | 低 | なし | まず最初に試すべき |
| マルチステージビルド | 高（サイズ50%削減） | 中 | なし | 新規サービスに特に有効 |
| .dockerignore の整備 | 小 | 低 | なし | ビルド時間短縮にも効く |
| 遅延インポート・遅延初期化 | 高（起動30〜60%短縮） | 中〜高 | なし | コード改修が必要 |
| Startup CPU Boost | 中（起動20〜30%短縮） | 低（フラグ1つ） | なし | Python/Java に特に有効 |
| min-instances=1 | 高（コールドスタートほぼ排除） | 低 | $6〜7/月 | 最も費用対効果が高い |
| CPU常時割り当て + min=1 | 最高（完全排除） | 低 | $50/月〜 | バックグラウンド処理が必要な場合のみ |

取り組む順番としては、まず「Dockerfileの最適化」と「遅延初期化」で追加費用なしに削減し、それでも目標レイテンシを達成できない場合に `min-instances=1` + `Startup CPU Boost` を組み合わせるアプローチが実用的です。

Cloud Run のコールドスタートは完全に排除するものではなく「ユーザーが感じないレベルまで短縮する」ことを目標にすると、コストと品質のバランスを取りやすくなります。

```mermaid
flowchart TD
    A[コールドスタートが問題になっている] --> B{p99レイテンシは\n目標値以内？}
    B -- Yes --> Z[対応不要]
    B -- No --> C[Dockerfileの最適化\nマルチステージ + レイヤー順序 + .dockerignore]
    C --> D{改善した？}
    D -- Yes --> Z
    D -- No --> E[遅延初期化\nインポート削減・DB接続最適化]
    E --> F{改善した？}
    F -- Yes --> Z
    F -- No --> G[Startup CPU Boost\n--cpu-boost フラグを追加]
    G --> H{改善した？}
    H -- Yes --> Z
    H -- No --> I{月額$6〜7の\nコストを許容できる？}
    I -- Yes --> J[min-instances=1 を設定]
    I -- No --> K[Cloud Schedulerで\nウォームアップpingを設定]
    J --> Z
    K --> Z
```

---

姉妹記事「[Cloud Run オートスケーリング徹底解説](/articles/cloud-run-autoscaling)」では、min-instances・max-instances・concurrency の全体的な設定とコスト計算の詳細を扱っています。コールドスタート以外のスケーリングチューニングはそちらを参照してください。
