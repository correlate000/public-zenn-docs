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

しかし実際に使おうとすると、**Claude Code のビルトインコネクタではこの機能が使えません**。

Figma Forum でも「ツールが表示されない」という報告が多数上がっており、公式ドキュメントにもこの区別が明確に書かれていません。本記事では、なぜ使えないのか・どう設定すれば使えるのかを、実際にはまった経験をもとに解説します。

---

## ビルトインコネクタとリモートMCPサーバーの違い

Claude Code には Figma との連携方法が2種類あります。

### ビルトインコネクタ（デフォルト）

Claude Code の設定画面から「Figma」を選択するだけで使えるコネクタです。特別な設定不要で、以下のようなツールが利用できます。

- `get_figma_data` — ファイル情報を取得する
- `get_node_by_id` — 特定ノードの詳細を取得する
- `get_images` — 画像データを取得する

これらはすべて**read-only**のツールです。Figmaファイルを「読む」ことしかできません。

### リモートMCPサーバー（手動追加）

`https://mcp.figma.com/mcp` に接続するリモートサーバーです。こちらには write 系のツールが含まれており、`generate_figma_design` もここに属します。

| 項目 | ビルトインコネクタ | リモートMCPサーバー |
|---|---|---|
| 設定 | Claude Code UI から選択するだけ | `claude mcp add` コマンドが必要 |
| 認証 | 自動 | OAuth（手動で承認が必要） |
| ツール数 | 一部（read-only） | 13ツール（write含む） |
| `generate_figma_design` | **使えない** | **使える** |

---

## 設定手順：リモートMCPサーバーを追加する

### 前提条件

まず以下を確認してください。

- **Figma アカウント**: Pro プラン以上、かつ Full seat であること
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

**原因**: ビルトインコネクタのみ設定されている状態です。

**対処**: 前述の手順でリモートMCPサーバーを追加してください。`claude mcp list` で現在の設定を確認できます。

```bash
claude mcp list
```

### OAuth 認証後もツールが表示されない

**対処**: Claude Code を完全に再起動してください。プロセスが残っている場合、設定が反映されないことがあります。

```bash
# プロセスを確認
ps aux | grep claude

# 全プロセスを終了してから再起動
```

### 認証は通るが生成が失敗する

**原因の候補**:
1. Figmaアカウントが Pro プラン未満
2. Full seat ではなく Viewer ロールになっている
3. 指定したファイルIDへのアクセス権限がない

Figma の「Settings > Members」でロールを確認してください。

### ビルトインコネクタと重複設定してしまった

両方の設定が混在すると、read-only ツールが重複して表示されることがあります。ビルトインコネクタを無効化し、リモートMCPサーバーのみを使う設定にすることをおすすめします。

---

## ビルトインとリモートを使い分ける場面

両者の使い分けの目安です。

**ビルトインコネクタで十分な場面**
- 既存のFigmaデザインをコードに変換したい（Design to Code）
- コンポーネントの構造を読み取りたい
- デザイントークンを確認したい

**リモートMCPサーバーが必要な場面**
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

## 参考リンク

- [Figma Developer Docs: Tools and prompts](https://developers.figma.com/docs/figma-mcp-server/tools-and-prompts/)
- [Figma Developer Docs: Plans access and permissions](https://developers.figma.com/docs/figma-mcp-server/access-and-permissions/)
- [Figma Blog: Introducing Claude Code to Figma](https://www.figma.com/blog/introducing-claude-code-to-figma/)
