---
title: "Figma MCP の generate_figma_design だけがロードされない問題——デバッグ手順と現状の回避策"
emoji: "🔍"
type: "tech"
topics: ["figma", "mcp", "claudecode", "debug", "ai"]
published: false
status: "draft"
publication_name: "correlate_dev"
---

## はじめに

Figma MCP をセットアップして Claude Code に接続し、13個のツールが使えるはずなのに `generate_figma_design` だけがロードされない。

この問題に遭遇した方は、おそらく次のような状況にいるはずです。

- `whoami` で接続は確認できている
- 他の12ツール（`get_figma_data`, `update_figma_design` など）は正常に動いている
- Figmaアカウントは Pro プラン、Full seat
- Claude Code のバージョンは最新
- `~/.claude/settings.json` の設定は正常に見える

それでも `generate_figma_design` だけが呼び出せない——という状況です。

本記事では、この問題の詳細な調査手順と、2026年2月時点での状況を共有します。

---

## 問題の全体像

Figma MCP の公式ドキュメントには、以下の13ツールが記載されています。

```
whoami
get_figma_data
get_node_by_id
get_images
get_local_components
get_published_components
get_styles
create_figma_file
update_figma_design
generate_figma_design      ← これだけロードされない
create_component
delete_node
export_node
```

`generate_figma_design` は「Code to Canvas」機能の中核ツールで、自然言語の説明からFigmaキャンバスにUIを生成するものです。

---

## 確認した環境と症状

以下の環境で問題を確認しました。

| 項目 | 内容 |
|---|---|
| Claude Code バージョン | v2.1.52 |
| MCP 接続方式 | Remote HTTP（`https://mcp.figma.com/mcp`） |
| Figma プラン | Pro |
| シート | Full seat（Editor 権限） |
| 設定ファイル | `~/.claude/settings.json`（正常） |

`/mcp` コマンドで確認すると、Figma サーバーが表示され、接続済みステータスになっています。しかし Claude Code に「Figmaに新しいデザインを生成してほしい」と伝えても、`generate_figma_design` は呼び出されず、代わりに「そのツールは利用できない」旨の返答が来ます。

---

## デバッグ手順

同じ問題に遭遇した場合に試すべき手順を順番に記載します。

### Step 1: ツール一覧を確認する

Claude Code のチャットで以下を入力します。

```
現在利用できる Figma の MCP ツール一覧を教えてください
```

ここで `generate_figma_design` が表示されない場合、ツールカタログへのロードに失敗しています。

### Step 2: MCP サーバーの接続状態を確認する

```
/mcp
```

Figma サーバーが「接続済み」になっているか確認します。「認証が必要」や「接続失敗」の場合は、OAuth 認証を再実行してください。

### Step 3: whoami で認証状態を確認する

```
Figma の自分のアカウント情報を確認してください
```

`whoami` ツールが呼ばれ、アカウント情報が返れば認証は正常です。これが失敗する場合は接続自体の問題です。

### Step 4: Figmaアカウントの権限を確認する

`generate_figma_design` はアカウントの権限に依存する可能性があります。

Figma にログインして `Settings > Members` を開き、自分のロールを確認してください。

- **Editor（Full seat）**: Code to Canvas が利用可能なはず
- **Viewer（View-only seat）**: Code to Canvas は利用不可

### Step 5: Claude Code を完全再起動する

プロセスを完全に終了し、再起動します。

```bash
# 実行中のClaude Codeプロセスを確認
ps aux | grep "claude"

# 全プロセスを終了後、再起動
```

再起動後、再度 Step 1 を試します。

### Step 6: MCP サーバーを削除して再追加する

既存の設定を削除し、再追加します。

```bash
# 現在の設定を確認
claude mcp list

# Figma サーバーを削除
claude mcp remove figma

# 再追加
claude mcp add --transport http figma https://mcp.figma.com/mcp
```

再追加後、Claude Code を再起動して OAuth 認証を再実行します。

### Step 7: settings.json を直接確認する

```bash
cat ~/.claude/settings.json
```

以下の形式になっているか確認します。

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

余分なパラメータや誤ったURLが入っていないか確認してください。

---

## 外部での報告状況

この問題は筆者の環境固有ではありません。

