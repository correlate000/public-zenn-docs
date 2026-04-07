---
title: "Figma MCP の Code to Canvas はビルトインコネクタでは使えない——正しい設定手順と落とし穴"
emoji: "🎨"
type: "tech"
topics: ["figma", "mcp", "claudecode", "ai", "design"]
published: false
status: "draft"
publication_name: "correlate_dev"
---

## はじめに

Figma MCP の「Code to Canvas」機能（`generate_figma_design` ツール）は、2026年2月17日のアップデートで正式リリースされました。Claude Code から自然言語でFigmaキャンバスにUIを生成できる、非常に魅力的な機能です。

しかし実際に使おうとすると、 **Claude Code のビルトインコネクタではこの機能が使えません ** 。

Figma Forum でも「ツールが表示されない」という報告が多数上がっており、公式ドキュメントにもこの区別が明確に書かれていません。本記事では、なぜ使えないのか・どう設定すれば使えるのかを、実際にはまった経験をもとに解説します。

---

## ビルトインコネクタとリモートMCPサーバーの違い

Claude Code には Figma との連携方法が2種類あります。

### ビルトインコネクタ（デフォルト）

Claude Code の設定画面から「Figma」を選択するだけで使えるコネクタです。特別な設定不要で、以下のようなツールが利用できます。

- `get_figma_data` — ファイル情報を取得する
- `get_node_by_id` — 特定ノードの詳細を取得する
- `get_images` — 画像データを取得する

これらはすべて **read-only** のツールです。Figmaファイルを「読む」ことしかできません。

### リモートMCPサーバー（手動追加）

`https://mcp.figma.com/mcp` に接続するリモートサーバーです。こちらには write 系のツールが含まれており、`generate_figma_design` もここに属します。

| 項目 | ビルトインコネクタ | リモートMCPサーバー |
|---|---|---|
| 設定 | Claude Code UI から選択するだけ | `claude mcp add` コマンドが必要 |
| 認証 | 自動 | OAuth（手動で承認が必要） |
| ツール数 | 一部（read-only） | 13ツール（write含む） |
| `generate_figma_design` | 使えない | 使える |

---

## 設定手順：リモートMCPサーバーを追加する

### 前提条件

まず以下を確認してください。

- **Figma アカウント **: Pro プラン以上、かつ Full seat であること
  - Starter プランや Viewer ロールでは Code to Canvas が利用できません
- **Claude Code**: 最新バージョンであること（`claude --version` で確認）

### Step 1: MCP サーバーを追加する

ターミナルで以下を実行します。

```bash
claude mcp add --transport http figma https://mcp.figma.com/mcp
```

このコマンドで、`~/.claude/settings.json` にサーバー設定が追加されます。

### Step 2: 設定を確認する

追加後、設定ファイルを確認します。

```bash
cat ~/.claude/settings.json
```

以下のような記述が追加されていれば成功です。

```json
{
  "mcpServers": {
    "figma": {
      "type": "http",
      "url": "https://mcp.figma.com/mcp"
    }
  }
}
```

### Step 3: Claude Code を再起動して OAuth 認証を行う

Claude Code を完全に終了し、再起動します。起動後、`/mcp` コマンドでサーバー一覧を確認します。

```
/mcp
```

Figma サーバーに「認証が必要」と表示されたら、表示されるURLをブラウザで開いてOAuth認証を完了します。Figma アカウントでログインし、アクセスを許可してください。

### Step 4: 接続確認

Claude Code のチャットで以下を試します。

```
Figma の自分のアカウント情報を教えてください
```

`whoami` ツールが呼ばれ、Figmaアカウントの情報が返れば接続成功です。

---

## generate_figma_design を使ってみる

接続が確認できたら、実際に Code to Canvas を試します。

### 基本的な使い方

Claude Code のチャットで、自然言語でデザインを指示します。

```
Figmaの [ファイルID] に、ログインフォームのコンポーネントを作成してください。
メールアドレスとパスワードの入力欄、ログインボタンを含めてください。
背景は白、ボタンは #0066FF のプライマリカラーで。
```

Claude Code が `generate_figma_design` ツールを呼び出し、Figmaキャンバス上にコンポーネントを生成します。

### ファイルIDの確認方法

FigmaファイルのURLから確認できます。

```
https://www.figma.com/file/XXXXXXXXXXXXXXXX/...
                           ^^^^^^^^^^^^^^^^
                           これがファイルID
```

