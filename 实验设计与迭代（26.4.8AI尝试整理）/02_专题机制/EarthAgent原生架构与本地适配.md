# Earth-Agent 原生架构与本地适配

## 1. 这篇文档的职责

这篇记录的是 `Earth-Agent origin` 这条对照线里，相对稳定的机制认识：

- 原始架构是什么形态
- 本地为了让它可跑，做了什么最小适配
- 它和当前 `nlrl_skills` 主线相比，最本质的差别是什么

具体 `formal60` 对照结果放在：

- `03_实验记录/2026-04-08_1601_EarthAgent_origin_formal60对照.md`

---

## 2. 原生架构的核心范式

从本地复现和原始代码观察看，`Earth-Agent` 更接近典型的单线程 `ReAct` agent：

1. LLM 读取当前问题
2. 发起 tool call
3. 读取 tool result
4. 再根据累积历史继续决定下一步

它的“记忆”本质上是：

- 同一条会话历史不断累积

当前 `nlrl_skills` 则会把下面这些部分拆开：

- route
- skill activation
- tool envelope
- actor / critic

拆成多块可控组件。

---

## 3. 原生架构默认没有显式上下文保护

整理旧观察后，当前可以比较稳地认为：

- 原始实现里没有明显的 token budgeting
- 没有显式 message 压缩
- 没有旧步骤裁剪
- 没有摘要回写

因此在 provider 不额外兜底时，长轨迹题理论上天然更接近上下文风险边界。

这也是为什么把它拉来做对照时，一个关键问题会变成：

- 它在没有复杂本地包装器的情况下，到底能把多少题自然跑完

---

## 4. 本地适配采用“最小兼容”原则

本次本地复现的原则很清楚：

- 尽量保留原生架构
- 只补本地运行与当前 provider 口径所必需的内容

旧文档里明确提到的适配方向包括：

- 把模型收口到这次实验实际可用的 `Qwen3-8B`
- 补 `stream=True + enable_thinking=True`
- 补本地路径与环境变量处理
- 提供可以直接筛题与并发跑 `formal60` 的最小入口

因此这条对照线的意义在于：

- 让原生范式在同一数据和同一模型条件下可以被直接比较

---

## 5. 它和当前 `nlrl_skills` 主线最本质的差别

### 5.1 `Earth-Agent origin`

重点是：

- 单 executor
- 工具调用闭环
- 历史消息累积
- 靠 agent 自己把规划、参数 grounding、answer closing 一条链收回来

### 5.2 `nlrl_skills`

重点是：

- 先 route 到 skill
- skill 提供更强的先验与工具边界
- executor 不再面对全开放空间
- 训练侧还有 actor / critic 持续改 skill

所以这两条线本来就是不同系统。

更准确的对照方式应该是：

- 把 `Earth-Agent origin` 看成“原生单 agent + 全工具轨迹”的参考点
- 把 `nlrl_skills` 看成“skill-guided executor + 可训练 skill library”的参考点

---

## 6. 当前通过这条对照线，已经能稳定学到什么

### 6.1 小模型下，answer closing 是独立问题

`Earth-Agent origin` 的对照结果非常清楚地提示了一点：

- “能跑完”不等于“能收回正确 final answer”

这条判断对 `nlrl_skills` 也有价值，因为它提醒我们：

- tool planning 成功
- tool execution 成功

之后，最后一跳的 answer closing 仍然可能是单独的失败点。

### 6.2 grounded tool arguments 仍然是瓶颈

原生对照里已经观察到：

- 第一跳就猜不存在文件名
- 或直接把伪参数塞进 tool call

这说明在没有 skill prior 的情况下，grounding 本身就很脆。

### 6.3 上下文只是瓶颈之一

即使存在长上下文风险，原生线当前最突出的失败也不集中在“题题爆 context”，更像：

- 起步不稳
- 参数不 grounded
- 最终不 closing

这对理解当前主线很有帮助，因为它告诉我们：

- 不要把所有低分都归因到 token window

---

## 7. 后续应该如何使用这条专题

后面如果继续做对照，建议把问题分开看：

1. 原生范式在小模型下的自然完成率
2. grounded tool arguments 的稳定性
3. tool result 后的 final answer closing
4. 长轨迹与上下文风险是否真的成为主要瓶颈

不要再把这些不同问题混成一句：

- “Earth-Agent 不行是因为爆上下文”

这个说法当前证据并不够强。
