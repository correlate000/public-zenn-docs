#!/usr/bin/env python3
"""batch-seo-fix.py — 監査結果に基づくfrontmatter一括修正（1回限り）

対象: Git tracked + published: true の40本
修正内容:
  P2-1: タイトル短縮（60文字超 → 60以内）
  P3-1: compound topics の修正
  P4-1: emoji 重複解消
  P4-2: topics 大文字小文字統一
"""
import re
import sys
from pathlib import Path

ARTICLES_DIR = Path(__file__).resolve().parent.parent / "articles"

# P2-1: タイトル短縮マップ
TITLE_FIXES = {
    "api-rate-limit-retry-pattern": "APIレートリミット対策の設計パターン — 自動リトライ機構の実装",
    "bigquery-ml-intro": "BigQuery MLではじめる機械学習 — SQLだけでモデル構築・予測・評価",
    "claude-code-hooks-complete-guide": "Claude Code Hooks完全ガイド — AIエージェントに安全ガードを設ける",
    "claude-code-knowledge-files": "CLAUDE.mdをKnowledge Files化して67%スリム化した実践記録",
    "claude-code-memory-design": "Claude Codeのメモリ設計 — 3層構造で長期記憶を実現する",
    "claude-md-guide": "CLAUDE.md設計ガイド — AIエージェントに環境を理解させる",
    "content-pipeline-philosophy": "コンテンツパイプラインという思想 — 記事が生まれる仕組みの設計",
    "cursor-vs-claude-code": "Cursor vs Claude Code — 2026年版AIコーディングツール使い分けガイド",
    "devils-advocate-ai-team": "AIチームにデビルズアドボケイトを入れたら品質が改善した話",
    "discord-bot-business": "1人法人がDiscord Botを業務ツールにしたら便利だった話",
    "discord-bot-remote-mac-mini": "Discord BotでMac miniをどこからでもClaude Code遠隔操作する",
    "gcp-cloud-tasks-queue": "Cloud Tasksで非同期処理を実装する — キュー管理とリトライ戦略",
    "mcp-custom-server": "MCPカスタムサーバーをPythonで実装する — Claudeに自前APIを接続",
    "mcp-vs-direct-api": "MCPサーバーより直接APIを叩くべき3つの理由",
    "nextjs-app-router-patterns": "Next.js App Router実践パターン — RSC・Server Actions・キャッシュ戦略",
    "nextjs-i18n-routing": "Next.js i18n完全ガイド — App RouterでMulti-Locale対応する",
    "obsidian-bigquery-sync": "Obsidian→BigQueryナレッジ同期 — 1人法人の第二の脳をDB化する",
    "python-type-hints-advanced": "Python型ヒント実践ガイド — mypy/pyrightで型安全なコードを書く",
    "python-uv-guide": "Python uv完全ガイド — pip/poetryより10倍速いパッケージ管理",
    "react-query-patterns": "TanStack Query実践パターン — キャッシュ・楽観的更新・無限スクロール",
    "solo-corp-info-platform": "1人法人の情報基盤設計 — BigQuery+Discord+Obsidianの活用法",
    "tailscale-dev-environment": "TailscaleでMac miniとMBPを同期して開発効率を激変させた話",
    "terraform-gcp-iac-basics": "Terraform × GCP入門 — インフラをコードで再現可能にする",
    "typescript-utility-types": "TypeScriptユーティリティ型完全ガイド — Partial・Omit・ReturnType",
    "typescript-zod-validation": "TypeScript × Zodで型安全なランタイムバリデーションを実現する",
    "vercel-edge-functions": "Vercel Edge Functions完全ガイド — エッジで超高速APIを実現",
    "zenn-note-dual-publish": "ZennとNoteに同時投稿するGitHub Actions自動化パイプライン",
}

