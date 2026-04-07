---
title: "月5,000円→月0円を達成した話 ─ Claude Code Agent + freee API + BigQuery の完全無料化パターン"
emoji: "💸"
type: "tech"
topics: ["claudecode", "bigquery", "gcp", "freee", "costreduction"]
published: false
status: "draft"
publication_name: "correlate_dev"
---

## はじめに ─ 何に課金していて、なぜ無料化できたのか

副業・個人開発のインフラ費用は「月5,000円以下なら誰も気にしない」という感覚があります。しかし積み重なると年間6万円で、サービスの成果が出ない時期が続くと「このままお金を払い続けてよいのか」という判断を迫られます。

筆者はfreeeの会計データをBigQueryに同期して可視化するシステムを個人利用で動かしていました。当初の月次請求はこの内訳でした。

| サービス | 月額 | 主な原因 |
|---------|------|---------|
| Firestore | 約2,200円 | 毎回フルリードしていた |
| Cloud Run | 約1,500円 | コンテナ起動・メモリ設定が大きすぎた |
| BigQuery | 約800円 | パーティションなし・フルスキャン連発 |
| その他（Logging等） | 約500円 | デフォルト設定のまま |
| 合計 | 約5,000円 | |

これを **Claude Code Agent** に丸投げしてリファクタリングした結果、翌月から月0円（無料枠内）を継続できています。この記事では、その具体的なプロセスと再現可能なパターンを公開します。

:::message
この記事で扱う「無料枠」はすべて2025年時点のGCP Always Free Tierに基づいています。上限は変更される可能性があるため、実際に設計する際はGCP公式ドキュメントを確認してください。
:::

---

## Chapter 1: Claude Code Agent にコスト調査を丸投げする

### 1-1. GCP Billing Export をCSVで落としてAgentに読ませる

最初にやったことはシンプルです。GCP ConsoleのBilling ExportをCSVでエクスポートして、Claude Codeに読み込ませました。

プロンプトはこう渡しました。

```
添付のGCP billing export（CSV）を分析して、以下を教えてください。

1. コストが発生しているサービスを金額降順でリストアップ
2. 各サービスで「無料枠に収まる使い方に変更した場合のコスト」を試算
3. 無料化のために変更が必要なコードや設定を特定
4. 変更の難易度（Low/Medium/High）と優先順位を提示

コードのリファクタリング提案は、変更ファイルと変更内容のdiffで示してください。
```

Claude Codeが返してきた分析レポートは想定以上に精度が高く、 ** 「Firestoreを使っているが、このユースケースならBigQuery + ローカルキャッシュで代替できる」 ** という指摘が核心を突いていました。

### 1-2. Agent に代替案を提案させる会話フロー

コスト分析の次は、アーキテクチャの代替案を議論しました。実際の対話ログ（抜粋）です。

```
[私]
Firestoreをやめてコストゼロにしたい。
このシステムの要件は「freeeの取引データを毎日1回同期してBigQueryに保存、
Looker Studioでグラフ化する」だけ。
Firestoreを使う必要はある？

[Claude Code]
この要件であればFirestoreは不要です。
以下の構成で完全にFirestoreを排除できます。

- 認証トークン保存: Secret Manager（無料枠: 6アクティブバージョン/月）
- バッチ処理状態管理: BigQueryのメタデータテーブル（追加コストなし）
- リアルタイム性: 不要（1日1回バッチで十分）

移行手順を具体的に提示しますか？
```

このやりとりで「人間が判断すべきこと」と「Agentに委ねてよいこと」の境界が明確になりました。

| 人間が判断する | Agentに委ねる |
|------------|------------|
| アーキテクチャの最終決定 | コード実装・リファクタリング |
| 無料枠の限界でのスケール判断 | 無料枠の計算・試算 |
| ビジネスロジックの変更 | 既存ロジックの移植 |
| セキュリティポリシー | セキュリティ実装のコーディング |