### 実際の呼び出し例

内部的には以下のような形式でツールが呼ばれます。

```json
{
  "tool": "generate_figma_design",
  "params": {
    "description": "ログインフォーム、白背景、プライマリカラー #0066FF",
    "fileId": "XXXXXXXXXXXXXXXX",
    "parentNodeId": "0:1"
  }
}
```

---

## よくあるトラブルと対処法

### 「generate_figma_design ツールが見つからない」

** 原因 **: ビルトインコネクタのみ設定されている状態です。

** 対処 **: 前述の手順でリモートMCPサーバーを追加してください。`claude mcp list` で現在の設定を確認できます。

```bash
claude mcp list
```

### OAuth 認証後もツールが表示されない

** 対処 **: Claude Code を完全に再起動してください。プロセスが残っている場合、設定が反映されないことがあります。

```bash
# プロセスを確認
ps aux | grep claude

# 全プロセスを終了してから再起動
```

### 認証は通るが生成が失敗する

** 原因の候補 **:
1. Figmaアカウントが Pro プラン未満
2. Full seat ではなく Viewer ロールになっている
3. 指定したファイルIDへのアクセス権限がない

Figma の「Settings > Members」でロールを確認してください。

### ビルトインコネクタと重複設定してしまった

両方の設定が混在すると、read-only ツールが重複して表示されることがあります。ビルトインコネクタを無効化し、リモートMCPサーバーのみを使う設定にすることをおすすめします。

---

## ビルトインとリモートを使い分ける場面

両者の使い分けの目安です。

** ビルトインコネクタで十分な場面 **
- 既存のFigmaデザインをコードに変換したい（Design to Code）
- コンポーネントの構造を読み取りたい
- デザイントークンを確認したい

** リモートMCPサーバーが必要な場面 **
- Claude Code からFigmaキャンバスにUIを生成したい（Code to Canvas）
- デザインを自動更新したい
- プログラマティックにコンポーネントを作成したい

---

## まとめ

Figma MCP の `generate_figma_design`（Code to Canvas）を使うには、Claude Code のビルトインコネクタではなく、リモートMCPサーバーを手動追加する必要があります。

設定のポイントをまとめます。

- `claude mcp add --transport http figma https://mcp.figma.com/mcp` でサーバーを追加
- 追加後に Claude Code を再起動して OAuth 認証を完了
- Figmaアカウントは Pro プラン以上・Full seat が必要
- ビルトインコネクタは read-only のみ、write 機能はリモートサーバー経由

公式ドキュメントやチュートリアル記事の多くがこの区別に触れていないため、「設定したのに動かない」という状況に陥りやすいです。本記事が同じ問題にはまった方の助けになれば幸いです。

---

## 設定ファイルの詳細とカスタマイズ

`~/.claude/settings.json` にはプロジェクト単位での設定も可能です。

### グローバル設定（全プロジェクト共通）

```json
{
  "mcpServers": {
    "figma": {
      "type": "http",
      "url": "https://mcp.figma.com/mcp"
    }
  }
}
```

### プロジェクトローカル設定（`.claude/settings.json`）

特定のプロジェクトだけで Figma MCP を有効にしたい場合は、プロジェクトルートに `.claude/settings.json` を作成します。

```json
{
  "mcpServers": {
    "figma": {
      "type": "http",
      "url": "https://mcp.figma.com/mcp"
    }
  }
}
```

グローバル設定とプロジェクトローカル設定が両方存在する場合、プロジェクトローカルが優先されます。

### 複数 MCP サーバーを共存させる場合

Figma MCP と他の MCP サーバーを同時に使う場合の設定例です。

```json
{
  "mcpServers": {
    "figma": {
      "type": "http",
      "url": "https://mcp.figma.com/mcp"
    },
    "github": {
      "type": "http",
      "url": "https://api.githubcopilot.com/mcp"
    }
  }
}
```

---

## generate_figma_design の効果的な使い方

### 指示の粒度を調整する

生成結果の質は、指示の具体性に大きく依存します。

**曖昧な指示（避ける）**

```
ログイン画面を作ってください。
```

**具体的な指示（推奨）**

