---
title: "Claude Code × GCP を MCP でつないでインフラ操作を自然言語化した話"
emoji: "🔧"
type: "tech"
topics: ["claudecode", "gcp", "mcp", "cloudrun", "bigquery"]
published: false
status: "draft"
publication_name: "correlate_dev"
---

## はじめに ─ なぜ今「AIエージェント × GCP」なのか

GCP の日常操作には思った以上に摩擦があります。Cloud Console・gcloud CLI・Terraform の使い分け判断、ドキュメントを行き来しながらフラグを調べる時間、新メンバーへのオンボーディングコスト。どれも積み重なると無視できない開発速度の低下につながります。

そこで今回試したのが、**Claude Code と GCP を MCP（Model Context Protocol）経由で接続し、インフラ操作を自然言語から直接実行できる環境を構築する**というアプローチです。

「Cloud Run に新バージョンをデプロイして、まず 10% だけトラフィックを流してログを確認して」という指示を一度入力するだけで、デプロイ・トラフィック切り替え・ログ確認の一連の操作が完結する ── そういう世界です。

本記事では、MCP サーバーの実装から Cloud Run・BigQuery・IAM の自動化パターン、本番で使うためのセキュリティ設計まで、実務で即使えるレベルで解説します。

### 前提条件

- GCP プロジェクトが存在し、課金が有効になっている
- `gcloud` CLI がインストール済みで `application-default login` が完了している
- Claude Code CLI がインストール済み
- Node.js >= 18.0.0

```bash
# バージョン確認
node --version          # >= 18.0.0
gcloud --version        # >= 450.0.0 推奨
claude --version        # Claude Code CLI
```

---

## MCP アーキテクチャの基礎知識

### Tool / Resource / Prompt の三層構造

MCP は JSON-RPC 2.0 ベースのプロトコルで、Claude Code（クライアント）と外部システム（サーバー）をつなぎます。GCP 統合では主に **Tool** を使います。

| レイヤー | 役割 | GCP での使い方 |
|---------|------|--------------|
| Tool | 実行できる関数 | `deploy_service`、`execute_query` など |
| Resource | 読み取れるデータ | BigQuery スキーマ、Cloud Run サービス一覧 |
| Prompt | テンプレート指示 | 定型の操作手順をプリセット化 |

Tool の定義例を示します。

```typescript
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";

const server = new McpServer({
  name: "gcp-server",
  version: "1.0.0",
});

// Cloud Run デプロイ Tool の定義
server.tool(
  "deploy_cloud_run",
  "Cloud Run サービスをデプロイまたは更新する",
  {
    serviceName: z.string().describe("サービス名"),
    image: z.string().describe("コンテナイメージ（例: gcr.io/project/app:v1）"),
    region: z.string().default("asia-northeast1").describe("リージョン"),
    maxInstances: z.number().default(10).describe("最大インスタンス数"),
  },
  async ({ serviceName, image, region, maxInstances }) => {
    // 実装は後述
  }
);
```

### 通信方式の選択

| 方式 | 用途 | 特徴 |
|------|------|------|
| stdio | 個人開発環境 | 最もシンプル。プロセス間通信 |
| SSE | チーム共有 | 複数クライアント対応 |
| HTTP | 本番 CI/CD | 認証ミドルウェアを追加可能 |

個人利用・PoC 段階では stdio で十分です。

### Claude Code への接続設定

プロジェクトルートに `.claude/mcp.json` を配置します。

```json
{
  "mcpServers": {
    "gcp-server": {
      "command": "node",
      "args": ["./mcp-gcp/dist/index.js"],
      "env": {
        "GOOGLE_CLOUD_PROJECT": "my-project-id"
      }
    }
  }
}
```

`GOOGLE_APPLICATION_CREDENTIALS` はファイルパスをハードコードせず、`gcloud auth application-default login` で設定した ADC（Application Default Credentials）を使うのが安全です。

---

## 環境構築 ─ MCP GCP サーバーのセットアップ

### プロジェクト初期化

```bash
mkdir mcp-gcp && cd mcp-gcp
npm init -y
npm install @modelcontextprotocol/sdk @google-cloud/run @google-cloud/bigquery \
            @google-cloud/iam @google-cloud/logging zod
npm install -D typescript @types/node tsx
```

`tsconfig.json` の主要設定：

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "Node16",
    "moduleResolution": "Node16",
    "outDir": "./dist",
    "strict": true
  }
}
```

### サーバーのエントリーポイント

```typescript
// src/index.ts
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { registerCloudRunTools } from "./tools/cloudrun.js";
import { registerBigQueryTools } from "./tools/bigquery.js";
import { registerIamTools } from "./tools/iam.js";

