---
title: "Claude Code × BigQuery で月次経営ダッシュボードを完全自動化した話 — Agent Teamsと定期実行パイプラインの設計"
emoji: "📊"
type: "tech"
topics: ["claudecode", "bigquery", "agentteams", "googlecloud", "llm"]
published: false
status: "draft"
publication_name: "correlate_dev"
---

## はじめに

「月次レポートの作成に毎回2〜3日かかる」「SQLを書ける人材が限られている」「データはあるのに意思決定が遅れる」——経営管理やデータエンジニアリングの現場で、このような声を聞いたことはないでしょうか。

筆者が運営するプロジェクトでは長らくこの課題を抱えていました。BigQueryにデータは蓄積されているのに、それを意思決定に使えるレポートへと変換する作業が毎月の手動ボトルネックになっていたのです。

本記事では、**Claude Code の Agent Teams 機能 × BigQuery × Cloud Scheduler** を組み合わせて、この問題を構造ごと解消した実装パターンを紹介します。「AIでSQL生成してみた」というレベルではなく、**月次経営ダッシュボードの生成・配信・意思決定支援まで含めたエンドツーエンドの本番パイプライン**を設計・運用した経験をもとに書いています。

## システム全体構成

まず全体像を把握してください。

```
Cloud Scheduler（月末 23:59 トリガー）
    ↓
Cloud Run Job（パイプライン実行コンテナ）
    ↓
Orchestrator Agent（Claude Sonnet）
    ├── SQL Agent       → BigQuery → 集計データ取得
    ├── Analysis Agent  → インサイト・異常値検知
    ├── Viz Agent       → Markdown/HTML レポート生成
    └── Distribution Agent → Slack / Email 配信
```

このパイプラインが毎月自動で動き、翌月1日の朝には経営陣の Slack に月次サマリーが届いています。

## 1. なぜ「月次レポート」が自動化の最適解なのか

### 1-1. 経営管理の「最後の手動作業」を特定する

多くの組織では、データ基盤（BigQuery や Redshift）への投資は完了しているにもかかわらず、そのデータを「読める状態」にする最終工程が手動のままです。

具体的には以下の作業が手動で行われていることが多いです。

- 前月末の数値を BigQuery から手動クエリで取得
- Excel にコピーして前月比を計算
- グラフを作成して PowerPoint に貼り付け
- 経営コメントを書いて配布

この一連の作業は、ビジネスロジックとデータ操作の両方を理解した人材が必要であり、属人化しやすく、スケールもしません。

### 1-2. Claude Code が解決する3つのボトルネック

| ボトルネック | 従来の問題 | Claude Code での解決 |
|---|---|---|
| **SQL 記述** | データエンジニアに依頼が必要 | スキーマを読んで SQL を自動生成 |
| **数値解釈** | 担当者の主観に依存 | KPI 定義に基づく一貫した解釈 |
| **レポート作成** | テンプレート埋め作業が発生 | Markdown/HTML の自動生成 |

### 1-3. BigQuery が経営ダッシュボードのデータ基盤として優れている理由

BigQuery を選択した理由は主に3つです。

1. **スキーマが宣言的**: `INFORMATION_SCHEMA.COLUMNS` から LLM がテーブル構造を読み取れる
2. **dry_run が標準機能**: クエリ実行前にコスト見積もりができるため、LLM が誤ったクエリを生成してもコストを抑えられる
3. **権限分離が容易**: サービスアカウントに `bigquery.jobs.create` のみ付与した読み取り専用権限で動かせる

## 2. アーキテクチャ設計：Agent Teams の全体構成

### 2-1. オーケストレーター vs サブエージェント：役割分担の考え方

Claude Code の Agent Teams では、**オーケストレーターが全体の意思決定を担い、サブエージェントが具体的な実行を担う**という分離が重要です。

オーケストレーターに持たせるもの：
- 月次レポートの目的と KPI 定義
- 各サブエージェントへの指示生成
- サブエージェントの出力検証
- エラー時のリトライ判断

サブエージェントに持たせるもの：
- 特化したタスクのみ（SQL 生成 / 分析 / 可視化 / 配信）
- 限定的なツールアクセス権限
- 出力形式の厳密な定義

