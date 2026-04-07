---
title: "Google Ads API v23でMAXIMIZE_CLICKSが使えない問題とTARGET_SPENDへの移行"
emoji: "🔧"
type: "tech"
topics: ["googleads", "python", "adgrants", "automation"]
published: false
status: "draft"
publication_name: "correlate_dev"
---

## はじめに

Google Ads API v23 の Python SDK で「クリック数の最大化」戦略を設定しようとしたら、AttributeError で落ちました。

```
AttributeError: Assignment not allowed to field "maximize_clicks"
in protocol message object.
```

「え、MAXIMIZE_CLICKS って使えないの？」と焦りましたが、答えは「使えないわけではなく、v23 では API の設計が変わっている」でした。

この記事では、v23 における入札戦略の正しい設定方法と、Ad Grants 運用における戦略選択の考え方を整理します。

---

## v23 で何が変わったか

### maximize_clicks が直接設定できない

v17 以前の SDK では以下のように書けていました：

```python
# 旧：v17以前
campaign.maximize_clicks.target_cpa_micros = 0
```

しかし v23 では Campaign オブジェクトに `maximize_clicks` というフィールドが**直接存在しない**ため、AttributeError になります。

### v23 での正しい書き方：target_spend を使う

v23 では「クリック数の最大化」を `TargetSpend` ビッディング戦略で表現します。

```python
from google.ads.googleads.client import GoogleAdsClient

def set_maximize_clicks_v23(client, customer_id, campaign_id):
    """v23でクリック数の最大化（TargetSpend）を設定する。"""
    campaign_service = client.get_service("CampaignService")
    campaign_operation = client.get_type("CampaignOperation")

    campaign = campaign_operation.update
    campaign.resource_name = campaign_service.campaign_path(
        customer_id, campaign_id
    )

    # TargetSpend を直接属性代入で設定（CopyFrom/MergeFromは使えない）
    campaign.target_spend.cpc_bid_ceiling_micros = 2_000_000  # $2.00（Ad Grants上限）
    # target_spend_micros を省略すると「予算上限まで使い切る」設定になる

    # FieldMaskはサブフィールドまで指定する
    client.copy_from(
        campaign_operation.update_mask,
        protobuf_helpers.field_mask(None, campaign._pb)
    )

    response = campaign_service.mutate_campaigns(
        customer_id=customer_id,
        operations=[campaign_operation]
    )
    print(f"[INFO] 入札戦略を更新しました: {response.results[0].resource_name}")
```

---

## ハマりポイント3選

### ハマり1：proto-plus 型に CopyFrom / MergeFrom が使えない

`TargetSpend` は proto-plus 型のため、`CopyFrom()` や `MergeFrom()` が使えません。

```python
# NG：proto-plus型にはCopyFromが使えない
target_spend = client.get_type("TargetSpend")
target_spend.cpc_bid_ceiling_micros = 2_000_000
campaign.target_spend.CopyFrom(target_spend)  # AttributeError！

# OK：直接属性代入
campaign.target_spend.cpc_bid_ceiling_micros = 2_000_000
```

proto-plus 型の場合、サブオブジェクトに直接代入するのが正しい方法です。

### ハマり2：FieldMask でネスト型を上位フィールドで指定するとエラー

FieldMask の指定が甘いと以下のエラーが出ます：

```
google.ads.googleads.errors.GoogleAdsException:
  - FIELD_HAS_SUBFIELDS
    FieldPath: target_spend
```

`target_spend` というフィールド全体を指定するのではなく、変更するサブフィールドまで具体的に指定する必要があります。

```python
# NG：上位フィールドだけでは FIELD_HAS_SUBFIELDS エラー
field_mask.paths.append("target_spend")

# OK：サブフィールドまで指定する
field_mask.paths.append("target_spend.cpc_bid_ceiling_micros")
# 複数サブフィールドを変更する場合はそれぞれ追加
# field_mask.paths.append("target_spend.target_spend_micros")
```

