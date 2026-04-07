---
title: "Claude Code Hooks実践ガイド — ユースケース別設計パターン4選"
emoji: "🪝"
type: "tech"
topics: ["claudecode", "claude", "ai", "automation", "security"]
published: false
status: "draft"
publication_name: "correlate_dev"
---

## はじめに

Claude Code Hooksの概要については[Claude Code Hooks完全ガイド](https://zenn.dev/correlate_dev/articles/claude-code-hooks-complete-guide)で解説しました。本記事は実践編として、実際の業務で使っているHooksパターンを4つのユースケースに整理して紹介します。

- ** セキュリティ ** ：危険コマンドのブロックと多段階確認
- ** 品質ゲート ** ：コミット前のLint・テスト強制
- ** 外部通知 ** ：Discord/Slack連携
- ** コスト管理 ** ：APIコスト追跡とセッション記録

各パターンはコピーして即使えるbashスクリプトとして提供します。

### 動作環境

- Claude Code 最新版
- bash 4.x 以上
- jq 1.6 以上（`brew install jq`）
- curl（通知連携に使用）

---

## Hooksの基本設定（おさらい）

`~/.claude/settings.json` への登録方法だけ確認しておきます。

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "~/.claude/hooks/pre-bash-guard.sh"
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Write|Edit|MultiEdit",
        "hooks": [
          {
            "type": "command",
            "command": "~/.claude/hooks/post-file-change.sh"
          }
        ]
      }
    ],
    "Notification": [
      {
        "matcher": ".*",
        "hooks": [
          {
            "type": "command",
            "command": "~/.claude/hooks/notification-discord.sh"
          }
        ]
      }
    ],
    "Stop": [
      {
        "matcher": ".*",
        "hooks": [
          {
            "type": "command",
            "command": "~/.claude/hooks/stop-session-cleanup.sh"
          }
        ]
      }
    ]
  }
}
```

フックスクリプトのexitコードで動作が変わります。

| exitコード | 意味 |
|---|---|
| 0 | 成功。ツール実行を続行 |
| 2 | Claude Codeに警告メッセージを返す（実行は継続） |
| それ以外 | エラー。PreToolUseでは実行をブロック |

---

## パターン1：セキュリティ ─ 危険コマンドのブロック

### 多段階ブロックの設計

単純なブロックリストではなく、「危険度」に応じたレスポンスを設計します。

```
危険度: HIGH  → 即座にブロック（確認不要）
危険度: MED   → 警告を出してAIに再確認を促す
危険度: LOW   → ログを記録して通過
```

```bash
#!/bin/bash
# ~/.claude/hooks/pre-bash-guard.sh

set -euo pipefail

INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // ""')

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> ~/.claude/logs/hooks.log
}

# 危険度: HIGH（即座にブロック）
HIGH_PATTERNS=(
  "rm -rf /"
  "rm -rf ~"
  "git push --force"
  "git push -f"
  "git reset --hard"
  "DROP TABLE"
  "DROP DATABASE"
  "TRUNCATE TABLE"
  "chmod -R 777"
  "mkfs"
  "dd if="
)

for pattern in "${HIGH_PATTERNS[@]}"; do
  if echo "$COMMAND" | grep -qi "$pattern"; then
    log "BLOCKED [HIGH] command: $COMMAND"
    echo "🚫 危険なコマンドをブロックしました: $pattern"
    echo "このコマンドは実行できません。別のアプローチを検討してください。"
    exit 1
  fi
done

# 危険度: MED（警告を出して再確認を促す）
MED_PATTERNS=(
  "git checkout --"
  "git clean -f"
  "git branch -D"
  "sudo rm"
  "pkill"
  "killall"
  "> /dev/null"  # 出力の完全破棄
)

for pattern in "${MED_PATTERNS[@]}"; do
  if echo "$COMMAND" | grep -qi "$pattern"; then
    log "WARNING [MED] command: $COMMAND"
    echo "⚠️ 要注意コマンドが検出されました: $pattern"
    echo "このコマンドは取り消せない可能性があります。本当に実行しますか？"
    echo "問題なければ続けてください。"
    exit 2  # 警告のみ（実行はAIが判断）
  fi