この分離により、サブエージェントの出力が壊れても、オーケストレーターが検知してリトライできます。

### 2-2. 4つのサブエージェントの設計

**SQL Agent**
- 入力：KPI 定義 + BigQuery スキーマ
- 処理：SQL 生成 → dry_run → 実行
- 出力：JSON 形式の集計結果

**Analysis Agent**
- 入力：SQL Agent の出力 + 前月データ
- 処理：前月比計算・異常値検知・トレンド分析
- 出力：構造化インサイト（重要度付き）

**Viz Agent**
- 入力：Analysis Agent の出力
- 処理：Markdown レポート生成（テーブル・数値ハイライト）
- 出力：レポート本文（Markdown）

**Distribution Agent**
- 入力：Viz Agent の出力
- 処理：Slack Block Kit 組み立て・Email HTML 生成
- 出力：配信実行・配信ログ

### 2-3. エージェント間のコンテキスト設計

エージェント間で渡すデータは**最小限にする**ことが重要です。

```python
# 悪い例：前のエージェントの全出力をそのまま渡す
next_agent_prompt = f"前のエージェントの出力: {full_output}\n次の作業をしてください"

# 良い例：構造化された必要情報のみを渡す
analysis_context = {
    "kpi_results": sql_results,          # 集計データ
    "previous_month": prev_month_data,   # 前月データ（比較用）
    "anomaly_threshold": 0.15,           # 異常値閾値（15%）
    "report_date": report_date,          # レポート対象月
}
next_agent_prompt = f"以下のデータを分析してください:\n{json.dumps(analysis_context, ensure_ascii=False)}"
```

### 2-4. 信頼性設計：LLM に「やらせないこと」を定義する

LLM に委任しない処理を明確にすることが、本番運用の安定性を担保します。

**LLM に委任しないこと：**
- 実際の BigQuery クエリ実行（Python SDK で実行し、結果を渡す）
- 認証情報の取り扱い（Secret Manager から Python で取得）
- Slack・Email の送信（Python ライブラリで実行）
- エラーハンドリングの最終判断（コードで定義したルールに従う）

**LLM に委任すること：**
- SQL の生成（ただし dry_run で検証してから実行）
- 数値の解釈とインサイト生成
- レポートの自然言語部分の作成
- 異常値の重要度判定

## 3. BigQuery × Claude Code の連携実装

### 3-1. 環境構築と認証設計

```python
# requirements.txt
google-cloud-bigquery==3.25.0
anthropic==0.34.0
google-cloud-secret-manager==2.20.0
slack-sdk==3.31.0
```

```python
# config.py - 認証とクライアント初期化
import os
from google.cloud import bigquery, secretmanager
import anthropic

def get_secret(secret_name: str) -> str:
    """Secret Manager からシークレットを取得"""
    client = secretmanager.SecretManagerServiceClient()
    project_id = os.environ["GOOGLE_CLOUD_PROJECT"]
    name = f"projects/{project_id}/secrets/{secret_name}/versions/latest"
    response = client.access_secret_version(request={"name": name})
    return response.payload.data.decode("UTF-8")

def init_clients():
    """各サービスクライアントを初期化して返す"""
    bq_client = bigquery.Client()  # Workload Identity で認証
    anthropic_client = anthropic.Anthropic(
        api_key=get_secret("anthropic-api-key")
    )
    return bq_client, anthropic_client
```

### 3-2. スキーマ理解エージェント：KPI を逆引きする

BigQuery の `INFORMATION_SCHEMA` を使って、LLM がテーブル構造を自律的に理解できるようにします。

```python
def fetch_schema_for_kpi(bq_client, dataset_id: str, kpi_keywords: list[str]) -> str:
    """KPI に関連するテーブルのスキーマを取得する"""
    query = f"""
    SELECT
        c.table_name,
        c.column_name,
        c.data_type,
        c.description
    FROM `{dataset_id}.INFORMATION_SCHEMA.COLUMNS` c
    JOIN `{dataset_id}.INFORMATION_SCHEMA.TABLES` t
        ON c.table_name = t.table_name
    WHERE t.table_type = 'BASE TABLE'
    ORDER BY c.table_name, c.ordinal_position
    """
    
    results = bq_client.query(query).result()
    
    # スキーマを LLM が読みやすい形式に変換
    schema_text = ""
    current_table = ""
    for row in results:
        if row.table_name != current_table:
            current_table = row.table_name
            schema_text += f"\n## テーブル: {current_table}\n"
        desc = f" ({row.description})" if row.description else ""
        schema_text += f"  - {row.column_name}: {row.data_type}{desc}\n"
    
    return schema_text
```

