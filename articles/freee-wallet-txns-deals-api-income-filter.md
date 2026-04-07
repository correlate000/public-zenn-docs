---
title: "freee APIで実入金だけを検出する — wallet_txns + deals 二重フィルタ設計"
emoji: "💰"
type: "tech"
topics: ["freee", "api", "python", "discord", "automation"]
published: false
status: "draft"
publication_name: "correlate_dev"
---

## はじめに

freee API で入金通知システムを作ろうとしたとき、最初に踏み込んだ落とし穴があります。

`wallet_txns` エンドポイントに `entry_side=income` フィルタをかければ入金だけ取れる、と思っていたら、**請求書を作成したときの自動仕訳も「income」として返ってくる**のです。

結果、請求書を登録するたびに「入金がありました」というDiscord通知が飛んでしまいました。

本記事では、この問題を解決するために実装した **wallet_txns + deals API の二重フィルタ設計** を紹介します。

---

## 問題の詳細：自動仕訳と実入金の混在

### freee の仕訳フロー

freee では、請求書を作成すると自動的に売掛金の仕訳が生成されます。この仕訳も `wallet_txns` の `entry_side=income` に含まれるため、単純なフィルタでは実際の入金と区別できません。

```
請求書作成
  → 売掛金仕訳（自動）→ wallet_txns に income として記録
  
実際の入金（銀行振込）
  → 入金仕訳（手動 or 自動マッチング）→ wallet_txns に income として記録
```

見た目がほぼ同じなので、フィールドを丁寧に読み解く必要があります。

### wallet_txns の主要フィールド

```json
{
  "id": 12345,
  "entry_side": "income",
  "amount": 110000,
  "date": "2026-02-28",
  "description": "売上計上 クライアントA",
  "rule_matched": false,
  "deal_id": 67890,
  "walletable_id": 1,
  "walletable_type": "bank_account"
}
```

注目すべきフィールドは：

- `rule_matched` — 自動仕訳ルールによってマッチングされたか
- `deal_id` — 紐付いている取引（deal）のID
- `walletable_type` — 銀行口座・カードの種別

---

## 一次フィルタ：rule_matched による除外

最初のフィルタは `rule_matched` です。

自動仕訳ルールでマッチングされたトランザクション（`rule_matched: true`）は、freee が自動的に処理したものです。実際の銀行入金がルールにマッチした場合も含まれますが、**請求書作成時の自動仕訳** はルールマッチではなく手動生成のため `rule_matched: false` になります。

ただし、`rule_matched: false` かつ `deal_id` が存在するケースが問題になります。

```python
import requests
from typing import Optional

FREEE_API_BASE = "https://api.freee.co.jp/api/1"

def get_wallet_txns(
    access_token: str,
    company_id: int,
    walletable_id: int,
    date_from: str,
    date_to: str,
) -> list[dict]:
    """wallet_txns を取得する"""
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {
        "company_id": company_id,
        "walletable_id": walletable_id,
        "walletable_type": "bank_account",
        "date_from": date_from,
        "date_to": date_to,
        "entry_side": "income",
    }
    resp = requests.get(
        f"{FREEE_API_BASE}/wallet_txns",
        headers=headers,
        params=params,
    )
    resp.raise_for_status()
    return resp.json().get("wallet_txns", [])


def primary_filter(txns: list[dict]) -> list[dict]:
    """一次フィルタ: 候補を絞り込む"""
    candidates = []
    for txn in txns:
        # rule_matched が True のものは実入金の可能性が高い
        # ただし deal_id があるものは二次フィルタが必要
        if txn.get("rule_matched") and txn.get("deal_id"):
            candidates.append(txn)
        elif txn.get("rule_matched") and not txn.get("deal_id"):
            # rule_matched かつ deal_id なし → ルールマッチのみ、処理スキップ
            pass
    return candidates
```

---

## 二次フィルタ：deals API で payments を確認

`deal_id` が存在するトランザクションについては、deals API で紐付いた取引の `payments` 配列を確認します。

**`payments` 配列に要素がある = 実際に入金が記録されている** ということになります。

```python
def get_deal_payments(
    access_token: str,
    company_id: int,
    deal_id: int,
) -> list[dict]:
    """deals API で payments を取得する"""
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {"company_id": company_id}
    resp = requests.get(
        f"{FREEE_API_BASE}/deals/{deal_id}",
        headers=headers,
        params=params,
    )
    resp.raise_for_status()
    deal = resp.json().get("deal", {})
    return deal.get("payments", [])


def secondary_filter(
    candidates: list[dict],
    access_token: str,
    company_id: int,
) -> list[dict]:
    """二次フィルタ: deals API で実入金か確認"""
    confirmed = []
    for txn in candidates:
        deal_id = txn.get("deal_id")
        if not deal_id:
            # deal_id なし → 実入金と判定
            confirmed.append(txn)
            continue

        try:
            payments = get_deal_payments(access_token, company_id, deal_id)
            if payments:
                # payments がある = 実入金
                confirmed.append(txn)
        except requests.HTTPError as e:
            # API失敗時は安全側（通知する方向）に倒す
            print(f"deals API error for deal_id={deal_id}: {e}")
            confirmed.append(txn)  # 失敗しても通知する

    return confirmed
```

