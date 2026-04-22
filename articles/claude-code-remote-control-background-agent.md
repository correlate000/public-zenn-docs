---
title: "Claude Code をリモート制御する — 寝ている間に AI にコードを書かせる実践ガイド"
emoji: "🤖"
type: "tech"
topics: ["claudecode", "automation", "github", "python", "llm"]
published: false
status: "draft"
publication_name: "correlate_dev"
---

副業・個人開発をしていると、「席を離れると開発が止まる」という問題に必ずぶつかります。

Claude Code は対話式 CLI として使うのが一般的ですが、外部シグナルからプロセスを制御する「Remote Control」構成にすることで、 ** 自分がいない間も AI がタスクを進め続ける ** 環境が作れます。

この記事では、Claude Code をバックグラウンド常駐させ、GitHub / Slack / cron などの外部トリガーから制御する実装パターンを、実際に動かして確認したコードとともに紹介します。

## 構成の全体像

Remote Control 構成のアーキテクチャはシンプルです。

```
[外部トリガー]         [受信レイヤー]          [実行レイヤー]          [出力]
 GitHub Webhook  →   ローカルHTTPサーバー  →  Claude Code プロセス  →  Git / FS
 Slack メッセージ →   Slack Bolt アプリ    →       (tmux常駐)        →  ログ・通知
 cron スケジュール →  シェルスクリプト     →                         →  Pushover通知
```

中核となるのは ** 「tmux 常駐 + named pipe (FIFO) での stdin 制御」 ** です。Claude Code のプロセスを永続的に立ち上げておき、外部からパイプ経由で指示を流し込む構成になります。

---

## 1. 環境構築 — ローカル常駐セッションの作り方

### 前提確認

```bash
# Claude Code のバージョン確認
claude --version

# ANTHROPIC_API_KEY が設定されていることを確認
echo $ANTHROPIC_API_KEY | head -c 20
```

Node.js 18 以上、Claude Code が `claude` コマンドとして呼び出せる状態にしておきます。

### tmux + named pipe による常駐構成

tmux でバックグラウンドセッションを作り、named pipe (FIFO) を経由して外部から stdin に指示を流す方法です。

```bash
#!/bin/bash
# scripts/start-claude-agent.sh

PIPE="/tmp/claude-agent-input"
SESSION="claude-agent"
WORKDIR="$HOME/dev/projects/self/my-project"
LOGFILE="$HOME/dev/logs/claude-agent-$(date +%Y%m%d).log"

# FIFO がなければ作成
[ -p "$PIPE" ] || mkfifo "$PIPE"

# 既存セッションがあれば削除
tmux has-session -t "$SESSION" 2>/dev/null && tmux kill-session -t "$SESSION"

# Claude Code をバックグラウンド起動
# cat でパイプを読み続け、claude の stdin に渡す
tmux new-session -d -s "$SESSION" \
  "cd $WORKDIR && cat $PIPE | claude --dangerously-skip-permissions 2>&1 | tee -a $LOGFILE"

echo "Claude agent started. Pipe: $PIPE"
```

起動したら以下で動作確認します。

```bash
# エージェント起動
bash scripts/start-claude-agent.sh

# 指示を送信
echo "src/index.ts の型エラーを全て修正してください" > /tmp/claude-agent-input

# ログで確認
tail -f ~/dev/logs/claude-agent-$(date +%Y%m%d).log
```

### macOS での launchd 常駐化

Mac mini などで常時起動させたい場合は launchd plist を使います。

```xml
<!-- ~/Library/LaunchAgents/dev.correlate.claude-agent.plist -->
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>dev.correlate.claude-agent</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>/Users/yourname/dev/scripts/start-claude-agent.sh</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>/Users/yourname/dev/logs/launchd-claude-agent.log</string>
  <key>StandardErrorPath</key>
  <string>/Users/yourname/dev/logs/launchd-claude-agent-err.log</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>ANTHROPIC_API_KEY</key>
    <string>sk-ant-xxxxx</string>
    <key>PATH</key>
    <string>/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin</string>
  </dict>
</dict>
</plist>
```

```bash
# 登録・起動
launchctl load ~/Library/LaunchAgents/dev.correlate.claude-agent.plist

# 確認
launchctl list | grep claude-agent

# ログ確認
log show --predicate 'process == "launchd"' --last 1h | grep claude-agent
```

