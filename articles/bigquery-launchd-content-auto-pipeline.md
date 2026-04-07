---
title: "BigQuery×launchd×watchdogで作るコンテンツ全自動生成パイプライン"
emoji: "🤖"
type: "tech"
topics: ["bigquery", "python", "launchd", "automation", "claudeapi"]
published: false
status: "draft"
publication_name: "correlate_dev"
---

## はじめに

「コンテンツのネタ出しからZennへの公開まで、全部自動化できないか」と思い立ったのが半年前です。

現在は以下のパイプラインが実際に稼働しています。

```
Cloud Scheduler（日次）
  → トレンド分析スクリプト
    → BigQuery（高スコア記事候補を蓄積）
      → pipeline-monitor.py（launchd常駐、4h間隔でBQポーリング）
        → researchファイル自動生成
          → watchdog（ファイル変更監視）
            → content_pipeline_auto.py
              → Deep Research → DAレビュー → Zenn公開
```

このパイプラインの実装詳細、ハマりポイント、そして実際に動かして分かったことを解説します。

---

## アーキテクチャ概要

### なぜこの構成を選んだか

最初はCron + シンプルなPythonスクリプトで実装しようとしました。しかし以下の問題が生じました。

- Cronはmacのスリープ中に実行されない
- エラー時の再起動が手動になる
- 複数プロセスの協調が難しい

`launchd` はmacOS標準のジョブスケジューラーです。Cronと異なり、macがスリープから復帰した際に「スキップした実行」を補完できます。また、プロセスのクラッシュ時には自動再起動もサポート。

`watchdog` はPythonのファイルシステム監視ライブラリです。ファイルの作成・変更をリアルタイムに検知し、コールバックを実行できます。

この2つを組み合わせることで、「定期的にBQをチェックし、新しい候補が見つかったらresearchファイルを生成し、それを検知して記事生成パイプラインを起動する」という非同期な流れを実現しました。

### ディレクトリ構成

```
~/dev/
├── scripts/
│   ├── pipeline-monitor.py      # BQポーリング + researchファイル生成
│   └── content-pipeline-auto.py # Deep Research + 記事生成 + Zenn公開
├── Obsidian/
│   └── 07_content_pipeline/
│       └── research/            # watchdog監視対象
└── logs/
    ├── pipeline-monitor.log
    └── content-pipeline.log

~/Library/LaunchAgents/
└── design.correlate.pipeline-monitor.plist  # launchd設定
```

---

## BigQuery側の設計

### コンテンツ候補テーブル

```sql
-- content_suggestions テーブルのスキーマ
CREATE TABLE IF NOT EXISTS `project.content.content_suggestions` (
  suggestion_id STRING NOT NULL,
  title STRING,
  slug STRING,
  destination STRING,  -- 'zenn-tech', 'zenn-idea' 等
  score FLOAT64,
  status STRING,       -- 'pending', 'in_progress', 'done', 'skipped'
  reason STRING,
  trend_keywords ARRAY<STRING>,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP(),
  processed_at TIMESTAMP,
)
PARTITION BY DATE(created_at)
CLUSTER BY status, destination;
```

### 冪等なステータス更新

BigQueryへの書き込みはMERGEで冪等性を担保しています。

```python
# pipeline-monitor.py より
def mark_as_in_progress(client: bigquery.Client, suggestion_id: str) -> None:
    """候補を処理中状態に更新する（冪等）"""
    query = """
    MERGE `project.content.content_suggestions` AS target
    USING (SELECT @suggestion_id AS suggestion_id) AS source
    ON target.suggestion_id = source.suggestion_id
    WHEN MATCHED AND target.status = 'pending' THEN
      UPDATE SET
        status = 'in_progress',
        processed_at = CURRENT_TIMESTAMP()
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("suggestion_id", "STRING", suggestion_id),
        ]
    )
    client.query(query, job_config=job_config).result()
```

### ポーリングクエリ

