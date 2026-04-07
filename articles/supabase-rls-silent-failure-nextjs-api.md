---
title: "Supabase RLSのサイレント失敗 — Next.js API Routeでエラーなしにnullが返る罠"
emoji: "🔕"
type: "tech"
topics: ["supabase", "nextjs", "rls", "typescript", "security"]
published: false
status: "draft"
publication_name: "correlate_dev"
---

## はじめに

「メール通知が届かない」というバグを調査していたら、原因はSupabaseのRLS（Row Level Security）でした。しかも厄介なのは、**エラーが一切発生しない**点です。

`data` は `null`、`error` は `null`。クエリ自体は成功しているように見えるのに、期待するデータが取得できない——これがRLSのサイレント失敗パターンです。

この記事では、実際にNext.js API Route 4ルートで同じ問題が横断的に発生していたケースを基に、原因と防御策を解説します。

## 問題の症状

次のコードを見てください。

```typescript
// app/api/reviews/create/route.ts
import { createRouteHandlerClient } from '@supabase/auth-helpers-nextjs'
import { cookies } from 'next/headers'

export async function POST(request: Request) {
  const supabase = createRouteHandlerClient({ cookies })
  
  // レビュー対象の組織のメールアドレスを取得
  const { data: profile, error } = await supabase
    .from('profiles')
    .select('email')
    .eq('id', organizationId)
    .single()
  
  console.log('profile:', profile) // → null
  console.log('error:', error)     // → null
  
  // emailがnullのままメール送信処理へ...
  await sendNotificationEmail(profile?.email) // 届かない
}
```

`error` が `null` なので、開発者はクエリが成功したと思い込みます。実際には、RLSがレコードを隠蔽しているのです。

## なぜエラーにならないのか

RLSの設計思想に起因します。

PostgreSQLのRLSは「アクセス権のないレコードを存在しないかのように扱う」仕様です。エラーを返すのではなく、フィルタリングした結果（0件）を返します。これはセキュリティ上の正しい判断です——エラーが返るとレコードの存在自体が漏洩してしまいます。

```sql
-- RLSポリシーの例
-- 自分のprofileのみ読み取り可能
CREATE POLICY "users can read own profile"
ON profiles FOR SELECT
USING (auth.uid() = id);
```

このポリシーがある状態で、`createRouteHandlerClient` を使って**他ユーザーのprofile**を取得しようとすると、クライアントはそのユーザーとして認証されているため、ポリシーで弾かれ、エラーなしに `null` が返ります。

## 問題が発生した4ルート

今回のケースでは、以下の4つのAPI Routeで同じパターンが存在していました。

| ルート | 取得しようとしていたデータ | 影響 |
|--------|--------------------------|------|
| `reviews/create` | レビュー対象組織のemail | 組織へのレビュー通知が届かない |
| `certificates/notify` | 証明書発行対象者のemail | 資格取得通知が届かない |
| `organizations/verify` | 申請者のemail | 審査結果通知が届かない |
| `completion-check` | 受講者のemail | 修了通知が届かない |

全てに共通するパターンは「**ユーザーAとして認証されたクライアントで、ユーザーBのデータを取得しようとしている**」です。

## 根本原因の整理

```
createRouteHandlerClient
  ↓ cookiesから認証情報を読み取る
  ↓ リクエストユーザー（例: 学生）として認証
  ↓ RLSポリシーが適用される
  ↓ 他ユーザーのレコードはフィルタリングされる
  → data: null, error: null
```

対して `createServiceRoleClient` は：

```
createServiceRoleClient
  ↓ SERVICE_ROLEキーを使用
  ↓ RLSポリシーをバイパス
  ↓ 全レコードにアクセス可能
  → data: { email: "..." }, error: null
```

## 解決策：認証とDB操作を分離する

正しいパターンは以下のとおりです。

```typescript
// app/api/reviews/create/route.ts
import { createRouteHandlerClient } from '@supabase/auth-helpers-nextjs'
import { createClient } from '@supabase/supabase-js'
import { cookies } from 'next/headers'

// Service Roleクライアントの生成（モジュールレベルで一度だけ）
function createServiceRoleClient() {
  return createClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.SUPABASE_SERVICE_ROLE_KEY!, // ← SERVICE_ROLEキー（公開禁止）
    {
      auth: {
        autoRefreshToken: false,
        persistSession: false,
      },
    }
  )
}

export async function POST(request: Request) {
  // 認証チェック: createRouteHandlerClientを使う（RLS適用）
  const supabaseAuth = createRouteHandlerClient({ cookies })
  const { data: { session } } = await supabaseAuth.auth.getSession()
  
  if (!session) {
    return new Response('Unauthorized', { status: 401 })
  }
  
  // 認証チェック: リクエストユーザーがレビュワー権限を持つか確認
  const { data: reviewer } = await supabaseAuth
    .from('profiles')
    .select('role')
    .eq('id', session.user.id)
    .single()
  
  if (reviewer?.role !== 'admin') {
    return new Response('Forbidden', { status: 403 })
  }
  
  // DB操作: Service Roleクライアントを使う（RLSバイパス）
  const supabaseAdmin = createServiceRoleClient()
  
  const { data: targetProfile, error } = await supabaseAdmin
    .from('profiles')
    .select('email, name')
    .eq('id', organizationId)
    .single()
  
  if (error || !targetProfile) {
    console.error('Profile fetch failed:', error)
    return new Response('Target not found', { status: 404 })
  }
  
  // 正常にemailが取得できる
  await sendNotificationEmail(targetProfile.email)
  
  return new Response('OK', { status: 200 })
}
```

