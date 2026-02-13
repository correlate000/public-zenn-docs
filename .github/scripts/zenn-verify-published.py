#!/usr/bin/env python3
"""
Zenn公開状態検証スクリプト（GitHub Actions用）

published: true の記事が実際にZennに公開されているか確認し、
公開されていない記事をリトライキューに追加する。

Usage:
    python3 zenn-verify-published.py [--fix]
"""

import re
import sys
import json
import requests
import os
from pathlib import Path
from typing import List, Dict
from datetime import datetime

# 設定（GitHub Actions環境対応）
WORKSPACE = Path(os.getenv("GITHUB_WORKSPACE", "."))
ARTICLES_DIR = WORKSPACE / "articles"
RETRY_QUEUE_FILE = WORKSPACE / ".github/scripts/.zenn-retry-queue.json"
DISCORD_WEBHOOK_URL = os.getenv(
    "DISCORD_WEBHOOK_URL_CONTENT",
    "https://discordapp.com/api/webhooks/1471532255363993745/J6-1wN1WdV_wnkZU9nVSxcm4gX_WeQ6O-CaNKyMH4S32lB-OgiodvSnuFNYnZ_J70kjy"
)


def extract_front_matter(file_path: Path) -> Dict[str, str]:
    """front matterを抽出"""
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    match = re.match(r'^---\n(.*?)\n---', content, re.DOTALL)
    if not match:
        return {}

    front_matter = {}
    for line in match.group(1).split('\n'):
        if ':' in line:
            key, value = line.split(':', 1)
            front_matter[key.strip()] = value.strip().strip('"')

    return front_matter


def check_published_on_zenn(slug: str, username: str = "correlate") -> bool:
    """Zennで実際に公開されているか確認"""
    url = f"https://zenn.dev/{username}/articles/{slug}"
    try:
        response = requests.head(url, timeout=10, allow_redirects=True)
        return response.status_code in [200, 301]
    except Exception as e:
        print(f"  [WARN] Failed to check {slug}: {e}", file=sys.stderr)
        return False


def rollback_published_flag(file_path: Path):
    """published: true → false にロールバック"""
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    updated = re.sub(
        r'^published:\s*true',
        'published: false',
        content,
        flags=re.MULTILINE
    )

    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(updated)


def load_retry_queue() -> List[Dict]:
    """リトライキューを読み込み"""
    if not RETRY_QUEUE_FILE.exists():
        return []

    with open(RETRY_QUEUE_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_retry_queue(queue: List[Dict]):
    """リトライキューを保存"""
    RETRY_QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(RETRY_QUEUE_FILE, 'w', encoding='utf-8') as f:
        json.dump(queue, f, indent=2, ensure_ascii=False)


def send_discord_notification(failed_articles: List[Dict]):
    """Discord通知送信"""
    if not failed_articles or not DISCORD_WEBHOOK_URL:
        return

    article_list = "\n".join([
        f"- `{a['slug']}` ({a['title'][:30]}...)"
        for a in failed_articles
    ])

    message = f"""🚨 **Zenn公開失敗検知**

以下の記事が `published: true` ですが、Zennに公開されていません:

{article_list}

**対処**: リトライキューに追加しました。次回デプロイで自動再試行します。

**確認**: https://zenn.dev/dashboard
"""

    try:
        requests.post(
            DISCORD_WEBHOOK_URL,
            json={"content": message},
            timeout=10
        )
    except Exception as e:
        print(f"Discord通知エラー: {e}", file=sys.stderr)


def main():
    fix_mode = "--fix" in sys.argv

    print(f"Zenn公開状態検証開始: {datetime.now().isoformat()}")
    print(f"Articles dir: {ARTICLES_DIR}")
    print(f"Fix mode: {'ON' if fix_mode else 'OFF'}")

    failed_articles = []
    retry_queue = load_retry_queue()

    for article_file in ARTICLES_DIR.glob("*.md"):
        front_matter = extract_front_matter(article_file)

        if front_matter.get('published') != 'true':
            continue

        slug = front_matter.get('slug', article_file.stem)
        title = front_matter.get('title', slug)

        print(f"Checking: {slug}...", end=" ")

        if check_published_on_zenn(slug):
            print("✅ OK")
        else:
            print("❌ NOT PUBLISHED")
            failed_articles.append({
                "slug": slug,
                "title": title,
                "file": str(article_file.relative_to(WORKSPACE)),
                "detected_at": datetime.now().isoformat(),
            })

            if fix_mode:
                rollback_published_flag(article_file)
                print(f"  → Rolled back to published: false")

    # リトライキューに追加
    existing_slugs = {item['slug'] for item in retry_queue}
    for article in failed_articles:
        if article['slug'] not in existing_slugs:
            retry_queue.append(article)

    save_retry_queue(retry_queue)

    # Discord通知
    if failed_articles:
        send_discord_notification(failed_articles)
        print(f"\n🚨 {len(failed_articles)}件の公開失敗を検知しました")
        print(f"リトライキュー: {len(retry_queue)}件")
    else:
        print("\n✅ 全記事が正常に公開されています")

    print(f"検証完了: {datetime.now().isoformat()}")


if __name__ == "__main__":
    main()