done

# 本番環境への操作検出
if echo "$COMMAND" | grep -qE "(prod|production|本番)" && \
   echo "$COMMAND" | grep -qE "(deploy|push|apply|migrate)"; then
  log "WARNING [PROD] command: $COMMAND"
  echo "⚠️ 本番環境への操作が検出されました"
  echo "ステージング環境での確認を先に実施してください"
  exit 2
fi

log "OK command: $(echo "$COMMAND" | head -c 100)"
exit 0
```

### 本番環境ブランチの保護

```bash
#!/bin/bash
# ~/.claude/hooks/pre-git-branch-guard.sh
# mainブランチへの直接コミットをブロックする

INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // ""')

# gitコマンドでなければスキップ
if ! echo "$COMMAND" | grep -qE "^git (commit|push)"; then
  exit 0
fi

# 現在のブランチを確認
CURRENT_BRANCH=$(git branch --show-current 2>/dev/null || echo "")

if [[ "$CURRENT_BRANCH" == "main" || "$CURRENT_BRANCH" == "master" ]]; then
  # 5行以内の小さな変更（タイポ修正等）は許可
  CHANGED_LINES=$(git diff --cached --numstat 2>/dev/null | \
    awk '{sum += $1 + $2} END {print sum}' || echo "0")
  
  if [[ "$CHANGED_LINES" -gt 5 ]]; then
    echo "🚫 mainブランチへの直接コミットはブロックされています"
    echo "変更行数: ${CHANGED_LINES}行"
    echo "feature/ または fix/ ブランチを作成してください"
    exit 1
  fi
fi

exit 0
```

---

## パターン2：品質ゲート ─ コミット前の自動チェック

### TypeScript + ESLint + テストを一括実行

```bash
#!/bin/bash
# ~/.claude/hooks/post-write-quality-gate.sh
# ファイル変更後に品質チェックを実行する

INPUT=$(cat)
TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // ""')
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // ""')

# 対象外のツールはスキップ
if [[ "$TOOL_NAME" != "Write" && "$TOOL_NAME" != "Edit" && "$TOOL_NAME" != "MultiEdit" ]]; then
  exit 0
fi

# TypeScript/TSXファイルのみ対象
if [[ "$FILE_PATH" != *.ts && "$FILE_PATH" != *.tsx ]]; then
  exit 0
fi

# プロジェクトルートを特定
PROJECT_ROOT=$(git rev-parse --show-toplevel 2>/dev/null)
if [[ -z "$PROJECT_ROOT" ]]; then
  exit 0
fi

cd "$PROJECT_ROOT" || exit 0

echo "🔍 品質チェック開始: $(basename "$FILE_PATH")"

# 1. TypeScriptの型チェック
if command -v tsc &> /dev/null || [[ -f "node_modules/.bin/tsc" ]]; then
  echo "  → TypeScript型チェック..."
  if ! npx tsc --noEmit --skipLibCheck 2>&1 | grep -v "^$" | head -10; then
    echo "  ❌ 型エラーが検出されました"
    exit 2  # 警告として通知（実行は継続）
  fi
  echo "  ✅ 型チェック通過"
fi

# 2. ESLintチェック（変更ファイルのみ）
if [[ -f ".eslintrc.js" || -f ".eslintrc.json" || -f "eslint.config.js" ]]; then
  echo "  → ESLintチェック..."
  LINT_OUTPUT=$(npx eslint "$FILE_PATH" --format compact 2>&1 || true)
  
  if echo "$LINT_OUTPUT" | grep -q "error"; then
    echo "  ⚠️ ESLintエラー:"
    echo "$LINT_OUTPUT" | grep "error" | head -5
    exit 2
  fi
  echo "  ✅ ESLint通過"
fi

