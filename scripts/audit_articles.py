#!/usr/bin/env python3
"""
Zenn記事 品質・SEO一括監査スクリプト
重大度: CRITICAL / HIGH / MEDIUM / LOW
"""

import os
import re
import sys
from pathlib import Path
from collections import defaultdict
from difflib import SequenceMatcher

ARTICLES_DIR = Path("/Users/naoyayokota/dev/projects/self/public-zenn-docs/articles")

# ---- frontmatter パーサー ----

def parse_frontmatter(content: str) -> dict:
    """YAMLフロントマターをパース（PyYAML不要）"""
    lines = content.split("\n")
    if not lines[0].strip() == "---":
        return {}

    fm = {}
    in_fm = False
    for i, line in enumerate(lines):
        if i == 0 and line.strip() == "---":
            in_fm = True
            continue
        if in_fm and line.strip() == "---":
            break
        if in_fm:
            # topics: [a, b, c] 形式
            if line.startswith("topics:"):
                val = line[len("topics:"):].strip()
                if val.startswith("[") and val.endswith("]"):
                    inner = val[1:-1]
                    topics = [t.strip().strip('"').strip("'") for t in inner.split(",") if t.strip()]
                    fm["topics"] = topics
                else:
                    fm["topics"] = []
            elif ":" in line:
                key, _, val = line.partition(":")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if val.lower() == "true":
                    val = True
                elif val.lower() == "false":
                    val = False
                fm[key] = val
    return fm


def get_body(content: str) -> str:
    """フロントマター以降の本文を返す"""
    lines = content.split("\n")
    if not lines[0].strip() == "---":
        return content
    end = -1
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end = i
            break
    if end == -1:
        return content
    return "\n".join(lines[end + 1:])


# ---- 記事読み込み ----

