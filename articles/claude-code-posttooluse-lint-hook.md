---
title: "Claude Code PostToolUse HookでWrite/Edit後に自動lintを走らせる実装ガイド"
emoji: "🔨"
type: "tech"
topics: ["claudecode", "hooks", "eslint", "automation", "devproductivity"]
published: false
status: "draft"
publication_name: "correlate_dev"
---

## はじめに

Claude CodeでTypeScriptファイルを編集したとき、ESLintのエラーに気づくのが「コミット直前」になっていませんか。

Pre-commitフックやCIでの検出は有効ですが、修正がClaudeによる自動編集であれば、編集の直後にlintを走らせてフィードバックを返す方が効率的です。Claude Codeには **PostToolUse Hook** という仕組みがあり、`Write`や`Edit`ツールが完了した直後に任意のコマンドを実行できます。

本記事では、PostToolUse Hookを使ってファイル編集直後にESLint（TypeScript/TSX）とflake8（Python）を自動実行し、結果を `additionalContext` でClaude Codeに渡す実装を解説します。実際に運用しているスクリプトを全公開しながら、macOS固有の罠やESLint 9+対応の考慮点も含めて説明します。

:::message
本記事のスクリプトは筆者の環境（Mac mini M4 Pro、macOS Sequoia、Node.js v24、Python 3.12）で実際に動作しています。DAレビューによる5ラウンドの品質改善を経た実装です。
:::

## Claude Code Hookの基礎知識

### Hookとは何か

Claude CodeのHookは、特定のイベント（ツールの使用前後など）に反応して外部コマンドを自動実行する仕組みです。`~/.claude/settings.json`に設定を記述します。

現在サポートされているHookのイベントは以下の4種類です。

| イベント | タイミング |
| --- | --- |
| `PreToolUse` | ツール実行前 |
| `PostToolUse` | ツール実行後 |
| `Notification` | 通知発生時 |
| `Stop` | Claudeの応答終了時 |

### PreToolUseとPostToolUseの違い

**PreToolUse** はツール実行を「許可する/ブロックする」の判断に使います。たとえば「廃止済みドメインへの書き込みをブロックする」「本番ブランチへの直接コミットを防ぐ」といった用途です。

**PostToolUse** はツール実行後の通知や情報追加に使います。lintの結果フィードバックはこちらが適しています。実行完了後に通知するだけなので、Claudeの作業をブロックしません。

### settingsへの設定方法

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Write|Edit",
        "hooks": [
          {
            "type": "command",
            "command": "/Users/yourname/.claude/hooks/post-write-lint.sh",
            "timeout": 15
          }
        ]
      }
    ]
  }
}
```

`matcher`にツール名を正規表現で指定します。`Write|Edit`と書くと、WriteとEditの両方にマッチします。`timeout`はミリ秒ではなく秒単位です。

## 実装するlintスクリプトの設計方針

### 非ブロック型にする理由

PostToolUse Hookは実行後通知なので、理論上はブロック（`decision: "block"`）を返しても意味がありません。`additionalContext`を使って警告をClaude Codeのコンテキストに渡すだけにします。

これにより、lintエラーがあっても作業が止まらず、Claudeが自律的に「警告があった → 修正する」という判断ができます。

### 対応ファイルと使用ツール

| 拡張子 | lintツール | 検出方法 |
| --- | --- | --- |
| `.ts` / `.tsx` | ESLint | `package.json` を上位ディレクトリから探索 |
| `.py` | flake8 | `command -v` またはpython3モジュールとして検出 |
| その他 | スキップ | 対象外 |

### 実装上の制約

macOSには標準で `timeout` コマンドがありません。代わりに Homebrew の `coreutils` パッケージに含まれる `gtimeout` を使います。どちらも存在しない場合はlintをスキップします（Claude Codeをブロックしないための安全策）。

## 完全なスクリプト実装

```bash
#!/bin/bash
# PostToolUse Hook: Write/Edit 後に自動 lint
# - .ts/.tsx → ESLint (プロジェクトに eslint がある場合のみ)
# - .py → flake8 (インストール済みの場合のみ)
# - その他 → スキップ
# 非ブロック: additionalContext で警告を渡すだけ

set -uo pipefail

# 非ブロック保証: 予期しないエラーで Claude をブロックしない（全体ガード）
trap 'echo "post-write-lint: error at line $LINENO" >&2; exit 0' ERR

# jq が存在しない場合はスキップ
command -v jq &>/dev/null || exit 0