注意点として、`ANTHROPIC_API_KEY` を plist に直書きするのは避けて、本番では `EnvironmentVariables` を省略し、スクリプト内で 1Password CLI や `~/.zshenv` から読み込む設計にすることをお勧めします。

---

## 2. 外部トリガー連携の実装

### パターン①: GitHub Webhook → Claude Code

GitHub の Issue や PR コメントをトリガーにする構成です。ローカルマシンに Webhook を受信するサーバーを立て、ngrok や Cloudflare Tunnel で公開します。

** 受信サーバーの実装（Python + Flask）:**

```python
# scripts/webhook-server.py
import os
import hmac
import hashlib
from flask import Flask, request, jsonify

app = Flask(__name__)

PIPE_PATH = "/tmp/claude-agent-input"
WEBHOOK_SECRET = os.environ.get("GITHUB_WEBHOOK_SECRET", "")


def verify_signature(payload: bytes, signature: str) -> bool:
    """GitHub Webhook の署名を検証する"""
    if not WEBHOOK_SECRET:
        return True  # 開発環境では署名検証をスキップ
    expected = "sha256=" + hmac.new(
        WEBHOOK_SECRET.encode(), payload, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def send_to_claude(instruction: str) -> None:
    """Claude Code プロセスに指示を送る"""
    with open(PIPE_PATH, "w") as pipe:
        pipe.write(instruction + "\n")


@app.route("/webhook/github", methods=["POST"])
def github_webhook():
    # 署名検証
    sig = request.headers.get("X-Hub-Signature-256", "")
    if not verify_signature(request.data, sig):
        return jsonify({"error": "Invalid signature"}), 403

    event = request.headers.get("X-GitHub-Event", "")
    payload = request.json

    # Issue にラベル "claude-task" が付いたら処理
    if event == "issues" and payload.get("action") == "labeled":
        label = payload["label"]["name"]
        if label == "claude-task":
            issue = payload["issue"]
            instruction = f"""
GitHub Issue #{issue['number']} のタスクを実装してください。

タイトル: {issue['title']}

内容:
{issue['body']}

ブランチ名は feature/issue-{issue['number']} で作成し、実装完了後に PR を作成してください。
"""
            send_to_claude(instruction.strip())
            return jsonify({"status": "task queued"}), 200

    # PR コメントで @claude をメンション
    if event == "issue_comment" and payload.get("action") == "created":
        body = payload["comment"]["body"]
        if "@claude" in body:
            pr_number = payload["issue"].get("number", "")
            instruction = body.replace("@claude", "").strip()
            send_to_claude(f"PR #{pr_number} に関するリクエスト: {instruction}")
            return jsonify({"status": "comment processed"}), 200

    return jsonify({"status": "ignored"}), 200


if __name__ == "__main__":
    app.run(port=8080)
```

**ngrok でローカルを公開:**

```bash
# Cloudflare Tunnel (推奨: 永続URL)
cloudflared tunnel --url http://localhost:8080

# または ngrok (一時URL)
ngrok http 8080
```

生成された URL を GitHub リポジトリの Settings → Webhooks に登録します。

### パターン②: Slack メッセージ → Claude Code

チャンネルで `@Claude-bot タスク内容` と書いたら Claude Code が動くようにします。

```python
# scripts/slack-bot.py
import os
import re
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

app = App(token=os.environ["SLACK_BOT_TOKEN"])

PIPE_PATH = "/tmp/claude-agent-input"
ALLOWED_CHANNEL = os.environ.get("ALLOWED_CHANNEL_ID", "")  # 実行を許可するチャンネル


def send_to_claude(instruction: str) -> None:
    with open(PIPE_PATH, "w") as pipe:
        pipe.write(instruction + "\n")


@app.event("app_mention")
def handle_mention(event, say, client):
    channel_id = event["channel"]

    # 許可チャンネル以外は無視（セキュリティ）
    if ALLOWED_CHANNEL and channel_id != ALLOWED_CHANNEL:
        say("このチャンネルでの実行は許可されていません。")
        return

    # メンション部分を除去してタスク内容を抽出
    text = re.sub(r"<@[A-Z0-9]+>", "", event["text"]).strip()

    if not text:
        say("タスク内容を入力してください。例: `@Claude-bot src/utils.ts の型エラーを修正して`")
        return

    # 承認ボタン付きメッセージを送信（安全のため実行前に確認）
    client.chat_postMessage(
        channel=channel_id,
        text=f"以下のタスクを実行しますか？\n```{text}```",
        blocks=[
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"以下のタスクを実行しますか？\n```{text}```"},
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "実行する"},
                        "style": "primary",
                        "action_id": "approve_task",
                        "value": text,
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "キャンセル"},
                        "style": "danger",
                        "action_id": "cancel_task",
                    },
                ],
            },
        ],
    )


@app.action("approve_task")
def handle_approve(ack, body, say):
    ack()
    task = body["actions"][0]["value"]
    send_to_claude(task)
    say(f"タスクをキューに追加しました。ログを確認してください。\nタスク: `{task}`")


@app.action("cancel_task")
def handle_cancel(ack, say):
    ack()
    say("キャンセルしました。")


if __name__ == "__main__":
    handler = SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
    handler.start()
```