### 3-3. SQL 生成エージェント：dry_run でコスト検証してから実行する

これが本パイプラインで最も重要な設計判断です。**LLM が生成した SQL を直接実行せず、必ず dry_run でコスト検証を挟む**構造にしています。

```python
def sql_agent(
    anthropic_client,
    bq_client,
    schema_text: str,
    kpi_definition: dict,
    target_month: str,
) -> dict:
    """SQL を生成・検証・実行して結果を返す"""
    
    # Step 1: SQL 生成
    response = anthropic_client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=2000,
        messages=[{
            "role": "user",
            "content": f"""
以下のスキーマとKPI定義に基づいて、BigQuery Standard SQL を生成してください。

## スキーマ
{schema_text}

## KPI定義
{json.dumps(kpi_definition, ensure_ascii=False, indent=2)}

## 対象月
{target_month}（例: 2024-11-01 から 2024-11-30 まで）

## 制約
- WITH句を使って可読性を高めること
- 金額はすべて円単位で返すこと
- 結果は1行のサマリーとして返すこと（GROUP BY 不要）
- コメントを必ず記載すること

SQLのみを返してください。説明は不要です。
            """
        }]
    )
    
    generated_sql = response.content[0].text.strip()
    # コードブロックを除去
    if generated_sql.startswith("```"):
        generated_sql = "\n".join(generated_sql.split("\n")[1:-1])
    
    # Step 2: dry_run でコスト検証（重要）
    job_config = bigquery.QueryJobConfig(dry_run=True, use_query_cache=False)
    try:
        dry_run_job = bq_client.query(generated_sql, job_config=job_config)
        bytes_processed = dry_run_job.total_bytes_processed
        gb_processed = bytes_processed / (1024 ** 3)
        estimated_cost_usd = gb_processed * 6.25  # $6.25/TB
        
        # コスト上限チェック（1クエリあたり $1 を上限に設定）
        if estimated_cost_usd > 1.0:
            raise ValueError(
                f"クエリコスト超過: ${estimated_cost_usd:.4f} (上限: $1.00)\n"
                f"処理データ量: {gb_processed:.2f} GB\n"
                f"生成されたSQL:\n{generated_sql}"
            )
        
        print(f"dry_run OK: {gb_processed:.3f} GB / ${estimated_cost_usd:.5f}")
        
    except Exception as e:
        if "クエリコスト超過" in str(e):
            raise  # コスト超過は再送出
        raise ValueError(f"SQL構文エラー: {e}\n生成SQL:\n{generated_sql}")
    
    # Step 3: 実際のクエリ実行
    results = bq_client.query(generated_sql).result()
    rows = [dict(row) for row in results]
    
    return {
        "sql": generated_sql,
        "results": rows,
        "bytes_processed": bytes_processed,
        "estimated_cost_usd": estimated_cost_usd,
    }