実装では `protobuf_helpers.field_mask()` を使うと自動で正しいパスを生成してくれます。

```python
from google.api_core import protobuf_helpers

# _pbにアクセスしてprotobufオブジェクトを取得してからfield_maskを生成
update_mask = protobuf_helpers.field_mask(None, campaign._pb)
client.copy_from(campaign_operation.update_mask, update_mask)
```

### ハマり3：MAXIMIZE_CONVERSIONS に切り替えたら成果がゼロになった

Ad Grants を開始してすぐに「コンバージョン数の最大化」に設定したところ、インプレッションが激減しました。

原因は単純で、**コンバージョンデータが蓄積されていない状態では MAXIMIZE_CONVERSIONS が機能しない**ためです。Google の機械学習が「何がコンバージョンかわからない」状態で最適化しようとして、結果的に何も配信しなくなります。

```
Ad Grants 開始直後の推奨順序：

1. TARGET_SPEND（クリック数の最大化）
   → コンバージョンデータを蓄積する（目安：30件以上）

2. MAXIMIZE_CONVERSIONS
   → データが蓄積されてから切り替える
```

---

## 完全な実装例

```python
import sys
from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.errors import GoogleAdsException
from google.api_core import protobuf_helpers


def get_campaign(client, customer_id, campaign_id):
    """GAQLでキャンペーン情報を取得する。"""
    ga_service = client.get_service("GoogleAdsService")
    query = f"""
        SELECT
            campaign.id,
            campaign.name,
            campaign.bidding_strategy_type,
            campaign.target_spend.cpc_bid_ceiling_micros,
            campaign.target_spend.target_spend_micros
        FROM campaign
        WHERE campaign.id = {campaign_id}
    """
    response = ga_service.search(customer_id=customer_id, query=query)
    rows = list(response)
    if not rows:
        raise ValueError(f"キャンペーンが見つかりません: {campaign_id}")
    return rows[0].campaign


def set_target_spend(client, customer_id, campaign_id, cpc_bid_ceiling_micros=None):
    """
    TARGET_SPEND（クリック数の最大化）を設定する。

    Args:
        cpc_bid_ceiling_micros: CPC上限（マイクロ単位）。Noneで上限なし。
                                Ad Grantsの場合は 2_000_000（$2.00）を推奨。
    """
    campaign_service = client.get_service("CampaignService")
    campaign_operation = client.get_type("CampaignOperation")

    campaign = campaign_operation.update
    campaign.resource_name = campaign_service.campaign_path(
        customer_id, campaign_id
    )

    # TargetSpend設定（直接属性代入）
    if cpc_bid_ceiling_micros is not None:
        campaign.target_spend.cpc_bid_ceiling_micros = cpc_bid_ceiling_micros

    # FieldMask自動生成
    update_mask = protobuf_helpers.field_mask(None, campaign._pb)
    client.copy_from(campaign_operation.update_mask, update_mask)

    try:
        response = campaign_service.mutate_campaigns(
            customer_id=customer_id,
            operations=[campaign_operation]
        )
        resource_name = response.results[0].resource_name
        print(f"[SUCCESS] TARGET_SPENDを設定しました: {resource_name}")
        return resource_name

    except GoogleAdsException as ex:
        for error in ex.failure.errors:
            print(
                f"[ERROR] {error.error_code}: {error.message}",
                file=sys.stderr
            )
        raise


def check_bidding_strategy(client, customer_id, campaign_id):
    """現在の入札戦略を確認する。"""
    campaign = get_campaign(client, customer_id, campaign_id)

    strategy_type = campaign.bidding_strategy_type.name
    print(f"現在の入札戦略: {strategy_type}")

    if strategy_type == "TARGET_SPEND":
        ceiling = campaign.target_spend.cpc_bid_ceiling_micros
        budget = campaign.target_spend.target_spend_micros
        ceiling_str = f"${ceiling / 1_000_000:.2f}" if ceiling else "上限なし"
        budget_str = f"${budget / 1_000_000:.2f}" if budget else "予算上限まで"
        print(f"  CPC上限: {ceiling_str}")
        print(f"  支出目標: {budget_str}")

    return strategy_type


def main():
    client = GoogleAdsClient.load_from_storage("google-ads.yaml")
    customer_id = "YOUR_CUSTOMER_ID"
    campaign_id = "YOUR_CAMPAIGN_ID"

    # 現在の戦略を確認
    current_strategy = check_bidding_strategy(client, customer_id, campaign_id)

    # TARGET_SPENDに設定（Ad Grants: $2.00上限）
    set_target_spend(
        client,
        customer_id,
        campaign_id,
        cpc_bid_ceiling_micros=2_000_000  # Ad Grants制約: $2.00
    )


if __name__ == "__main__":
    main()
```

