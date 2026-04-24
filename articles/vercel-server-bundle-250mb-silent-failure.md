---
title: "Vercelサーバーバンドル250MB制限の罠 — 12デプロイがサイレントに失敗していた"
emoji: "💥"
type: "tech"
topics: ["vercel", "nextjs", "deployment", "debugging", "performance"]
published: false
status: "publish-ready"
publication_name: "correlate_dev"
---

## 何が起きていたか

ある日、本番サイトが古いUIのままであることに気づいた。ローカルで `next build` は成功している。`git push` も通っている。Vercelのダッシュボードを開くと、デプロイは **"Ready"** と表示されている。

なのに、ブラウザに表示されるのは1時間前にリリースしたはずの変更が反映されていない古い画面だった。

最初はCDNのキャッシュを疑った。しかしVercelのキャッシュパージを実行しても変わらない。次のデプロイを試みた。また "Ready"。それでも変わらない。さらに3回、4回と push を繰り返した。

**結果として、12回以上のデプロイがすべてサイレントに失敗していた。**

## 問題の構造

今回の問題には2つの要因が重なっていた。

### 要因1: @google-cloud/bigquery のバンドル肥大化

Next.jsに横断検索機能を追加するため、Route Handler（API Route）から直接BigQueryを叩く実装を選択した。

```bash
npm install @google-cloud/bigquery
```

このパッケージ1つが **33個の依存パッケージ** を追加する。gRPC関連、Protocol Buffers、Google Auth Libraryなど、サーバーサイドのヘビーな依存関係群だ。ローカルの `node_modules` がどれだけ膨らんでいても気にしない開発者は多いが、Vercelにデプロイするとこれがそのままサーバーバンドルに含まれる。

### 要因2: site-data JSONの巨大配列

マチカルテ（議会発言分析サービス）では、自治体ごとの詳細データをJSONファイルとして `public/site-data/` に配置していた。

各自治体のJSONには次のような配列が含まれていた:

- `sessions`: 議会セッション一覧（無制限）
- `session_summaries`: セッションサマリー一覧（無制限）
- `kentou_items`: 審議項目一覧（無制限）

487自治体のデータを生成した結果、`public/site-data/` ディレクトリの合計サイズは **271MB** に達していた。

この `public/` 配下のファイルは、Next.jsのサーバーバンドルに含まれる。Vercelのサーバーバンドル上限は **250MB**。21MB超過していた。

## なぜサイレントに失敗するのか

ここが最も厄介なポイントだ。

Vercelのビルドログには何も残らない。デプロイステータスは "Ready" になる。URLも有効で、古いバージョンのページが返ってくる。エラーも警告も出ない。

実際のビルドログを確認しても、次のような正常に見えるメッセージしか出ていなかった。

```
✓ Compiled successfully
✓ Collecting page data
✓ Generating static pages (489/489)
✓ Finalizing page optimization
```

「ビルドは成功しているのにデプロイが反映されない」という状況で、開発者がバンドルサイズ制限を疑うことはほぼない。ログが存在しない以上、原因の特定には相当な時間がかかる。

## 発見の経緯

12回目のデプロイ失敗後、ふと Vercel のドキュメントを読み直していたときに、このセクションが目に入った。

> Serverless Functions have a maximum unzipped deployment size of 250 MB.

"unzipped deployment size" という表現が引っかかった。ローカルの `next build` 出力には `.next/server/` ディレクトリが含まれる。そのサイズを確認してみた。

```bash
du -sh .next/server/
# 278M    .next/server/
```

250MBを超えている。次に `public/site-data/` のサイズを確認した。

```bash
du -sh public/site-data/
# 271M    public/site-data/
```

原因が判明した瞬間だった。

## 解決策: 2段階アプローチ

### Step 1: JSONの配列サイズに上限を設ける

`generate_site_data.py`（site-dataを生成するPythonスクリプト）に件数制限を追加した。

```python
# 修正前: 無制限
data["sessions"] = all_sessions
data["session_summaries"] = all_summaries
data["kentou_items"] = all_kentou_items

# 修正後: 件数制限
MAX_SESSIONS = 20
MAX_SESSION_SUMMARIES = 20
MAX_KENTOU_ITEMS = 30

data["sessions"] = all_sessions[:MAX_SESSIONS]
data["session_summaries"] = all_summaries[:MAX_SESSION_SUMMARIES]
data["kentou_items"] = all_kentou_items[:MAX_KENTOU_ITEMS]
```

この変更だけで、`public/site-data/` のサイズは **271MB から約40MB** に削減された。

### Step 2: @google-cloud/bigquery をバンドルから除外

BigQueryへの直接接続を廃止し、Cloud Run（Python API）へのプロキシに変更した。

**変更前: Next.js Route Handler から直接BigQueryに接続**

```typescript
// app/api/search/route.ts（変更前）
import { BigQuery } from '@google-cloud/bigquery';

const bigquery = new BigQuery({
  projectId: process.env.GCP_PROJECT_ID,
  credentials: JSON.parse(process.env.GCP_CREDENTIALS!),
});

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const query = searchParams.get('q') ?? '';

  const [rows] = await bigquery.query({
    query: `
      SELECT municipality_code, speaker_name, body, year
      FROM \`project.dataset.speeches\`
      WHERE body LIKE @keyword
      LIMIT 100
    `,
    params: { keyword: `%${query}%` },
  });

  return Response.json({ results: rows });
}
```

**変更後: Cloud Run プロキシ経由**

```typescript
// app/api/search/route.ts（変更後）
const CLOUD_RUN_URL = process.env.CLOUD_RUN_URL!;

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const query = searchParams.get('q') ?? '';

  const res = await fetch(
    `${CLOUD_RUN_URL}/search?q=${encodeURIComponent(query)}`,
    { next: { revalidate: 60 } }
  );

  if (!res.ok) {
    return Response.json({ error: 'Search failed' }, { status: 502 });
  }

  const data = await res.json();
  return Response.json(data);
}
```

Cloud Run側（Python）でBigQueryへの接続を担当し、Next.jsはその結果を受け取るだけにした。

```python
# cloud-run/routers/search.py
from google.cloud import bigquery
from fastapi import APIRouter

