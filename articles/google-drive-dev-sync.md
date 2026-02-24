---
title: "Google Driveで開発フォルダを同期してはいけない理由（fileproviderd 167GB書き込み事件）"
emoji: "💾"
type: "tech"
topics: ["googledrive", "macos", "syncthing", "rsync", "開発環境"]
published: false
publication_name: "correlate_dev"
---

## はじめに - 何が起きたのか

新しいMac mini M4 Proが手元に届いた日、筆者はいつもと同じように環境移行を始めました。既存のMacBook ProからGoogle Drive File Stream経由で開発フォルダを移そうとして、`cp -R`コマンドを実行しました。その後、アクティビティモニタを眺めていると、見慣れないプロセスが異常な勢いでディスクに書き込みを続けていることに気づきました。

プロセス名は `fileproviderd`。ディスク書き込みの累計は止まる気配がなく、最終的に **167GB（アクティビティモニタの「書き込まれたバイト数」列の表示値）に到達** しました。

> **計測方法について**: アクティビティモニタ → 「ディスク」タブ → 「書き込まれたバイト数」列で確認しました。対象フォルダは複数のNode.jsプロジェクトを含む `~/dev/`（`node_modules` を含む状態）で、合計ファイル数はおよそ数十万件規模でした。なお、アクティビティモニタの表示は「GB」と表記されますが、実際の換算方式（SI単位系か2進数単位系か）はmacOSのバージョンによって異なる場合があります。

移行しようとした開発フォルダの実際のサイズは **10.3GiB** です。実データの16倍以上のディスク書き込みが、静かに、しかし着実に発生していたわけです。

この記事では、なぜこのようなことが起きるのか、fileproviderdのメカニズムを交えながら解説し、開発フォルダを安全に複数のMac間で同期するための現実的な代替手段を紹介します。Google Driveは書類管理には優れたツールです。ただ、開発フォルダとの相性は、構造的に良くありません。

---

## なぜこうなるのか - fileproviderdの仕組み

```mermaid
graph TB
    subgraph クラウドストレージサービス
        iCloud[iCloud Drive]
        GDrive[Google Drive]
        OneDrive[OneDrive]
        Dropbox[Dropbox]
    end

    subgraph File_Provider_Extension_Framework
        FPE[NSFileProviderExtension\nFile Provider Extension フレームワーク]
    end

    subgraph macOS_System
        fpd[fileproviderd\nシステムデーモン]
    end

    subgraph macOS_UI
        Finder[Finder\nmacOS ファイルマネージャ]
        Apps[その他のアプリケーション]
    end

    iCloud -- 拡張機能API --> FPE
    GDrive -- 拡張機能API --> FPE
    OneDrive -- 拡張機能API --> FPE
    Dropbox -- 拡張機能API --> FPE

    FPE -- 統合管理 --> fpd

    fpd -- ファイルシステム提供 --> Finder
    fpd -- ファイルシステム提供 --> Apps
```

### fileproviderdとは

`fileproviderd` はmacOSに標準搭載されているシステムデーモンです。iCloud Drive、Google Drive、OneDrive、Dropboxといった、さまざまなクラウドストレージサービスをmacOS上で統合管理する役割を担っています。

具体的には、Apple の **File Provider Extension フレームワーク（NSFileProviderExtension）** を通じて動作します。各クラウドサービスのアプリがこのフレームワークに対応することで、Finderとの統合、オンデマンドダウンロード、ファイルの変更検知といった機能が実現されています。

