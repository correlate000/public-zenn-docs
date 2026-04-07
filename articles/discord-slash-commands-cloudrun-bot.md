---
title: "discord.py v2.x スラッシュコマンド設計パターン ─ Cog分割・Autocomplete・Modalを体系的に実装する"
emoji: "⚡"
type: "tech"
topics: ["discord", "python", "discordpy", "bot", "cloudrun"]
published: false
status: "draft"
publication_name: "correlate_dev"
---

## はじめに

Discord Botの開発において、スラッシュコマンドはユーザー体験の中心を担います。入力補完・型チェック・ヘルプの自動生成など、従来のプレフィックスコマンドにはない利便性があります。

しかし、discord.py v2.x で導入された `app_commands` モジュールは、v1.x までの `commands.Bot` と設計が大きく異なります。コマンドツリー（CommandTree）・Cog設計・Autocomplete・Modalといった概念を整理しないまま実装を進めると、コマンドが反映されない・シンクが重複するといった問題に悩まされます。

本記事では、discord.py v2.x を使ったスラッシュコマンドの ** 設計パターン ** を体系的に解説します。Cog分割によるスケーラブルなアーキテクチャを軸に、Autocomplete・Modal・Embedまで実践的なコード例を交えて紹介します。

:::message
本記事は **WebSocket型（Gateway接続） ** のBot実装に特化しています。Cloud RunのゼロスケールとHTTP Interaction型を組み合わせたコスト最適構成については、別記事「Discord Bot × Cloud Run ─ スラッシュコマンドとAI連携を含む本番デプロイガイド」を参照してください。
:::

---

## 1. discord.py v2.x における app_commands の基本概念

### CommandTree とは

v2.x では、スラッシュコマンドの管理に `discord.app_commands.CommandTree` を使います。`commands.Bot` がデフォルトで `CommandTree` を内包しており、`bot.tree` としてアクセスできます。

コマンドは `tree.sync()` で Discord API に登録されて初めて利用可能になります。 ** ファイルを保存しただけでは反映されない点 ** が、プレフィックスコマンドとの最大の違いです。

### グローバル vs ギルドコマンドの使い分け

コマンドの登録先には「グローバル」と「ギルド（特定サーバー）」の2種類があります。

| 種別 | 反映速度 | 用途 |
|:--|:--|:--|
| グローバル | 最大1時間 | リリース済みのコマンド |
| ギルド | 即時 | 開発・テスト中のコマンド |

開発中はギルドコマンドで即時反映を確認し、リリース時にグローバル登録へ切り替える運用が効率的です。

```python
# ギルドコマンドとして即時反映
GUILD_ID = discord.Object(id=int(os.environ["GUILD_ID"]))

@bot.event
async def on_ready():
    # 開発中: ギルドにコピーしてから同期（即時反映）
    bot.tree.copy_global_to(guild=GUILD_ID)
    await bot.tree.sync(guild=GUILD_ID)
    print(f"Synced to guild: {GUILD_ID}")
```

:::message alert
`GUILD_ID` は環境変数で管理し、ハードコードしないでください。Secret Manager や `.env` を使います。
:::

---

## 2. Cog 設計パターン

コマンド数が増えると、単一ファイルでの管理は破綻します。`commands.Cog` を継承したクラスにコマンドをグループ化し、ファイル分割することで保守性を確保できます。

### ディレクトリ構成

```
bot/
├── main.py               # エントリーポイント
├── cogs/
│   ├── __init__.py
│   ├── general.py        # /ping, /info など汎用コマンド
│   ├── search.py         # Autocomplete 付き検索コマンド
│   └── feedback.py       # Modal 付きフォームコマンド
├── Dockerfile
└── requirements.txt
```

### main.py ─ Cog の動的ロード

```python
# main.py
import os
import discord
from discord.ext import commands

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

GUILD_ID = discord.Object(id=int(os.environ["GUILD_ID"]))


async def load_cogs():
    """cogs/ ディレクトリの全 Cog を動的ロード"""
    for filename in os.listdir("./cogs"):
        if filename.endswith(".py") and not filename.startswith("_"):
            await bot.load_extension(f"cogs.{filename[:-3]}")
            print(f"Loaded: cogs.{filename[:-3]}")


@bot.event
async def on_ready():
    await load_cogs()
    bot.tree.copy_global_to(guild=GUILD_ID)
    await bot.tree.sync(guild=GUILD_ID)
    print(f"{bot.user} is ready.")


bot.run(os.environ["BOT_TOKEN"])
```

