#!/usr/bin/env python3
"""
Zenn公開状態検証スクリプト（GitHub Actions用）

published: true の記事が実際にZennに公開されているか確認し、
公開されていない記事をリトライキューに追加する。

レートリミット対策:
  - 猶予期間: 最終変更から6時間以内の記事はスキップ（デプロイ待ち）
  - 失敗記録: ロールバック時に .publish-failures.json に記録
  - daily-publish.py が失敗記事を48hクールダウンで回避

Usage:
    python3 zenn-verify-published.py [--fix]
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List

import requests

# 設定（GitHub Actions環境対応）
WORKSPACE = Path(os.getenv("GITHUB_WORKSPACE", "."))
ARTICLES_DIR = WORKSPACE / "articles"
RETRY_QUEUE_FILE = WORKSPACE / ".github/scripts/.zenn-retry-queue.json"
FAILURE_LOG = WORKSPACE / "scripts" / ".publish-failures.json"
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL_CONTENT", "")
GRACE_PERIOD_HOURS = 6
JST = timezone(timedelta(hours=9))


def extract_front_matter(file_path: Path) -> Dict[str, str]:
    """front matterを抽出"""
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not match:
        return {}

    front_matter = {}
    for line in match.group(1).split("\n"):
        if ":" in line:
            key, value = line.split(":", 1)
            front_matter[key.strip()] = value.strip().strip('"')

    return front_matter


def check_published_on_zenn(slug: str, username: str = "correlate") -> bool | None:
    """Zennで実際に公開されているか確認. ネットワークエラー時は None."""
    url = f"https://zenn.dev/{username}/articles/{slug}"
    try:
        response = requests.head(url, timeout=10, allow_redirects=True)
        return response.status_code in [200, 301]
    except Exception as e:
        print(f"  [WARN] Failed to check {slug}: {e}", file=sys.stderr)
        return None


def get_file_modified_hours_ago(file_path: Path) -> float | None:
    """git logからファイルの最終コミット時刻を取得し、経過時間を返す"""
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%aI", "--", str(file_path)],
            capture_output=True,
            text=True,
            cwd=WORKSPACE,
            timeout=10,
        )
        if result.stdout.strip():
            modified = datetime.fromisoformat(result.stdout.strip())
            delta = datetime.now(timezone.utc) - modified
            return delta.total_seconds() / 3600
    except Exception:
        pass
    return None


def rollback_published_flag(file_path: Path):
    """published: true → false にロールバック + published_at も削除"""
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    # published: true → false
    updated = re.sub(
        r"^published:\s*true", "published: false", content, flags=re.MULTILINE
    )

    # published_at も削除（published: false + published_at は無効な組み合わせ）
    updated = re.sub(r"^published_at:.*\n", "", updated, flags=re.MULTILINE)

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(updated)


def load_retry_queue() -> List[Dict]:
    """リトライキューを読み込み"""
    if not RETRY_QUEUE_FILE.exists():
        return []

    with open(RETRY_QUEUE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_retry_queue(queue: List[Dict]):
    """リトライキューを保存"""
    RETRY_QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(RETRY_QUEUE_FILE, "w", encoding="utf-8") as f:
        json.dump(queue, f, indent=2, ensure_ascii=False)


def load_failures() -> dict:
    """失敗ログを読み込み"""
    if not FAILURE_LOG.exists():
        return {}
    try:
        with open(FAILURE_LOG, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, Exception):
        return {}


def save_failures(failures: dict):
    """失敗ログを保存"""
    FAILURE_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(FAILURE_LOG, "w", encoding="utf-8") as f:
        json.dump(failures, f, indent=2, ensure_ascii=False)


def record_failure(slug: str, failures: dict):
    """失敗を記録（カウント+タイムスタンプ）"""
    now_str = datetime.now(JST).isoformat()
    if slug in failures:
        failures[slug]["count"] += 1
        failures[slug]["last_failed"] = now_str
    else:
        failures[slug] = {"count": 1, "last_failed": now_str}


def send_discord_notification(failed_articles: List[Dict]):
    """Discord通知送信"""
    if not failed_articles or not DISCORD_WEBHOOK_URL:
        return

    article_list = "\n".join(
        [f"- `{a['slug']}` ({a['title'][:30]}...)" for a in failed_articles]
    )

    message = f"""🚨 **Zenn公開失敗検知**

