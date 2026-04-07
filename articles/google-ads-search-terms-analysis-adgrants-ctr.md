---
title: "Ad Grants CTR改善のための検索クエリ分析パイプライン自動化"
emoji: "📊"
type: "tech"
topics: ["googleads", "adgrants", "python", "bigquery"]
published: false
status: "draft"
publication_name: "correlate_dev"
---

## はじめに

Ad Grants には「アカウント全体の CTR を5%以上に維持する」という要件があります。この要件を満たせないとアカウントが停止されます。

CTR を下げる主な原因の一つが、**関係のない検索クエリでの表示**です。「キャリア診断」を狙ったつもりが、「美容師 大変」「介護士 辞めたい」という検索にも広告が表示されてしまう——こういったケースに対処するための除外キーワード管理が重要です。

この記事では、Google Ads の `search_term_view` から実際の検索語句を取得し、スコアリングで除外候補を自動発掘する Python パイプラインを解説します。

---

## search_term_view とは

`search_term_view` は実際にユーザーが Google で入力した検索語句と、その結果を記録した Google Ads のリソースです。

GAQL で以下のように取得できます：

```sql
SELECT
    search_term_view.search_term,
    search_term_view.status,
    metrics.impressions,
    metrics.clicks,
    metrics.ctr,
    metrics.conversions,
    campaign.name,
    ad_group.name
FROM search_term_view
WHERE segments.date DURING LAST_30_DAYS
  AND metrics.impressions > 0
ORDER BY metrics.impressions DESC
```

### status の3値

`search_term_view.status` は3つの値を取ります：

| ステータス | 意味 |
|-----------|------|
| `ADDED` | 登録済みキーワードとして追加済み |
| `EXCLUDED` | 除外キーワードとして設定済み |
| `NONE` | どちらでもない（未分類） |

除外候補を分析する際、`EXCLUDED` はすでに除外済みなので対象外とします。

---

## パイプラインの全体設計

```
search_term_view取得（GAQL）
        │
        ▼
スコアリング（imp多・CTR低・CV0・疑わしいパターン）
        │
        ▼
除外候補リスト生成（提案のみ、自動適用しない）
        │
        ├──→ BigQuery に保存（週次履歴）
        │
        └──→ Discord 通知（週次レポート）
```

重要な設計判断として、**除外候補は「提案のみ」** とし、自動適用はしません。誤除外によるトラフィック損失のリスクが高いためです。

---

## 実装

### Step 1：search_term_view の取得

```python
from dataclasses import dataclass
from google.ads.googleads.client import GoogleAdsClient


@dataclass
class SearchTermRecord:
    """検索クエリの記録。"""
    search_term: str
    status: str          # ADDED / EXCLUDED / NONE
    impressions: int
    clicks: int
    ctr: float
    conversions: float
    campaign_name: str
    ad_group_name: str


def fetch_search_terms(
    client: GoogleAdsClient,
    customer_id: str,
    days: int = 30,
) -> list[SearchTermRecord]:
    """
    search_term_viewから検索語句データを取得する。

    Args:
        days: 過去何日分のデータを取得するか（デフォルト30日）
    """
    ga_service = client.get_service("GoogleAdsService")

    query = f"""
        SELECT
            search_term_view.search_term,
            search_term_view.status,
            metrics.impressions,
            metrics.clicks,
            metrics.ctr,
            metrics.conversions,
            campaign.name,
            ad_group.name
        FROM search_term_view
        WHERE segments.date DURING LAST_{days}_DAYS
          AND metrics.impressions > 0
          AND search_term_view.status != 'EXCLUDED'
        ORDER BY metrics.impressions DESC
        LIMIT 5000
    """

    response = ga_service.search(customer_id=customer_id, query=query)

    records = []
    for row in response:
        records.append(SearchTermRecord(
            search_term=row.search_term_view.search_term,
            status=row.search_term_view.status.name,
            impressions=row.metrics.impressions,
            clicks=row.metrics.clicks,
            ctr=row.metrics.ctr,
            conversions=row.metrics.conversions,
            campaign_name=row.campaign.name,
            ad_group_name=row.ad_group.name,
        ))

    print(f"[INFO] {len(records)} 件の検索クエリを取得しました。")
    return records
```

### Step 2：スコアリングロジック