def load_articles() -> list[dict]:
    articles = []
    for path in sorted(ARTICLES_DIR.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        fm = parse_frontmatter(text)
        body = get_body(text)
        articles.append({
            "path": path,
            "slug": path.stem,
            "filename": path.name,
            "fm": fm,
            "body": body,
            "raw": text,
            "line_count": len(text.split("\n")),
        })
    return articles


def is_published(a: dict) -> bool:
    return a["fm"].get("published") is True


def title_similarity(t1: str, t2: str) -> float:
    return SequenceMatcher(None, t1, t2).ratio()


# ---- チェック関数 ----

def check_duplicate_fixed_visual(articles: list[dict]) -> list[dict]:
    """同スラグのfixed_visual版と通常版が両方 published の場合"""
    issues = []
    slug_map = defaultdict(list)
    for a in articles:
        if not is_published(a):
            continue
        slug_map[a["slug"]].append(a)

    # fixed_visual版のベーススラグを取得
    for slug, arts in slug_map.items():
        if slug.endswith("_fixed_visual"):
            base_slug = slug[: -len("_fixed_visual")]
            if base_slug in slug_map:
                base_arts = slug_map[base_slug]
                fv_art = arts[0]
                for base_art in base_arts:
                    issues.append({
                        "severity": "CRITICAL",
                        "category": "重複記事",
                        "message": (
                            f"fixed_visual版と通常版が両方 published=true\n"
                            f"  通常版: {base_art['filename']}  title={base_art['fm'].get('title','')}\n"
                            f"  FV版:   {fv_art['filename']}  title={fv_art['fm'].get('title','')}"
                        ),
                    })
    return issues


def check_similar_titles(articles: list[dict]) -> list[dict]:
    """タイトルが酷似する記事ペアを検出"""
    issues = []
    published = [a for a in articles if is_published(a)]
    for i, a in enumerate(published):
        t1 = a["fm"].get("title", "")
        if not t1:
            continue
        for b in published[i + 1 :]:
            t2 = b["fm"].get("title", "")
            if not t2:
                continue
            if t1 == t2:
                issues.append({
                    "severity": "CRITICAL",
                    "category": "重複記事",
                    "message": (
                        f"タイトル完全一致\n"
                        f"  {a['filename']}\n"
                        f"  {b['filename']}\n"
                        f"  title=「{t1}」"
                    ),
                })
            elif title_similarity(t1, t2) >= 0.85:
                issues.append({
                    "severity": "HIGH",
                    "category": "類似タイトル",
                    "message": (
                        f"タイトル類似度 {title_similarity(t1, t2):.0%}\n"
                        f"  {a['filename']} →「{t1}」\n"
                        f"  {b['filename']} →「{t2}」"
                    ),
                })
    return issues


def check_title_length(articles: list[dict]) -> list[dict]:
    """タイトル長チェック"""
    issues = []
    for a in articles:
        if not is_published(a):
            continue
        title = a["fm"].get("title", "")
        length = len(title)
        if length > 60:
            issues.append({
                "severity": "HIGH",
                "category": "SEO: タイトル長",
                "message": f"{length}文字（60文字超） {a['filename']}\n  title=「{title}」",
            })
        elif length < 20:
            issues.append({
                "severity": "MEDIUM",
                "category": "SEO: タイトル短",
                "message": f"{length}文字（20文字未満） {a['filename']}\n  title=「{title}」",
            })
    return issues


def check_topics_count(articles: list[dict]) -> list[dict]:
    """topics が2個以下"""
    issues = []
    for a in articles:
        if not is_published(a):
            continue
        topics = a["fm"].get("topics", [])
        if isinstance(topics, list) and len(topics) <= 2:
            issues.append({
                "severity": "MEDIUM",
                "category": "SEO: topics不足",
                "message": f"topics={topics} ({len(topics)}個) {a['filename']}",
            })
    return issues


def check_compound_topics(articles: list[dict]) -> list[dict]:
    """compound word の topics（スペースなし長い単語）"""
    issues = []
    for a in articles:
        if not is_published(a):
            continue
        topics = a["fm"].get("topics", [])
        if not isinstance(topics, list):
            continue
        for t in topics:
            # 15文字超の英数字のみの単語をcompound word候補とみなす
            if re.match(r'^[a-z0-9]{15,}$', t):
                issues.append({
                    "severity": "LOW",
                    "category": "SEO: compound topic",
                    "message": f"compound word疑い: topic='{t}'  {a['filename']}",
                })
    return issues


def check_type_ratio(articles: list[dict]) -> list[dict]:
    """tech:idea 比率チェック（目標 2:1）"""
    published = [a for a in articles if is_published(a)]
    tech = [a for a in published if a["fm"].get("type") == "tech"]
    idea = [a for a in published if a["fm"].get("type") == "idea"]
    total = len(published)
    ratio = len(tech) / len(idea) if idea else float("inf")

    issues = []
    issues.append({
        "severity": "INFO",
        "category": "記事種別統計",
        "message": (
            f"公開記事総数: {total}本 / tech: {len(tech)}本 / idea: {len(idea)}本 / "
            f"tech:idea = {ratio:.1f}:1  (目標 2:1)"
        ),
    })
    if ratio < 1.5:
        issues.append({
            "severity": "MEDIUM",
            "category": "コンテンツバランス",
            "message": f"idea記事の比率が高すぎます (tech:idea={ratio:.1f}:1、目標2:1以上)",
        })
    return issues


def check_short_articles(articles: list[dict]) -> list[dict]:
    """150行未満の短い記事"""
    issues = []
    for a in articles:
        if not is_published(a):
            continue
        if a["line_count"] < 150:
            issues.append({
                "severity": "MEDIUM",
                "category": "コンテンツ品質: 行数不足",
                "message": f"{a['line_count']}行（150行未満）{a['filename']}\n  title=「{a['fm'].get('title','')}」",
            })
    return issues


def check_heading_count(articles: list[dict]) -> list[dict]:
    """## 見出しが3個未満"""
    issues = []
    for a in articles:
        if not is_published(a):
            continue
        headings = re.findall(r'^#{1,2} .+', a["body"], re.MULTILINE)
        h2 = [h for h in headings if h.startswith("## ")]
        if len(h2) < 3:
            issues.append({
                "severity": "MEDIUM",
                "category": "コンテンツ品質: 見出し不足",
                "message": f"## 見出し {len(h2)}個（3個未満）{a['filename']}\n  title=「{a['fm'].get('title','')}」",
            })
    return issues


def check_lead_section(articles: list[dict]) -> list[dict]:
    """冒頭リード文チェック（はじめに/概要/前提が最初のh2より前にあるか）"""
    LEAD_KEYWORDS = ["はじめに", "概要", "前提", "introduction", "overview", "背景", "この記事について", "この記事では"]
    issues = []
    for a in articles:
        if not is_published(a):
            continue
        body = a["body"].strip()
        # 最初の ## 見出しの位置
        first_h2 = re.search(r'^## ', body, re.MULTILINE)
        if not first_h2:
            continue
        first_h2_pos = first_h2.start()
        first_h2_text = body[first_h2_pos:].split("\n")[0].lower()

        has_lead = any(kw in first_h2_text for kw in LEAD_KEYWORDS)
        # 本文の冒頭にリード文がある（h2の前に十分なテキストがある）
        preamble = body[:first_h2_pos].strip()
        has_preamble = len(preamble) > 100

        if not has_lead and not has_preamble:
            issues.append({
                "severity": "LOW",
                "category": "コンテンツ品質: リード文なし",
                "message": f"冒頭リード文なし（はじめに/概要セクション未検出）{a['filename']}\n  title=「{a['fm'].get('title','')}」",
            })
    return issues


def check_tech_without_code(articles: list[dict]) -> list[dict]:
    """type:tech なのにコードブロックがゼロ"""
    issues = []
    for a in articles:
        if not is_published(a):
            continue
        if a["fm"].get("type") != "tech":
            continue
        code_blocks = re.findall(r'```', a["body"])
        if len(code_blocks) == 0:
            issues.append({
                "severity": "HIGH",
                "category": "コンテンツ品質: techにコードなし",
                "message": f"type=tech なのにコードブロックゼロ {a['filename']}\n  title=「{a['fm'].get('title','')}」",
            })
    return issues


def check_duplicate_emoji(articles: list[dict]) -> list[dict]:
    """emoji が重複している記事ペア"""
    issues = []
    published = [a for a in articles if is_published(a)]
    emoji_map = defaultdict(list)
    for a in published:
        emoji = a["fm"].get("emoji", "")
        if emoji:
            emoji_map[emoji].append(a)

    for emoji, arts in emoji_map.items():
        if len(arts) > 1:
            filenames = "  " + "\n  ".join(f"{ar['filename']} 「{ar['fm'].get('title','')}」" for ar in arts)
            issues.append({
                "severity": "LOW",
                "category": "frontmatter: emoji重複",
                "message": f"emoji='{emoji}' が{len(arts)}記事で重複\n{filenames}",
            })
    return issues


def check_known_bugs(articles: list[dict]) -> list[dict]:
    """既知バグのチェック"""
    issues = []
    for a in articles:
        if not is_published(a):
            continue
        slug = a["slug"]
        title = a["fm"].get("title", "")
        # cursor-vs-claude-code_fixed_visual のタイトルが「プロジェクトルール」バグ
        if slug == "cursor-vs-claude-code_fixed_visual" and "プロジェクトルール" in title:
            issues.append({
                "severity": "CRITICAL",
                "category": "frontmatter: タイトルバグ",
                "message": (
                    f"cursor-vs-claude-code_fixed_visual のタイトルが「{title}」になっている"
                    f"（明らかなバグ）{a['filename']}"
                ),
            })
    return issues


# ---- 統計情報 ----

def print_stats(articles: list[dict]):
    published = [a for a in articles if is_published(a)]
    fv_published = [a for a in published if "_fixed_visual" in a["slug"]]
    normal_published = [a for a in published if "_fixed_visual" not in a["slug"]]

    print("=" * 70)
    print("📊 記事統計")
    print("=" * 70)
    print(f"  総記事数（全ファイル）: {len(articles)}")
    print(f"  published=true 総数 : {len(published)}")
    print(f"    うち fixed_visual版: {len(fv_published)}")
    print(f"    うち 通常版        : {len(normal_published)}")

    tech = [a for a in published if a["fm"].get("type") == "tech"]
    idea = [a for a in published if a["fm"].get("type") == "idea"]
    print(f"  type=tech: {len(tech)}本 / type=idea: {len(idea)}本")
    if idea:
        print(f"  tech:idea比率 = {len(tech)/len(idea):.1f}:1")


# ---- メイン ----

SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}

