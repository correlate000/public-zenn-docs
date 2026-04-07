---
title: "npm緊急対応チェックリスト + Claude Code自動監視エージェント——最小設定から自動防御へ"
emoji: "🔒"
type: "tech"
topics: ["npm", "claudecode", "security", "nodejs", "automation"]
published: false
status: "draft"
publication_name: "correlate_dev"
---

## はじめに

npmセキュリティの問題は「発覚してから対応する」では遅いことがあります。`axios` のサプライチェーン攻撃が話題になったとき、自分のプロジェクトに影響があるか確認するだけで数時間を要しました。

もう一つの問題は、`.npmrc` のセキュア設定について「設定すべき項目は知っているが、全プロジェクトに適用するのが面倒で後回し」になりがちなことです。

この記事では2つのアプローチを組み合わせます。

1. **`.npmrc` の最小セキュア設定 ** （今すぐできる基盤）
2. **Claude Codeを使った自動監視エージェント ** （仕組みで継続的に守る）

---

## Part 1: .npmrc の最小セキュア設定

### なぜ .npmrc が重要か

npm はデフォルト設定のままでは、セキュリティ的に望ましくない挙動をとることがあります。

| 問題 | デフォルト挙動 | リスク |
|------|-------------|------|
| スクリプト自動実行 | `postinstall` などが自動実行される | 悪意あるパッケージによるコード実行 |
| レジストリ検証なし | パッケージのintegrity検証が弱い | 改ざんされたパッケージの混入 |
| audit警告の無視 | 脆弱性があっても無視できる設定 | 既知の脆弱性を放置 |
| ログへの認証情報 | npm-debug.logに認証情報が含まれる可能性 | 誤ってコミットしたときの漏洩 |

### 推奨する .npmrc 設定

```ini
# ~/.npmrc （グローバル設定）または プロジェクト直下の .npmrc

# ===== セキュリティ設定 =====

# パッケージのintegrity（ハッシュ）検証を強制する
# インストール時に package-lock.json のハッシュと照合する
audit=true

# audit結果が高・重大レベルの場合はインストールを失敗させる
# 低・中レベルの脆弱性は許容する場合は "moderate" または "low" に変更
audit-level=high

# スクリプトの自動実行を無効化する（最も重要な設定の一つ）
# postinstall, preinstall などが実行されなくなる
# ※ 一部のパッケージで手動ビルドが必要になる場合がある
ignore-scripts=false  # true にすると安全だがビルドが必要なパッケージで問題になりうる

# package-lock.json を必ず使用する（lock ファイルなしのインストールを禁止）
save-exact=false

# ===== ログとデータ設定 =====

# npm が収集するメトリクスを無効化する
send-metrics=false

# ===== レジストリ設定 =====

# 公式レジストリのみを使用（必要に応じてプロジェクト別に上書き）
registry=https://registry.npmjs.org/

# ===== 出力設定 =====

# 不要なログを抑制する
loglevel=warn
```

:::message
`ignore-scripts=true` は最も効果的なセキュリティ設定ですが、`esbuild` や `node-sass` など、ネイティブコードのビルドが必要なパッケージで問題が発生します。プロジェクトの依存関係を確認してから適用してください。
:::

### プロジェクト別の .npmrc

グローバル設定とは別に、プロジェクトのルートに `.npmrc` を置くことで、プロジェクト固有の設定を上書きできます。

```ini
# プロジェクト直下の .npmrc

# プライベートレジストリを使う場合
# registry=https://npm.pkg.github.com/
# //npm.pkg.github.com/:_authToken=${NPM_TOKEN}

# このプロジェクトでは scripts を許可する（ビルドが必要なため）
ignore-scripts=false
```

### 設定の検証スクリプト

```bash
#!/bin/bash
# check-npmrc.sh

echo "=== .npmrc セキュリティ設定の確認 ==="

# グローバル設定を確認
echo ""
echo "--- グローバル設定 ---"
npm config get audit
npm config get audit-level
npm config get ignore-scripts
npm config get send-metrics
npm config get registry

# プロジェクト設定を確認（実行ディレクトリの .npmrc）
echo ""
echo "--- プロジェクト設定 ($(pwd)) ---"
if [ -f ".npmrc" ]; then
    cat .npmrc
else
    echo "プロジェクト .npmrc なし（グローバル設定が適用される）"
fi

# 既知の脆弱性を確認
echo ""
echo "--- npm audit 結果 ---"
npm audit --audit-level=high
```

---

## Part 2: npm緊急対応チェックリスト

セキュリティインシデントが発生したとき、パニックにならずに対応するためのチェックリストです。

### ケース1: 使用中のパッケージに脆弱性が発覚した

```bash
# Step 1: 影響を確認する
npm audit

# Step 2: 自動修正を試みる
npm audit fix

# Step 3: 自動修正で解決しない場合（メジャーバージョンアップが必要なケース）
npm audit fix --force  # ← 破壊的変更が入る可能性があるため慎重に

# Step 4: 特定パッケージを手動アップデート
npm install package-name@latest

# Step 5: テストを実行して問題がないか確認
npm test
```