### パターン③: cron バッチ → Claude Code

定時実行が向いているのは「毎朝テストを走らせて失敗があれば修正する」「週次で依存パッケージの更新 PR を作る」といったタスクです。

```bash
#!/bin/bash
# scripts/nightly-batch.sh

PIPE="/tmp/claude-agent-input"
LOG="$HOME/dev/logs/nightly-$(date +%Y%m%d).log"
WORKDIR="$HOME/dev/projects/self/my-project"
NTFY_TOPIC="your-ntfy-topic"  # ntfy.sh の通知先

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG"
}

log "夜間バッチ開始"

# テストを実行
cd "$WORKDIR"
TEST_OUTPUT=$(npm test -- --passWithNoTests 2>&1)
TEST_EXIT=$?

if [ $TEST_EXIT -ne 0 ]; then
  log "テスト失敗を検出。Claude Code にデバッグを依頼します"

  INSTRUCTION="以下のテスト失敗を修正してください。修正後、テストが通ることを確認してからコミットしてください。

テスト出力:
$TEST_OUTPUT"

  echo "$INSTRUCTION" > "$PIPE"

  # Claude が処理する時間を待つ（最大15分）
  sleep 900

  # 通知
  curl -s -d "夜間バッチ完了: テスト失敗を検出し Claude Code に修正を依頼しました" \
    "https://ntfy.sh/$NTFY_TOPIC"
else
  log "テスト全件通過"
  curl -s -d "夜間バッチ完了: テスト全件通過" "https://ntfy.sh/$NTFY_TOPIC"
fi
```

crontab への登録:

```bash
# crontab -e で追加
# 毎朝 3:00 に実行
0 3 * * * /bin/bash $HOME/dev/scripts/nightly-batch.sh
```

### パターン比較

| パターン | リアルタイム性 | セットアップ難易度 | 適したユースケース |
|---------|:----------:|:-----------:|----------------|
| GitHub Webhook | △（秒単位） | 中 | Issue対応、PRコメント処理 |
| Slack Bot | ◎（即時） | 中 | アドホックな指示 |
| cron バッチ | ✗（定時のみ） | 低 | 定常メンテナンス |

---

## 3. セキュリティ・コスト管理

### 実行権限の制限

Claude Code が触れるディレクトリを明示的に制限します。`--allowedTools` フラグでツール使用も絞れます。

```bash
# 特定ディレクトリのみ操作を許可
cat /tmp/claude-agent-input | claude \
  --dangerously-skip-permissions \
  --allowedTools "Read,Write,Bash" \
  2>&1
```

より厳密にやるなら、Docker コンテナ内で動かす構成も有効です。

```dockerfile
# Dockerfile.claude-sandbox
FROM node:20-slim

RUN npm install -g @anthropic-ai/claude-code

WORKDIR /workspace

# 操作対象のコードだけをマウント
# docker run -v $(pwd):/workspace --env ANTHROPIC_API_KEY=$KEY claude-sandbox
CMD ["sh", "-c", "cat /tmp/input | claude --dangerously-skip-permissions"]
```

### トークン消費の監視

API コストが予期せず膨らまないよう、日次の使用量をチェックするスクリプトを常駐させます。

