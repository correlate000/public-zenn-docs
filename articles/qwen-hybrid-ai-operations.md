---
title: "データレイク設計のアンチパターン：「沼」に落ちないための10の教訓"
emoji: "🏞"
type: "tech"
topics: ["datalake", "dataengineering", "bigquery", "spark", "apacheiceberg"]
published: false
publication_name: "correlate_dev"
---

# データレイク設計のアンチパターン：「沼」に落ちないための10の教訓

---

## なぜデータレイクは「沼」になるのか

```mermaid
stateDiagram-v2
    [*] --> 設計・ガバナンス欠如

    設計・ガバナンス欠如 --> データ無秩序蓄積 : データ流入開始\n（スキーマ未定義・命名規則なし）

    データ無秩序蓄積 --> メタデータ不整備 : ドキュメント化省略\nカタログ未整備

    メタデータ不整備 --> 発見困難 : データ所在不明\n検索不能

    発見困難 --> 信頼喪失 : データ品質不明\n鮮度・正確性への疑念

    信頼喪失 --> データスワンプ : 利用者離脱\n負債の蓄積

    データスワンプ --> [*]

    state 設計・ガバナンス欠如 {
        [*] --> スキーマ未定義
        スキーマ未定義 --> アクセス制御なし
        アクセス制御なし --> 責任者不在
    }

    state データ無秩序蓄積 {
        [*] --> 重複データ混入
        重複データ混入 --> 生データ放置
    }

    state データスワンプ {
        [*] --> 誰も使わない沼
        誰も使わない沼 --> コスト増大
        コスト増大 --> プロジェクト廃棄
    }
```

### データレイクとデータスワンプの境界線

「データを一か所に集めれば、分析の民主化が実現できる」——そう信じてデータレイクを構築したものの、1年後には誰もそのデータを使いこなせなくなっていた。そんな経験をしたエンジニアは、決して少なくありません。

データレイク（Data Lake）とは、構造化・半構造化・非構造化を問わず、あらゆるデータを生の形式でまとめて蓄積するストレージアーキテクチャです。RDBのように事前にスキーマを定義する必要がなく、「とりあえず全部貯めておいて、使うときに解釈する」という柔軟性が最大の売りです。

しかしその柔軟性は、ガバナンスなき運用のもとでは呪いに変わります。データが積み上がるにつれ、「どこに何があるかわからない」「このデータは信頼できるのか」「クエリを投げたら何時間も返ってこない」という状況が生まれ、データレイクはやがて **データスワンプ（Data Swamp：データの沼）** へと転落します。

データレイクとデータスワンプの境界線は、技術的な複雑さにあるのではありません。 **ガバナンス、設計規律、そして組織的な意思決定の有無** にあります。まったく同じツールスタックを使っていても、一方は価値を生み出すデータ基盤になり、もう一方は誰も近づかない負債の山になる。その違いを生み出すのが、本記事で解説する「アンチパターン」です。

### アンチパターンが生まれる3つの根本原因

```mermaid
flowchart TD
    A[データレイク設計のアンチパターン] --> B[根本原因①\n速度優先\nSpeed over Structure]
    A --> C[根本原因②\nガバナンス後回し\nGovernance as Afterthought]
    A --> D[根本原因③\nスケール無視\nIgnoring Scale]

    B --> B1[設計省略のまま\nデータ投入開始]
    B1 --> B2[スキーマ不整合の蓄積]
    B1 --> B3[依存関係の複雑化]
    B2 --> F1[修正コストの\n加速度的増大]
    B3 --> F1

    C --> C1[命名規則・分類の\nルール不在]
    C --> C2[データ品質チェック\nの欠如]
    C1 --> F2[どこに何があるか\nわからない状態]
    C2 --> F3[データ信頼性の\n喪失]
    F2 --> F4[誰もデータを\n使わなくなる]
    F3 --> F4

    D --> D1[小規模前提の\nアーキテクチャ設計]
    D --> D2[パーティション・\nインデックス未整備]
    D1 --> F5[データ量増加による\n性能劣化]
    D2 --> F5
    F5 --> F6[クエリが何時間も\n返ってこない]

    F1 --> G[データスワンプ\nData Swamp]
    F4 --> G
    F6 --> G

    G --> G1[分析の民主化\n失敗]
    G --> G2[データ基盤への\n不信感蔓延]
    G --> G3[技術的負債の\n肥大化]

    style A fill:#4a4a8a,color:#fff
    style B fill:#c0392b,color:#fff
    style C fill:#e67e22,color:#fff
    style D fill:#8e44ad,color:#fff
    style G fill:#2c3e50,color:#fff
    style G1 fill:#7f8c8d,color:#fff
    style G2 fill:#7f8c8d,color:#fff
    style G3 fill:#7f8c8d,color:#fff
    style F1 fill:#e74c3c,color:#fff
    style F4 fill:#e74c3c,color:#fff
    style F6 fill:#e74c3c,color:#fff
```

