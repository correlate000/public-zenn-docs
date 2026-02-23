#!/usr/bin/env python3
"""
daily-publish.py — 優先度キューから毎日N本ずつ published: false → true に変更する

使い方:
  python3 scripts/daily-publish.py          # デフォルト3本公開
  python3 scripts/daily-publish.py --dry-run # 確認のみ（変更なし）
  python3 scripts/daily-publish.py --count 4 # 4本公開
"""
import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
ARTICLES_DIR = REPO_ROOT / "articles"
QUEUE_FILE = Path(__file__).parent / "publish-queue.txt"


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


def main():
    parser = argparse.ArgumentParser(description="Zenn 下書き記事を自動公開")
    parser.add_argument("--dry-run", action="store_true", help="変更せずに確認のみ")
    parser.add_argument("--count", type=int, default=3, help="公開本数（デフォルト: 3）")
    args = parser.parse_args()

    queue = load_queue()
    to_publish = []

    for slug in queue:
        article_path = ARTICLES_DIR / f"{slug}.md"
        if not article_path.exists():
            continue
        if is_draft(article_path):
            to_publish.append((slug, article_path))
        if len(to_publish) >= args.count:
            break

    if not to_publish:
        print("公開待ち記事なし（キュー完了またはすべて公開済み）")
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