除外候補のスコアリングは「インプレッションが多い割に CTR・CV が低い」語句を優先的に検出します。

```python
from dataclasses import dataclass, field


# Ad Grants のCTR基準値
ADGRANTS_CTR_THRESHOLD = 0.05  # 5%

# スコアリングの重み
SCORE_HIGH_IMP_LOW_CTR = 3     # インプレッション多・CTR低
SCORE_ZERO_CV          = 2     # コンバージョンゼロ
SCORE_SUSPICIOUS       = 5     # 疑わしいパターン

# 疑わしいキーワードパターン（除外優先）
SUSPICIOUS_PATTERNS = [
    "無料",       # 「無料で〇〇したい」系（費用対効果低）
    "比較",       # 競合比較検索
    "口コミ",     # レビュー検索
    "2ch", "5ch", # 掲示板検索
    "twitter", "インスタ",  # SNS検索
    "副業詐欺", "怪しい",   # ネガティブワード
]

# インプレッション閾値（これ以上で「多い」と判定）
HIGH_IMPRESSION_THRESHOLD = 50


@dataclass
class ScoredSearchTerm:
    """スコアリング結果付きの検索クエリ。"""
    record: SearchTermRecord
    score: int = 0
    reasons: list[str] = field(default_factory=list)

    @property
    def is_exclude_candidate(self) -> bool:
        return self.score >= 3  # スコア3以上を除外候補とする


def score_search_term(record: SearchTermRecord) -> ScoredSearchTerm:
    """検索クエリをスコアリングして除外候補を判定する。"""
    scored = ScoredSearchTerm(record=record)

    # ルール1: インプレッション多 + CTR低
    if record.impressions >= HIGH_IMPRESSION_THRESHOLD and record.ctr < ADGRANTS_CTR_THRESHOLD:
        scored.score += SCORE_HIGH_IMP_LOW_CTR
        scored.reasons.append(
            f"imp={record.impressions}, CTR={record.ctr:.1%}（閾値{ADGRANTS_CTR_THRESHOLD:.0%}未満）"
        )

    # ルール2: クリックありでコンバージョンゼロ（長期間）
    if record.clicks >= 10 and record.conversions == 0:
        scored.score += SCORE_ZERO_CV
        scored.reasons.append(f"clicks={record.clicks}, CV=0")

    # ルール3: 疑わしいパターン
    term_lower = record.search_term.lower()
    for pattern in SUSPICIOUS_PATTERNS:
        if pattern in term_lower:
            scored.score += SCORE_SUSPICIOUS
            scored.reasons.append(f"疑わしいパターン: '{pattern}'")
            break  # 1つマッチしたら終了

    return scored


def find_exclude_candidates(
    records: list[SearchTermRecord],
) -> list[ScoredSearchTerm]:
    """除外候補リストを生成する（スコア降順）。"""
    scored_list = [score_search_term(r) for r in records]
    candidates = [s for s in scored_list if s.is_exclude_candidate]
    candidates.sort(key=lambda s: s.score, reverse=True)

    print(f"[INFO] 除外候補: {len(candidates)} 件")
    return candidates
```

### Step 3：レポート生成

```python
from datetime import datetime


def generate_report(
    candidates: list[ScoredSearchTerm],
    top_n: int = 20,
) -> str:
    """除外候補レポートを文字列で生成する。"""
    now = datetime.now().strftime("%Y-%m-%d")
    lines = [
        f"# 除外キーワード候補レポート（{now}）",
        f"",
        f"除外候補数: {len(candidates)} 件（上位{top_n}件を表示）",
        f"",
        f"| 検索クエリ | インプレッション | CTR | CV | スコア | 理由 |",
        f"|-----------|----------------|-----|-----|--------|------|",
    ]

    for s in candidates[:top_n]:
        r = s.record
        ctr_str = f"{r.ctr:.1%}"
        cv_str = f"{r.conversions:.0f}"
        reasons_str = " / ".join(s.reasons)

        lines.append(
            f"| {r.search_term} | {r.impressions:,} | {ctr_str} | {cv_str} | {s.score} | {reasons_str} |"
        )

    return "\n".join(lines)


def print_report(candidates: list[ScoredSearchTerm], top_n: int = 20):
    """コンソールにレポートを出力する。"""
    report = generate_report(candidates, top_n)
    print(report)
```

### Step 4：除外キーワードを適用する（--execute フラグ）