```

### 3-4. 結果解釈エージェント：数値を「経営言語」に変換する

```python
def analysis_agent(
    anthropic_client,
    kpi_results: dict,
    previous_month_results: dict,
    kpi_definitions: dict,
) -> dict:
    """KPI の数値を経営インサイトに変換する"""
    
    # 前月比を計算（Python で計算してから LLM に渡す）
    comparisons = {}
    for kpi_name, current_value in kpi_results.items():
        prev_value = previous_month_results.get(kpi_name)
        if prev_value and prev_value != 0:
            change_rate = (current_value - prev_value) / prev_value
            comparisons[kpi_name] = {
                "current": current_value,
                "previous": prev_value,
                "change_rate": change_rate,
                "is_anomaly": abs(change_rate) >= 0.15,  # ±15% で異常フラグ
            }
    
    response = anthropic_client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=3000,
        messages=[{
            "role": "user",
            "content": f"""
経営幹部向けに月次KPI分析を行ってください。

## KPI定義
{json.dumps(kpi_definitions, ensure_ascii=False, indent=2)}

## 今月と前月の比較データ
{json.dumps(comparisons, ensure_ascii=False, indent=2)}

以下の形式でJSONを返してください：

{{
  "executive_summary": "3文以内の経営サマリー",
  "anomalies": [
    {{
      "kpi": "KPI名",
      "severity": "critical/warning/info",
      "message": "異常の説明と推定原因",
      "recommended_action": "推奨アクション"
    }}
  ],
  "highlights": ["ポジティブなハイライト1", "ハイライト2"],
  "risks": ["リスク1", "リスク2"],
  "next_focus": "来月最優先で見るべき指標と理由"
}}

数値は具体的に記載し、抽象的な表現は避けてください。
            """
        }]
    )
    
    insight_text = response.content[0].text.strip()
    # JSON ブロック抽出
    if "```json" in insight_text:
        insight_text = insight_text.split("```json")[1].split("```")[0].strip()
    
    return json.loads(insight_text)
```

## 4. KPI 設計とプロンプト設計の実践

### 4-1. 経営 KPI のプロンプト化

KPI 定義をコードとして管理することで、レポートの一貫性を担保します。

```python
# kpi_definitions.py - KPI定義をコードで管理
KPI_DEFINITIONS = {
    "mrr": {
        "name": "MRR（月次経常収益）",
        "unit": "円",
        "description": "当月に発生したサブスクリプション収益の合計",
        "good_direction": "increase",  # 上昇が良い方向
        "anomaly_threshold": 0.10,     # ±10% で警告
    },
    "churn_rate": {
        "name": "チャーンレート（解約率）",
        "unit": "%",
        "description": "当月解約顧客数 / 月初顧客数",
        "good_direction": "decrease",  # 減少が良い方向
        "anomaly_threshold": 0.05,     # ±5pp で警告
    },
    "cac": {
        "name": "CAC（顧客獲得コスト）",
        "unit": "円",
        "description": "当月マーケティング費用 / 当月新規獲得顧客数",
        "good_direction": "decrease",
        "anomaly_threshold": 0.20,     # ±20% で警告
    },
    "ltv_cac_ratio": {
        "name": "LTV/CAC比率",
        "unit": "倍",
        "description": "顧客生涯価値 / 顧客獲得コスト（3以上が健全）",
        "good_direction": "increase",
        "healthy_minimum": 3.0,        # 健全ラインの定義
    },
}
```

### 4-2. 異常値検知の実装

LLM に異常値の「判定」をさせるのではなく、**Python でフラグを立てて LLM に「解釈」させる**設計にしています。

```python
def detect_anomalies(kpi_results: dict, kpi_definitions: dict) -> list[dict]:
    """異常値を検知してフラグを返す（LLMを使わず Python で実行）"""
    anomalies = []
    
    for kpi_key, definition in kpi_definitions.items():
        if kpi_key not in kpi_results:
            continue
        
        current = kpi_results[kpi_key]["current"]
        previous = kpi_results[kpi_key]["previous"]
        threshold = definition.get("anomaly_threshold", 0.15)
        
        if previous == 0:
            continue
        
        change_rate = (current - previous) / previous
        is_bad_direction = (
            definition["good_direction"] == "increase" and change_rate < -threshold
        ) or (
            definition["good_direction"] == "decrease" and change_rate > threshold
        )
        
        if abs(change_rate) >= threshold:
            anomalies.append({
                "kpi": kpi_key,
                "kpi_name": definition["name"],
                "change_rate": change_rate,
                "is_bad_direction": is_bad_direction,
                "severity": "critical" if abs(change_rate) >= threshold * 2 else "warning",
            })
    
    return sorted(anomalies, key=lambda x: abs(x["change_rate"]), reverse=True)
```

## 5. 定期実行パイプラインの構築

### 5-1. Cloud Scheduler × Cloud Run Jobs の設定

```yaml
# cloud-run-job.yaml
apiVersion: run.googleapis.com/v1
kind: Job
metadata:
  name: monthly-dashboard-job
