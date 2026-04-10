# 2026-04-08 `no-skill / all-tools` 基线

## 1. 这轮实验想回答什么

这轮看的是一个更底的对照问题：

- 如果完全不激活 skill，只把真实 `104` 个 MCP 工具全部暴露给 executor，`formal60` 会怎样

这条线的作用是给当前 `v8` 聚合主线提供下限参考。

---

## 2. 关键目录

### 2.1 抽样 `10` 题

- `runs/no_skill_all104_formal60_sample10_remoteqwen_20260408`

### 2.2 剩余 `50` 题

- `runs/no_skill_all104_formal60_rest50_remoteqwen_c20_20260408`

### 2.3 静态 prompt 探针

旧观察里还用了本地探针脚本：

- `scripts/inspect_no_skill_prompt_budget.py`

它的作用是量：

- `104` 工具全暴露时，首轮 `step1` 静态 prompt 的精确 token 数

---

## 3. full `60` 合并结果

把 `sample10 + rest50` 合起来，当前这条基线可以记成：

- `task_count = 60`
- `success_count = 14`

加权后的平均指标：

| 指标 | 数值 |
| --- | ---: |
| `Accuracy` | `0.2333` |
| `TAO` | `0.2044` |
| `TIO` | `0.2347` |
| `TEM` | `0.0000` |
| `Parameters` | `0.0000` |
| `Efficiency` | `0.4828` |

证据：

- `runs/no_skill_all104_formal60_sample10_remoteqwen_20260408/evaluation_summary.json`
- `runs/no_skill_all104_formal60_rest50_remoteqwen_c20_20260408/evaluation_summary.json`

---

## 4. 这条基线和 `v8` 主线怎么对照

把几条最关键的线放在一起：

| 线别 | success | ACC |
| --- | ---: | ---: |
| `no-skill / all-tools` | `14 / 60` | `0.2333` |
| `v8 flat` 首轮旧口径 | `15 / 60` | `0.2500` |
| `v8 tree` 首轮旧口径 | `18 / 60` | `0.3000` |
| `v8 flat` token-budget | `24~25 / 60` | `0.4000~0.4167` |
| `v8 tree` token-budget | `25 / 60` | `0.4167` |

最重要的结论是：

- skill library 有实际作用

因为一旦把 skill 全部拿掉，只剩“全工具开放”，效果并不会自然接近当前 `v8` token-budget 主线。

---

## 5. 这轮里最有价值的观察落在成功率之外

旧观察里其实有一个更重要的发现：

- 静态首轮 prompt 长度
- 和真实多步运行时 provider usage

两者差得很大。

### 5.1 静态探针的结论

在 `earth-bench-c-1` 上，这组静态探针记录到的精确结果是：

- `tool_count = 104`
- `tools_json_chars = 27403`
- `tools_json_tokens = 7803`
- `system_message_chars = 30551`
- `system_message_tokens = 8498`
- `user_prompt_chars = 2031`
- `user_prompt_tokens = 734`
- `step1_prompt_tokens = 9245`
- 相对 `32768 input budget` 的静态剩余量是 `23523`

这组数只回答一个问题：

- 把 `104` 个真实 MCP 工具的 schema 全塞进首轮 `system + user` 后，静态首轮到底有多长

因此如果只看这个探针，很容易形成一种错觉：

- “104` 工具虽然长，但离 `32K` 还远”

### 5.2 动态抽样直跑的结论

但同一天的 `sample10` 直跑给出的是真实 provider usage。把 `10` 题下所有 `26` 次 executor 调用逐个统计后，精确结果是：

- 全部 `26` 次调用：
  - `input_tokens` 平均值 `30197.04`
  - `input_tokens` 最小值 `29605`
  - `input_tokens` 最大值 `31355`
- 如果只看每题第 `1` 步的 `10` 次首轮调用：
  - `input_tokens` 平均值 `29941.5`
  - `input_tokens` 最小值 `29605`
  - `input_tokens` 最大值 `30407`

而且旧观察还明确指出：

- 这 `10` 题里并没有真实触发当前 executor 的显式 token-budget 截断
- 也就是说，很多 request 天然就会长到 `30K+`，到达这个量级并不依赖截断

这条观察非常重要，因为它说明：

- 当前 memory / packing 问题不能简单归因成“只是工具表太长”

所以这里真正该分开的两个准数是：

- 静态首轮 `step1_prompt_tokens = 9245`
- 动态真实调用 `input_tokens`：
  - 首轮 `step1` 平均 `29941.5`
  - 全部 `26` 次调用平均 `30197.04`

更真实的结构是：

- 工具表
- 系统协议
- 题面
- 多步 tool result

一起把 request 顶长了。

---

## 6. 这轮基线对当前主线意味着什么

### 6.1 不要再期待“全工具开放”自动优于 skill-guided

这条基线已经给出很直接的答案：

- 不会

至少在当前 `Qwen3-8B` 条件下，完全放开工具空间并不能自动替代 skill prior。

### 6.2 skill 的作用不只是限工具，更是缩搜索空间

从这条线和 `Earth-Agent origin` 的对照也能看出：

- 只要模型直接面对过大的开放动作空间
- grounded planning 与 answer closing 都会变得更不稳

所以当前 skill 的价值，不只是：

- `allowed-tools`

更在于：

- 提供问题分解先验
- 减少模型在无约束状态下的搜索负担

### 6.3 这条基线反而更支持继续优化 skill 注入

因为现在最值得优化的是：

- 保留 skill 的前提下，怎么把 skill 注入做得更瘦、更有用

---

## 7. 当前最稳的一句话

`no-skill / all-tools` 基线告诉我们的结论是：

- 在当前模型和当前任务上，没有 skill 的开放空间本身就是成本极高的条件

因此后续主线不应往“去掉 skill”走，而应往：

- 更轻、更准、更不挤窗口的 skill 注入

上继续推进。
