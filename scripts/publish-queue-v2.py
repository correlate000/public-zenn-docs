#!/usr/bin/env python3
"""
publish-queue-v2.py — Zenn記事の自動公開スクリプト

status: publish-ready を優先し、次に draft 記事を1日1本、ランダム時刻に公開する。
未完成マーカー（TODO:, FIXME: 等）を含む記事は自動スキップする。

設計思想:
  Zenn AI コンテンツガイドライン（2026-03-10）対応。
  公開タイミングのみ自動化し、記事の品質管理はDAレビューで担保する。

タイミング制御:
  - 公開時刻: 日付ベースのハッシュで 8:00-21:00 JST に決定
  - 休日制御: 土日は 50% の確率でスキップ
  - 連続制御: 3日連続公開したら1日休む
  - レートリミット: 過去24hの公開数をチェック（上限4本/日）

使い方:
  python3 scripts/publish-queue-v2.py              # 通常実行（時刻チェックあり）
  python3 scripts/publish-queue-v2.py --dry-run    # 確認のみ（変更なし）
  python3 scripts/publish-queue-v2.py --force      # 時刻/休日チェックをスキップ（未完成チェックは維持）
  python3 scripts/publish-queue-v2.py --list       # 公開対象一覧表示
"""
import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
ARTICLES_DIR = REPO_ROOT / "articles"
FAILURE_LOG = Path(__file__).parent / ".publish-failures.json"
PUBLISH_HISTORY = Path(__file__).parent / ".publish-history.json"
DAILY_LIMIT = 4  # Zenn公式上限5、安全マージン1
COOLDOWN_HOURS = 48
MAX_CONSECUTIVE_DAYS = 3
JST = timezone(timedelta(hours=9))

# 公開時間帯（JST）
PUBLISH_HOUR_MIN = 8
PUBLISH_HOUR_MAX = 21

# 未完成記事の検出パターン（本文にこれらが含まれる記事はスキップ）
# 注: コードブロック内のマーカーも検出されるため、行頭パターンで判定する
DRAFT_MARKERS_EXACT = [
    "ドラフトはここで終端",
    "【未完成】",
    "別途追記が必要",
]
DRAFT_MARKERS_LINE_START = [
    "TODO:",
    "FIXME:",
    "WIP:",
]


def date_hash(date_str: str, salt: str = "correlate_dev") -> int:
    """日付文字列から決定的なハッシュ値を生成"""
    h = hashlib.sha256(f"{salt}:{date_str}".encode()).hexdigest()
    return int(h[:8], 16)


def get_publish_hour(today: datetime) -> int:
    """今日の公開時刻（JST）をハッシュで決定"""
    h = date_hash(today.strftime("%Y-%m-%d"), "publish_hour")
    return PUBLISH_HOUR_MIN + (h % (PUBLISH_HOUR_MAX - PUBLISH_HOUR_MIN + 1))


def should_skip_weekend(today: datetime) -> bool:
    """土日は50%の確率でスキップ"""
    if today.weekday() not in (5, 6):  # 月-金
        return False
    h = date_hash(today.strftime("%Y-%m-%d"), "weekend_skip")
    return h % 2 == 0


def load_publish_history() -> dict:
    """公開履歴を読み込み"""
    if not PUBLISH_HISTORY.exists():
        return {"dates": []}
    try:
        with open(PUBLISH_HISTORY, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"dates": []}