spec:
  template:
    spec:
      template:
        spec:
          containers:
          - image: asia-northeast1-docker.pkg.dev/${PROJECT_ID}/pipeline/dashboard:latest
            env:
            - name: GOOGLE_CLOUD_PROJECT
              value: ${PROJECT_ID}
            - name: TARGET_DATASET
              value: analytics_prod
            - name: SLACK_CHANNEL
              value: "#monthly-report"
            resources:
              limits:
                memory: 1Gi
                cpu: "1"
          timeoutSeconds: 1800   # 30分タイムアウト
          maxRetries: 2          # 失敗時2回リトライ
```

```bash
# Cloud Scheduler の設定（月末 23:59 JST）
gcloud scheduler jobs create http monthly-dashboard \
  --schedule="59 23 L * *" \
  --uri="https://run.googleapis.com/v1/projects/${PROJECT_ID}/locations/asia-northeast1/jobs/monthly-dashboard-job:run" \
  --message-body="{}" \
  --oauth-service-account-email="${SA_EMAIL}" \
  --location=asia-northeast1 \
  --time-zone="Asia/Tokyo"
```

### 5-2. エラーハンドリングとフォールバック設計

```python
# orchestrator.py - エラーハンドリング付きオーケストレーター
import logging
from typing import Optional

logger = logging.getLogger(__name__)

def run_monthly_pipeline(target_month: str) -> dict:
    """月次パイプラインのメインエントリーポイント"""
    bq_client, anthropic_client = init_clients()
    pipeline_result = {"status": "running", "errors": []}
    
    try:
        # Step 1: スキーマ取得
        schema_text = fetch_schema_for_kpi(bq_client, "analytics_prod", list(KPI_DEFINITIONS.keys()))
        
        # Step 2: SQL Agent（失敗しても後続を止めない）
        kpi_results = {}
        for kpi_key, kpi_def in KPI_DEFINITIONS.items():
            try:
                result = sql_agent(anthropic_client, bq_client, schema_text, kpi_def, target_month)
                kpi_results[kpi_key] = result["results"][0] if result["results"] else None
            except Exception as e:
                logger.error(f"SQL Agent エラー ({kpi_key}): {e}")
                pipeline_result["errors"].append({"step": "sql_agent", "kpi": kpi_key, "error": str(e)})
                kpi_results[kpi_key] = None  # 欠損扱いで続行
        
        # Step 3: Analysis Agent
        prev_month = get_previous_month_data(bq_client, target_month)  # BQ から前月データ取得
        analysis_result = analysis_agent(anthropic_client, kpi_results, prev_month, KPI_DEFINITIONS)
        
        # Step 4: Viz Agent
        report_markdown = viz_agent(anthropic_client, kpi_results, analysis_result, target_month)
        
        # Step 5: Distribution Agent
        distribute_report(report_markdown, analysis_result, pipeline_result["errors"])
        
        pipeline_result["status"] = "completed"
        
    except Exception as e:
        pipeline_result["status"] = "failed"
        pipeline_result["fatal_error"] = str(e)
        logger.exception("パイプライン致命的エラー")
        # 致命的エラーは Slack にアラート送信
        notify_error_to_slack(str(e), target_month)
        raise
    
    return pipeline_result
```

### 5-3. GitHub Actions を使った代替パイプライン（GCP 不要版）

GCP を使わない場合は GitHub Actions でも同様のパイプラインを組めます。

```yaml
# .github/workflows/monthly-dashboard.yml
name: Monthly Dashboard Pipeline

on:
  schedule:
    - cron: '59 14 L * *'  # UTC 14:59 = JST 23:59
  workflow_dispatch:         # 手動トリガーも許可

jobs:
  generate-dashboard:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    
    steps:
      - uses: actions/checkout@v4
      
      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      
      - name: Install dependencies
        run: pip install -r requirements.txt
      
      - name: Authenticate to Google Cloud
        uses: google-github-actions/auth@v2
        with:
          credentials_json: ${{ secrets.GCP_SA_KEY }}
      
      - name: Run monthly pipeline
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          SLACK_BOT_TOKEN: ${{ secrets.SLACK_BOT_TOKEN }}
          GOOGLE_CLOUD_PROJECT: ${{ secrets.GCP_PROJECT_ID }}
        run: |
          python -m src.orchestrator --month "$(date -d 'last month' '+%Y-%m')"
