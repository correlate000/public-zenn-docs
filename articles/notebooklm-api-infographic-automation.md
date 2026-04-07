---
title: "NotebookLM非公式APIでインフォグラフィック自動生成を試みた話——認証・レートリミット・ポーリングの罠"
emoji: "📊"
type: "tech"
topics: ["notebooklm", "python", "automation", "api", "playwright"]
published: false
status: "draft"
publication_name: "correlate_dev"
---

## はじめに

NotebookLMのインフォグラフィック生成機能をPythonから自動化できないか——そう思ったのは、長文ドキュメントから視覚的なサマリーを定期的に作りたかったからです。

公式APIは存在しません。調べると `notebooklm-py` という非公式ライブラリがあり、ブラウザCookieを使った認証でNotebookLMの機能にアクセスできるとのこと。日本語情報がほぼ皆無だったため、手探りで実装することになりました。

この記事では、動くまでに詰まったポイント（認証・属性名の不一致・レートリミット・ポーリング）を中心に書きます。完全な自動化パイプラインを目指す方の参考になれば幸い。

:::message alert
** 免責事項 **: `notebooklm-py` は非公式ライブラリです。Googleの利用規約に反する可能性があります。本記事は技術的な探求の記録であり、本番環境での使用を推奨しない立場。また、ライブラリの仕様は予告なく変更・破壊される可能性があります。
:::

---

## notebooklm-pyとは

`notebooklm-py` はNotebookLMのWeb UIをリバースエンジニアリングして作られたPythonライブラリです。

```bash
pip install notebooklm-py
```

公式ドキュメントは英語のみで、日本語の情報はほとんどありません。GitHubのREADMEとソースコードを読みながら進める必要があります。

---

## 認証の仕組み

`notebooklm-py` はブラウザのCookieを使って認証します。PlaywrightでChromeブラウザを制御し、Googleアカウントにログイン済みの状態からCookieを取得する仕組みです。

### `AuthTokens.from_storage()` の実装

```python
# auth_setup.py

import asyncio
from playwright.async_api import async_playwright
from notebooklm import AuthTokens

async def setup_auth() -> AuthTokens:
    """
    PlaywrightでChromeのCookieを取得してAuthTokensを作成する

    前提: Chrome で NotebookLM にログイン済みであること
    """
    async with async_playwright() as p:
        # ユーザープロファイルを使うことでログイン済み状態を維持
        browser = await p.chromium.launch_persistent_context(
            user_data_dir="/path/to/chrome/profile",  # Chromeのプロファイルパス
            headless=True,  # バックグラウンドで実行
            channel="chrome"  # システムにインストール済みのChromeを使用
        )

        page = await browser.new_page()
        await page.goto("https://notebooklm.google.com/")

        # ログイン状態を確認
        await page.wait_for_selector("[data-testid='notebook-list']", timeout=10000)

        # AuthTokensを生成
        auth = await AuthTokens.from_storage(browser.storage_state())
        await browser.close()

        return auth


# macOSのChromeプロファイルパス
# ~/Library/Application Support/Google/Chrome/Default
```

:::message
**macOS Tahoe (25.x) + Python 3.13 の注意 **: 現時点でSSLバグが報告されています。Python 3.12.x を pyenv で使うことを推奨します。詳細は `reference_macos_tahoe_network_issue.md` を参照してください。
:::

### Cookie直接指定（より簡単な方法）

PlaywrightのセットアップなしにCookieを直接渡す方法もあります。Chromeの開発者ツールからCookieを取得して環境変数に設定する方法ですが、セキュリティ上のリスクに注意が必要です。

```python
# simple_auth.py

from notebooklm import AuthTokens
import os

def create_auth_from_env() -> AuthTokens:
    """環境変数からCookieを読み込む（セキュリティに注意）"""
    return AuthTokens(
        session_cookie=os.environ["NOTEBOOKLM_SESSION_COOKIE"],
        # 他に必要なCookieがある場合は追加
    )
```

---

## 基本フロー：ノートブック作成〜インフォグラフィック生成

認証ができたら、以下のフローでインフォグラフィックを生成します。

```
1. ノートブック作成
2. テキストソースを追加
3. インフォグラフィック生成をトリガー
4. 完了をポーリング
5. ダウンロード
```

### 実装

