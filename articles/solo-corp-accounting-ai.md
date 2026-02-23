---
title: "1人法人のAI経理自動化 ─ freee × Claude × BigQueryで月の経理作業を3時間に圧縮した話"
emoji: "🧾"
type: "tech"
topics: ["freee", "claude", "bigquery", "自動化", "1人法人"]
published: false
publication_name: "correlate_dev"
---

## はじめに ─ 1人法人の経理という地味な重荷

合同会社を設立してから3年、ずっと悩んでいたのが経理作業の重さです。税理士に丸投げするほどの規模でもなく、かといって手作業で毎月30〜40時間を費やすのも限界がありました。

1人法人の経理は「わかっていれば難しくない」のですが、毎月繰り返す作業量が積み重なります。請求書の発行から始まり、入金確認、領収書の仕分け、freeeへの登録、月次損益の確認まで、会計期末が近づくにつれてプレッシャーも増していきます。

この記事では、freee API・Claude API・BigQueryを組み合わせて月次経理作業を月3時間程度に圧縮した実践例を紹介します。Python と Cloud Run のコードを中心に、再現できる形で解説します。対象読者は1人法人のエンジニアや、副業法人を持つフリーランスエンジニアです。

---

## 自動化前の月次作業と所要時間

まず自動化前の状態を整理します。毎月こなしていた作業と、それぞれにかかっていた時間の目安です。

| 作業 | 月あたり時間 | 自動化可否 |
|------|------------|----------|
| 請求書発行・送付 | 1〜2時間 | 一部自動化可 |
| 入金確認・消込 | 30分 | 自動化可 |
| 経費領収書の仕分け・登録 | 2〜3時間 | 自動化可 |
| 銀行・カード明細の照合 | 1〜2時間 | 自動化可 |
| 源泉徴収税の確認 | 30分 | 半自動化可 |
| 月次レポート作成 | 2〜3時間 | 自動化可 |
| freeeへの手入力修正 | 1〜2時間 | 削減可 |
| 合計 | 8〜13時間/月 | ─ |

上記は「慣れたペース」での試算ですが、月初の集中や突発的な確認で倍以上かかる月もありました。これを自動化後は確認・例外処理のみで済む状態にするのが目標です。

---

## システム構成の全体像

3つのサービスを連携させてパイプラインを構成しています。

```mermaid
flowchart LR
    subgraph 取得層
        A[freee API\n取引・請求書・経費]
    end
    subgraph 分類層
        B[Cloud Run\nバッチジョブ]
        C[Claude API\nHaiku\n仕訳判定]
        B -->|取引データ| C
        C -->|勘定科目 + 理由| B
    end
    subgraph 蓄積・集計層
        D[BigQuery\ntransactions table]
        E[Looker Studio\n月次レポート]
        D --> E
    end
    subgraph スケジュール
        F[Cloud Scheduler\n日次 08:00 JST]
    end

    A -->|OAuth2| B
    B -->|MERGE INSERT| D
    F -->|HTTP POST| B
```

各コンポーネントの役割は次のとおりです。

- freee API: 取引・請求書・経費データのソース
- Cloud Run: バッチ処理の実行環境（Pythonコンテナ）
- Claude API (Haiku): 取引の仕訳判定・勘定科目分類
- BigQuery: 経理データの蓄積・集計
- Cloud Scheduler: 日次・月次バッチのトリガー
- Looker Studio: 月次レポートの可視化

---

## Step 1: freee API でデータを取得する

### 認証とトークン管理

freee API は OAuth2 認証を使います。アクセストークンの有効期間は24時間で、リフレッシュトークンは使い捨て（再発行のたびに新しいトークンが払い出される）という仕様です。

トークンは Secret Manager に保管し、Cloud Run から参照する構成にしています。`.env` ファイルや環境変数へのハードコードは禁止です。

```python
import os
from google.cloud import secretmanager
import requests

def get_freee_access_token() -> str:
    """Secret Managerからリフレッシュトークンを取得してアクセストークンを発行します"""
    client = secretmanager.SecretManagerServiceClient()
    secret_name = "projects/{PROJECT_ID}/secrets/freee-refresh-token/versions/latest"

    response = client.access_secret_version(name=secret_name)
    refresh_token = response.payload.data.decode("utf-8").strip()

    token_url = "https://accounts.secure.freee.co.jp/public_api/token"
    payload = {
        "grant_type": "refresh_token",
        "client_id": os.environ["FREEE_CLIENT_ID"],
        "client_secret": os.environ["FREEE_CLIENT_SECRET"],
        "refresh_token": refresh_token,
    }

    res = requests.post(token_url, data=payload)
    res.raise_for_status()
    token_data = res.json()

    # 新しいリフレッシュトークンをSecret Managerに保存
    _save_refresh_token(token_data["refresh_token"])
    return token_data["access_token"]
```

