---
title: "Claude Codeのpermissions.deny設計ガイド — 250件の実践的ルールセットとdeny/askの使い分け"
emoji: "🛡️"
type: "tech"
topics: ["claudecode", "security", "permissions", "ai", "devops"]
published: false
status: "publish-ready"
publication_name: "correlate_dev"
---

Claude Codeを業務で使い込むほど、ある問題に直面します。「AIエージェントに自由にコマンドを実行させたい。しかし `.env` を読まれたり、`git push --force` されたりするのは困る」という矛盾した要求をどう両立させるか。

本記事では、筆者が実際に運用している250件の `permissions.deny` ルールセットを題材に、deny/ask/allowの使い分け基準、見落としがちなBash経由の漏洩経路、そして正直に認めるべき構造的限界までを解説します。

## permissions.deny とは何か

Claude Codeの `~/.claude/settings.json` には、AIエージェントが実行できる操作を制御する3層の権限モデルがあります。

| レベル | 動作 | 用途 |
|--------|------|------|
| allow | 確認なしで実行 | 日常的に安全な操作 |
| ask | 毎回ユーザーに確認 | 状況次第で許可したい操作 |
| deny | 完全拒否(実行不可) | いかなる状況でも実行させない操作 |

`deny` に登録された操作は、Claude Codeがどれだけ「必要です」と主張しても実行されません。`allow` / `deny` のいずれにもマッチしない操作は、ユーザーに確認を求めるデフォルト動作となります。`permissions.ask` で明示的にパターンを指定することも可能です。 **deny は allow より優先される** ため、仮にallowにマッチするパターンであっても、denyにもマッチすれば拒否される仕組みです。

## 250件の内訳 ── 8カテゴリの全体像

筆者の `settings.json` に登録されている250件のdenyルールは、以下の8カテゴリに分類されます。

| カテゴリ | 件数 | 防御対象 |
|----------|------|----------|
| システム破壊 | 10 | rm -rf /, dd, mkfs, shutdown等 |
| Git破壊的操作 | 12 | force push, reset --hard, branch -D等 |
| データベース破壊 | 20 | DROP TABLE, TRUNCATE(BQ/PostgreSQL/MySQL/Redis/MongoDB) |
| インフラ・デプロイ | 14 | terraform destroy, vercel deploy --prod, gh repo delete等 |
| Bash経由ファイル読み取り | 74 | cat/head/tail/less/more/strings による機密ファイル参照 |
| Bash経由ファイル移動 | 34 | cp/mv による機密ファイルのコピー・移動 |
| Read/Edit/Write ツール | 81 | Claude Code組み込みツールによる機密ファイル操作 |
| MCP操作 | 5 | freee APIの書き込み系操作 |

注目すべきは、全体の約76%(189件)が **機密ファイルの保護** に費やされている点です。破壊的コマンドの防御は56件(22%)に過ぎません。実運用で本当に危険なのは「派手な破壊」よりも「静かな情報漏洩」だということが、この比率から読み取れます。

## deny vs ask の判断基準

すべてをdenyにすれば安全ですが、作業効率が著しく低下します。筆者が運用する中で固まった判断基準は、 **「取り返しがつくか」の一点** です。

### deny にすべき操作

```
判定基準: 実行後に git revert 1回で戻せない操作
```

具体的には以下の3パターンが該当します。

1. **不可逆な破壊**: `rm -rf /`, `DROP TABLE`, `terraform destroy`
2. **情報漏洩**: `.env` の内容表示、SSH秘密鍵の読み取り
3. **外部への不可逆な副作用**: `git push --force`(他者のコミット消失)、`npm publish`(バージョン番号の消費)

### ask のまま残すべき操作

```
判定基準: 実行結果を確認してから判断したい操作
```

たとえば以下は、denyにすると業務に支障が出る操作です。

- `rm temp.txt`(一時ファイルの削除)
- `kubectl delete pod`(Podの再起動)
- `bq query "DELETE FROM table WHERE condition"`(条件付きDELETE)

これらはaskのままにしておき、Claude Codeが「この操作を実行してよいか」と聞いてきた時点で人間が判断するのが実用的な運用方針です。

### 実例: BigQueryのdeny設計

BigQueryは筆者の環境で最も頻繁に使うデータベースです。deny設計は以下のようになっています。