### 1-3. CLAUDE.md でスコープを制限する

Agent に自由にコードを書かせると、無関係なファイルまで変更されることがあります。リポジトリルートに `CLAUDE.md` を置いて制約を明示しました。

```markdown
# CLAUDE.md

## 変更してよいファイル
- src/sync/freee_sync.py
- src/bq/schema.py
- infra/terraform/ 以下すべて

## 変更禁止ファイル
- src/auth/ （認証ロジックは手動レビュー必須）
- .env.* （環境変数ファイルは絶対に変更しない）

## 制約事項
- Firestoreへの参照を追加しない
- BigQuery streaming insertを使わない（コスト発生のため）
- 新たなGCPサービスを追加する場合は先に相談すること
```

---

## Chapter 2: freee API を完全無料で使い倒す設計

### 2-1. freee API の無料枠と制約を正確に理解する

freee APIは個人アプリ（自社専用アプリ）であれば無料で利用できます。ただし制限があります。

| 制限項目 | 個人アプリ |
|---------|----------|
| レート制限 | 50リクエスト/分 |
| 月次コール数上限 | なし（ただし規約の範囲内） |
| 対応機能 | 会計・HR・請求ほぼ全機能 |
| Webhook | 対応（会計・HR） |

月次バッチ処理であれば、レート制限は余裕で収まります。1日1回の同期なら1分以内に完了します。

### 2-2. OAuth2.0 PKCE フロー実装 ─ Secret Manager でトークン管理

個人利用の場合、初回のOAuth認証は手動で行い、リフレッシュトークンをSecret Managerに保存する設計にしています。

```python
# src/auth/freee_auth.py
import os
import json
import requests
from google.cloud import secretmanager

SECRET_NAME = "freee-oauth-token"
FREEE_TOKEN_URL = "https://accounts.secure.freee.co.jp/public_api/token"


def get_token_from_secret_manager() -> dict:
    """Secret Manager からトークンを取得する"""
    client = secretmanager.SecretManagerServiceClient()
    project_id = os.environ["GCP_PROJECT_ID"]
    name = f"projects/{project_id}/secrets/{SECRET_NAME}/versions/latest"
    
    response = client.access_secret_version(request={"name": name})
    return json.loads(response.payload.data.decode("UTF-8"))


def refresh_access_token(refresh_token: str) -> dict:
    """リフレッシュトークンでアクセストークンを更新する"""
    client_id = os.environ["FREEE_CLIENT_ID"]
    client_secret = os.environ["FREEE_CLIENT_SECRET"]
    
    response = requests.post(
        FREEE_TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def save_token_to_secret_manager(token_data: dict) -> None:
    """更新したトークンを Secret Manager に上書き保存する"""
    client = secretmanager.SecretManagerServiceClient()
    project_id = os.environ["GCP_PROJECT_ID"]
    parent = f"projects/{project_id}/secrets/{SECRET_NAME}"
    
    payload = json.dumps(token_data).encode("UTF-8")
    client.add_secret_version(
        request={
            "parent": parent,
            "payload": {"data": payload},
        }
    )


def get_valid_access_token() -> str:
    """有効なアクセストークンを返す（必要に応じてリフレッシュ）"""
    token_data = get_token_from_secret_manager()
    
    # アクセストークンの有効期限を確認（freee は24時間）
    # 余裕を持って常にリフレッシュする設計でも問題なし
    new_token = refresh_access_token(token_data["refresh_token"])
    save_token_to_secret_manager(new_token)
    
    return new_token["access_token"]
```

Secret Managerの無料枠は「6アクティブバージョン/月」と「10,000回アクセス/月」です。1つのシークレットを毎日更新しても月30バージョンになりますが、古いバージョンを自動削除するポリシーを設定すれば無料枠内に収まります。

