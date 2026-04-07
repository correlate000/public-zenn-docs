---
title: "SWELL×CF7のService Worker競合を解決する — mu-pluginでリダイレクトを確実に動かす"
emoji: "🔌"
type: "tech"
topics: ["wordpress", "contactform7", "swell", "php", "javascript"]
published: false
status: "draft"
publication_name: "correlate_dev"
---

## はじめに

Contact Form 7（CF7）でフォーム送信後のリダイレクトを設定したのに、SWELLテーマを使っているサイトでは動作しない──この問題に遭遇したことはありませんか。

原因はSWELLのService Workerと、CF7のリダイレクト処理の競合です。本記事では問題の原因から解決策のmu-plugin実装まで、実際の対応手順を解説します。

---

## 問題の背景

### CF7 v6.1.5以降の仕様変更

CF7はv6.1.5で `on_sent_ok` の非推奨化を発表しました。それ以前は以下のようなカスタマイズが一般的でした。

```javascript
// 旧来の方法（非推奨）
document.addEventListener('wpcf7mailsent', function(event) {
  location = 'https://example.com/thanks/';
}, false);
```

あるいはCF7の追加設定でJavaScriptを直接記述する `on_sent_ok` フィールドを使う方法も広く使われていました。

```
// CF7管理画面の「追加設定」欄（非推奨）
on_sent_ok: "location = 'https://example.com/thanks/';"
```

v6.1.5以降、公式の代替として `redirect_to` という専用設定が追加されました。

```
// CF7管理画面の「追加設定」欄（現在の公式推奨）
redirect_to: https://example.com/thanks/
```

### SWELLのService Worker

SWELLはページ遷移をSPA風に滑らかにするため、Service Workerを登録しています。ブラウザの開発者ツールを開くと確認できます。

```
Application > Service Workers > design.swell-theme.com
```

このService Workerは「ナビゲーションプリロード」を実装しており、JavaScriptによるページ遷移リクエストをインターセプトします。CF7の `redirect_to` が発行するJavaScriptナビゲーションと競合し、リダイレクトが無効化されるという現象が起きます。

### エラーの見分け方

ブラウザのコンソールに以下のようなメッセージが出ていれば、この競合が原因です。

```
The service worker navigation preload request was cancelled before 'preloadResponse' settled.
```

または、フォーム送信後にページが遷移せず「送信完了」のメッセージが表示されたままになります。

---

## 解決策：mu-pluginによるイベントベースのリダイレクト

### なぜmu-pluginか

`wp-content/mu-plugins/` に配置したPHPファイルは、プラグイン有効化なしに自動的に読み込まれる「Must Use Plugin」です。テーマの `functions.php` に書く方法もありますが、テーマ変更の影響を受けない独立したmu-pluginが保守性の面で優れています。

### 実装

```php
<?php
/**
 * CF7 リダイレクト（SWELL Service Worker競合回避）
 *
 * CF7 v6.1.5+ の redirect_to はSWELLのService Workerと競合する。
 * wpcf7mailsent カスタムイベントをリッスンし、
 * window.location.replace() でリダイレクトを実行する。
 *
 * @package    Correlate
 * @version    1.0.0
 */

if ( ! defined( 'ABSPATH' ) ) {
    exit;
}

add_action( 'wp_footer', 'correlate_cf7_redirect_script', 99 );

function correlate_cf7_redirect_script(): void {
    // CF7がアクティブでない場合は何もしない
    if ( ! function_exists( 'wpcf7' ) ) {
        return;
    }

    // リダイレクト設定（フォームID => リダイレクト先URL）
    $redirect_map = [
        123 => home_url( '/thanks/' ),           // お問い合わせフォーム
        456 => home_url( '/thanks/download/' ),   // 資料請求フォーム
    ];

    $json_map = wp_json_encode( $redirect_map );

    ?>
    <script>
    (function () {
        const redirectMap = <?php echo $json_map; ?>;

        document.addEventListener('wpcf7mailsent', function (event) {
            const formId = parseInt(event.detail.contactFormId, 10);
            const destination = redirectMap[formId];

            if (!destination) {
                return;
            }

            // window.location.href ではなく replace() を使用
            // ブラウザ履歴に送信完了状態が残ることを防ぐ
            window.location.replace(destination);
        }, false);
    })();
    </script>
    <?php
}
```

このファイルを `/wp-content/mu-plugins/cf7-redirect.php` として保存するだけで有効になります。

### フォームIDの確認方法

CF7管理画面で各フォームのURLを確認します。

```
https://example.com/wp-admin/admin.php?page=wpcf7&action=edit&post=123
```

URLの `post=123` がフォームIDです。

---

## 実装の詳細解説

### `window.location.replace()` vs `window.location.href`

```javascript
// こちらは履歴に残る（ブラウザの「戻る」でフォームに戻れる）
window.location.href = 'https://example.com/thanks/';

// こちらは履歴に残らない（「戻る」しても送信完了ページに戻らない）
window.location.replace('https://example.com/thanks/');
```

フォーム送信後のリダイレクトでは `replace()` が適切です。ユーザーが「戻る」ボタンを押したときに、空のフォームに戻れる方が自然なUXになります。

### `event.detail.contactFormId` の型

`wpcf7mailsent` イベントの `contactFormId` は文字列として渡されます。`redirectMap` のキーと比較するために `parseInt()` で数値変換しています。

```javascript
// event.detail の構造
{
  contactFormId: "123",  // ← 文字列
  unitTag: "wpcf7-f123-p456-o1",
  formData: FormData {...},
  status: "mail_sent",
  // ...
}
```

