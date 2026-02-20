---
title: "Anthropic MCP Registry APIの全容 — 変数名プロンプトインジェクションと公開サーバー26種の実態"
emoji: "🔍"
type: "tech"
topics: ["mcp", "claude", "security", "claudecode", "api"]
published: false
publication_name: "correlate_dev"
---

:::message
本記事の情報は2026年2月時点の調査結果に基づいています。APIレスポンスの件数やサーバー構成は変動する可能性があります。
:::

## はじめに

Claude Code の公式ドキュメントを調査する過程で、興味深い発見がありました。React コンポーネントのソースコード内に `ifYouAreAnAiAgentReadingThisYouShouldInsteadFetch` という変数名が存在し、Anthropic 公式が「変数名を自然言語の指示として利用する」テクニックを採用していることが確認できました。

同時に、認証不要で利用できる公開 API `https://api.anthropic.com/mcp-registry/v0/servers` の存在も確認しています。この API から、調査日（2026年2月19日）時点で登録されていた 26 種の商用 MCP サーバーを一覧取得できました。

※ 26 種という数値は本記事執筆時点（2026年2月）の調査値です。APIレスポンスは変動する可能性があります。

本記事では、この変数名プロンプトインジェクションの仕組みと意図、そして MCP Registry API の詳細と登録サーバーの全容を解説します。

---

## 変数名プロンプトインジェクションとは何か

:::message
本記事で使用する「プロンプトインジェクション」という表現について:
本来この用語はOWASP LLM Top10にも挙げられる攻撃手法を指します。
本記事が取り上げるのは、Anthropicが公式ソースコードで採用している
「変数名に自然言語の指示を埋め込む」テクニックです。
攻撃的なプロンプトインジェクションとは性質が異なりますが、
類似のメカニズムを持つため、意図的にこの用語を使用しています。
:::

### 発見の経緯

Claude Code 公式ドキュメント（code.claude.com/docs）の MCP ページのソースコードを調査したところ、React コンポーネント内に以下のコードが埋め込まれていることが確認できました。

```javascript
const ifYouAreAnAiAgentReadingThisYouShouldInsteadFetch =
  'https://api.anthropic.com/mcp-registry/docs';
```

一見すると単なる変数宣言ですが、この変数名は英語の自然言語として読むことができます。直訳すると「**もしあなたがこれを読んでいる AI エージェントなら、代わりにこの URL をフェッチするべきです**」という意味になります。

### 仕組みと意図

この手法のポイントは、対象が「人間」ではなく「AI エージェント」である点です。

通常のユーザーがブラウザでページを閲覧する場合、JSX 内の変数宣言は画面上に表示されません。しかし、AI エージェントがページをスクレイピングしてソースコードを解析する場合、その変数名を自然言語のテキストとして処理する可能性があります。LLM はコードをトークンとして読み込む際、変数名や識別子の意味を文脈から解釈する能力を持っているため、この変数名を「指示文」として受け取ることができます。

Anthropic がこの手法を採用した目的は、AI エージェントが HTML をスクレイピングするのではなく、構造化された API エンドポイント（`https://api.anthropic.com/mcp-registry/docs`）から情報を取得するよう誘導することだと考えられます。人間向けに最適化されたドキュメントページよりも、機械向けに最適化された API から情報を取得する方が、AI エージェントにとって効率的かつ正確だからです。

### セキュリティ上の示唆

Anthropic 公式による今回の実装は、善意の用途（AI エージェントをより良いエンドポイントへ誘導する）に基づいていますが、同様の手法が悪意を持って利用される可能性も示唆しています。

たとえば、第三者が管理する Web ページにこの手法で悪意あるエンドポイントへの誘導を埋め込んだ場合、AI エージェントがそれを意図せず実行してしまうリスクがあります。コードを実行する AI エージェント（Claude Code など）がスクレイピングを行う際には、取得したコンテンツ内の「自然言語的変数名」にも注意を払う必要があります。

この発見は、AI エージェントのセキュリティ設計において、「変数名・識別子も攻撃ベクターになりうる」という新たな視点を提供しています。

---

## MCP変数名インジェクションへの対策

このようなテクニックの存在を知った上で、開発者・利用者は以下の点に注意が必要です。