router = APIRouter()
client = bigquery.Client()

@router.get("/search")
async def search(q: str, limit: int = 50):
    query = """
        SELECT municipality_code, speaker_name, body, year
        FROM `project.dataset.speeches`
        WHERE body LIKE @keyword
        ORDER BY year DESC
        LIMIT @limit
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("keyword", "STRING", f"%{q}%"),
            bigquery.ScalarQueryParameter("limit", "INT64", limit),
        ]
    )
    results = client.query(query, job_config=job_config).result()
    return {"results": [dict(row) for row in results]}
```

この変更によって `@google-cloud/bigquery` を `package.json` から完全に削除でき、バンドルサイズがさらに削減された。

### 最終結果

| 項目 | 変更前 | 変更後 |
|------|--------|--------|
| site-data ディレクトリ | 271MB | 15MB |
| サーバーバンドル合計 | 278MB | 約20MB |
| Vercel制限 | 250MB | 250MB |
| デプロイ結果 | サイレント失敗 | 正常反映 |

271MBから15MBへの削減。18分の1以下になった。

## バンドルサイズを事前に確認する方法

今後の再発防止として、デプロイ前にバンドルサイズを確認するコマンドを手順に組み込んだ。

```bash
# ローカルビルド後にサーバーバンドルサイズを確認
next build && du -sh .next/server/

# 内訳を詳しく見る（サイズ上位のファイルを表示）
find .next/server -type f | xargs du -sh | sort -rh | head -20

# public/ ディレクトリのサイズも確認（バンドルに含まれる）
du -sh public/
du -sh public/*/  # サブディレクトリ別
```

`next build` 成功後に必ずこれを確認するようにした。数字が250MBに近づいていたら、デプロイ前に対処できる。

## Vercel バンドル制限の仕様整理

今回の調査で理解した仕様をまとめる。

| 項目 | 内容 |
|------|------|
| 制限値 | 250MB（unzipped） |
| 対象 | `.next/server/` + `public/` の合計 |
| 超過時の挙動 | サイレント失敗（エラーログなし） |
| ローカル検知 | `next build` では検知不能 |
| 確認方法 | `du -sh .next/server/` でローカル確認 |
| 公式ドキュメント | [Serverless Function Size](https://vercel.com/docs/functions/runtimes#bundle-size) |

「public/ もバンドルに含まれる」という点は見落としやすい。静的ファイルはCDNから配信されるという認識が強いため、サーバーバンドルの計算に含めていない開発者は多いはずだ。

## 似たパターンで発生しやすいケース

今回のケースを抽象化すると、次のような条件が揃うとリスクが高い。

**`public/` に大量のJSONやデータファイルを配置しているとき**
- SSG（Static Site Generation）でデータを事前生成してpublic/に配置するパターン
- 検索インデックスファイルをpublic/に置くパターン（pagefind等）

**重量級のNode.jsパッケージを直接利用しているとき**
- `@google-cloud/bigquery`（gRPC依存）
- `aws-sdk`（モジュラー化前の旧バージョン）
- `puppeteer`（Chromiumバイナリを同梱）
- `sharp`（ネイティブバイナリ）

Vercelは `sharp` については [公式でサポート](https://vercel.com/docs/functions/runtimes/node-js#sharp-images)しており、バンドルから除外する設定が用意されている。しかし `@google-cloud/bigquery` のような重量級パッケージは自分で対処が必要だ。

## まとめ

3点に集約できる。

**サイレント失敗は最も厄介なバグカテゴリ**。エラーがなければ調査の手がかりがない。今回は「デプロイが反映されない」という現象から逆算して原因にたどり着いたが、気づかなければ古いバージョンが何日も配信され続けていた可能性がある。

**ローカルビルド成功はVercelデプロイ成功を保証しない**。`next build` と Vercel のビルドは異なる環境で動く。特にバンドルサイズ制限はローカルでは検知できない。デプロイ前の `du -sh .next/server/` 確認を習慣にするべきだ。

**重量級パッケージはバックエンドに隔離する**。Next.jsのRoute Handlerは便利だが、`@google-cloud/bigquery` のような重量級パッケージを直接importすると、バンドルサイズへの影響が大きい。Cloud RunやLambdaなどの専用バックエンドへのプロキシパターンが、長期的には安定する。

12回のデプロイ失敗から得た教訓として、次のプロジェクトでは **バンドルサイズを CI のチェック項目に加える** ことを検討している。

```yaml
# .github/workflows/build-check.yml の一例
- name: Check server bundle size
  run: |
    SIZE=$(du -sm .next/server/ | cut -f1)
    echo "Server bundle size: ${SIZE}MB"
    if [ "$SIZE" -gt 200 ]; then
      echo "WARNING: Server bundle size exceeds 200MB threshold (limit: 250MB)"
      exit 1
    fi
```

250MBに対して200MBをしきい値にすることで、制限到達前に警告を受け取れる。デプロイが何度サイレントに失敗したかを数え始めるより、こちらのほうがはるかに建設的だ。