```python
def cleanup_old_secret_versions(keep_versions: int = 2) -> None:
    """古いバージョンを削除して無料枠内に収める"""
    client = secretmanager.SecretManagerServiceClient()
    project_id = os.environ["GCP_PROJECT_ID"]
    parent = f"projects/{project_id}/secrets/{SECRET_NAME}"
    
    versions = list(client.list_secret_versions(request={"parent": parent}))
    # 最新N個以外を無効化
    for version in versions[keep_versions:]:
        if version.state.name == "ENABLED":
            client.disable_secret_version(request={"name": version.name})
```

### 2-3. freee 取引データの差分取得戦略

毎回全件取得するとAPIコール数が増え、BigQueryへの書き込みも増えます。`updated_at` ベースの増分取得を実装しています。

```python
# src/sync/freee_sync.py
import os
from datetime import datetime, timedelta, timezone
from typing import Optional
import requests


FREEE_API_BASE = "https://api.freee.co.jp/api/1"


def fetch_deals(
    access_token: str,
    company_id: int,
    start_date: Optional[str] = None,
) -> list[dict]:
    """
    freee から取引データを取得する。
    start_date が指定されれば差分取得、なければ直近30日を取得。
    """
    if start_date is None:
        # デフォルトは昨日から（日次バッチ想定）
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        start_date = yesterday

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    params = {
        "company_id": company_id,
        "start_issue_date": start_date,
        "offset": 0,
        "limit": 100,  # freee の最大取得件数
    }

    deals = []
    while True:
        response = requests.get(
            f"{FREEE_API_BASE}/deals",
            headers=headers,
            params=params,
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()

        deals.extend(data["deals"])

        # ページネーション処理
        if len(data["deals"]) < params["limit"]:
            break
        params["offset"] += params["limit"]

    return deals
```

### 2-4. BigQuery への書き込み ─ streaming insert を避ける

BigQueryへの書き込みには2つの方法があります。

| 方法 | コスト | 遅延 | 推奨用途 |
|-----|-------|------|---------|
| `load_table_from_json` | 無料 | 数秒〜数分 | バッチ処理 |
| Streaming insert | 1GBあたり$0.01 | ほぼリアルタイム | リアルタイム処理 |

日次バッチであれば `load_table_from_json` で十分です。

```python
# src/bq/loader.py
import json
import os
from datetime import date
from google.cloud import bigquery


def load_deals_to_bigquery(deals: list[dict], target_date: date) -> None:
    """
    取引データを BigQuery にロードする。
    パーティションを指定して重複ロードを防ぐ。
    """
    client = bigquery.Client()
    project = os.environ["GCP_PROJECT_ID"]
    dataset = os.environ["BQ_DATASET"]
    table_id = f"{project}.{dataset}.deals"

    # パーティション指定でロード（同日再実行時は上書き）
    partition_decorator = target_date.strftime("%Y%m%d")
    table_ref = f"{table_id}${partition_decorator}"

    job_config = bigquery.LoadJobConfig(
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,  # 冪等ロード
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        autodetect=False,  # スキーマは明示的に指定
        schema=get_deals_schema(),
    )

    # dict のリストを NDJSON 形式に変換
    ndjson_data = "\n".join(json.dumps(deal, ensure_ascii=False) for deal in deals)

    job = client.load_table_from_json(
        [json.loads(line) for line in ndjson_data.splitlines()],
        table_ref,
        job_config=job_config,
    )
    job.result()  # ジョブ完了まで待機


def get_deals_schema() -> list[bigquery.SchemaField]:
    """freee 取引データのBigQueryスキーマ"""
    return [
        bigquery.SchemaField("id", "INTEGER", mode="REQUIRED"),
        bigquery.SchemaField("company_id", "INTEGER", mode="REQUIRED"),
        bigquery.SchemaField("issue_date", "DATE", mode="REQUIRED"),
        bigquery.SchemaField("due_date", "DATE", mode="NULLABLE"),
        bigquery.SchemaField("amount", "INTEGER", mode="NULLABLE"),
        bigquery.SchemaField("due_amount", "INTEGER", mode="NULLABLE"),
        bigquery.SchemaField("type", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("partner_name", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("ref_number", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("updated_at", "TIMESTAMP", mode="NULLABLE"),
    ]
```