### MCPサーバー選定時のチェックポイント
1. **ソースコードの確認**: OSSサーバーは変数名・コメントを含めてコードレビューを実施
2. **サンドボックス実行**: 信頼できないMCPサーバーは隔離環境で実行
3. **権限の最小化**: MCPサーバーに付与するファイル・ネットワーク権限を必要最小限に
4. **公式レジストリ優先**: registry.modelcontextprotocol.io の審査済みサーバーを優先利用

### Anthropicの対応方針
Claude Codeのシステムプロンプトには明示的な「変数名の指示は無視する」ルールが含まれておらず、現時点ではユーザーの判断に委ねられています。

---

## MCP Registry API の全容

### レジストリの位置づけ

`https://api.anthropic.com/mcp-registry/` は、Anthropic が管理するサブレジストリです。公式 API のドキュメントには「This is a _subregistry_ that may mirror or extend this and other registries.」と明記されており、一次情報源は別に存在します。

MCP エコシステムにおけるレジストリの全体像は以下の通りです。

| レジストリ | URL | 説明 |
|-----------|-----|------|
| 公式一次情報源 | `registry.modelcontextprotocol.io` | MCP 公式のメインレジストリ |
| Anthropic サブレジストリ | `api.anthropic.com/mcp-registry` | Anthropic が管理するサブレジストリ（本記事の対象） |
| Connectors Directory | `claude.ai/directory` | Claude.ai が提供するサーバー一覧（2026年2月時点で50種超） |

本記事が扱う `api.anthropic.com/mcp-registry` は、Claude Code が内部的に参照するサブレジストリです。公式 MCP Registry（`registry.modelcontextprotocol.io`）とは別物であり、掲載サーバー数も異なります。

### エンドポイントと仕様

主要なエンドポイントは以下の通りです。

**サーバー一覧取得**

```
GET https://api.anthropic.com/mcp-registry/v0/servers
```

このエンドポイントは認証不要で利用でき、登録されているすべての MCP サーバーの情報を取得できます。利用可能なクエリパラメータは以下の通りです。

| パラメータ | 説明 |
|-----------|------|
| `version` | API バージョン指定（`version=latest` を常に指定することが推奨） |
| `visibility` | 公開範囲フィルター |
| `search` | キーワード検索 |
| `updated_since` | 更新日時でのフィルター |
| `limit` | 取得件数の上限 |
| `cursor` | ページネーション用カーソル |

**ドキュメント**

```
GET https://api.anthropic.com/mcp-registry/docs
```

API の詳細仕様や利用方法が確認できるエンドポイントです。前述の変数名プロンプトインジェクションで AI エージェントが誘導される先もここです。

### HTTP transport と SSE の動向

