---
title: "Google Ads RSA をClaude + Pythonで自動更新する実装詳解"
emoji: "🤖"
type: "tech"
topics: ["googleads", "python", "claude", "automation", "adgrants"]
published: false
status: "draft"
publication_name: "correlate_dev"
---

## はじめに

Google Ads の RSA（Responsive Search Ad：レスポンシブ検索広告）を手動で更新し続けることに限界を感じていました。

- 全角・半角混在の文字数カウントが面倒
- IMMUTABLE フィールドのせいで UPDATE が使えず、毎回 REMOVE → CREATE が必要
- 複数の広告グループを横断して更新するとミスが増える

そこで、Claude が広告文を生成し、Python スクリプトが Google Ads API 経由でバリデーション・更新を行う「一気通貫パイプライン」を構築しました。

この記事では、実装の中核となる `display_width()` によるバリデーション設計と `fcntl.flock()` による安全実行の仕組みを詳解します。

---

## RSA の IMMUTABLE 制約と REMOVE+CREATE

RSA では以下のフィールドが **IMMUTABLE** （変更不可）です：

- `final_urls`
- `headlines`（テキスト本体）
- `descriptions`（テキスト本体）

これらを変更しようとすると、API は以下のエラーを返します：

```
AdError.CANNOT_SET_AD_FIELD_WITH_PENDING_APPROVAL
または
FieldError.IMMUTABLE_FIELD
```

したがって広告文の更新は常に **REMOVE → CREATE** の2ステップになります。

```python
# RSA更新は必ずREMOVE → CREATE
# UPDATE は使えない

# Step 1: 既存広告を削除
remove_operation = client.get_type("AdGroupAdOperation")
remove_operation.remove = existing_ad_resource_name

# Step 2: 新しい広告を作成
create_operation = client.get_type("AdGroupAdOperation")
# ... 広告文を設定 ...

# 2つのオペレーションをまとめてAPIに送る（ただしアトミックではない）
response = ad_service.mutate_ad_group_ads(
    customer_id=customer_id,
    operations=[remove_operation, create_operation]
)
```

---

## display_width() による文字数バリデーション

RSA の文字数制限は「バイト数」でも「文字数」でもなく、 ** 表示幅 ** （display width）で判定されます。

- 半角英数字・記号 → 表示幅 1
- 全角文字（漢字・ひらがな・カタカナ）→ 表示幅 2
- 一部の特殊文字（絵文字等）→ 表示幅 2

Python の `unicodedata` モジュールを使って実装します：

```python
import unicodedata


def display_width(text: str) -> int:
    """
    文字列の表示幅を計算する。
    全角文字は2、半角文字は1として計算する。

    Args:
        text: 計算対象の文字列

    Returns:
        表示幅（整数）

    Examples:
        >>> display_width("Hello")
        5
        >>> display_width("こんにちは")
        10
        >>> display_width("AI転職支援")
        10  # A=1, I=1, 転=2, 職=2, 支=2, 援=2
    """
    width = 0
    for char in text:
        east_asian_width = unicodedata.east_asian_width(char)
        if east_asian_width in ("W", "F"):
            # Wide / Fullwidth → 表示幅2
            width += 2
        else:
            # Narrow / Halfwidth / Neutral / Ambiguous → 表示幅1
            width += 1
    return width
```

### RSA の文字数制限

| フィールド | 上限（表示幅） |
|-----------|--------------|
| headline | 30 |
| description | 90 |

```python
RSA_HEADLINE_MAX_WIDTH = 30
RSA_DESCRIPTION_MAX_WIDTH = 90
RSA_HEADLINE_MIN_COUNT = 3   # 最低3本
RSA_HEADLINE_MAX_COUNT = 15  # 最大15本
RSA_DESCRIPTION_MIN_COUNT = 2
RSA_DESCRIPTION_MAX_COUNT = 4
```

---

## validate_rsa() の設計

バリデーション結果を「致命的エラー（fatal）」と「警告（warnings）」に分類して返します。