ポイントは以下の2点です。

1. **認証・権限チェック**には `createRouteHandlerClient`（RLS適用）を使う
2. **実際のDB操作**には `createServiceRoleClient`（RLSバイパス）を使う

## SERVICE_ROLEキーの管理

`SUPABASE_SERVICE_ROLE_KEY` は絶対にクライアントサイドに露出してはいけません。

```bash
# .env.local
NEXT_PUBLIC_SUPABASE_URL=https://xxx.supabase.co  # クライアントから参照可能
NEXT_PUBLIC_SUPABASE_ANON_KEY=eyJ...              # クライアントから参照可能
SUPABASE_SERVICE_ROLE_KEY=eyJ...                  # サーバーサイドのみ（NEXT_PUBLICを付けない）
```

`NEXT_PUBLIC_` プレフィックスが**ない**ことを確認してください。Next.jsはこのプレフィックスがある環境変数のみクライアントバンドルに含めます。

## GET/POSTが同一ファイルにある場合の注意点

Next.jsのApp RouterではGETとPOSTを同一ファイルに書くことができます。Service Roleに切り替えた場合、**全ハンドラの認証ロジックを再確認**する必要があります。

```typescript
// app/api/organizations/[id]/route.ts

// GETは一般ユーザーも閲覧可能（RLS適用で安全）
export async function GET(request: Request) {
  const supabase = createRouteHandlerClient({ cookies })
  // RLSが適用されているのでそのまま使える
  const { data } = await supabase
    .from('organizations')
    .select('name, description') // publicカラムのみ
    .eq('id', params.id)
    .single()
  
  return Response.json(data)
}

// POSTは管理者のみ（Service Role使用 → 認証チェックが必須）
export async function POST(request: Request) {
  // ← ここで必ず認証チェックを行う
  const supabaseAuth = createRouteHandlerClient({ cookies })
  const { data: { session } } = await supabaseAuth.auth.getSession()
  
  if (!session) return new Response('Unauthorized', { status: 401 })
  
  // Service Roleでの操作
  const supabaseAdmin = createServiceRoleClient()
  // ...
}
```

GETをRLSありで安全に運用していても、同じファイルのPOSTでService Roleを使い始めた場合、認証チェックの抜け漏れに注意が必要です。

## デバッグ方法：サイレント失敗の検出

RLSのサイレント失敗をデバッグする際に有効な手法を紹介します。

### 1. Supabaseダッシュボードで直接クエリを実行する

```sql
-- SQL Editorでservice_roleとして実行（RLS無視）
SELECT * FROM profiles WHERE id = 'target-user-id';

-- anon/authenticated roleとして実行（RLS適用）
SET role = authenticated;
SET LOCAL "request.jwt.claims" = '{"sub": "current-user-id"}';
SELECT * FROM profiles WHERE id = 'target-user-id';
```

両者の結果を比較することで、RLSが原因かどうかをすぐに確認できます。

### 2. レスポンスに詳細ログを追加する

```typescript
const { data, error, count, status, statusText } = await supabase
  .from('profiles')
  .select('email', { count: 'exact' })
  .eq('id', targetId)
  .single()

console.log({
  data,
  error,
  count,    // 0件ならRLSで弾かれている可能性が高い
  status,   // 200 or 406
  statusText,
})
```

`count: 0` かつ `error: null` の場合、RLSによるフィルタリングを疑いましょう。

### 3. RLSポリシーを一時的に無効化して確認する

```sql
-- 開発環境のみ。本番では絶対に実行しない
ALTER TABLE profiles DISABLE ROW LEVEL SECURITY;
-- テスト後は必ず戻す
ALTER TABLE profiles ENABLE ROW LEVEL SECURITY;
```

これで問題が解消するなら、RLSが原因と確定できます。

## 予防策：コードレビューチェックリスト

同様の問題を防ぐために、以下のチェックリストをコードレビューに組み込むことを推奨します。

```
□ 他ユーザーのデータにアクセスするAPI RouteはService Roleを使っているか
□ Service Roleを使うルートには認証チェックが必ずあるか
□ SUPABASE_SERVICE_ROLE_KEY に NEXT_PUBLIC_ がついていないか
□ メール送信前にnullチェックをしているか
□ data取得後にerrorだけでなくdataそのものもnullチェックしているか
```

最後のnullチェックは特に重要です。

```typescript
// 危険なパターン
if (error) throw error
await sendEmail(data.email) // dataがnullの場合、TypeError

// 安全なパターン
if (error || !data) {
  console.error('Data fetch failed:', { error, data })
  return new Response('Not found', { status: 404 })
}
await sendEmail(data.email) // 確実にnullでない
```

## まとめ

Supabase RLSのサイレント失敗をまとめると：

- **原因**: `createRouteHandlerClient` はセッションユーザーとして認証されるため、RLSポリシーが他ユーザーのレコードをフィルタリングする
- **症状**: `data: null, error: null` となりクエリ成功に見える
- **解決**: 認証チェックは `createRouteHandlerClient`、DB操作は `createServiceRoleClient` で分離する
- **予防**: `data` のnullチェックを `error` と同様に必ず実施する

「エラーがないから正常」は罠です。特にメール送信・通知系の処理でRLSを使っている場合は、このパターンを疑う習慣を持つことが重要です。

## 参考

- [Supabase RLS ドキュメント](https://supabase.com/docs/guides/auth/row-level-security)
- [createRouteHandlerClient vs createServerClient](https://supabase.com/docs/guides/auth/server-side/creating-a-client)
- [PostgreSQL RLS仕様](https://www.postgresql.org/docs/current/ddl-rowsecurity.html)
