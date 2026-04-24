---
title: "119自治体の議会議事録を一括スクレイピングする — kensakusystem.jp攻略ガイド"
emoji: "🏛️"
type: "tech"
topics: ["python", "scraping", "opendata", "automation", "bigquery"]
published: false
status: "draft"
publication_name: "correlate_dev"
---

## はじめに

地方議会の議事録は、住民が行政に何を問い、行政がどう答えたかの一次記録です。しかし、多くの自治体ではシステムがバラバラで、横断的なデータ分析は困難とされてきました。

実は、全国 119 の自治体が **kensakusystem.jp** という共通プラットフォームを採用しています。URL 構造がほぼ統一されているため、1 つのスクレイパーで全自治体を網羅できます。

この記事では、kensakusystem.jp の URL 構造を解析し、118 自治体から議会議事録を一括取得するまでの実装を解説します。弘前市・鎌倉市・豊島区など実際に取得した数値を交えながら、エンコーディングの罠・3 階層ツリー走査・並列化パターンを具体的に示します。

### この記事で扱う内容

- kensakusystem.jp の URL 構造と 3 階層ツリーの走査方法
- `system_code` の自動検出ロジック
- cp932（Windows 拡張 Shift_JIS）の必須性とその理由
- 役職パーサーの 2 パターン対応（括弧あり・括弧なし）
- `startPos=-1` 問題
- `ThreadPoolExecutor` による自治体間並列化（直列 162 時間 → 並列 40 時間）
- BigQuery への冪等格納

---

## kensakusystem.jp とは

kensakusystem.jp は NEC ネクサソリューションズが提供する自治体向け議会会議録検索システムです。2026 年 4 月時点で **119 自治体** が採用しています。

robots.txt はどの自治体でも `404` を返すため、アクセス制限はありません。対照的に大和速記が提供する DB-Search システムは `Disallow: /` でブロックしているため、kensakusystem は研究・シビックテック用途で扱いやすい存在です。

全 119 自治体のドライランを実施した結果、**118/119 が到達可能** （豊能町のみ移行済みと推定）、全 118 件で `system_code` の自動検出に成功しました。

---

## URL 構造の解析

kensakusystem は CGI ベースのシステムで、エンドポイントはすべて `.exe` 形式です。主要な 4 エンドポイントを示します。

```
https://www.kensakusystem.jp/{municipality_id}/cgi-bin3/See.exe
https://www.kensakusystem.jp/{municipality_id}/cgi-bin3/ResultFrame.exe
https://www.kensakusystem.jp/{municipality_id}/cgi-bin3/r_Speakers.exe
https://www.kensakusystem.jp/{municipality_id}/cgi-bin3/GetText3.exe
```

取得フローは 5 段階です。

```
See.exe（ツリー）
  ├── Level 1: 年ラベル（"令和 7年"）
  ├── Level 2: カテゴリ（"令和 7年 第1回定例会"）
  └── Level 3: ResultFrame リンク（fileName + startPos）
        └── r_Speakers.exe → GetText3.exe（発言テキスト）
```

`municipality_id` は URL の一部（例: `toshima`、`kamakura`）で、`system_code` はトップページ HTML に埋め込まれた 18 文字程度のランダム文字列です。

---

## system_code の自動検出

各自治体の HTML には `Code={system_code}` の形でコードが埋め込まれています。

```python
def discover_system_code(self) -> Optional[str]:
    html = self._curl_get(f"https://www.kensakusystem.jp/{self.municipality_id}/")
    if not html:
        return None
    # パターン1: Code=xxxxx（99%以上をカバー）
    m = re.search(r'Code=([a-z0-9]{10,})', html)
    if m:
        return m.group(1)
    # パターン2: See.exe?Code=xxxxx
    m = re.search(r'See\.exe\?Code=([a-z0-9]{10,})', html)
    return m.group(1) if m else None
```

豊島区は `jejucn3ptc8dzc6egg`、目黒区・葛飾区・鎌倉市はそれぞれ異なる値が検出されます。119 自治体すべてで検出に成功しました。

---

## macOS SSL 問題と curl ラッパー

macOS Tahoe（25.2）と Python 3.12 の組み合わせで、`urllib` / `requests` が kensakusystem.jp への SSL 接続でタイムアウトします。既知のバグのため、`subprocess` + `curl` で回避します。GET/POST ともにレスポンスは `cp932` でデコード、POST パラメータも `cp932` でエンコードします。

```python
def _curl_get(self, url: str) -> str:
    result = subprocess.run(
        ["curl", "-sS", "--connect-timeout", "15",
         "-H", "User-Agent: MachiKarte/0.1 (research)", url],
        capture_output=True, timeout=30,
    )
    return result.stdout.decode("cp932", errors="replace")

def _curl_post(self, url: str, params: dict) -> str:
    data = urllib.parse.urlencode(params, encoding="cp932")
    result = subprocess.run(
        ["curl", "-sS", "--connect-timeout", "15",
         "-H", "User-Agent: MachiKarte/0.1 (research)",
         "-d", data, url],
        capture_output=True, timeout=30,
    )
    return result.stdout.decode("cp932", errors="replace")
```

