# `actor` / `critic` 控制流

## 1. 这篇文档的职责

这篇只总结当前 `task-local` 训练循环里：

- `critic` 在什么时机运行
- `actor` 在什么时机运行
- 为什么某些 iteration 里你会看到它们“不完整”

这篇不负责保存某一轮 run 的碎日志，只负责钉住当前代码已经证实的控制流。

---

## 2. 当前代码里的主控制流

核心位置：

- `nlrl_skills/task_local_trainer.py:143-233`

按代码顺序压缩后，当前训练循环就是下面这条链：

1. 每轮创建 `iteration_dir`
2. 读取当前 `skill_headers`
3. 跑 `environment.run(...)`
4. 紧接着跑 `critic.evaluate(...)`
5. 如果本轮已经 `task_success = true`
   - 直接写 `iteration_summary.json`
   - `actor_decision = None`
   - 本题结束
6. 如果本轮没成功
   - 才进入 `actor.act(...)`
   - 然后 `actor.apply(...)`
7. 如果 `env` 或 `critic` 异常
   - 直接写 `iteration_failure.json`
   - 本题结束
8. 如果 `actor` 异常
   - 也直接写 `iteration_failure.json`
   - 本题结束

对应的关键代码点：

- `149-156`：`environment.run` 后立刻 `critic.evaluate`
- `174-186`：成功则短路，不再进入 `actor`
- `190-199`：只有失败轮才会走 `actor`
- `157-165`：`env/critic` 异常直接 break
- `201-209`：`actor` 异常直接 break

---

## 3. 为什么 `actor` 看起来会比 `critic` 少

这是当前设计的正常表现。

### 3.1 成功轮次会跳过 `actor`

只要 `critic` 给出的当前 `state.env_result.evaluation.task_success = true`，代码就会在：

- `task_local_trainer.py:174-186`

直接收口。

因此会出现一种很常见的现象：

- `critic` 已经跑了
- `actor` 没再跑

这代表“本轮已成功，不需要再改 skill”，控制流本身没有丢。

### 3.2 `actor` 只负责失败后的下一步 skill 动作

当前逻辑里，`actor` 只在失败轮次担任 skill 编辑器。

代码里甚至把动作类型先硬收成了：

- 没有 active skill 时：`create_skill`
- 已有 active skill 时：`modify_skill`

位置：

- `188-199`

---

## 4. 为什么有时连 `critic` 也像没出现

这通常有两类原因。

### 4.1 `env` 先报错了

如果 `environment.run(...)` 自己就抛异常，控制流会直接落到：

- `157-165`

这种情况下：

- `critic` 根本没机会正常落盘
- `actor` 也不会继续跑

### 4.2 `critic` 本身报错了

`env` 和 `critic` 被包在同一个 `try` 里，所以只要其中任何一步失败，都会被统一归到：

- `iteration_{k}_env_or_critic_error`

因此当你在 run 目录里看不到完整 `critic` 结果时，不应默认理解成“critic 被跳过”，更可能是：

- `env` 或 `critic` 在这一轮直接失败了

---

## 5. 为什么第 1 轮经常像“先 critic，再 actor 创建 skill”

这和冷启动条件有关。

当前代码里：

- `skill_headers` 为空时，`forced_skill_name = None`
- 位置在 `148`

于是第 1 轮常见形态是：

1. 没有可激活 skill
2. `environment.run(...)` 在无 skill 条件下跑一轮
3. `critic` 先对这个失败状态打分
4. `actor` 再据此创建第一份 skill

所以你在训练目录里看到“第 1 轮像先 critic，再 actor 创建 skill”，这正是当前冷启动设计的正常表现。

---

## 6. 当前训练循环还有两个容易忽略的事实

### 6.1 当前 workspace 会被收成单 skill 继续迭代

在 `actor.apply(...)` 后，代码会调用：

- `self._enforce_single_skill_workspace(...)`

位置：

- `199`

也就是说，当前 task-local 训练会继续沿当前目标 skill 往下改，不会在同题上并行维护很多 skill 分支。

### 6.2 每轮前后 skill header 都会落盘

代码会在 iteration 目录里写：

- `skill_headers_before.json`
- `skill_headers_after.json`

位置：

- `145-146`
- `211-212`

所以如果后面要追“这一轮 skill 到底怎么变了”，优先去看这两个文件；只盯 `actor` 文本回复，信息不够。

---

## 7. 调试训练时最稳的判断口径

如果后面继续看训练日志，建议按下面四类去判断，不要再混说。

### A. 成功短路

特征：

- `critic` 有结果
- `actor_decision = null`
- iteration status 是成功收口

解释：

- 这一轮已经完成任务，不需要继续改 skill

### B. 正常失败后修改

特征：

- `critic` 有结果
- `actor` 也有结果
- 本轮写了 `skill_headers_after.json`

解释：

- 这是最标准的一轮 skill 迭代

### C. `env_or_critic_error`

特征：

- `iteration_failure.json`
- 错误原因落在 `env` 或 `critic`

解释：

- 本轮在 skill 修改前就挂了

### D. `actor_error`

特征：

- `critic` 已经正常返回
- 但 `actor.apply` 或前面的 actor 步骤失败

解释：

- 执行评估通了，但 skill 编辑环节失败

---

## 8. 当前应如何使用这篇专题

后面如果再讨论：

- 为什么某题 `actor` 没跑
- 为什么某轮只有 `critic`
- 为什么第 1 轮像是无 skill 冷启动

应该先回到这篇和：

- `nlrl_skills/task_local_trainer.py`

避免再把这些机制解释重复追加回总实验文档。