const server = new McpServer({
  name: "gcp-server",
  version: "1.0.0",
});

registerCloudRunTools(server);
registerBigQueryTools(server);
registerIamTools(server);

const transport = new StdioServerTransport();
await server.connect(transport);
```

### 接続確認

```bash
# ビルドして Claude Code から確認
npm run build
claude mcp list  # gcp-server が表示されれば成功
```

よくあるエラーと対処法：

| エラー | 原因 | 対処 |
|--------|------|------|
| `PERMISSION_DENIED` | ADC の権限不足 | 必要な IAM ロールを付与 |
| `Module not found` | ビルド未実行 | `npm run build` を先に実行 |
| `spawn ENOENT` | node のパス解決失敗 | `command` に絶対パスを指定 |

---

## Cloud Run の自動化 ─ デプロイからログ確認まで

### 実装するツール一覧

| ツール名 | 説明 | 使用 API |
|---------|------|---------|
| `list_services` | サービス一覧と状態確認 | Cloud Run Admin API |
| `deploy_service` | イメージからサービス作成・更新 | `services.replaceService` |
| `update_traffic` | リビジョン間トラフィック分割 | `services.replaceService` |
| `get_logs` | 直近ログ取得（件数指定可） | Cloud Logging API |
| `delete_service` | サービス削除（確認トークン付き） | `services.delete` |

### デプロイ Tool の実装

```typescript
// src/tools/cloudrun.ts
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { ServicesClient } from "@google-cloud/run";
import { z } from "zod";

const runClient = new ServicesClient();
const PROJECT = process.env.GOOGLE_CLOUD_PROJECT!;

export function registerCloudRunTools(server: McpServer) {
  server.tool(
    "deploy_service",
    "Cloud Run サービスをデプロイまたは更新する",
    {
      serviceName: z.string(),
      image: z.string(),
      region: z.string().default("asia-northeast1"),
      maxInstances: z.number().default(10),
      memory: z.string().default("512Mi"),
      envVars: z.record(z.string()).optional(),
    },
    async ({ serviceName, image, region, maxInstances, memory, envVars }) => {
      const parent = `projects/${PROJECT}/locations/${region}`;
      const name = `${parent}/services/${serviceName}`;

      const serviceConfig = {
        name,
        template: {
          containers: [
            {
              image,
              resources: {
                limits: { memory },
              },
              env: Object.entries(envVars ?? {}).map(([k, v]) => ({
                name: k,
                value: v,
              })),
            },
          ],
          scaling: { maxInstanceCount: maxInstances },
        },
      };

      try {
        // 既存サービスの更新を試みる
        const [operation] = await runClient.replaceService({
          name,
          service: serviceConfig,
        });
        const [service] = await operation.promise();
        return {
          content: [
            {
              type: "text",
              text: `✅ デプロイ完了\nURL: ${service.uri}\nリビジョン: ${service.latestCreatedRevision}`,
            },
          ],
        };
      } catch (err: any) {
        if (err.code === 5) {
          // NOT_FOUND → 新規作成
          const [operation] = await runClient.createService({
            parent,
            serviceId: serviceName,
            service: serviceConfig,
          });
          const [service] = await operation.promise();
          return {
            content: [
              {
                type: "text",
                text: `✅ サービス作成完了\nURL: ${service.uri}`,
              },
            ],
          };
        }
        throw err;
      }
    }
  );

  // トラフィック分割 Tool
  server.tool(
    "update_traffic",
    "Cloud Run サービスのトラフィック分割を更新する",
    {
      serviceName: z.string(),
      region: z.string().default("asia-northeast1"),
      latestPercent: z
        .number()
        .min(0)
        .max(100)
        .describe("最新リビジョンへのトラフィック割合（%）"),
    },
    async ({ serviceName, region, latestPercent }) => {
      const name = `projects/${PROJECT}/locations/${region}/services/${serviceName}`;
      const [currentService] = await runClient.getService({ name });

      const traffic = [
        {
          type: "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST" as const,
          percent: latestPercent,
        },
      ];

      // 残りを stable リビジョンに割り当て
      if (latestPercent < 100 && currentService.traffic?.[0]?.revision) {
        traffic.push({
          type: "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION" as const,
          revision: currentService.traffic[0].revision,
          percent: 100 - latestPercent,
        });
      }

      const [operation] = await runClient.replaceService({
        name,
        service: { ...currentService, traffic },
      });
      await operation.promise();

      return {
        content: [
          {
            type: "text",
            text: `✅ トラフィック更新完了\n最新リビジョン: ${latestPercent}%\n旧リビジョン: ${100 - latestPercent}%`,
          },
        ],
      };
    }
  );
}
```

### 危険操作の防御設計

削除やスケールゼロは確認トークンを使った二段階確認を実装します。

```typescript
import crypto from "crypto";

