---
title: "朝会ボットを設計書から実装まで全部AIにやらせた話"
emoji: "🌅"
type: "tech"
topics: ["claudecode", "discord", "cloudrun", "python", "ai"]
published: true
publication_name: "correlate_dev"
---

## 毎朝30分かけて情報を集めていた

合同会社を1人で回していると、朝がとにかく忙しい。Google Calendarで今日の予定を確認し、freeeで未入金の請求書をチェックし、スプレッドシートでタスクの優先順位を眺め、BigQueryでKPIを引っ張る。全部バラバラのサービスに散在していて、毎朝30分はこの「情報収集ルーティン」に費やしていました。

「これ、1つのメッセージにまとめてDiscordに届けてくれたら最高なのに」

そう思ったのが2週間前。そこからClaude Codeと共同で設計書を書き、デビルズアドボケイト（DA）レビューで品質を磨き、774行のPythonモジュールとして実装するまでの記録です。朝の確認作業は5分以下になりました。

## なぜ朝会ボットなのか

1人法人のエンジニアにとって、朝のルーティンは地味に重い。チームがいれば朝会で情報共有されるけれど、1人だと全部自分で集めなければならない。しかもデータソースが6つもある。

| # | 情報 | サービス | 確認方法 |
|:--|:--|:--|:--|
| 1 | 今日の予定 | Google Calendar | ブラウザで開く |
| 2 | 未完了タスク | Google Sheets（GAS API） | スプレッドシートを開く |
| 3 | 進行中案件 | Google Sheets（GAS API） | 別のシートを開く |
| 4 | FIRE KPI | BigQuery | クエリを実行 |
| 5 | 昨日の工数 | BigQuery | 別のクエリを実行 |
| 6 | 未入金請求書 | freee | freeeにログイン |

6つのタブを開いて目視で確認する毎朝。これを1つのDiscordメッセージに集約できたら、朝の生産性が劇的に変わるはず。そんな構想から始まりました。

## AIと共同で1331行の設計書を書いた

「まず設計書を書こう」と決めたのは、過去の失敗から学んだ教訓でもあります。いきなりコードを書き始めると、途中で方針がブレて手戻りが発生する。特にAIと開発する場合、最初に全体像を共有しておかないと、毎回コンテキストの説明からやり直しになってしまいます。

Claude Codeとの対話で、以下の13セクションからなる設計書を作成しました。

:::details 設計書の13セクション一覧（1331行）
1. 概要（背景・配信先・配信項目）
2. アーキテクチャ判断（既存Cloud Run拡張 vs 新規実装）
3. データソース詳細（Calendar、GAS、BigQuery、freee）
4. BigQueryテーブル設計（クエリ定義含む）
5. Discord出力フォーマット（Embed JSONサンプル）
6. スケジューリング（Cloud Scheduler + OIDC認証）
7. 実装設計（モジュール構造・クラス設計）
8. 実装ステップ（Phase分けとタスク一覧）
9. 環境変数・認証情報一覧
10. エラーハンドリング（部分失敗・全失敗・冪等性）
11. 監視・モニタリング設計
12. テスト戦略（モック・シナリオ・CI/CD）
13. 将来の拡張
:::

ポイントは、最初から「完璧な設計書」を目指さなかったこと。Claude Codeに「朝会ボットを作りたい。6つのデータソースからデータを集めてDiscordに配信する」と伝え、対話を重ねながら肉付けしていった。AIは構造化が得意なので、箇条書きで要件を伝えるだけで、テーブルやコードブロックを含む設計書のドラフトが出てくる。人間はそれを読んで「これは違う」「ここをもっと詳しく」とフィードバックする。この繰り返しで設計書が育っていきます。

## デビルズアドボケイトが見つけた致命的なバグ

設計書を書いただけでは終わりません。ここからが本番。デビルズアドボケイト（DA）、つまり「あえて批判的な視点でレビューするAIエージェント」を投入しました。