```python
PENDING_QUERY = """
SELECT
  suggestion_id,
  title,
  slug,
  destination,
  score,
  reason,
  trend_keywords,
FROM `project.content.content_suggestions`
WHERE
  status = 'pending'
  AND destination = 'zenn-tech'
  AND score >= 70.0
ORDER BY score DESC, created_at ASC
LIMIT 3
"""
```

スコアしきい値を70.0に設定し、一度に最大3件を処理するよう制限しています。パイプラインが並列で過負荷になることを防ぐためです。

---

## launchdの設定

### plistファイル

```xml
<!-- ~/Library/LaunchAgents/design.correlate.pipeline-monitor.plist -->
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>design.correlate.pipeline-monitor</string>

  <key>ProgramArguments</key>
  <array>
    <string>/Users/naoya/.pyenv/versions/3.12.0/bin/python3</string>
    <string>/Users/naoya/dev/scripts/pipeline-monitor.py</string>
  </array>

  <!-- 4時間ごとに実行 -->
  <key>StartInterval</key>
  <integer>14400</integer>

  <!-- クラッシュ時は自動再起動 -->
  <key>KeepAlive</key>
  <dict>
    <key>Crashed</key>
    <true/>
  </dict>

  <key>StandardOutPath</key>
  <string>/Users/naoya/dev/logs/pipeline-monitor.log</string>
  <key>StandardErrorPath</key>
  <string>/Users/naoya/dev/logs/pipeline-monitor-error.log</string>

  <!-- 環境変数（Google Cloud認証） -->
  <key>EnvironmentVariables</key>
  <dict>
    <key>GOOGLE_APPLICATION_CREDENTIALS</key>
    <string>/Users/naoya/.config/gcloud/application_default_credentials.json</string>
    <key>GCP_PROJECT_ID</key>
    <string>my-project-id</string>
  </dict>
</dict>
</plist>
```

登録と管理：

```bash
# 登録
launchctl load ~/Library/LaunchAgents/design.correlate.pipeline-monitor.plist

# 手動実行（テスト用）
launchctl start design.correlate.pipeline-monitor

# 停止
launchctl unload ~/Library/LaunchAgents/design.correlate.pipeline-monitor.plist

# ステータス確認
launchctl list | grep pipeline
```

### Pythonバスの注意点

launchdはシェルのPATHを継承しません。Pythonのフルパスを指定する必要があります。

```bash
# pyenvを使っている場合はこのパスを確認
pyenv which python3
# /Users/naoya/.pyenv/versions/3.12.0/bin/python3
```

---

## pipeline-monitor.pyの実装

### BQポーリングとresearchファイル生成

```python
#!/usr/bin/env python3
# scripts/pipeline-monitor.py

import asyncio
import fcntl
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from google.cloud import bigquery

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

RESEARCH_DIR = Path.home() / "dev/Obsidian/07_content_pipeline/research"
LOCK_FILE = Path("/tmp/pipeline-monitor.lock")
PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "my-project-id")


def acquire_lock() -> bool:
    """排他ロックを取得する（重複実行防止）"""
    try:
        lock_fd = open(LOCK_FILE, 'w')
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except IOError:
        logger.warning("Another instance is running. Exiting.")
        return False


def fetch_pending_suggestions(client: bigquery.Client) -> list[dict]:
    """BigQueryからpending状態の候補を取得する"""
    query = """
    SELECT suggestion_id, title, slug, destination, score, reason, trend_keywords
    FROM `{project}.content.content_suggestions`
    WHERE status = 'pending'
      AND destination = 'zenn-tech'
      AND score >= 70.0
    ORDER BY score DESC, created_at ASC
    LIMIT 3
    """.format(project=PROJECT_ID)

    rows = list(client.query(query).result())
    logger.info(f"Found {len(rows)} pending suggestions")
    return [dict(row) for row in rows]


def generate_research_file(suggestion: dict) -> Path:
    """リサーチファイルを生成する"""
    slug = suggestion['slug']
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    content = f"""---
destination: {suggestion['destination']}
---

# {suggestion['title']}

** 生成日 **: {timestamp}
** ステータス **: 候補検出（Deep Research待ち）
**BQ ID**: {suggestion['suggestion_id']}
** スコア **: {suggestion['score']}

---

## コンテンツ候補情報

- ** タイトル **: {suggestion['title']}
- **Slug**: {slug}
- ** 推薦理由 **: {suggestion['reason']}
- ** 関連トレンド **: {', '.join(suggestion.get('trend_keywords', []))}
"""

    file_path = RESEARCH_DIR / f"research-{slug}.md"
    file_path.write_text(content, encoding='utf-8')
    logger.info(f"Generated research file: {file_path}")
    return file_path


def main():
    if not acquire_lock():
        sys.exit(0)

    # BQクライアントは1回だけ生成（コスト削減）
    client = bigquery.Client(project=PROJECT_ID)

    suggestions = fetch_pending_suggestions(client)
    if not suggestions:
        logger.info("No pending suggestions. Exiting.")
        return

    for suggestion in suggestions:
        mark_as_in_progress(client, suggestion['suggestion_id'])
        generate_research_file(suggestion)

    logger.info(f"Processed {len(suggestions)} suggestions")


if __name__ == '__main__':
    main()
```