const pendingConfirmations = new Map<string, { action: string; target: string }>();

server.tool(
  "delete_service",
  "Cloud Run サービスを削除する（確認が必要）",
  {
    serviceName: z.string(),
    region: z.string().default("asia-northeast1"),
    confirmationToken: z.string().optional().describe("削除確認用トークン"),
  },
  async ({ serviceName, region, confirmationToken }) => {
    if (!confirmationToken) {
      // 初回呼び出し → トークンを発行して確認を求める
      const token = crypto.randomBytes(8).toString("hex");
      pendingConfirmations.set(token, { action: "delete", target: serviceName });

      return {
        content: [
          {
            type: "text",
            text: [
              `⚠️ **削除確認が必要です**`,
              `サービス: ${serviceName}`,
              `この操作は取り消せません。`,
              `削除を実行するには confirmationToken: "${token}" を指定して再度呼び出してください。`,
            ].join("\n"),
          },
        ],
      };
    }

    const pending = pendingConfirmations.get(confirmationToken);
    if (!pending || pending.target !== serviceName) {
      throw new Error("無効な確認トークンです。操作を最初からやり直してください。");
    }

    pendingConfirmations.delete(confirmationToken);
    const name = `projects/${PROJECT}/locations/${region}/services/${serviceName}`;
    await runClient.deleteService({ name });

    return {
      content: [{ type: "text", text: `✅ サービス ${serviceName} を削除しました。` }],
    };
  }
);
```

### デモシナリオ：Blue/Green デプロイ

実際に Claude Code に送った指示と動作の流れを示します。

```
[ユーザー]
my-api サービスの新バージョン gcr.io/my-project/my-api:v2.0 をデプロイして、
まず 10% だけトラフィックを流して、ログを 20 件確認して問題なければ 100% に切り替えて

[Claude Code の動作]
1. deploy_service("my-api", "gcr.io/my-project/my-api:v2.0") を呼び出す
2. update_traffic("my-api", latestPercent: 10) を呼び出す
3. get_logs("my-api", limit: 20) を呼び出す
4. エラーなし確認 → update_traffic("my-api", latestPercent: 100) を呼び出す
5. 結果をサマリーして報告
```

この一連の操作が自然言語一発で完結します。

---

## BigQuery の自動化 ─ スキーマ探索からクエリ実行まで

### 安全なクエリ実行パターン

BigQuery 操作で最も重要なのは**実行前のコスト見積もり**です。`dryRun: true` を使った事前チェックを必ず挟みます。

```typescript
// src/tools/bigquery.ts
import { BigQuery } from "@google-cloud/bigquery";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";

const bq = new BigQuery({ projectId: process.env.GOOGLE_CLOUD_PROJECT });

// コスト閾値（GB）。環境変数で上書き可能
const COST_THRESHOLD_GB = Number(process.env.BQ_COST_THRESHOLD_GB ?? "10");

