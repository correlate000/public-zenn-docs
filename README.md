# public-zenn-docs

Zenn 投稿用リポジトリ（無料記事）

## 概要

[Zenn](https://zenn.dev/) に公開する無料の技術記事・アイデア記事を管理するリポジトリです。
GitHub 連携により、`main` ブランチへの push で自動的に Zenn へデプロイされます。

## リポジトリ構成

```
public-zenn-docs/
├── articles/          # 記事（Markdown）
│   └── {slug}.md
├── books/             # 本（将来的に利用）
│   └── {slug}/
├── .zenn/             # Zenn CLI 内部設定
├── package.json
└── pnpm-lock.yaml
```

## セットアップ

```bash
# 依存関係のインストール
pnpm install

# 新しい記事を作成
npx zenn new:article --slug my-article

# ローカルプレビュー（http://localhost:8000）
npx zenn preview
```

## 記事の公開フロー

1. `npx zenn new:article --slug {slug}` で記事ファイルを作成
2. `articles/{slug}.md` を編集
3. `npx zenn preview` でローカル確認
4. frontmatter の `published: true` に変更
5. `git add . && git commit && git push` で Zenn に自動公開

## 記事フォーマット

```yaml
---
title: "記事のタイトル"
emoji: "📝"
type: "tech"        # tech: 技術記事 / idea: アイデア記事
topics: ["Claude", "AI"]  # 1-5個のトピック
published: true     # true: 公開 / false: 下書き
---
```

## 関連リポジトリ

| リポジトリ | 用途 |
|-----------|------|
| [public-zenn-docs](https://github.com/correlate000/public-zenn-docs) | 無料記事（このリポジトリ） |
| [private-zenn-docs](https://github.com/correlate000/private-zenn-docs) | 有料記事 |

## 技術スタック

- [Zenn CLI](https://zenn.dev/zenn/articles/install-zenn-cli) v0.4.5
- pnpm
- GitHub 連携によるデプロイ

## 著者

**Naoya** / [合同会社コラレイトデザイン](https://correlate.design)