---

## エンコーディングの罠：shift_jis ではなく cp932

最大のハマりポイントです。**kensakusystem のレスポンスは Shift_JIS ではなく cp932（Windows 拡張 Shift_JIS）です。**

`shift_jis` でデコードすると人名に含まれる外字（`德`・`彌` など）が文字化けします。

```python
# NG: 外字（德、彌など）が文字化けする
result.stdout.decode("shift_jis", errors="replace")

# OK: Windows外字を含む全文字に対応
result.stdout.decode("cp932", errors="replace")
```

実際に葛飾区の「青木克德区長」の `德`（土偏の德）が `shift_jis` では `?` に化けました。`cp932` で解消。日本の行政系システムを扱う際は **cp932 を第一候補** にすることを推奨します。

---

## 3 階層ツリーの走査

See.exe は onClick イベントの `treedepth.value='...'` でツリーの深さを管理しています。年・カテゴリ・セッションの 3 階層を順に走査します。

```python
def get_all_sessions(self, year: Optional[int] = None) -> list[dict]:
    html = self._curl_post(f"{self.base_cgi}/See.exe", {"Code": self.system_code})

    # トップに直接 ResultFrame リンクがある場合（1階層の自治体）
    direct = self._extract_sessions_from_html(html)
    if direct:
        return direct

    # 年ラベルを取得（"令和 7年" 形式のみ）
    year_labels = [
        v for v in self._extract_treedepth_values(html)
        if re.match(r'^(令和|平成|昭和)\s*\d+年$', v.strip())
    ]
    all_sessions, seen = [], set()
    for year_label in year_labels:
        time.sleep(DELAY)
        categories = self._get_category_treedepths(year_label)
        targets = categories if categories else [year_label]
        for cat in targets:
            time.sleep(DELAY)
            for s in self.get_session_list(year_code=cat):
                if s["filename"] not in seen:
                    seen.add(s["filename"])
                    all_sessions.append(s)
    return all_sessions
```

treedepth 値の重複排除は `seen: set[str]` で管理します。

---

## startPos の -1 問題

ResultFrame へのリンクは `fileName=R070218A&startPos=0` の形式ですが、 **自治体によっては `startPos=-1` になります。** 考慮しないと鎌倉市など一部の自治体でセッション一覧が空になります。

```python
def _extract_sessions_from_html(self, html: str) -> list[dict]:
    pattern = (
        r'ResultFrame\.exe\?Code=%s&fileName=(\w+)&startPos=(-?\d+)'
        % re.escape(self.system_code)
    )
    links = re.findall(pattern, html)
    # ...
```

`(-?\d+)` で負の値にも対応します。豊島区は `startPos=0`、鎌倉市は `startPos=-1` でした。

---

## 発言者パーサーの 2 パターン

タイトル行から発言者名と役職を抽出します。書式は自治体によって異なります。

**パターン 1: 括弧付き形式（豊島区・鎌倉市など）**  
`区長（高際みゆき）` / `議長（池田実議員）`

**パターン 2: 括弧なし形式（目黒区・葛飾区など）**  
`青木英二区長` / `井口信二教育委員`

```python
ROLE_SUFFIXES = [
    "選挙管理委員会事務局長", "選挙管理委員長",
    "副議長", "議長", "副区長", "副市長", "区長", "市長",
    "事務局長",  # 「局長」より先にマッチさせる
    "部長", "課長", "局長", "次長", "参事",
    "副委員長", "委員長", "議員",
]

def _parse_title_line(self, title_line: str) -> tuple[str, str, str]:
    m = re.match(r"(.+?）)\s+(.+)", title_line)
    if not m:
        return title_line, "", ""
    session_title = m.group(1).strip()
    speaker_raw = m.group(2).strip()

    # パターン1: "区長（高際みゆき）"
    role_match = re.match(r"(.+?)（(.+?)）", speaker_raw)
    if role_match:
        role = role_match.group(1).strip()
        name = role_match.group(2).strip()
        if name.endswith("議員"):
            name = name[:-2]
        return session_title, name, role

    # パターン2: 末尾の役職サフィックスで分離
    for suffix in self.ROLE_SUFFIXES:
        if speaker_raw.endswith(suffix) and len(speaker_raw) > len(suffix):
            return session_title, speaker_raw[: -len(suffix)], suffix

    return session_title, speaker_raw, "議員"
```

`ROLE_SUFFIXES` の順番が重要で、「事務局長」を「局長」より前に置かないと `茶木久美子事務局長` が誤分割されます。

---

## QA ペアとブラックリスト方式

発言列から「質問-答弁」ペアを構造化する際、答弁者の判定が課題です。部長名をホワイトリスト管理しようとしましたが、自治体ごとに部長名が異なるため維持できません。「議員・議長・副議長・事務局長でなければ答弁者」というブラックリスト方式で解決しました。

```python
MODERATOR_ROLES = {"議長", "副議長", "事務局長"}
QUESTIONER_ROLES = {"議員"}
# これ以外の役職は全て答弁者（執行側）として扱う
```

