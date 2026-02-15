---
title:" "1ファイル3500行超のPythonを恐れずにモジュール分割した話""
emoji: "🧩"
type: "tech"
topics: ["python", "fastapi", "refactoring", "architecture"]
published: true
publication_name: "correlate_dev"
---

## 約3500行のmain.pyに新機能を追加することになった

Cloud Run上で動いているFastAPIアプリがあります。Google Ads API、freee API、GAS連携、BigQueryクエリ、Discord Bot――業務に必要な機能を片っ端から`main.py`に書き足していった結果、ファイルは約3500行に膨れ上がっていました。

ある日、朝会ブリーフィング機能を追加する話が持ち上がります。毎朝Discord に予定・KPI・アラートを自動配信する仕組み。見積もりでは数百行規模の新規コード。

「このまま main.py に追記したら4000行を超える」

さすがに限界でした。新機能の追加をきっかけに、初めてモジュール分割に踏み切った記録をお話しします。

## なぜ約3500行の1ファイルが生まれたのか

答えは単純で、「動いているものに追記するのが一番早い」から。

最初は数百行のシンプルなAPIサーバーでした。ヘルスチェックとBigQueryへのデータ連携ぐらいの規模感。そこにGoogle Ads APIの連携を足し、freee APIの請求書作成を足し、GASとのデータ橋渡しを足し、Discord Botの対話機能を足し……。気づけばこんな構成になっていたわけです。

```
main.py（約3500行）の内訳
├── import + 設定変数       67行
├── Google Ads API        136行
├── freee API             266行
├── デプロイ管理           158行
├── FastAPI エンドポイント  139行
├── GAS API ヘルパー       250行
├── Discord 対話保存        85行
├── Discord Bot本体      2,272行  ← 全体の64%
└── Drive同期             149行
```

Discord Botセクションだけで2,272行。ファイル全体の64%を占めています。AIチャット応答、経理フロー、スレッド管理、キャッシュ処理――Botの中にさらに複数の責務が混在する状態。

「分割したほうがいい」のは誰が見ても明らかでしたが、動いているものを触るリスクと、分割に費やす工数を天秤にかけると、つい「今のままでも動くし……」となる。この先延ばしが約3500行を生んだ根本原因です。

## 分割のきっかけは「新機能の追加」

朝会ブリーフィング機能の設計を進める中で、2つの選択肢がありました。

| 案 | 内容 | メリット | デメリット |
|:---|:-----|:---------|:-----------|
| A | main.py に追記 | 変更ファイル1つ、既存の接続を直接利用 | 4000行超、さらに保守性悪化 |
| B | 別モジュールに分離 | 責務が明確、テスト可能 | DI設計が必要、import構造の検討 |

既存コードを大規模にリファクタリングする余裕はありません。ただ、新機能だけでも別モジュールにすれば、少なくともこれ以上の肥大化は止められる。

「既存は触らず、新規コードから分離を始める」――これが今回の基本戦略になりました。

## 分割の設計方針

### 原則: 薄いエンドポイント + 太いモジュール

main.pyのエンドポイントは呼び出しだけに徹し、ロジックの実体は別モジュールに置く方針を立てました。

```python
# main.py側（薄い）
@app.post("/api/morning-briefing")
async def api_morning_briefing(request: Request):
    from morning_briefing import MorningBriefing

    if not discord_bot or not discord_bot.is_ready():
        raise HTTPException(status_code=503, detail="Discord Bot is not ready")

    briefing = MorningBriefing(
        discord_bot=discord_bot,
        bq_client=bigquery.Client(project=PROJECT_ID),
    )
    result = await briefing.execute()
    return result
```

main.py側はわずか10行。Discord Botの接続チェックとモジュールの呼び出しだけ。

### 依存関係の整理: コンストラクタ注入

分割で最初にぶつかるのが「既存の接続オブジェクトをどう渡すか」という問題。main.pyにはDiscord Botインスタンスやグローバル変数が散らばっています。

解決策はシンプルなコンストラクタ注入（DI）でした。

```python
# morning_briefing.py
class MorningBriefing:
    def __init__(self, discord_bot, bq_client: bigquery.Client):
        self.bot = discord_bot
        self.bq = bq_client
        self._errors: dict[str, str] = {}
```

必要なオブジェクトはすべてコンストラクタで受け取る。モジュール内部からグローバル変数を参照しない。このルールを徹底するだけで、モジュールの独立性が大きく向上します。

テスト時にはモックを渡せばよいので、テスタビリティも自然と確保される設計。

### 環境変数は自モジュールで管理

ただし、環境変数だけは例外的にモジュール内で直接読み込む方針にしました。

```python
# morning_briefing.py 冒頭
GAS_API_URL = os.environ.get(
    "GAS_API_URL",
    "https://script.google.com/macros/s/YOUR_SCRIPT_ID/exec",
)
GOOGLE_CALENDAR_ID = os.environ.get("GOOGLE_CALENDAR_ID", "primary")
PROJECT_ID = "correlate-workspace"
```

理由は、環境変数まで全部コンストラクタで渡し始めると引数が膨れ上がるため。環境変数はデプロイ環境ごとに固定される値なので、モジュール側で直接読んでも結合度はそこまで上がらないと判断しました。

## 分離後のモジュール構造

`morning_briefing.py` は773行、1クラスに以下の責務をまとめた構成です。