筆者がこれまで見てきたデータレイクの失敗事例を振り返ると、その根本原因はほぼ例外なく以下の3つに集約されます。

**① 急いで作る（Speed over Structure）**
「まず動くものを作れ」というプレッシャーのもと、設計を省略してデータを流し込み始める。初速は出るが、後から修正するコストがデータ量・依存関係の増加に伴い加速度的に増大する。

**② ガバナンスを後回しにする（Governance as an afterthought）**
「ガバナンスは安定したら考えよう」という先送りが常態化する。しかし安定するころには、ガバナンスなき慣習が組織に深く根付いてしまっている。

**③ スケールを想定しない（Ignoring scale from day one）**
100GBのデータで動いた設計が、100TBでは壊滅的に機能しなくなる。データ量・ユーザー数・クエリ頻度の成長を設計段階で織り込まなかった結果、後から根本的な作り直しを迫られる。

### この記事の読み方・対象読者

本記事はデータエンジニア・データアーキテクトを主な読者として想定しています。データ基盤を所管するエンジニアリングマネージャーやテックリード、あるいはデータ基盤を利用する立場のデータサイエンティストにとっても、自分たちの環境を診断するうえで有益な視点を提供できます。

各アンチパターンは「症状 → 根本原因 → 処方箋」の構成で解説します。「うちのデータレイク、なんか遅い」「このデータ、誰も信用していない」といった **症状から逆引き** して、自社の問題と照合する読み方をお勧めします。

記事後半には診断チェックリストを用意しています。読後すぐに自社環境の健康状態を評価してみてください。

---

## アンチパターン1 ── データカタログなきデータレイク

```mermaid
flowchart TD
    A([データカタログ不在]) --> B{症状}

    B --> S1[データ探索困難\nどこに何があるかわからない]
    B --> S2[KPI不整合\nチームごとに数値が食い違う]
    B --> S3[データ信頼喪失\n誰もデータを使わなくなる]

    S1 --> R1[根本原因①\nメタデータが存在しない\nまたは散在している]
    S2 --> R2[根本原因②\n定義・計算ロジックが\n文書化されていない]
    S3 --> R3[根本原因③\nデータ品質・鮮度が\n不透明]

    R1 --> P1[処方箋①\n統合メタデータ管理基盤の導入\n例: Apache Atlas / DataHub]
    R2 --> P2[処方箋②\nビジネス用語集・KPI定義書の整備\nと一元管理]
    R3 --> P3[処方箋③\nデータ品質スコア・\nリネージ情報の可視化]

    P1 --> G([データカタログ確立])
    P2 --> G
    P3 --> G

    G --> OUT[データ探索の迅速化\nKPI整合性の確保\nデータ信頼の回復]

    style A fill:#e74c3c,color:#fff,stroke:#c0392b
    style B fill:#e67e22,color:#fff,stroke:#d35400
    style S1 fill:#f39c12,color:#fff,stroke:#e67e22
    style S2 fill:#f39c12,color:#fff,stroke:#e67e22
    style S3 fill:#f39c12,color:#fff,stroke:#e67e22
    style R1 fill:#8e44ad,color:#fff,stroke:#7d3c98
    style R2 fill:#8e44ad,color:#fff,stroke:#7d3c98
    style R3 fill:#8e44ad,color:#fff,stroke:#7d3c98
    style P1 fill:#2980b9,color:#fff,stroke:#1a6fa0
    style P2 fill:#2980b9,color:#fff,stroke:#1a6fa0
    style P3 fill:#2980b9,color:#fff,stroke:#1a6fa0
    style G fill:#27ae60,color:#fff,stroke:#1e8449
    style OUT fill:#1abc9c,color:#fff,stroke:#17a589
```