# 3. 関連テストの実行（ファイル名から推定）
TEST_FILE="${FILE_PATH%.tsx}.test.tsx"
if [[ ! -f "$TEST_FILE" ]]; then
  TEST_FILE="${FILE_PATH%.ts}.test.ts"
fi
if [[ ! -f "$TEST_FILE" ]]; then
  TEST_FILE="${FILE_PATH%.tsx}.spec.tsx"
fi

if [[ -f "$TEST_FILE" ]]; then
  echo "  → 関連テスト実行: $(basename "$TEST_FILE")"
  if ! npx jest "$TEST_FILE" --passWithNoTests --no-coverage 2>&1 | tail -5; then
    echo "  ❌ テスト失敗"
    exit 2
  fi
  echo "  ✅ テスト通過"
fi

echo "✅ 品質チェック完了"
exit 0
```

### セッション記録のコミット前チェック

```bash
#!/bin/bash
# ~/.claude/hooks/pre-commit-session-check.sh
# gitコミット前にセッション記録が更新されているか確認する

INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // ""')

# git commit コマンドでなければスキップ
if ! echo "$COMMAND" | grep -qE "^git commit"; then
  exit 0
fi

SESSION_DIR=~/dev/Obsidian/00_sessions/
TODAY=$(date +%Y-%m-%d)

# 今日のセッションファイルが存在するか確認
TODAY_SESSION=$(find "$SESSION_DIR" -name "${TODAY}*.md" -newer "$SESSION_DIR" 2>/dev/null | head -1)

if [[ -z "$TODAY_SESSION" ]]; then
  echo "⚠️ 本日のセッション記録が見つかりません"
  echo "セッション記録を更新してからコミットしてください"
  echo "セッションディレクトリ: $SESSION_DIR"
  exit 2
fi

# セッション記録が直近30分以内に更新されているか確認
LAST_MODIFIED=$(stat -f %m "$TODAY_SESSION" 2>/dev/null || stat -c %Y "$TODAY_SESSION" 2>/dev/null)
NOW=$(date +%s)
AGE=$((NOW - LAST_MODIFIED))

if [[ "$AGE" -gt 1800 ]]; then  # 30分 = 1800秒
  echo "⚠️ セッション記録が30分以上更新されていません"
  echo "最終更新: $(date -r "$LAST_MODIFIED" '+%H:%M' 2>/dev/null || echo '不明')"
  echo "コミット前にセッション記録を更新してください"
  exit 2
fi

echo "✅ セッション記録確認完了: $(basename "$TODAY_SESSION")"
exit 0
```

---

## パターン3：外部通知 ─ Discord/Slack連携

### Discordへの通知（長時間タスク用）

```bash
#!/bin/bash
# ~/.claude/hooks/notification-discord.sh

INPUT=$(cat)
NOTIFICATION_TYPE=$(echo "$INPUT" | jq -r '.notification_type // "unknown"')
MESSAGE=$(echo "$INPUT" | jq -r '.message // ""')

# Discordのwebhook URL（環境変数から取得）
WEBHOOK_URL="${DISCORD_WEBHOOK_URL:-}"
if [[ -z "$WEBHOOK_URL" ]]; then
  exit 0  # 設定がなければスキップ
fi

# 通知タイプに応じてフォーマットを変える
case "$NOTIFICATION_TYPE" in
  "task_complete")
    EMOJI="✅"
    COLOR=3066993  # 緑
    ;;
  "error")
    EMOJI="❌"
    COLOR=15158332  # 赤
    ;;
  "warning")
    EMOJI="⚠️"
    COLOR=16776960  # 黄
    ;;
  *)
    EMOJI="ℹ️"
    COLOR=3447003  # 青
    ;;
esac

# 長いメッセージは切り詰め
SHORT_MSG=$(echo "$MESSAGE" | head -c 200)

