# `executor` 记忆与上下文预算

## 1. 这篇文档的职责

这篇只回答当前 `nlrl_skills` executor 的机制问题：

- 每一步请求里到底带哪些历史
- budget 是怎么计算的
- 超预算后按什么顺序保留与截断
- 为什么即使扩窗后仍然会很快贴顶

历史 run 的具体结果与日期，放在：

- `03_实验记录/2026-04-08_1103_executor_token_budget与输入窗重跑.md`
- `03_实验记录/2026-04-08_2118_no-skill_all-tools基线.md`

---

## 2. 当前代码里的消息骨架

核心入口：

- `nlrl_skills/agent_loop.py:266-380`

每轮 `executor` 启动时，消息从两条起步：

1. `system`
2. `user`

其中：

- `system` 由 `base_system_prompt + tool protocol + 当前允许工具表` 拼出来
- `user` 里放的是当前题目 payload 与激活 skill 的注入内容

对应位置：

- `277-285`

所以当前 prompt 膨胀最靠前的两块是：

- `system` 自身
- `user` 自身

这也是为什么 skill 一旦写得过肥，就算 history 还没长起来，也会很快把预算挤满。

---

## 3. 当前 input budget 怎么算

当前代码直接按下面公式算每次可用输入预算：

- `max_input_tokens = executor_total_token_budget - executor.max_tokens`

位置：

- `295-300`

因此要分清两个层次：

1. `executor_total_token_budget`
2. `executor.max_tokens`

例如当前代码默认值是：

- `executor_total_token_budget = 32768`
- `executor.max_tokens = 8192`

那么默认 input budget 实际上是：

- `24576`

对应代码与配置：

- `nlrl_skills/config.py:45-47`
- `nlrl_skills/config.py:135-145`

---

## 4. 当前 history 是按什么单位保的

当前 token-budget 模式改成了按 turn 处理，不再使用早期那种纯字符裁剪。

### 4.1 turn 的定义

代码会把历史尽量按下面的二元组组织：

- `assistant(step_k tool call json)`
- `user(step_k tool result)`

位置：

- `83-99`

### 4.2 保留顺序

超预算时，当前逻辑会优先保：

1. 前两条消息，也就是 `system + user`
2. 尽可能多的最近历史 turn
3. 如果连一个完整 turn 都塞不下，再在该 turn 内做 token 级截断

位置：

- `181-247`

所以当前模式的核心策略是：

- 先保住 prompt 主体
- 再尽量保住最近几轮完整轨迹

---

## 5. 一个 turn 真要截断时，先裁谁

当前代码在单 turn 内的裁剪顺序是：

- 先试着裁 `user`
- 再试着裁 `assistant`

位置：

- `127-177`

更具体地说，排序 key 为：

- `working[idx].role != "user"`

这意味着：

- `user(tool result)` 会先成为截断目标
- 如果还不够，再轮到 `assistant` 那条工具调用 JSON

这与当前观察也一致：

- 大量超长内容首先来自 tool result；assistant 的 tool-call skeleton 通常更短

---

## 6. `[truncated]` 在当前代码里意味着什么

当前截断标记常量在：

- `agent_loop.py:13`

一旦某条消息被 token 级截短，结尾会被补上：

- `[truncated]`

所以后面你在 request 文件里看到这个标记，应该理解为：

- 当前 packer 已经真实介入
- 这个标记由当前 packer 追加

---

## 7. 当前已经能稳定说死的结论

### 7.1 扩窗有用，但不会自然消灭 prompt inflation

`2026-04-08` 的直接观察已经说明：

- 就算把 input budget 抬到 `32768`
- request 仍然可能精确贴到输入上限

也就是说，当前问题已经比“旧窗太小”更复杂：

- `system`
- `task payload + skill`
- 多步 `assistant / tool result`

这些东西叠起来，本来就很容易把单 executor prompt 顶满。

### 7.2 skill 肥度仍然是系统级问题

当前消息结构决定了：

- `system + user` 永远是最高优先级

因此如果激活 skill 本体太长，就会直接挤压历史可保留空间。

这也是为什么之前会看到：

- `flat` 某些超级 skill 更早退化

memory 机制会把“skill 注入过肥”放大成更明显的问题，它本身也确实是问题链条中的一环。

### 7.3 `104` 工具 no-skill 基线也会自然逼近 `30K+`

对 `no-skill / all-tools` 的抽样直跑说明：

- 即使还没触发显式 `[truncated]`
- provider 侧的单次 `input_tokens` 也已经经常自然到 `30K+`

因此不能把当前问题简单归因成：

- “只是工具表太长”

更准确的说法是：

- 工具表长度只是底座之一
- 真正把请求顶长的是题面、系统协议、工具返回、以及多步轨迹一起叠加

---

## 8. 当前调试 executor 时最该看哪里

### 8.1 看 budget 配置

先确认两项：

- `config_snapshot.json` 里的 `runtime.executor_total_token_budget`
- `config_snapshot.json` 里的 `executor.max_tokens`

### 8.2 看 request 是否真的被 packer 截断

重点看：

- `env/executor/executor_steps/*_request.json`

检查：

- 有没有 `[truncated]`
- 消息条数是否还完整递增
- 历史 turn 是否已经开始丢失

### 8.3 看 code 当前默认值，不要只看旧文档

旧长文档里保留了很多关于历史 `1600 / 600 chars` 模式的观察，但当前 workspace 真正生效的主逻辑要以：

- `nlrl_skills/agent_loop.py`
- `nlrl_skills/config.py`

为准。

---

## 9. 当前最稳的一句话

当前 executor 的问题已经落到更细的层面：

- 在固定 input budget 下，`system + skill + task + tool result` 的组合体积本来就很容易膨胀

所以后续优化不应只盯“截断器怎么改”，还必须一起看：

- skill 本体瘦身
- tool result 体积
- 长轨迹任务的多步闭环设计