---

## Chapter 3: BigQuery 完全無料化の4パターン

BigQueryの無料枠は以下のとおりです。

| 項目 | 無料枠 |
|-----|-------|
| クエリ処理量 | 1TB/月 |
| ストレージ | 10GB/月（アクティブ）|
| Sandbox モード | クレジットカード不要 |

freee会計データ程度のボリューム（個人・小規模法人）であれば、適切に設計すれば余裕でこの範囲に収まります。

### パターンA: DATE パーティション × クラスタリング

最も効果的なのはこれです。`issue_date`（取引日）でパーティション化し、`type`（取引種別）でクラスタリングします。

```sql
-- テーブル作成時のDDL
CREATE TABLE IF NOT EXISTS `project.dataset.deals`
(
  id INT64 NOT NULL,
  company_id INT64 NOT NULL,
  issue_date DATE NOT NULL,
  due_date DATE,
  amount INT64,
  due_amount INT64,
  type STRING,
  partner_name STRING,
  ref_number STRING,
  updated_at TIMESTAMP
)
PARTITION BY issue_date
CLUSTER BY type, partner_name
OPTIONS (
  require_partition_filter = TRUE  -- フルスキャンを物理的に禁止
);
```

クエリでは必ずパーティションキーを使います。

```sql
-- ✅ パーティション使用（低コスト）
SELECT *
FROM `project.dataset.deals`
WHERE issue_date BETWEEN '2024-04-01' AND '2024-04-30'
  AND type = 'income';

-- ❌ フルスキャン（コスト大）
SELECT *
FROM `project.dataset.deals`
WHERE amount > 100000;
```

`require_partition_filter = TRUE` を設定すると、パーティションキーなしのクエリがエラーになります。誤ったフルスキャンを物理的に防げます。

### パターンB: Sandbox モードで本番データを守る

BigQuery Sandboxはクレジットカードなしで使えるモードです。個人の検証環境として、Sandboxプロジェクトを本番とは分離して使っています。

```
本番: my-freee-project（課金有効、無料枠内を厳守）
検証: my-freee-sandbox（Sandbox、クレジットカード不要）
```

検証時に「スキャン量が多い実験的なクエリ」をSandboxで実行し、最適化してから本番に移す運用です。

### パターンC: マテリアライズドビューで繰り返しクエリを削減

Looker Studioのダッシュボードは毎回BigQueryにクエリを投げます。アクセスが多いと無料枠を消費します。マテリアライズドビューを使うとキャッシュが効き、スキャン量の大幅削減が可能。

```sql
-- 月次集計のマテリアライズドビュー
CREATE MATERIALIZED VIEW `project.dataset.monthly_summary`
OPTIONS (
  enable_refresh = TRUE,
  refresh_interval_minutes = 1440  -- 24時間ごとに更新
)
AS
SELECT
  DATE_TRUNC(issue_date, MONTH) AS month,
  type,
  SUM(amount) AS total_amount,
  COUNT(*) AS deal_count
FROM `project.dataset.deals`
WHERE issue_date >= '2023-01-01'
GROUP BY 1, 2;
```

Looker Studioはこのマテリアライズドビューにクエリを当てます。元データへの問い合わせはマテリアライズドビューの更新時のみになります。

### パターンD: BI Engine + Looker Studio で無料ダッシュボード

BI Engineの無料枠は1GBです。freeeの個人利用データ程度であれば全テーブルをBI Engineのメモリに乗せられます。

```sql
-- BI Engine を有効にしたプロジェクトでは
-- Looker Studio の接続設定で「BI Engine を優先」を選択するだけ
-- 特別なSQL変更は不要
```

