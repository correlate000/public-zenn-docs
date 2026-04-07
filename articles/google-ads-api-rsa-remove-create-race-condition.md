---
title: "Google Ads API で広告が消えた15秒間 — REMOVE+CREATE競合エラーのリカバリ手順"
emoji: "🚨"
type: "tech"
topics: ["googleads", "python", "automation", "adgrants"]
published: false
status: "draft"
publication_name: "correlate_dev"
---

## はじめに

ある朝、`echo "y" | python update_rsa.py` を実行したら、広告が15秒間完全にゼロ本になりました。

ISVD（一般社団法人社会構想デザイン機構）のAd Grants運用で発生した実障害です。REMOVE は成功し、CREATE は失敗した——その15秒間、検索結果にISVDの広告は一切表示されていませんでした。

この記事では、Google Ads API 固有の「REMOVE+CREATE の非アトミック性」というリスクと、その対策として実装したロックファイル・リトライ・フラグ設計について解説します。

---

## 何が起きたか

### エラーメッセージ

```
google.ads.googleads.errors.GoogleAdsException:
  Request ID: xxxxxxx
  Error: multiple_errors
  Details:
    - Multiple requests were attempting to modify the same resource at once.
```

「Multiple requests were attempting to modify the same resource at once」——複数のリクエストが同一リソースを同時に変更しようとした、という競合エラーです。

### 原因：2プロセス同時起動

`echo "y" | python update_rsa.py` というパターンが、2つのプロセスを同時に起動させていました。

実際のシェルスクリプト側で同じコマンドが重複していたことが直接原因でしたが、そもそも `echo "y" |` でパイプ実行できる設計自体がリスクを内包していました。

### RSA更新の非アトミック性

Google Ads API では、RSA（Responsive Search Ad）の headlines や final_urls は **IMMUTABLE フィールド** です。広告文を変更するには、必ず以下の2ステップが必要です。

```
Step 1: REMOVE（既存広告の削除）
Step 2: CREATE（新しい広告の作成）
```

この2ステップはアトミックではありません。Step 1 と Step 2 の間には必ず時間差があり、その間は広告が存在しない状態になります。

今回はStep 1（REMOVE）が成功し、Step 2（CREATE）が競合エラーで失敗しました。結果として広告が約15秒間ゼロ本になりました。

---

## 即時リカバリ手順

競合エラー発生時のリカバリは、**CREATE-onlyの再実行**です。

```python
# リカバリ用：REMOVEせずにCREATEのみ再実行
python update_rsa.py --create-only --no-confirm
```

すでにREMOVEは成功しているため、再度REMOVEしてはいけません。CREATE-onlyで実行することで広告を復元します。

今回はこのリカバリで約15秒後に広告が復帰しました。

---

## 根本対策の実装

### 対策1：fcntl.flock() によるロックファイル

最も重要な対策が、ファイルロックによる同時実行防止です。

```python
import fcntl
import os
import sys

LOCK_FILE = "/tmp/update_rsa.lock"

def acquire_lock():
    """同時実行を防ぐためのファイルロックを取得する。"""
    lock_fd = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return lock_fd
    except BlockingIOError:
        print("[ERROR] 別のupdate_rsa.pyプロセスが実行中です。終了します。", file=sys.stderr)
        lock_fd.close()
        sys.exit(1)

def release_lock(lock_fd):
    """ロックを解放する。"""
    fcntl.flock(lock_fd, fcntl.LOCK_UN)
    lock_fd.close()
    if os.path.exists(LOCK_FILE):
        os.remove(LOCK_FILE)
```

使い方：

```python
def main():
    lock_fd = acquire_lock()
    try:
        # メイン処理
        run_update()
    finally:
        release_lock(lock_fd)
```

`fcntl.LOCK_NB`（non-blocking）フラグにより、ロック取得に失敗した場合はすぐにエラーで終了します。待機しないことで、ユーザーが「なんか遅いな」と思って再実行する——という二重実行パターンも防止できます。

### 対策2：--no-confirm フラグでパイプ実行を排除

`echo "y" | python update_rsa.py` というパターンを根本から封じます。

```python
import argparse

def parse_args():
    parser = argparse.ArgumentParser(description="RSA広告を更新する")
    parser.add_argument(
        "--no-confirm",
        action="store_true",
        help="確認プロンプトをスキップする（CI/CD・スクリプト実行用）"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="APIへの書き込みを行わずに検証のみ実行する"
    )
    parser.add_argument(
        "--create-only",
        action="store_true",
        help="REMOVEをスキップしてCREATEのみ実行する（リカバリ用）"
    )
    return parser.parse_args()
```

