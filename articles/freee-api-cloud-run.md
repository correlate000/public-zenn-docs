---
title: "freee APIをCloud Runで動かして経理作業を月2時間に減らした話"
emoji: "🧾"
type: "tech"
topics: ["freee", "cloudrun", "python", "fastapi", "oauth"]
published: true
---

## 毎月の経理に半日費やしていた

合同会社を運営していると、開発以外の業務が地味に重い。特に経理。請求書の作成、経費の登録、入金の確認と消込。freeeを使っていても、ブラウザを開いて手作業で入力する時間が毎月半日ほどかかっていました。

「freeeにはAPIがあるんだから、自動化すればいいのでは？」

そう思ったのが半年前。実際にCloud Run上でfreee APIと連携する仕組みを作り、経理作業を月2時間程度まで圧縮できました。この記事ではその実装と、途中でハマったポイントを共有します。

## なぜGASではなくCloud Runなのか

freee APIの連携記事を探すと、Google Apps Script（GAS）での実装例がほとんど見つかります。GASは無料で手軽に始められるため、最初の選択肢としては妥当でしょう。

しかし、業務の中核に据えるにはいくつかの壁がありました。

| 項目 | GAS | Cloud Run |
|:--|:--|:--|
| 実行時間制限 | 6分（無料版） | 制限なし（タイムアウト設定可能） |
| 非同期処理 | 不可 | async/await対応 |
| 外部ライブラリ | 制限あり | pip install自由 |
| 他サービスとの統合 | GAS単体 | FastAPIでAPI統合可能 |
| デバッグ | ブラウザ上 | ローカルで完結 |
| 月額コスト | 無料 | 月額$0〜5程度（最小構成、CPU常時割り当ての場合） |

私の場合、すでにCloud Run上でDiscord BotとFastAPI APIサーバーが稼働していました。freee連携をここに追加するだけで、追加コストほぼゼロで経理自動化が実現します。GASで別の仕組みを作って二重管理するよりも、1つのコンテナに統合するほうがシンプルだと判断しました。

## OAuth2の最大のハマりどころ: リフレッシュトークン問題

freee APIの認証にはOAuth2を使います。ここで最大のハマりどころがあります。

:::message
`FREEE_CLIENT_ID`、`FREEE_CLIENT_SECRET`は環境変数またはGoogle Cloud Secret Managerで管理し、コードにハードコードしないでください。
:::

**freeeはリフレッシュトークンを「使い捨て」にする仕様です。**

一般的なOAuth2では、リフレッシュトークンは何度でも再利用できるケースが多いですが、freeeでは新しいアクセストークンを取得するたびに、リフレッシュトークンも新しいものが発行されます。古いリフレッシュトークンは無効になるため、新しいトークンを確実に保存しなければなりません。

```python
async def get_freee_access_token() -> str:
    """freee APIのアクセストークンを取得（必要に応じてリフレッシュ）"""
    global freee_access_token, freee_token_expires_at, _freee_current_refresh_token

    # トークンが有効ならそのまま返す（5分前にリフレッシュ）
    if freee_access_token and freee_token_expires_at:
        if datetime.now() < freee_token_expires_at - timedelta(minutes=5):
            return freee_access_token

    # トークンをリフレッシュ
    async with httpx.AsyncClient() as client:
        response = await client.post(
            'https://accounts.secure.freee.co.jp/public_api/token',
            data={
                'grant_type': 'refresh_token',
                'client_id': FREEE_CLIENT_ID,
                'client_secret': FREEE_CLIENT_SECRET,
                'refresh_token': _freee_current_refresh_token,
            }
        )
        if response.status_code == 200:
            data = response.json()
            freee_access_token = data['access_token']
            freee_token_expires_at = datetime.now() + timedelta(
                # freeeのアクセストークンの有効期限は6時間（21600秒）
                seconds=data.get('expires_in', 21600)
            )
            # ここが重要: 新しいリフレッシュトークンを保存
            if 'refresh_token' in data:
                _freee_current_refresh_token = data['refresh_token']
            return freee_access_token
```

ポイントは3つあります。

1. **5分前リフレッシュ**: トークンの有効期限ギリギリではなく、5分の余裕を持たせる
2. **新しいリフレッシュトークンの即時保存**: レスポンスに含まれる新しいリフレッシュトークンを必ずメモリ上に保持
3. **Cloud Runのステートレス性への対応**: Cloud Runのコンテナは再起動される可能性があるため、初期のリフレッシュトークンは環境変数で渡す

:::message alert
Cloud Runのコンテナが再起動されると、メモリ上のリフレッシュトークンは失われます。長期運用する場合は、Secret ManagerやFirestoreなどの永続ストレージにトークンを保存する仕組みが必要です。私の環境ではコンテナの再起動頻度が低いため、環境変数の初期値 + メモリ更新で運用していますが、頻繁にスケールイン/アウトする環境では注意してください。
:::

