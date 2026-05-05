---
title: "LLM-jp-4の技術解剖：日本語MT-BenchでGPT-4oを超えた4つの理由"
emoji: "🇯🇵"
type: "tech"
topics: ["llm", "ai", "machinelearning", "japanese"]
published: false
status: "draft"
publication_name: "correlate_dev"
---

## はじめに

2026年4月3日、国立情報学研究所（NII）が国産LLM「LLM-jp-4」を公開しました。発表の中でひときわ注目を集めたのが「日本語MT-BenchでGPT-4oを上回るスコアを達成した」という主張です。

ただし、この主張には重要な限定条件があります。

**「GPT-4oを超えた」が意味すること**:
- ✅ 日本語MT-BenchおよびMT-Bench（英語）の2指標において、LLM-jp-4 32B-A3BがGPT-4oを上回るスコアを達成（NII公式発表）
- ❌ コーディング・数学的推論・長文理解などの指標での優位性は未評価
- ⚠️ NIIによる自己評価であり、独立第三者機関による再現検証は現時点で限定的

これらの前提を踏まえた上で、LLM-jp-4がなぜそのスコアを達成できたのか、技術的な観点から解説します。ライセンスはApache 2.0で、商用利用・ファインチューニング・再配布がすべて無償で可能です。

## LLM-jp-4とは何か