```python
# scripts/cost-monitor.py
import os
import json
import datetime
import httpx

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
DAILY_LIMIT_USD = float(os.environ.get("DAILY_LIMIT_USD", "5.0"))
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")


def get_usage_today() -> dict:
    """Anthropic API から当日の使用量を取得する"""
    today = datetime.date.today().isoformat()
    resp = httpx.get(
        "https://api.anthropic.com/v1/usage",
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
        },
        params={"start_date": today, "end_date": today},
    )
    resp.raise_for_status()
    return resp.json()


def notify(message: str) -> None:
    if NTFY_TOPIC:
        httpx.post(f"https://ntfy.sh/{NTFY_TOPIC}", data=message.encode())


def main():
    try:
        usage = get_usage_today()
        total_cost = usage.get("total_cost_usd", 0)

        print(f"本日の使用量: ${total_cost:.4f} / 上限: ${DAILY_LIMIT_USD:.2f}")

        if total_cost >= DAILY_LIMIT_USD * 0.8:
            notify(f"[警告] Claude API コスト: ${total_cost:.4f} (上限の80%超)")

        if total_cost >= DAILY_LIMIT_USD:
            notify(f"[緊急] Claude API 日次上限到達: ${total_cost:.4f}")
            # エージェントプロセスを停止
            os.system("tmux kill-session -t claude-agent")
            notify("Claude agent を緊急停止しました")

    except Exception as e:
        print(f"使用量取得エラー: {e}")


if __name__ == "__main__":
    main()
```

`cron` で30分ごとに実行します。

```bash
# 30分ごとにコスト確認
*/30 * * * * python3 $HOME/dev/scripts/cost-monitor.py
```

### 監査ログの設計

誰が・いつ・何を指示したかを記録しておくと、問題発生時のトレースが楽になります。

```python
# scripts/audit-logger.py
import sqlite3
import datetime
import json
from pathlib import Path

DB_PATH = Path.home() / "dev" / "logs" / "claude-audit.db"


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            source TEXT NOT NULL,
            instruction TEXT NOT NULL,
            metadata TEXT
        )
    """)
    conn.commit()
    conn.close()


def log_instruction(source: str, instruction: str, metadata: dict = None):
    """指示内容を監査ログに記録する"""
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO audit_log (timestamp, source, instruction, metadata) VALUES (?, ?, ?, ?)",
        (
            datetime.datetime.now().isoformat(),
            source,
            instruction,
            json.dumps(metadata or {}),
        ),
    )
    conn.commit()
    conn.close()


def send_to_claude_with_log(source: str, instruction: str, metadata: dict = None):
    """ログを記録してから Claude Code に送信する"""
    log_instruction(source, instruction, metadata)
    with open("/tmp/claude-agent-input", "w") as pipe:
        pipe.write(instruction + "\n")
```

---

## 4. 実践レシピ

### レシピ①: 寝ている間に PR を作る

GitHub Issues の "claude-task" ラベルが付いた Issue を毎晩自動処理するスクリプトです。

```bash
#!/bin/bash
# scripts/nightly-issue-processor.sh

REPO="yourname/your-repo"
PIPE="/tmp/claude-agent-input"
GH_TOKEN="${GITHUB_TOKEN}"

# "claude-task" ラベルの未クローズ Issue を取得
ISSUES=$(curl -s \
  -H "Authorization: token $GH_TOKEN" \
  "https://api.github.com/repos/$REPO/issues?labels=claude-task&state=open" \
  | python3 -c "
import json, sys
issues = json.load(sys.stdin)
for i in issues:
    print(f\"{i['number']}||{i['title']}||{i['body'] or ''}\")
")

while IFS='||' read -r number title body; do
  [ -z "$number" ] && continue

  echo "Processing Issue #$number: $title"

  INSTRUCTION="GitHub Issue #${number} を実装してください。

タイトル: ${title}
内容: ${body}

手順:
1. git checkout -b feature/issue-${number}
2. 実装
3. テスト実行 (npm test)
4. git commit -m 'feat: close #${number} ${title}'
5. git push origin feature/issue-${number}
6. gh pr create --title 'feat: close #${number}' --body 'Closes #${number}'

完了したら Done と出力してください。"

  echo "$INSTRUCTION" > "$PIPE"

  # 1 Issue あたり最大20分待機
  sleep 1200

done <<< "$ISSUES"
```

### レシピ②: テスト失敗の自動デバッグループ