## freee APIのラッパーを作る

毎回認証ヘッダーの設定やエラーハンドリングを書くのは面倒なので、汎用的なラッパー関数を用意しています。

```python
async def freee_api_request(method: str, endpoint: str, data: dict = None) -> dict:
    """freee APIリクエストの共通処理"""
    token = await get_freee_access_token()
    if not token:
        return {'error': 'freee API認証が設定されていません'}

    url = f'https://api.freee.co.jp/api/1/{endpoint}'
    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
    }

    async with httpx.AsyncClient() as client:
        if method == 'GET':
            response = await client.get(url, headers=headers, params=data)
        elif method == 'POST':
            response = await client.post(url, headers=headers, json=data)
        else:
            return {'error': f'Unsupported method: {method}'}

        if response.status_code in [200, 201]:
            return response.json()
        else:
            # エラーメッセージを解析して返す
            error_msg = f'freee API error: {response.status_code}'
            try:
                error_json = response.json()
                if 'errors' in error_json:
                    messages = []
                    for err in error_json['errors']:
                        if 'messages' in err:
                            messages.extend(err['messages'])
                    if messages:
                        error_msg = f'{error_msg} - {", ".join(messages)}'
            except Exception:
                pass
            return {'error': error_msg}
```

`httpx`の非同期クライアントを使っているのは、FastAPIの非同期エンドポイントと相性が良いためです。`requests`でも動作しますが、他のAPI呼び出しと並行処理したい場面では非同期が有利になります。

:::message
実際の運用では`httpx.AsyncClient`はアプリケーション起動時に1つ生成して使い回すことを推奨します。上記コードでは説明の簡潔さを優先して毎回生成しています。また、並行リクエスト時のトークンリフレッシュには`asyncio.Lock`で排他制御を入れることを検討してください。
:::

## 請求書を自動作成する

請求書の作成は、freee APIの中でも最も使用頻度が高い機能でしょう。