確認が必要な場合は `--no-confirm` フラグを明示的に渡す設計にします。`echo "y" |` のようなパイプ実行は想定しない——この設計上の意図をコードで表現します。

### 対策3：exponential backoff リトライ

競合エラーはリトライで解決できることが多いです。1回失敗しても諦めず、指数バックオフで再試行します。

```python
import time
from google.ads.googleads.errors import GoogleAdsException

def create_rsa_with_retry(client, customer_id, ad_group_id, rsa_data, max_retries=3):
    """競合エラーに対してexponential backoffでリトライするCREATE処理。"""
    delay = 1  # 初回待機秒数

    for attempt in range(1, max_retries + 1):
        try:
            result = _create_rsa(client, customer_id, ad_group_id, rsa_data)
            if attempt > 1:
                print(f"[INFO] {attempt}回目で成功しました。")
            return result

        except GoogleAdsException as ex:
            # 競合エラーか判定
            is_concurrent_error = any(
                "Multiple requests were attempting to modify the same resource"
                in str(error.message)
                for error in ex.failure.errors
            )

            if is_concurrent_error and attempt < max_retries:
                print(
                    f"[WARN] 競合エラー発生（{attempt}/{max_retries}回目）。"
                    f"{delay}秒後にリトライします..."
                )
                time.sleep(delay)
                delay *= 2  # 1s → 2s → 4s
            else:
                # リトライ上限 or 競合以外のエラー
                raise
```

待機時間は 1秒 → 2秒 → 4秒 と指数的に増加します。最大3回リトライして、それでも失敗した場合は例外を上位に伝播させます。

---

## 改修後のメイン処理フロー

```python
def main():
    args = parse_args()

    # ロック取得（同時実行防止）
    lock_fd = acquire_lock()

    try:
        client = get_ads_client()

        # 広告文のバリデーション
        fatals, warnings = validate_rsa(rsa_data)
        if fatals:
            print(f"[ERROR] バリデーションエラー: {fatals}")
            sys.exit(1)
        if warnings:
            print(f"[WARN] 警告: {warnings}")

        # dry-runモード
        if args.dry_run:
            print("[DRY-RUN] 検証完了。APIへの書き込みは行いません。")
            return

        # 確認プロンプト（--no-confirmでスキップ）
        if not args.no_confirm:
            answer = input("RSAを更新しますか？ (yes/no): ")
            if answer.lower() != "yes":
                print("キャンセルしました。")
                return

        # REMOVE（--create-onlyの場合はスキップ）
        if not args.create_only:
            remove_existing_rsa(client, customer_id, ad_group_id)

        # CREATE（競合エラーに対してリトライ付き）
        create_rsa_with_retry(client, customer_id, ad_group_id, rsa_data)

        print("[SUCCESS] RSA更新が完了しました。")

    finally:
        release_lock(lock_fd)

if __name__ == "__main__":
    main()
```

---

## 設計上の教訓

### 「echo pipe stdin」は危険なパターン

`echo "y" | python script.py` は一見便利ですが、以下のリスクがあります。

- 確認プロンプトをバイパスするため、意図しない実行を引き起こす
- パイプがハングした場合に予期しない挙動になる
- スクリプトが「対話的実行を想定している」のか「非対話的実行を想定している」のか曖昧になる

正しいアプローチは **明示的なフラグ** で制御することです。非対話実行したい場合は `--no-confirm` を渡す。この設計ならコードを読んだ人間が意図を理解できます。

### REMOVE+CREATE はトランザクションではない

Google Ads API には、複数操作を1つのアトミックなトランザクションにまとめる手段がありません（少なくともRSAの場合）。設計上の前提として、**REMOVE と CREATE の間には必ずダウンタイムが生じる**ことを受け入れる必要があります。

このリスクを最小化するためには：

1. **同時実行を防ぐ**（fcntl.flock）
2. **失敗時に素早くリカバリできる設計**（--create-only フラグ）
3. **競合時にリトライする**（exponential backoff）

の3層で対策することが有効です。

### 競合エラーのログを残す

今回のような実障害が発生した場合、ログがなければ原因調査ができません。GoogleAdsException のエラーメッセージを必ずログに記録してください。

