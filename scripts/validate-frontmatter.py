#!/usr/bin/env python3
"""Zenn記事 front matter バリデーション.

チェック項目:
  1. published_at スケジュール重複検出
  2. published: false + published_at の禁止組み合わせ
  3. front matter 形式統一（type/publication_name の引用符、topics のインライン配列形式）
  4. 24時間あたり5本上限チェック
  5. 必須フィールド存在確認

使い方:
  python3 scripts/validate-frontmatter.py          # 全記事チェック
  python3 scripts/validate-frontmatter.py --fix     # 自動修正モード
  python3 scripts/validate-frontmatter.py --ci      # CI用（エラー時 exit 1）

pre-commit hook としても動作:
  .githooks/pre-commit から呼び出し（git config core.hooksPath .githooks）
"""

import re
import sys
from collections import defaultdict
from pathlib import Path

ARTICLES_DIR = Path(__file__).resolve().parent.parent / "articles"

REQUIRED_FIELDS = {"title", "emoji", "type", "topics", "published", "publication_name"}
VALID_TYPES = {"tech", "idea"}
VALID_PUBLICATION = "correlate_dev"
MAX_PER_DAY = 5  # Zenn rate limit: 24h に 5本


def parse_frontmatter(filepath: Path) -> tuple[dict, list[str]]:
    """front matter をパースして dict と生行リストを返す."""
    lines = filepath.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, lines

    end_idx = None
    for i, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            end_idx = i
            break

    if end_idx is None:
        return {}, lines

    fm_lines = lines[1:end_idx]
    fm = {}
    fm["_raw_lines"] = fm_lines
    fm["_end_idx"] = end_idx

    for line in fm_lines:
        m = re.match(r'^(\w[\w_]*)\s*:\s*(.*)$', line)
        if m:
            key = m.group(1)
            val = m.group(2).strip()
            fm[key] = val

    return fm, lines


def check_title_emoji_quoting(fm: dict, filepath: Path) -> list[str]:
    """title, emoji の引用符チェック."""
    errors = []
    raw = "\n".join(fm.get("_raw_lines", []))

    for field in ("title", "emoji"):
        m = re.search(rf'^{field}:\s*(\S.*)$', raw, re.MULTILINE)
        if m:
            val = m.group(1).strip()
            if not (val.startswith('"') and val.endswith('"')):
                errors.append(f'  [FORMAT] {field} は引用符必須: {field}: "{val}" にすべき')

    return errors


def check_quoting(fm: dict, filepath: Path) -> list[str]:
    """type, publication_name の引用符 + 値チェック."""
    errors = []
    raw = "\n".join(fm.get("_raw_lines", []))

    # type should be quoted: type: "tech"
    type_match = re.search(r'^type:\s*(\S.*)$', raw, re.MULTILINE)
    if type_match:
        val = type_match.group(1).strip()
        if not (val.startswith('"') and val.endswith('"')):
            errors.append(f"  [FORMAT] type は引用符必須: type: \"{val}\" にすべき")
        # 値チェック
        type_val = val.strip('"').strip("'")
        if type_val not in VALID_TYPES:
            errors.append(f"  [VALUE] type は {VALID_TYPES} のいずれか: 現在 \"{type_val}\"")

    # publication_name should be quoted
    pub_match = re.search(r'^publication_name:\s*(\S.*)$', raw, re.MULTILINE)
    if pub_match:
        val = pub_match.group(1).strip()
        if not (val.startswith('"') and val.endswith('"')):
            errors.append(f'  [FORMAT] publication_name は引用符必須: publication_name: "{val}" にすべき')
        # 値チェック
        pub_val = val.strip('"').strip("'")
        if pub_val != VALID_PUBLICATION:
            errors.append(f'  [VALUE] publication_name は "{VALID_PUBLICATION}": 現在 "{pub_val}"')

    return errors


def check_topics_format(fm: dict, filepath: Path) -> list[str]:
    """topics がインライン配列形式 ["a", "b"] かチェック."""
    errors = []
    raw_lines = fm.get("_raw_lines", [])

    for i, line in enumerate(raw_lines):
        if line.startswith("topics:"):
            val = line[len("topics:"):].strip()
            if not val:
                # YAML list format detected
                errors.append("  [FORMAT] topics はインライン配列形式にすべき: topics: [\"a\", \"b\"]")
            elif not val.startswith("["):
                errors.append("  [FORMAT] topics はインライン配列形式にすべき: topics: [\"a\", \"b\"]")
            break

    return errors


def check_schedule_conflicts(all_articles: dict) -> list[str]:
    """published_at のスケジュール重複チェック."""
    errors = []
    schedule_map = defaultdict(list)

    for name, fm in all_articles.items():
        published_at = fm.get("published_at", "").strip('"').strip("'")
        if published_at:
            schedule_map[published_at].append(name)

    for dt, articles in schedule_map.items():
        if len(articles) > 1:
            names = ", ".join(articles)
            errors.append(f"  [CONFLICT] {dt} に {len(articles)} 記事が重複: {names}")

    return errors


def check_daily_limits(all_articles: dict) -> list[str]:
    """1日あたりの記事数上限チェック."""
    errors = []
    daily_map = defaultdict(list)

    for name, fm in all_articles.items():
        published_at = fm.get("published_at", "").strip('"').strip("'")
        if published_at:
            date_part = published_at.split(" ")[0]
            daily_map[date_part].append(name)

    for date, articles in daily_map.items():
        if len(articles) > MAX_PER_DAY:
            errors.append(f"  [RATE] {date} に {len(articles)} 記事（上限 {MAX_PER_DAY}）: {', '.join(articles)}")

    return errors