DAの役割はシンプルです。設計書を読み、以下の視点で問題を指摘する。

- セキュリティの穴はないか
- 工数見積もりは楽観的すぎないか
- エラー時にどう振る舞うか定義されているか
- テスト戦略は十分か

### R1: 18件の問題が見つかった

最初のレビュー（R1）で、DAは18件の問題を検出しました。中でも致命的だったのが認証バグ。

```python
# R1で指摘された認証バグ（修正前）
@app.post("/api/morning-briefing")
async def api_morning_briefing(request: Request):
    auth = request.headers.get("Authorization")
    # SCHEDULER_SECRETが未設定の場合、認証をスキップしてしまう
    if SCHEDULER_SECRET and auth != f"Bearer {SCHEDULER_SECRET}":
        raise HTTPException(status_code=403)
    # ...
```

`SCHEDULER_SECRET`が環境変数に設定されていない場合、`if`文の最初の条件が`False`になり、認証チェックが丸ごとスキップされる。本番にデプロイしていたら、誰でもこのエンドポイントを叩けてしまう状態でした。

DAの指摘を受けて、認証方式をBearer TokenからOIDC（OpenID Connect）トークン方式に全面変更。Cloud Run IAMレベルで認証を行うことで、アプリケーションコードに認証ロジックを持つ必要がなくなり、この種のバグは構造的に発生しなくなりました。

:::message
DAレビューで見つかった問題の分類:
- 高優先度（6件） -- 認証バグ、テスト不在、工数見積もり楽観、main.py肥大化
- 中優先度（10件） -- 冪等性未設計、トークン管理、コールドスタート、監視未定義
- 低優先度（2件） -- Embed文字数制限、BigQueryパーティション
:::

### R2: さらに6件の追加指摘

R1の指摘を全て反映した設計書に対して、R2レビューを実施。今度は6件の新たな問題が見つかりました。

たとえば「テストエンドポイントが本番と機能的に同一」という指摘。テスト用の`/api/morning-briefing/test`が本番の`/api/morning-briefing`と全く同じ処理を実行する設計だったため、テスト実行のつもりで本番配信してしまうリスクがある。これをドライラン方式に変更し、Discord送信をスキップしてJSON形式で結果を返す仕組みに改めました。

また「Cloud Schedulerのリトライで同じ日に複数回配信されるリスク」の指摘から、BigQueryで当日の配信済みレコードをチェックする冪等性設計を追加。DAがいなければ、リトライ時の重複配信に気づかないまま本番運用していたかもしれません。

| レビューラウンド | 指摘件数 | 高優先度 | 主な発見 |
|:--|:--|:--|:--|
| R1 | 18件 | 6件 | 認証バグ、テスト不在、工数見積もり楽観 |
| R2 | 6件 | 3件 | テストの二重送信リスク、冪等性未設計、Secret Manager設計不足 |

DAレビュー2ラウンドで、設計書は約1.5倍に膨れ上がりました。しかし、膨れた分だけ「運用時に初めて気づく問題」を事前に潰せたことになる。1人開発でレビュー相手がいない環境でも、AIをDAとして活用すれば品質を大幅に引き上げられる。これは今回の最大の学びでした。

## 774行のPythonモジュールを実装する

設計書が固まったら、実装はスムーズに進みます。Claude Codeに設計書を読ませて「morning_briefing.pyを実装して」と伝えるだけで、設計書の仕様に沿ったコードが生成される。

### 6データソースの並列取得

6つのデータソースを順番に取得していたら、1つ30秒でも合計3分かかる。`asyncio.gather`で並列取得することで、最も遅いデータソースの応答時間だけで済むようになりました。

