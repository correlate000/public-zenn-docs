#!/usr/bin/env python3
"""Zenn記事公開成功をDiscord Webhookに通知（git commit 成功後に実行）"""
import json
import os
import sys
import urllib.request


def main():
    url = os.environ.get("DISCORD_WEBHOOK_CONTENT", "")
    slugs_raw = os.environ.get("PUBLISHED_SLUGS", "")
    if not url or not slugs_raw:
        print("DISCORD_WEBHOOK_CONTENT or PUBLISHED_SLUGS not set, skipping")
        sys.exit(0)

    slugs = [s.strip() for s in slugs_raw.split(",") if s.strip()]
    if not slugs:
        sys.exit(0)

    embed = {
        "title": f"\U0001f4dd Zenn {len(slugs)}\u672c \u516c\u958b",
        "description": "\n".join(
            f"- [{s}](https://zenn.dev/correlate/articles/{s})" for s in slugs
        ),
        "color": 3066993,
    }
    payload = json.dumps({"embeds": [embed]}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req, timeout=10)
        print(f"Discord notification sent for {len(slugs)} articles")
    except Exception as e:
        print(f"Discord notification failed: {e}", file=sys.stderr)
        # 通知失敗でワークフローを止めない
        sys.exit(0)


if __name__ == "__main__":
    main()