```python
class MorningBriefing:
    # メインエントリポイント
    async def execute(self, channel_name="日報", dry_run=False) -> dict

    # データ収集（6ソース並列）
    async def _collect_data(self) -> dict
    async def _fetch_calendar_events(self) -> list
    async def _fetch_tasks(self) -> list
    async def _fetch_projects(self) -> list
    async def _fetch_kpi(self) -> dict
    async def _fetch_work_logs(self) -> dict
    async def _fetch_alerts(self) -> dict

    # Embed構築
    def _build_embeds(self, data: dict) -> list[discord.Embed]
    def _build_header_embed(self, ...) -> discord.Embed
    def _build_alert_embed(self, ...) -> discord.Embed
    # ... 計6種のEmbed

    # Discord送信 + BigQuery記録
    async def _send_to_discord(self, ...) -> dict
    async def _save_result(self, ...) -> None

    # ユーティリティ
    async def _is_already_delivered_today(self) -> bool  # 冪等性
    def _truncate_field(text: str, max_length: int = 1024) -> str   # 文字数制限
```

データ収集は `asyncio.gather` で6つのソースを並列実行。1つが失敗しても他は正常に動き、エラーはEmbedで明示表示する設計です。

```python
results = await asyncio.gather(
    self._fetch_calendar_events(),
    self._fetch_tasks(),
    self._fetch_projects(),
    self._fetch_kpi(),
    self._fetch_work_logs(),
    self._fetch_alerts(),
    return_exceptions=True,
)
```

:::message
`return_exceptions=True` を使えば、1つのデータソースの失敗が全体を巻き込むことを防げます。ただし、返り値が例外オブジェクトになるため、呼び出し側で `isinstance(val, Exception)` のチェックが必須。この見落としは実際にバグの温床になりやすいポイントです。
:::

## Before / After

| 観点 | Before | After |
|:-----|:-------|:------|
| ファイル構成 | main.py 1ファイル（約3500行） | main.py（約3550行）+ morning_briefing.py（773行） |
| 新機能の追加場所 | main.pyに追記 | 新規モジュールを作成 |
| テスト可能性 | グローバル変数依存で困難 | DI経由でモック差し替え可能 |
| 責務の明確さ | 9つの機能が1ファイルに混在 | 朝会機能は完全に独立 |
| 新規開発者の理解 | 約3500行を読む必要あり | 関心のあるモジュールだけ読めばよい |

main.py自体の行数は大きく減ってはいません。今回分離したのは新規コードであり、既存コードには手を入れていないからです。しかし、これ以上の肥大化に歯止めをかけたことと、次の分割の前例を作ったことに意味があります。

## やってみて気づいたこと

### 「最初の1モジュール」が一番重い

約3500行のファイルを前にして「どこから手をつけるか」を考え始めると、完璧な設計を追い求めてしまいがち。data_sources ディレクトリを作って、discord_utils を分けて、models を定義して――理想のフォルダ構成はいくらでも描けます。

ただ、理想を追うほど着手が遅れるのも事実。今回は「新機能の追加」という自然なタイミングで、1モジュールだけ切り出すことに集中しました。完璧なリファクタリングではなく、最小限の分離。その判断は正解だったと感じています。

### 既存コードを触らない安心感

既存のmain.pyには一切のロジック変更を加えていません。追加したのはエンドポイント定義（約20行）と、新モジュールのimport文だけ。

これは心理的な安全性として大きい。「分割したら既存機能が壊れるのでは」という恐怖が、巨大ファイルを放置する最大の理由だからです。新規コードから分離を始めれば、そのリスクはほぼゼロになります。

### 次に分割するなら

朝会ボットの安定稼働後、段階的に以下の分割を計画しています。

```
correlate-workspace/cloud-run/api/
├── main.py                    # ルーティングのみ（薄く保つ）
├── morning_briefing.py        # 朝会ブリーフィング（済）
├── auth.py                    # 認証・トークン管理
├── data_sources/
│   ├── calendar.py            # Google Calendar
│   ├── gas.py                 # GAS API
│   ├── bigquery_client.py     # BigQuery
│   └── freee.py               # freee API
└── discord_utils/
    ├── embed_builder.py       # Embed構築
    └── channel_manager.py     # チャンネル管理
```

FastAPIの公式ドキュメントでは `APIRouter` を使ったモジュール化が推奨されています。エンドポイントごとにRouterを分け、`app.include_router()` でメインアプリに統合する形。次のステップではこのパターンを採用する予定です。

:::details FastAPI APIRouter の基本パターン
```python
# routers/ads.py
from fastapi import APIRouter

router = APIRouter(prefix="/api/ads", tags=["ads"])

@router.get("/performance")
def get_performance(days: int = 7):
    ...

# main.py
from routers.ads import router as ads_router
app.include_router(ads_router)
```
:::

## まとめ: 巨大ファイルとの向き合い方

約3500行の巨大ファイルを前にして学んだのは、「完璧な分割」を目指す必要はないということ。

- 新機能の追加は、分割の最も自然なきっかけ
- 既存コードに触れず、新規コードだけ分離すればリスクはほぼゼロ
- コンストラクタ注入でモジュールの独立性を確保する
- 最初の1モジュールを切り出せば、次の分割は格段に楽になる

「いつか時間ができたらリファクタリングしよう」と思っているファイルがあるなら、次に機能を追加するタイミングが最初の一歩になるかもしれません。まずは1つ、モジュールを切り出してみてください。

---

*合同会社コラレイトデザインでは、実務で生じる設計判断を記録し、再現可能なパターンとして蓄積しています。*

## 参考資料

https://fastapi.tiangolo.com/tutorial/bigger-applications/

https://medium.com/@amirm.lavasani/how-to-structure-your-fastapi-projects-0219a6600a8f

https://github.com/zhanymkanov/fastapi-best-practices

https://zenn.dev/koduki/articles/b413f78b824688