提案した除外候補をユーザーが確認後、`--execute` フラグで実際に適用します。

```python
def apply_negative_keywords(
    client: GoogleAdsClient,
    customer_id: str,
    campaign_id: str,
    keywords: list[str],
) -> dict:
    """
    除外キーワードをキャンペーンレベルで適用する。
    partial_failure=True で1件エラーでも残りを処理する。

    Returns:
        {"success": int, "failed": int}
    """
    criterion_service = client.get_service("CampaignCriterionService")
    campaign_service = client.get_service("CampaignService")

    campaign_resource_name = campaign_service.campaign_path(customer_id, campaign_id)

    operations = []
    for keyword_text in keywords:
        operation = client.get_type("CampaignCriterionOperation")
        criterion = operation.create
        criterion.campaign = campaign_resource_name
        criterion.negative = True  # 除外キーワード
        criterion.keyword.text = keyword_text
        criterion.keyword.match_type = client.enums.KeywordMatchTypeEnum.BROAD

        operations.append(operation)

    if not operations:
        print("[INFO] 適用するキーワードがありません。")
        return {"success": 0, "failed": 0}

    try:
        response = criterion_service.mutate_campaign_criteria(
            customer_id=customer_id,
            operations=operations,
            partial_failure=True,  # request dict 形式が必要な場合は下のコメントを参照
        )

        # partial_failureエラーの確認
        failed_count = 0
        if response.partial_failure_error:
            # エラー処理（省略）
            pass

        success_count = len(response.results) - failed_count
        print(f"[SUCCESS] 除外KW適用: {success_count}件成功 / {failed_count}件失敗")
        return {"success": success_count, "failed": failed_count}

    except Exception as ex:
        print(f"[ERROR] {ex}", file=sys.stderr)
        raise


# partial_failure を request dict 形式で渡す場合（SDK バージョンによる）
# response = criterion_service.mutate_campaign_criteria(
#     request={
#         "customer_id": customer_id,
#         "operations": operations,
#         "partial_failure": True,
#     }
# )
```

---

## BigQuery への保存設計

週次の定点観測のために、分析結果を BigQuery に保存します。

```python
from google.cloud import bigquery
from datetime import date


BQ_PROJECT = "your-project-id"
BQ_DATASET = "google_ads"
BQ_TABLE = "search_term_analysis"

BQ_SCHEMA = [
    bigquery.SchemaField("analysis_date", "DATE"),
    bigquery.SchemaField("search_term", "STRING"),
    bigquery.SchemaField("status", "STRING"),
    bigquery.SchemaField("impressions", "INTEGER"),
    bigquery.SchemaField("clicks", "INTEGER"),
    bigquery.SchemaField("ctr", "FLOAT"),
    bigquery.SchemaField("conversions", "FLOAT"),
    bigquery.SchemaField("exclude_score", "INTEGER"),
    bigquery.SchemaField("exclude_reasons", "STRING"),
    bigquery.SchemaField("campaign_name", "STRING"),
    bigquery.SchemaField("ad_group_name", "STRING"),
]


def save_to_bigquery(
    candidates: list[ScoredSearchTerm],
    project: str = BQ_PROJECT,
    dataset: str = BQ_DATASET,
    table: str = BQ_TABLE,
):
    """除外候補をBigQueryに保存する。"""
    bq_client = bigquery.Client(project=project)
    table_ref = f"{project}.{dataset}.{table}"

    today = date.today().isoformat()
    rows = []
    for s in candidates:
        r = s.record
        rows.append({
            "analysis_date": today,
            "search_term": r.search_term,
            "status": r.status,
            "impressions": r.impressions,
            "clicks": r.clicks,
            "ctr": r.ctr,
            "conversions": r.conversions,
            "exclude_score": s.score,
            "exclude_reasons": " / ".join(s.reasons),
            "campaign_name": r.campaign_name,
            "ad_group_name": r.ad_group_name,
        })

    if not rows:
        print("[INFO] 保存するデータがありません。")
        return

    errors = bq_client.insert_rows_json(table_ref, rows)
    if errors:
        print(f"[ERROR] BQ保存エラー: {errors}")
    else:
        print(f"[SUCCESS] {len(rows)} 件を BigQuery に保存しました。")
```

---

## メイン実行スクリプト