> **影響度: High ／ 発生頻度: 非常に多い**

### 症状：「あのデータ、どこにあるか知ってる？」が日常になる

`s3://data-lake-prod/` 以下に数千ものフォルダが並んでいるのに、どのフォルダに何が入っているか誰も把握していない。新しいデータサイエンティストがジョインするたびに「使えるデータをリストアップしてほしい」という依頼がSlackに飛んでくる。月に何時間も「データ探し」に費やされている——これがデータカタログ不在のデータレイクで起きることです。

データ発見の困難さは、単なる不便にとどまりません。同じデータを別々のチームが独自に再加工し始め、微妙に異なる複数の「真実」が組織内に乱立します。やがて「このKPIとあのKPIの数値が合わない」という問題が経営会議で発覚し、データへの信頼が根底から崩れます。

### 根本原因：メタデータ管理を「後でやる」にした

データカタログの整備は、目に見える価値を即座に生まない地味な作業です。「まずデータを流すパイプラインを作ろう」「カタログは後でまとめてやろう」という判断は、短期的には合理的に見えます。しかし後でまとめてやろうとしたとき、すでにデータセットは数百に膨れ上がり、各データセットの文脈を知っているメンバーは半分異動していたりします。メタデータは、データと同時に生成されない限り、永遠に後回しになります。

### 処方箋：Data Catalogの導入戦略とDataHub/Glueの使い分け

```mermaid
sequenceDiagram
    actor Dev as 開発者
    participant Git as Gitリポジトリ
    participant CI as CIサーバー
    participant Cat as データカタログ
    participant Rev as レビュアー
    participant CD as CDパイプライン
    participant Lake as データレイク

    Dev->>Git: コード変更をプッシュ
    Git->>CI: プッシュイベント通知

    activate CI
    CI->>CI: ユニットテスト実行
    CI->>CI: データ品質チェック
    CI->>CI: スキーマバリデーション
    CI-->>Dev: テスト結果通知
    deactivate CI

    CI->>Cat: メタデータ・スキーマ登録
    activate Cat
    Cat->>Cat: データセット情報更新
    Cat->>Cat: リネージ情報記録
    Cat-->>CI: 登録完了
    deactivate Cat

    Cat->>Rev: 承認依頼通知
    activate Rev
    Rev->>Cat: メタデータ内容確認
    Rev->>Rev: データ品質レビュー
    alt 承認
        Rev-->>Cat: 承認
        Cat-->>CD: デプロイ許可通知
    else 却下
        Rev-->>Cat: 差し戻し
        Cat-->>Dev: 修正依頼通知
    end
    deactivate Rev

    activate CD
    CD->>Lake: パイプラインデプロイ
    Lake-->>CD: デプロイ完了
    CD-->>Dev: デプロイ完了通知
    deactivate CD
```

処方箋は明確です。 **データの取り込みと同時にメタデータを登録する仕組みを作ること** 、そして **カタログの整備をエンジニアの義務にすること** です。

ツールの選択肢としては、以下のものが代表的です。