# P3-1 + P4-2: topics修正マップ（compound word分割 + 大文字小文字統一）
TOPICS_FIXES = {
    "api-rate-limit-retry-pattern": '["api", "python", "githubactions", "retry", "automation"]',
    "bigquery-ml-intro": '["bigquery", "ml", "gcp", "sql", "data"]',
    "claude-code-knowledge-files": '["claudecode", "ai", "llm", "contextengineering"]',
    "context-ownership-model-claude-code": '["claudecode", "llm", "ai", "contextengineering"]',
    "gcp-cloud-tasks-queue": '["gcp", "cloudrun", "python", "async", "queue"]',
    "google-drive-incident": '["googledrive", "mac", "開発環境", "失敗談"]',
    "nextjs-app-router-patterns": '["nextjs", "react", "typescript", "approuter", "rsc"]',
    "react-query-patterns": '["react", "typescript", "reactquery", "nextjs", "フロントエンド"]',
    "vercel-edge-functions": '["vercel", "edge", "nextjs", "typescript", "performance"]',
    "what-not-how-prompt-philosophy": '["claudecode", "ai", "prompt", "sdd"]',
    "zenn-note-dual-publish": '["githubactions", "zenn", "note", "playwright", "automation"]',
    "zenn-rate-limit-retry": '["zenn", "githubactions", "python", "automation", "ci"]',
    # P4-2: 大文字小文字統一のみ
    "agent-teams-parallel": '["claude", "ai", "開発効率化", "自動化"]',
    "claude-code-hooks-complete-guide": '["claudecode", "claude", "ai", "自動化", "開発効率化"]',
    "claude-md-guide": '["claude", "ai", "開発環境", "自動化"]',
    "devils-advocate-ai-team": '["claude", "ai", "品質管理", "開発手法"]',
    "mcp-vs-direct-api": '["mcp", "api", "オープンデータ", "行政api", "セキュリティ"]',
}

# P4-1: emoji重複解消マップ
EMOJI_FIXES = {
    "bigquery-ml-intro": "🔮",
    "discord-bot-business": "💼",
    "local-llm-judgment": "🧩",
    "react-query-patterns": "🔄",
    "vercel-edge-functions": "🌐",
    "obsidian-bigquery-sync": "📊",
    "context-ownership-model-claude-code": "🔍",
    "what-not-how-prompt-philosophy": "💡",
    "gcp-cloud-tasks-queue": "⏱️",
    "terraform-gcp-iac-basics": "🔧",
}


def fix_article(filepath: Path, apply: bool) -> list[str]:
    """1記事のfrontmatterを修正。変更内容リストを返す。"""
    slug = filepath.stem
    content = filepath.read_text(encoding="utf-8")
    original = content
    changes = []

    # タイトル修正
    if slug in TITLE_FIXES:
        new_title = TITLE_FIXES[slug]
        content = re.sub(
            r'^(title:\s*)"[^"]*"',
            rf'\1"{new_title}"',
            content,
            count=1,
            flags=re.MULTILINE,
        )
        if content != original:
            changes.append(f"  A1: title → \"{new_title}\"")

    # topics修正
    prev = content
    if slug in TOPICS_FIXES:
        new_topics = TOPICS_FIXES[slug]
        content = re.sub(
            r'^topics:\s*\[.*\]',
            f"topics: {new_topics}",
            content,
            count=1,
            flags=re.MULTILINE,
        )
        if content != prev:
            changes.append(f"  B: topics → {new_topics}")
    prev = content

    # emoji修正
    if slug in EMOJI_FIXES:
        new_emoji = EMOJI_FIXES[slug]
        content = re.sub(
            r'^(emoji:\s*)"[^"]*"',
            rf'\1"{new_emoji}"',
            content,
            count=1,
            flags=re.MULTILINE,
        )
        if content != prev:
            changes.append(f"  F1: emoji → {new_emoji}")

    if content != original:
        if apply:
            filepath.write_text(content, encoding="utf-8")
        return changes
    return []


def main():
    apply = "--apply" in sys.argv

    articles = sorted(ARTICLES_DIR.glob("*.md"))
    total_changes = 0

    for article in articles:
        changes = fix_article(article, apply)
        if changes:
            prefix = "FIXED" if apply else "WOULD FIX"
            print(f"{prefix}: {article.name}")
            for c in changes:
                print(c)
            total_changes += len(changes)

    print(f"\n合計: {total_changes} 件の変更")
    if not apply and total_changes > 0:
        print("--apply を付けて再実行すると、実際にファイルを変更します")


if __name__ == "__main__":
    main()
