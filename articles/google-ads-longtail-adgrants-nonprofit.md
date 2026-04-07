---
title: "Ad Grantsの$2制限を逆手に取る — ロングテール一括登録戦略と実装"
emoji: "🎯"
type: "tech"
topics: ["googleads", "adgrants", "python", "automation"]
published: false
status: "draft"
publication_name: "correlate_dev"
---

## はじめに

Ad Grants（非営利団体向け Google 広告）には、最大 CPC が **$2.00** という制限があります。

この制限のせいで、競合の多い「転職」「キャリア相談」といった一般的なキーワードでは入札競争に勝てません。「ではどうするか」——その答えが ** ロングテール一括登録戦略 ** です。

ISVD（一般社団法人社会構想デザイン機構）の Ad Grants 運用で実装した、1,000件超のロングテールキーワードを Google Ads API で一括登録する手法を解説します。

---

## 戦略の考え方

### なぜロングテールが有効か

Ad Grants の $2.00 CPC 制限では、検索ボリュームの大きいキーワード（HIGH・MEDIUM 競合）では上位表示が難しい状況です。

しかし、 ** 超ロングテールキーワード ** （月間検索数 10未満）には競合がほとんどいないため、$2.00 でも十分に入札できます。

さらに重要な点として：

- ** インプレッション0のキーワードはCTR計算に含まれない **
- Ad Grants の CTR 5%維持ルールを守りながら、大量のキーワードを「網」として張れる
- 実際に検索されたときだけ、高 CTR で表示できる

ロングテール戦略は「広く薄く張る網」ではなく、「実際に検索されるニッチな語句でのピンポイント高品質表示」を目指すものです。

### 組み合わせ生成の発想

ロングテールキーワードを手動で考えるのは非効率です。そこで「組み合わせ生成」を使います。

```
職種 × 向き不向き述語 × 修飾語 = ロングテールキーワード

例：
「公務員」×「向いていない」×「特徴」
→「公務員 向いていない 特徴」

「コンサルタント」×「辛い」×「理由」
→「コンサルタント 辛い 理由」
```

この組み合わせを Python で生成すると、数百〜数千件のキーワードを一気に作成できます。

---

## 実装

### Step 1：キーワード組み合わせ生成

```python
from itertools import product
from typing import Generator


# 職種リスト（抜粋）
OCCUPATIONS = [
    "公務員", "教師", "看護師", "エンジニア", "コンサルタント",
    "営業", "事務", "医師", "弁護士", "保育士",
    "介護士", "警察官", "自衛隊", "銀行員", "税理士",
]

# 向き不向き述語
PREDICATES = [
    "向いていない", "向いてない", "向いてる人", "向いてる特徴",
    "つらい", "辛い", "きつい", "しんどい", "大変",
    "メリット", "デメリット", "やめとけ", "後悔",
]

# 修飾語（なしも含む）
MODIFIERS = [
    "", "特徴", "理由", "原因", "対処法",
    "解決策", "転職", "辞めたい", "悩み",
]


def generate_longtail_keywords(
    occupations: list[str],
    predicates: list[str],
    modifiers: list[str],
) -> Generator[str, None, None]:
    """組み合わせからロングテールキーワードを生成する。"""
    for occupation, predicate, modifier in product(occupations, predicates, modifiers):
        if modifier:
            keyword = f"{occupation} {predicate} {modifier}"
        else:
            keyword = f"{occupation} {predicate}"
        yield keyword.strip()


def deduplicate(keywords: list[str]) -> list[str]:
    """重複を除去して順序を保持する。"""
    seen = set()
    result = []
    for kw in keywords:
        if kw not in seen:
            seen.add(kw)
            result.append(kw)
    return result


# 生成実行
all_keywords = list(generate_longtail_keywords(OCCUPATIONS, PREDICATES, MODIFIERS))
unique_keywords = deduplicate(all_keywords)
print(f"生成されたキーワード数: {len(unique_keywords)}")
# → 例：15 × 14 × 9 = 1,890件（空修飾語含む実際の数は変動）
```

### Step 2：バッチ分割

