---
title: "GA4コンバージョン設定をPythonで自動化 — Admin API v1alpha/v1beta の使い分け"
emoji: "📊"
type: "tech"
topics: ["ga4", "python", "gcp", "googleanalytics", "automation"]
published: false
status: "draft"
publication_name: "correlate_dev"
---

## はじめに

GA4のコンバージョン設定は、ブラウザのUIから手動でポチポチするのが一般的です。しかし、複数のプロパティを管理していたり、CI/CDでGA4設定を自動化したかったりする場合、**Python で Admin API を使いたい**という場面が出てきます。

この記事では、GA4 Admin API を Python で操作する際の**最大の落とし穴**である「v1alpha と v1beta の使い分け」を解説します。403エラーで詰まっている方にも参考になるはずです。

---

## 結論から先に

GA4 Admin API には v1alpha と v1beta の2バージョンがあり、**機能によって使い分けが必要**です。

| 機能 | 使うバージョン |
|------|-------------|
| `eventCreateRules`（イベント作成ルール） | **v1alpha のみ** |
| `conversionEvents`（コンバージョン登録） | **v1beta** |
| プロパティ一覧・詳細 | v1beta |

`eventCreateRules` は v1beta に**存在しません**。v1beta でアクセスすると 404 が返ります。公式ドキュメントも分かりにくいので、この差異を知らずにハマる人が多いです。

---

## 認証の設定

### gcloud CLIのトークンでは403になる

最初に引っかかるポイントがこれです。

```bash
# これでは Analytics のスコープが含まれないので403になる
gcloud auth application-default login
```

`google.auth.default()` で取得したデフォルト認証情報は `analytics.edit` スコープを持っていないため、GA4 Admin API の書き込み系エンドポイントで403エラーが返ります。

### サービスアカウントで明示的にスコープ指定

```python
from google.oauth2 import service_account

def get_ga4_credentials(service_account_path: str):
    """GA4 Admin API 用の認証情報を取得する"""
    scopes = [
        "https://www.googleapis.com/auth/analytics.edit",
        "https://www.googleapis.com/auth/analytics.readonly",
    ]
    credentials = service_account.Credentials.from_service_account_file(
        service_account_path,
        scopes=scopes,
    )
    return credentials
```

サービスアカウントには事前に GA4 プロパティの「編集者」権限を付与しておく必要があります（Google Analytics の管理画面 > アクセス管理）。

---

## 必要なライブラリのインストール

```bash
pip install google-analytics-admin google-auth
```

`google-analytics-admin` が GA4 Admin API のクライアントライブラリです。内部的に v1alpha と v1beta の両方を扱えます。

---

## eventCreateRules の操作（v1alpha）

### クライアントの初期化

```python
from google.analytics.admin import AnalyticsAdminServiceClient
from google.analytics.admin_v1alpha import types as alpha_types

def get_alpha_client(credentials) -> AnalyticsAdminServiceClient:
    """v1alpha クライアントを返す"""
    return AnalyticsAdminServiceClient(credentials=credentials)
```

### イベント作成ルールの一覧取得

```python
def list_event_create_rules(
    client: AnalyticsAdminServiceClient,
    property_id: str,
    data_stream_id: str,
) -> list:
    """
    イベント作成ルールを一覧取得する（v1alpha のみ対応）

    property_id: "properties/123456789"
    data_stream_id: "dataStreams/987654321"
    """
    parent = f"{property_id}/{data_stream_id}"
    rules = client.list_event_create_rules(parent=parent)
    return list(rules)


# 使用例
credentials = get_ga4_credentials("/path/to/service-account.json")
client = get_alpha_client(credentials)

rules = list_event_create_rules(
    client=client,
    property_id="properties/123456789",
    data_stream_id="dataStreams/987654321",
)
for rule in rules:
    print(rule.name, rule.destination_event)
```

### イベント作成ルールの追加

ここで注意が必要なのが **フィールド名** です。

「受信イベント名」のフィールドは `destination_event` であり、`event_name` ではありません。公式ドキュメントのサンプルコードが古かったり、フィールド名が混在していたりするので注意してください。

```python
from google.analytics.admin_v1alpha import types as alpha_types

def create_event_create_rule(
    client: AnalyticsAdminServiceClient,
    property_id: str,
    data_stream_id: str,
    destination_event: str,  # 作成後のイベント名（注：event_name ではない）
    source_event: str,       # トリガーとなる元のイベント名
) -> alpha_types.EventCreateRule:
    """
    イベント作成ルールを追加する

    例: page_view を tutorial_complete に変換するルール
    destination_event = "tutorial_complete"
    source_event = "page_view"
    """
    parent = f"{property_id}/{data_stream_id}"

    rule = alpha_types.EventCreateRule(
        destination_event=destination_event,  # ← ここは event_name ではない
        event_conditions=[
            alpha_types.MatchingCondition(
                field="event_name",
                comparison_type=alpha_types.MatchingCondition.ComparisonType.EQUALS,
                value=source_event,
            )
        ],
    )

    created_rule = client.create_event_create_rule(
        parent=parent,
        event_create_rule=rule,
    )
    return created_rule
```

---

## conversionEvents の操作（v1beta）

### v1beta クライアントの初期化