# macOS では timeout がない → gtimeout（coreutils）を使用
# TIMEOUT_CMD が空（両方なし）の場合は lint スキップ
if command -v timeout &>/dev/null; then
  TIMEOUT_CMD="timeout"
elif command -v gtimeout &>/dev/null; then
  TIMEOUT_CMD="gtimeout"
else
  # DoS リスク回避: タイムアウト手段がない環境では lint を実行しない
  exit 0
fi

INPUT=$(cat)
if [[ -z "$INPUT" ]]; then
  echo "post-write-lint: stdin empty, skipping" >&2
  exit 0
fi
FILE_PATH=$(jq -r '.tool_input.file_path // empty' <<< "$INPUT")

# ファイルパスが取れない場合はスキップ
if [[ -z "$FILE_PATH" ]] || [[ ! -f "$FILE_PATH" ]]; then
  exit 0
fi

# FILE_PATH の絶対パス・パストラバーサルガード
if [[ "$FILE_PATH" != /* ]]; then
  exit 0
fi
if [[ "$FILE_PATH" =~ (^|/)\.\.(\/|$) ]]; then
  exit 0
fi

# 拡張子で分岐
case "$FILE_PATH" in
  *.ts|*.tsx)
    # プロジェクトルートを探す（package.json のあるディレクトリ）
    DIR="$(dirname "$FILE_PATH")"
    PROJECT_ROOT=""
    DEPTH=0
    MAX_DEPTH=10
    while [[ "$DIR" != "/" ]] && [[ "$DEPTH" -lt "$MAX_DEPTH" ]]; do
      if [[ -f "$DIR/package.json" ]]; then
        PROJECT_ROOT="$DIR"
        break
      fi
      DIR="$(dirname "$DIR")"
      DEPTH=$((DEPTH + 1))
    done

    # eslint が見つからなければスキップ
    if [[ -z "$PROJECT_ROOT" ]]; then
      exit 0
    fi
    if [[ ! -f "$PROJECT_ROOT/node_modules/.bin/eslint" ]]; then
      exit 0
    fi

    # ESLint 9+ で設定ファイルが存在しない場合は exit code 2 になるためスキップ
    ESLINT_CONFIG_FOUND=0
    for cfg in "$PROJECT_ROOT/eslint.config.js" "$PROJECT_ROOT/eslint.config.mjs" \
               "$PROJECT_ROOT/.eslintrc" "$PROJECT_ROOT/.eslintrc.js" \
               "$PROJECT_ROOT/.eslintrc.cjs" "$PROJECT_ROOT/.eslintrc.json" \
               "$PROJECT_ROOT/.eslintrc.yaml" "$PROJECT_ROOT/.eslintrc.yml"; do
      if [[ -f "$cfg" ]]; then
        ESLINT_CONFIG_FOUND=1
        break
      fi
    done
    if [[ "$ESLINT_CONFIG_FOUND" -eq 0 ]]; then
      exit 0
    fi

    # ESLint 実行後に exit code を保存し、exit code 2（設定エラー）はスキップ
    ESLINT_EXIT=0
    RESULT=$($TIMEOUT_CMD 10 "$PROJECT_ROOT/node_modules/.bin/eslint" \
      --no-error-on-unmatched-pattern -- "$FILE_PATH" 2>&1) || ESLINT_EXIT=$?
    if [[ "$ESLINT_EXIT" -eq 2 ]]; then
      exit 0
    fi

    # 出力がなければ問題なし
    if [[ -z "$RESULT" ]]; then
      exit 0
    fi

    # 警告/エラーがあれば additionalContext で渡す
    CTX=$(printf 'ESLint 警告:\n%s' "$RESULT")
    jq -n --arg ctx "$CTX" '{
      hookSpecificOutput: {
        additionalContext: $ctx
      }
    }'
    ;;

  *.py)
    # flake8 の検出（コマンドまたはpython3モジュール）
    if command -v flake8 &>/dev/null; then
      FLAKE8_CMD=(flake8)
    elif python3 -m flake8 --version &>/dev/null 2>&1; then
      FLAKE8_CMD=(python3 -m flake8)
    else
      exit 0
    fi

    RESULT=$($TIMEOUT_CMD 10 "${FLAKE8_CMD[@]}" --max-line-length=120 -- "$FILE_PATH" 2>&1) || true

    if [[ -z "$RESULT" ]]; then
      exit 0
    fi

    CTX=$(printf 'flake8 警告:\n%s' "$RESULT")
    jq -n --arg ctx "$CTX" '{
      hookSpecificOutput: {
        additionalContext: $ctx
      }
    }'
    ;;

  *)
    # 対象外のファイルはスキップ
    exit 0
    ;;
esac
```

スクリプトは `~/.claude/hooks/post-write-lint.sh` に配置し、実行権限を付与します。

```bash
chmod +x ~/.claude/hooks/post-write-lint.sh
```

## 各セクションの詳細解説

### エラートラップによる全体ガード

```bash
trap 'echo "post-write-lint: error at line $LINENO" >&2; exit 0' ERR
```

`set -uo pipefail` を設定しているため、未定義変数参照やパイプのエラーでスクリプトが終了します。ただし、エラーで exit code 1 を返すと Claude Codeがblockと解釈する可能性があります。`trap ... ERR` でキャッチして `exit 0` を返すことで、予期しないエラーでもClaude Codeの動作に影響しないよう保証します。

### stdinからのJSON解析

Claude CodeはHookスクリプトのstdinにJSONを渡します。

```json
{
  "tool_name": "Write",
  "tool_input": {
    "file_path": "/path/to/edited/file.ts",
    "content": "..."
  }
}
```

`jq -r '.tool_input.file_path // empty'` で `file_path` を取り出します。`// empty` は値がnullの場合に空文字を返すためのガードです。

### macOSでのtimeout問題

macOSには `timeout` コマンドが付属していません。Linux環境では使えますが、macで同じスクリプトを使うと即座にコマンド not found で失敗します。

```bash
if command -v timeout &>/dev/null; then
  TIMEOUT_CMD="timeout"
elif command -v gtimeout &>/dev/null; then
  TIMEOUT_CMD="gtimeout"
else
  exit 0
fi
```

Homebrewで `coreutils` をインストールすると `gtimeout` が使えます。

```bash
brew install coreutils
```

タイムアウト手段が一切ない場合は、無制限に実行される危険を避けるためlintをスキップします。

### package.jsonによるプロジェクトルート探索

Claudeが編集するファイルは `src/components/Button.tsx` のようにネストされたパスにあります。`node_modules/.bin/eslint` を使うには、プロジェクトルートを見つける必要があります。

```bash
DIR="$(dirname "$FILE_PATH")"
PROJECT_ROOT=""
DEPTH=0
MAX_DEPTH=10
while [[ "$DIR" != "/" ]] && [[ "$DEPTH" -lt "$MAX_DEPTH" ]]; do
  if [[ -f "$DIR/package.json" ]]; then
    PROJECT_ROOT="$DIR"
    break
  fi
  DIR="$(dirname "$DIR")"
  DEPTH=$((DEPTH + 1))
done
```

親ディレクトリを最大10階層まで辿って `package.json` を探します。`MAX_DEPTH`を設けているのは、ファイルシステムのルートまで辿り続けるのを防ぐためです。

### ESLint 9+の設定ファイルチェック

ESLint 9.0から設定ファイルのフォーマットが変わり（`eslint.config.js` が正式サポート）、設定ファイルが存在しない状態でESLintを実行すると exit code 2 が返るようになりました。

exit code 2はESLint的には「設定エラー」です。これを「lintエラーあり」と誤解釈しないよう、実行前に設定ファイルの存在を確認します。

```bash
for cfg in "$PROJECT_ROOT/eslint.config.js" "$PROJECT_ROOT/eslint.config.mjs" \
           "$PROJECT_ROOT/.eslintrc" "$PROJECT_ROOT/.eslintrc.js" \
           "$PROJECT_ROOT/.eslintrc.cjs" "$PROJECT_ROOT/.eslintrc.json" \
           "$PROJECT_ROOT/.eslintrc.yaml" "$PROJECT_ROOT/.eslintrc.yml"; do
  if [[ -f "$cfg" ]]; then
    ESLINT_CONFIG_FOUND=1
    break
  fi
done
```

新旧の設定ファイル名を網羅しているため、ESLint 8系・9系のどちらでも動作します。

### additionalContextで結果を渡す

```bash
CTX=$(printf 'ESLint 警告:\n%s' "$RESULT")
jq -n --arg ctx "$CTX" '{
  hookSpecificOutput: {
    additionalContext: $ctx
  }
}'
```

`additionalContext` はClaude Codeのコンテキストに追加情報として挿入されます。Claudeはこの内容を受け取ると、次のターンで「ESLint警告があった → 修正する」という判断ができます。

`printf` を使っているのは、文字化けを防ぐためです。`echo` よりも `printf` の方が変数展開時の挙動が安定します。

### flake8の二段階検出

```bash
if command -v flake8 &>/dev/null; then
  FLAKE8_CMD=(flake8)
elif python3 -m flake8 --version &>/dev/null 2>&1; then
  FLAKE8_CMD=(python3 -m flake8)
else
  exit 0
fi
```

`flake8` をグローバルにインストールしている場合はそのまま使い、virtualenvに入っている場合は `python3 -m flake8` として実行します。配列変数 `FLAKE8_CMD` で構築することで、後の呼び出し `"${FLAKE8_CMD[@]}"` が正しくクォーティングされます。

## settings.jsonへの設定

作成したスクリプトを `~/.claude/settings.json` に登録します。

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Write|Edit",
        "hooks": [
          {
            "type": "command",
            "command": "/Users/yourname/.claude/hooks/post-write-lint.sh",
            "timeout": 15
          }
        ]
      }
    ]
  }
}
```

`command` はホームディレクトリ展開（`~/`）がされないため、絶対パスで記述します。`timeout` は15秒を推奨します。大規模なTypeScriptプロジェクトでは、ESLintの初回実行が10秒前後かかることがあります。

## 実際の動作確認

スクリプトを配置して設定を記述したら、動作確認します。

### テスト用のTypeScriptファイルを作成

```typescript
// test.ts
const x = 1
const y = 2
console.log(x)
// yは未使用のままにしておく
```

Claude Codeで「test.tsを開いてyに100を足してください」と依頼します。Editツールが実行された後、PostToolUse Hookが起動し、ESLintが実行されます。

`no-unused-vars` ルールが有効であれば、以下のような `additionalContext` がClaude Codeに渡ります。

```
ESLint 警告:
/path/to/test.ts
  4:7  warning  'y' is assigned a value but never used  @typescript-eslint/no-unused-vars
```

Claudeはこの警告を受け取り、次のターンで「ESLintのno-unused-varsの警告があります。yを使用するか削除してよいですか？」のように提案します。

### デバッグ用のスタンドアロン実行

スクリプト単体で動作確認する場合は、stdinにJSONを渡します。

```bash
echo '{"tool_input":{"file_path":"/path/to/your/file.ts"}}' \
  | ~/.claude/hooks/post-write-lint.sh
```

正常に動作すれば、ESLintの結果またはなにも出力されません（警告ゼロの場合）。

## 実運用で遭遇したトラブルと対処

### トラブル1: スクリプトが常にexit code 1を返す

`set -e` または `set -uo pipefail` の環境下では、コマンドが失敗するとすぐにスクリプトが終了します。ESLintがlintエラーを検出した場合はexit code 1を返すため、そこでスクリプトが止まってしまいます。

対処は2段階です。まず `trap ... ERR` で全体をガード。そしてESLint実行時は `|| ESLINT_EXIT=$?` で終了コードを変数に保存し、スクリプト自体は続行させます。

```bash
ESLINT_EXIT=0
RESULT=$($TIMEOUT_CMD 10 "$PROJECT_ROOT/node_modules/.bin/eslint" \
  --no-error-on-unmatched-pattern -- "$FILE_PATH" 2>&1) || ESLINT_EXIT=$?
```

### トラブル2: ESLint 9でexit code 2が返る

ESLint設定ファイルが存在しないプロジェクトで実行すると、ESLint 9は「設定が見つからない」として exit code 2 を返します。exit code 2をlintエラー（exit code 1）と同一視すると、空のRESULTを `additionalContext` に渡してしまいます。

対処は実行前の設定ファイル存在チェックです。設定ファイルが1つも見つからなければ、そのプロジェクトではlintをスキップします。

### トラブル3: macOSでgtimeoutが見つからない

`gtimeout` は `brew install coreutils` でインストールできますが、環境によってはPATHに含まれていないことがあります。

```bash
# PATHを確認
which gtimeout
# 見つからない場合
echo $PATH
```

Homebrewのインストール先が `/opt/homebrew/bin`（Apple Silicon Mac）の場合、`.zshrc` や `.bashrc` に以下を追記します。

```bash
export PATH="/opt/homebrew/bin:$PATH"
```

ただし、Claude Codeが起動するシェルのPATHは対話的なシェルと異なることがあります。`settings.json` の `env.PATH` にも追記しておくと確実です。

```json
{
  "env": {
    "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
  }
}
```

### トラブル4: パストラバーサル攻撃への対処

`tool_input.file_path` は外部から注入される値です。`../../etc/passwd` のようなパスが渡された場合に備え、2つのガードを設けています。

```bash
# 絶対パスでなければスキップ
if [[ "$FILE_PATH" != /* ]]; then
  exit 0
fi
# ../ を含む場合はスキップ
if [[ "$FILE_PATH" =~ (^|/)\.\.(\/|$) ]]; then
  exit 0
fi
```

Claude Codeのローカル環境で使うスクリプトなのでリスクは低いですが、防御的なコーディングとして実装しています。

## 複数Hookを組み合わせる設計

PostToolUseには複数のHookを登録できます。筆者の環境では、lintの他に2つのHookを `Write|Edit` イベントに設定しています。

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Write|Edit",
        "hooks": [
          {
            "type": "command",
            "command": "/Users/naoyayokota/.claude/hooks/post-write-lint.sh",
            "timeout": 15
          },
          {
            "type": "command",
            "command": "/Users/naoyayokota/.claude/hooks/post-write-sync-check.sh",
            "timeout": 5
          },
          {
            "type": "command",
            "command": "/Users/naoyayokota/.claude/hooks/post-write-devserver-check.sh",
            "timeout": 20
          }
        ]
      }
    ]
  }
}
```

`post-write-sync-check.sh` はja/enの同時更新が必要なファイルで片方だけ更新された場合に警告を出します。`post-write-devserver-check.sh` は開発サーバーが起動していない状態でコードを編集した場合にリマインドを出します。

複数Hookは上から順に実行されます。いずれかがblockを返すと後続は実行されません（PostToolUseの場合、blockは返さない設計にしておくべきです）。

## PreToolUseとの組み合わせ

lintはPostToolUseで走らせますが、危険な操作のブロックにはPreToolUseを使います。

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Write|Edit",
        "hooks": [
          {
            "type": "command",
            "command": "/Users/naoyayokota/.claude/hooks/check-domain.sh",
            "timeout": 10
          }
        ]
      }
    ]
  }
}
```

`check-domain.sh` は廃止済みドメインへの書き込みを検知してブロックします。PreToolUseで「書かせない」、PostToolUseで「書いた後に品質チェック」という役割分担です。

## additionalContextの活用パターン

`additionalContext` はClaude Codeのコンテキストに文字列を追加するシンプルな仕組みです。使い方次第でさまざまな情報を渡せます。

### lintエラー（本記事の実装）

```bash
jq -n --arg ctx "$CTX" '{
  hookSpecificOutput: {
    additionalContext: $ctx
  }
}'
```

### ファイルサイズ警告

```bash
SIZE=$(wc -c < "$FILE_PATH")
if [[ "$SIZE" -gt 100000 ]]; then
  jq -n --arg ctx "警告: ファイルサイズが100KB超（${SIZE}バイト）です" '{
    hookSpecificOutput: { additionalContext: $ctx }
  }'
fi
```

### 依存パッケージの更新通知

```bash
if [[ "$FILE_PATH" == *"/package.json" ]]; then
  jq -n --arg ctx "package.jsonが更新されました。pnpm installの実行を検討してください" '{
    hookSpecificOutput: { additionalContext: $ctx }
  }'
fi
```

`additionalContext` に渡した文字列はClaude Codeの次のターンで参照できます。具体的で簡潔な内容にすることで、Claudeが正しく解釈して行動できます。

## まとめ

PostToolUse Hookによる自動lintは、以下の設計原則で実装します。

| 原則 | 実装 |
| --- | --- |
| 非ブロック | `exit 0` と `additionalContext` で通知のみ |
| macOS対応 | `timeout`/`gtimeout` の二段階検出 |
| ESLint 9+ 対応 | 設定ファイル存在チェック + exit code 2のスキップ |
| セキュリティ | 絶対パス確認 + パストラバーサルガード |
| 堅牢性 | `trap ERR exit 0` で全体をガード |

Claude Codeが自律的にlint警告を受け取り、次のターンで修正を提案するようになると、「Claudeが書いたコードをlintが壊す」という状況を大幅に減らせます。

設定は15分もあれば完了します。ぜひ試してみてください。

---

## 参考リソース

- [Claude Code Hooks - 公式ドキュメント](https://docs.anthropic.com/en/docs/claude-code/hooks)
- [ESLint 9 Migration Guide](https://eslint.org/docs/latest/use/migrate-to-9.0.0)
- [Flake8 Documentation](https://flake8.pycqa.org/en/latest/)

---

**関連記事**

- [CLAUDE.mdをKnowledge Files化して67%スリム化した実践記録](https://zenn.dev/correlate_dev/articles/claude-code-knowledge-files)
- [Claude Codeの7つの拡張機能を「所有権モデル」で整理する](https://zenn.dev/correlate_dev/articles/context-ownership-model-claude-code)

> [correlate_dev](https://zenn.dev/p/correlate_dev) では、Claude Code・GCP・Pythonを使った開発自動化の実践知を発信しています。