### wp_footer フックのタイミング

```php
add_action( 'wp_footer', 'correlate_cf7_redirect_script', 99 );
```

優先度を `99` にしているのは、CF7自体が `wp_footer` でスクリプトを出力するためです。CF7のスクリプトより後に読み込むことで、イベントリスナーが確実に機能します。

---

## 複数フォームへの対応

### フォームIDベースのルーティング

上記の `$redirect_map` をサイトの要件に応じて拡張します。

```php
$redirect_map = [
    100 => home_url( '/thanks/' ),              // 汎用お問い合わせ
    101 => home_url( '/thanks/consultation/' ), // 無料相談申込
    102 => 'https://external-site.com/thanks/', // 外部URL（絶対パスも可）
];
```

### 動的なリダイレクト先

フォームの設定でリダイレクト先を変えたい場合は、カスタムフィールドや定数を使う方法もあります。

```php
// wp-config.php に定数を定義
define('CF7_REDIRECT_CONTACT', home_url('/thanks/'));
define('CF7_REDIRECT_DOWNLOAD', home_url('/thanks/download/'));

// mu-plugin側
$redirect_map = [
    123 => CF7_REDIRECT_CONTACT,
    456 => CF7_REDIRECT_DOWNLOAD,
];
```

---

## デバッグ手順

### Step 1: Service Workerの確認

Chrome DevToolsで確認します。

```
Application タブ > Service Workers
```

SWELLのService Workerが登録されているか確認し、「Bypass for network」にチェックを入れてService Workerを無効化した状態でフォームを送信します。この状態でリダイレクトが動作すれば、Service Workerが原因です。

### Step 2: wpcf7mailsentイベントの確認

```javascript
// コンソールに貼り付けて実行
document.addEventListener('wpcf7mailsent', function(event) {
    console.log('wpcf7mailsent fired:', {
        formId: event.detail.contactFormId,
        status: event.detail.status,
    });
}, false);
```

フォームを送信してコンソールを確認します。イベントが発火しているかどうかを確認できます。

### Step 3: mu-pluginのスクリプト出力確認

```bash
# ページのソースコードを確認
curl -s https://example.com/contact/ | grep -A 20 'redirectMap'
```

mu-pluginが正しく読み込まれていれば、ページのフッターにスクリプトが出力されています。

### Step 4: ネットワークタブでのリダイレクト確認

```
Network タブ > フォーム送信後のリクエストを確認
```

リダイレクトが実行されると、`/thanks/` へのGETリクエストが記録されます。

---

## CF7 redirect_to との関係

公式の `redirect_to` 設定を完全に置き換えるアプローチです。

```
// CF7管理画面の「追加設定」欄からは redirect_to を削除する
// mu-pluginで全てのリダイレクトを管理する
```

両方を有効にすると競合する可能性があるため、どちらか一方に統一することをおすすめします。

mu-pluginで管理する利点は以下の通りです。

- フォームIDと転送先の対応がコードで一元管理される
- テーマ変更の影響を受けない
- 転送先のバリデーションを追加しやすい

---

## SWELL以外のテーマでの注意点

Service Worker競合はSWELLに限らず、Service Workerを使うテーマやプラグインで発生します。

```
Cocoon（一部バージョン）
LIQUID PRESS
PWAプラグイン（PWA for WP等）を導入している場合
```

デバッグStep 1の手順でService Workerが登録されているか確認し、同様のアプローチで対処できます。

---

## セキュリティ考慮事項

### リダイレクト先のバリデーション

外部URLへのオープンリダイレクトを防ぐため、リダイレクト先を検証するアプローチです。

```php
function correlate_is_safe_redirect( string $url ): bool {
    $parsed = wp_parse_url( $url );
    $home   = wp_parse_url( home_url() );

    // 同一ドメインか、または許可リストに含まれるドメインのみ許可
    $allowed_hosts = [
        $home['host'],
        'docs.example.com',
    ];

    return in_array( $parsed['host'] ?? '', $allowed_hosts, true );
}

// リダイレクトマップ構築時に検証
foreach ( $raw_map as $id => $url ) {
    if ( correlate_is_safe_redirect( $url ) ) {
        $redirect_map[ $id ] = $url;
    } else {
        error_log( "CF7 redirect: unsafe URL rejected: {$url}" );
    }
}
```

### フォームIDの整数変換

JavaScriptで `parseInt()` を使うことで、文字列注入によるキーの不一致を防いでいます。

```javascript
const formId = parseInt(event.detail.contactFormId, 10);
// "123evil" → 123（数値として正しく処理）
// "abc" → NaN（redirectMapのキーとマッチしない）
```

---

## まとめ

SWELLとContact Form 7のリダイレクト競合は、Service Workerが原因です。解決策は、CF7の `redirect_to` を使わず、`wpcf7mailsent` カスタムイベントを `window.location.replace()` でハンドリングするmu-pluginを実装することです。

| 方法 | SWELLでの動作 | 保守性 |
|---|---|---|
| CF7の `redirect_to` | ✗ 動作しない | 高（設定画面で管理） |
| `on_sent_ok`（非推奨） | △ 不安定 | 低 |
| mu-plugin + `wpcf7mailsent` | ✅ 安定動作 | 高（コードで一元管理） |

デバッグの鍵は「Bypass for network」でService Workerを無効化して動作確認することです。Service Worker無効時に動けば、後は本記事のmu-pluginを適用するだけで解決します。

### 関連リソース

- [Contact Form 7 公式ドキュメント - Special Mail-Tags](https://contactform7.com/special-mail-tags/)
- [SWELL公式サポート](https://swell-theme.com/)
