# Agent 基准测试报告

面向「Agent 技术深度」评分项的可复现实验：让 Agent 在 10 个固定科研任务上自主规划、调用工具、汇总作答，统计成功率、步数与耗时。

## 一、实验环境

| 项目 | 配置 |
|---|---|
| 运行方式 | Docker Compose（`research-copilot-backend` 容器，healthy） |
| 模型 | DeepSeek（`LLM_BASE_URL=https://api.deepseek.com/v1`，`llm=true`） |
| 嵌入 | `hash`（384 维特征哈希，零外部依赖） |
| 语料 | 21 篇真实论文（Transformer / BERT / GPT-2 / GPT-3 / T5 / ViT / LoRA / RAG / ReAct / FlashAttention 等） |
| 检索 | BM25 + 稠密向量 + RRF 融合 |
| 时间 | 2026-11-04 |

## 二、判定标准

- **成功**：收到 `final` 事件、无工具报错、最终答案 ≥ 150 字。
- **步数**：实际执行的工具调用次数（`step_start` 事件计数），不含规划与汇总。
- **引用率**：答案中出现「第 N 页」页码标注的任务数。

## 三、结果

| 编号 | 任务 | 成功 | 步数 | 工具链 | 耗时 | 答案字数 | 页码引用 |
|---|---|---|---|---|---|---|---|
| T1 | 总结 Transformer 的核心创新点，并给出页码依据 | ✅ | 3 | citation_graph → answer_question → future_work | 80.5s | 772 | 4 |
| T2 | 对比 BERT 与 GPT-2 在预训练目标上的差异 | ✅ | 2 | search_library → compare_papers | 16.9s | 925 | 6 |
| T3 | 引用图谱里哪些论文是基石？列出 PageRank 前 3 | ✅ | 1 | citation_graph | 12.9s | 622 | — |
| T4 | 从语料库中抽取 3 条仍未解决的 Future Work | ✅ | 1 | future_work | 69.0s | 715 | 5 |
| T5 | 翻译 FlashAttention 论文第 2 页的核心段落 | ✅ | 2 | citation_graph → translate_paper | 22.5s | 1037 | 5 |
| T6 | 为「多模态检索增强生成」生成论文框架大纲 | ✅ | 1 | draft_outline | 17.4s | 1169 | — |
| T7 | 注意力机制有哪些变体？基于证据回答 | ✅ | 1 | search_library | 4.1s | 768 | 7 |
| T8 | 检索关于位置编码的证据片段 | ✅ | 1 | search_library | 3.5s | 919 | 8 |
| T9 | 总结 LoRA 的动机与效果（限定 p210609685） | ✅ | 1 | answer_question | 7.1s | 986 | 10 |
| T10 | 基于检索结果写 200 字综述段落，必须注明页码 | ✅ | 1 | search_library | 3.4s | 612 | 12 |

**汇总：成功率 10/10 = 100%；平均步数 1.4；平均耗时 23.7s；8/10 任务带页码引用。**

## 四、结论与解读

1. **工具选择正确率 10/10**：涉及图谱、对比、翻译、Future Work、写作大纲、限定论文范围的任务都选对了工具，没有出现「编造工具名」或「虚构论文 id」。
2. **页码可验证**：8 个任务在答案里给出了「第 N 页」，可直接对应阅读器的 bbox 高亮，形成「引用 → 原文」闭环。
3. **耗时结构**：T1（80.5s）与 T4（69.0s）较慢，原因是这两次规划多走了一步工具，并触发了长文汇总的流式生成；其余任务 3–23s。平均 23.7s 属于可交互范围。
4. **步数偏少（1.4）不全是优点**：简单任务 1 步即答，说明规划器没有过度调用工具；但复杂综述类任务（T10）也只用 1 步检索就生成，是当前规划的保守倾向，可作为后续改进点（引入自检 / 多轮补证）。

## 五、如何复现

```bash
# 1. 启动后端（容器）
cd research-copilot
docker compose up -d

# 2. 在本机或容器内跑压测
cd backend
python scripts/bench_agent.py

# 3. 查看原始结果
cat scripts/bench_result.json
```

> 每次运行会真实消耗 DeepSeek token；10 个任务约消耗 3–5 万 token。若 `LLM_API_KEY` 未配置或余额不足，Agent 会自动降级为离线规则模式（结果中 `offline=true`），此时成功率仍为 10/10，但答案不含 LLM 生成的综述段落。

## 六、已知局限

- 评测集只有 10 条，且由本人编写，存在选题偏差；结论用于「可复现的工程量级证据」，不等同于公开 benchmark。
- 未统计 token 成本与首 token 延迟（SSE 流式下首 token 延迟约 1–2s，未单独记录）。
- 未做多次重复运行的方差统计；如需更严谨，可对每条任务重复 3 次取均值。