```python
async def collect_data(self) -> dict:
    """全データソースから並列でデータ収集"""
    calendar, tasks, projects, kpi, work_logs, alerts = await asyncio.gather(
        self.fetch_calendar_events(),
        self.fetch_tasks(),
        self.fetch_projects(),
        self.fetch_kpi(),
        self.fetch_work_logs(),
        self.fetch_alerts(),
        return_exceptions=True  # 1つが失敗しても他は継続
    )
    return {
        "calendar": calendar if not isinstance(calendar, Exception) else [],
        "tasks": tasks if not isinstance(tasks, Exception) else [],
        # ... 以下同様
    }
```

`return_exceptions=True`を指定しているのがポイントです。6つのうち1つが失敗しても、残り5つのデータは正常に取得できる。部分的に失敗した場合は、取得できたデータだけでブリーフィングを配信し、失敗したデータソースはエラーEmbedで明示する設計にしています。これもDAレビューで追加された仕様です。

### Discord Embed 5連結の色分け設計

朝のブリーフィングは一目で優先順位が分かることが重要。5つのEmbedを色分けして連結する設計にしました。

| Embed | 色 | 内容 |
|:--|:--|:--|
| ヘッダー | 青 | 挨拶 + 今日のカレンダー予定 |
| アラート | 赤 | 未入金・納期接近・補助金期限 |
| KPI | 緑 | FIRE KPIサマリー |
| タスク | 黄 | 優先タスク一覧 |
| 案件 | 紫 | 進行中案件 + 工数状況 |

赤いアラートEmbedが目に飛び込んでくれば「今日はまず未入金の対応から」と即座に判断できる。アラートが0件なら赤いEmbedは表示されないので、心理的にも安心して1日を始められます。

### 冪等性設計

Cloud Schedulerはリトライ機能があるため、同じ日に複数回エンドポイントが呼ばれる可能性がある。そこでBigQueryの`morning_briefings`テーブルで当日の配信済みチェックを行います。

```python
async def is_already_delivered_today(self) -> bool:
    """当日の配信済みチェック（冪等性保証）"""
    query = """
    SELECT COUNT(*) as cnt
    FROM `correlate-workspace.workspace.morning_briefings`
    WHERE date = CURRENT_DATE('Asia/Tokyo')
      AND status IN ('success', 'partial_failure')
    """
    result = self.bq.query(query).result()
    for row in result:
        return row.cnt > 0
    return False
```

`status = 'failure'`のレコードは再実行を許可するようにしています。失敗した配信のリトライは止めたくないが、成功した配信の重複は防ぎたい。この区別がDAレビューで指摘されて追加された設計です。

## Cloud Scheduler + OIDC認証の構成

スケジューリングにはCloud Schedulerを使用。毎朝7時（JST）にHTTP POSTリクエストをCloud Runに送信します。

```
Cloud Scheduler (07:00 JST)
    |
    | HTTP POST + OIDCトークン
    v
Cloud Run (correlate-api)
    |
    +-- morning_briefing.py
    |       |
    |       +-- Google Calendar API  → 今日の予定
    |       +-- GAS API /tasks       → タスク一覧
    |       +-- GAS API /projects    → 案件状況
    |       +-- BigQuery             → KPI, 工数
    |       +-- freee API            → 未入金チェック
    |       |
    |       +-- Discord Embed構築
    |       v
    +-- Discord #日次レポート へ送信
            └── 自動スレッド作成
```

認証にはOIDCトークン方式を採用しています。Cloud Schedulerが専用のサービスアカウント（`scheduler-sa`）でOIDCトークンを発行し、Cloud Run側はIAMレベルでトークンを検証する。アプリケーションコードに認証ロジックを書く必要がないため、先述の認証バグのような問題が構造的に発生しません。

```bash
# Cloud Schedulerジョブの作成（OIDC認証）
gcloud scheduler jobs create http morning-briefing \
  --schedule="0 7 * * *" \
  --time-zone="Asia/Tokyo" \
  --uri="https://correlate-api-xxxxx-an.a.run.app/api/morning-briefing" \
  --http-method=POST \
  --oidc-service-account-email="scheduler-sa@project.iam.gserviceaccount.com" \
  --oidc-token-audience="https://correlate-api-xxxxx-an.a.run.app" \
  --attempt-deadline=180s \
  --max-retry-attempts=3
```