export function registerBigQueryTools(server: McpServer) {
  server.tool(
    "execute_query",
    "BigQuery でクエリを実行する（実行前にコスト見積もりを行う）",
    {
      query: z.string().describe("実行する StandardSQL クエリ"),
      maxResults: z.number().default(100).describe("最大取得行数"),
      forceRun: z
        .boolean()
        .default(false)
        .describe("コスト閾値超過時でも強制実行する"),
    },
    async ({ query, maxResults, forceRun }) => {
      // Dry Run でコスト見積もり
      const [dryRunJob] = await bq.createQueryJob({ query, dryRun: true });
      const estimatedBytes = Number(
        dryRunJob.metadata.statistics.totalBytesProcessed
      );
      const estimatedGB = estimatedBytes / 1e9;

      if (estimatedGB > COST_THRESHOLD_GB && !forceRun) {
        return {
          content: [
            {
              type: "text",
              text: [
                `⚠️ コスト閾値超過: 推定 ${estimatedGB.toFixed(2)} GB（閾値: ${COST_THRESHOLD_GB} GB）`,
                `強制実行する場合は forceRun: true を指定してください。`,
              ].join("\n"),
            },
          ],
        };
      }

      // 本実行
      const [job] = await bq.createQueryJob({ query });
      const [rows] = await job.getQueryResults({ maxResults });

      const processedGB = (
        Number(job.metadata.statistics.totalBytesProcessed) / 1e9
      ).toFixed(3);

      return {
        content: [
          {
            type: "text",
            text: [
              `✅ クエリ完了 | 処理データ量: ${processedGB} GB | 取得行数: ${rows.length}`,
              "```json",
              JSON.stringify(rows.slice(0, maxResults), null, 2),
              "```",
            ].join("\n"),
          },
        ],
      };
    }
  );

  // スキーマ探索 Tool
  server.tool(
    "get_schema",
    "BigQuery テーブルのスキーマを取得する",
    {
      dataset: z.string(),
      table: z.string(),
    },
    async ({ dataset, table }) => {
      const [metadata] = await bq.dataset(dataset).table(table).getMetadata();
      const schema = metadata.schema.fields;

      const schemaText = schema
        .map(
          (f: any) =>
            `- ${f.name} (${f.type}${f.mode === "REQUIRED" ? ", REQUIRED" : ""}): ${f.description ?? ""}`
        )
        .join("\n");

      return {
        content: [
          {
            type: "text",
            text: `## ${dataset}.${table} スキーマ\n\n${schemaText}`,
          },
        ],
      };
    }
  );

  // データセット・テーブル一覧 Tool
  server.tool(
    "list_tables",
    "BigQuery データセット内のテーブル一覧を取得する",
    {
      dataset: z.string(),
    },
    async ({ dataset }) => {
      const [tables] = await bq.dataset(dataset).getTables();
      const tableList = tables
        .map((t) => `- ${t.id} (${t.metadata.type})`)
        .join("\n");

      return {
        content: [
          {
            type: "text",
            text: `## ${dataset} のテーブル一覧\n\n${tableList}`,
          },
        ],
      };
    }
  );
}
```

### デモシナリオ：日次売上レポートの自動集計

```
[ユーザー]
sales データセットの orders テーブルで先週の日次売上を集計して、
前週比も出して Markdown テーブル形式で見せて

[Claude Code の動作]
1. list_tables("sales") → テーブル構造を把握
2. get_schema("sales", "orders") → カラム定義を確認
3. execute_query(集計 SQL) → dryRun でコスト確認 → 実行
4. 結果を Markdown テーブルに整形して返答
```

Claude が自動でスキーマを読んで適切な SQL を生成するため、テーブル定義を毎回調べる手間がなくなります。

---

## IAM の自動管理 ─ 最小権限原則を維持する

### 危険ロールのガードレール

IAM 操作で最も重要なのは「付与してはいけないロール」の明示的なブロックです。

```typescript
// src/tools/iam.ts
import { IAMClient } from "@google-cloud/iam";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";

// 自動付与を禁止するロール一覧
const BLOCKED_ROLES = new Set([
  "roles/owner",
  "roles/editor",
  "roles/iam.securityAdmin",
  "roles/iam.serviceAccountTokenCreator",
]);

export function registerIamTools(server: McpServer) {
  server.tool(
    "add_iam_binding",
    "IAM ロールバインディングを追加する",
    {
      member: z.string().describe("例: serviceAccount:name@project.iam.gserviceaccount.com"),
      role: z.string().describe("付与するロール（例: roles/run.invoker）"),
      resource: z.string().optional().describe("リソース指定（省略時はプロジェクトレベル）"),
    },
    async ({ member, role, resource }) => {
      // ガードレール: 危険ロールのブロック
      if (BLOCKED_ROLES.has(role)) {
        return {
          content: [
            {
              type: "text",
              text: [
                `🚫 セキュリティポリシー違反: ${role} の自動付与は禁止されています。`,
                `最小権限の原則に従い、必要最小限のロールを使用してください。`,
                `承認が必要な場合は手動でコンソールから操作してください。`,
              ].join("\n"),
            },
          ],
        };
      }

      // プロジェクトの IAM ポリシーを取得して更新
      const project = process.env.GOOGLE_CLOUD_PROJECT!;
      const { ProjectsClient } = await import("@google-cloud/resource-manager");
      const rmClient = new ProjectsClient();

      const [policy] = await rmClient.getIamPolicy({ resource: `projects/${project}` });
      
      // 既存バインディングへの追加 or 新規作成
      const existingBinding = policy.bindings?.find((b) => b.role === role);
      if (existingBinding) {
        if (!existingBinding.members?.includes(member)) {
          existingBinding.members = [...(existingBinding.members ?? []), member];
        }
      } else {
        policy.bindings = [...(policy.bindings ?? []), { role, members: [member] }];
      }

      await rmClient.setIamPolicy({ resource: `projects/${project}`, policy });

      return {
        content: [
          {
            type: "text",
            text: `✅ IAM バインディング追加完了\nメンバー: ${member}\nロール: ${role}`,
          },
        ],
      };
    }
  );

  // サービスアカウント一覧 Tool
  server.tool(
    "list_service_accounts",
    "プロジェクトのサービスアカウント一覧を取得する",
    {},
    async () => {
      const { IAMClient: GIAMClient } = await import("@google-cloud/iam");
      const iamClient = new GIAMClient();
      const project = process.env.GOOGLE_CLOUD_PROJECT!;

      const [response] = await iamClient.listServiceAccounts({
        name: `projects/${project}`,
      });

      const list = response.accounts
        ?.map(
          (sa) =>
            `- ${sa.email}\n  表示名: ${sa.displayName ?? "(なし)"}\n  無効: ${sa.disabled ? "はい" : "いいえ"}`
        )
        .join("\n\n");

      return {
        content: [{ type: "text", text: `## サービスアカウント一覧\n\n${list}` }],
      };
    }
  );
}
```

### Workload Identity Federation との組み合わせ

本番環境では、サービスアカウントキーファイルを使わず **Workload Identity Federation** を使うことを強く推奨します。

```bash
# GitHub Actions からの認証設定例
gcloud iam workload-identity-pools create "github-pool" \
  --location="global" \
  --display-name="GitHub Actions Pool"

