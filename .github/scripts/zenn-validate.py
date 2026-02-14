#!/usr/bin/env python3
"""
Zenn記事のバリデーション

published: false + published_at の組み合わせを検出し、
デプロイ前にエラーを防ぐ。

Usage:
    python3 zenn-validate.py
"""
import sys
import re
from pathlib import Path

ARTICLES_DIR = Path("articles")


def validate_articles() -> list[str]:
    """バリデーションエラーを検出"""
    errors = []

    for article_file in ARTICLES_DIR.glob("*.md"):
        content = article_file.read_text(encoding="utf-8")

        published_match = re.search(r'^published:\s*(\w+)', content, re.MULTILINE)
        published_at_match = re.search(r'^published_at:', content, re.MULTILINE)

        if published_match and published_at_match:
            published = published_match.group(1)
            if published == "false":
                published_at_line = re.search(
                    r'^published_at:\s*["\']?([^"\']+)["\']?',
                    content,
                    re.MULTILINE
                )
                published_at_value = published_at_line.group(1) if published_at_line else "Unknown"
                errors.append(
                    f"{article_file.name}: published: false + published_at: {published_at_value} は無効"
                )

    return errors


def main():
    print("🔍 Zenn記事バリデーション開始...")

    errors = validate_articles()

    if errors:
        print("\n❌ バリデーションエラー:")
        for error in errors:
            print(f"  - {error}")
        print("\n💡 修正方法:")
        print("  ./.github/scripts/zenn-fix-invalid-states.sh を実行")
        sys.exit(1)
    else:
        print("✅ 全記事バリデーション合格")
        sys.exit(0)


if __name__ == "__main__":
    main()