Google Ads API は1リクエストあたりの操作数に上限（通常2,000件）があります。安全のため500件ずつ分割します。

```python
def chunk_list(lst: list, size: int) -> Generator[list, None, None]:
    """リストをsize件ずつのチャンクに分割する。"""
    for i in range(0, len(lst), size):
        yield lst[i:i + size]
```

### Step 3：Google Ads API でキーワードを一括登録

```python
import sys
from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.errors import GoogleAdsException


MATCH_TYPE_PHRASE = "PHRASE"   # フレーズ一致
MATCH_TYPE_EXACT  = "EXACT"    # 完全一致
MATCH_TYPE_BROAD  = "BROAD"    # 部分一致（絞り込み廃止後）


def build_keyword_operations(client, ad_group_resource_name, keywords, match_type):
    """キーワード追加オペレーションのリストを生成する。"""
    operations = []

    for keyword_text in keywords:
        operation = client.get_type("AdGroupCriterionOperation")
        criterion = operation.create
        criterion.ad_group = ad_group_resource_name
        criterion.status = client.enums.AdGroupCriterionStatusEnum.ENABLED
        criterion.keyword.text = keyword_text
        criterion.keyword.match_type = (
            client.enums.KeywordMatchTypeEnum[match_type]
        )
        operations.append(operation)

    return operations


def add_keywords_with_partial_failure(
    client,
    customer_id: str,
    ad_group_id: str,
    keywords: list[str],
    match_type: str = MATCH_TYPE_PHRASE,
    batch_size: int = 500,
) -> dict:
    """
    partial_failure=Trueでキーワードを一括登録する。

    ポリシー違反の1件のせいで全件失敗しないよう、
    partial_failure=Trueを使うのが重要。

    Returns:
        {"success": int, "failed": int, "errors": list}
    """
    ad_group_service = client.get_service("AdGroupService")
    criterion_service = client.get_service("AdGroupCriterionService")

    ad_group_resource_name = ad_group_service.ad_group_path(
        customer_id, ad_group_id
    )

    results = {"success": 0, "failed": 0, "errors": []}

    for batch_idx, batch in enumerate(chunk_list(keywords, batch_size)):
        operations = build_keyword_operations(
            client, ad_group_resource_name, batch, match_type
        )

        try:
            response = criterion_service.mutate_ad_group_criteria(
                customer_id=customer_id,
                operations=operations,
                # ポリシー違反など一部失敗しても残りを処理する
                partial_failure=True,
            )

            # 成功件数
            success_count = sum(
                1 for r in response.results if r.resource_name
            )
            results["success"] += success_count

            # partial_failureエラーの確認
            if response.partial_failure_error:
                from google.ads.googleads.util import convert_proto_plus_to_protobuf
                from google.rpc.status_pb2 import Status

                partial_error = convert_proto_plus_to_protobuf(
                    response.partial_failure_error
                )
                for detail in partial_error.details:
                    failure = client.get_type("GoogleAdsFailure")
                    failure._pb.MergeFromString(detail.value)

                    for error in failure.errors:
                        failed_op_index = error.location.field_path_elements[0].index
                        failed_keyword = batch[failed_op_index]
                        results["failed"] += 1
                        results["errors"].append({
                            "keyword": failed_keyword,
                            "code": str(error.error_code),
                            "message": error.message,
                        })

            print(
                f"バッチ {batch_idx + 1}: "
                f"成功 {success_count}/{len(batch)} 件"
            )

        except GoogleAdsException as ex:
            # バッチ全体が失敗した場合
            for error in ex.failure.errors:
                print(f"[ERROR] {error.message}", file=sys.stderr)
            results["failed"] += len(batch)
            raise

    return results


def print_summary(results: dict, total_keywords: int):
    """登録結果のサマリーを表示する。"""
    print("\n=== 登録結果サマリー ===")
    print(f"総キーワード数: {total_keywords}")
    print(f"登録成功: {results['success']} 件")
    print(f"登録失敗: {results['failed']} 件")

    if results["errors"]:
        print("\n--- 失敗キーワード（先頭10件）---")
        for error in results["errors"][:10]:
            print(f"  NG: {error['keyword']}")
            print(f"      理由: {error['message']}")
```