```python
from dataclasses import dataclass, field


@dataclass
class RsaContent:
    """RSA広告文のデータクラス。"""
    headlines: list[str]
    descriptions: list[str]
    final_url: str


def validate_rsa(content: RsaContent) -> tuple[list[str], list[str]]:
    """
    RSA広告文をバリデーションする。

    Returns:
        (fatals, warnings) のタプル。
        fatals が空でない場合はAPIへの投入を中止すること。
    """
    fatals = []
    warnings = []

    # --- headline バリデーション ---
    headline_count = len(content.headlines)

    if headline_count < RSA_HEADLINE_MIN_COUNT:
        fatals.append(
            f"ヘッドラインが{headline_count}本しかありません（最低{RSA_HEADLINE_MIN_COUNT}本必要）"
        )
    elif headline_count > RSA_HEADLINE_MAX_COUNT:
        fatals.append(
            f"ヘッドラインが{headline_count}本あります（最大{RSA_HEADLINE_MAX_COUNT}本）"
        )

    for i, headline in enumerate(content.headlines, 1):
        width = display_width(headline)
        if width > RSA_HEADLINE_MAX_WIDTH:
            fatals.append(
                f"ヘッドライン{i}「{headline}」が"
                f"{width}幅（上限{RSA_HEADLINE_MAX_WIDTH}）"
            )
        elif width >= RSA_HEADLINE_MAX_WIDTH - 2:
            warnings.append(
                f"ヘッドライン{i}「{headline}」が"
                f"{width}幅（上限まで{RSA_HEADLINE_MAX_WIDTH - width}幅の余裕）"
            )

    # --- description バリデーション ---
    desc_count = len(content.descriptions)

    if desc_count < RSA_DESCRIPTION_MIN_COUNT:
        fatals.append(
            f"ディスクリプションが{desc_count}本しかありません（最低{RSA_DESCRIPTION_MIN_COUNT}本必要）"
        )
    elif desc_count > RSA_DESCRIPTION_MAX_COUNT:
        fatals.append(
            f"ディスクリプションが{desc_count}本あります（最大{RSA_DESCRIPTION_MAX_COUNT}本）"
        )

    for i, description in enumerate(content.descriptions, 1):
        width = display_width(description)
        if width > RSA_DESCRIPTION_MAX_WIDTH:
            fatals.append(
                f"ディスクリプション{i}が{width}幅（上限{RSA_DESCRIPTION_MAX_WIDTH}）"
            )

    # --- URL バリデーション ---
    if not content.final_url.startswith("https://"):
        fatals.append(f"final_url が https:// で始まっていません: {content.final_url}")

    # --- 重複チェック ---
    if len(set(content.headlines)) < len(content.headlines):
        warnings.append("重複するヘッドラインがあります")
    if len(set(content.descriptions)) < len(content.descriptions):
        warnings.append("重複するディスクリプションがあります")

    return fatals, warnings
```

### 使い方

```python
content = RsaContent(
    headlines=[
        "AI が適職を診断します",
        "3分でキャリア相談",
        "無料・社会人向け職業診断",
    ],
    descriptions=[
        "AIがあなたの強みと志向を分析し、向いている職業をご提案します。",
        "フリーランス・転職・副業を検討中の方に。無料でご利用いただけます。",
    ],
    final_url="https://career.isvd.or.jp/diagnosis"
)

fatals, warnings = validate_rsa(content)

if fatals:
    print("[ERROR] 致命的エラーがあります。APIへの投入を中止します。")
    for f in fatals:
        print(f"  - {f}")
    sys.exit(1)

if warnings:
    print("[WARN] 警告があります（投入は可能です）：")
    for w in warnings:
        print(f"  - {w}")
```

---

## RSA の REMOVE → CREATE 実装