- **AWS Glue Data Catalog** ：AWSエコシステム内で完結するシンプルなカタログ。Athena・EMR・Glue ETLとの統合が強い。
- **Apache Atlas** ：オープンソースのメタデータ・ガバナンスフレームワーク。ただし2023年以降は開発活動が著しく低下しており、新規導入には適さない。HDP/CDPなどのレガシーHadoop環境との統合が既存要件として存在する場合に限り選択肢となるが、それ以外の場合はDataHubまたはOpenMetadataを推奨する。
- **DataHub** （LinkedIn発OSS）：リネージュ（データの来歴）まで含めた包括的なメタデータ管理が可能。モダンなデータスタックとの統合が豊富。新規導入の第一候補。
- **OpenMetadata** ：DataHubと並ぶモダンなOSSカタログ。APIファーストな設計で拡張性が高く、新規導入に適した選択肢の一つ。
- **Unity Catalog** （Databricks）：Delta Lakeを使う環境でのガバナンスを一元管理。列レベルのアクセス制御・行フィルタリングといった細粒度のセキュリティ機能を備える。なお2024年にOSS化されており（[unity-catalog on GitHub](https://github.com/unitycatalog/unitycatalog)）、Databricksライセンスなしでも利用可能になりつつある点も注目に値する。

ツール選択より重要なのは **運用の仕組み化** です。データパイプラインのコードとカタログ登録を同一のCI/CDプロセスに組み込み、登録なしではデプロイできない状態にすることが、カタログを形骸化させないための最も有効な手段です。

---

## アンチパターン2 ── 小ファイル爆増問題

```mermaid
flowchart TD
    A[小ファイル問題の発生] --> B{パターンの分類}

    B --> C[パターン①\nストリーミング書き込みの誤用]
    B --> D[パターン②\n過剰なパーティション分割]

    C --> C1[リアルタイムデータを\n逐次ファイルとして書き込む]
    C1 --> C2[イベント発生ごとに\n新規ファイルが生成される]
    C2 --> C3[短時間で大量の\n小ファイルが蓄積]
    C3 --> C4[メタデータ管理の\nオーバーヘッド増大]
    C4 --> C5[クエリ実行時に\nファイルオープンが多発]
    C5 --> C6[クエリ性能の\n著しい低下]

    D --> D1[カーディナリティの高い列で\nパーティションを設定する]
    D1 --> D2[パーティションキーの\n組み合わせが爆発的に増加]
    D2 --> D3[各パーティションへの\nデータ書き込みが分散]
    D3 --> D4[パーティションごとの\nファイルサイズが極小化]
    D4 --> D5[ディレクトリ・ファイル数が\n管理限界を超える]
    D5 --> D6[クエリ性能の\n著しい低下]

    C6 --> E[共通の問題\nNameNodeへの負荷集中\n／ストレージ非効率]
    D6 --> E

    style A fill:#ff6b6b,color:#fff
    style B fill:#ffa94d,color:#fff
    style C fill:#4dabf7,color:#fff
    style D fill:#69db7c,color:#333
    style C6 fill:#cc5de8,color:#fff
    style D6 fill:#cc5de8,color:#fff
    style E fill:#f03e3e,color:#fff
```

> **影響度: High ／ 発生頻度: 非常に多い**

### 症状：クエリが異常に遅い、Sparkジョブがタスク地獄に

特定のパーティション配下に100万個のファイルが存在し、1ファイルあたりの平均サイズが数KB——このような状況に気づいたとき、すでに問題は相当深刻です。Apache Sparkでこのデータを読もうとすると、HDFSのNameNodeあるいはAWS S3のAPI呼び出しが爆発的に増加し、タスクのオーバーヘッドがデータ処理本体を超えてしまいます。クエリエンジン（Trino/Prestoなど）でも同様で、ファイルのオープン・クローズだけで処理時間の大半が消費されます。

### 根本原因：ストリーミング書き込み・細粒度パーティションの誤用

小ファイル問題が生まれる典型的なパターンは2つあります。

1つ目は **ストリーミング書き込みの誤用** です。Apache KafkaやKinesis等からリアルタイムでデータをS3に書き込む際、数秒〜数分おきにマイクロバッチとして書き出すと、あっという間に大量の小ファイルが蓄積されます。

2つ目は **過剰なパーティション分割** です。`year/month/day/hour/minute` と時刻で細かくパーティションを切り、さらにそこに小さなファイルを書き込むと、ディレクトリ数とファイル数が乗算的に増加します。

### 処方箋：Compaction戦略とIcebergによるファイル管理自動化

```mermaid
sequenceDiagram
    participant S as スケジューラ<br/>(Airflow/Step Functions)
    participant J as Sparkジョブ
    participant I as Icebergライブラリ
    participant ST as ストレージ<br/>(S3/HDFS)

    loop 定期実行サイクル
        S->>S: スケジュール起動<br/>(Cron/EventBridge)
        S->>J: Compactionジョブ起動
        activate J

        J->>I: rewrite_data_files() 呼び出し
        activate I

        I->>ST: 小さいファイル一覧取得
        ST-->>I: 対象ファイルリスト返却

        I->>ST: ファイル読み込み & 統合処理
        ST-->>I: データ読み込み完了

        I->>ST: 統合済み大ファイル書き込み
        ST-->>I: 書き込み完了

        I->>ST: メタデータ更新<br/>(旧ファイル → 新ファイル)
        ST-->>I: メタデータ更新完了

        I-->>J: ファイル統合完了<br/>(統合ファイル数・サイズ報告)
        deactivate I

        J-->>S: ジョブ正常終了
        deactivate J

        S->>S: 次回スケジュール登録
    end
```

根本的な解決策は **Compaction（コンパクション：小さなファイルを大きなファイルに統合する処理）** です。

Apache Icebergを使用している場合、以下のようなCompaction処理を定期的に実行することで、ファイル数を適切な水準に保てます。

> **注記**: 以下のコードはApache Iceberg 1.4.x時点の `rewrite_data_files` プロシージャの挙動に基づきます。バージョンによって仕様が変わる可能性があるため、実際の導入時は使用バージョンのリリースノートを必ず確認してください。また、SparkSession設定は一部抜粋です。完全な設定については後述の補足を参照してください。

```python
# Apache Iceberg 1.4.x
from pyspark.sql import SparkSession

spark = SparkSession.builder \
    .appName("IcebergCompaction") \
    .config("spark.sql.extensions",
            "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions") \
    .config("spark.sql.catalog.glue_catalog",
            "org.apache.iceberg.spark.SparkCatalog") \
    .config("spark.sql.catalog.glue_catalog.type",
            "glue") \
    .getOrCreate()

# ターゲットファイルサイズを128MBに設定してCompactionを実行
# rewrite_data_files: 小さなファイルを結合して最適なサイズに再編成する
spark.sql("""
    CALL glue_catalog.system.rewrite_data_files(
        table => 'my_database.my_table',
        options => map(
            'target-file-size-bytes', '134217728',  -- 目標出力ファイルサイズ: 128MB
            'min-file-size-bytes',    '33554432',   -- この値未満のファイルをCompaction対象とする: 32MB
            'max-file-size-bytes',    '268435456'   -- この値を超えるファイルも分割対象とする: 256MB
        )
    )
""")
```

このCompactionジョブをAirflowやAWS Step Functionsで定期実行（例：日次深夜バッチ）することで、ファイル数の爆増を継続的に抑制できます。Delta Lakeの場合は `OPTIMIZE` コマンド、Apache Hudiでは `HoodieSparkClient` のcleaningとclusteringが対応する機能です。

また、ストリーミング取り込みの段階での小ファイル生成を抑える設計も重要です。ただし、バッファリング時間を延ばすアプローチはユースケースによってトレードオフがあるため、以下のように使い分けることを推奨します。

- **Apache Kafka → S3 の構成** ：Kafka Connect の S3 Sink Connector を使用し、`flush.size`（レコード数によるフラッシュ閾値）や `rotate.interval.ms`（時間によるローテーション間隔）でバッファリングを制御するのが主要な対策です。
- **Spark Structured Streaming の構成** ：`trigger(processingTime='1 hour')` でバッチ間隔を広げる方法もありますが、より現代的なアプローチとして `trigger(availableNow=True)` を使い、溜まったデータをバッチ的に処理する設計パターンも有効です。
- **リアルタイム性要件がある場合** ：バッファ時間を長くすることは遅延許容度との兼ね合いになります。厳格なリアルタイム性が求められるシステムでは、Compactionによる後処理を前提とした設計が現実的な落とし所となります。