`conversionEvents` は v1beta で操作します。クライアントのインポートパスが v1alpha と異なります。

```python
# v1alpha
from google.analytics.admin import AnalyticsAdminServiceClient
from google.analytics.admin_v1alpha import types as alpha_types

# v1beta
from google.analytics.admin_v1beta import AnalyticsAdminServiceClient as BetaClient
from google.analytics.admin_v1beta import types as beta_types

def get_beta_client(credentials) -> BetaClient:
    """v1beta クライアントを返す"""
    return BetaClient(credentials=credentials)
```

### コンバージョンイベントの登録

```python
def create_conversion_event(
    client: BetaClient,
    property_id: str,
    event_name: str,
) -> beta_types.ConversionEvent:
    """
    コンバージョンイベントを登録する（v1beta）

    event_name: コンバージョンとして登録するイベント名
    例: "purchase", "sign_up", "tutorial_complete"
    """
    conversion_event = beta_types.ConversionEvent(
        event_name=event_name,
    )

    created = client.create_conversion_event(
        parent=property_id,
        conversion_event=conversion_event,
    )
    return created


# 使用例
beta_client = get_beta_client(credentials)

event = create_conversion_event(
    client=beta_client,
    property_id="properties/123456789",
    event_name="tutorial_complete",
)
print(f"コンバージョン登録完了: {event.name}")
```

### コンバージョンイベントの一覧確認

```python
def list_conversion_events(
    client: BetaClient,
    property_id: str,
) -> list[beta_types.ConversionEvent]:
    """登録済みコンバージョンイベントを一覧取得"""
    return list(client.list_conversion_events(parent=property_id))
```

---

## Realtime API でのイベント確認

イベント作成ルールとコンバージョンを設定した後、Realtime API で動作確認することがあります。ここにも落とし穴があります。

**`start_minutes_ago` の最大値は 29 です。30 以上を指定すると 400 エラーになります。**

```python
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import RunRealtimeReportRequest

def get_realtime_active_users(
    property_id: str,
    credentials,
    minutes: int = 5,
) -> int:
    """リアルタイムのアクティブユーザー数を取得"""
    if minutes > 29:
        raise ValueError(f"start_minutes_ago の最大値は 29 です（指定値: {minutes}）")

    client = BetaAnalyticsDataClient(credentials=credentials)

    request = RunRealtimeReportRequest(
        property=property_id,
        dimensions=[{"name": "minutesAgo"}],
        metrics=[{"name": "activeUsers"}],
        minute_ranges=[{
            "name": "last_5_min",
            "start_minutes_ago": minutes,
            "end_minutes_ago": 0,
        }],
    )

    response = client.run_realtime_report(request=request)
    total = sum(int(row.metric_values[0].value) for row in response.rows)
    return total
```

---

## エラー対処まとめ

実装中によく遭遇するエラーとその原因を整理します。

| エラー | 原因 | 解決策 |
|--------|------|--------|
| 403 Permission Denied | gcloud デフォルト認証の使用 | SA + `analytics.edit` スコープ指定 |
| 404 Not Found（eventCreateRules） | v1beta を使っている | v1alpha に切り替える |
| 400 Bad Request（Realtime API） | `start_minutes_ago >= 30` | 29以下に変更する |
| フィールド名エラー | `event_name` と `destination_event` の混同 | `destination_event` を使う |

---

## CI/CDでの活用例

複数サイトのGA4プロパティを管理している場合、コンバージョン設定をコードで管理できると便利です。

```yaml
# ga4-config.yaml
properties:
  - id: "properties/111111111"
    name: "サイトA"
    conversions:
      - "purchase"
      - "sign_up"
    event_rules:
      - destination: "sign_up"
        source: "page_view"
        url_filter: "/register/complete"

  - id: "properties/222222222"
    name: "サイトB"
    conversions:
      - "contact_form_submit"
```

```python
import yaml
from pathlib import Path

def apply_ga4_config(config_path: str, credentials):
    """YAMLベースのGA4設定を適用する"""
    config = yaml.safe_load(Path(config_path).read_text())

    alpha_client = get_alpha_client(credentials)
    beta_client = get_beta_client(credentials)

    for prop in config["properties"]:
        property_id = prop["id"]
        print(f"設定適用中: {prop['name']} ({property_id})")

        # コンバージョン登録
        for event_name in prop.get("conversions", []):
            try:
                create_conversion_event(beta_client, property_id, event_name)
                print(f"  コンバージョン登録: {event_name}")
            except Exception as e:
                print(f"  コンバージョン登録スキップ（既存の可能性）: {event_name} - {e}")
```

---

## まとめ

GA4 Admin API を Python で扱う際の重要ポイントを整理します：

1. **v1alpha と v1beta を使い分ける** — `eventCreateRules` は v1alpha のみ、`conversionEvents` は v1beta
2. **認証は SA + `analytics.edit` スコープ** — gcloud デフォルト認証では403になる
3. **フィールド名は `destination_event`** — `event_name` ではない
4. **Realtime API の `start_minutes_ago` は最大29** — 30以上は400エラー

ブラウザUIでの手動設定からコード管理に移行することで、複数プロパティの一括管理や CI/CD での設定管理が可能になります。設定のバージョン管理もできるので、「いつ誰が何を変えたか」の追跡も容易になります。