macOS Ventura以降（とくにSonoma以降）でその仕様が大きく変更され、Google Driveなどのサードパーティ製クラウドサービスもこのFile Providerフレームワークへの移行が進んでいます。この変更に伴い、Apple Community や Google Drive サポートフォーラム（[例: Google Drive Community](https://support.google.com/drive/threads)）では `fileproviderd` のCPU使用率や異常なディスク書き込みに関するスレッドが多数報告されるようになっています。

### 開発フォルダが地雷になる理由

通常のドキュメントフォルダをクラウドで同期する場合、ファイル数はせいぜい数百〜数千程度です。しかし開発フォルダには、クラウド同期サービスにとって本質的に相性が悪い要素が含まれています。

**`node_modules`** は、その代表格です。小〜中規模のNode.jsプロジェクトでも、`node_modules` 以下のファイル数は数万に及ぶことは珍しくありません。それぞれが独立したファイルとして扱われるため、1つのプロジェクトを同期しようとするだけで、fileproviderdのキューに数万件の処理が積み上がります。

**`.git`** ディレクトリも同様です。Gitはファイルの差分をオブジェクトファイルとして `.git/objects/` 以下に格納します。コミット履歴が積み重なったリポジトリでは、この中のファイル数が数千〜数万になります。さらに、`git commit` や `git fetch` のたびにこれらのファイルが更新・追加されるため、fileproviderdは継続的に変更を検知し続けます。

**`vendor`** （PHPプロジェクトのComposer依存パッケージ格納先）や **`.venv`** （Pythonの仮想環境）なども同じ理由で問題になります。

これらの特徴をまとめると、次のようになります。なお、以下のファイル数の目安は、リポジトリ歴2〜3年相当の中規模プロジェクトを想定した参考値です。

| フォルダ | ファイル数の目安 | 更新頻度 |
|---|---|---|
| `node_modules` | 数万〜十数万 | `npm install` のたび |
| `.git/objects` | 数千〜数万 | コミット・フェッチのたび |
| `vendor` | 数千〜数万 | `composer install` のたび |
| `.venv` | 数千 | パッケージ追加のたび |

### なぜ167GBになったのか（推定メカニズム）

```mermaid
sequenceDiagram
    actor User as ユーザー
    participant Shell as シェル
    participant FS as ファイルシステム
    participant FPD as fileproviderd
    participant TMP as 一時ファイル領域
    participant API as クラウドAPI<br/>(Google Drive)
    participant Log as レスポンス記録

    User->>Shell: cp -R ~/dev/ /Volumes/GoogleDrive/dev/
    Shell->>FS: ファイル書き込み開始<br/>（実データ 10.3 GiB）

    loop ファイルごとに繰り返し（数十万件）
        FS->>FPD: ファイル変更イベント通知<br/>（FSEvents / File Provider API）

        activate FPD
        FPD->>FPD: メタデータ解析<br/>（ファイル種別・属性・ハッシュ計算）
        FPD->>TMP: 一時ファイル書き込み<br/>（チャンク分割・エンコード）
        TMP-->>FPD: 書き込み完了

        FPD->>API: クラウドAPIリクエスト<br/>（アップロード / メタデータ同期）

        alt 正常レスポンス
            API-->>FPD: 200 OK<br/>（ファイルID・バージョン情報）
            FPD->>Log: レスポンス内容を記録
            FPD->>TMP: 一時ファイル削除 / 更新
        else エラー / タイムアウト
            API-->>FPD: エラーレスポンス<br/>（429 / 500 / timeout）
            FPD->>Log: エラー内容を記録
            FPD->>FPD: リトライ待機<br/>（exponential backoff）
            FPD->>TMP: 再エンコード・再書き込み
            FPD->>API: リトライリクエスト送信
        end
        deactivate FPD
    end

    Note over FPD,TMP: メタデータDB更新・journal書き込み・<br/>チャンク一時領域が累積し<br/>ディスク書き込みが増幅

    Note over Shell,Log: 実データ 10.3 GiB に対して<br/>ディスク書き込み累計 167 GB 到達<br/>（約16倍以上の書き込み増幅）
```

> **注意**: 以下はAppleの公式ドキュメントおよび著者の観測をもとにした推定です。macOS内部の動作はAppleが公開していない部分を含むため、あくまで参考として捉えてください。

実データ10.3GiBに対して167GBのディスク書き込みが発生した理由は、以下のメカニズムが複合的に作用したものと推定されます。

まず、fileproviderdはファイルごとにメタデータ（更新日時、サイズ、クラウド側との差分情報など）を処理します。数万のファイルがキューに積み上がると、このメタデータ処理だけでも相当量の一時ファイルやインデックスの書き込みが発生します。

次に、クラウドストレージAPIとの往復通信のオーバーヘッドがあります。ファイルを1件アップロードするたびに、APIリクエスト・レスポンスの処理がローカルに記録されます。さらに、一部のファイルで同期エラーや競合が発生した場合、リトライが繰り返されます。これが大量のファイル数と掛け合わさることで、実サイズをはるかに超える書き込みが発生します。

重要なのは、 **167GBはネットワーク転送量ではなく、ローカルディスクへの書き込み量** である点です。SSDの書き込み寿命（TBW）を消費するという意味でも、無視できない問題です。

---

## 問題のある同期パターン3選

```mermaid
flowchart TD
    Start([開発フォルダをGoogle Driveで同期しようとする]) --> Q1{配置場所は？}

    Q1 -->|Google Drive直下に配置| P1[パターン1\nGoogle Drive直下に開発フォルダ配置]
    Q1 -->|ローカルからコピー| Q2{node_modulesを\n含んでいる？}
    Q1 -->|既存リポジトリをコピー| Q3{.gitディレクトリを\n含んでいる？}

    P1 --> P1D[全ファイルがGoogle Driveの\n監視対象になる]
    P1D --> P1E[数十万件規模のファイルを\nfileproviderdが逐一処理]

    Q2 -->|含んだままコピー| P2[パターン2\nnode_modulesを含んだままコピー]
    Q2 -->|除外してコピー| Safe2[安全\nfileproviderdへの負荷なし]

    P2 --> P2D[node_modules内の\n大量の小ファイルが同期対象になる]
    P2D --> P2E[依存パッケージのファイル数が\n爆発的に増加\n例：数十万ファイル]

    Q3 -->|含んだまま同期| P3[パターン3\n.gitを含んだまま同期]
    Q3 -->|除外して同期| Safe3[安全\nfileproviderdへの負荷なし]

    P3 --> P3D[.git/objects内の\nバイナリ差分ファイルが\n大量に生成・更新される]
    P3D --> P3E[コミットやブランチ操作のたびに\nfileproviderdが大量書き込みを検知]

    P1E --> Overload
    P2E --> Overload
    P3E --> Overload

    Overload{fileproviderdが\n過負荷状態に陥る}

    Overload --> R1[ファイルのメタデータ処理が\n大量キューに積まれる]
    R1 --> R2[クラウド同期のための\n書き込みが連鎖的に発生]
    R2 --> R3[実データ10GiB超に対し\nディスク書き込みが167GBに到達]
    R3 --> End([Mac全体のパフォーマンス低下\nSSDの不要な消耗])

    Safe2 --> AltEnd([正常終了\nGitなど別手段での同期を推奨])
    Safe3 --> AltEnd
```

### パターン1: 開発フォルダをGoogle Driveに直接配置

`~/Google Drive/dev/` のような形で、Google Driveの同期対象フォルダ直下に開発フォルダを置くパターンです。Google Driveが管理するフォルダに開発ファイルが入った瞬間から、すべてのファイルが同期対象になります。これは最も問題が起きやすい構成です。

### パターン2: node_modulesを含んだまま同期

開発フォルダをGoogle Driveの外に置いていても、後から「このフォルダも同期したい」とGoogle Driveに追加した場合や、`cp -R` でGoogle Drive配下にコピーした場合は同様の問題が起きます。`node_modules` を明示的に除外しないかぎり、Google Drive for Desktopにはフィルタリング機能（`.gitignore` に相当するもの）が **存在しない** ため、すべてのファイルが同期対象になります。

### パターン3: .gitを含んだまま同期

ソースコードを「バックアップとして」Google Driveに同期したいと考えるケースです。`.git` ディレクトリを除外せずに同期すると、コミットのたびにfileproviderdが変更を検知して処理を走らせます。開発中は常にバックグラウンドで負荷がかかり続ける状態になります。

---

## 解決策 - どうすればよいか

```mermaid
flowchart TD
    A([開発フォルダをGoogle Driveで同期しようとしているか？]) --> B{Google Drive経由で\n開発フォルダを同期中?}

    B -- "いいえ" --> Z([問題なし\nそのまま利用可能])
    B -- "はい" --> C{node_modulesなどの\n大量小ファイルを含む?}

    C -- "いいえ" --> D{別のMacへの\nファイル転送が目的?}
    C -- "はい" --> E{除外設定で\n対応できるか?}

    E -- "はい\n（node_modules等を除外可能）" --> S1[解決策1\nGoogle Drive除外設定\n.gdignore を配置し\nnode_modulesを同期対象外にする]
    E -- "いいえ\n（プロジェクト全体が必要）" --> D

    D -- "はい\n（一度きりの転送）" --> S2[解決策2\nrsyncで直接転送\nrsync -avz --exclude node_modules\nでMac間をローカル転送]
    D -- "いいえ\n（継続的な同期が必要）" --> F{リアルタイム同期が\n必要?}

    F -- "はい" --> S3[解決策3\nSyncthingで同期\nP2P型・クラウド非経由で\nリアルタイム同期を実現]
    F -- "いいえ\n（定期同期でよい）" --> S2

    S1 --> R1{fileproviderdの\n異常書き込みは解消?}
    S2 --> R2([安全に転送完了\nfileproviderd問題を回避])
    S3 --> R3([継続同期を安全に運用\nfileproviderd問題を回避])

    R1 -- "解消した" --> R2
    R1 -- "まだ発生する" --> D
```

### 解決策1: 開発フォルダをGoogle Driveから除外する（最も簡単）

根本的かつ最もシンプルな解決策は、 **開発フォルダをGoogle Driveの同期対象から外す** ことです。

Google Drive for Desktop（macOS版）では、特定のフォルダをMyDriveの同期対象から除外できます。Google Driveメニューバーアイコン → 設定 → 「マイドライブの同期設定」から、フォルダ単位で同期のオン/オフを切り替えられます。

開発フォルダを最初からGoogle Driveのマウントポイント（`/Volumes/GoogleDrive/`）の外に置くのが最善です。`~/dev/` や `~/projects/` といったホームディレクトリ直下のパスに開発フォルダを置き、Google Drive管理外であることを明示しましょう。

### 解決策2: rsyncで必要なファイルだけ同期する

```mermaid
sequenceDiagram
    actor User as ユーザー
    participant Shell as シェル
    participant Rsync as rsync
    participant FS as ファイルシステム

    User->>Shell: rsync --dry-run -av --delete src/ dst/ を実行
    Shell->>Rsync: ドライラン開始（実際の変更なし）
    Rsync->>FS: ファイル差分を確認（読み取りのみ）
    FS-->>Rsync: ファイル一覧・差分情報を返す
    Rsync-->>Shell: 実行予定の操作を出力（コピー・削除リスト）
    Shell-->>User: ドライラン結果を表示

    User->>User: 出力内容を検証\n（削除対象・コピー対象を確認）

    alt 問題あり
        User->>Shell: コマンドを修正して再度 --dry-run
        Shell->>Rsync: ドライラン再実行
        Rsync-->>User: 修正後の実行予定を表示
    end

    User->>Shell: rsync -av --delete src/ dst/ を実行（本実行）
    Shell->>Rsync: 本実行開始
    Rsync->>FS: ファイルをコピー・削除・更新
    FS-->>Rsync: 処理完了
    Rsync-->>Shell: 実行結果サマリーを出力
    Shell-->>User: 同期完了を通知
```

すでにGoogle Driveに開発フォルダが入ってしまっている場合、または別のMacに開発ファイルをコピーする必要がある場合は、`rsync` を使った除外パターン付きのコピーが有効です。

macOS標準搭載の `rsync` は `--exclude` オプションで細かいフィルタリングが可能です。以下のコマンドは、開発フォルダから不要なファイルを除外しながら別の場所にコピーする例です。

```bash
rsync -avh --progress \
  --exclude='node_modules/' \
  --exclude='.git/' \
  --exclude='vendor/' \
  --exclude='.venv/' \
  --exclude='__pycache__/' \
  --exclude='.DS_Store' \
  --exclude='*.log' \
  --exclude='.env' \
  --exclude='dist/' \
  --exclude='build/' \
  --exclude='.next/' \
  --exclude='.nuxt/' \
  ~/dev/ \
  /Volumes/GoogleDrive/MyDrive/dev-backup/
```

Mac 2台間で定期的に同期する場合は、`--delete` オプションを追加するとコピー先にある余分なファイルが削除され、ミラーリングに近い状態を保てます。

```bash
rsync -avh --progress --delete \
  --exclude='node_modules/' \
  --exclude='.git/' \
  --exclude='vendor/' \
  ~/dev/ \
  /path/to/destination/dev/
```

**`--delete` オプションの注意点**: このオプションを指定すると、コピー先にあってコピー元にないファイルはすべて削除されます。誤ったパスを指定した場合、意図しないファイルが失われる可能性があります。必ず実行前に `--dry-run`（または `-n`）オプションで動作を確認してください。

```bash
# まず --dry-run で確認する
rsync -avhn --progress --delete \
  --exclude='node_modules/' \
  --exclude='.git/' \
  ~/dev/ \
  /path/to/destination/dev/
```

rsyncはあくまでコマンド手動実行またはcron/launchdによる定期実行です。リアルタイム同期ではない点に注意してください。

### 解決策3: Syncthingで開発フォルダを同期する

```mermaid
sequenceDiagram
    actor User as ユーザー
    participant Pkg as パッケージマネージャー
    participant Sys as システム
    participant SVC as Syncthingサービス
    participant Browser as ブラウザ
    participant WebUI as Web UI (localhost:8384)
    participant Remote as リモートデバイス

    User->>Pkg: Syncthingをインストール<br/>(brew install syncthing 等)
    Pkg-->>User: インストール完了

    User->>Sys: サービス起動コマンド実行<br/>(brew services start syncthing)
    Sys->>SVC: サービス開始
    SVC-->>Sys: 起動完了
    Sys-->>User: サービス稼働中

    User->>Browser: localhost:8384 を開く
    Browser->>WebUI: HTTPリクエスト
    WebUI-->>Browser: ダッシュボード表示
    Browser-->>User: Web UIが表示される

    User->>WebUI: 「デバイスを追加」をクリック
    WebUI-->>User: デバイスID入力フォーム表示
    User->>WebUI: リモートデバイスのデバイスIDを入力
    WebUI->>SVC: デバイス追加リクエスト送信
    SVC->>Remote: 接続要求
    Remote-->>SVC: 接続承認
    SVC-->>WebUI: デバイス追加完了
    WebUI-->>User: デバイスが一覧に表示される

    User->>WebUI: 「フォルダを追加」をクリック
    WebUI-->>User: フォルダ設定フォーム表示
    User->>WebUI: 同期フォルダのパスを入力
    User->>WebUI: 共有先デバイスを選択

    WebUI->>SVC: フォルダ共有設定を保存
    SVC->>Remote: フォルダ共有の招待を送信
    Remote-->>SVC: 招待を承認
    SVC-->>WebUI: 同期開始
    WebUI-->>User: フォルダの同期が開始される
```

**Syncthing** （https://syncthing.net/）は、オープンソースで無料のP2P型ファイル同期ツールです。中継サーバーを経由せずデバイス間で直接ファイルを同期する設計になっており、開発者コミュニティでの採用実績も豊富です。こうした特性から、開発フォルダの同期においてGoogle Driveの代替として有力な選択肢です。なお、同種のツールとしてResilio Syncなどもありますが、本記事ではオープンソースで無償利用できるSyncthingを取り上げます。

**Syncthingのインストール（macOS）**

Homebrewを使う場合は以下のコマンドでインストールできます。

```bash
brew install syncthing
```

インストール後、Syncthingをバックグラウンドサービスとして起動するには以下を実行します。

```bash
brew services start syncthing
```

起動後、ブラウザで `http://localhost:8384` にアクセスするとWeb UIが表示されます。設定はWebブラウザのGUIで行います（CLIでの設定もXML設定ファイルの直接編集で可能ですが、本記事ではGUIを使用します）。

**Syncthingの基本設定手順**

1. Mac mini（新しいMac）でもSyncthingをインストール・起動する
2. どちらか一方のWeb UIで「デバイスを追加」をクリックし、もう一方のデバイスIDを入力する
3. 同期したいフォルダ（例: `~/dev/`）を「フォルダを追加」から設定し、共有先デバイスを選択する
