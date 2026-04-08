---
title: "WordPressパーマリンク変更で301リダイレクトが効かない理由 — .htaccess配置の落とし穴"
emoji: "🔁"
type: "tech"
topics: ["wordpress", "htaccess", "apache", "redirect", "seo"]
published: true
status: "published"
publication_name: "correlate_dev"
---

## はじめに

WordPressのパーマリンク構造を変更した後、旧URLの301リダイレクトを `.htaccess` に書いたのに全く効かない——そんな経験はないでしょうか。

`.htaccess` の設定は間違っていない。ブラウザで旧URLにアクセスすると、リダイレクトされるどころか404になる。あるいは、WordPressのデフォルトページが表示される。

この原因は **`.htaccess` 内のリダイレクトルールの配置位置 ** にあります。WordPressが自動生成するブロックの後ろにルールを書いてしまうと、そのルールは永遠に実行されません。

## WordPressの .htaccess 構造

WordPressをインストールすると、`.htaccess` に以下のブロックが自動生成されます。

```apache
# BEGIN WordPress
# The directives (lines) between "BEGIN WordPress" and "END WordPress" are
# dynamically generated, and should only be modified via WordPress filters.
# Any changes to the directives between these markers will be overwritten.
<IfModule mod_rewrite.c>
RewriteEngine On
RewriteBase /
RewriteRule ^index\.php$ - [L]
RewriteCond %{REQUEST_FILENAME} !-f
RewriteCond %{REQUEST_FILENAME} !-d
RewriteRule . /index.php [L]
</IfModule>
# END WordPress
```

最後の行 `RewriteRule . /index.php [L]` が重要です。

この `[L]` フラグは「Last」を意味し、 ** このルールにマッチしたらそれ以降のルールを処理しない ** という意味です。そして `RewriteRule . /index.php` は ** 全てのリクエスト ** （`.` は任意の1文字以上にマッチ）をWordPressの `index.php` に転送します。

つまり、このブロックの後ろにリダイレクトルールを書いても、先にWordPressのルールが全てのリクエストをキャプチャしてしまうため、カスタムルールに到達しません。

## 問題のある .htaccess（動かないパターン）

```apache
# BEGIN WordPress
<IfModule mod_rewrite.c>
RewriteEngine On
RewriteBase /
RewriteRule ^index\.php$ - [L]
RewriteCond %{REQUEST_FILENAME} !-f
RewriteCond %{REQUEST_FILENAME} !-d
RewriteRule . /index.php [L]  ← ここで全リクエストが吸収される
</IfModule>
# END WordPress

# ↓ ここに書いても到達しない
RewriteRule ^archives/([0-9]+)/$ /%category%/$1/ [R=301,L]
```

`# END WordPress` の後ろにルールを追加するのは、多くのブログ記事や技術フォーラムでも見かける間違いです。

## 正しい .htaccess（動くパターン）

```apache
# カスタムリダイレクト（WordPressブロックより前に配置）
<IfModule mod_rewrite.c>
RewriteEngine On
RewriteBase /

# 旧パーマリンク（/archives/投稿ID/）を新パーマリンクへ301リダイレクト
RewriteRule ^archives/([0-9]+)/$ /category/article-slug/ [R=301,L]

# 複数のリダイレクトルールも追加可能
RewriteRule ^old-page/$ /new-page/ [R=301,L]
</IfModule>

# BEGIN WordPress
<IfModule mod_rewrite.c>
RewriteEngine On
RewriteBase /
RewriteRule ^index\.php$ - [L]
RewriteCond %{REQUEST_FILENAME} !-f
RewriteCond %{REQUEST_FILENAME} !-d
RewriteRule . /index.php [L]
</IfModule>
# END WordPress
```

カスタムルールを `# BEGIN WordPress` より ** 前 ** に配置することで、リダイレクトが正常に機能します。

## パーマリンク変更時の全手順

パーマリンク構造を変更する際の完全な手順を示します。

### Step 1: 変更前に旧URLのリストを作成する

```bash
# WP-CLIで全投稿の旧URLを取得
wp post list --post_type=post --fields=ID,post_name --format=csv > old-urls.csv

# または投稿ID一覧を取得
wp post list --post_type=post --fields=ID --format=ids
```

### Step 2: WP-CLIでパーマリンク構造を変更する

```bash
# 例: /archives/%post_id%/ から /%category%/%postname%/ に変更
wp rewrite structure '/%category%/%postname%/'

# .htaccessを自動更新
wp rewrite flush --hard
```

このコマンドで `.htaccess` の `# BEGIN WordPress` 〜 `# END WordPress` ブロックが自動更新されます。ただし、 ** カスタムリダイレクトは手動で追加する必要があります ** 。

### Step 3: .htaccess にリダイレクトルールを追加する

```bash
# .htaccessのバックアップを取る（必須）
cp /var/www/html/.htaccess /var/www/html/.htaccess.backup.$(date +%Y%m%d)

# 現在の内容を確認
cat /var/www/html/.htaccess
```

テキストエディタで `.htaccess` を開き、`# BEGIN WordPress` の前にルールを挿入します。

```apache
# カスタム301リダイレクト
# パーマリンク変更対応: /archives/ID/ → /category/slug/
<IfModule mod_rewrite.c>
RewriteEngine On
RewriteBase /

# 個別投稿のリダイレクト
RewriteRule ^archives/123/$ /technology/my-first-post/ [R=301,L]
RewriteRule ^archives/456/$ /technology/second-post/ [R=301,L]

# パターンマッチが使える場合（スラッグが同じなら）
# RewriteRule ^archives/([0-9]+)/$ /technology/$1/ [R=301,L]
</IfModule>

# BEGIN WordPress
...
```