```jsonc
// deny: DDLレベルの破壊的操作
"Bash(bq query *DROP *)",
"Bash(bq query *TRUNCATE *)",
"Bash(bq query *drop *)",      // 小文字パターンも必要
"Bash(bq query *truncate *)",
"Bash(bq rm *)",               // データセット・テーブル削除
"Bash(bq *rm *)",              // サブコマンド経由の削除

// ask(denyに入れない): DMLレベルの操作
// bq query "DELETE FROM dataset.table WHERE ..."
// bq query "UPDATE dataset.table SET ..."
// bq query "MERGE dataset.table USING ..."
```

`DROP TABLE` と `DELETE WHERE` の違いは、 **前者はテーブル定義ごと消える** のに対し、後者は条件次第でロールバック可能な点にあります。DMLまでdenyにすると、Claude Codeにデータ加工を任せられなくなるため、askで運用しています。

SQL文のキーワードには大文字・小文字の両パターンが必要です。Claude Codeは状況によって `DROP TABLE` とも `drop table` とも生成するため、片方だけでは漏れが発生します。

## 見落としがちな漏洩経路 ── deny対称性パターン

ここからが本記事の核心部分です。

### Read/Edit だけブロックしても意味がない

多くのユーザーは `.env` ファイルの保護として、以下のように設定します。

```jsonc
// よくある設定(不十分)
"Read: **/.env",
"Read: **/.env.*",
"Edit: **/.env",
"Edit: **/.env.*"
```

一見、完璧に見えるかもしれません。しかしClaude Codeには `Bash` ツールがあります。

```bash
# Read ツールがブロックされても、Bash経由で読める
cat .env
head -5 .env.production
tail -1 .env.local
strings .env.development
```

Claude Codeの `Read` ツールと `Bash(cat ...)` は **完全に別の経路** です。Readをdenyしても、Bashのcatはブロックされません。筆者はこの問題を実運用中に発見し、「deny対称性パターン」として体系化しました。

### deny対称性パターンの全容

1つの機密ファイルに対して、Bash経由（8パターン）とClaude Codeツール経由（6パターン）の **計14経路をdenyする** のが対称性パターンの基本形です。

```jsonc
// .envファイルの完全防御(14経路)
// --- Bash経由の読み取り(6パターン) ---
"Bash(cat **/.env*)",
"Bash(head **/.env*)",
"Bash(tail **/.env*)",
"Bash(less **/.env*)",
"Bash(more **/.env*)",
"Bash(strings **/.env*)",

// --- Bash経由のコピー・移動(2パターン) ---
"Bash(cp **/.env* *)",
"Bash(mv **/.env* *)",

// --- Claude Codeツール(6パターン) ---
"Read: **/.env",
"Read: **/.env.*",
"Edit: **/.env",
"Edit: **/.env.*",
"Write: **/.env",
"Write: **/.env.*"
```

`cp` と `mv` をブロックする理由は、「ファイルを別名にコピーしてから読む」という迂回を防ぐためです。`strings` はバイナリファイルからテキストを抽出するコマンドですが、テキストファイルに対しても動作するため、対象に含めます。

### 保護対象ファイルの一覧

筆者の環境では、以下のファイル群に対してこの対称性パターンを適用しています。

```
.env / .env.* / .envrc         ← 環境変数
*credentials*.json             ← GCP/AWS認証
*.pem / *.key / *.p12 / *.pfx  ← 証明書・秘密鍵
*token*.json / secrets.json    ← APIトークン
*.secret / secret.yaml         ← シークレットファイル
~/.ssh/*                       ← SSH鍵
~/.aws/*                       ← AWSクレデンシャル
~/.kube/*                      ← Kubernetes設定
~/.netrc / ~/.npmrc            ← 認証トークン
~/.claude*                     ← Claude Code設定自体
~/.config/*                    ← gcloud等の設定
~/Library/Keychains/*          ← macOSキーチェーン
id_rsa* / id_ed25519*          ← SSH秘密鍵(パス不問)
google-ads.yaml                ← Google Ads認証
freee-mcp/**                   ← freee API認証
```