### 取引データの取得

取引一覧（deals）エンドポイントから前日分の取引を取得します。

```python
import datetime

def fetch_deals(access_token: str, company_id: int, date: datetime.date) -> list[dict]:
    """指定日の取引一覧を取得する"""
    url = "https://api.freee.co.jp/api/1/deals"
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {
        "company_id": company_id,
        "start_issue_date": date.isoformat(),
        "end_issue_date": date.isoformat(),
        "limit": 100,
        "offset": 0,
    }

    all_deals = []
    while True:
        res = requests.get(url, headers=headers, params=params)
        res.raise_for_status()
        data = res.json()
        deals = data.get("deals", [])
        all_deals.extend(deals)

        if len(all_deals) >= data.get("total_count", 0):
            break
        params["offset"] += 100

    return all_deals
```

同様に、経費精算（receipts）と請求書（invoices）もそれぞれのエンドポイントから取得します。

---

## Step 2: Claude API で仕訳判定を行う

### プロンプト設計

freeeから取得した取引データをClaude Haikuに渡して勘定科目を判定させます。コスト最適化のため、システムプロンプト部分はプロンプトキャッシングを利用します。

```python
import anthropic
import json

ACCOUNT_ITEMS = [
    {"code": "110", "name": "現金"},
    {"code": "135", "name": "売掛金"},
    {"code": "510", "name": "売上高"},
    {"code": "604", "name": "旅費交通費"},
    {"code": "614", "name": "通信費"},
    {"code": "620", "name": "消耗品費"},
    {"code": "621", "name": "福利厚生費"},
    {"code": "630", "name": "会議費"},
    {"code": "641", "name": "外注費"},
    {"code": "661", "name": "支払手数料"},
    # 以下、使用する勘定科目を列挙
]

SYSTEM_PROMPT = f"""あなたは日本の法人経理に詳しい仕訳アシスタントです。
取引データを受け取り、最も適切な勘定科目を以下のリストから選んでください。

勘定科目リスト:
{json.dumps(ACCOUNT_ITEMS, ensure_ascii=False, indent=2)}

出力は必ず次のJSON形式で返してください:
{{
  "account_item_code": "勘定科目コード",
  "account_item_name": "勘定科目名",
  "confidence": 0.0〜1.0の信頼度,
  "reason": "判定理由（50字以内）"
}}

判断に迷う場合は confidence を 0.7 未満にしてください。その場合は後で人間が確認します。
"""

client = anthropic.Anthropic()

def classify_transaction(deal: dict) -> dict:
    """Claude Haikuで取引の仕訳を判定する"""
    transaction_text = f"""
取引日: {deal.get('issue_date')}
取引先: {deal.get('partner_name', '不明')}
金額: {deal.get('amount')}円
摘要: {deal.get('description', '')}
支払方法: {deal.get('payment_type', '')}
"""

    response = client.messages.create(
        model="claude-haiku-3-5",
        max_tokens=256,
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},  # キャッシュ有効化
            }
        ],
        messages=[
            {"role": "user", "content": transaction_text}
        ],
    )

    result_text = response.content[0].text
    return json.loads(result_text)
```

### コスト試算

claude-haiku-3-5 の料金は入力 $0.80/1M トークン、出力 $4.00/1M トークンです。1件あたりのトークン数を測定すると次のようになりました。

| 区分 | トークン数 | 備考 |
|------|-----------|------|
| システムプロンプト（初回） | 約600 | キャッシュ書き込み1回のみ |
| システムプロンプト（2回目以降） | 約600 | キャッシュ読み込み（90%割引） |
| 取引データ（入力） | 約80〜120 | 1件あたり |
| 判定結果（出力） | 約60〜100 | JSON形式 |

月200件処理する場合の試算：

| 項目 | 計算 | 金額 |
|------|------|------|
| システムプロンプト（書き込み×1） | 600 × $0.75 / 1M | $0.00045 |
| システムプロンプト（読み込み×199） | 600 × 199 × $0.08 / 1M | $0.0096 |
| 取引データ入力（200件） | 100 × 200 × $0.80 / 1M | $0.016 |
| 出力（200件） | 80 × 200 × $4.00 / 1M | $0.064 |
| 合計 | ─ | 約$0.09/月 |