```python
import argparse
import sys
from google.ads.googleads.client import GoogleAdsClient


def parse_args():
    parser = argparse.ArgumentParser(description="Ad Grants 検索クエリ分析パイプライン")
    parser.add_argument("--customer-id", required=True)
    parser.add_argument("--campaign-id", help="除外適用対象のキャンペーンID")
    parser.add_argument("--days", type=int, default=30, help="分析期間（デフォルト30日）")
    parser.add_argument("--top-n", type=int, default=20, help="表示する除外候補数")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="除外キーワードを実際に適用する（--campaign-id 必須）"
    )
    parser.add_argument(
        "--save-bq",
        action="store_true",
        help="結果をBigQueryに保存する"
    )
    return parser.parse_args()


def main():
    args = parse_args()

    if args.execute and not args.campaign_id:
        print("[ERROR] --execute には --campaign-id が必要です。", file=sys.stderr)
        sys.exit(1)

    client = GoogleAdsClient.load_from_storage("google-ads.yaml")

    # 検索クエリ取得
    records = fetch_search_terms(client, args.customer_id, days=args.days)

    # スコアリング
    candidates = find_exclude_candidates(records)

    # レポート表示
    print_report(candidates, top_n=args.top_n)

    # BigQuery保存
    if args.save_bq:
        save_to_bigquery(candidates)

    # 除外適用（--executeフラグ時のみ）
    if args.execute:
        keywords_to_exclude = [s.record.search_term for s in candidates[:args.top_n]]
        print(f"\n以下の {len(keywords_to_exclude)} 件を除外キーワードとして適用します：")
        for kw in keywords_to_exclude[:5]:
            print(f"  - {kw}")
        if len(keywords_to_exclude) > 5:
            print(f"  ... 他{len(keywords_to_exclude) - 5}件")

        answer = input("\n実行しますか？ (yes/no): ")
        if answer.lower() == "yes":
            apply_negative_keywords(
                client, args.customer_id, args.campaign_id, keywords_to_exclude
            )
        else:
            print("キャンセルしました。")


if __name__ == "__main__":
    main()
```

---

## 実行例

```bash
# 分析のみ（確認用）
python analyze_search_terms.py \
  --customer-id 1234567890 \
  --days 30 \
  --top-n 30

# BigQuery保存付き
python analyze_search_terms.py \
  --customer-id 1234567890 \
  --save-bq

# 除外適用まで実行
python analyze_search_terms.py \
  --customer-id 1234567890 \
  --campaign-id 9876543210 \
  --execute
```

---

## Ad Grants 固有の注意点

### CTR 5%ルールとの関係

除外キーワードの適用は CTR 改善に直結します。ただし、適用しすぎるとインプレッション数が激減して予算消化率が下がります。

バランスの目安：

- **優先除外**：インプレッション100以上 + CTR 2%未満 + CV0
- **要観察**：インプレッション30〜100 + CTR 5%未満
- **様子見**：インプレッション30未満（データ不足）

### 完全一致 vs ブロードマッチ

除外キーワードにはブロードマッチ（部分一致）を使うのが基本です。「介護士 辞めたい」をブロードマッチで除外すると、「介護士 辞めたい 理由」「辞めたい 介護士」なども除外されます。

ただし、除外範囲が広くなりすぎる場合はフレーズ一致または完全一致を使います。

---

## まとめ

| コンポーネント | 役割 |
|-------------|------|
| `search_term_view` GAQL | 実際の検索語句を取得 |
| スコアリング | imp多・CTR低・CV0・疑わしいパターンで除外候補を判定 |
| `--execute` フラグ | ユーザー確認後に除外を適用 |
| `partial_failure=True` | ポリシー違反1件で全件失敗しない |
| BigQuery 保存 | 週次履歴で定点観測 |

Ad Grants の CTR 5%ルールは「守らなければならない制約」ですが、検索クエリ分析を自動化することで「どのクエリが CTR を下げているか」を素早く発見できます。除外キーワードの管理は地道な作業ですが、API を活用することで週次の定点観測を習慣化できます。

---

## 関連記事

- [Ad Grantsの$2制限を逆手に取る — ロングテール一括登録戦略と実装](./google-ads-longtail-adgrants-nonprofit)
- [Google Ads RSAをClaude + Pythonで自動生成する仕組みを作った](./google-ads-rsa-claude-python-automation)