SSH鍵は `~/.ssh/*` に加えて `**/id_rsa*` と `**/id_ed25519*` もdenyに追加し、プロジェクトディレクトリやバックアップ先にコピーされた鍵も保護対象としています。

## Git操作の防御設計

Git関連のdenyは12件で、大きく2グループに分かれます。

### 破壊的操作の完全拒否

```jsonc
"Bash(git push --force*)",
"Bash(git push -f *)",
"Bash(git reset --hard*)",
"Bash(git branch -D *)",      // 大文字Dのみ(強制削除)
"Bash(git clean -f*)",
"Bash(git checkout -- .)",
"Bash(git restore .)",
"Bash(git switch --discard-changes*)"
```

`git branch -d`(小文字d)はdenyに含めていません。小文字dはマージ済みブランチのみ削除可能で、未マージブランチを削除しようとするとGit自体がエラーを出すため、安全弁が二重に働きます。

### フック・署名のバイパス防止

```jsonc
"Bash(git commit --no-verify*)",
"Bash(git commit * --no-verify*)",
"Bash(git commit --no-gpg-sign*)",
"Bash(git commit * --no-gpg-sign*)"
```

Claude Codeは pre-commit hookが失敗すると、効率を優先して `--no-verify` を付けたがることがあります。hookは品質ゲートとして機能しているため、バイパスを許可すると本末転倒です。 **フラグの位置が `git commit` の直後とオプションの後の2パターンある** 点に注意が必要です。

## インフラ・デプロイの防御

```jsonc
// クラウドリソースの削除
"Bash(terraform destroy*)",
"Bash(gcloud * delete *)",

// 本番デプロイの防止
"Bash(vercel deploy --prod*)",
"Bash(vercel deploy * --prod*)",
"Bash(vercel env *)",           // 環境変数操作も拒否

// リポジトリ削除
"Bash(gh repo delete*)",

// パイプインストール(任意コード実行)
"Bash(curl *|*sh*)",
"Bash(curl *|*bash*)",
"Bash(wget *|*sh*)",
"Bash(wget *|*bash*)",

// 権限の過剰付与
"Bash(chmod 777 *)",
"Bash(chmod -R 777 *)",

// パッケージの公開
"Bash(npm publish*)"
```

`vercel deploy --prod` をdenyにしつつ `vercel deploy --prebuilt` はallow側に登録することで、ステージング経由のデプロイフローを強制しています。`curl | sh` パターンはパイプ実行の内容を事前確認できないため、完全拒否が適切です。

## MCP操作の制御

```jsonc
"mcp__freee-mcp__freee_api_post",
"mcp__freee-mcp__freee_api_put",
"mcp__freee-mcp__freee_api_patch",
"mcp__freee-mcp__freee_api_delete",
"mcp__freee-mcp__freee_file_upload"
```

MCPサーバー経由の外部API操作も `permissions.deny` で制御できます。freee(会計API)の書き込み系操作(POST/PUT/PATCH/DELETE)とファイルアップロードをdenyにしています。読み取り(GET)はaskのまま残し、会計データの参照はClaude Codeに許可する方針です。

外部APIへの書き込みは **「実行されてから気づいても遅い」** 典型例です。請求書の誤送信や仕訳の誤登録は、取り消しが困難なケースが多いため、denyが適切な判断になります。

## 構造的限界を正直に認める

ここまでの設計で多くの漏洩経路を塞げますが、 **完全ではありません** 。permissions.denyには以下の構造的限界があります。

### 限界1: グロブパターンマッチングの壁

`permissions.deny` はグロブパターン（`*`、`**` によるワイルドカード）で判定されます。コマンド文字列がパターンにマッチするかどうかで判定される仕組みです。

```bash
# denyされる
cat .env

# denyされない(パターン未登録)
python3 -c "print(open('.env').read())"
node -e "require('fs').readFileSync('.env','utf8')"
```

筆者の環境では `Bash(python3 *)` と `Bash(npx *)` がallow側に登録されています。これらを経由すれば、denyルールをバイパスして機密ファイルを読み取ることが理論上可能です。

### 限界2: 間接的な読み取りとリダイレクト

```bash
# grepの出力に.envの内容が含まれる
grep -r "API_KEY" .

# リダイレクトはマッチしない可能性がある
cat < .env
```

`grep` をdenyするとコード検索自体が不可能になるため、ここは許容せざるを得ない領域です。