```

## 6. レポート生成と Slack 配信

### 6-1. Slack Block Kit を使った経営サマリー配信

```python
def distribute_report(
    report_markdown: str,
    analysis_result: dict,
    errors: list,
) -> None:
    """Slack に経営サマリーを配信する"""
    from slack_sdk import WebClient
    
    slack_client = WebClient(token=get_secret("slack-bot-token"))
    
    # 重大異常値があれば色を変える
    has_critical = any(a["severity"] == "critical" for a in analysis_result.get("anomalies", []))
    color = "#E53E3E" if has_critical else "#38A169"  # 赤 or 緑
    
    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"📊 月次経営レポート — {analysis_result['report_month']}",
            }
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*エグゼクティブサマリー*\n{analysis_result['executive_summary']}"
            }
        },
    ]
    
    # 異常値があれば追加
    if analysis_result.get("anomalies"):
        anomaly_text = "\n".join([
            f"{'🔴' if a['severity'] == 'critical' else '🟡'} *{a['kpi_name']}*: {a['message']}"
            for a in analysis_result["anomalies"][:3]  # 上位3件のみ
        ])
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*要注意項目*\n{anomaly_text}"}
        })
    
    # パイプラインエラーがあれば通知
    if errors:
        blocks.append({
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f"⚠️ {len(errors)}件のKPI取得エラーがありました"}]
        })
    
    slack_client.chat_postMessage(
        channel="#monthly-report",
        blocks=blocks,
        text=f"月次経営レポート: {analysis_result['executive_summary'][:100]}",
    )
```

## 7. 本番運用で直面した課題と対策

### 7-1. LLM の出力ブレを抑えるバリデーション設計

最初の1ヶ月で最も苦労したのが、**LLM の出力フォーマットが安定しない問題**です。

Analysis Agent が返す JSON の構造が微妙にずれることがありました。対策として Pydantic によるスキーマ検証を導入しました。

```python
from pydantic import BaseModel, Field

class AnomalyItem(BaseModel):
    kpi: str
    severity: str = Field(pattern="^(critical|warning|info)$")
    message: str
    recommended_action: str

class AnalysisResult(BaseModel):
    executive_summary: str = Field(max_length=500)
    anomalies: list[AnomalyItem]
    highlights: list[str]
    risks: list[str]
    next_focus: str

def parse_analysis_result(raw_json: str) -> AnalysisResult:
    """LLM の出力を検証してパースする"""
    try:
        data = json.loads(raw_json)
        return AnalysisResult(**data)
    except (json.JSONDecodeError, ValueError) as e:
        # パース失敗時はリトライ（最大2回）
        raise ValueError(f"Analysis Agent の出力が不正です: {e}\n生データ: {raw_json[:500]}")
```

### 7-2. クエリコスト管理：月次上限の設定方法

BigQuery のコスト管理は Cloud Console だけでなく、コードレベルでも制御します。

```python
# BigQuery プロジェクト全体のカスタムクォータ設定
# ※ Cloud Console > BigQuery > [プロジェクト設定] から設定可能
# 月次上限: 100 GB（おおよそ $0.625/月）

# コードレベルでのクエリコスト上限チェック
MAX_BYTES_PER_QUERY = 10 * 1024 ** 3     # 1クエリあたり 10 GB まで
MAX_MONTHLY_BYTES = 100 * 1024 ** 3      # 月次合計 100 GB まで

def check_monthly_budget(bq_client, project_id: str) -> float:
    """今月の BigQuery 使用量を確認する"""
    query = """
    SELECT
        SUM(total_bytes_processed) AS total_bytes
    FROM `region-us`.INFORMATION_SCHEMA.JOBS_BY_PROJECT
    WHERE
        DATE(creation_time) >= DATE_TRUNC(CURRENT_DATE(), MONTH)
        AND statement_type = 'SELECT'
        AND error_result IS NULL
    """
    result = list(bq_client.query(query).result())
    return result[0].total_bytes if result[0].total_bytes else 0