:::message
freeeは請求書APIの分離を進めており、`/api/1/invoices`エンドポイントは将来的に変更される可能性があります。最新の仕様は[公式リファレンス](https://developer.freee.co.jp/reference/accounting/reference)を確認してください。
:::

```python
async def create_freee_invoice(
    partner_id: int,
    items: list,
    due_date: str,
    description: str = ''
) -> dict:
    """freeeで請求書を作成"""
    data = {
        'company_id': int(FREEE_COMPANY_ID),
        'partner_id': partner_id,
        'invoice_date': datetime.now(JST).strftime('%Y-%m-%d'),
        'due_date': due_date,
        'invoice_status': 'draft',
        'invoice_layout': 'default_classic',
        'tax_entry_method': 'exclusive',
        'invoice_contents': items,
        'description': description,
    }
    return await freee_api_request('POST', 'invoices', data)
```

`invoice_status`を`draft`にしているのは、自動で送信してしまうリスクを避けるためです。作成後にfreeeの管理画面で内容を確認し、問題なければ手動で送信するフローにしています。完全自動化も技術的には可能ですが、金額を間違えた請求書が自動送信されるリスクを考えると、最終確認は人間が行うほうが安全でしょう。

## 経費登録の自動化

経費登録では、勘定科目の推定ロジックを組み込んでいます。

```python
async def create_freee_expense(
    account_item_name: str,
    amount: int,
    description: str
) -> dict:
    """freeeで経費を登録"""
    # 以下のIDは一例です。実際にはGET /api/1/account_itemsエンドポイントから取得してください
    account_map = {
        '交通費': {'id': 4, 'name': '旅費交通費'},
        '会議費': {'id': 5, 'name': '会議費'},
        '接待費': {'id': 6, 'name': '接待交際費'},
        '消耗品': {'id': 7, 'name': '消耗品費'},
        '通信費': {'id': 8, 'name': '通信費'},
        '書籍':   {'id': 9, 'name': '新聞図書費'},
    }

    account = account_map.get(
        account_item_name,
        {'id': 7, 'name': '消耗品費'}
    )

    data = {
        'company_id': int(FREEE_COMPANY_ID),
        'issue_date': datetime.now(JST).strftime('%Y-%m-%d'),
        'type': 'expense',
        'details': [{
            'account_item_id': account['id'],
            'amount': amount,
            'description': description,
        }],
    }
    return await freee_api_request('POST', 'deals', data)
```

「交通費」「通信費」といったキーワードから勘定科目を自動判定します。マッチしない場合は「消耗品費」にフォールバック。この程度のマッピングでも、経費登録の手間はかなり減りました。

:::message
勘定科目IDはfreeeの事業所ごとに異なります。上記のIDは一例であり、実際にはfreee APIの`account_items`エンドポイントから自分の事業所の勘定科目一覧を取得して設定してください。
:::

## 日本語の金額表記にも対応する

地味に便利なのが、日本語の金額表記をパースする関数です。

```python
import re

def parse_amount(text: str) -> int:
    """金額テキストをパース
    対応形式: '50万', '3万5千', '1500', '¥3,000'
    未対応: '3万5000' のような万と数字の混合表記
    """
    text = text.replace(',', '').replace('円', '').replace('¥', '').strip()

    # 「50万」「3万5千」などの日本語表記
    match = re.search(r'(\d+)万(\d*)千?', text)
    if match:
        man = int(match.group(1)) * 10000
        sen = int(match.group(2)) * 1000 if match.group(2) else 0
        return man + sen

    # 純粋な数値
    match = re.search(r'(\d+)', text)
    if match:
        return int(match.group(1))

    return 0
```

Discord Botから「経費 交通費 1500 電車代」のように入力するとき、金額部分を柔軟にパースできます。「50万」と入力すれば500000に変換されます。ちょっとしたことですが、毎日使うツールでは入力の楽さが継続利用に直結します。

## 未入金チェックの自動化

毎月末に気になるのが「まだ入金されていない請求書はないか」の確認。これもAPIで自動化できます。

```python
async def get_freee_unpaid_invoices() -> list:
    """未入金の請求書一覧を取得"""
    result = await freee_api_request('GET', 'invoices', {
        'company_id': FREEE_COMPANY_ID,
        'invoice_status': 'sent',
        'payment_status': 'unsettled',
    })
    if 'error' in result:
        return []
    return result.get('invoices', [])
```

この関数を朝会ブリーフィングBot（Cloud SchedulerからCloud RunのHTTPエンドポイントをcronトリガーで毎朝実行）に組み込み、未入金がある場合はDiscordに通知しています。「あの請求書、入金まだだったかな...」とfreeeの画面を確認しにいく手間がなくなりました。

## Before / After

月5〜10件の請求書を発行している場合の目安です。

| | Before | After |
|:--|:--|:--|
| 請求書作成 | freee管理画面で手入力（15分/件） | Discordからコマンド実行（2分/件） |
| 経費登録 | freee管理画面で手入力（5分/件） | Discordからコマンド実行（30秒/件） |
| 未入金チェック | 月末にfreee画面を目視確認 | 毎朝自動通知 |
| 月次経理の合計時間 | 約4〜6時間 | 約2時間（freee管理画面での最終確認・月次決算チェック） |

数字で見ると劇的な変化ではないかもしれません。ただ、「あ、経理やらなきゃ」という心理的な負荷がなくなったのが一番大きい。Discordで普段の作業をしながら、ついでに経費を登録できる。この「ついで感」が自動化の本質だと感じています。

## ハマったポイントまとめ

実装中に遭遇した問題をまとめておきます。

### 1. リフレッシュトークンの消失

Cloud Runのコンテナが予期せず再起動した際、メモリ上のリフレッシュトークンが失われてAPIが使えなくなりました。環境変数に最新のトークンを手動で再設定して復旧。長期運用するなら永続ストレージへの保存が必須です。

### 2. freee APIのレートリミット

freee APIには1分あたり300リクエストの制限があります。一括処理（例: 過去の請求書を全件取得）を行う際は、リクエスト間に適切なウェイトを入れる必要がありました。

### 3. 勘定科目IDの不一致

freeeの勘定科目IDは事業所によって異なります。開発環境と本番環境で事業所が違う場合、IDのマッピングが合わなくなる。初回セットアップ時に`account_items`エンドポイントからIDを取得して設定ファイルに保存する仕組みを入れたほうがよいでしょう。

### 4. 日付のタイムゾーン

freee APIは日本時間（JST）で日付を扱います。Cloud Runのデフォルトタイムゾーンはutcなので、`datetime.now()`をそのまま使うと日付がずれる。明示的にJSTを指定する必要があります。

```python
JST = timezone(timedelta(hours=9))

def now_jst() -> datetime:
    return datetime.now(JST)
```

## まとめ

freee APIとCloud Runの組み合わせで、小規模法人の経理作業を大幅に効率化できます。GASでも同様のことは可能ですが、すでにCloud Run上に他のサービスが動いているなら、そこに統合するほうがシンプルです。

最初のハードルはOAuth2のトークン管理。特にfreee固有のリフレッシュトークン更新仕様は、知らないとハマります。この記事がその回り道を省く助けになれば幸いです。

自動化の効果は「時間の短縮」だけではありません。「やらなきゃいけない経理作業」が「ついでにできる経理作業」に変わること。この心理的な変化こそが、1人で会社を回すエンジニアにとって一番価値のある部分だと実感しています。

## 参考資料

- [freee会計API リファレンス](https://developer.freee.co.jp/reference/accounting/reference)
- [Cloud Run ドキュメント](https://cloud.google.com/run/docs)
- [httpx - 非同期HTTPクライアント](https://www.python-httpx.org/)