:::message alert
`npm audit fix --force` は依存関係を強制的に変更するため、本番環境では必ずステージング環境でテストしてから適用してください。
:::

### ケース2: 不審なパッケージがインストールされた疑い

```bash
# Step 1: インストールされているパッケージを確認
npm list --depth=0  # トップレベルのみ
npm list           # 全ての依存関係（大量に出る）

# Step 2: 最近インストールされたパッケージを確認
# node_modules の更新日時で判断
ls -lt node_modules | head -20

# Step 3: package.json と package-lock.json の差分確認
git diff HEAD package.json
git diff HEAD package-lock.json

# Step 4: 不審なパッケージの詳細を確認
npm info suspicious-package
npm view suspicious-package scripts  # インストールスクリプトを確認
```

### ケース3: サプライチェーン攻撃を検知した（または疑った）

```bash
# Step 1: 即座に開発を停止
# ネットワーク接続を切断することも検討

# Step 2: 問題のあるパッケージをアンインストール
npm uninstall compromised-package

# Step 3: node_modules を完全削除して再インストール
rm -rf node_modules
npm ci  # package-lock.json に基づいた厳密なインストール

# Step 4: 認証情報が漏洩した可能性がある場合
# npm のアクセストークンを即座に無効化
npm token revoke [token-id]
npm token list  # 現在のトークン一覧を確認
```

---

## Part 3: Claude Code自動監視エージェントの構築

「チェックリストを参照して手動対応する」だけでは、インシデント発生後の対応になります。Claude Codeを使って、 ** 脆弱性の検知から初期対応まで自動化 ** するエージェントを構築します。

### エージェントの設計

```
[毎日の定期実行]
    ↓
npm audit 実行
    ↓
結果を解析
    ↓
High/Critical があれば
    ├── 自動修正を試みる（npm audit fix）
    ├── 修正できなければIssueをGitHubに作成
    └── Discordに通知
```

### Claude Code スラッシュコマンドとして実装

まず、`.claude/commands/npm-security-check.md` としてスラッシュコマンドを定義します。

```markdown
<!-- .claude/commands/npm-security-check.md -->

# npm セキュリティチェックと自動修正

## 目的
現在のプロジェクトの npm 依存関係のセキュリティを確認し、
High 以上の脆弱性を自動修正する。

## 実行手順

1. `npm audit --json` を実行してJSONで結果を取得する
2. High/Critical の脆弱性を抽出してリスト化する
3. `npm audit fix` で自動修正を試みる
4. 修正結果を確認し、残存する脆弱性をレポートする
5. 修正した場合は変更内容を git commit する
6. レポートを出力する

## 出力形式

以下の形式でレポートを作成すること:
- 検出した脆弱性の一覧（パッケージ名・深刻度・CVE番号）
- 自動修正できたもの / できなかったもの
- 手動対応が必要な項目のアクションリスト

## 注意事項
- `npm audit fix --force` は実行しない（破壊的変更リスク）
- 本番環境では実行しない（ステージング環境での確認を推奨）
```

### Python監視スクリプト