```python
except GoogleAdsException as ex:
    for error in ex.failure.errors:
        print(
            f"[ERROR] Code: {error.error_code}\n"
            f"        Message: {error.message}\n"
            f"        Trigger: {error.trigger.string_value}"
        )
    raise
```

---

## 監視：競合エラーをアラートで検知する

障害の早期発見のために、競合エラーが発生した際に Discord 等へ通知する仕組みを加えることを推奨します。

```python
import urllib.request
import json
import os


def notify_discord(message: str):
    """Discordに通知を送る。"""
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        return

    payload = json.dumps({"content": message}).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status not in (200, 204):
                print(f"[WARN] Discord通知失敗: status={resp.status}")
    except Exception as e:
        print(f"[WARN] Discord通知エラー: {e}")


def create_rsa_with_retry(client, customer_id, ad_group_id, rsa_data, max_retries=3):
    """競合エラーに対してexponential backoffでリトライするCREATE処理。"""
    delay = 1

    for attempt in range(1, max_retries + 1):
        try:
            result = _create_rsa(client, customer_id, ad_group_id, rsa_data)
            if attempt > 1:
                print(f"[INFO] {attempt}回目で成功しました。")
                notify_discord(f"✅ RSA更新: {attempt}回目のリトライで成功しました。")
            return result

        except GoogleAdsException as ex:
            is_concurrent_error = any(
                "Multiple requests were attempting to modify the same resource"
                in str(error.message)
                for error in ex.failure.errors
            )

            if is_concurrent_error and attempt < max_retries:
                warn_msg = (
                    f"[WARN] 競合エラー（{attempt}/{max_retries}回目）。"
                    f"{delay}秒後にリトライします..."
                )
                print(warn_msg)
                notify_discord(f"⚠️ RSA更新競合エラー: {warn_msg}")
                time.sleep(delay)
                delay *= 2
            else:
                error_msg = f"RSA更新失敗（{attempt}回リトライ後）: {ex}"
                notify_discord(f"🚨 {error_msg}")
                raise
```

環境変数 `DISCORD_WEBHOOK_URL` を設定しておくと、競合エラー発生時・リトライ成功時・最終失敗時に Discord へ通知が届きます。

---

## CI/CD での実行パターン

GitHub Actions や Cloud Scheduler から定期実行する場合のサンプルです：

```yaml
# .github/workflows/update-rsa.yml
name: RSA Update

on:
  schedule:
    - cron: "0 1 * * 1"  # 毎週月曜日 AM 1:00 JST（UTC 16:00 日曜）
  workflow_dispatch:       # 手動実行も可能

jobs:
  update-rsa:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install dependencies
        run: pip install google-ads

      - name: Run RSA update
        env:
          GOOGLE_ADS_YAML: ${{ secrets.GOOGLE_ADS_YAML }}
          DISCORD_WEBHOOK_URL: ${{ secrets.DISCORD_WEBHOOK_URL }}
        run: |
          echo "$GOOGLE_ADS_YAML" > google-ads.yaml
          python update_rsa.py \
            --content-file rsa_content.json \
            --no-confirm
```

`--no-confirm` フラグで確認プロンプトをスキップしつつ、`echo "y" |` パターンを使わない安全な自動実行を実現しています。

---

## まとめ

| 問題 | 対策 |
|------|------|
| 2プロセス同時起動 | `fcntl.flock()` によるロックファイル |
| `echo "y" \|` パイプ実行 | `--no-confirm` フラグによる明示的制御 |
| CREATE失敗時のリカバリ困難 | `--create-only` フラグ |
| 競合エラーで即座に失敗 | exponential backoff（1s→2s→4s、最大3回） |
| 障害の検知遅れ | Discord 通知による即時アラート |

Google Ads API の REMOVE+CREATE パターンは非アトミックです。この前提を設計に織り込み、同時実行防止・リトライ・リカバリ・監視の4層で守ることが、広告ゼロ本という実障害を防ぐための実践的なアプローチです。

---

## 関連記事

- [Google Ads RSA を Claude + Python で自動生成・自動更新する仕組みを作った](./google-ads-rsa-claude-python-automation)
- [Google Ads API v23でMAXIMIZE_CLICKSが使えない問題とTARGET_SPENDへの移行](./google-ads-api-v23-maximize-clicks-target-spend)