PAYLOAD=$(jq -n \
  --arg emoji "$EMOJI" \
  --arg msg "$SHORT_MSG" \
  --argjson color "$COLOR" \
  '{
    embeds: [{
      title: ($emoji + " Claude Code通知"),
      description: $msg,
      color: $color,
      timestamp: (now | todate)
    }]
  }'
)

curl -s -X POST "$WEBHOOK_URL" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD" \
  > /dev/null 2>&1

exit 0
```

### 環境変数の設定

```bash
# ~/.zshrc または ~/.bashrc に追加
export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/xxx/yyy"
export SLACK_WEBHOOK_URL="https://hooks.slack.com/services/xxx/yyy/zzz"
```

launchdで動かす場合は、plistの `EnvironmentVariables` に設定します。

```xml
<key>EnvironmentVariables</key>
<dict>
  <key>DISCORD_WEBHOOK_URL</key>
  <string>https://discord.com/api/webhooks/xxx/yyy</string>
</dict>
```

### Slack通知版

```bash
#!/bin/bash
# ~/.claude/hooks/notification-slack.sh

INPUT=$(cat)
MESSAGE=$(echo "$INPUT" | jq -r '.message // ""')
NOTIFICATION_TYPE=$(echo "$INPUT" | jq -r '.notification_type // "info"')

WEBHOOK_URL="${SLACK_WEBHOOK_URL:-}"
if [[ -z "$WEBHOOK_URL" ]]; then
  exit 0
fi

# Slackのemojiマッピング
case "$NOTIFICATION_TYPE" in
  "task_complete") EMOJI=":white_check_mark:" ;;
  "error")         EMOJI=":x:" ;;
  "warning")       EMOJI=":warning:" ;;
  *)               EMOJI=":information_source:" ;;
esac

SHORT_MSG=$(echo "$MESSAGE" | head -c 500)

curl -s -X POST "$WEBHOOK_URL" \
  -H "Content-Type: application/json" \
  -d "{\"text\": \"${EMOJI} ${SHORT_MSG}\"}" \
  > /dev/null 2>&1

exit 0
```

---

## パターン4：コスト管理 ─ APIコストの追跡とセッション記録

### Stopフックでセッションサマリーを生成

```bash
#!/bin/bash
# ~/.claude/hooks/stop-session-cleanup.sh

INPUT=$(cat)
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // "unknown"')
TOTAL_COST=$(echo "$INPUT" | jq -r '.total_cost_usd // 0')
TOOL_CALLS=$(echo "$INPUT" | jq -r '.tool_calls_count // 0')

LOG_DIR=~/.claude/logs/sessions/
mkdir -p "$LOG_DIR"

TODAY=$(date +%Y-%m-%d)
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

# セッションサマリーを記録
cat >> "${LOG_DIR}/${TODAY}-summary.log" << EOF
---
Session: ${SESSION_ID}
Time: ${TIMESTAMP}
Cost: \$${TOTAL_COST}
Tool calls: ${TOOL_CALLS}
EOF

echo "📊 セッション終了サマリーを記録しました"
echo "  コスト: \$${TOTAL_COST}"
echo "  ツール呼び出し: ${TOOL_CALLS}回"

# 月次コスト集計
MONTH=$(date +%Y-%m)
MONTHLY_TOTAL=$(grep -h "Cost:" "${LOG_DIR}"/${MONTH}*-summary.log 2>/dev/null | \
  awk -F'$' '{sum += $2} END {printf "%.4f", sum}')

echo "  今月の累計コスト: \$${MONTHLY_TOTAL}"

# コスト上限アラート（$10超えたら警告）
COST_LIMIT=10.0
if (( $(echo "$MONTHLY_TOTAL > $COST_LIMIT" | bc -l) )); then
  echo "⚠️ 月次コスト上限（\$${COST_LIMIT}）を超えています！"
  
  # Discord通知（設定がある場合）
  if [[ -n "${DISCORD_WEBHOOK_URL:-}" ]]; then
    curl -s -X POST "$DISCORD_WEBHOOK_URL" \
      -H "Content-Type: application/json" \
      -d "{\"content\": \"⚠️ Claude Codeの月次コストが \$${MONTHLY_TOTAL} になりました。上限: \$${COST_LIMIT}\"}" \
      > /dev/null 2>&1
  fi