```

### 7-3. 監査ログ：「なぜこの数値が出たか」を追跡可能にする

LLM が関与したレポートでは、**数値の根拠を追跡可能にする**ことが信頼性の鍵です。

```python
def save_audit_log(
    bq_client,
    target_month: str,
    kpi_results: dict,
    generated_sqls: dict,
    analysis_result: dict,
) -> None:
    """パイプラインの実行ログを BigQuery に保存する"""
    table_id = f"{os.environ['GOOGLE_CLOUD_PROJECT']}.pipeline_logs.monthly_dashboard_runs"
    
    rows = [{
        "run_timestamp": datetime.now(timezone.utc).isoformat(),
        "target_month": target_month,
        "kpi_name": kpi_key,
        "kpi_value": json.dumps(value),
        "sql_used": generated_sqls.get(kpi_key, ""),
        "bytes_processed": kpi_results.get(kpi_key, {}).get("bytes_processed", 0),
        "model_used": "claude-sonnet-4-5",
    } for kpi_key, value in kpi_results.items()]
    
    errors = bq_client.insert_rows_json(table_id, rows)
    if errors:
        logger.error(f"監査ログ保存エラー: {errors}")
```

## 8. 発展：経営判断の完全自動化へ

### 8-1. 実運用で得られた知見

6ヶ月の運用を経て、以下の知見が得られています。

**有効だった設計判断：**
- dry_run によるコスト検証は必須（LLM が生成する SQL は結合が複雑になりがち）
- Pydantic によるスキーマ検証で出力ブレが激減
- 異常値判定を Python で行い、解釈だけ LLM に委任する分離が安定性の鍵

**想定外の課題：**
- プロンプトキャッシングを使わないと、スキーマが大きいとき API コストが跳ね上がる
- 前月データが存在しない月初はパイプラインのエッジケースが多い
- Slack のブロックレイアウトは LLM に生成させるより Python でハードコードした方が安定

### 8-2. コスト試算

参考として、このパイプラインの月次コスト概算を示します。

| 項目 | 使用量 | 単価 | 月次コスト |
|---|---|---|---|
| Claude API（Sonnet） | 約 50,000 tokens / 月 | $3/MTok (input) + $15/MTok (output) | 約 $0.5〜1.0 |
| BigQuery クエリ | 約 5 GB / 月 | $6.25/TB | $0.031 |
| Cloud Run Jobs | 月1回、30分 | 1 vCPU × $0.00002400/秒 | $0.04 |
| **合計** | | | **約 $1〜2 / 月** |

月次経営レポート1本あたり 100〜200円のコストで自動化できます。

### 8-3. 次のフロンティア：リアルタイム経営ダッシュボードへ

現在は月次バッチですが、以下の拡張で段階的にリアルタイム化できます。

1. **週次レポートへの拡張**：Cloud Scheduler の cron を `0 9 * * 1`（毎週月曜 9:00）に変更するだけで対応可能
2. **アラートトリガー型への転換**：BigQuery の Scheduled Query で異常値を検知 → Pub/Sub → Cloud Run で即時レポート生成
3. **承認フローとの統合**：Slack の Interactivity API を使い、レポートへの反応を次月の KPI 目標に自動フィードバック

## まとめ

本記事では、Claude Code の Agent Teams × BigQuery × Cloud Scheduler を使った月次経営ダッシュボード自動化パイプラインの設計と実装を紹介しました。

重要な設計原則を改めて整理します。

1. **SQL の実行は Python に任せ、LLM には生成のみ委任する**（dry_run 必須）
2. **異常値の検知はコードで、解釈だけを LLM に任せる**（ハルシネーション対策）
3. **エージェント間のコンテキストは最小限に保つ**（構造化 JSON で受け渡す）
4. **LLM の出力は必ず Pydantic で検証する**（本番安定性の担保）
5. **監査ログを BigQuery に残す**（「なぜこの数値か」を追跡可能にする）

「AIに経営を任せる」ではなく、「経営判断の品質を上げるためにAIを使う」という視点が、本番で安定して動くパイプラインを設計する上での根本的な思想です。

データはある、時間はない、という課題を抱えているチームに、本記事が参考になれば幸いです。

---

*本記事で紹介したコードはコンセプトを示すための簡略版です。本番導入時はエラーハンドリング・監査ログ・セキュリティレビューを必ず実施してください。*
