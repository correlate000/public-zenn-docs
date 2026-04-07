---
title: "Node.js ストリーミング API 完全ガイド ─ pipe() の落とし穴からバックプレッシャー制御まで"
emoji: "🌊"
type: "tech"
topics: ["nodejs", "stream", "javascript", "backend", "api"]
published: false
status: "draft"
publication_name: "correlate_dev"
---

> **本記事のコードはすべて ESM（`"type": "module"` 設定済み）、Node.js v18 以上を前提とします。**
> `node:` プレフィックスは v14.18 以降で利用可能です。`pipe()` との比較も扱いますが、実装では `pipeline()` を中心に解説します。

---

## はじめに ─ なぜストリームが必要か

「`fs.readFile` で読んでいたファイルが、本番でメモリ不足を起こした」という経験はないでしょうか。

ストリームが解決するのは次の 3 点です。

| 課題 | 非ストリーム | ストリーム |
|------|-------------|-----------|
| **メモリ効率** | ファイル全体をメモリに展開 | チャンク単位で処理 |
| **TTFB（最初のバイト到達時間）** | 全データ処理後にレスポンス | 生成と同時に送信 |
| **合成可能性** | 単一の変換処理 | 複数ストリームをチェーン接続 |

100 MB のファイルを `readFile` で読んだ場合、RSS（Resident Set Size）は一気に 100 MB 以上増加します。一方 `createReadStream` を使うと、`highWaterMark`（デフォルト 64 KB）単位でメモリ使用量がほぼ一定に保たれます。

---

## Node.js Stream の基礎 ─ 4 種類を正確に理解する

### Readable Stream ─ データを生産する

Readable には **paused mode** と **flowing mode** の 2 つの動作モードがあります。

- **paused mode（デフォルト）**: `read()` を明示的に呼ぶか、`for await...of` で消費する
- **flowing mode**: `data` イベントリスナーを登録すると自動的に切り替わる

現代的なコードでは `for await...of` を推奨します。

```js
import { createReadStream } from 'node:fs';

const stream = createReadStream('./large-file.csv', { encoding: 'utf-8' });

for await (const chunk of stream) {
  // チャンク単位で処理できる
  process(chunk);
}
```

カスタム `Readable` を実装する場合は `_read()` フックをオーバーライドします。

```js
import { Readable } from 'node:stream';

class CounterReadable extends Readable {
  constructor(limit) {
    super({ objectMode: true }); // objectMode: オブジェクトをそのまま流せる
    this.count = 0;
    this.limit = limit;
  }

  _read() {
    if (this.count < this.limit) {
      // push() が false を返したら内部バッファが満杯（バックプレッシャー）
      const canContinue = this.push(this.count++);
      if (!canContinue) return; // バックプレッシャー発生時は即座に止める
    } else {
      this.push(null); // null を push すると EOF シグナル
    }
  }
}

// 使用例
const counter = new CounterReadable(10);
for await (const num of counter) {
  console.log(num); // 0, 1, 2, ..., 9
}
```

### Writable Stream ─ データを消費する

Writable の実装では `_write()` と `_writev()` を選択します。

- `_write(chunk, encoding, callback)`: チャンクを 1 つずつ処理
- `_writev(chunks, callback)`: 複数チャンクをまとめて処理（バッファリングが効く場面で効率的）

`highWaterMark` はバッファの上限サイズです。`write()` の戻り値が `false` になったら、`drain` イベントを待ってから次の書き込みを行う必要があります（これがバックプレッシャー制御の核心です）。

```js
import { Writable } from 'node:stream';

class CollectorWritable extends Writable {
  constructor() {
    super({ highWaterMark: 1024 * 64 }); // 64 KB
    this.chunks = [];
  }

  _write(chunk, encoding, callback) {
    this.chunks.push(chunk);
    // 非同期処理を伴う場合も callback を呼ぶまでは次チャンクが来ない
    callback(); // 処理完了を通知
  }

  getResult() {
    return Buffer.concat(this.chunks).toString('utf-8');
  }
}
```

