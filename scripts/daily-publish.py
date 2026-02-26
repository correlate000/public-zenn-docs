#!/usr/bin/env python3
"""
daily-publish.py — 優先度キューから毎日N本ずつ published: false → true に変更する

レートリミット対策:
  - 過去24hのデプロイ数をgit logから取得し、残り枠のみ公開
  - 過去に失敗（ロールバック）した記事は48hクールダウン
  - デフォルト公開数: 2本（安全マージン確保）

使い方:
  python3 scripts/daily-publish.py          # デフォルト2本公開
  python3 scripts/daily-publish.py --dry-run # 確認のみ（変更なし）
  python3 scripts/daily-publish.py --count 3 # 3本公開
"""
import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
ARTICLES_DIR = REPO_ROOT / "articles"
QUEUE_FILE = Path(__file__).parent / "publish-queue.txt"
FAILURE_LOG = Path(__file__).parent / ".publish-failures.json"
DAILY_LIMIT = 4  # Zenn公式上限5、安全マージン1
COOLDOWN_HOURS = 48
JST = timezone(timedelta(hours=9))


def load_queue() -> list[str]:
    """キューファイルからスラグリストを読み込む（コメント・空行を除外）"""
    slugs = []
    with open(QUEUE_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                slugs.append(line)
    return slugs


def is_draft(article_path: Path) -> bool:
    """記事が published: false かどうか確認"""
    content = article_path.read_text(encoding="utf-8")
    return bool(re.search(r"^published:\s*false\s*$", content, re.MULTILINE))


def publish_article(article_path: Path) -> bool:
    """published: false → true に変更（1箇所のみ）"""
    content = article_path.read_text(encoding="utf-8")
    new_content, count = re.subn(
        r"^(published:\s*)false(\s*)$",
        r"\1true\2",
        content,
        count=1,
        flags=re.MULTILINE,
    )
    if count == 0:
        return False
    article_path.write_text(new_content, encoding="utf-8")
    return True


def count_recent_deploys() -> int:
    """過去24hにdaily-publishで公開された記事数をgit logから取得（ロールバック除外）"""
    try:
        # daily-publish/予約公開コミットのみカウント（ロールバックコミットを除外）
        result = subprocess.run(
            ["git", "log", "--since=24 hours ago",
             "--grep=自動公開", "--grep=予約記事を自動公開",
             "--name-only", "--pretty=format:", "--", "articles/*.md"],
            capture_output=True, text=True, cwd=REPO_ROOT, timeout=10,
        )
        files = set(
            line.strip() for line in result.stdout.splitlines() if line.strip()
        )
        return len(files)
    except Exception as e:
        print(f"  [WARN] git log 取得失敗（安全側で上限適用）: {e}", file=sys.stderr)
        return DAILY_LIMIT  # 取得失敗時は公開しない（安全側）


def load_failures() -> dict:
    """失敗ログを読み込み"""
    if not FAILURE_LOG.exists():
        return {}
    try:
        with open(FAILURE_LOG, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def is_in_cooldown(slug: str, failures: dict) -> bool:
    """記事が失敗クールダウン中か判定"""
    if slug not in failures:
        return False
    try:
        last_failed = datetime.fromisoformat(failures[slug]["last_failed"])
        # タイムゾーン情報がない場合はJSTと仮定
        if last_failed.tzinfo is None:
            last_failed = last_failed.replace(tzinfo=JST)
        return datetime.now(JST) - last_failed < timedelta(hours=COOLDOWN_HOURS)
    except (KeyError, ValueError):
        return False


def main():
    parser = argparse.ArgumentParser(description="Zenn 下書き記事を自動公開")
    parser.add_argument("--dry-run", action="store_true", help="変更せずに確認のみ")
    parser.add_argument("--count", type=int, default=2, help="公開本数（デフォルト: 2）")
    args = parser.parse_args()

    # レートリミット計算
    recent_deploys = count_recent_deploys()
    remaining_quota = max(0, DAILY_LIMIT - recent_deploys)
    actual_count = min(args.count, remaining_quota)

    print(f"過去24hデプロイ数: {recent_deploys} / 日次上限: {DAILY_LIMIT}")
    print(f"残り枠: {remaining_quota} → 公開予定: {actual_count}")

    if actual_count == 0:
        print("レートリミットに到達済み。本日の公開をスキップします。")
        sys.exit(0)

    # 失敗ログ読み込み
    failures = load_failures()

    queue = load_queue()
    to_publish = []
    skipped_cooldown = []

    for slug in queue:
        article_path = ARTICLES_DIR / f"{slug}.md"
        if not article_path.exists():
            continue
        if not is_draft(article_path):
            continue
        if is_in_cooldown(slug, failures):
            skipped_cooldown.append(slug)
            continue
        to_publish.append((slug, article_path))
        if len(to_publish) >= actual_count:
            break

    if skipped_cooldown:
        print(f"クールダウン中（{COOLDOWN_HOURS}h）: {', '.join(skipped_cooldown)}")

    if not to_publish:
        print("公開待ち記事なし（キュー完了、すべて公開済み、またはクールダウン中）")
        sys.exit(0)

    print(f"{'[DRY RUN] ' if args.dry_run else ''}公開予定 {len(to_publish)} 本:")
    for slug, path in to_publish:
        print(f"  - {slug}")

    if args.dry_run:
        print("\n--dry-run のため変更しません")
        sys.exit(0)

    published = []
    for slug, path in to_publish:
        if publish_article(path):
            published.append(slug)
            print(f"  ✓ {slug}: published: false → true")
        else:
            print(f"  ✗ {slug}: 変更失敗（前後確認を）", file=sys.stderr)

    print(f"\n完了: {len(published)} 本を公開しました")


if __name__ == "__main__":
    main()