```python
# npm_security_monitor.py

import subprocess
import json
import os
from datetime import datetime
from pathlib import Path
from anthropic import Anthropic

client = Anthropic()

def run_npm_audit(project_path: str) -> dict:
    """npm audit を実行してJSONで結果を取得する"""
    result = subprocess.run(
        ["npm", "audit", "--json"],
        cwd=project_path,
        capture_output=True,
        text=True
    )
    # npm audit は脆弱性があると非ゼロ終了コードを返すため
    # returncode で判定せず、出力をパースする
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"error": result.stderr, "raw": result.stdout}


def extract_high_vulnerabilities(audit_result: dict) -> list[dict]:
    """High/Critical レベルの脆弱性を抽出する"""
    vulnerabilities = []

    if "vulnerabilities" not in audit_result:
        return vulnerabilities

    for pkg_name, vuln_data in audit_result["vulnerabilities"].items():
        severity = vuln_data.get("severity", "")
        if severity in ("high", "critical"):
            vulnerabilities.append({
                "package": pkg_name,
                "severity": severity,
                "via": vuln_data.get("via", []),
                "fix_available": vuln_data.get("fixAvailable", False),
                "range": vuln_data.get("range", "")
            })

    return vulnerabilities


def attempt_auto_fix(project_path: str) -> dict:
    """npm audit fix を実行する（--force は使わない）"""
    result = subprocess.run(
        ["npm", "audit", "fix"],
        cwd=project_path,
        capture_output=True,
        text=True
    )
    return {
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr
    }


def analyze_with_claude(
    audit_result: dict,
    vulnerabilities: list[dict],
    fix_result: dict | None = None
) -> str:
    """Claude API で脆弱性を分析し、対応方針を提案する"""

    vuln_summary = json.dumps(vulnerabilities, ensure_ascii=False, indent=2)
    fix_summary = json.dumps(fix_result, ensure_ascii=False, indent=2) if fix_result else "未実施"

    prompt = f"""
以下のnpm audit結果を分析して、対応方針を日本語でレポートしてください。

## 検出された脆弱性（High/Critical）
{vuln_summary}

## 自動修正の結果
{fix_summary}

## レポートに含める内容
1. 各脆弱性の簡潔な説明（CVE番号があれば含める）
2. 実際の攻撃リスクの評価（開発環境のみの依存か、本番影響があるか）
3. 手動対応が必要な項目の具体的な手順
4. 優先度の高い順に並べた対応リスト

専門的かつ実用的な内容で、開発者がすぐに行動できる形式でまとめてください。
"""

    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}]
    )

    return response.content[0].text


def send_discord_notification(webhook_url: str, message: str) -> None:
    """Discord にレポートを送信する"""
    import urllib.request
    import urllib.parse

    payload = json.dumps({
        "content": message[:2000],  # Discord のメッセージ上限
        "username": "npm Security Monitor"
    })

    req = urllib.request.Request(
        webhook_url,
        data=payload.encode(),
        headers={"Content-Type": "application/json"},
        method="POST"
    )

    with urllib.request.urlopen(req) as response:
        if response.status not in (200, 204):
            raise RuntimeError(f"Discord通知失敗: {response.status}")


def main():
    project_path = os.environ.get("PROJECT_PATH", ".")
    discord_webhook = os.environ.get("DISCORD_WEBHOOK_URL")

    print(f"[{datetime.now().isoformat()}] npm セキュリティチェック開始: {project_path}")

    # 1. npm audit 実行
    audit_result = run_npm_audit(project_path)

    if "error" in audit_result:
        print(f"エラー: {audit_result['error']}")
        return

    # 2. 高リスク脆弱性を抽出
    vulnerabilities = extract_high_vulnerabilities(audit_result)

    if not vulnerabilities:
        print("High/Critical の脆弱性はありませんでした")
        return

    print(f"{len(vulnerabilities)}件のHigh/Critical脆弱性を検出")

    # 3. 自動修正を試みる
    fix_result = attempt_auto_fix(project_path)
    print(f"自動修正: {'成功' if fix_result['returncode'] == 0 else '一部未解決'}")

    # 4. Claude で分析
    report = analyze_with_claude(audit_result, vulnerabilities, fix_result)

    # 5. レポートを保存
    report_path = Path(f"npm-security-report-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md")
    report_path.write_text(report, encoding="utf-8")
    print(f"レポート保存: {report_path}")

    # 6. Discord に通知
    if discord_webhook:
        notification = f"**npm セキュリティアラート **\n{len(vulnerabilities)}件の High/Critical 脆弱性を検出。詳細はリポジトリのレポートを確認してください。"
        send_discord_notification(discord_webhook, notification)

    print(report)


if __name__ == "__main__":
    main()
```

### launchd での定期実行（macOS）

```xml
<!-- ~/Library/LaunchAgents/com.correlate.npm-security-monitor.plist -->
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.correlate.npm-security-monitor</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Users/yourname/.pyenv/shims/python3</string>
        <string>/path/to/npm_security_monitor.py</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PROJECT_PATH</key>
        <string>/path/to/your/project</string>
        <key>DISCORD_WEBHOOK_URL</key>
        <string>YOUR_WEBHOOK_URL</string>
    </dict>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>9</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>/tmp/npm-security-monitor.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/npm-security-monitor-error.log</string>
</dict>
</plist>
```

```bash
# launchd に登録する
launchctl load ~/Library/LaunchAgents/com.correlate.npm-security-monitor.plist

# 動作確認（即時実行）
launchctl start com.correlate.npm-security-monitor
```

---

## Claude Code Hooks との統合

Claude Code の Hooks 機能を使うと、git commit 時などに自動的にセキュリティチェックを走らせることができます。

```json
// .claude/settings.json

{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "bash -c 'if echo \"$CLAUDE_TOOL_INPUT\" | grep -q \"npm install\"; then npm audit --audit-level=high; fi'"
          }
        ]
      }
    ]
  }
}
```

このフックにより、Claude Code が `npm install` を実行しようとするたびに、事前に `npm audit` が走ります。高リスクの脆弱性が存在する場合は警告が表示されます。

---

## まとめ：最小設定から自動防御へのステップ

実装すべき順序を優先度別に整理します。

** 今すぐやる（5分）:**
- `~/.npmrc` に `audit=true` と `audit-level=high` を追加
- `npm audit` を手動で実行して現状を把握

** 今週やる（1時間）:**
- `.npmrc` のセキュア設定を全プロジェクトに適用
- 緊急対応チェックリストをチームで共有

** 今月やる（半日）:**
- `npm_security_monitor.py` を設定して定期実行
- Discord 通知を設定して可視化
- Claude Code Hooks で npm install 時の自動チェックを設定

セキュリティは「一度設定したら終わり」ではなく、継続的な運用が必要です。自動化によって「忘れて放置」を防ぐことが、長期的な安全につながります。

---

## 関連記事

- [Claude Code Hooks で開発フローを自動化する](/claude-code-hooks-complete-guide)
- [MCP Registry とプロンプトインジェクション対策](/mcp-registry-api-prompt-injection)