### Transform Stream ─ 変換処理の中核

Transform は Readable と Writable を合成したストリームです。`_transform()` でチャンクを変換し、`_flush()` でストリーム終了時の後処理を行います。

実用例として JSON Lines パーサーを実装してみます。

```js
import { Transform } from 'node:stream';

class JsonLinesParser extends Transform {
  constructor() {
    super({ objectMode: true }); // 出力をオブジェクトにする
    this._buffer = '';
  }

  _transform(chunk, encoding, callback) {
    this._buffer += chunk.toString('utf-8');
    const lines = this._buffer.split('\n');
    this._buffer = lines.pop(); // 末尾の不完全な行はバッファに残す

    for (const line of lines) {
      if (line.trim()) {
        try {
          this.push(JSON.parse(line));
        } catch (e) {
          return callback(new Error(`Invalid JSON: ${line}`));
        }
      }
    }
    callback();
  }

  _flush(callback) {
    // ストリーム終端で残ったバッファを処理
    if (this._buffer.trim()) {
      try {
        this.push(JSON.parse(this._buffer));
      } catch (e) {
        return callback(new Error(`Invalid JSON in flush: ${this._buffer}`));
      }
    }
    callback();
  }
}
```

`PassThrough` は変換処理を挟まず通過させるだけの Transform です。デバッグ時のログ挿入や、インターフェースとして Readable / Writable の両方が必要な場面で活躍します。

### Duplex Stream ─ 双方向通信

Duplex は Readable と Writable が完全に独立して動作するストリームです。`net.Socket`（TCP ソケット）がその代表例で、受信（Readable）と送信（Writable）が並行して機能します。カスタム実装が必要な場面はネットワークプロトコルの低レイヤー実装などに限られるため、本記事では概念の把握にとどめます。

---

## 制御フローの正しい選択 ─ `pipe()` から `pipeline()` へ

### `pipe()` の問題点

```js
// ❌ アンチパターン: エラーが握りつぶされ、メモリリークが起きうる
readStream.pipe(transformStream).pipe(writeStream);
```

`pipe()` には以下の欠点があります。

1. **エラーが自動伝播しない**: 中間ストリームでエラーが発生しても、上流・下流のストリームは閉じられない
2. **クリーンアップが手動**: ストリームが中断されたとき、すべてのストリームを個別に `destroy()` する必要がある
3. **Promise と組み合わせにくい**: callback ベースのため、async/await コードに混ぜると複雑になる

### `stream.pipeline()` ─ 推奨パターン

```js
import { pipeline } from 'node:stream/promises';
import { createReadStream, createWriteStream } from 'node:fs';
import { createGzip } from 'node:zlib';

// ✅ 推奨: エラー発生時にすべてのストリームを自動クリーンアップ
await pipeline(
  createReadStream('input.txt'),
  createGzip(),
  createWriteStream('output.gz')
);

console.log('圧縮完了');
```

`node:stream/promises` の `pipeline()` は Promise を返します。エラーが発生すると reject され、連結されたすべてのストリームが自動的に `destroy()` されます。これによってメモリリークを防げます。

エラーハンドリングは try/catch で簡潔に書けます。

```js
import { pipeline } from 'node:stream/promises';

async function compressFile(input, output) {
  try {
    await pipeline(
      createReadStream(input),
      createGzip(),
      createWriteStream(output)
    );
  } catch (err) {
    // どのストリームで失敗しても err にエラーが集約される
    console.error('圧縮失敗:', err.message);
    throw err;
  }
}
```

### `stream.finished()` の活用

ストリームが正常終了・エラー終了・中断されたことを検知したい場合は `finished()` を使います。