def main():
    print("Loading articles...")
    articles = load_articles()
    print(f"Loaded {len(articles)} articles.\n")

    print_stats(articles)
    print()

    all_issues = []

    # 1. 重複記事
    all_issues += check_duplicate_fixed_visual(articles)
    all_issues += check_similar_titles(articles)
    all_issues += check_known_bugs(articles)

    # 2. SEO
    all_issues += check_title_length(articles)
    all_issues += check_topics_count(articles)
    all_issues += check_compound_topics(articles)
    all_issues += check_type_ratio(articles)

    # 3. コンテンツ品質
    all_issues += check_short_articles(articles)
    all_issues += check_heading_count(articles)
    all_issues += check_lead_section(articles)
    all_issues += check_tech_without_code(articles)

    # 4. frontmatter品質
    all_issues += check_duplicate_emoji(articles)

    # ソート（重大度順）
    all_issues.sort(key=lambda x: SEVERITY_ORDER.get(x["severity"], 99))

    # カテゴリ別集計
    counts = defaultdict(int)
    for issue in all_issues:
        counts[issue["severity"]] += 1

    print("=" * 70)
    print("🔍 監査結果サマリー")
    print("=" * 70)
    for sev in ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]:
        if counts[sev]:
            print(f"  {sev}: {counts[sev]}件")
    print()

    # 詳細出力
    current_sev = None
    for issue in all_issues:
        sev = issue["severity"]
        if sev != current_sev:
            print("=" * 70)
            print(f"【{sev}】")
            print("=" * 70)
            current_sev = sev
        print(f"[{issue['category']}]")
        print(f"  {issue['message']}")
        print()


if __name__ == "__main__":
    main()
