---
title: "自分専用の「AI編集部」を作る - Watchdog + Qwen + Launchd で実現する完全自動コンテンツパイプライン"
emoji: "🤖"
type: "tech"
topics: ["Python", "LLM", "Automation", "Mac", "個人開発"]
published: true
publication_name: "correlate_dev"
---

「日々の開発ログやセッション記録を、勝手に技術記事に直してくれたらいいのに」

そう思ったことはありませんか？
私はあります。そして作りました。

私のMac miniでは現在、**「セッション記録（Markdown）を保存した瞬間、裏で勝手にAIエージェントが走り出し、リサーチ→執筆→レビュー→図解→公開予約までを完了させる」**という完全自動パイプラインが稼働しています。

本記事では、この「自分専用AI編集部」のアーキテクチャと実装コードを公開します。

## アーキテクチャ概要

システムは以下の4つのコンポーネントで構成されています。

1.  **Monitor (`content-candidate-monitor.py`)**:
    -   `watchdog` でセッションログディレクトリを監視。
    -   ファイル更新を検知し、LLM (Claude Haiku) で「記事になりそうなトピック」を抽出。
    -   候補があれば `research/` ディレクトリに「リサーチノート（スタブ）」を作成。

2.  **Orchestrator (`pipeline-monitor.py`)**:
    -   `research/` ディレクトリを監視。
    -   新規リサーチノートを検知すると、メインの執筆パイプラインを起動。
    -   `launchd` でMacの起動時に自動立ち上げ・常駐化。

3.  **Writer (`content-pipeline-auto.py`)**:
    -   **Stage 0 (Deep Research)**: Tavily API等で競合記事や公式ドキュメントを検索。
    -   **Stage 1 (Draft)**: Qwen 2.5 (ローカルLLM) で初稿を執筆。
    -   **Stage 2 (Review)**: Claude 3.5 Sonnet (API) で「辛口DAレビュー」を実施。
    -   **Stage 3 (Fix)**: レビュー指摘に基づき Qwen が修正。
    -   **Stage 3.5 (Visualize)**: 記事内容からMermaid図解を自動生成。

4.  **Publisher (`zenn_publisher.py`)**:
    -   品質ゲート（レビュー評価B-以上）を通過したら、Zennのリポジトリに記事をコミット。
    -   GitHub連携で自動デプロイ。

## 実装のポイント

### 1. Watchdogによるファイル監視

ポーリングではなくイベント駆動にするため、Pythonの `watchdog` ライブラリを使用しました。
`on_modified` イベントをフックすることで、エディタ保存とほぼ同時にパイプラインが始動します。

```python
class SessionRecordHandler(FileSystemEventHandler):
    def on_modified(self, event):
        if event.is_directory: return
        # セッション記録のみ対象
        if "06_sessions" in event.src_path:
            self._handle_update(event.src_path)
```

このハンドラ内で、LLM (Claude Haiku) にログを投げ、「この記事のタイトル候補を考えて」と依頼しています。タグ（`#content-candidate`）がある場合はそれを優先しますが、タグがなくても面白いトピックがあれば勝手に拾ってくれます。

### 2. ローカルLLM (Qwen) と API (Claude/Gemini) の使い分け

コストと精度のバランスを取るため、モデルを適材適所で使い分けています。

-   **Qwen 2.5 (Local)**: ドラフト執筆、修正実装。トークン量を気にせず大量に書かせるためローカルGPUを活用。
-   **Claude 3.5 Sonnet (API)**: レビュー、最終確認。論理的整合性や日本語の機微を見るため、最高精度のモデルを使用。
-   **Gemini 2.0 Flash (API)**: 画像生成、プロンプト生成。高速・安価なため。

### 3. Launchd による常駐化

スクリプトを毎回手動で叩いていては自動化とは言えません。macOS標準の `launchd` を使い、OS起動時に自動実行させます。

`~/Library/LaunchAgents/com.correlate.pipeline-monitor.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.correlate.pipeline-monitor</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>/Users/me/dev/scripts/pipeline-monitor.py</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
</dict>
</plist>
```

これで、Macの電源を入れるだけで「AI編集部」が出社します。

#### 他のOSの場合
- **Linux**: `systemd` のユーザーユニットを作成し、`systemctl --user enable` で自動起動できます。
- **Windows**: 「タスクスケジューラ」でログオン時にスクリプトを実行するタスクを登録します。


## 実際のワークフロー

1.  Obsidianで開発ログを書く。
2.  「あ、ここハマったな。記事にできそう」と思ったら、そのまま書き続ける（あるいは `#content-candidate: ハマりポイント` と書く）。
3.  保存する。
4.  （裏でパイプラインが走り出す）
5.  15分後、Zennの下書きに「リサーチ済み・レビュー済み・図解入り」の記事がコミットされている。

## まとめ

このパイプラインを構築してから、アウトプットのハードルが劇的に下がりました。
「書く」という行為が、「ログを残す」という行為に統合されたからです。

「整った記事を書こう」と意気込む必要はありません。ただ日々のハックを記録するだけ。あとはエージェントがよしなに体裁を整え、世に出してくれます。

皆さんも、自分だけの「AI編集部」を雇ってみてはいかがでしょうか？