fi

exit 0
```

### 日次コストレポートの生成

```python
#!/usr/bin/env python3
# ~/.claude/scripts/daily-cost-report.py

from pathlib import Path
from datetime import datetime, timedelta
import re

LOG_DIR = Path.home() / ".claude/logs/sessions"

def parse_session_logs(days: int = 7) -> list[dict]:
    """直近N日分のセッションログを解析する"""
    sessions = []
    
    for i in range(days):
        date = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        log_file = LOG_DIR / f"{date}-summary.log"
        
        if not log_file.exists():
            continue
        
        content = log_file.read_text()
        blocks = content.split("---\n")[1:]  # 先頭の空エントリを除く
        
        for block in blocks:
            if not block.strip():
                continue
            
            session = {"date": date}
            
            for line in block.strip().split("\n"):
                if line.startswith("Cost:"):
                    cost_str = re.search(r'\$([\d.]+)', line)
                    session["cost"] = float(cost_str.group(1)) if cost_str else 0.0
                elif line.startswith("Tool calls:"):
                    calls_str = re.search(r'(\d+)', line)
                    session["tool_calls"] = int(calls_str.group(1)) if calls_str else 0
            
            if "cost" in session:
                sessions.append(session)
    
    return sessions


def print_report(sessions: list[dict]) -> None:
    """コストレポートを出力する"""
    if not sessions:
        print("セッション記録が見つかりません")
        return
    
    total_cost = sum(s.get("cost", 0) for s in sessions)
    total_calls = sum(s.get("tool_calls", 0) for s in sessions)
    
    print(f"📊 直近7日間のClaude Codeコストレポート")
    print(f"  合計コスト: ${total_cost:.4f}")
    print(f"  合計ツール呼び出し: {total_calls}回")
    print(f"  セッション数: {len(sessions)}")
    
    if sessions:
        avg_cost = total_cost / len(sessions)
        print(f"  平均コスト/セッション: ${avg_cost:.4f}")


if __name__ == "__main__":
    sessions = parse_session_logs(days=7)
    print_report(sessions)
```

---

## フックスクリプトのテスト方法

Hooksが正しく動作するか確認する方法です。

```bash
# PreToolUseフックのテスト（標準入力をシミュレート）
echo '{"tool_name": "Bash", "tool_input": {"command": "rm -rf /"}}' | \
  bash ~/.claude/hooks/pre-bash-guard.sh

# 期待する出力:
# 🚫 危険なコマンドをブロックしました: rm -rf /
# exitコード: 1
```

```bash
# PostToolUseフックのテスト
echo '{"tool_name": "Write", "tool_input": {"file_path": "src/components/Button.tsx"}}' | \
  bash ~/.claude/hooks/post-write-quality-gate.sh
```

---

## まとめ

4つのユースケース別パターンをまとめます。

| パターン | フックタイプ | 主な効果 |
|---|---|---|
| 危険コマンドブロック | PreToolUse | 取り消せない操作の防止 |
| 品質ゲート | PostToolUse | 型エラー・Lintの即時検出 |
| Discord/Slack通知 | Notification | 長時間タスクの完了把握 |
| コスト追跡 | Stop | 予算管理・月次レポート |

Hooksの設計で重要なのは「エラーの重大度に応じたexitコード」です。全てをブロック（exit 1）にしてしまうと、AIの作業が頻繁に中断されます。警告（exit 2）と組み合わせることが、安全性と作業効率のバランスを保つ鍵です。

### 関連記事

- [Claude Code Hooks完全ガイド](https://zenn.dev/correlate_dev/articles/claude-code-hooks-complete-guide)
- [Claude CodeのCLAUDE.mdで作業品質を担保する](https://zenn.dev/correlate_dev/articles/claude-md-guide)