### OpenAI Codex での同種の報告

GitHub の OpenAI Codex リポジトリ（Issue #12021）に「generate_figma_design is not exposed in tool catalog」という報告があります。Codex 側の視点での報告ですが、Claude Code でも同じ問題が発生しています。

この Issue には「他のツールは動くが generate_figma_design だけが出てこない」という複数のコメントがあり、2026年2月時点で未解決のままです。

### Figma Forum での報告

Figma Forum でも「ツールが表示されない」「generate_figma_design が動かない」という報告スレッドがあります。公式からの明確な回答はなく、「再起動してみてください」というワークアラウンドが示されているのみです。

---

## 現時点での仮説

いくつかの仮説が考えられます。

**仮説1: ロールアウト中の機能制限**
`generate_figma_design` は2026年2月17日にリリースされたばかりの機能です。段階的なロールアウトの段階で、一部のアカウントや地域にのみ提供されている可能性があります。

**仮説2: サーバー側のツールカタログの問題**
MCP サーバーが返すツールカタログに、特定の条件下で `generate_figma_design` が含まれないケースがある可能性があります。

**仮説3: アカウント条件の追加制約**
Pro / Full seat 以外にも、追加のアカウント条件（利用規約への同意、機能フラグなど）が存在する可能性があります。

---

## 現時点での回避策

`generate_figma_design` が使えない場合の代替アプローチです。

### 代替1: update_figma_design で段階的に構築する

`generate_figma_design` が「一から生成する」ツールであるのに対し、`update_figma_design` は「既存ノードを更新する」ツールです。

手順：
1. Figma で手動で空のフレームを作成する
2. Claude Code から `update_figma_design` で要素を追加・変更していく

```
Figmaのフレーム [NODE_ID] に、以下の要素を追加してください：
- 幅360px / 高さ52px のテキスト入力フィールド
- placeholder: "メールアドレス"
- ボーダー: 1px solid #E0E0E0
- 角丸: 8px
```

完全な自動生成にはなりませんが、細かいコントロールが効くため品質管理の面では優位な場合もあります。

### 代替2: Figma AI（Figma ネイティブ機能）を使う

Figma 自体にも AI によるデザイン生成機能があります。Figma を開いた状態でAIパネルから「Generate」を使うと、`generate_figma_design` と近い体験が得られます。

Claude Code との連携はなくなりますが、純粋な「テキストからデザイン生成」が目的であれば現実的な代替です。

### 代替3: 解決を待つ

Figma Forum や OpenAI Codex の Issue を追いかけ、公式の修正を待つ方法です。アクティブに報告されている問題のため、近い将来に修正される可能性があります。

---

## 問題解決のためにできること

もし同じ問題を抱えている場合、以下の情報を Figma Forum や GitHub Issue に投稿すると、問題の解決が早まります。

```
環境情報（投稿テンプレート）

- Claude Code バージョン: 
- OS: 
- Figma プラン: 
- シートタイプ（Editor/Viewer）: 
- MCP 接続方式（ビルトイン/リモートHTTP）: 
- 他のツールの動作状況（whoami は動くか等）: 
- 試した解決策: 
- settings.json の内容（センシティブ情報を除く）: 
```

---

## まとめ

`generate_figma_design` だけがロードされない問題は、2026年2月時点で複数の環境で報告されている既知の問題です。

調査のポイントをまとめます。

- **接続自体は正常**: `whoami` が動くなら認証・接続の問題ではない
- **外部でも報告あり**: OpenAI Codex Issue #12021、Figma Forum での報告を確認
- **再起動で解消する可能性**: 試す価値はある
- **代替手段**: `update_figma_design` による段階的構築、Figmaネイティブ AI

現状では確実な解決策が存在しないため、引き続き調査を続けています。進展があれば本記事を更新します。

もし解決できた方がいれば、コメントで情報を共有していただけると助かります。

---

## 参考リンク

- [Figma Developer Docs: Tools and prompts](https://developers.figma.com/docs/figma-mcp-server/tools-and-prompts/)
- [Figma Blog: Introducing Claude Code to Figma](https://www.figma.com/blog/introducing-claude-code-to-figma/)
- [OpenAI Codex Issue #12021](https://github.com/openai/codex/issues/12021)