LLM-jp-4は、NIIの大規模言語モデル研究開発センター（[LLMC](https://llmc.nii.ac.jp/)）が中心となり、研究機関・企業・官公庁が参加する産学官連携コミュニティ「LLM勉強会」がフルスクラッチで開発した日本語LLMです。

公開されたのは以下の2モデルです（出典: [NII公式プレスリリース](https://www.nii.ac.jp/news/release/2026/0403.html)）。

| 項目 | LLM-jp-4 8B | LLM-jp-4 32B-A3B |
|------|-------------|-----------------|
| 総パラメータ | 約8.6B（86億） | 約32B（320億） |
| アクティブパラメータ | 8.6B（Dense） | 3.8B |
| アーキテクチャ | Llama 2系 Dense | Qwen3 MoE（128 Expert / Top-8） |
| コンテキスト長 | 最大65,000 tokens | 最大65,000 tokens |
| ライセンス | Apache 2.0 | Apache 2.0 |

前世代のLLM-jp-3と比較して、学習コーパス規模は約6倍に拡大しています。学習データには前世代比6倍となる約10.5兆トークンを使用し、さらに合成データを含む約1.2兆トークンの中間学習（annealing）フェーズを経ています。

## ベンチマーク結果の正確な読み方

### MT-Benchスコア（出典: NII公式プレスリリース）

MT-Benchは、LLMの「会話能力」を測る評価指標です。マルチターンの対話品質を第三者モデル（今回はGPT-4系）が採点する形式で、10点満点でスコアが算出されます。

**日本語MT-Bench（llm-jp-judge / GPT-4評価）**

```
1位: LLM-jp-4 32B-A3B  7.82  ← 2026-04-03 公開
2位: LLM-jp-4 8B       7.54  ← 2026-04-03 公開
3位: GPT-4o             7.29
4位: Qwen3-8B           7.14
```

**英語MT-Bench（同上）**

| モデル | 英語MT-Bench |
|--------|--------------|
| LLM-jp-4 32B-A3B | ★7.86 |
| LLM-jp-4 8B | ★7.79 |
| GPT-4o | 7.69 |

注目すべき点は、英語MT-Benchでも両モデルがGPT-4oを上回っていることです。日本語特化を意図したモデルが英語でも高スコアを記録した背景については、後述のアーキテクチャ選択（Qwen3 MoE）が影響している可能性があります。

また、42種類の評価タスクを使用する「llm-jp-eval v2.1.3」では、LLM-jp-4はgpt-oss-20b・Qwen3-8Bと同等水準の日本語性能を確認していますが、タスク別の具体的数値スコアは現時点で公開されていません。

### MT-Benchで測れていないこと

MT-Benchはあくまで会話能力の一指標です。以下のような能力については、本記事執筆時点（2026年4月）で独立した評価データが公開されていません。

- コーディング能力（HumanEval / MBPP相当）
- 数学的推論（GSM8K / MATH相当）
- 長文理解・RAGとの組み合わせ性能

「総合的にGPT-4oを超えた」と解釈するのは過剰な主張です。現時点では「日本語・英語の会話品質においてGPT-4oと比肩、もしくは上回る可能性がある国産モデルが登場した」と評価するのが適切です。

## 技術的ブレイクスルーの4要因

### 要因1: 19.5兆トークンと日本語の戦略的オーバーサンプリング

学習データのプールは約19.5兆トークンで、内訳は以下のとおりです（出典: [gihyo.jp](https://gihyo.jp/article/2026/04/llm-jp-4)）。

| 言語 | トークン数 |
|------|-----------|
| 英語 | 約17.8兆 |
| 日本語 | 約7,000億 |
| その他（中国語・韓国語等） | 約8,500億 |
| プログラムコード | 約2,000億 |

このプールから最適なサンプリング比で約10.5兆トークンを選択して事前学習に使用しています。

ここで重要なのが**日本語のオーバーサンプリング戦略**です（出典: [NIIエグゼクティブサマリ](https://yorozuipsc.com/uploads/1/3/2/5/132566344/d293ab1057895435b7f0.pdf)）。

```
コーパス内の日本語比率:  3.5%（約7,000億 / 約19.5兆）
訓練時の実効サンプリング比率: 15.9%（約4.5倍にブースト）
```

英語を中心とした大規模コーパスを使用しながら、訓練時のサンプリング比率を調整することで日本語能力を意図的に強化しています。大量の英語データで汎用的な言語理解能力を獲得しつつ、日本語への特化を両立させるアプローチです。

さらに、政府文書・国会議事録・LLM-jp Corpus v4（日本語6,880億トークン含む）など、信頼性の高い一次情報源を組み込んでいます。インターネットのクロールデータだけでなく、公的文書という高品質なデータが日本語の出力品質に寄与していると考えられます。

### 要因2: MoEアーキテクチャ：「32Bの頭脳・3.8Bの財布」

32B-A3Bモデルが採用するMixture of Experts（MoE）アーキテクチャは、推論コストを抑えながら高い表現力を維持する設計思想です（出典: [gihyo.jp](https://gihyo.jp/article/2026/04/llm-jp-4)）。

```
Dense 32Bモデル: 推論時に32B全パラメータを使用
MoE 32B-A3Bモデル: 128のエキスパートから8つ（3.8B相当）を動的選択
→ 推論コストは実質3.8Bモデル相当、表現力は32Bモデル規模を維持
```

具体的な仕様は以下のとおりです。
- **ベースアーキテクチャ**: Qwen3 MoEをベースに採用
- **Expert総数**: 128
- **1推論あたりの活性Expert数**: Top-8
- **総パラメータ**: 32B / **アクティブパラメータ**: 3.8B

各入力トークンに対して、128のエキスパートの中から最も適切な8つが自動で選択・活性化されます。これにより、言語知識・コーディング知識・専門知識などを異なるエキスパート群が担当する専門分化が自然に発生します。英語MT-Benchでも高スコアを記録した背景には、MoEによる「英語専用エキスパート」と「日本語専用エキスパート」の自然な役割分担が機能している可能性があります。

### 要因3: SFT + DPOによるアライメント：強化学習を使わない2段階設計

事前学習済みモデルを実際の対話用途に整えるポストトレーニングは、2段階で実施されています（出典: [Hugging Face DPOデータセット](https://huggingface.co/datasets/llm-jp/llm-jp-4-32b-a3b-thinking-dpo-data)）。

**Phase 1: SFT（Supervised Fine-Tuning）**
22種類のインストラクションチューニングデータを使用して、基礎的な指示追従能力を確立します。

**Phase 2: DPO（Direct Preference Optimization）**
gpt-oss-120bを評価者として使用し、「選好ペア（chosen / rejected）」を学習します。人間の好みに近い出力を選好するよう直接最適化します。

特筆すべき点は、**RLHF（人間からのフィードバックによる強化学習）を使用していない**ことです。RLHFは報酬モデルの設計・学習が複雑で計算コストが高く、報酬ハッキングのリスクもあります。DPOはこれをシンプルかつ安定した最適化問題に変換し、計算コストを抑えながら高品質なアライメントを実現します。

また、Thinkingモデルでは `reasoning_low / medium / high` の3段階で思考量を制御できます。ユースケースに応じてレイテンシと回答品質のトレードオフを調整できる設計です。

### 要因4: 産学官連携による一次情報源コーパスの確保

LLM-jp-4の開発は、NIIが主導する産学官連携コミュニティ「LLM勉強会（[LLMC](https://llmc.nii.ac.jp/)）」によるものです。研究機関・民間企業・官公庁が参加し、コーパスの収集・品質評価・学習の各フェーズで協力しています。

このコミュニティ体制がもたらす最大の利点は、**インターネットクロールだけでは得られない一次情報源へのアクセス**です。国会議事録・政府文書・学術論文・法令データベースなど、信頼性と品質が担保されたテキストを体系的に組み込めます。ウェブ上の雑多なテキストと比較して、こうした一次情報源は正確な日本語表現・専門知識・論理的な文体を含んでいます。

コーパスはオープンな形で「[LLM-jp Corpus v4](https://llmc.nii.ac.jp/topics/llm-jp-corpus-v4/)」としても公開されており、研究コミュニティへの貢献も兼ねています。

## 国産LLM勢力図2026

LLM-jp-4の公開により、国産LLMのランドスケープはどう変わったのでしょうか。

| モデル | 開発元 | パラメータ | 日本語MT-Bench | ライセンス | 特徴 |
|--------|--------|-----------|---------------|-----------|------|
| LLM-jp-4 32B-A3B | NII | 32B（MoE）/ 3.8B active | ★7.82 | Apache 2.0 | フルスクラッチ・産学官 |
| LLM-jp-4 8B | NII | 8.6B（Dense） | ★7.54 | Apache 2.0 | フルスクラッチ |
| PLaMo 3.0 Prime | Preferred Networks | 非公開 | 未公開 | 非公開 | 長考機能（初の国産Thinking） |
| Swallow-120B-RL | 東京工業大学 | 120B | 参照注1 | Apache 2.0 | GPT-oss-Swallow系 |
| CALM3-22B-Chat | CyberAgent | 22B | 未公開 | Apache 2.0 | 日本語特化SFT |

注1: Swallowの評価は[Nejumi Leaderboard 4](https://swallow-llm.github.io/evaluation/index.ja.html)（正規化スコア方式）で行われており、MT-Benchスコアとの直接比較はできません。

**各モデルの差別化ポイント**:
- **LLM-jp-4**: フルスクラッチかつ産学官連携。Apache 2.0でオンプレ商用利用が完全無償
- **PLaMo 3.0 Prime**: 国産初のThinkingモデル機能を搭載（2026年3月、βリリース）
- **Swallow**: Llama継続学習方式。120Bの大規模モデルで長文・専門知識に強み
- **CALM3**: CyberAgentの独自コーパスを活用した商用志向のモデル

LLM-jp-4の特異点は、**フルスクラッチかつApache 2.0**という組み合わせです。PLaMoやSwallowがそれぞれのライセンス制約を持つ中、LLM-jp-4はオンプレミス環境での商用展開において最も制約が少ないモデルとなっています。

## 実際に動かす方法

LLM-jp-4はHugging Faceで公開されており、`transformers`ライブラリから利用できます（[モデルコレクション](https://huggingface.co/collections/llm-jp/llm-jp-4-models)）。

### モデルバリアントとVRAM目安

| モデルID | 用途 | VRAM目安 |
|---------|------|---------|
| `llm-jp/llm-jp-4-8b-base` | 事前学習済みベース（ファインチューニング向け） | 16GB |
| `llm-jp/llm-jp-4-8b-thinking` | 推論・対話（Thinking付き） | 16GB |
| `llm-jp/llm-jp-4-32b-a3b-thinking` | 高性能対話（MoE・推論コスト抑制） | 24GB（bfloat16） |

### 推論コード例

```python
import torch
from transformers import AutoTokenizer, AutoModelForCausalLM

# 8Bモデル（ローカル推論向け）
model_name = "llm-jp/llm-jp-4-8b-thinking"
# MoEモデル（高性能・推論コスト抑制）
# model_name = "llm-jp/llm-jp-4-32b-a3b-thinking"

tokenizer = AutoTokenizer.from_pretrained(model_name)
model = AutoModelForCausalLM.from_pretrained(
    model_name,
    device_map="auto",
    torch_dtype=torch.bfloat16,
    trust_remote_code=True,
)
model.eval()

messages = [{"role": "user", "content": "自然言語処理とは何ですか？"}]
text = tokenizer.apply_chat_template(
    messages, tokenize=False, add_generation_prompt=True
)
inputs = tokenizer(text, return_tensors="pt").to(model.device)

with torch.no_grad():
    output = model.generate(
        **inputs, max_new_tokens=512, temperature=0.7, top_p=0.95
    )

print(tokenizer.decode(output[0], skip_special_tokens=True))
```

32B-A3BモデルはROCm環境での第三者ベンチマークで、Qwen3.5比41%高速という報告もあります（出典: [lilting.ch ROCmベンチマーク](https://lilting.ch/en/articles/llm-jp-4-32b-a3b-rocm-benchmark)）。MoEによる推論コスト削減効果が顕著に現れています。

ファインチューニングを行う場合は、NIIが提供するチューニングスクリプト（[llm-jp-dpo](https://github.com/llm-jp/llm-jp-dpo)、NeMo-Alignerベース）とDPOデータセット（[llm-jp-4-32b-a3b-thinking-dpo-data](https://huggingface.co/datasets/llm-jp/llm-jp-4-32b-a3b-thinking-dpo-data)）が公開されています。

## 産業応用への期待と今後の展望

### Apache 2.0がもたらす産業展開の可能性

Apache 2.0ライセンスは、金融・医療・官公庁など情報管理が厳格なセクターにとって特に重要です。クラウドAPIへのデータ送信を避けながら、オンプレミス環境で本番運用できます。

コンテキスト長65,000トークンは、長大な契約書・医療カルテ・会議議事録などを1コンテキストで処理できる実用的な長さです。産学官連携で構築された日本語コーパスの品質は、行政文書処理や専門分野への適用において強みになると期待されます。

### 今後の公開予定モデル

| 時期 | モデル | 概要 |
|------|--------|------|
| 2026年度中（予定） | LLM-jp-4 32B（Dense） | 32B全密モデル。推論精度重視用途向け |
| 2026年度中（予定） | LLM-jp-4 332B-A31B | 総3,320億・アクティブ310億のMoE超大規模モデル |
| 2026年度中（予定） | 軽量モデル | 詳細未公開（エッジ・スマートフォン向けの可能性）|

（出典: [NII公式プレスリリース](https://www.nii.ac.jp/news/release/2026/0403.html)）

特に注目は**LLM-jp-4 332B-A31B**です。アクティブパラメータが310億規模になることで、現行モデルとは質的に異なる推論能力が期待されます。ただし、このモデルが現行と同じコーパスを使用するか、追加データを組み込むかは現時点で未公表です。

### 残された課題

国産LLMの成熟という観点から、以下の課題が残っています。

1. **コーディング・数学推論の独立評価**: HumanEval / MBPP / GSM8K等での結果が未公開。GPT-4oとの対比を語るには不可欠なデータです
2. **独立第三者による再現検証**: 自己評価としてのベンチマークの信頼性向上のため、外部機関による追試が望まれます
3. **評価者モデルの透明性**: MT-Bench評価で使用されたGPT-4のバージョン（Turbo? GPT-4o?）が非開示であり、評価の公平性に影響する可能性があります

## まとめ

LLM-jp-4がMT-Benchで高スコアを達成した技術的背景を整理すると、以下の4要因が挙げられます。

1. **日本語4.5倍オーバーサンプリング**: 英語中心の大規模コーパスを使いながら、サンプリング調整で日本語能力を戦略的に強化
2. **MoEアーキテクチャ**: 128エキスパート / Top-8選択により、32Bの表現力を3.8Bの推論コストで実現
3. **SFT + DPOの2段階アライメント**: 強化学習なしでシンプル・安定・低コストなアライメントを実現
4. **産学官連携による一次情報源コーパス**: 国会議事録・政府文書など高品質な日本語一次情報源の体系的活用

タイトルに掲げた「GPT-4oを超えた」は、日本語・英語MT-Benchという会話能力の指標に限定した評価です。総合的な優位性を主張するものではありません。

しかし、この限定的な領域であっても、国産モデルがGPT-4oと比肩するスコアを達成した事実は、国産LLMの到達点として大きな意義があります。Apache 2.0ライセンスで商用利用・オンプレ展開が完全に開かれており、2026年度中に予定されている332B-A31Bモデルが公開された際には、より広範な評価での実力が明らかになるでしょう。

---

## 参考情報

- NII公式プレスリリース: https://www.nii.ac.jp/news/release/2026/0403.html
- NII LLMC公式サイト: https://llmc.nii.ac.jp/
- LLM-jp Corpus v4: https://llmc.nii.ac.jp/topics/llm-jp-corpus-v4/
- Hugging Faceモデルコレクション: https://huggingface.co/collections/llm-jp/llm-jp-4-models
- DPOデータセット: https://huggingface.co/datasets/llm-jp/llm-jp-4-32b-a3b-thinking-dpo-data
- gihyo.jp技術解説: https://gihyo.jp/article/2026/04/llm-jp-4
- チューニングコンペティション: https://llm-jp.github.io/tuning-competition/