月200件でも $0.10 未満という計算です。経費精算や請求書の自動仕訳まで含めても月 $1 以下に収まります。

---

## Step 3: BigQuery にデータを蓄積する

### テーブル設計

経理データの蓄積先として BigQuery を使います。冪等性を保つため、`transaction_id` をキーに MERGE で INSERT します。

```sql
CREATE TABLE IF NOT EXISTS `{PROJECT_ID}.accounting.transactions`
(
  transaction_id    STRING    NOT NULL,
  transaction_date  DATE      NOT NULL,
  amount            INT64     NOT NULL,
  counterparty_name STRING,
  description       STRING,
  account_item_code STRING,
  account_item_name STRING,
  ai_classified     BOOL      DEFAULT FALSE,
  ai_confidence     FLOAT64,
  ai_reason         STRING,
  created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP()
)
PARTITION BY transaction_date
CLUSTER BY account_item_code, counterparty_name;
```

パーティションを `transaction_date` に設定することで、月次クエリのスキャン量を大幅に削減できます。

### Python から MERGE INSERT する

```python
from google.cloud import bigquery

def upsert_transactions(
    bq_client: bigquery.Client,
    project_id: str,
    rows: list[dict],
) -> None:
    """BigQueryへMERGE INSERTで冪等に書き込む"""
    table_ref = f"{project_id}.accounting.transactions"

    # 一時テーブルにロード
    job_config = bigquery.LoadJobConfig(
        write_disposition="WRITE_TRUNCATE",
        schema=[
            bigquery.SchemaField("transaction_id", "STRING"),
            bigquery.SchemaField("transaction_date", "DATE"),
            bigquery.SchemaField("amount", "INTEGER"),
            bigquery.SchemaField("counterparty_name", "STRING"),
            bigquery.SchemaField("description", "STRING"),
            bigquery.SchemaField("account_item_code", "STRING"),
            bigquery.SchemaField("account_item_name", "STRING"),
            bigquery.SchemaField("ai_classified", "BOOL"),
            bigquery.SchemaField("ai_confidence", "FLOAT"),
            bigquery.SchemaField("ai_reason", "STRING"),
        ],
    )

    temp_table = f"{project_id}.accounting._tmp_transactions"
    load_job = bq_client.load_table_from_json(rows, temp_table, job_config=job_config)
    load_job.result()

    # MERGE で本テーブルに反映
    merge_query = f"""
    MERGE `{table_ref}` AS T
    USING `{temp_table}` AS S
      ON T.transaction_id = S.transaction_id
    WHEN MATCHED THEN
      UPDATE SET
        account_item_code = S.account_item_code,
        account_item_name = S.account_item_name,
        ai_classified     = S.ai_classified,
        ai_confidence     = S.ai_confidence,
        ai_reason         = S.ai_reason
    WHEN NOT MATCHED THEN
      INSERT ROW
    """
    bq_client.query(merge_query).result()
```

---

## Step 4: 月次レポートの自動集計

BigQuery に蓄積したデータを使って、月次の損益サマリーを自動生成します。

```sql
-- 月次損益サマリー
SELECT
  FORMAT_DATE('%Y-%m', transaction_date)  AS month,
  account_item_name,
  SUM(amount)                             AS total_amount,
  COUNT(*)                                AS transaction_count,
  ROUND(AVG(ai_confidence), 2)            AS avg_ai_confidence
FROM
  `{PROJECT_ID}.accounting.transactions`
WHERE
  transaction_date BETWEEN DATE_TRUNC(CURRENT_DATE(), MONTH)
                       AND LAST_DAY(CURRENT_DATE(), MONTH)
GROUP BY
  month,
  account_item_name
ORDER BY
  total_amount DESC;
```

このクエリを Looker Studio のデータソースに設定しておくと、毎月1日にレポートが自動更新されます。追加の手作業は不要です。

---

## Step 5: Cloud Scheduler で全自動化する

### Cloud Run エントリーポイント

Cloud Scheduler から HTTP POST で呼び出されるエンドポイントを用意します。