```js
import { finished } from 'node:stream/promises';
import { createReadStream } from 'node:fs';

const stream = createReadStream('./data.txt');
stream.resume(); // flowing mode に切り替えてデータを消費

try {
  await finished(stream);
  console.log('ストリーム終了');
} catch (err) {
  console.error('ストリームエラー:', err);
}
```

---

## バックプレッシャーを理解・制御する

バックプレッシャーとは「Consumer がデータを処理しきれず、Producer に減速を要求する仕組み」です。これを無視すると内部バッファが膨張し、メモリリークにつながります。

### バックプレッシャーの発生メカニズム

```
[Producer]  push() で書き込む
     │
     ▼
[内部バッファ]  highWaterMark を超えると push() が false を返す
     │
     ▼
[Consumer]  データを処理し終えると drain イベントが発火
```

### 手動制御が必要な場面

`pipeline()` を使えばバックプレッシャーは自動的に処理されます。ただし `write()` を直接呼ぶ場面では戻り値を必ずチェックしてください。

```js
// ❌ バックプレッシャーを無視した例（バッファが際限なく膨らむ）
for (const item of largeArray) {
  writeStream.write(JSON.stringify(item) + '\n');
}

// ✅ バックプレッシャーを考慮した例
async function writeWithBackpressure(writeStream, items) {
  for (const item of items) {
    const canContinue = writeStream.write(JSON.stringify(item) + '\n');
    if (!canContinue) {
      // drain イベントが来るまで待機
      await new Promise((resolve) => writeStream.once('drain', resolve));
    }
  }
}
```

### `highWaterMark` のチューニング指針

| ユースケース | 推奨値 | 備考 |
|-------------|--------|------|
| objectMode（小オブジェクト） | 16〜64 | オブジェクト数ベース |
| バイナリ（小ファイル） | 64 KB（デフォルト） | 変更不要 |
| バイナリ（大ファイル/高スループット） | 256 KB〜1 MB | I/O ボトルネック解消 |
| SSE / リアルタイム配信 | 1〜4（objectMode） | レイテンシ優先 |

---

## 実装パターン集 ─ よくあるユースケース 5 選

### パターン 1 ─ 大容量ファイルダウンロード API

Express でファイルを gzip 圧縮しながらストリーミング送信します。

```js
import express from 'express';
import { createReadStream } from 'node:fs';
import { createGzip } from 'node:zlib';
import { pipeline } from 'node:stream/promises';
import { stat } from 'node:fs/promises';

const app = express();

app.get('/download/:filename', async (req, res) => {
  const filePath = `/data/${req.params.filename}`;

  try {
    await stat(filePath); // ファイル存在確認
  } catch {
    return res.status(404).json({ error: 'File not found' });
  }

  res.setHeader('Content-Type', 'application/octet-stream');
  res.setHeader('Content-Encoding', 'gzip');
  res.setHeader(
    'Content-Disposition',
    `attachment; filename="${req.params.filename}.gz"`
  );

  try {
    await pipeline(
      createReadStream(filePath),
      createGzip({ level: 6 }),
      res
    );
  } catch (err) {
    // pipeline が失敗した場合、res はすでに部分送信済みの可能性がある
    if (!res.headersSent) {
      res.status(500).json({ error: 'Stream error' });
    }
  }
});
```

### パターン 2 ─ Server-Sent Events（SSE）

AI 生成やリアルタイムデータの配信に使われる SSE をストリームで実装します。

```js
app.get('/events', async (req, res) => {
  res.setHeader('Content-Type', 'text/event-stream');
  res.setHeader('Cache-Control', 'no-cache');
  res.setHeader('Connection', 'keep-alive');
  res.flushHeaders();

  const sendEvent = (data) => {
    // SSE フォーマット: "data: {json}\n\n"
    res.write(`data: ${JSON.stringify(data)}\n\n`);
  };

  // クライアント切断時のクリーンアップ
  const cleanup = () => {
    clearInterval(timer);
    res.end();
  };
  req.on('close', cleanup);

  let count = 0;
  const timer = setInterval(() => {
    sendEvent({ count: count++, timestamp: Date.now() });
    if (count >= 10) cleanup();
  }, 1000);
});
```