### Step 4: リダイレクトの動作確認

```bash
# curlで301レスポンスを確認
curl -I https://example.com/archives/123/

# 期待するレスポンス
# HTTP/1.1 301 Moved Permanently
# Location: https://example.com/technology/my-first-post/
```

ブラウザだけでなく、`curl -I` でのHTTPヘッダー確認を必ず行ってください。ブラウザはリダイレクトをキャッシュするため、誤判断の原因になります。

## よくある失敗パターンと対処法

### 失敗パターン1: `[R=301]` を書き忘れて302になる

```apache
# 302（一時的）になってしまう
RewriteRule ^archives/([0-9]+)/$ /new/$1/ [R,L]

# 301（永続的）にする
RewriteRule ^archives/([0-9]+)/$ /new/$1/ [R=301,L]
```

SEOの引き継ぎには301が必要です。302では検索エンジンが旧URLを削除しません。

### 失敗パターン2: RewriteBaseの指定が間違っている

```apache
# WordPressがサブディレクトリにある場合
# https://example.com/blog/ にインストールしている場合
<IfModule mod_rewrite.c>
RewriteEngine On
RewriteBase /blog/  ← WordPressのインストールパスに合わせる

RewriteRule ^archives/([0-9]+)/$ /blog/new/$1/ [R=301,L]
</IfModule>
```

### 失敗パターン3: パーマリンク設定画面で「変更を保存」するたびにルールが上書きされる

WordPressの管理画面から「設定 > パーマリンク > 変更を保存」を実行すると、`# BEGIN WordPress` 〜 `# END WordPress` の間のみが上書きされます。それ以外の部分（カスタムルール）は維持されます。

ただし、誤って `# BEGIN WordPress` の内側にカスタムルールを書いてしまうと上書きされるので注意が必要です。

### 失敗パターン4: mod_rewriteが有効になっていない

```bash
# mod_rewriteの有効化確認（Apache）
apache2ctl -M | grep rewrite

# 有効化（Ubuntu/Debian）
a2enmod rewrite
systemctl restart apache2
```

サーバー側でmod_rewriteが無効の場合、全てのRewriteRuleが無視されます。

## 大量のURLをリダイレクトする場合

投稿数が多い場合、1件ずつルールを書くのは現実的ではありません。この場合はWordPressプラグインか、DBからのマッピングが有効です。

### Redirection プラグインを使う方法

```
Redirection プラグイン（https://wordpress.org/plugins/redirection/）
→ CSVインポートで一括登録が可能
→ 内部でWordPressのDB（wp_redirection_items）を使うため .htaccess 不要
```

### WP-CLIとスクリプトで生成する方法

```bash
#!/bin/bash
# generate-redirects.sh
# 旧URLリストから .htaccess ルールを自動生成

OLD_URL_LIST="old-urls.txt"  # 旧パス一覧（1行1パス）
NEW_BASE="/technology"

echo "# 自動生成リダイレクトルール"
echo "# 生成日: $(date)"
echo "<IfModule mod_rewrite.c>"
echo "RewriteEngine On"
echo "RewriteBase /"

while IFS=',' read -r post_id post_slug; do
  echo "RewriteRule ^archives/${post_id}/\$ ${NEW_BASE}/${post_slug}/ [R=301,L]"
done < "$OLD_URL_LIST"

echo "</IfModule>"
```

## Nginxの場合

ApacheではなくNginxを使っている場合、`.htaccess` は使えません。Nginxの設定ファイルで対応します。

```nginx
# /etc/nginx/sites-available/example.com

server {
    listen 80;
    server_name example.com;
    
    # カスタムリダイレクト（Wordpressの設定より前に記述）
    location ~ ^/archives/([0-9]+)/$ {
        return 301 /technology/$1/;
    }
    
    # WordPress用設定
    location / {
        try_files $uri $uri/ /index.php?$args;
    }
    
    location ~ \.php$ {
        fastcgi_pass unix:/var/run/php/php8.2-fpm.sock;
        include fastcgi_params;
        fastcgi_param SCRIPT_FILENAME $document_root$fastcgi_script_name;
    }
}
```

Nginxの場合、`location` ブロックの優先順位はApacheのRewriteRuleとは異なります。`location ~`（正規表現）は `location /`（前方一致）より優先されるため、明示的な順序指定は不要です。

## まとめ

WordPressの `.htaccess` でリダイレクトが効かない原因と解決策をまとめます。

| 問題 | 原因 | 解決策 |
|------|------|--------|
| リダイレクトが無視される | WordPressブロックの後ろに書いている | `# BEGIN WordPress` の前に移動 |
| 302になる | `[R=301]` を省略している | `[R=301,L]` と明示する |
| 変更が反映されない | ブラウザキャッシュ | `curl -I` でHTTPヘッダーを確認 |
| 全て404になる | mod_rewriteが無効 | `a2enmod rewrite` で有効化 |

パーマリンク変更はSEO的に慎重さが必要な作業です。変更前の全URL把握 → リダイレクトルール作成 → 動作確認という順序を守り、必ず `.htaccess` のバックアップを取ってから作業してください。

## 参考

- [Apache mod_rewrite ドキュメント](https://httpd.apache.org/docs/current/mod/mod_rewrite.html)
- [WordPress .htaccess の生成仕様](https://developer.wordpress.org/reference/functions/got_mod_rewrite/)
- [WP-CLI rewrite コマンド](https://developer.wordpress.org/cli/commands/rewrite/)