---

## Ad Grants における入札戦略の考え方

Ad Grants には独自の制約があります：

| 制約 | 内容 |
|------|------|
| 最大 CPC | $2.00（TARGET_SPENDで上限設定） |
| CTR 要件 | アカウント全体で5%以上を維持 |
| コンバージョン追跡 | MAXIMIZE_CONVERSIONS 使用に必須 |
| 月次予算 | $10,000（実際の消化は大幅に下回ることも多い） |

### 運用フェーズ別の推奨戦略

**フェーズ1：開始直後（コンバージョンデータ0件）**

```python
# TARGET_SPEND + $2.00 CPC上限
set_target_spend(client, customer_id, campaign_id, cpc_bid_ceiling_micros=2_000_000)
```

この段階ではクリックを集めてコンバージョンデータを蓄積することが最優先です。

**フェーズ2：コンバージョンデータ蓄積後（30件以上）**

MAXIMIZE_CONVERSIONS への切り替えを検討します。ただし Ad Grants では $2.00 CPC 制約があるため、コンバージョン単価目標（tCPA）を設定しないと制約に引っかかる場合があります。

```python
# MAXIMIZE_CONVERSIONSへの切り替えはv23 SDKで別途実装が必要
# target_cpa_micros を設定する場合は MaximizeConversions を使う
```

---

## GAQL で入札戦略を確認する

現在のキャンペーン設定を確認するための GAQL クエリです：

```sql
SELECT
    campaign.id,
    campaign.name,
    campaign.bidding_strategy_type,
    campaign.target_spend.cpc_bid_ceiling_micros,
    campaign.target_spend.target_spend_micros,
    campaign.maximize_conversions.target_cpa_micros
FROM campaign
WHERE campaign.status = 'ENABLED'
ORDER BY campaign.id
```

これを実行すると各キャンペーンの入札戦略と設定値を一覧で確認できます。

---

## まとめ

| 問題 | 原因 | 解決策 |
|------|------|--------|
| `AttributeError: maximize_clicks` | v23で直接設定不可 | `target_spend` フィールドを使う |
| `CopyFrom()` が使えない | proto-plus 型 | 直接属性代入 |
| `FIELD_HAS_SUBFIELDS` エラー | FieldMask の指定が粗い | サブフィールドまで指定 |
| MAXIMIZE_CONVERSIONS で配信ゼロ | データ不足 | まず TARGET_SPEND でデータ蓄積 |

Google Ads API はバージョンごとに SDK の設計が変わります。エラーメッセージをよく読み、proto-plus 型の扱いに慣れることで、多くのハマりどころを乗り越えられます。

---

## 関連記事

- [Google Ads RSA を Claude + Python で自動生成・自動更新する仕組みを作った](./google-ads-rsa-claude-python-automation)
- [Google Ads API で広告が消えた15秒間 — REMOVE+CREATE競合エラーのリカバリ手順](./google-ads-api-rsa-remove-create-race-condition)