CI が落ちたら Claude Code に自動で修正させ、最大3回リトライする構成です。

```python
# scripts/auto-debug-loop.py
import subprocess
import time


PIPE_PATH = "/tmp/claude-agent-input"
MAX_RETRIES = 3
WAIT_SEC = 300  # 修正を待つ時間（秒）


def run_tests() -> tuple[bool, str]:
    """テストを実行して結果を返す"""
    result = subprocess.run(
        ["npm", "test", "--", "--passWithNoTests"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    return result.returncode == 0, result.stdout + result.stderr


def send_to_claude(instruction: str) -> None:
    with open(PIPE_PATH, "w") as pipe:
        pipe.write(instruction + "\n")


def main():
    for attempt in range(1, MAX_RETRIES + 1):
        print(f"[{attempt}/{MAX_RETRIES}] テスト実行中...")
        passed, output = run_tests()

        if passed:
            print("テスト全件通過！")
            return

        print(f"テスト失敗。Claude Code にデバッグを依頼します（試行 {attempt}/{MAX_RETRIES}）")

        instruction = f"""以下のテストが失敗しています。原因を調査して修正してください。
修正後、テストが通ることを確認してからコミットしてください。

【テスト出力】
{output}

修正が完了したら FIXED と出力してください。
修正が難しい場合は ESCALATE と出力してください。"""

        send_to_claude(instruction)

        print(f"{WAIT_SEC}秒待機中...")
        time.sleep(WAIT_SEC)

    # 最大リトライ数に達した場合はエスカレーション
    print("最大リトライ数に達しました。手動対応が必要です。")
    # Pushover や ntfy で通知する処理をここに追加


if __name__ == "__main__":
    main()
```

---

## 5. トラブルシューティング

### セッションが途中で切れる（context limit）

Claude Code はコンテキストウィンドウの上限に達するとセッションをリセットします。長時間のタスクでは、タスクを小さな単位に分割して送ることで回避できます。

```bash
# 大きなタスクを分割して送る例
echo "Step 1: src/api/ ディレクトリの型エラーのみ修正してください" > "$PIPE"
sleep 300
echo "Step 2: src/utils/ ディレクトリの型エラーのみ修正してください" > "$PIPE"
```

### Webhook が届かない（ngrok/Cloudflare Tunnel の切断）

ngrok の無料プランはセッションが 8 時間で切れます。Cloudflare Tunnel を使うと URL が固定されて安定します。

```bash
# cloudflared で固定 URL を取得（要 Cloudflare アカウント）
cloudflared tunnel create my-claude-agent
cloudflared tunnel route dns my-claude-agent webhook.yourdomain.com
cloudflared tunnel run my-claude-agent
```

### コスト爆発の緊急停止

```bash
#!/bin/bash
# scripts/emergency-stop.sh

# Claude エージェントを即時停止
tmux kill-session -t claude-agent 2>/dev/null
pkill -f "claude" 2>/dev/null

# FIFO をクリア
rm -f /tmp/claude-agent-input

echo "Claude agent を緊急停止しました"
```

これをショートカットに登録しておくと、予期しないコスト増加時にすぐ止められます。

---

## まとめ

Claude Code の Remote Control 構成で実現できることをまとめます。

| やりたいこと | 構成 |
|------------|------|
| GitHub Issue を自動実装 | Webhook 受信 + named pipe |
| Slack から即座に指示 | Slack Bolt + Socket Mode |
| 毎晩テストと修正を自動実行 | cron + バッチスクリプト |
| コスト上限を超えたら自動停止 | cron + 使用量 API 監視 |
| 全指示の監査ログ | SQLite + audit-logger |

最初は「cron + named pipe」のシンプルな構成から始めて、徐々に Slack Bot などを追加していくのが現実的です。

個人開発の最大のボトルネックは「席を離れると作業が止まる」こと。Remote Control 構成を組んでおくと、通勤中や睡眠中も開発が前に進むようになります。ぜひ試してみてください。

---

## 参考リンク

- [Claude Code 公式ドキュメント](https://docs.anthropic.com/ja/docs/claude-code)
- [Slack Bolt for Python](https://slack.dev/bolt-python/)
- [Cloudflare Tunnel ドキュメント](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/)
- [ntfy.sh — セルフホスト可能なプッシュ通知](https://ntfy.sh/)