:::message
`load_cogs()` を `on_ready` 内で呼ぶと、再接続のたびに二重ロードされるリスクがあります。実装規模が大きい場合は `setup_hook` でロードする設計が安全です。
:::

### setup_hook を使う安全な実装

```python
# main.py（setup_hook パターン）
class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=discord.Intents.default())

    async def setup_hook(self):
        """接続前に1回だけ実行される初期化フック"""
        for filename in os.listdir("./cogs"):
            if filename.endswith(".py") and not filename.startswith("_"):
                await self.load_extension(f"cogs.{filename[:-3]}")

        guild = discord.Object(id=int(os.environ["GUILD_ID"]))
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)

    async def on_ready(self):
        print(f"{self.user} is ready.")


bot = MyBot()
bot.run(os.environ["BOT_TOKEN"])
```

---

## 3. 基本的なスラッシュコマンド（general.py）

```python
# cogs/general.py
import discord
from discord import app_commands
from discord.ext import commands
import platform


class GeneralCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="ping", description="Botの応答速度を確認します")
    async def ping(self, interaction: discord.Interaction):
        latency_ms = round(self.bot.latency * 1000)
        await interaction.response.send_message(
            f"🏓 Pong! レイテンシ: **{latency_ms}ms**",
            ephemeral=True,  # 実行者にのみ表示
        )

    @app_commands.command(name="info", description="Bot の情報を表示します")
    async def info(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="Bot 情報",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="discord.py", value=discord.__version__, inline=True)
        embed.add_field(name="Python", value=platform.python_version(), inline=True)
        embed.add_field(
            name="サーバー数",
            value=str(len(self.bot.guilds)),
            inline=True,
        )
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(GeneralCog(bot))
```

### ephemeral（エフェメラル）の使い分け

`ephemeral=True` を指定すると、コマンドの実行者にのみ応答が表示されます。チャンネルを汚さずに確認系の応答を返す際に便利です。

| 用途 | ephemeral 設定 |
|:--|:--|
| エラーメッセージ・警告 | `True` |
| 個人設定・秘密情報 | `True` |
| チャンネル全体への通知 | `False`（デフォルト） |

---

## 4. Autocomplete（オートコンプリート）の実装

`@app_commands.autocomplete` デコレータを使うと、コマンドの引数入力中にサジェストを表示できます。外部APIやデータベースを参照した動的なサジェストも実装可能です。

```python
# cogs/search.py
import discord
from discord import app_commands
from discord.ext import commands

# サンプルデータ（実際はDB/APIから取得）
LANGUAGES = [
    "Python", "TypeScript", "Go", "Rust", "Java",
    "C++", "Ruby", "Kotlin", "Swift", "Dart",
]


async def language_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    """入力中の文字列でフィルタリングして最大25件返す"""
    filtered = [lang for lang in LANGUAGES if current.lower() in lang.lower()]
    return [
        app_commands.Choice(name=lang, value=lang)
        for lang in filtered[:25]  # Discord API の上限は25件
    ]


class SearchCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="search",
        description="プログラミング言語に関する情報を検索します",
    )
    @app_commands.describe(language="検索するプログラミング言語")
    @app_commands.autocomplete(language=language_autocomplete)
    async def search(self, interaction: discord.Interaction, language: str):
        await interaction.response.defer()  # 処理に時間がかかる場合

        # 実際の処理（API呼び出し・DB検索など）
        embed = discord.Embed(
            title=f"{language} の情報",
            description=f"{language} に関する情報を取得しました。",
            color=discord.Color.green(),
        )
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(SearchCog(bot))
```

### defer() と followup の使い分け

Discord は、インタラクション受信後 **3秒以内 ** に応答しないとタイムアウトします。外部API呼び出しや重い処理がある場合は `defer()` を使って応答を延長（最大15分）してください。

```python
# 処理が3秒以内に終わる場合
await interaction.response.send_message("完了しました")

# 処理に3秒以上かかる場合
await interaction.response.defer()
# ... 重い処理 ...
await interaction.followup.send("完了しました")
```

---

## 5. Modal（モーダル）でフォーム入力を受け付ける