Looker Studio（旧Data Studio）自体は完全無料です。BigQuery接続も無料枠内で利用できます。

---

## Chapter 4: 完全無料アーキテクチャの全体設計

### 4-1. アーキテクチャ図

```
[freee API]
    │
    ▼（日次バッチ、OAuth2.0 PKCE）
[Cloud Run Jobs]  ← Cloud Scheduler（3ジョブ/月まで無料）
    │
    ├── Secret Manager（トークン取得・更新）
    │
    ▼（load_table_from_json、streaming insertなし）
[BigQuery]
    │
    ├── パーティション＋クラスタリング
    ├── マテリアライズドビュー（Looker Studio向け）
    │
    ▼
[Looker Studio]（無料）
```

### 4-2. 各サービスの無料枠と実際の使用量

| サービス | 無料枠 | 実際の使用量 | 余裕 |
|---------|-------|------------|------|
| Cloud Run Jobs | 180,000 vCPU秒/月 | 約100秒/日 × 30日 = 3,000秒 | 98.3% 余裕 |
| Cloud Scheduler | 3ジョブ/月 | 1ジョブ | 余裕 |
| BigQuery ストレージ | 10GB/月 | 約0.1GB | 99% 余裕 |
| BigQuery クエリ | 1TB/月 | 約1GB/月 | 99.9% 余裕 |
| Secret Manager | 10,000アクセス/月 | 約60回 | 余裕 |
| Cloud Logging | 50GB/月 | 約10MB | 余裕 |

### 4-3. Billing Budget で $0.01 超えたら即通知

無料枠を超えた瞬間に気づけるよう、Billing Budgetアラートを設定しています。

```python
# infra/setup_billing_alert.py
from google.cloud import billing_budgets_v1


def create_zero_cost_alert(project_id: str, billing_account: str) -> None:
    """$0.01 を超えた時点でアラートを発火する Budget を作成"""
    client = billing_budgets_v1.BudgetServiceClient()
    
    budget = billing_budgets_v1.Budget(
        display_name="zero-cost-alert",
        budget_filter=billing_budgets_v1.Filter(
            projects=[f"projects/{project_id}"],
        ),
        amount=billing_budgets_v1.BudgetAmount(
            specified_amount={"currency_code": "JPY", "units": 1}  # 1円
        ),
        threshold_rules=[
            billing_budgets_v1.ThresholdRule(threshold_percent=0.01),  # $0.01相当
        ],
    )
    
    parent = f"billingAccounts/{billing_account}"
    created = client.create_budget(parent=parent, budget=budget)
    print(f"Budget created: {created.name}")
```

このアラートがSlackに届いたことは一度もありません。

### 4-4. Terraform で無料枠構成をコード管理

インフラをコードで管理しておくと、環境再構築が容易です。

```hcl
# infra/main.tf

# Cloud Run Job（freee同期バッチ）
resource "google_cloud_run_v2_job" "freee_sync" {
  name     = "freee-sync"
  location = var.region

  template {
    template {
      containers {
        image = "gcr.io/${var.project_id}/freee-sync:latest"
        
        resources {
          limits = {
            cpu    = "1"
            memory = "512Mi"  # 必要最小限
          }
        }
        
        env {
          name  = "GCP_PROJECT_ID"
          value = var.project_id
        }
        env {
          name = "FREEE_CLIENT_ID"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.freee_client_id.secret_id
              version = "latest"
            }
          }
        }
      }
    }
  }
}

# Cloud Scheduler（日次実行）
resource "google_cloud_scheduler_job" "freee_sync_daily" {
  name      = "freee-sync-daily"
  schedule  = "0 6 * * *"  # 毎朝6時
  time_zone = "Asia/Tokyo"

  http_target {
    http_method = "POST"
    uri         = "https://run.googleapis.com/v2/${google_cloud_run_v2_job.freee_sync.id}:run"
    
    oauth_token {
      service_account_email = google_service_account.scheduler.email
    }
  }
}
```