### この限界にどう向き合うか

permissions.denyは **「誤操作防止の第1防御層」** であり、唯一の防御手段ではありません。CLAUDE.mdでの指示レベルの制約、.gitignore + pre-commit hookによるリポジトリ混入防止、settings.jsonのhooksセクションによるカスタム検証スクリプトと組み合わせた多層防御が前提です。

## 実践: 18件から始める最小構成

読者が自分の環境でdenyルールを設計するための、最小かつ実用的な出発点を示します。

```jsonc
{
  "permissions": {
    "deny": [
      // --- 1. システム破壊 ---
      "Bash(rm -rf /)",
      "Bash(rm -rf /*)",
      "Bash(rm -rf ~)",

      // --- 2. Git破壊的操作 ---
      "Bash(git push --force*)",
      "Bash(git push -f *)",
      "Bash(git reset --hard*)",

      // --- 3. .env対称性パターン ---
      "Bash(cat **/.env*)",
      "Bash(head **/.env*)",
      "Bash(tail **/.env*)",
      "Bash(strings **/.env*)",
      "Bash(cp **/.env* *)",
      "Bash(mv **/.env* *)",
      "Read: **/.env",
      "Read: **/.env.*",
      "Edit: **/.env",
      "Edit: **/.env.*",
      "Write: **/.env",
      "Write: **/.env.*"
    ]
  }
}
```

この18件だけで、最も深刻なリスクの大半をカバーできます。ここから自分の環境に合わせてルールを追加していくのが現実的な方針です。

新しい機密ファイルパターンを追加する際は、 **Read/Edit/Write + Bash cat/head/tail/strings/cp/mv の全経路が揃っているか** を毎回確認してください。1つでも欠けていれば、その経路から漏洩します。

deny vs askで迷ったら「git revert 1回で戻せるか」で判断し、迷うならdenyにしておく方が安全です。「とりあえずask」で始めて事故が起きてからdenyに変更するのでは遅い、という非対称性を意識してください。

## allow側の設計と注意点

deny側だけでなく、allow側の設計も重要です。筆者のallow設定(41件)から、設計の要点を抜粋します。

```jsonc
{
  "permissions": {
    "allow": [
      // Git: 読み取り系は自動許可
      "Bash(git status*)",
      "Bash(git log*)",
      "Bash(git diff*)",
      "Bash(git branch*)",

      // Git: 書き込み系も基本許可(hookが品質を担保)
      "Bash(git add *)",
      "Bash(git commit *)",

      // パッケージマネージャー
      "Bash(pnpm *)",
      "Bash(npm run *)",

      // デプロイ: ステージングのみ許可
      "Bash(vercel deploy --prebuilt *)"
    ]
  }
}
```

ポイントは、 `git commit *` をallowにしつつ `git commit --no-verify*` をdenyにしている点です。denyはallowより優先されるため、通常のコミットは自動許可されつつ、hookバイパスは確実にブロックされる設計になっています。

注意点として、`Bash(python3 *)` や `Bash(npx *)` をallowに含めると、ランタイム内から任意のファイル操作が可能になります。この経路はCLAUDE.mdでの指示レベルの制約と組み合わせて対処する必要があります。

## まとめ: permissions.denyは「安全帯」である

permissions.denyは万能ではありません。グロブパターンマッチングの限界があり、ランタイム経由のバイパスも理論上は可能です。しかし、それでも設定する価値は十分にあります。

シートベルトが衝突を防ぐのではなく被害を軽減するように、permissions.denyは **AIエージェントの「うっかり」を防ぐ安全帯** です。250件のルールを整備した環境では、`.env` の誤表示や `git push --force` の事故はゼロになりました。

設計の要点を3つにまとめます。

- **deny対称性パターン**: 1つの機密ファイルに対して Read/Edit/Write + Bash cat/head/tail/strings/cp/mv の全経路をブロックする
- **deny vs ask の判断基準**: 「git revert 1回で戻せるか」で仕分ける
- **構造的限界の認識**: python3/npx経由のバイパスは存在する。多層防御(CLAUDE.md + hooks + .gitignore)で補完する

まずは18件の最小構成から始めて、自分の環境に合わせて育てていくのが最も実践的なアプローチです。