```python
# infographic_generator.py

import asyncio
import time
from notebooklm import NotebookLM, AuthTokens
from notebooklm.models import Notebook, Source

async def generate_infographic(
    auth: AuthTokens,
    text_content: str,
    notebook_title: str = "自動生成ノートブック"
) -> bytes:
    """
    テキストからインフォグラフィックを生成してバイナリで返す

    Returns:
        生成されたインフォグラフィックの画像データ（PNG）
    """
    client = NotebookLM(auth=auth)

    # 1. ノートブック作成
    notebook = await client.create_notebook(title=notebook_title)
    print(f"ノートブック作成: {notebook.notebook_id}")  # ← 要注意: 属性名

    # 2. テキストソースを追加
    source = await client.add_source(
        notebook_id=notebook.notebook_id,
        content=text_content,
        source_type="text"
    )
    print(f"ソース追加: {source.source_id}")

    # ソース処理完了を待機
    await wait_for_source_ready(client, notebook.notebook_id, source.source_id)

    # 3. インフォグラフィック生成をトリガー
    job = await client.create_infographic(
        notebook_id=notebook.notebook_id
    )
    print(f"生成ジョブ開始: {job.job_id}")

    # 4. 完了をポーリング（exponential backoff）
    image_data = await poll_until_complete(client, notebook.notebook_id, job.job_id)

    return image_data
```

---

## 最大の罠1：属性名の不一致

実装中に最もはまったのが、`Notebook` オブジェクトの属性名の問題でした。

### 問題

ドキュメントには `notebook.id` と書かれているのに、実際のオブジェクトは `notebook_id` という属性名を持っています。

```python
# ❌ ドキュメント通りに書いたが AttributeError が発生する
notebook = await client.create_notebook(title="テスト")
print(notebook.id)  # AttributeError: 'Notebook' object has no attribute 'id'

# ✅ 実際の属性名
print(notebook.notebook_id)  # こちらが正しい
```

同様の問題がSourceオブジェクトでも発生します。

```python
# Source オブジェクトの場合
source = await client.add_source(...)
# source.id ← 存在しない
# source.source_id ← 正しい
```

** 対策 **: 実行時に `dir()` や `vars()` でオブジェクトの実際の属性を確認してから使うこと。

```python
# デバッグ時に属性を確認する
notebook = await client.create_notebook(title="テスト")
print("利用可能な属性:", [attr for attr in dir(notebook) if not attr.startswith('_')])
# → ['created_at', 'notebook_id', 'title', 'updated_at', ...]
```

---

## 最大の罠2：レートリミット

`notebooklm-py` を使っていると、 ** 約10回でリクエストが全ブロック ** されます。

```
RateLimitError: Too many requests. Please try again later.
```

このエラーが出ると、同一アカウントからのリクエストが数時間〜数十分ブロックされます。

### レートリミットの実測値

| 操作 | 制限の目安 |
|------|---------|
| ノートブック作成 | 10〜15回/時間 |
| ソース追加 | ノートブック単位で制限あり |
| インフォグラフィック生成 | 5〜10回/時間（最も厳しい） |
| 全体のリクエスト | 約10回でブロック |

### 対策：リトライとクールダウン

```python
# rate_limit_handler.py

import asyncio
import random
from functools import wraps

class RateLimitError(Exception):
    pass

def with_rate_limit_retry(max_retries: int = 3, base_delay: float = 60.0):
    """
    レートリミットエラーを検知してリトライするデコレータ
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    if "rate limit" in str(e).lower() or "too many requests" in str(e).lower():
                        if attempt == max_retries - 1:
                            raise RateLimitError(f"レートリミット: {max_retries}回リトライ後も失敗") from e

                        # Exponential backoff + jitter
                        delay = base_delay * (2 ** attempt) + random.uniform(0, 10)
                        print(f"レートリミット検知。{delay:.0f}秒後にリトライ (試行 {attempt + 1}/{max_retries})")
                        await asyncio.sleep(delay)
                    else:
                        raise
            return None
        return wrapper
    return decorator


@with_rate_limit_retry(max_retries=3, base_delay=120.0)
async def create_notebook_safely(client, title: str):
    """レートリミット対応のノートブック作成"""
    return await client.create_notebook(title=title)
```

### 複数アカウントでの分散処理

