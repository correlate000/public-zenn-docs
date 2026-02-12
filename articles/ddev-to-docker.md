---
title: "DDEV使いがDockerを理解したら世界が変わった話"
emoji: "🐳"
type: "tech"
topics: ["docker", "ddev", "wordpress", "cloudrun", "開発環境"]
published: true
published_at: "2026-02-11 19:00"
---

## 「ddev start」しか打てなかった開発者の告白

WordPressの案件を受注するたびに、ターミナルで打つコマンドは決まっていました。

```bash
ddev start
```

これだけ。3分待てばNginx + PHP + MariaDBの環境が立ち上がり、ブラウザでWordPressの管理画面が開く。DDEVは本当に素晴らしいツールで、Docker Desktopがバックグラウンドで動いていることすら忘れさせてくれる。

ただ、一つ問題がありました。「Dockerって何？」と聞かれても、まともに答えられなかったのです。

## DDEVの便利さという「蓋」

DDEVを使い始めたきっかけは、WordPressのローカル開発環境が必要になったこと。以前はMAMPを使っていましたが、PHPバージョンの切り替えや複数サイトの同時運用で限界を感じていました。

DDEVに乗り換えてからは、`.ddev/config.yaml` に数行書くだけで環境が整う。

```yaml
name: client-site
type: wordpress
docroot: web
php_version: "8.2"
webserver_type: nginx-fpm
database:
  type: mariadb
  version: "10.4"
```

これで `ddev start` を打てば完了。PHPのバージョン変更も `php_version` を書き換えるだけ。複数のWordPressサイトを同時に起動しても、DDEVがポートの衝突を自動で解決してくれます。

便利すぎました。便利すぎて、その裏側で何が起きているのか、一度も考えなかった。

## 転機: Cloud Runとの出会い

転機は、自社の業務基盤を構築するときに訪れました。

freee APIと連携する経理自動化サーバー、Discord Botによる社内通知、BigQueryへのデータ同期。これらをGoogle Cloud Runで動かすことになった。Cloud RunはDockerコンテナをデプロイするサービスです。

「Dockerfileを書いてください」

この一言で、自分がDockerについて何も理解していないことに気づかされました。DDEVを1年以上使ってきたのに、Dockerfileの1行目すら書けない。毎日車を運転しているのにエンジンの仕組みを一切知らない。そんな状態だったのです。

## Docker理解の旅: DDEVの知識を足がかりに

ここからが本題。ゼロからDocker入門するのではなく、DDEVで培った「なんとなくの理解」を足がかりにしたことで、Dockerの本質が予想以上に早く見えてきました。

### Dockerfile = 環境の「設計図」

DDEVでは `.ddev/config.yaml` が設定ファイルでした。Dockerの世界では、これに相当するのがDockerfileです。

実際にCloud Run用に書いたDockerfileがこちら。

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py morning_briefing.py ./
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
```

たった6行。しかし、この6行にDockerの本質が詰まっています。

| 行 | 意味 | DDEVでの対応 |
|:--|:--|:--|
| `FROM python:3.11-slim` | ベースイメージの指定 | `php_version: "8.2"` |
| `WORKDIR /app` | 作業ディレクトリの指定 | `docroot: web` |
| `COPY requirements.txt .` | 依存関係ファイルのコピー | DDEVが自動でcomposer.jsonを認識 |
| `RUN pip install ...` | パッケージのインストール | DDEVが自動でcomposer installを実行 |
| `COPY main.py ...` | アプリケーションコードのコピー | DDEVがプロジェクトフォルダをマウント |
| `CMD [...]` | 起動コマンド | DDEVがNginx+PHP-FPMを自動起動 |

こう並べると、DDEVがどれだけ多くのことを「自動」でやってくれていたか分かる。DDEVユーザーが無意識に享受していた機能を、Dockerfileでは1行ずつ明示的に書く必要があるのです。

### イメージとコンテナ: 「レシピ」と「料理」

Docker理解で最初に混乱したのが「イメージ」と「コンテナ」の関係でした。

DDEVの世界では、この区別を意識する必要がありません。`ddev start` で全部まとめて起動してくれるから。

実際は以下の関係です。

```
Dockerfile（設計図）
    ↓ docker build
Docker Image（イメージ = レシピ）
    ↓ docker run
