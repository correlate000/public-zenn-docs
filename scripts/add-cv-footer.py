"""全公開記事に関連記事 + Publication フッターを追加するスクリプト."""

import os
import re
import sys
import tempfile
from pathlib import Path

RELATED_MAP = {
    "agent-teams-parallel": ["ai-content-pipeline", "claude-code-hooks-complete-guide", "morning-bot-ai"],
    "ai-content-pipeline": ["agent-teams-parallel", "content-pipeline-philosophy", "claude-code-knowledge-files"],
    "api-rate-limit-retry-pattern": ["python-asyncio-api", "mcp-custom-server", "discord-bot-cloud-run"],
    "bigquery-ml-intro": ["obsidian-bigquery-sync", "notion-vs-bigquery-knowledge-management", "solo-corp-info-platform"],
    "claude-code-hooks-complete-guide": ["claude-code-memory-design", "claude-md-guide", "context-ownership-model-claude-code"],
    "claude-code-knowledge-files": ["claude-md-guide", "claude-code-memory-design", "context-ownership-model-claude-code"],
    "claude-code-memory-design": ["claude-code-knowledge-files", "claude-md-guide", "context-ownership-model-claude-code"],
    "claude-md-guide": ["claude-code-knowledge-files", "claude-code-memory-design", "what-not-how-prompt-philosophy"],
    "cloud-run-cold-start": ["solo-corp-gcp", "discord-bot-cloud-run", "terraform-gcp-iac-basics"],
    "content-pipeline-philosophy": ["obsidian-bigquery-sync", "notion-vs-bigquery-knowledge-management"],
    "context-ownership-model-claude-code": ["claude-code-hooks-complete-guide", "claude-code-knowledge-files", "claude-code-memory-design"],
    "cursor-vs-claude-code": ["claude-md-guide", "what-not-how-prompt-philosophy", "local-llm-judgment"],
    "ddev-to-docker": ["cloud-run-cold-start", "discord-bot-cloud-run", "solo-corp-gcp"],
    "devils-advocate-ai-team": ["agent-teams-parallel", "ai-content-pipeline", "what-not-how-prompt-philosophy"],
    "discord-bot-business": ["claude-code-hooks-complete-guide", "cloud-run-cold-start", "agent-teams-parallel"],
    "discord-bot-cloud-run": ["discord-bot-business", "discord-bot-remote-mac-mini", "morning-bot-ai"],
    "discord-bot-remote-mac-mini": ["discord-bot-business", "discord-bot-cloud-run", "tailscale-dev-environment"],
    "freee-api-cloud-run": ["solo-corp-gcp", "solo-corp-info-platform", "cloud-run-cold-start"],
    "gcp-cloud-tasks-queue": ["cloud-run-cold-start", "solo-corp-gcp", "discord-bot-cloud-run"],
    "google-drive-incident": ["tailscale-dev-environment", "solo-corp-gcp", "ddev-to-docker"],
    "local-llm-judgment": ["cursor-vs-claude-code", "claude-code-memory-design", "python-asyncio-api"],
    "mcp-custom-server": ["mcp-vs-direct-api", "claude-code-hooks-complete-guide", "context-ownership-model-claude-code"],
    "mcp-vs-direct-api": ["mcp-custom-server", "api-rate-limit-retry-pattern", "claude-code-hooks-complete-guide"],
    "morning-bot-ai": ["discord-bot-cloud-run", "agent-teams-parallel", "claude-code-hooks-complete-guide"],
    "nextjs-app-router-patterns": ["react-query-patterns", "typescript-utility-types", "typescript-zod-validation"],
    "nextjs-i18n-routing": ["nextjs-app-router-patterns", "typescript-utility-types", "vercel-edge-functions"],
    "notion-vs-bigquery-knowledge-management": ["obsidian-bigquery-sync", "bigquery-ml-intro", "solo-corp-info-platform"],
    "obsidian-bigquery-sync": ["notion-vs-bigquery-knowledge-management", "solo-corp-info-platform", "content-pipeline-philosophy"],
    "python-asyncio-api": ["api-rate-limit-retry-pattern", "python-module-splitting", "python-type-hints-advanced"],
    "python-module-splitting": ["python-asyncio-api", "python-type-hints-advanced", "python-uv-guide"],
    "python-type-hints-advanced": ["python-module-splitting", "typescript-utility-types", "python-uv-guide"],
    "python-uv-guide": ["python-module-splitting", "python-type-hints-advanced", "ddev-to-docker"],
    "qwen-hybrid-ai-operations": ["bigquery-ml-intro", "obsidian-bigquery-sync", "solo-corp-info-platform"],
    "react-query-patterns": ["nextjs-app-router-patterns", "typescript-zod-validation", "typescript-utility-types"],
    "solo-corp-gcp": ["solo-corp-info-platform", "freee-api-cloud-run", "discord-bot-cloud-run"],
    "solo-corp-info-platform": ["solo-corp-gcp", "obsidian-bigquery-sync", "discord-bot-business"],
    "tailscale-dev-environment": ["discord-bot-remote-mac-mini", "google-drive-incident", "ddev-to-docker"],
    "terraform-gcp-iac-basics": ["cloud-run-cold-start", "solo-corp-gcp", "gcp-cloud-tasks-queue"],
    "typescript-utility-types": ["typescript-zod-validation", "nextjs-app-router-patterns", "python-type-hints-advanced"],
    "typescript-zod-validation": ["typescript-utility-types", "react-query-patterns", "nextjs-app-router-patterns"],
    "vercel-edge-functions": ["nextjs-app-router-patterns", "nextjs-i18n-routing", "cloud-run-cold-start"],
    "what-not-how-prompt-philosophy": ["devils-advocate-ai-team", "cursor-vs-claude-code"],
    "zenn-note-dual-publish": ["zenn-rate-limit-retry", "ai-content-pipeline", "content-pipeline-philosophy"],
    "zenn-rate-limit-retry": ["zenn-note-dual-publish", "api-rate-limit-retry-pattern", "python-asyncio-api"],
}