### パターン 3 ─ データベース Cursor のストリーミング

大量のレコードを一括取得せず、Cursor（カーソル）でストリーミングします。PostgreSQL の例を示します。

```js
import pg from 'pg';
import QueryStream from 'pg-query-stream';
import { pipeline } from 'node:stream/promises';
import { Transform } from 'node:stream';

const pool = new pg.Pool({ connectionString: process.env.DATABASE_URL });

app.get('/export/users', async (req, res) => {
  const client = await pool.connect();

  res.setHeader('Content-Type', 'application/x-ndjson'); // JSON Lines 形式

  const query = new QueryStream(
    'SELECT id, name, email FROM users ORDER BY id',
    [],
    { batchSize: 100 } // 一度に取得する行数
  );

  const dbStream = client.query(query);

  const rowToJsonLine = new Transform({
    objectMode: true,
    transform(row, _, callback) {
      callback(null, JSON.stringify(row) + '\n');
    },
  });

  try {
    await pipeline(dbStream, rowToJsonLine, res);
  } finally {
    client.release(); // 必ず接続を返却する
  }
});
```

### パターン 4 ─ CSV → JSON Lines 変換パイプライン

先ほど実装した `JsonLinesParser` を組み合わせて、CSV を JSON Lines に変換します。

```js
import { createReadStream, createWriteStream } from 'node:fs';
import { pipeline } from 'node:stream/promises';
import { Transform } from 'node:stream';

class CsvToJsonTransform extends Transform {
  constructor() {
    super({ objectMode: true });
    this._headers = null;
    this._buffer = '';
  }

  _transform(chunk, _, callback) {
    this._buffer += chunk.toString('utf-8');
    const lines = this._buffer.split('\n');
    this._buffer = lines.pop(); // 末尾の不完全行をバッファに残す

    for (const line of lines) {
      const values = line.trim().split(',');
      if (!this._headers) {
        this._headers = values; // 1 行目はヘッダー
      } else if (values.length === this._headers.length) {
        const obj = Object.fromEntries(
          this._headers.map((h, i) => [h, values[i]])
        );
        this.push(JSON.stringify(obj) + '\n');
      }
    }
    callback();
  }

  _flush(callback) {
    if (this._buffer.trim() && this._headers) {
      const values = this._buffer.trim().split(',');
      if (values.length === this._headers.length) {
        const obj = Object.fromEntries(
          this._headers.map((h, i) => [h, values[i]])
        );
        this.push(JSON.stringify(obj) + '\n');
      }
    }
    callback();
  }
}

await pipeline(
  createReadStream('input.csv'),
  new CsvToJsonTransform(),
  createWriteStream('output.ndjson')
);
```

### パターン 5 ─ Fetch レスポンスのストリーミング転送

Node.js 18 以降では `fetch()` のレスポンスが Web Streams API の `ReadableStream` を返します。これを Node.js Streams に変換して扱えます。

```js
import { Readable } from 'node:stream';
import { pipeline } from 'node:stream/promises';
import { createWriteStream } from 'node:fs';

async function downloadWithFetch(url, outputPath) {
  const response = await fetch(url);

  if (!response.ok) {
    throw new Error(`HTTP Error: ${response.status}`);
  }

  // Web Streams → Node.js Streams に変換
  const nodeStream = Readable.fromWeb(response.body);

  await pipeline(nodeStream, createWriteStream(outputPath));
}
```

---

## テスト戦略 ─ ストリームのユニットテスト

### `Readable.from()` でモックデータを注入する