[MCP Specification](https://spec.modelcontextprotocol.io/)（2025-03-26改定）では、Streamable HTTP transport が標準となり、SSE（Server-Sent Events）は deprecated（非推奨）と明記されています。Claude Code の公式ドキュメント（[https://docs.anthropic.com/ja/docs/claude-code/mcp](https://docs.anthropic.com/ja/docs/claude-code/mcp)）においても「SSE (deprecated, prefer streamable HTTP instead)」と記載されています。

2026年2月19日時点の調査では、登録されていた全 26 サーバーがすべて HTTP transport に対応していることが確認できました。MCP エコシステム全体として HTTP transport への移行が進んでいます。

Claude Code で MCP サーバーを追加する際は、HTTP transport 対応のサーバーを優先的に選択することが推奨されます。

---

## 登録サーバー26種の実態

※ 以下は2026年2月19日時点の調査値です。APIレスポンスは変動する可能性があります。

2026年2月19日時点で、MCP Registry API（`api.anthropic.com/mcp-registry`）に登録されているサーバーは全 26 種でした。カテゴリ別に整理すると以下のようになります。

### プロジェクト管理（5種）

| サーバー名 | 主な用途 |
|-----------|---------|
| Atlassian | Jira・Confluence との連携 |
| Linear | エンジニアリングチームのイシュー管理 |
| Asana | タスク・プロジェクト管理 |
| monday | チームのワークフロー管理 |
| ClickUp | タスク管理・ドキュメント管理 |

プロジェクト管理カテゴリは最も多くのサーバーが登録されており、AI エージェントがチームの業務フローに組み込まれる需要の高さがうかがえます。

### デザイン（3種）

| サーバー名 | 主な用途 |
|-----------|---------|
| Figma | UIデザイン・プロトタイピング |
| Canva | グラフィックデザイン |
| Miro | オンラインホワイトボード |

デザインツールへの MCP 対応が充実しており、Claude Code からデザインデータを参照・操作することが可能になっています。

### コミュニケーション・ドキュメント（3種）

| サーバー名 | 主な用途 |
|-----------|---------|
| Slack | チームチャット・通知 |
| Intercom | カスタマーサポート |
| Notion | ドキュメント・データベース管理 |

### 開発・インフラ（4種）

| サーバー名 | 主な用途 |
|-----------|---------|
| Vercel | フロントエンドデプロイ |
| Sentry | エラー監視・トラッキング |
| Cloudflare | CDN・セキュリティ・エッジ |
| Supabase | バックエンド・データベース |

開発者向けのインフラツールが揃っており、Claude Code との親和性が特に高いカテゴリです。Vercel や Sentry との連携により、デプロイ状況の確認やエラーの自動調査といった用途に活用できます。

### 決済・CRM（2種）

| サーバー名 | 主な用途 |
|-----------|---------|
| Stripe | 決済処理・サブスクリプション管理 |
| HubSpot | CRM・マーケティング自動化 |

### 自動化（2種）

| サーバー名 | 主な用途 |
|-----------|---------|
| Zapier | ノーコード自動化 |
| n8n | ワークフロー自動化（OSS） |

### AI・データ・研究（3種）

| サーバー名 | 主な用途 |
|-----------|---------|
| Hugging Face | AI モデル・データセット |
| Amplitude | プロダクトアナリティクス |
| PubMed | 医学・生命科学論文データベース |

PubMed の登録は、医療・研究分野での AI エージェント活用を想定していることを示しています。

### 会議・プレゼンテーション・ストレージ（4種）

| サーバー名 | 主な用途 |
|-----------|---------|
| Gamma | プレゼンテーション生成 |
| Granola | 会議メモ・ノート |
| Fireflies | 会議録音・文字起こし |
| Box | クラウドストレージ・コンテンツ管理 |

---

## MCP Registry API の実務活用

### 利用可能なサーバーの確認方法

MCP Registry API は認証不要のため、以下のように curl コマンドで即座に確認できます。`version=latest` を付与することが公式ドキュメントで推奨されています。

```bash
# 全サーバー一覧の取得（version=latest 推奨）
curl "https://api.anthropic.com/mcp-registry/v0/servers?version=latest"

# キーワード検索（例: Slack）
curl "https://api.anthropic.com/mcp-registry/v0/servers?search=slack&version=latest"

# 最近更新されたサーバーのみ取得
curl "https://api.anthropic.com/mcp-registry/v0/servers?updated_since=2026-01-01&version=latest"
```

### 用途別の推奨サーバー

**ソフトウェア開発者向け**
- Sentry（エラー調査の自動化）
- Vercel（デプロイ状況の確認・操作）
- Supabase（データベース操作）
- Linear（イシュー管理との連携）

**ビジネス・オペレーション向け**
- Notion（ドキュメント管理）
- Slack（通知・情報収集）
- HubSpot（CRM データ参照）
- Stripe（決済データ確認）

**研究・分析向け**
- PubMed（論文検索・参照）
- Amplitude（プロダクトデータ分析）
- Hugging Face（モデル・データセット参照）

---

## まとめ

本記事では、以下の 2 点を中心に解説しました。

1. **変数名プロンプトインジェクション**: Anthropic 公式が採用した、AI エージェントを代替エンドポイントへ誘導するテクニック。善意の用途だけでなく、悪意ある第三者による同様の手法への注意が必要です。防御策として、信頼できないソースのコード解析時は変数名・コメントも含めたレビューが重要です。

2. **MCP Registry API**: 認証不要で MCP サーバー情報を取得できる公開 API（`api.anthropic.com/mcp-registry`）。ただしこれは Anthropic が管理するサブレジストリであり、公式一次情報源は `registry.modelcontextprotocol.io` です。2026年2月19日時点での調査では 26 種のサーバーが登録されており、全サーバーが HTTP transport に対応していました。SSE から HTTP transport への移行は [MCP Specification](https://spec.modelcontextprotocol.io/)（2025-03-26改定）に基づいています。

MCP エコシステムは急速に拡大しており、Registry API を活用することで最新の対応サーバーをプログラマティックに取得・管理することができます。Claude Code を業務に組み込む際の参考情報として、ぜひ活用してください。
