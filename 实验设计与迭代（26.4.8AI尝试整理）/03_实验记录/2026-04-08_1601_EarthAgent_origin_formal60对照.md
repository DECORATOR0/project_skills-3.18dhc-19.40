# 2026-04-08 `Earth-Agent origin` `formal60` 对照

## 1. 这轮实验的职责

这篇记录的是 `Earth-Agent origin` 在同批 `formal60` 题上的对照结果。

这条线的价值不在于替代当前主线，而在于回答：

- 原生单 agent + 全工具轨迹，在当前 `Qwen3-8B` 条件下到底能跑成什么样

---

## 2. 关键目录

本轮正式目录不在当前 repo 内，而在：

- `/data/xsy/Earth-Agent-origin/evaluate_langchain/qwen3_8b_shlab_AP_formal60_20260408_160119`

汇总文件：

- `/data/xsy/Earth-Agent-origin/evaluate_langchain/qwen3_8b_shlab_AP_formal60_20260408_160119/run_summary.json`
- `/data/xsy/Earth-Agent-origin/evaluate_langchain/qwen3_8b_shlab_AP_formal60_20260408_160119/merged_results_summary.json`

---

## 3. 运行层结果

旧记录里给出的运行层结果是：

- 总题数：`60`
- 成功跑完：`57`
- 运行失败：`3`

其中明确区分出的失败原因：

- `158`：`InputContextLimitExceeded`
- `23`：`429 insufficient_quota`
- `81`：`429 insufficient_quota`

因此真正能直接归因到上下文爆掉的，只有：

- `1 / 60`

---

## 4. 简单准确率口径

旧记录里对 `Autonomous Planning` 做的轻量核对给出：

- 严格 exact：`12 / 60`
- 轻微归一化后：`13 / 60`

所以这条线最不该被误读的地方是：

- `57 / 60` 表示成功跑完数
- 它只是“成功跑完数”

更真实的概括是：

- 原生架构在当前模型下，大部分题能把流程跑起来
- 但真正答对的只有 `12~13 / 60`

---

## 5. 这轮最重要的失败形态

旧记录把这轮失败总结得很清楚：

### 5.1 `No answer found` 很多

在 `57` 个成功跑完的题里，有：

- `24` 题被记成 `No answer found`

也就是说，这条线最主要的问题之一集中在：

- 工具跑过了，但最终答案没有稳定收回来

### 5.2 三种典型 failure pattern

#### A. 第一跳就空

特征：

- 只有 `user`
- 没有稳定的 `assistant/tool`

解释更像：

- 流式回写没形成可提取最终内容

#### B. 生成了 tool call，但参数是伪数据

典型表现：

- 没先取数
- 直接把“应该存在的数据列表”写成参数

这说明：

- 规划存在
- grounding 不成立

#### C. 工具都跑出来了，但没有 final answer closing

典型表现：

- 先纠偏文件名
- 工具结果也出来了
- 但最后停在 tool result，没有回 `A/B/C/D`

这一类对当前主线最有参考价值，因为它说明：

- answer closing 是单独的失败点

---

## 6. 这轮对当前主线最大的启示

### 6.1 上下文只是这条线的一部分问题

从这次结果看，很难把问题总结成：

- “原生架构不行，因为老爆上下文”

因为真正明确爆 context 的只有 `1` 题。

更准确的说法是：

- 起步不稳
- 参数不 grounded
- final answer closing 不稳

才是更主流的问题。

### 6.2 `成功跑完` 和 `答对` 必须严格分开

这条对当前 `nlrl_skills` 也有提醒意义：

- 不要把“完成了多步轨迹”误当成“已经解决了题”

### 6.3 原生对照并没有压过当前 `v8` 主线

如果只看简单准确率，这条线大致是：

- `12~13 / 60`

而当前 `v8` token-budget 重跑已经到：

- `24~25 / 60`

所以当前证据并不支持一个结论：

- “回到原生单 agent 架构会更好”

---

## 7. 当前最稳的一句话

`Earth-Agent origin` 这条对照线说明的是：

- 小模型下，原生单 agent 架构并不会自然解决当前问题

它真正暴露出来的，是另一类同样重要的瓶颈：

- grounded tool arguments
- final answer closing

这两点后面也应该反过来指导当前主线的继续优化。