def check_published_combo(fm: dict, filepath: Path) -> list[str]:
    """published: false + published_at の禁止組み合わせ."""
    errors = []
    published = fm.get("published", "").strip('"').strip("'")
    published_at = fm.get("published_at", "")

    if published == "false" and published_at:
        errors.append("  [INVALID] published: false + published_at は Zenn エラー。published: true に変更必須")

    return errors


def check_required_fields(fm: dict, filepath: Path) -> list[str]:
    """必須フィールド存在チェック."""
    errors = []
    missing = REQUIRED_FIELDS - set(fm.keys())
    if missing:
        errors.append(f"  [MISSING] 必須フィールド不足: {', '.join(sorted(missing))}")
    return errors


def fix_frontmatter(filepath: Path) -> bool:
    """front matter を自動修正. 変更があれば True."""
    content = filepath.read_text(encoding="utf-8")
    original = content

    # Fix unquoted title
    content = re.sub(
        r'^(title:\s*)(?!")(.+)$',
        r'\1"\2"',
        content,
        count=1,
        flags=re.MULTILINE,
    )

    # Fix unquoted emoji
    content = re.sub(
        r'^(emoji:\s*)(?!")(\S+)\s*$',
        r'\1"\2"',
        content,
        count=1,
        flags=re.MULTILINE,
    )

    # Fix unquoted type
    content = re.sub(
        r'^(type:\s*)(?!")(\w+)\s*$',
        r'\1"\2"',
        content,
        count=1,
        flags=re.MULTILINE,
    )

    # Fix unquoted publication_name
    content = re.sub(
        r'^(publication_name:\s*)(?!")(\w+)\s*$',
        r'\1"\2"',
        content,
        count=1,
        flags=re.MULTILINE,
    )

    # Fix YAML list topics → inline array
    lines = content.split("\n")
    new_lines = []
    i = 0
    while i < len(lines):
        if lines[i].startswith("topics:") and not lines[i].strip().endswith("]"):
            # Collect YAML list items
            topics = []
            i += 1
            while i < len(lines) and re.match(r'^\s+-\s+', lines[i]):
                topic = re.sub(r'^\s+-\s+', '', lines[i]).strip().strip('"').strip("'")
                topics.append(f'"{topic}"')
                i += 1
            new_lines.append(f"topics: [{', '.join(topics)}]")
            continue
        new_lines.append(lines[i])
        i += 1

    content = "\n".join(new_lines)

    if content != original:
        filepath.write_text(content, encoding="utf-8")
        return True
    return False


def main():
    fix_mode = "--fix" in sys.argv
    ci_mode = "--ci" in sys.argv

    if fix_mode and ci_mode:
        print("⚠️  --fix と --ci は同時指定できません。--fix を優先します。")
        ci_mode = False

    if not ARTICLES_DIR.exists():
        print(f"ERROR: {ARTICLES_DIR} が見つかりません")
        sys.exit(1)

    articles = sorted(ARTICLES_DIR.glob("*.md"))
    all_fm = {}
    all_errors = {}
    total_errors = 0

    # Parse all articles
    for article in articles:
        fm, _ = parse_frontmatter(article)
        name = article.stem
        all_fm[name] = fm

        errors = []
        errors.extend(check_required_fields(fm, article))
        errors.extend(check_published_combo(fm, article))
        errors.extend(check_title_emoji_quoting(fm, article))
        errors.extend(check_quoting(fm, article))
        errors.extend(check_topics_format(fm, article))

        if errors:
            all_errors[name] = errors
            total_errors += len(errors)

    # Cross-article checks
    schedule_errors = check_schedule_conflicts(all_fm)
    daily_errors = check_daily_limits(all_fm)

    # Output
    if fix_mode:
        fixed_count = 0
        for article in articles:
            if fix_frontmatter(article):
                print(f"  FIXED: {article.name}")
                fixed_count += 1
        if fixed_count:
            print(f"\n{fixed_count} 件修正しました")
        else:
            print("修正対象なし")
        # 修正後にバリデーション再実行（スケジュール重複等は --fix で直せないため警告）
        remaining_errors = []
        remaining_errors.extend(schedule_errors)
        remaining_errors.extend(daily_errors)
        if remaining_errors:
            print("\n⚠️  --fix では修正できない問題が残っています:")
            for e in remaining_errors:
                print(e)
        return

    print(f"=== Zenn Front Matter Validation ({len(articles)} articles) ===\n")

    if not all_errors and not schedule_errors and not daily_errors:
        print("ALL PASS — 全記事の front matter が正常です")
        sys.exit(0)

    for name, errors in sorted(all_errors.items()):
        print(f"{name}.md:")
        for e in errors:
            print(e)
        print()

    if schedule_errors:
        print("Schedule:")
        for e in schedule_errors:
            print(e)
        print()

    if daily_errors:
        print("Rate Limit:")
        for e in daily_errors:
            print(e)
        print()

    print(f"--- {total_errors + len(schedule_errors) + len(daily_errors)} issues found ---")

    if ci_mode:
        sys.exit(1)


if __name__ == "__main__":
    main()