```js
import { Readable } from 'node:stream';
import { pipeline } from 'node:stream/promises';
import assert from 'node:assert/strict';

// テスト用のモックデータ生成
const mockData = ['{"id":1}\n', '{"id":2}\n', '{"id":3}\n'];
const mockReadable = Readable.from(mockData);

// Transform の出力をバッファに収集するヘルパー
async function collectStream(readable) {
  const chunks = [];
  for await (const chunk of readable) {
    chunks.push(chunk);
  }
  return chunks;
}

// テスト例（Node.js 標準の assert を使用）
const parser = new JsonLinesParser();
Readable.from(mockData).pipe(parser);

const results = await collectStream(parser);
assert.equal(results.length, 3);
assert.deepEqual(results[0], { id: 1 });
```

### エラーケースのテスト

```js
import { Readable } from 'node:stream';

async function testInvalidJson() {
  const invalidData = ['not-json\n'];
  const readable = Readable.from(invalidData);
  const parser = new JsonLinesParser();

  try {
    await pipeline(readable, parser, new Writable({ write(chunk, _, cb) { cb(); } }));
    assert.fail('エラーが発生するはずでした');
  } catch (err) {
    assert.match(err.message, /Invalid JSON/);
  }
}
```

---

## Web Streams API との共存 ─ Node.js 18+ 対応

### Node.js Streams と Web Streams の相互変換

Node.js 18 以降、WHATWG 準拠の Web Streams API（`ReadableStream`・`WritableStream`・`TransformStream`）がグローバルに利用可能になりました。Fetch API や CloudFlare Workers、Deno との互換性が高まっています。

```js
import { Readable, Writable } from 'node:stream';
import { ReadableStream } from 'node:stream/web';

// ── Node.js Stream → Web Stream ──────────────────────
const nodeReadable = createReadStream('./data.txt');
const webStream = Readable.toWeb(nodeReadable);

// ── Web Stream → Node.js Stream ──────────────────────
const webReadable = new ReadableStream({
  start(controller) {
    controller.enqueue(new TextEncoder().encode('hello'));
    controller.close();
  },
});
const nodeStream = Readable.fromWeb(webReadable);

// ── Writable の変換 ───────────────────────────────────
const nodeWritable = createWriteStream('./output.txt');
const webWritable = Writable.toWeb(nodeWritable);
```

### 使い分けの指針

| シナリオ | 推奨 |
|---------|------|
| 既存の Node.js エコシステム（`fs`, `zlib`, `crypto` 等） | Node.js Streams |
| Fetch API・Service Worker との統合 | Web Streams |
| Edge Runtime（Vercel Edge / CF Workers） | Web Streams |
| 両環境に対応したライブラリ開発 | Web Streams を基本とし `fromWeb/toWeb` で変換 |

---

## まとめ ─ ストリーム設計のチェックリスト

実装前に以下を確認してください。

```
実装前チェックリスト
├─ [ ] データサイズが不定または大きい場合はストリームを選択した
├─ [ ] pipe() ではなく pipeline() を使用している
├─ [ ] エラーハンドリングを try/catch または .catch() で実装した
├─ [ ] highWaterMark をユースケースに合わせて設定した
├─ [ ] write() の戻り値を確認し、drain を待機している（直接呼ぶ場合）
├─ [ ] Transform の _flush() で末尾データを処理している
├─ [ ] テストで Readable.from() を使ってモックデータを注入している
└─ [ ] Node.js / Web Streams どちらを選ぶか意識的に判断している
```

ストリームは「知っている」と「使いこなせる」の間のギャップが大きい領域です。本記事で紹介したパターンをそのまま実装に持ち込んでいただき、本番環境での大容量データ処理に役立てていただければ幸いです。

---

## 参考リソース

- [Node.js 公式ドキュメント - Stream](https://nodejs.org/api/stream.html)
- [Node.js 公式ドキュメント - Web Streams API](https://nodejs.org/api/webstreams.html)
- [Node.js Streams ─ Backpressuring in Streams](https://nodejs.org/en/docs/guides/backpressuring-in-streams)
