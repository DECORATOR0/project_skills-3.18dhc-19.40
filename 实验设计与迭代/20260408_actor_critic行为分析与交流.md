# `critic` / `actor` 行为分析与交流

## 1. 目的

这份文档单独记录 `project_skills-3.18dhc-19.40` 当前训练链路里：

- `critic` 在什么时机运行
- `actor` 在什么时机运行
- 为什么在某些 task / iteration 里看起来不是“每轮都完整跑了两者”
- 我们围绕这件事的交流结论

这样后续再讨论训练过程时，不需要把这部分继续混在总梳理文档里。

---

## 2. 先说结论

结论先钉死：

- 当前 task-local 训练循环里，`critic` 不是“绝对每轮都一定成功落盘”，但它比 `actor` 更靠前。
- `actor` 不是每个 iteration 都会执行。
- 只有在：
  - `env` 正常完成
  - `critic` 正常返回
  - 本轮 `task_success = false`
  时，才会继续进入 `actor`。
- 如果本轮已经成功，训练会直接收口，不再调用 `actor`。
- 如果 `env` 或 `critic` 报错，也会直接 break，`actor` 不会执行。
- 如果 `actor` 自己报错，本轮会留下 `evaluation + reward + error`，然后结束该 task。

所以你在 `60` 题正式训练里看到“某个 task 的某个 iteration 里，critic 和 actor 不是总在每次都操作”，这不是异常现象，而是当前训练代码本来就这样设计的控制流。

---

## 3. 实际训练循环长什么样

核心代码在：

- `nlrl_skills/task_local_trainer.py`

关键逻辑可压成下面这段：

1. 每轮先跑 `environment.run(...)`
2. 然后跑 `critic.evaluate(...)`
3. 如果 `task_success = true`
   - 直接写 `iteration_summary.json`
   - `actor_decision = null`
   - 直接 `break`
4. 如果本轮没成功
   - 才继续跑 `actor.act(...)`
   - 然后 `actor.apply(...)`
5. 如果 `env/critic` 任何一步异常
   - 写 `iteration_*_env_or_critic_error`
   - 直接 `break`
6. 如果 `actor` 异常
   - 写 `iteration_*_actor_error`
   - 直接 `break`

对应代码位置：

- `task_local_trainer.py:149-156`
  - 先跑 `env`，再跑 `critic`
- `task_local_trainer.py:174-186`
  - 一旦 `task_success`，直接结束，不进 `actor`
- `task_local_trainer.py:190-200`
  - 只有未成功时才调用 `actor`
- `task_local_trainer.py:157-165`
  - `env/critic` 异常直接结束
- `task_local_trainer.py:201-209`
  - `actor` 异常直接结束

---

## 4. 为什么 `actor` 会比 `critic` 少

这是最容易让人误解的一点。

### 4.1 成功轮次会跳过 `actor`

当前逻辑里，就算 `critic` 已经给了 reward，只要这一轮执行结果已经 `task_success = true`，训练就会直接收口。

也就是说：

- `critic` 已经运行了
- 但 `actor` 不再改 skill

这是因为代码把“任务已经做对”当成 task-local 训练的终止条件，而不是“既然 critic 也看到了，那 actor 再顺手优化一下”。

因此某个 run 里常见的现象就是：

- `critic` 落盘次数 > `actor` 落盘次数

这正是之前 smoke 里看到的情况：

- `actor` 落盘调用数 `17`
- `critic` 落盘调用数 `19`

这不代表有两轮丢了 actor 日志，更常见的解释就是：

- 有若干轮任务已经成功
- 所以 `critic` 有
- `actor` 被故意跳过

---

## 5. 为什么有时候连 `critic` 也看不到

如果一个 iteration 在 `env` 或 `critic` 阶段就抛异常，那么这轮不会再进入正常 summary 分支。

此时会出现：

- `iteration_failure.json`
- `discard_reason = iteration_X_env_or_critic_error`

而不是完整的：

- `evaluation + reward + actor_decision`

这类情况以前在 JSON 不稳定阶段更常见。也就是说：

- “这轮没有 actor” 不一定是 actor 被跳过
- 也可能是前面的 `env` 或 `critic` 已经先炸了

---