### Step 4：dry-run モードで事前確認

```python
import argparse


def parse_args():
    parser = argparse.ArgumentParser(description="Ad Grantsロングテールキーワード一括登録")
    parser.add_argument("--customer-id", required=True, help="Google Ads 顧客ID")
    parser.add_argument("--ad-group-id", required=True, help="広告グループID")
    parser.add_argument(
        "--match-type",
        default="PHRASE",
        choices=["PHRASE", "EXACT", "BROAD"],
        help="キーワードマッチタイプ（デフォルト: PHRASE）"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="APIへの書き込みを行わず件数確認のみ"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="登録件数の上限（テスト用）"
    )
    return parser.parse_args()


def main():
    args = parse_args()

    # キーワード生成
    all_keywords = list(generate_longtail_keywords(OCCUPATIONS, PREDICATES, MODIFIERS))
    unique_keywords = deduplicate(all_keywords)

    if args.limit:
        unique_keywords = unique_keywords[:args.limit]

    print(f"登録対象キーワード数: {len(unique_keywords)} 件")

    if args.dry_run:
        print("[DRY-RUN] APIへの書き込みはスキップします。")
        print("先頭5件のプレビュー:")
        for kw in unique_keywords[:5]:
            print(f"  - {kw}")
        return

    client = GoogleAdsClient.load_from_storage("google-ads.yaml")
    results = add_keywords_with_partial_failure(
        client=client,
        customer_id=args.customer_id,
        ad_group_id=args.ad_group_id,
        keywords=unique_keywords,
        match_type=args.match_type,
    )

    print_summary(results, len(unique_keywords))


if __name__ == "__main__":
    main()
```

---

## 実際の運用結果

ISVD Ad Grants での実装後、以下の変化が見られました：

- インプレッション数が大幅に増加（ロングテール経由のオーガニックなアクセス）
- CTR は全体で5%超を維持（インプレッション0のKWがCTRを希薄化しない）
- 予算消化率は $329/日の上限に近づいた

特に「〇〇 向いていない 特徴」「〇〇 辛い 理由」といった悩み系クエリからのクリックが、サイトの本来の目的（職業適性・キャリア相談）に合致するユーザーを連れてきました。

---

## 注意点と失敗パターン

### partial_failure を request dict 形式で渡す

一部の SDK バージョンでは、`partial_failure=True` をキーワード引数ではなく `request` 辞書形式で渡す必要があります：

```python
# SDKのバージョンによってはこちらが必要
response = criterion_service.mutate_ad_group_criteria(
    request={
        "customer_id": customer_id,
        "operations": operations,
        "partial_failure": True,
    }
)
```

`AttributeError` や `TypeError` が出た場合は request dict 形式を試してください。

### ポリシー違反キーワードへの対処

一部のキーワードは Google のポリシー違反と判定されて登録できません。`partial_failure=True` を使えば、違反キーワードをスキップして残りを全件登録できます。失敗リストをログに残しておくと後のレビューが容易になる。

### 既存キーワードとの重複

すでに登録されているキーワードと重複する場合も `partial_failure` でスキップされます。冪等性のある設計として「常に full リストを投入する」アプローチも取れます。

---

## まとめ

Ad Grants の $2.00 CPC 制限は制約ではなく、 ** ロングテール戦略の入口 ** です。

- HIGH・MEDIUM 競合キーワードは捨てる
- 組み合わせ生成で 1,000件超のロングテールを一括生成
- `partial_failure=True` でポリシー違反をスキップしながら全件登録
- インプレッション0のKWはCTRに影響しない

非営利団体の IT 担当者にとって、Google Ads API の自動化は「月$10,000分の広告枠を活用する」ための重要な手段です。

---

## 関連記事

- [Google Ads 検索クエリ分析パイプラインを自動化する](./google-ads-search-terms-analysis-adgrants-ctr)
- [Google Ads RSA を Claude + Python で自動生成・自動更新する仕組みを作った](./google-ads-rsa-claude-python-automation)
