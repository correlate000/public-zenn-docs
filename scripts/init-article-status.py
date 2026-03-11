#!/usr/bin/env python3
"""
init-article-status.py — 既存記事に status フィールドを一括追加する

一度だけ実行するマイグレーションスクリプト。

ルール:
  - published: true → status: "published"
  - published: false → status: "draft"
  - 既に status がある記事はスキップ

使い方:
  python3 scripts/init-article-status.py            # 確認のみ
  python3 scripts/init-article-status.py --apply     # 実行
"""
import argparse
import re
import sys
from pathlib import Path

ARTICLES_DIR = Path(__file__).resolve().parent.parent / "articles"


def has_status(content: str) -> bool:
    """front matter 内に status フィールドがあるか（本文中の status: は無視）"""
    lines = content.split("\n")
    if not lines or lines[0].strip() != "---":
        return False
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if re.match(r"^status:", line):
            return True
    return False


def get_published(content: str) -> str | None:
    """front matter 内の published の値を取得（本文中の published: は無視）"""
    lines = content.split("\n")
    if not lines or lines[0].strip() != "---":
        return None
    for line in lines[1:]:
        if line.strip() == "---":
            break
        m = re.match(r"^published:\s*(\S+)", line)
        if m:
            return m.group(1).strip('"').strip("'")
    return None


def add_status(filepath: Path, apply: bool) -> str | None:
    """status フィールドを frontmatter 内の published 行直後に追加.

    変更内容を返す（変更なしは None）。
    frontmatter の範囲内のみを操作し、本文を改変しない。
    """
    content = filepath.read_text(encoding="utf-8")

    if has_status(content):
        return None

    published = get_published(content)
    if published is None:
        return None

    status = "published" if published == "true" else "draft"

    # frontmatter の範囲を特定してから置換
    lines = content.split("\n")
    if not lines or lines[0].strip() != "---":
        return None

    fm_end = None
    for i, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            fm_end = i
            break
    if fm_end is None:
        return None

    # frontmatter 内の published 行を探し、直後に status を挿入
    new_lines = []
    inserted = False
    for i, line in enumerate(lines):
        new_lines.append(line)
        if not inserted and 1 <= i < fm_end and re.match(r"^published:\s*\S+", line):
            new_lines.append(f'status: "{status}"')
            inserted = True

    if not inserted:
        return None

    new_content = "\n".join(new_lines)
    if new_content == content:
        return None

    if apply:
        filepath.write_text(new_content, encoding="utf-8")

    return status


def main():
    parser = argparse.ArgumentParser(description="既存記事に status フィールドを追加")
    parser.add_argument("--apply", action="store_true", help="実際にファイルを変更")
    args = parser.parse_args()

    if not ARTICLES_DIR.exists():
        print(f"ERROR: {ARTICLES_DIR} が見つかりません")
        sys.exit(1)

    articles = sorted(ARTICLES_DIR.glob("*.md"))
    counts = {"published": 0, "draft": 0, "skipped": 0}

    for article in articles:
        result = add_status(article, args.apply)
        if result is None:
            counts["skipped"] += 1
        else:
            counts[result] += 1
            prefix = "UPDATED" if args.apply else "WOULD UPDATE"
            print(f"  {prefix}: {article.name} → status: \"{result}\"")

    print(f"\n合計: {len(articles)} 記事")
    print(f"  published → status: \"published\": {counts['published']}")
    print(f"  draft → status: \"draft\": {counts['draft']}")
    print(f"  スキップ（既にstatusあり）: {counts['skipped']}")

    if not args.apply and (counts["published"] + counts["draft"]) > 0:
        print("\n--apply を付けて再実行すると、実際にファイルを変更します")


if __name__ == "__main__":
    main()