Container（コンテナ = 料理）
```

- イメージ。読み取り専用のテンプレート。何度でも同じコンテナを作れる
- コンテナ。イメージから作られた実行中のインスタンス。停止・削除しても、イメージから再作成可能

DDEVで `ddev start` を打つと、内部的にはDDEV用のイメージからコンテナが起動している。`ddev stop` はコンテナの停止、`ddev delete` はコンテナの削除に相当します。

### docker-compose = 複数コンテナのオーケストレーション

WordPressの開発環境には、Webサーバー、PHP、データベースの3つが必要です。DDEVはこれを `ddev start` 一発で立ち上げてくれますが、裏ではdocker-composeが動いています。

docker-composeは、複数のコンテナをまとめて定義・管理するツール。DDEVが生成するdocker-composeファイルを覗いてみると、その実態が見えてきます。

:::message
DDEVのdocker-composeファイルは `.ddev/.ddev-docker-compose-base.yaml` に自動生成されます。このファイルはDDEV専用で、`ddev start` のたびに上書きされるため編集禁止です。カスタム設定が必要な場合は `.ddev/docker-compose.custom.yaml` を作成してください。
:::

## DDEVの裏側を解き明かす

Dockerを理解してから、DDEVの `.ddev` フォルダを改めて見てみました。

```
.ddev/
├── config.yaml                     ← あなたが書く設定（数行）
├── .ddev-docker-compose-base.yaml  ← DDEVが自動生成（編集禁止）
├── .ddev-docker-compose-full.yaml  ← 最終的なcompose設定
├── docker-compose.custom.yaml      ← カスタム追加（任意）
└── ...
```

`config.yaml` のたった数行の設定から、DDEVは完全なdocker-compose設定を自動生成していたのです。Nginx、PHP-FPM、MariaDB、ddev-router（Traefik）の構成が全て含まれています。

つまり、DDEVは「docker-composeの高レベルラッパー」だったのです。

:::message
DDEVのルーター（ddev-router）にはTraefik Proxyが使われています。これにより、複数のDDEVプロジェクトを同時に起動しても、ポート80/443を共有しつつ、ホスト名ベースで正しいプロジェクトにリクエストを振り分けられます。
:::

## DDEVとDocker直接利用の使い分け

Dockerを理解した今、両者の使い分けが明確になりました。

| 用途 | ツール | 理由 |
|:--|:--|:--|
| WordPress/PHP案件 | DDEV | 3分で環境構築、PHP/DB設定が簡単、マルチサイト対応 |
| カスタムAPIサーバー | Docker直接 | Dockerfile/docker-composeで完全制御、Cloud Runデプロイ |
| Python/FastAPI | Docker直接 | DDEVはPHP/Node.js向け、Python環境は自分で構築 |
| 本番デプロイ | Docker直接 | Cloud Run/ECS等のコンテナサービスはDockerイメージを要求 |

DDEVはWordPress開発の最適解であり続ける。ただし、DDEVの外の世界（Cloud Run、AWS ECS、Kubernetes等）に出るときは、Dockerの直接操作が必須になります。

## Before / After

| | Before | After |
|:--|:--|:--|
| Dockerの理解 | 「なんか裏で動いてるやつ」 | イメージ・コンテナ・Dockerfileの関係を説明できる |
| Dockerfileを書ける？ | 書けない | 6行のDockerfileでCloud Runにデプロイ |
| DDEVの理解 | 「ddev startで動く魔法のツール」 | docker-composeのラッパーだと理解 |
| DDEVのカスタマイズ | config.yamlの基本設定のみ | docker-compose.custom.yamlで拡張可能と知っている |
| コンテナの活用範囲 | WordPress開発のみ | Cloud Run, Discord Bot, APIサーバーにも展開 |
| トラブル対応 | `ddev restart` を祈るように実行 | `docker logs` でコンテナのログを確認して原因特定 |

一番大きかったのは、トラブル対応の変化。以前は「ddev restart」で直らなければお手上げでした。今は `docker ps` でコンテナの状態を確認し、`docker logs` で原因を特定できる。DDEVのトラブルも、Dockerレベルで調査できるようになりました。

## まとめ: DDEVは入口、Dockerは本質

DDEVは素晴らしい入口です。「ddev start」だけでWordPressの開発環境が立ち上がる体験は、開発者の参入障壁を大きく下げてくれる。

でも、いつかDDEVの外に出る日が来る。Cloud Runでサービスを動かしたい、独自のコンテナ環境を作りたい、チームの開発基盤を整備したい。そのときにDockerの基礎を知っているかどうかで、選択肢の幅が全く違ってきます。

もしあなたが今DDEVだけを使っていて、「Dockerってよく分からない」と感じているなら、こんな一歩から始めてみてください。

1. `.ddev/.ddev-docker-compose-base.yaml` を開いて中身を読んでみる
2. `docker ps` を打って、DDEVが裏で何個のコンテナを動かしているか確認する
3. 簡単なDockerfile（Hello Worldレベル）を書いてビルドしてみる

DDEVが「魔法」から「仕組みの分かるツール」に変わる瞬間が、きっと訪れるでしょう。

## 参考資料

https://ddev.com/get-started/

https://ddev.com/blog/ddev-docker-architecture/

https://docs.docker.com/reference/dockerfile/

https://cloud.google.com/run/docs/quickstarts/build-and-deploy/deploy-python-fastapi-service

https://zenn.dev/correlate/articles/solo-corp-gcp

https://fastapi.tiangolo.com/ja/deployment/docker/

https://zenn.dev/0tofu/articles/c90f5fe46c6720