def save_publish_history(history: dict) -> None:
    """公開履歴を保存"""
    # 直近30日分のみ保持
    cutoff = (datetime.now(JST) - timedelta(days=30)).strftime("%Y-%m-%d")
    history["dates"] = [d for d in history["dates"] if d >= cutoff]
    with open(PUBLISH_HISTORY, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def should_skip_consecutive(history: dict, today: datetime) -> bool:
    """3日連続公開したら1日休む"""
    today_str = today.strftime("%Y-%m-%d")
    recent = []
    for i in range(1, MAX_CONSECUTIVE_DAYS + 1):
        d = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        recent.append(d in history.get("dates", []))
    return all(recent)


def parse_frontmatter(filepath: Path) -> dict:
    """front matter をパースして dict を返す"""
    lines = filepath.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    end_idx = None
    for i, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        return {}
    fm = {}
    for line in lines[1:end_idx]:
        m = re.match(r'^(\w[\w_]*)\s*:\s*(.*)$', line)
        if m:
            fm[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return fm


def has_draft_markers(filepath: Path) -> str | None:
    """記事本文に未完成マーカーが含まれていないかチェック.

    完全一致マーカー（ドラフト終端等）は本文中のどこにあっても検出。
    行頭マーカー（TODO: 等）は行頭にある場合のみ検出し、
    コードブロック内のサンプルコメントによる False Positive を軽減する。
    見つかった場合はマーカー文字列を返し、なければ None。
    """
    content = filepath.read_text(encoding="utf-8")
    for marker in DRAFT_MARKERS_EXACT:
        if marker in content:
            return marker
    for line in content.splitlines():
        stripped = line.strip()
        for marker in DRAFT_MARKERS_LINE_START:
            if stripped.startswith(marker):
                return marker
    return None


def find_publishable_articles() -> list[tuple[str, Path]]:
    """公開対象の記事を取得（ファイル名順）.

    status: publish-ready を優先し、次に draft を対象にする。
    未完成マーカーを含む記事はスキップする。
    """
    ready = []
    drafts = []
    for article in sorted(ARTICLES_DIR.glob("*.md")):
        fm = parse_frontmatter(article)
        if fm.get("published") != "false":
            continue
        # 未完成マーカーチェック
        marker = has_draft_markers(article)
        if marker:
            print(f"  スキップ（未完成）: {article.stem} — 「{marker}」を検出")
            continue
        status = fm.get("status", "draft")
        if status == "publish-ready":
            ready.append((article.stem, article))
        elif status == "draft":
            drafts.append((article.stem, article))
    return ready + drafts


def publish_article(article_path: Path) -> bool:
    """published: false → true, status: publish-ready → published に変更.

    frontmatter の範囲内のみを操作し、本文中の同名フィールドを誤置換しない。
    """
    content = article_path.read_text(encoding="utf-8")
    lines = content.split("\n")

    if not lines or lines[0].strip() != "---":
        return False

    fm_end = None
    for i, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            fm_end = i
            break
    if fm_end is None:
        return False

    published_changed = False
    status_changed = False
    for i in range(1, fm_end):
        # published: false → true
        if re.match(r"^published:\s*false\s*$", lines[i]):
            lines[i] = re.sub(r"^(published:\s*)false", r"\1true", lines[i])
            published_changed = True
        # status: "publish-ready" or "draft" → "published"
        if re.match(r'^status:\s*"?(publish-ready|draft)"?\s*$', lines[i]):
            lines[i] = re.sub(
                r'^(status:\s*)"?(publish-ready|draft)"?',
                r'\1"published"',
                lines[i],
            )
            status_changed = True

    if not published_changed:
        return False

    if not status_changed:
        print(
            f"  [WARN] {article_path.name}: published を変更しましたが "
            f"status: publish-ready が見つかりません",
            file=sys.stderr,
        )

    article_path.write_text("\n".join(lines), encoding="utf-8")
    return True


def count_recent_deploys() -> int:
    """当日に公開された記事数を .publish-history.json から取得.

    git log のコミットメッセージに依存せず、履歴ファイルを単一ソースとする。
    日付単位で判定し、「今日既に公開したか」を確認する。
    """
    history = load_publish_history()
    today_str = datetime.now(JST).strftime("%Y-%m-%d")
    return history.get("dates", []).count(today_str)


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
        if last_failed.tzinfo is None:
            last_failed = last_failed.replace(tzinfo=JST)
        return datetime.now(JST) - last_failed < timedelta(hours=COOLDOWN_HOURS)
    except (KeyError, ValueError):
        return False


def main():
    parser = argparse.ArgumentParser(description="Zenn記事の自動公開（タイミング制御付き）")
    parser.add_argument("--dry-run", action="store_true", help="変更せずに確認のみ")
    parser.add_argument("--force", action="store_true", help="時刻/休日チェックをスキップ")
    parser.add_argument("--list", action="store_true", help="publish-ready 一覧を表示")
    args = parser.parse_args()

    now = datetime.now(JST)
    today_str = now.strftime("%Y-%m-%d")

    # --list: 一覧表示のみ
    if args.list:
        articles = find_publishable_articles()
        if not articles:
            print("公開対象の記事はありません")
        else:
            print(f"公開対象: {len(articles)} 本")
            for slug, path in articles:
                print(f"  - {slug}")
        sys.exit(0)

    # タイミングチェック（--force でスキップ可能）
    if not args.force:
        # 休日チェック
        if should_skip_weekend(now):
            print(f"週末スキップ（{now.strftime('%A')}）")
            sys.exit(0)

        # 連続公開チェック
        history = load_publish_history()
        if should_skip_consecutive(history, now):
            print(f"{MAX_CONSECUTIVE_DAYS}日連続公開済み — 本日は休止")
            sys.exit(0)

        # 時刻チェック（公開時刻以降なら実行。重複は「既に公開済み」チェックで防止）
        publish_hour = get_publish_hour(now)
        if now.hour < publish_hour:
            print(f"本日の公開時刻は {publish_hour}:00 JST（現在 {now.hour}:00）— スキップ")
            sys.exit(0)

        # 既に今日公開済みかチェック
        if today_str in history.get("dates", []):
            print("本日は既に公開済み — スキップ")
            sys.exit(0)

    # レートリミット確認
    recent_deploys = count_recent_deploys()
    remaining_quota = max(0, DAILY_LIMIT - recent_deploys)
    print(f"当日デプロイ数: {recent_deploys} / 日次上限: {DAILY_LIMIT}")

    if remaining_quota == 0:
        print("レートリミットに到達済み。本日の公開をスキップします。")
        sys.exit(0)

    # 公開対象の記事を検索
    articles = find_publishable_articles()
    failures = load_failures()

    # クールダウン中の記事を除外
    eligible = []
    for slug, path in articles:
        if is_in_cooldown(slug, failures):
            print(f"  クールダウン中: {slug}")
            continue
        eligible.append((slug, path))

    if not eligible:
        print("公開対象なし（対象記事 0 件、またはクールダウン中）")
        sys.exit(0)

    # 1本だけ選ぶ（日付ハッシュで決定的ランダム選択）
    idx = date_hash(today_str, "article_select") % len(eligible)
    slug, path = eligible[idx]
    print(f"{'[DRY RUN] ' if args.dry_run else ''}公開予定: {slug}")

    if args.dry_run:
        print("--dry-run のため変更しません")
        sys.exit(0)

    # 公開実行
    if publish_article(path):
        print(f"  ✓ {slug}: published → true")
    else:
        print(f"  ✗ {slug}: 変更失敗", file=sys.stderr)
        # 失敗をログに記録（クールダウン保護を有効化）
        failures = load_failures()
        failures[slug] = {"last_failed": now.isoformat()}
        try:
            with open(FAILURE_LOG, "w", encoding="utf-8") as f:
                json.dump(failures, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
        sys.exit(1)

    # 公開履歴を更新
    history = load_publish_history()
    if today_str not in history["dates"]:
        history["dates"].append(today_str)
    save_publish_history(history)

    # GitHub Actions output
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as f:
            f.write(f"published_slug={slug}\n")
            f.write(f"published=true\n")

    print(f"\n完了: {slug} を公開しました")


if __name__ == "__main__":
    main()