以下の記事が `published: true` ですが、Zennに公開されていません:

{article_list}

**対処**: ロールバック + 48hクールダウン適用済み。自動リトライされます。

**確認**: https://zenn.dev/dashboard
"""

    try:
        requests.post(
            DISCORD_WEBHOOK_URL, json={"content": message}, timeout=10
        )
    except Exception as e:
        print(f"Discord通知エラー: {e}", file=sys.stderr)


def main():
    fix_mode = "--fix" in sys.argv

    print(f"Zenn公開状態検証開始: {datetime.now(JST).isoformat()}")
    print(f"Articles dir: {ARTICLES_DIR}")
    print(f"Fix mode: {'ON' if fix_mode else 'OFF'}")
    print(f"Grace period: {GRACE_PERIOD_HOURS}h")

    failed_articles = []
    retry_queue = load_retry_queue()
    failures = load_failures()

    now = datetime.now(JST)

    for article_file in ARTICLES_DIR.glob("*.md"):
        front_matter = extract_front_matter(article_file)

        if front_matter.get("published") != "true":
            continue

        slug = front_matter.get("slug", article_file.stem)
        title = front_matter.get("title", slug)

        # published_at が未来の場合はスキップ（予約公開待ち）
        published_at_str = front_matter.get("published_at", "")
        if published_at_str:
            try:
                published_at = datetime.strptime(
                    published_at_str, "%Y-%m-%d %H:%M"
                ).replace(tzinfo=JST)
                if published_at > now:
                    print(f"Checking: {slug}... ⏳ SCHEDULED ({published_at_str})")
                    continue
            except ValueError:
                pass  # パースできない場合は通常チェック

        # 猶予期間: 最終コミットから6h以内の記事はスキップ
        hours_ago = get_file_modified_hours_ago(article_file)
        if hours_ago is not None and hours_ago < GRACE_PERIOD_HOURS:
            print(
                f"Checking: {slug}... ⏳ GRACE PERIOD ({hours_ago:.1f}h ago, need {GRACE_PERIOD_HOURS}h)"
            )
            continue

        print(f"Checking: {slug}...", end=" ")

        result = check_published_on_zenn(slug)
        if result is True:
            # 公開成功 → 失敗ログから削除
            if slug in failures:
                del failures[slug]
            print("✅ OK")
        elif result is None:
            print("⚠️ NETWORK ERROR (skipped)")
        else:
            print("❌ NOT PUBLISHED")
            failed_articles.append(
                {
                    "slug": slug,
                    "title": title,
                    "file": str(article_file.relative_to(WORKSPACE)),
                    "detected_at": now.isoformat(),
                }
            )

            # 失敗記録
            record_failure(slug, failures)

            if fix_mode:
                rollback_published_flag(article_file)
                print(f"  → Rolled back to published: false")

    # 失敗ログの肥大化防止: 失敗10回以上の記事は手動対応案件として除外
    permanent_failures = [
        slug for slug, data in failures.items() if data.get("count", 0) >= 10
    ]
    for slug in permanent_failures:
        print(f"  ⚠️ {slug}: 失敗10回以上 → 手動対応が必要です")
        del failures[slug]

    # 失敗ログ保存
    save_failures(failures)

    # リトライキューに追加（30日超のエントリは自動削除）
    cutoff = (now - timedelta(days=30)).isoformat()
    retry_queue = [
        item for item in retry_queue
        if item.get("detected_at", now.isoformat()) > cutoff
    ]
    existing_slugs = {item["slug"] for item in retry_queue}
    for article in failed_articles:
        if article["slug"] not in existing_slugs:
            retry_queue.append(article)

    save_retry_queue(retry_queue)

    # Discord通知
    if failed_articles:
        send_discord_notification(failed_articles)
        print(f"\n🚨 {len(failed_articles)}件の公開失敗を検知しました")
        print(f"リトライキュー: {len(retry_queue)}件")
        print(f"失敗記録: {len(failures)}件（48hクールダウン適用）")
    else:
        print("\n✅ 全記事が正常に公開されています")

    print(f"検証完了: {datetime.now(JST).isoformat()}")


if __name__ == "__main__":
    main()