`attempt-deadline=180s`はDAレビューで追加された設定。Cloud Runのコールドスタート時にdiscord.pyの接続確立に時間がかかるリスクを考慮して、デフォルトの120秒から延長しています。

## Before / After

| | Before | After |
|:--|:--|:--|
| 朝の情報収集 | 6サービスを個別に確認（30分） | Discord 1メッセージで完結（5分以下） |
| 未入金チェック | freeeにログインして目視確認 | 赤いアラートEmbedで自動通知 |
| KPI確認 | BigQueryでクエリを手動実行 | 緑のKPI Embedで毎朝自動表示 |
| タスク優先順位 | スプレッドシートを開いて確認 | 黄色いタスクEmbedに優先度順で表示 |
| 情報の網羅性 | 忙しい朝は確認を省略しがち | 毎朝漏れなく全情報が届く |

数字以上に変わったのは「朝の気分」かもしれません。以前は「今日は何を確認し忘れていないだろうか」という不安感がありました。今はDiscordを開けば全てが揃っている。この「漏れがない安心感」が1人法人の朝を変えてくれた実感があります。

## AIと設計する際のコツ

今回の体験から得た、AIと共同で設計・実装するためのコツをまとめます。

### 1. 設計書ファーストで実装は後

いきなり「コードを書いて」ではなく、まず設計書を作る。設計書があれば、AIは仕様に沿った一貫性のあるコードを生成できます。設計書がないと、会話のたびに方針がブレるリスクが高い。

### 2. DAを入れる（品質が段違い）

「批判的な視点でレビューして」とAIに指示するだけで、見落としていた問題が続々と出てくる。今回は認証バグ、冪等性未設計、テスト不在など、本番運用で致命的になりうる問題を事前に潰せた実績があります。

:::message
DAレビューのプロンプト例:
「この設計書をデビルズアドボケイトの視点で批判的にレビューしてください。セキュリティ、工数見積もり、エラーハンドリング、テスト戦略の観点で問題点を指摘し、重要度（高/中/低）を付けてください。」
:::

### 3. 具体的な数値・データで指示する

「適切に処理して」ではなく「Discord Embedのフィールド値は最大1024文字。超えた場合は末尾に"...（以下省略）"を付けて切り詰める」のように具体的に指示する。AIは具体的な制約条件を与えるほど、実用的なコードを生成します。

### 4. 失敗パターンを先に考える

「正常時の動作」だけでなく「1つのデータソースが落ちたらどうするか」「全部落ちたらどうするか」を先に設計する。DAレビューではこの観点が特に強化されます。

## まとめ

1人法人の朝を変えるために朝会ボットを作った話でした。設計書1331行、実装774行、DAレビュー2ラウンドで24件の問題を検出・修正。毎朝30分の手作業が5分以下の自動配信になりました。

技術的には、Cloud Scheduler + Cloud Run + OIDCの構成、asyncio.gatherでの並列データ取得、BigQueryによる冪等性チェックなど、ソロ開発でも堅牢なシステムを構築できることを実感しています。

1人開発でレビュー相手がいない環境でも、AIをデビルズアドボケイトとして活用すれば、チーム開発に近い品質管理が実現できる。この記事が、同じ悩みを持つエンジニアの第一歩になれば幸いです。

## 参考資料

https://cloud.google.com/run/docs

https://docs.cloud.google.com/run/docs/triggering/using-scheduler

https://discordpy.readthedocs.io/

https://zenn.dev/luup_developers/articles/server-jang-20251215

https://developer.freee.co.jp/reference/accounting/reference

https://zenn.dev/correlate/articles/freee-api-cloud-run

https://zenn.dev/correlate/articles/discord-bot-business