---

## Chapter 5: 移行の実際 ─ Before/After

### 5-1. Claude Code との作業セッション（抜粋）

最も時間がかかったのはFirestoreをBigQueryメタデータテーブルに置き換える部分でした。Claude Codeとのやりとりです。

```
[私]
Firestore で管理している「最終同期日時」を
BigQuery テーブルに移行したい。
以下のFirestoreコードをBigQuery版に書き直してください。

[Claude Code]
BigQueryでメタデータ管理をする場合、
以下のアプローチが適切です。

1. sync_metadata テーブルを作成
2. 最終実行時刻を upsert
3. 取得時は最新レコードを SELECT

ただし注意点として、BigQuery は行レベルのロックがないため
同時実行には対応していません。このバッチは並列実行しないことを
前提として設計します。よろしいですか？
```

このように Claude Code は「そのまま実装する」のではなく、前提条件の確認をしてくれました。結果として安全に移行できました。

### 5-2. 移行後1ヶ月の結果

- 月次請求: 5,000円 → 0円
- クエリ処理量: 推定月30GB → 実測月0.8GB（97%削減）
- パフォーマンス: 同期処理時間 約45秒 → 約38秒（むしろ改善）
- 障害: ゼロ（Secret Manager の version cleanup を忘れて一度エラーになったが即修正）

Cloud Runのコールドスタートが気になっていましたが、日次バッチなので問題なく動作しています。

---

## まとめ: 完全無料化チェックリスト

自分のケースに当てはめるための確認リストです。

** アーキテクチャ設計 **
- [ ] Firestoreを使っている → BigQueryメタデータテーブルで代替できるか確認
- [ ] BigQuery streaming insertを使っている → `load_table_from_json` に変更
- [ ] パーティション設定をしていない → DATE パーティション追加
- [ ] `require_partition_filter = TRUE` を設定していない → 設定する
- [ ] クラスタリングを設定していない → よく使うフィルタ列に設定

**freee API**
- [ ] トークンをファイルや環境変数に直接保存している → Secret Manager に移行
- [ ] 毎回全件取得している → `updated_at` ベースの差分取得に変更
- [ ] 取得間隔が短すぎる → レート制限（50req/分）内の設計になっているか確認

** 監視 **
- [ ] Billing Budget の $1 アラートを設定していない → 設定する
- [ ] Secret Manager の古いバージョンを削除していない → クリーンアップジョブを追加

**Claude Code 活用 **
- [ ] `CLAUDE.md` で変更禁止ファイルを定義していない → 定義する
- [ ] コスト分析を感覚でやっている → Billing Export CSV を渡して分析させる

---

## おわりに

この構成で特に良かったのは「Claude Code が間違えた箇所を自分で直した経験」です。最初の提案で `streaming insert` を使うコードが出てきたとき、「それは有料では？」と指摘したら即座に修正されました。

**AIエージェントを使ったコスト最適化の本質は、人間がアーキテクチャの判断基準を持った上で、実装の詳細をAgentに委ねること ** です。「完全に丸投げ」ではなく「判断は人間、実装はAgent」という役割分担が機能しました。

月0円のまま半年が経ちました。freeeのデータはBigQueryに蓄積され、Looker Studioで毎月の収支を確認しています。インフラ費用への心配はゼロ。

---

## 参考リソース

- [freee API リファレンス](https://developer.freee.co.jp/docs)
- [BigQuery 無料枠の詳細（GCP公式）](https://cloud.google.com/bigquery/pricing#free-tier)
- [Cloud Run 無料枠（GCP公式）](https://cloud.google.com/run/pricing#tables)
- [Secret Manager 無料枠（GCP公式）](https://cloud.google.com/secret-manager/pricing)
- [GCP Always Free Products](https://cloud.google.com/free/docs/free-cloud-features)