## 6. 第 1 轮为什么经常像是“先 critic，再 actor 创建 skill”

这是当前 task-local 单题训练的另一个关键设定。

每个 task 开始时，本地 workspace 会被清空：

- reset 本地 `skill_library`
- reset 本地 `experience_buffer`

因此第 1 轮通常是：

1. skill 库为空
2. `env` 发现没有可用 skill
3. 环境返回 `没有合适的skill`
4. `critic` 根据这个状态给出 “应该 `create_skill`” 的判断
5. `actor` 才据此生成第一个 task-local skill

所以从行为上看，第一轮经常不是“modify 现有 skill”，而是：

- `critic` 先识别“当前是 coverage 缺失”
- `actor` 再执行 `create_skill`

---

## 7. `actor` 在这套训练里到底扮演什么角色

如果只看名字，容易把它理解成传统 RL 里“每轮都更新 policy 的 actor”。

但这套代码里它更接近：

- 失败后的 skill 编辑器
- 根据 `critic` 的文字反馈，创建或修改 task-local skill 的写作者

也就是说它不是：

- 无条件每轮优化一次 policy

而是：

- 只有任务没成功时，才接手修 skill

这个区别很重要，因为它直接解释了为什么：

- 有些 iteration 只看到 `critic`
- 有些 iteration 同时有 `critic + actor`
- 有些 iteration 两者都不完整，只留下 failure

---

## 8. 目前可稳定使用的判断口径

后面再看任意一个 task 的训练轨迹时，可以直接按下面四类判断：

### A. 成功短路

特征：

- 有 `evaluation`
- 有 `reward`
- `task_success = true`
- `actor_decision = null`

含义：

- `critic` 已经跑了
- 但因为任务已成功，`actor` 被故意跳过

### B. 正常修改轮

特征：

- 有 `evaluation`
- 有 `reward`
- `task_success = false`
- 有完整 `actor_decision`

含义：

- 本轮失败
- `critic` 给反馈
- `actor` 正常创建/修改 skill

### C. `env_or_critic_error`

特征：

- 有 `iteration_failure.json`
- `discard_reason = iteration_X_env_or_critic_error`

含义：

- `env` 或 `critic` 已经异常退出
- `actor` 没有机会运行

### D. `actor_error`

特征：

- 通常已有 `evaluation`
- 通常已有 `reward`
- 但最终写成 `iteration_failure.json`
- `discard_reason = iteration_X_actor_error`

含义：

- 前半轮是好的
- 失败发生在 `actor` 生成或应用 skill 的阶段

---

## 9. 一次交流结论记录

### 问题

在这次 `60` 题训练观察里，为什么对于某一个 task 的某个 iteration，`critic` 和 `actor` 不是总在每次都操作？

### 回答

因为当前训练循环不是“每轮固定执行四段并全部落盘”，而是一个带短路和异常退出的控制流：

- `critic` 位于 `env` 后、`actor` 前
- 成功后直接停，所以 `actor` 会被跳过
- `env/critic` 报错会直接停，所以 `actor` 也会缺席
- `actor` 自己报错时，会留下半轮记录然后结束

因此：

- `critic` 次数通常不小于 `actor`
- `actor` 次数少于 `critic` 是正常的
- 不能看到“某轮没 actor”就误以为训练逻辑漏跑了

---

## 10. 后续建议

如果后面还要继续分析正式 `60` 题 run，建议对单个 task 的所有 iteration 直接加一个人工标签列：

- `success_short_circuit`
- `critic_actor_normal`
- `env_or_critic_error`
- `actor_error`
- `iteration_limit_reached`

这样会比只盯着原始 JSON 更容易看出：

- 到底是因为成功收口而少跑了 `actor`
- 还是因为前面某个阶段异常
- 还是因为一直反复修改但没收敛，最后耗尽 iteration budget

---

## 11. 本文档关联位置

如果后面要继续对照代码和历史结论，优先看：

- `nlrl_skills/task_local_trainer.py`
- `nlrl_skills/environment.py`
- `实验设计与迭代/20260401_183442_origin_dhc_skillpool_六优良梳理.md`
- `实验设计与迭代/20260401_183442_origin_dhc_skillpool_六优良梳理_第8部分及后续.md`