gcloud iam workload-identity-pools providers create-oidc "github-provider" \
  --location="global" \
  --workload-identity-pool="github-pool" \
  --issuer-uri="https://token.actions.githubusercontent.com" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository"
```

MCP サーバー自体を Cloud Run 上で動かす場合は、サービスアカウントを Attach するだけで ADC が自動的に機能します。キーファイルの管理が不要になり、セキュリティリスクが大幅に下がります。

---

## セキュリティ設計のまとめ

実務導入にあたって設計すべきガードレールを整理します。

### チェックリスト

| 項目 | 対策 |
|------|------|
| 認証 | ADC / Workload Identity（キーファイル禁止） |
| 認可 | 最小権限ロールのみ許可。`BLOCKED_ROLES` で危険ロールをブロック |
| 危険操作 | 確認トークンによる二段階確認 |
| コスト | BigQuery dryRun による事前見積もりと閾値チェック |
| 監査 | Cloud Audit Logs を有効化し、IAM 変更を追跡 |
| MCP サーバー自体 | stdio 利用時は localhost 限定。HTTP 公開時は mTLS + 認証ヘッダー |

### Terraform との使い分け

MCP + Claude Code は Terraform の置き換えではありません。**役割が異なります**。

| 用途 | 推奨ツール | 理由 |
|------|-----------|------|
| インフラの初期構築・設計 | Terraform | 再現性・レビュー・State 管理 |
| 日常の探索・デバッグ | MCP + Claude Code | 速度・柔軟性 |
| ログ確認・データ分析 | MCP + Claude Code | 自然言語での指示が直感的 |
| 本番の重要変更 | Terraform（レビュー必須） | 監査性・ロールバック保証 |

「探索フェーズは Claude Code、確定したら Terraform で IaC 化」という使い分けが現実的です。

---

## まとめ

本記事で構築した環境のポイントを振り返ります。

**実現したこと**
- MCP サーバーを自作し、Claude Code から GCP を自然言語で操作できる環境を構築
- Cloud Run のデプロイ・トラフィック分割・ログ確認をワンライナーの指示で完結
- BigQuery のコスト見積もりを挟んだ安全なクエリ実行パターンを実装
- IAM の危険ロール自動ブロックと確認トークンによる二段階確認を設計

**次のステップとして検討していること**
- MCP サーバーを Cloud Run 上で動かしてチームで共有
- Cloud Monitoring・Alert Policy の操作ツールを追加
- `dangerous_operation` フラグを持つ Tool を Slack 通知経由で承認する Human-in-the-Loop フロー

「AIがコードを書く時代」から「AIがインフラを操作する時代」へ、確実に移行しつつあります。セキュリティガードレールをしっかり設計した上で、ぜひ試してみてください。

---

## 参考リンク

- [Model Context Protocol 公式ドキュメント](https://modelcontextprotocol.io/)
- [Google Cloud Run Admin API リファレンス](https://cloud.google.com/run/docs/reference/rest)
- [BigQuery クライアントライブラリ（Node.js）](https://cloud.google.com/bigquery/docs/reference/libraries#client-libraries-install-nodejs)
- [Workload Identity Federation](https://cloud.google.com/iam/docs/workload-identity-federation)
- [Cloud Audit Logs](https://cloud.google.com/logging/docs/audit)
