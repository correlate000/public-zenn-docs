---
title: "Zenn記事を毎日3本自動公開する仕組み — GitHub Actions + Pythonキューで実現"
emoji: "📅"
type: "tech"
topics: ["zenn", "githubactions", "python", "automation", "contentpipeline"]
published: false
status: "draft"
publication_name: "correlate_dev"
---

## はじめに

ストック記事が50本以上たまっているのに、公開のタイミングを決められずに放置している——そんな状況に陥っていました。

Zennには1日あたりのレートリミットがあります。一気に大量公開するとレートリミットに引っかかる可能性がある上に、フィード上でも埋もれてしまいます。かといって毎日手動で公開するのは継続できない。

そこで構築したのが、 ** 優先度付きキューから毎日3本ずつ自動公開するGitHub Actionsワークフロー ** です。この記事では設計思想から実装コードまで解説します。

## システム全体の構成

```
publish-queue.txt     ← 公開待ち記事のキュー（優先度順）
     ↓
daily-publish.py      ← キューを読み取りFront Matterを書き換えるPythonスクリプト
     ↓
daily-publish.yml     ← 毎朝9時に自動実行するGitHub Actionsワークフロー
     ↓
Zenn                  ← GitHubにpushされると自動でZennに反映
```

## キューファイルの設計

`scripts/publish-queue.txt` がシステムの中核です。

```
# Tier1: AI/最新技術（最優先）
articles/claude-code-agent-teams-benchmark.md
articles/supabase-rls-silent-failure-nextjs-api.md
articles/nextjs-app-router-cache-pitfalls.md

# Tier2: 開発ツール・環境
articles/github-actions-reusable-workflows.md
articles/devcontainer-advanced-config.md

# Tier3: フレームワーク・ライブラリ
articles/react-server-components-deep-dive.md
articles/tailwind-v4-migration-guide.md

# Tier4: インフラ・クラウド
articles/cloud-run-job-scheduler-pattern.md
articles/bigquery-cost-optimization.md

# Tier5: WordPress/CMS
articles/wordpress-htaccess-redirect-placement.md
articles/zenn-markdown-bold-lint-fix.md

# Tier6: ライティング・コンテンツ
articles/ai-writing-rhythm-taigendome-technique.md

# Tier7: その他
articles/zenn-auto-publish-github-actions.md
```

Tier1（AI/最新技術）から順に公開することで、トレンド性の高い記事が先に露出される設計です。`#` で始まる行はコメントとして無視されます。

## Pythonスクリプト（daily-publish.py）

```python
#!/usr/bin/env python3
"""
Zenn記事の自動公開スクリプト
publish-queue.txtから指定本数の記事をpublished: trueに変更する
"""

import sys
import argparse
from pathlib import Path
import re

QUEUE_FILE = Path("scripts/publish-queue.txt")
ARTICLES_DIR = Path("articles")
DEFAULT_COUNT = 3


def load_queue() -> list[str]:
    """キューファイルから公開待ち記事のパスを読み込む"""
    if not QUEUE_FILE.exists():
        print(f"Error: {QUEUE_FILE} が見つかりません", file=sys.stderr)
        sys.exit(1)
    
    queue = []
    with open(QUEUE_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            # 空行・コメント行をスキップ
            if not line or line.startswith("#"):
                continue
            queue.append(line)
    
    return queue


def is_published(article_path: Path) -> bool:
    """記事がすでにpublished: trueかどうかを確認する"""
    content = article_path.read_text(encoding="utf-8")
    # Front Matterのpublishedフィールドを検索
    match = re.search(r"^published:\s*(true|false)", content, re.MULTILINE)
    if match:
        return match.group(1) == "true"
    return False


def set_published(article_path: Path, dry_run: bool = False) -> bool:
    """記事のpublishedをtrueに変更する"""
    content = article_path.read_text(encoding="utf-8")
    
    if is_published(article_path):
        print(f"  SKIP (already published): {article_path}")
        return False
    
    new_content = re.sub(
        r"^(published:\s*)false",
        r"\1true",
        content,
        flags=re.MULTILINE
    )
    
    if dry_run:
        print(f"  DRY-RUN: would publish {article_path}")
        return True
    
    article_path.write_text(new_content, encoding="utf-8")
    print(f"  PUBLISHED: {article_path}")
    return True


def remove_from_queue(published_paths: list[str]) -> None:
    """公開済み記事をキューから削除する"""
    queue_content = QUEUE_FILE.read_text(encoding="utf-8")
    
    for path in published_paths:
        # 行単位で削除（コメント行は保持）
        queue_content = re.sub(
            rf"^{re.escape(path)}\n?",
            "",
            queue_content,
            flags=re.MULTILINE
        )
    
    QUEUE_FILE.write_text(queue_content, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Zenn記事の自動公開")
    parser.add_argument(
        "--count", "-n",
        type=int,
        default=DEFAULT_COUNT,
        help=f"公開する記事数（デフォルト: {DEFAULT_COUNT}）"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="実際には変更せずに確認のみ"
    )
    args = parser.parse_args()
    
    queue = load_queue()
    
    if not queue:
        print("キューが空です。公開する記事はありません。")
        sys.exit(0)
    
    print(f"キュー内の記事数: {len(queue)}")
    print(f"公開予定数: {args.count}")
    if args.dry_run:
        print("DRY-RUNモード: ファイルは変更されません")
    print()
    
    published_paths = []
    publish_count = 0
    
    for article_rel_path in queue:
        if publish_count >= args.count:
            break
        
        article_path = Path(article_rel_path)
        
        if not article_path.exists():
            print(f"  WARNING: ファイルが見つかりません: {article_path}")
            continue
        
        if is_published(article_path):
            # すでに公開済みなのにキューに残っている場合はキューから削除
            print(f"  CLEANUP: キューから削除（公開済み）: {article_path}")
            published_paths.append(article_rel_path)
            continue
        
        if set_published(article_path, dry_run=args.dry_run):
            publish_count += 1
            if not args.dry_run:
                published_paths.append(article_rel_path)
    
    print()
    print(f"公開完了: {publish_count}件")
    
    if published_paths and not args.dry_run:
        remove_from_queue(published_paths)
        print(f"キューから削除: {len(published_paths)}件")
    
    # 残りのキュー件数を表示
    remaining = load_queue()
    print(f"残りキュー: {len(remaining)}件")


if __name__ == "__main__":
    main()
```