---

## watchdogによるファイル監視

### ハンドラーの実装

```python
# content-pipeline-auto.py より（watchdog部分）

import queue
import threading
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# スレッドセーフなキュー
processing_queue: queue.Queue = queue.Queue()
processed_files: set[str] = set()
_lock = threading.Lock()


class ResearchFileHandler(FileSystemEventHandler):
    """researchファイルの作成を監視するハンドラー"""

    def on_created(self, event):
        if event.is_directory:
            return

        file_path = event.src_path
        file_name = os.path.basename(file_path)

        # research-*.md のみ対象
        if not file_name.startswith('research-') or not file_name.endswith('.md'):
            return

        with _lock:
            # 重複発火防止（watchdogは同一ファイルで複数イベントを発火することがある）
            if file_path in processed_files:
                logger.debug(f"Already queued: {file_path}")
                return
            processed_files.add(file_path)

        logger.info(f"New research file detected: {file_path}")
        processing_queue.put(file_path)


def start_file_watcher(watch_dir: str) -> Observer:
    """ファイル監視を開始する"""
    handler = ResearchFileHandler()
    observer = Observer()
    observer.schedule(handler, watch_dir, recursive=False)
    observer.start()
    logger.info(f"Watching: {watch_dir}")
    return observer
```

### 重複発火の問題と解決

watchdogは同一ファイルへの書き込みで複数の `on_created` イベントを発火させることがあります（エディタが一時ファイルを介して保存する場合など）。

`processed_files` セットによる管理で解決しましたが、再処理を許容する場合はTTL付きキャッシュを使うとよいです。

```python
from datetime import datetime, timedelta
from collections import OrderedDict

class TTLSet:
    """TTL付き重複チェック用セット"""
    def __init__(self, ttl_seconds: int = 60):
        self._store: OrderedDict[str, datetime] = OrderedDict()
        self._ttl = timedelta(seconds=ttl_seconds)

    def contains(self, key: str) -> bool:
        self._evict_expired()
        return key in self._store

    def add(self, key: str) -> None:
        self._store[key] = datetime.now()

    def _evict_expired(self) -> None:
        now = datetime.now()
        expired = [k for k, t in self._store.items() if now - t > self._ttl]
        for k in expired:
            del self._store[k]
```

---

## Zenn slug制約への対応

Zennのslugには制約があります。

- 使える文字: `a-z`, `0-9`, `-`
- 長さ: 12〜50文字
- ハイフンで始まる・終わるスラッグは不可

BigQueryのデータには日本語や記号が混入する可能性があるため、バリデーションを追加しました。