また、答弁の曖昧さを数値化するため「検討します」系フレーズを 8 パターンで検出します。豊島区令和 6〜7 年の 133 QA ペアを分析した結果、**43.6% のペアで「検討します」相当フレーズを検出** しました。

```python
KENTOU_PATTERNS = [
    r"検討してまいります", r"検討いたします", r"検討します",
    r"研究してまいります", r"研究いたします", r"研究します",
    r"検討していく", r"検討を進めて",
]
```

---

## ThreadPoolExecutor による並列化

1 自治体のスクレイピングにかかる時間はセッション数 × 発言数 × 1 秒（レート制限）です。弘前市は 98 セッション・約 12,000 発言で約 3.5 時間。直列で 119 自治体を処理すると推定 162 時間かかります。**4 並列で約 40 時間、6 並列で約 27 時間** に短縮できます。

設計のポイントは「自治体間は並列、自治体内は直列」。自治体ごとに 1 秒のウェイトを維持することでサーバー負荷を分散します。

```python
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

print_lock = Lock()

def scrape_one(muni: dict) -> dict:
    output_dir = DATA_DIR / muni["ks_id"]
    output_dir.mkdir(parents=True, exist_ok=True)
    scraper = MunicipalityScraper(**muni)
    scraper.system_code = scraper.discover_system_code()
    sessions = scraper.get_all_sessions()
    for s in sessions:
        json_file = output_dir / f"{s['filename']}.json"
        if json_file.exists():   # レジューム: 取得済みをスキップ
            continue
        scraper.scrape_session(s["filename"], output_dir)
    return {"total_sessions": len(sessions)}

with ThreadPoolExecutor(max_workers=4) as executor:
    futures = {executor.submit(scrape_one, m): m for m in municipalities}
    for future in as_completed(futures):
        future.result()
```

セッションごとに JSON を保存するため、途中で止まっても `json_file.exists()` でセッション単位にレジュームできます。

---

## BigQuery への冪等格納

`insert_rows_json`（ストリーミング API）はコストと制約があるため、`load_table_from_file` で DELETE + INSERT による冪等ロードを実装します。

```python
def load_speeches_to_bq(speeches: list[dict], municipality_id: str) -> None:
    client = bigquery.Client(project="your-project")
    table_ref = client.dataset("machikarte").table("speeches")
    # 冪等 DELETE
    client.query(
        f"DELETE FROM `machikarte.speeches` WHERE municipality_id = '{municipality_id}'"
    ).result()
    # JSONL 一括 INSERT
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        for s in speeches:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
        tmp_path = f.name
    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
        autodetect=True,
    )
    with open(tmp_path, "rb") as f:
        client.load_table_from_file(f, table_ref, job_config=job_config).result()
```

---

## 取得結果の実績値

| 自治体 | セッション数 | 発言数 | QA ペア数 |
|--------|------------|--------|---------|
| 豊島区 | 23 | 2,211 | 133 |
| 鎌倉市 | 94 | 10,000+ | 1,008+ |
| 弘前市 | 98 | 12,000+ | 600+ |
| 全 119 自治体（完了後） | 7,543 | 174,000+ | 5,129 |

QA ペアの「検討します」含有率は豊島区で 43.6%。全自治体分析では検討フレーズを含む答弁が 7,547 件（174,000 発言中）確認できました。

---

## 実装上のハマりポイント早見表

| 項目 | 注意点 |
|------|--------|
| エンコーディング | `cp932` 必須。`shift_jis` では人名外字が文字化け |
| POST パラメータ | `urllib.parse.urlencode(params, encoding="cp932")` でエンコード |
| startPos | `(-?\d+)` で負値対応。省略すると鎌倉市などでセッション一覧が空 |
| ROLE_SUFFIXES | 長い役職名を先に配置（`事務局長` > `局長`） |
| 並列化設計 | 自治体間は並列、自治体内は直列（1 秒ウェイト維持） |
| レジューム | セッションごとに JSON を保存。`json_file.exists()` でスキップ |
| macOS Tahoe | Python 3.12 の SSL バグ → `subprocess` + `curl` で回避 |

---

## 法的・倫理的な注意点

kensakusystem.jp は `robots.txt` が 404（存在しない）のため、明示的なクロール禁止はありません。ただし自治体独自ドメインで別システムを使っている場合は個別確認が必要です。

1 発言あたり 1 秒のウェイトを挟み、`User-Agent` にボットと研究目的を明示します。議会議事録は地方自治法に基づく公文書であり、多くの場合は公益的利用が認められますが、自治体の利用規約を個別確認することを推奨します。

---

## おわりに

kensakusystem.jp は URL 構造が統一されているため、1 つのスクレイパーで 119 自治体を横断できます。実装上の注意点は 4 つに集約されます。

1. エンコーディングは `cp932` を使う
2. `startPos` は `(-?\d+)` で負値に対応する
3. 役職パーサーは括弧あり・なしの両方に対応する
4. 自治体内は直列、自治体間は並列で負荷とスループットを両立する

これらを押さえれば、118 自治体・174,000 件超の発言データを取得できます。地方議会のオープンデータ活用に取り組む方の参考になれば幸いです。