## GitHub Actionsワークフロー

`.github/workflows/daily-publish.yml` を以下のように設定します。

```yaml
name: Daily Zenn Publish

on:
  # 毎朝9時（JST）に自動実行 = UTC 0時
  schedule:
    - cron: "0 0 * * *"
  
  # 手動実行にも対応
  workflow_dispatch:
    inputs:
      count:
        description: "公開する記事数"
        required: false
        default: "3"
        type: string
      dry_run:
        description: "DRY-RUNモード（実際には公開しない）"
        required: false
        default: false
        type: boolean

jobs:
  publish:
    runs-on: ubuntu-latest
    
    permissions:
      contents: write  # pushに必要
    
    steps:
      - name: Checkout
        uses: actions/checkout@v4
        with:
          token: ${{ secrets.GITHUB_TOKEN }}
      
      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      
      - name: Run publish script
        run: |
          COUNT="${{ github.event.inputs.count || '3' }}"
          DRY_RUN="${{ github.event.inputs.dry_run || 'false' }}"
          
          if [ "$DRY_RUN" = "true" ]; then
            python scripts/daily-publish.py --count "$COUNT" --dry-run
          else
            python scripts/daily-publish.py --count "$COUNT"
          fi
      
      - name: Commit and push if changed
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          
          # 変更がある場合のみcommit
          if [ -n "$(git status --porcelain)" ]; then
            git add .
            git commit -m "chore: auto-publish $(date '+%Y-%m-%d') [skip ci]"
            git push
          else
            echo "No changes to commit"
          fi
```

## 手動実行とdry-runオプション

GitHub ActionsのUI（または `gh` CLI）から手動実行が可能です。

```bash
# gh CLIで手動実行（dry-run確認）
gh workflow run daily-publish.yml \
  -f count=5 \
  -f dry_run=true

# 本数を変えて手動実行
gh workflow run daily-publish.yml \
  -f count=10
```

新しい記事をまとめてキューに追加したタイミングで、一時的に多めに公開することができます。

## レートリミット対策

Zennの公式ドキュメントには明示されていませんが、短時間に大量のpushを行うと反映が遅延することがあります。以下の点に注意してください。

- 1日の公開数は5本以下を目安にする（安全マージンとして3本を採用）
- commit message に `[skip ci]` を付けてCI/CDの二重起動を防ぐ
- `workflow_dispatch` を使って手動介入できる設計にしておく

## キューの管理フロー

```
新記事を書く
    ↓
articles/ に追加（published: false）
    ↓
publish-queue.txt の適切なTierに追記
    ↓
自動公開を待つ（毎朝9時に3本ずつ処理）
    ↓
公開されたらキューから自動削除
```

## 実装上のポイント

### Front Matter の正規表現

`published: false` を `published: true` に書き換える際、Pythonの正規表現で Front Matter を正確に操作します。

```python
new_content = re.sub(
    r"^(published:\s*)false",
    r"\1true",
    content,
    flags=re.MULTILINE  # 行単位でマッチ
)
```

`re.MULTILINE` フラグで各行の先頭（`^`）にマッチさせることが重要です。これがないと文字列全体の先頭にしかマッチしません。

### すでに公開済みの記事をキューに残した場合

`is_published()` で事前チェックして、公開済みならスキップ＆キューからクリーンアップする設計にしています。手動で `published: true` に変えた記事がキューに残り続けることを防げます。

### `[skip ci]` コミット

自動公開のcommitがさらにCIをトリガーしないよう、`[skip ci]` をcommit messageに含めます。GitHub Actionsはこれを認識してワークフローをスキップします。

## まとめ

Zenn記事の自動公開システムの全体像をまとめます。

| コンポーネント | ファイル | 役割 |
|--------------|---------|------|
| キュー | `scripts/publish-queue.txt` | 公開待ち記事の優先度管理 |
| スクリプト | `scripts/daily-publish.py` | Front Matter書き換えとキュー更新 |
| ワークフロー | `.github/workflows/daily-publish.yml` | 毎朝9時の自動実行 |

ストック記事が多い場合、このシステムによって「毎日継続的にコンテンツを公開し続ける」状態を自動化できます。Tier分けによる優先度管理も、トレンド性の高い記事を先に出す上で効果的です。

コードは全てリポジトリに含まれているため、フォークして自分のZenn用リポジトリに適用することもできます。

## 参考

- [ZennのGitHub連携](https://zenn.dev/zenn/articles/connect-to-github)
- [GitHub Actions schedule トリガー](https://docs.github.com/actions/writing-workflows/choosing-when-your-workflow-runs/events-that-trigger-workflows#schedule)
- [workflow_dispatch イベント](https://docs.github.com/actions/writing-workflows/choosing-when-your-workflow-runs/events-that-trigger-workflows#workflow_dispatch)