```python
import sys
from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.errors import GoogleAdsException


def get_existing_rsa(client, customer_id, ad_group_id):
    """既存のRSAを取得する。存在しない場合はNoneを返す。"""
    ga_service = client.get_service("GoogleAdsService")
    query = f"""
        SELECT
            ad_group_ad.resource_name,
            ad_group_ad.ad.id,
            ad_group_ad.status
        FROM ad_group_ad
        WHERE ad_group_ad.ad_group = '{ad_group_id}'
          AND ad_group_ad.ad.type = 'RESPONSIVE_SEARCH_AD'
          AND ad_group_ad.status != 'REMOVED'
    """
    response = ga_service.search(customer_id=customer_id, query=query)
    rows = list(response)
    return rows[0].ad_group_ad if rows else None


def build_rsa_create_operation(client, ad_group_resource_name, content: RsaContent):
    """RSA作成オペレーションを構築する。"""
    operation = client.get_type("AdGroupAdOperation")
    ad_group_ad = operation.create
    ad_group_ad.ad_group = ad_group_resource_name
    ad_group_ad.status = client.enums.AdGroupAdStatusEnum.ENABLED

    rsa = ad_group_ad.ad.responsive_search_ad

    # ヘッドライン設定
    for headline_text in content.headlines:
        headline = client.get_type("AdTextAsset")
        headline.text = headline_text
        rsa.headlines.append(headline)

    # ディスクリプション設定
    for description_text in content.descriptions:
        description = client.get_type("AdTextAsset")
        description.text = description_text
        rsa.descriptions.append(description)

    # final_url
    ad_group_ad.ad.final_urls.append(content.final_url)

    return operation


def update_rsa(
    client,
    customer_id: str,
    ad_group_id: str,
    content: RsaContent,
    dry_run: bool = False,
    create_only: bool = False,
):
    """
    RSAをREMOVE → CREATEで更新する。

    Args:
        create_only: Trueの場合はREMOVEをスキップ（障害リカバリ用）
    """
    ad_service = client.get_service("AdGroupAdService")
    ad_group_service = client.get_service("AdGroupService")

    ad_group_resource_name = ad_group_service.ad_group_path(
        customer_id, ad_group_id
    )

    # バリデーション
    fatals, warnings = validate_rsa(content)
    if fatals:
        print("[ERROR] バリデーションエラーがあります。処理を中止します。")
        for f in fatals:
            print(f"  - {f}")
        sys.exit(1)
    if warnings:
        for w in warnings:
            print(f"[WARN] {w}")

    if dry_run:
        print("[DRY-RUN] バリデーション通過。APIへの書き込みはスキップします。")
        return

    operations = []

    # REMOVE（既存広告があれば）
    if not create_only:
        existing = get_existing_rsa(client, customer_id, ad_group_id)
        if existing:
            remove_op = client.get_type("AdGroupAdOperation")
            remove_op.remove = existing.resource_name
            operations.append(remove_op)
            print(f"[INFO] 既存RSAを削除します: {existing.resource_name}")
        else:
            print("[INFO] 既存RSAなし。CREATEのみ実行します。")

    # CREATE
    create_op = build_rsa_create_operation(client, ad_group_resource_name, content)
    operations.append(create_op)

    try:
        response = ad_service.mutate_ad_group_ads(
            customer_id=customer_id,
            operations=operations,
        )
        for result in response.results:
            print(f"[SUCCESS] RSAを更新しました: {result.resource_name}")

    except GoogleAdsException as ex:
        for error in ex.failure.errors:
            print(f"[ERROR] {error.error_code}: {error.message}", file=sys.stderr)
        raise
```

---

## ロックファイルと指数バックオフの組み合わせ

同時実行防止と競合リトライを組み合わせた完全な実装：

```python
import fcntl
import time
import os

LOCK_FILE = "/tmp/update_rsa.lock"


def with_lock(func):
    """デコレータ：ファイルロックで同時実行を防ぐ。"""
    def wrapper(*args, **kwargs):
        lock_fd = open(LOCK_FILE, "w")
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("[ERROR] 別のプロセスが実行中です。終了します。", file=sys.stderr)
            lock_fd.close()
            sys.exit(1)

        try:
            return func(*args, **kwargs)
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            lock_fd.close()
            if os.path.exists(LOCK_FILE):
                os.remove(LOCK_FILE)

    return wrapper


def retry_with_backoff(func, max_retries=3, initial_delay=1.0):
    """exponential backoffでリトライする。"""
    delay = initial_delay
    for attempt in range(1, max_retries + 1):
        try:
            return func()
        except GoogleAdsException as ex:
            is_concurrent = any(
                "Multiple requests were attempting" in str(e.message)
                for e in ex.failure.errors
            )
            if is_concurrent and attempt < max_retries:
                print(f"[WARN] 競合エラー。{delay:.0f}秒後にリトライ（{attempt}/{max_retries}）...")
                time.sleep(delay)
                delay *= 2
            else:
                raise


@with_lock
def main():
    args = parse_args()
    client = GoogleAdsClient.load_from_storage("google-ads.yaml")

    content = load_rsa_content(args.content_file)  # JSONやYAMLから読み込む

    retry_with_backoff(
        lambda: update_rsa(
            client,
            args.customer_id,
            args.ad_group_id,
            content,
            dry_run=args.dry_run,
            create_only=args.create_only,
        )
    )
```

---

## まとめ

RSA 自動更新パイプラインの主要コンポーネントをまとめます：

| コンポーネント | 役割 |
|-------------|------|
| `display_width()` | 全角/半角混在の表示幅カウント |
| `validate_rsa()` | fatal/warnings 分類のバリデーション |
| REMOVE+CREATE | IMMUTABLE フィールドへの対処 |
| `fcntl.flock()` | 同時実行防止 |
| exponential backoff | 競合エラーのリトライ |
| `--dry-run` | 安全確認 |
| `--create-only` | 障害リカバリ |

Claude による広告文生成と組み合わせた一気通貫パイプラインの設計については、別記事で解説しています。

---

## 関連記事

- [Google Ads RSA をClaude + Pythonで自動生成する仕組みを作った（前編）](./google-ads-rsa-claude-python-automation)
- [Google Ads API で広告が消えた15秒間 — REMOVE+CREATE競合エラーのリカバリ手順](./google-ads-api-rsa-remove-create-race-condition)