`discord.ui.Modal` を使うと、複数行のテキスト入力フォームをポップアップで表示できます。フィードバック収集・申請フォームなどに活用できます。

```python
# cogs/feedback.py
import discord
from discord import app_commands
from discord.ext import commands


class FeedbackModal(discord.ui.Modal, title="フィードバック送信"):
    """フィードバック入力フォーム"""

    subject = discord.ui.TextInput(
        label="件名",
        placeholder="件名を入力してください",
        max_length=100,
    )

    body = discord.ui.TextInput(
        label="本文",
        style=discord.TextStyle.paragraph,
        placeholder="詳細を入力してください（最大1000文字）",
        max_length=1000,
        required=True,
    )

    rating = discord.ui.TextInput(
        label="評価（1〜5）",
        placeholder="1〜5の数字を入力してください",
        max_length=1,
        required=False,
    )

    async def on_submit(self, interaction: discord.Interaction):
        # フィードバックを受け取った際の処理
        embed = discord.Embed(
            title="フィードバックを受け付けました",
            color=discord.Color.gold(),
        )
        embed.add_field(name="件名", value=self.subject.value, inline=False)
        embed.add_field(name="本文", value=self.body.value, inline=False)

        if self.rating.value:
            embed.add_field(name="評価", value=f"{'⭐' * int(self.rating.value)}", inline=True)

        embed.set_footer(text=f"送信者: {interaction.user.display_name}")

        await interaction.response.send_message(embed=embed, ephemeral=True)

        # 管理チャンネルに転送（実際の用途）
        # channel = interaction.guild.get_channel(ADMIN_CHANNEL_ID)
        # await channel.send(embed=embed)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        await interaction.response.send_message(
            "エラーが発生しました。しばらくしてから再試行してください。",
            ephemeral=True,
        )


class FeedbackCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="feedback",
        description="フィードバックを送信します",
    )
    async def feedback(self, interaction: discord.Interaction):
        modal = FeedbackModal()
        await interaction.response.send_modal(modal)


async def setup(bot: commands.Bot):
    await bot.add_cog(FeedbackCog(bot))
```

---

## 6. コマンドグループ（SubCommand）の実装

関連するコマンドを `/config set` `/config get` のようにグループ化できます。

```python
# app_commands.Group でサブコマンドを定義
class ConfigGroup(app_commands.Group, name="config", description="Bot の設定を管理します"):

    @app_commands.command(name="set", description="設定値を変更します")
    @app_commands.describe(key="設定キー", value="設定値")
    async def config_set(
        self,
        interaction: discord.Interaction,
        key: str,
        value: str,
    ):
        # 設定の保存処理
        await interaction.response.send_message(
            f"✅ `{key}` を `{value}` に設定しました。",
            ephemeral=True,
        )

    @app_commands.command(name="get", description="設定値を確認します")
    @app_commands.describe(key="確認する設定キー")
    async def config_get(self, interaction: discord.Interaction, key: str):
        # 設定の取得処理
        value = "（未設定）"
        await interaction.response.send_message(
            f"📋 `{key}`: `{value}`",
            ephemeral=True,
        )


class AdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.bot.tree.add_command(ConfigGroup())


async def setup(bot: commands.Bot):
    await bot.add_cog(AdminCog(bot))
```

---

## 7. エラーハンドリング

コマンド単位のエラーハンドラを Cog に定義できます。

```python
class GeneralCog(commands.Cog):
    # ... コマンド定義 ...

    @ping.error
    async def ping_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ):
        await interaction.response.send_message(
            f"エラーが発生しました: {error}",
            ephemeral=True,
        )
```

Bot 全体のエラーハンドラは `CommandTree.on_error` をオーバーライドして定義します。

```python
class MyTree(app_commands.CommandTree):
    async def on_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "このコマンドを実行する権限がありません。",
                ephemeral=True,
            )
        elif isinstance(error, app_commands.CommandOnCooldown):
            await interaction.response.send_message(
                f"クールダウン中です。{error.retry_after:.1f}秒後に再試行してください。",
                ephemeral=True,
            )
        else:
            # 想定外のエラーはロギング
            import logging
            logging.error(f"Unhandled error in {interaction.command}: {error}")
            await interaction.response.send_message(
                "予期しないエラーが発生しました。",
                ephemeral=True,
            )
```

---

## 8. Cloud Run での運用ポイント

WebSocket 型 Bot を Cloud Run で運用する際の注意点をまとめます。