---

## 統合実装：完全な入金検出フロー

```python
from datetime import date, timedelta

def detect_real_income(
    access_token: str,
    company_id: int,
    walletable_id: int,
) -> list[dict]:
    """実際の入金のみを検出する"""
    today = date.today()
    yesterday = today - timedelta(days=1)

    # 前日分のトランザクションを取得
    txns = get_wallet_txns(
        access_token=access_token,
        company_id=company_id,
        walletable_id=walletable_id,
        date_from=yesterday.isoformat(),
        date_to=today.isoformat(),
    )

    # 一次フィルタ
    candidates = primary_filter(txns)

    # 二次フィルタ
    confirmed = secondary_filter(candidates, access_token, company_id)

    return confirmed
```

---

## Discord通知への連携

実入金を検出したらDiscordに通知します。

```python
import httpx
from typing import Optional

def notify_discord(
    webhook_url: str,
    txns: list[dict],
    dry_run: bool = False,
) -> None:
    """Discordに入金通知を送る"""
    if not txns:
        return

    for txn in txns:
        amount = txn.get("amount", 0)
        description = txn.get("description", "（説明なし）")
        txn_date = txn.get("date", "不明")

        message = (
            f"**入金を検出しました**\n"
            f"日付: {txn_date}\n"
            f"金額: ¥{amount:,}\n"
            f"内容: {description}"
        )

        if dry_run:
            print(f"[DRY RUN] {message}")
            continue

        resp = httpx.post(webhook_url, json={"content": message})
        resp.raise_for_status()


# メインフロー
def main():
    import os

    access_token = os.environ["FREEE_ACCESS_TOKEN"]
    company_id = int(os.environ["FREEE_COMPANY_ID"])
    walletable_id = int(os.environ["FREEE_WALLETABLE_ID"])
    discord_webhook = os.environ["DISCORD_WEBHOOK_URL"]

    confirmed = detect_real_income(
        access_token=access_token,
        company_id=company_id,
        walletable_id=walletable_id,
    )

    notify_discord(
        webhook_url=discord_webhook,
        txns=confirmed,
    )

    print(f"処理完了: {len(confirmed)} 件の実入金を検出")


if __name__ == "__main__":
    main()
```

---

## 実装上の注意点

### 1. APIレート制限

freee API のレート制限は 3,600リクエスト/時間（OAuth2トークン単位）です。`deal_id` がある候補ごとに deals API を1回呼ぶため、候補が多い場合はレート制限に注意してください。

候補が多い場合は `time.sleep(0.1)` を挟むか、並列処理時には `asyncio` + セマフォで同時接続数を制限します。

```python
import asyncio
import httpx

async def get_deal_payments_async(
    client: httpx.AsyncClient,
    access_token: str,
    company_id: int,
    deal_id: int,
    semaphore: asyncio.Semaphore,
) -> list[dict]:
    async with semaphore:
        headers = {"Authorization": f"Bearer {access_token}"}
        resp = await client.get(
            f"{FREEE_API_BASE}/deals/{deal_id}",
            headers=headers,
            params={"company_id": company_id},
        )
        resp.raise_for_status()
        return resp.json().get("deal", {}).get("payments", [])
```

### 2. トークンリフレッシュ

freee のアクセストークンは24時間で期限切れになります。長期稼働させる場合はリフレッシュトークンを使った自動更新が必要です。

```python
def refresh_token(client_id: str, client_secret: str, refresh_token: str) -> dict:
    resp = requests.post(
        "https://accounts.secure.freee.co.jp/public_api/token",
        data={
            "grant_type": "refresh_token",
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
        },
    )
    resp.raise_for_status()
    return resp.json()  # access_token と refresh_token を含む
```

### 3. 安全側への倒し方

二次フィルタでAPIが失敗した場合、**通知する方向（偽陽性）** に倒します。誤通知は手間ですが、実入金の見逃し（偽陰性）よりはるかにマシです。

この判断基準は実装に明示的に書いておくと、後から読み返したときに設計意図が伝わります。

---

## まとめ

freee APIで実入金を確実に検出するためのポイントを整理します：

| 問題 | 解決策 |
|------|--------|
| 請求書作成時の自動仕訳が混入 | `rule_matched` + `deal_id` で一次フィルタ |
| 売掛金マッチングと実入金の区別 | deals API の `payments` 配列で二次確認 |
| API失敗時のリスク | 安全側（通知する方向）に倒す |
| トークン期限切れ | リフレッシュトークンで自動更新 |

`wallet_txns` の `entry_side=income` だけでは不十分で、`deals` API との組み合わせが必要です。freee の自動仕訳の挙動を理解した上でフィルタを設計することが、正確な入金通知システムの鍵になります。