```python
import re
import unicodedata


def sanitize_slug(raw_slug: str) -> str:
    """Zennのslug制約に合わせてsanitizeする"""
    # ひらがな・カタカナ・漢字を除去
    slug = unicodedata.normalize('NFKD', raw_slug)
    slug = slug.encode('ascii', 'ignore').decode('ascii')

    # 小文字に変換
    slug = slug.lower()

    # 使用可能文字以外を除去
    slug = re.sub(r'[^a-z0-9-]', '-', slug)

    # 連続するハイフンを1つに
    slug = re.sub(r'-+', '-', slug)

    # 先頭・末尾のハイフンを除去
    slug = slug.strip('-')

    # 長さ制約（12〜50文字）
    if len(slug) < 12:
        slug = slug + '-article'  # パディング
    if len(slug) > 50:
        slug = slug[:50].rstrip('-')

    return slug


# テスト
assert sanitize_slug("BigQuery×launchd活用術") == "bigquery-launchd"
assert len(sanitize_slug("a" * 60)) <= 50
```

---

## 実運用でわかったこと

### BQクライアントの生成コスト

最初の実装では、BQへのクエリごとに `bigquery.Client()` を生成していました。クライアントの初期化には認証処理が含まれており、実行ごとに1〜2秒のオーバーヘッドが生じます。

```python
# NG: 毎回クライアントを生成
def process():
    client = bigquery.Client()  # 毎回認証処理が走る
    rows = client.query("SELECT ...").result()
```

```python
# OK: シングルトンで使い回す
_bq_client: bigquery.Client | None = None

def get_bq_client() -> bigquery.Client:
    global _bq_client
    if _bq_client is None:
        _bq_client = bigquery.Client(project=PROJECT_ID)
    return _bq_client
```

### DAレビューで発見した問題

実装後にDAレビュー（Devil's Advocate）を実施し、以下の問題が指摘されました。

1. **watchdog重複発火 ** ：前述の通り、TTLセットで解決
2. **BQクライアント生成コスト ** ：シングルトンで解決
3. **Zenn slug制約違反 ** ：サニタイズ処理の追加で解決
4. **launchdのPATH問題 ** ：Pythonのフルパス指定で解決

---

## パイプラインの監視

### ログ確認

```bash
# リアルタイムログ監視
tail -f ~/dev/logs/pipeline-monitor.log

# エラーのみ抽出
grep "ERROR\|WARNING" ~/dev/logs/pipeline-monitor.log | tail -20

# 直近の処理件数
grep "Processed" ~/dev/logs/pipeline-monitor.log | tail -10
```

### BigQueryでの状況確認

```sql
-- 直近24時間のパイプライン処理状況
SELECT
  status,
  COUNT(*) AS count,
  AVG(score) AS avg_score,
FROM `project.content.content_suggestions`
WHERE created_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 24 HOUR)
GROUP BY status
ORDER BY count DESC;
```

---

## まとめ

BigQuery × launchd × watchdog の組み合わせにより、コンテンツ候補の検出から記事生成・公開まで全自動化できました。

| コンポーネント | 役割 | 代替案 |
|---|---|---|
| BigQuery | 候補の永続化・状態管理 | PostgreSQL, Firestore |
| launchd | 定期実行・自動再起動 | Cron, systemd |
| watchdog | ファイル変更検知 | inotify, FSEvents直接 |
| queue.Queue | スレッドセーフな処理キュー | asyncio.Queue |
| fcntl | 排他ロック | Redis, ファイルロック |

このパイプラインで特に重要だったのは「冪等性」の確保です。launchdの再起動やwatchdogの重複発火があっても、同じ記事が二重生成されないよう設計することが安定運用の鍵になりました。

### 関連記事

- [ObsidianとBigQueryを繋ぐナレッジパイプライン](https://zenn.dev/correlate_dev/articles/obsidian-bigquery-sync)
- [macのlaunchd完全ガイド — CronからlaunchAgentへ](https://zenn.dev/correlate_dev/articles/launchagent-macos-cron)