### min-instances=1 の必須設定

WebSocket 型は常時接続を維持する必要があるため、Cloud Run のゼロスケールと相性が悪いです。`min-instances=1` を設定して常時起動を保証してください。

```yaml
# cloudbuild.yaml（デプロイ設定）
steps:
  - name: 'gcr.io/cloud-builders/docker'
    args: ['build', '-t', 'gcr.io/$PROJECT_ID/discord-bot', '.']
  - name: 'gcr.io/cloud-builders/docker'
    args: ['push', 'gcr.io/$PROJECT_ID/discord-bot']
  - name: 'gcr.io/google.com/cloudsdktool/cloud-sdk'
    entrypoint: gcloud
    args:
      - run
      - deploy
      - discord-bot
      - '--image=gcr.io/$PROJECT_ID/discord-bot'
      - '--min-instances=1'
      - '--max-instances=1'
      - '--memory=512Mi'
      - '--region=asia-northeast1'
      - '--no-allow-unauthenticated'
```

### Secret Manager でのトークン管理

```bash
# BOT_TOKEN を Secret Manager に登録
echo -n "YOUR_BOT_TOKEN" | gcloud secrets create discord-bot-token \
  --data-file=-

# Cloud Run サービスに Secret をマウント
gcloud run services update discord-bot \
  --set-secrets="BOT_TOKEN=discord-bot-token:latest" \
  --region=asia-northeast1
```

### Dockerfile

```dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "main.py"]
```

```text
# requirements.txt
discord.py>=2.3.0
```

### コマンド同期のタイミング制御

Cloud Run のコールドスタート後、WebSocket 接続が完了する前に `sync()` を呼び出すとエラーになる場合があります。`setup_hook` 内で確実に実行するか、`on_ready` でリトライロジックを実装してください。

```python
async def setup_hook(self):
    """Cloud Run 再起動時も安全に同期"""
    for filename in os.listdir("./cogs"):
        if filename.endswith(".py") and not filename.startswith("_"):
            await self.load_extension(f"cogs.{filename[:-3]}")

    guild = discord.Object(id=int(os.environ["GUILD_ID"]))
    self.tree.copy_global_to(guild=guild)

    try:
        synced = await self.tree.sync(guild=guild)
        print(f"Synced {len(synced)} commands.")
    except discord.HTTPException as e:
        print(f"Failed to sync commands: {e}")
```

---

## 9. 実装チェックリスト

コマンドが反映されない・動作しないときに確認すべき項目です。

- [ ] `tree.sync()` を呼んでいるか
- [ ] ギルドコマンドの場合、正しい `GUILD_ID` を指定しているか
- [ ] `setup` 関数で `await bot.add_cog(...)` を呼んでいるか
- [ ] `BOT_TOKEN` と `GUILD_ID` が環境変数として設定されているか
- [ ] Botに `applications.commands` スコープが付与されているか（OAuth2 設定確認）
- [ ] Cloud Run の場合、`min-instances=1` が設定されているか

---

## まとめ

discord.py v2.x のスラッシュコマンド実装で押さえるべきポイントを整理します。

| 概念 | ポイント |
|:--|:--|
| CommandTree | `setup_hook` で同期、開発中はギルドコマンドで即時確認 |
| Cog 設計 | ドメイン単位でファイル分割、`setup()` 関数で登録 |
| Autocomplete | 最大25件、インタラクション引数からリアルタイムフィルタリング |
| Modal | `ui.Modal` + `TextInput`、複数フィールドのフォーム入力に対応 |
| エラーハンドリング | コマンド単位 + `CommandTree.on_error` で全体カバー |
| Cloud Run 運用 | `min-instances=1` 必須、トークンは Secret Manager で管理 |

Cog 設計パターンを採用することで、コマンド追加・削除がファイル単位で完結し、チーム開発でのコンフリクトも最小化できます。まずは `general.py` 1ファイルからはじめて、機能が増えてきたら `search.py` `feedback.py` と分割していく進め方がスムーズです。

---

## 参考リンク

- [discord.py 公式ドキュメント](https://discordpy.readthedocs.io/en/stable/)
- [discord.app_commands リファレンス](https://discordpy.readthedocs.io/en/stable/interactions/api.html)
- [Discord Developer Portal](https://discord.com/developers/docs/interactions/application-commands)
