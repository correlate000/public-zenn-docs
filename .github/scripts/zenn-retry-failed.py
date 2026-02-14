#!/usr/bin/env python3
"""
Zennリトライ処理スクリプト（GitHub Actions用）

リトライキューに記録された失敗記事を、
レートリミットを考慮しながら再公開する。

Usage:
    python3 zenn-retry-failed.py [--max N]
"""

import re
import sys
import json
import os
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict

# 設定（GitHub Actions環境対応）
WORKSPACE = Path(os.getenv("GITHUB_WORKSPACE", "."))
ARTICLES_DIR = WORKSPACE / "articles"
RETRY_QUEUE_FILE = WORKSPACE / ".github/scripts/.zenn-retry-queue.json"
MAX_ARTICLES_PER_RUN = 3  # 24時間に5本以内のため、保守的に3本


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


def schedule_article(file_path: Path, published_at: str):
    """記事のpublished_atを更新"""
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # published: false → true
    content = re.sub(
        r'^published:\s*false',
        'published: true',
        content,
        flags=re.MULTILINE
    )

    # published_at を更新
    if re.search(r'^published_at:', content, re.MULTILINE):
        content = re.sub(
            r'^published_at:.*$',
            f'published_at: "{published_at}"',
            content,
            flags=re.MULTILINE
        )
    else:
        content = re.sub(
            r'^(published: true)$',
            f'\\1\npublished_at: "{published_at}"',
            content,
            flags=re.MULTILINE
        )

    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)


def get_existing_scheduled_times() -> set:
    """既存記事のpublished_atを取得（競合チェック用）"""
    scheduled_times = set()

    for article_file in ARTICLES_DIR.glob("*.md"):
        with open(article_file, 'r', encoding='utf-8') as f:
            content = f.read()

        # published_at を抽出
        match = re.search(r'^published_at:\s*["\']?([^"\']+)["\']?', content, re.MULTILINE)
        if match:
            scheduled_times.add(match.group(1).strip())

    return scheduled_times


def calculate_next_slots(count: int) -> List[str]:
    """次の公開スロットを計算（既存記事と競合しないように）"""
    now = datetime.now()
    existing_times = get_existing_scheduled_times()

    print(f"既存の予約記事: {len(existing_times)}件")
    if existing_times:
        print(f"  例: {list(existing_times)[:3]}")

    slots = []
    time_slots = [
        (8, 0),   # 08:00
        (12, 30), # 12:30
        (19, 0),  # 19:00
    ]

    current_day = now.date()
    days_to_check = 14  # 2週間分チェック（余裕を持たせる）

    for day_offset in range(days_to_check):
        check_date = current_day + timedelta(days=day_offset)

        for hour, minute in time_slots:
            slot_time = datetime.combine(check_date, datetime.min.time()).replace(
                hour=hour, minute=minute
            )

            # 未来の時刻のみ
            if slot_time <= now:
                continue

            slot_str = slot_time.strftime("%Y-%m-%d %H:%M")

            # 既存の予約と重複していないスロットのみ
            if slot_str not in existing_times:
                slots.append(slot_str)
                print(f"  空きスロット発見: {slot_str}")

            if len(slots) >= count:
                return slots

    return slots


def main():
    max_articles = MAX_ARTICLES_PER_RUN
    if "--max" in sys.argv:
        idx = sys.argv.index("--max")
        if idx + 1 < len(sys.argv):
            max_articles = int(sys.argv[idx + 1])

    print(f"Zennリトライ処理開始: {datetime.now().isoformat()}")
    print(f"最大処理数: {max_articles}件")

    retry_queue = load_retry_queue()

    if not retry_queue:
        print("リトライキューは空です")
        return

    print(f"リトライキュー: {len(retry_queue)}件")

    to_process = retry_queue[:max_articles]
    remaining = retry_queue[max_articles:]

    next_slots = calculate_next_slots(len(to_process))

    print(f"\n処理する記事: {len(to_process)}件")
    for i, article in enumerate(to_process):
        file_path = WORKSPACE / article.get('file_path', article.get('file', ''))
        if not file_path.exists():
            print(f"  ❌ {article['slug']}: ファイルが見つかりません")
            continue

        scheduled_at = next_slots[i]
        schedule_article(file_path, scheduled_at)
        print(f"  ✅ {article['slug']}: {scheduled_at} に予約")

    save_retry_queue(remaining)

    print(f"\n残りのリトライキュー: {len(remaining)}件")
    print(f"リトライ処理完了: {datetime.now().isoformat()}")


if __name__ == "__main__":
    main()