レートリミットを根本的に回避するには、複数のGoogleアカウントを使って処理を分散する方法があります。

```python
# account_pool.py

from dataclasses import dataclass
from collections import deque
import asyncio

@dataclass
class AccountSlot:
    auth: AuthTokens
    last_used: float = 0.0
    request_count: int = 0
    is_blocked: bool = False

class AccountPool:
    def __init__(self, auth_list: list):
        self.slots = deque([AccountSlot(auth=auth) for auth in auth_list])
        self.lock = asyncio.Lock()

    async def get_available_account(self) -> AccountSlot:
        """利用可能なアカウントを取得する"""
        async with self.lock:
            # ブロックされていないアカウントを探す
            for _ in range(len(self.slots)):
                slot = self.slots[0]
                self.slots.rotate(-1)

                if not slot.is_blocked and slot.request_count < 8:  # 安全マージン
                    slot.request_count += 1
                    return slot

            raise RuntimeError("全アカウントが利用不可能です")

    async def mark_blocked(self, slot: AccountSlot, unblock_after: float = 3600.0):
        """アカウントをブロック済みとしてマークする"""
        slot.is_blocked = True
        # 1時間後に自動リセット
        await asyncio.sleep(unblock_after)
        slot.is_blocked = False
        slot.request_count = 0
```

---

## 最大の罠3：ポーリング戦略

インフォグラフィック生成は非同期で実行されます。`job.job_id` で完了状態を定期的に確認（ポーリング）する必要がありますが、 ** 間隔が短すぎるとレートリミットを消費してしまう ** のが問題です。

### Exponential Backoffの実装

```python
# polling.py

import asyncio
import time

async def poll_until_complete(
    client,
    notebook_id: str,
    job_id: str,
    initial_interval: float = 5.0,
    max_interval: float = 60.0,
    timeout: float = 600.0,  # 10分でタイムアウト
    multiplier: float = 1.5
) -> bytes:
    """
    Exponential backoffでインフォグラフィック生成完了を待つ

    Args:
        initial_interval: 最初のポーリング間隔（秒）
        max_interval: 最大ポーリング間隔（秒）
        timeout: タイムアウト（秒）
        multiplier: 間隔の倍率

    Returns:
        生成された画像データ
    """
    start_time = time.monotonic()
    interval = initial_interval

    while True:
        elapsed = time.monotonic() - start_time
        if elapsed > timeout:
            raise TimeoutError(f"インフォグラフィック生成がタイムアウトしました（{timeout}秒）")

        # 状態確認
        job_status = await client.get_infographic_status(
            notebook_id=notebook_id,
            job_id=job_id
        )

        print(f"ステータス: {job_status.status} (経過: {elapsed:.0f}秒)")

        if job_status.status == "COMPLETED":
            # 完了: 画像をダウンロード
            return await client.download_infographic(
                notebook_id=notebook_id,
                job_id=job_id
            )
        elif job_status.status == "FAILED":
            raise RuntimeError(f"インフォグラフィック生成失敗: {job_status.error_message}")
        elif job_status.status in ("PENDING", "PROCESSING"):
            # 次のポーリングまで待機
            await asyncio.sleep(interval)
            # Exponential backoff
            interval = min(interval * multiplier, max_interval)
        else:
            raise RuntimeError(f"不明なステータス: {job_status.status}")


async def wait_for_source_ready(
    client,
    notebook_id: str,
    source_id: str,
    timeout: float = 120.0
) -> None:
    """ソースの処理完了を待つ"""
    start_time = time.monotonic()
    interval = 3.0

    while True:
        if time.monotonic() - start_time > timeout:
            raise TimeoutError("ソース処理タイムアウト")

        source_status = await client.get_source_status(
            notebook_id=notebook_id,
            source_id=source_id
        )

        if source_status.is_ready:
            return

        await asyncio.sleep(interval)
        interval = min(interval * 1.3, 30.0)
```

### 実際の生成時間

実測した生成時間の目安です（コンテンツ量・サーバー負荷により変動します）。

| コンテンツ量 | ソース処理 | インフォグラフィック生成 | 合計 |
|------------|---------|---------------------|------|
| 〜1,000文字 | 10〜20秒 | 30〜60秒 | 約1分 |
| 〜5,000文字 | 20〜40秒 | 60〜120秒 | 約2〜3分 |
| 〜10,000文字 | 30〜60秒 | 120〜300秒 | 約3〜6分 |

