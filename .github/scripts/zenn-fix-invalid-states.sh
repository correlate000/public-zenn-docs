#!/bin/bash
# published: false + published_at の組み合わせを一括修正

set -e

cd "$(dirname "$0")/../.."

echo "🔧 Zenn記事の無効な状態を一括修正..."

fixed=0

for f in articles/*.md; do
  if [ ! -f "$f" ]; then
    continue
  fi

  published=$(grep "^published:" "$f" | head -1 | awk '{print $2}')
  has_published_at=$(grep -c "^published_at:" "$f" || true)

  if [ "$published" = "false" ] && [ "$has_published_at" -gt 0 ]; then
    slug=$(basename "$f" .md)
    echo "  Fixing: $slug"
    sed -i '' '/^published_at:/d' "$f"
    ((fixed++))
  fi
done

if [ $fixed -eq 0 ]; then
  echo "✅ 修正対象なし（全記事正常）"
else
  echo "✅ $fixed 記事を修正"
fi