```python
from flask import Flask, request, jsonify
import datetime

app = Flask(__name__)

@app.route("/run/daily-accounting", methods=["POST"])
def daily_accounting():
    """日次経理バッチ: 前日分の取引を取得・分類・BQに保存"""
    target_date = datetime.date.today() - datetime.timedelta(days=1)

    access_token = get_freee_access_token()
    company_id = int(os.environ["FREEE_COMPANY_ID"])

    # 取引データ取得
    deals = fetch_deals(access_token, company_id, target_date)

    # 仕訳判定
    rows = []
    for deal in deals:
        classification = classify_transaction(deal)
        rows.append({
            "transaction_id": str(deal["id"]),
            "transaction_date": deal["issue_date"],
            "amount": deal.get("amount", 0),
            "counterparty_name": deal.get("partner_name", ""),
            "description": deal.get("description", ""),
            "account_item_code": classification["account_item_code"],
            "account_item_name": classification["account_item_name"],
            "ai_classified": True,
            "ai_confidence": classification["confidence"],
            "ai_reason": classification["reason"],
        })

    # BigQueryへ保存
    if rows:
        bq_client = bigquery.Client()
        upsert_transactions(bq_client, os.environ["GCP_PROJECT_ID"], rows)

    return jsonify({"status": "ok", "processed": len(rows)})
```

### Cloud Scheduler の設定

```bash
# 日次バッチ（毎日8:00 JST）
gcloud scheduler jobs create http daily-accounting \
  --location=asia-northeast1 \
  --schedule="0 8 * * *" \
  --time-zone="Asia/Tokyo" \
  --uri="https://{CLOUD_RUN_URL}/run/daily-accounting" \
  --message-body='{}' \
  --oidc-service-account-email="{SERVICE_ACCOUNT}@{PROJECT_ID}.iam.gserviceaccount.com"
```

これで毎朝8時に前日分の取引が自動で分類・BigQuery に保存されます。

---

## 実際の効果測定

3ヶ月運用した結果です。

### 時間削減

| 作業 | 自動化前 | 自動化後 | 削減 |
|------|---------|---------|------|
| 入金確認・消込 | 30分 | 5分（確認のみ） | 83% |
| 経費仕分け・登録 | 2〜3時間 | 20分（例外処理） | 90% |
| 明細照合 | 1〜2時間 | 0分（自動化） | 100% |
| 月次レポート作成 | 2〜3時間 | 10分（確認のみ） | 95% |
| 合計 | 8〜13時間 | 1〜2時間 | 約87% |

### インフラコスト

| サービス | 月額 |
|---------|------|
| Claude API (Haiku) | $5〜$15 |
| Cloud Run | $2〜$5 |
| BigQuery | $1〜$3 |
| Cloud Scheduler | $0.10 |
| Secret Manager | $0.06 |
| 合計 | $8〜$23 |

月額 $15 前後、日本円で約2,200円程度のコストで月8〜13時間の作業が自動化できます。時給換算すると非常に高いROIです。

### AI分類精度

3ヶ月の運用データでの仕訳精度は次のとおりです。

| confidence 閾値 | 取引件数 | 自動確定率 | 要確認率 |
|---------------|---------|----------|--------|
| 0.9以上 | 68% | 正解率97% | 3% |
| 0.7〜0.9 | 22% | 正解率89% | 11% |
| 0.7未満 | 10% | ─ | 要人間レビュー |

confidence が 0.9 以上の取引は自動確定、0.7 未満は人間がレビューする運用にしています。全体の約90%は人の目を通さずに正しく分類できています。

---

## まとめ・次のステップ

freee × Claude × BigQuery の組み合わせで、月次経理作業の大部分を自動化できました。特に効果が大きかったのは、仕訳判定の自動化です。「これは何費？」という毎回の判断コストが大幅に減り、月末のプレッシャーもほぼなくなりました。

現在の課題と次のステップとして考えているのは次の点です。

- 請求書の自動作成・送付（freee 請求書 API + Cloud Tasks）
- 低confidence案件のSlack通知とインライン承認フロー
- 年次決算レポートの自動生成（BigQuery ML による売上予測）
- freee と銀行APIの直接連携（GMO あおぞらなど対応行）

1人法人の経理は「完全自動化」が目標ではなく、「自分が確認する部分だけに集中できる状態」を作ることが大切だと感じています。AIを使った自動化は、その状態を比較的低コストで実現できるため、同じ状況にある方にとって参考になれば幸いです。

コードの全量は GitHub に公開予定です。

---

## 参考

- [freee API ドキュメント](https://developer.freee.co.jp/reference/accounting/reference)
- [Anthropic API ドキュメント（Prompt Caching）](https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching)
- [BigQuery MERGE ステートメント](https://cloud.google.com/bigquery/docs/reference/standard-sql/dml-syntax#merge_statement)