---

## 完全な実装例

ここまでの実装をまとめた、実際に動く（と思われる）コードです。

```python
# main.py

import asyncio
import os
from pathlib import Path
from datetime import datetime

# notebooklm-py の import
from notebooklm import NotebookLM, AuthTokens

# 上記で実装した関数
from auth_setup import setup_auth
from infographic_generator import generate_infographic
from rate_limit_handler import with_rate_limit_retry


async def main():
    # 認証
    print("認証中...")
    auth = await setup_auth()

    # 生成対象のテキスト
    text_content = """
    機械学習モデルの評価指標について解説します。

    1. 精度（Accuracy）: 全予測のうち正解の割合
    2. 適合率（Precision）: 陽性予測のうち本当の陽性の割合
    3. 再現率（Recall）: 実際の陽性のうち正しく検出できた割合
    4. F1スコア: 適合率と再現率の調和平均

    これらの指標はユースケースによって使い分けが必要です...
    """

    # インフォグラフィック生成
    print("インフォグラフィック生成中...")
    try:
        image_data = await generate_infographic(
            auth=auth,
            text_content=text_content,
            notebook_title=f"自動生成_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        )

        # 保存
        output_path = Path(f"output/infographic_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png")
        output_path.parent.mkdir(exist_ok=True)
        output_path.write_bytes(image_data)

        print(f"保存完了: {output_path}")

    except Exception as e:
        print(f"エラー: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
```

---

## 現時点での限界と代替案

`notebooklm-py` を試した結果、現時点では ** 定期バッチ処理のような使い方は難しい ** という結論に至りました。

### 制約のまとめ

| 制約 | 深刻度 | 回避策 |
|------|--------|--------|
| 〜10回/時間のレートリミット | 高 | 複数アカウント・長い間隔 |
| 非公式API（破壊的変更リスク） | 高 | バージョン固定・定期テスト |
| Cookie認証の有効期限 | 中 | 定期的な再認証 |
| 日本語ドキュメント皆無 | 低 | ソースコードを読む |

### 代替案の検討

現時点でより安定した代替案として以下を検討しています。

1. **Google Slides API + Gemini API**: 公式APIで安定、インフォグラフィック的なスライドを生成可能
2. **Claude API + SVG生成 **: プロンプトでSVGを生成させ、画像変換する
3. **D3.js + Puppeteer**: データ可視化ライブラリをヘッドレスブラウザで実行

```python
# alternative_claude_svg.py

async def generate_infographic_via_claude(text: str) -> str:
    """Claude APIでSVGインフォグラフィックを生成する代替案"""
    from anthropic import AsyncAnthropic

    client = AsyncAnthropic()
    response = await client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=4000,
        messages=[{
            "role": "user",
            "content": f"""
以下のテキストをわかりやすいSVGインフォグラフィックにしてください。

テキスト:
{text}

要件:
- SVGのみを出力（説明テキスト不要）
- 幅800px、高さ600px
- 日本語フォント対応
- 視認性の高いカラーパレット
"""
        }]
    )

    return response.content[0].text  # SVGコード
```

---

## まとめ

`notebooklm-py` でインフォグラフィック自動生成を試した記録をまとめます。

** うまくいったこと:**
- 認証フローの実装（`AuthTokens.from_storage()` で比較的簡単）
- 基本的なノートブック作成→ソース追加→生成のフロー
- Exponential backoffによる安定したポーリング

** 詰まったこと:**
- 属性名の不一致（`notebook.id` vs `notebook.notebook_id`）
- 予想より厳しいレートリミット（10回程度でブロック）
- 日本語情報がゼロなので全てソースコードを読む必要がある

** 今後の方針:**
- 公式API待ち（Google が NotebookLM API を正式公開した場合に移行）
- 少量・手動トリガーの用途には使い続ける
- 大量バッチ処理は Claude API + SVG生成の代替案で対応

日本語情報が皆無だったため、誰かの参考になれば幸いです。

---

## 関連記事

- [Claude Code スラッシュコマンドで作業を自動化する](/claude-code-slash-commands)
- [Python asyncio で API 呼び出しを並列化する](/python-asyncio-api)