```
Figmaの [ファイルID] に、以下の仕様でログインフォームのフレームを作成してください。

フレームサイズ: 1440 × 900
背景色: #FFFFFF

要素:
1. ページ中央に幅 400px のフォームコンテナ（角丸 12px）
2. メールアドレス入力フィールド（高さ 48px、ボーダー #E0E0E0、角丸 8px）
3. パスワード入力フィールド（高さ 48px、ボーダー #E0E0E0、角丸 8px）
4. ログインボタン（幅 100%、高さ 48px、背景 #0066FF、テキスト白、角丸 8px）
5. 「パスワードを忘れた方」テキストリンク（右揃え、12px、#0066FF）

自動レイアウト: フォームコンテナは縦方向、ギャップ 16px
```

### ファイルIDとノードIDを事前に確認する

```bash
# FigmaファイルのURLからファイルIDを取得するワンライナー
echo "https://www.figma.com/file/XXXXXXXXXXXXXXXX/My-Design" | grep -oP '(?<=/file/)[^/]+'
```

特定のフレームやコンポーネントを対象にする場合は、Figma でノードを右クリック→「Copy link」でノードIDを含むURLを取得できます。

```
https://www.figma.com/file/XXXXXXXXXXXXXXXX/My-Design?node-id=123-456
                                                              ^^^^^^^
                                                              ノードID（%3Aを:に置換）
```

### 生成後の確認チェックリスト

```
[ ] フレームサイズが指定通りか
[ ] 自動レイアウトが有効になっているか
[ ] カラーが指定値と一致しているか
[ ] 命名規則が適切か（日本語ではなく英語推奨）
[ ] レイヤー構造が整理されているか
```

---

## トラブルシューティング FAQ

### Q. `claude mcp add` コマンドを実行したが、再起動後に設定が消えている

**A.** `~/.claude/settings.json` への書き込み権限を確認してください。

```bash
ls -la ~/.claude/settings.json
# -rw-r--r-- のように自分に書き込み権限があるか確認

# 権限がなければ修正
chmod 644 ~/.claude/settings.json
```

### Q. OAuth 認証画面が開かない

**A.** デフォルトブラウザの設定を確認してください。`/mcp` コマンド実行後に表示されるURLをコピーして、手動でブラウザに貼り付けることでも認証できます。

### Q. ツールは呼ばれるが Figma ファイルが更新されない

**A.** 以下を順に確認してください。

1. 指定したファイルIDが正しいか（大文字小文字を含め完全一致）
2. 対象ファイルへの編集権限があるか（Figma で「Edit」アクセス）
3. ファイルがチームスペースではなく個人スペースに存在しないか（チームスペース推奨）

```bash
# 現在の認証トークンの状態を確認
# Claude Code 内で実行
Figmaのwhoamiを確認してください
```

### Q. `generate_figma_design` と `update_figma_design` の使い分けは？

**A.** 用途が明確に異なります。

| ツール | 用途 | 使うタイミング |
|---|---|---|
| `generate_figma_design` | 自然言語から新規デザイン生成 | 白紙から作り始めるとき |
| `update_figma_design` | 既存ノードの修正・更新 | 部分的な変更、反復改善 |

初回は `generate_figma_design` で骨格を作り、以降の修正は `update_figma_design` を使うのが基本パターンです。

### Q. 生成されたデザインが毎回バラバラで安定しない

**A.** 制約を明示的に指定することで安定性が向上します。カラーコード、サイズ、スペーシング値を数値で指定してください。「モダンな」「シンプルな」といった主観的な表現は避け、測定可能な値に置き換えるのが効果的です。

詳細なプロンプト設計については、同シリーズの記事「Figma MCP × Claude Code でデザインワークフローを構造化する」を参照してください。

### Q. Pro プランに加入しているが「機能が使えない」と言われる

**A.** プランだけでなく「シートタイプ」の確認が必要です。Pro プランでも Viewer（閲覧専用）シートでは Code to Canvas は使えません。

Figma の `Settings > Members` で自分のロールを確認し、Editor（Full seat）であることを確認してください。Viewer から Editor への変更は、ワークスペースのオーナーまたは管理者が行う必要があります。

---

## 参考リンク

- [Figma Developer Docs: Tools and prompts](https://developers.figma.com/docs/figma-mcp-server/tools-and-prompts/)
- [Figma Developer Docs: Plans access and permissions](https://developers.figma.com/docs/figma-mcp-server/access-and-permissions/)
- [Figma Blog: Introducing Claude Code to Figma](https://www.figma.com/blog/introducing-claude-code-to-figma/)