PUBLICATION = "correlate_dev"
PUBLICATION_URL = f"https://zenn.dev/p/{PUBLICATION}"
BASE_URL = f"https://zenn.dev/{PUBLICATION}/articles"

FOOTER_TEMPLATE = """
---

**関連記事**

{related_links}

> [{PUBLICATION}]({PUBLICATION_URL}) では、Claude Code・GCP・Pythonを使った開発自動化の実践知を発信しています。
"""

FOOTER_MARKER = f"> [{PUBLICATION}]({PUBLICATION_URL})"

# 旧URLパターン → 新URLパターン
OLD_URL_PATTERNS = [
    "zenn.dev/correlate/articles/",
    "zenn.dev/correlate000/articles/",
]
NEW_URL_PATTERN = f"zenn.dev/{PUBLICATION}/articles/"


def atomic_write(path: Path, content: str) -> None:
    """一時ファイル経由でアトミックに書き込む."""
    dir_ = path.parent
    fd, tmp_path = tempfile.mkstemp(dir=dir_, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def fix_old_urls(content: str) -> tuple[str, int]:
    """旧URLを新URLに置換する。変更件数を返す。"""
    total_count = 0
    new_content = content
    for pattern in OLD_URL_PATTERNS:
        total_count += new_content.count(pattern)
        new_content = new_content.replace(pattern, NEW_URL_PATTERN)
    return new_content, total_count


def build_title_map(articles_dir: Path) -> dict[str, str]:
    """全公開記事のslug→titleマップを構築."""
    title_map = {}
    for f in sorted(articles_dir.glob("*.md")):
        if "_fixed_visual" in f.name:
            continue
        try:
            content = f.read_text(encoding="utf-8")
        except OSError as e:
            print(f"  WARN: {f.name} を読み込めませんでした: {e}")
            continue
        if not re.search(r"published:\s*true", content):
            continue
        slug = f.stem
        m = re.search(r'title:\s*"(.+?)"', content)
        if m:
            title_map[slug] = m.group(1)
    return title_map


def has_footer(content: str) -> bool:
    """既にフッターが追加済みか."""
    return FOOTER_MARKER in content


def extract_body_slugs(content: str) -> set[str]:
    """本文中に既出のスラッグを抽出する（フッター追加前の本文から）."""
    return set(re.findall(r'zenn\.dev/correlate_dev/articles/([a-z0-9-]+)', content))


def build_footer(slug: str, title_map: dict[str, str], body_slugs: set[str] | None = None) -> str | None:
    """記事のフッターを生成。本文中に既出のスラッグは除外する。
    除外後に関連記事が0本になった場合はNoneを返す。"""
    related_slugs = RELATED_MAP.get(slug, [])
    if body_slugs is None:
        body_slugs = set()
    links = []
    for rs in related_slugs:
        if rs in body_slugs:
            continue
        title = title_map.get(rs, rs)
        links.append(f"- [{title}]({BASE_URL}/{rs})")
    if not links:
        return None
    related_links = "\n".join(links)
    return FOOTER_TEMPLATE.format(
        related_links=related_links,
        PUBLICATION=PUBLICATION,
        PUBLICATION_URL=PUBLICATION_URL,
    )


def main():
    dry_run = "--dry-run" in sys.argv

    # スクリプト位置を基準にarticlesディレクトリを解決
    articles_dir = Path(__file__).parent.parent / "articles"
    if not articles_dir.exists():
        print(f"ERROR: articlesディレクトリが見つかりません: {articles_dir}", file=sys.stderr)
        sys.exit(1)

    title_map = build_title_map(articles_dir)

    url_fixed = 0
    updated = 0
    would_update = 0
    skipped = 0

    for f in sorted(articles_dir.glob("*.md")):
        if "_fixed_visual" in f.name:
            continue

        try:
            content = f.read_text(encoding="utf-8")
        except OSError as e:
            print(f"  WARN: {f.name} を読み込めませんでした: {e}")
            continue

        if not re.search(r"published:\s*true", content):
            continue

        slug = f.stem

        # --- 前処理: 旧URL修正 ---
        new_content, url_count = fix_old_urls(content)
        if url_count > 0:
            if dry_run:
                print(f"  DRY-RUN URL-FIX: {slug} — {url_count}件の旧URLを置換予定")
            else:
                try:
                    atomic_write(f, new_content)
                    print(f"  URL-FIXED ({url_count}件): {slug}")
                    url_fixed += 1
                    content = new_content
                except OSError as e:
                    print(f"  ERROR: {slug} のURL修正に失敗しました: {e}")
                    continue
            content = new_content

        # --- フッター追加 ---
        if has_footer(content):
            print(f"  SKIP (already has footer): {slug}")
            skipped += 1
            continue

        if slug not in RELATED_MAP:
            print(f"  SKIP (no mapping): {slug}")
            skipped += 1
            continue

        body_slugs = extract_body_slugs(content)
        footer = build_footer(slug, title_map, body_slugs)

        if footer is None:
            print(f"  SKIP (all related already in body): {slug}")
            skipped += 1
            continue

        if dry_run:
            print(f"  DRY-RUN: {slug} — would append footer")
            would_update += 1
        else:
            new_content = content.rstrip() + "\n" + footer
            try:
                atomic_write(f, new_content)
                print(f"  UPDATED: {slug}")
                updated += 1
            except OSError as e:
                print(f"  ERROR: {slug} のフッター追加に失敗しました: {e}")
                continue

    print(f"\nTotal: {url_fixed} url-fixed, {updated} updated, {skipped} skipped"
          + (f", {would_update} would-update" if dry_run else ""))


if __name__ == "__main__":
    main()
