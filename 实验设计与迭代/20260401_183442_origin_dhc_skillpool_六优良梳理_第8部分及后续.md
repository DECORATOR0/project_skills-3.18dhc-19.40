## 8. 2026-04-06 聚合重构 v8 约束与后续执行计划

这一节用于承接本轮关于“聚合 skill 结构重构”的讨论结果，供后续切换新上下文后直接继续执行。这里记录的是**当前已经确认的设计约束、已完成的事实确认、以及接下来必须推进的改动顺序**。另外，本节的优先级已经重新调整：**先跑 `60` 题快速验证线，再回到 `140` 题正式线**。

### 8.1 当前总目标与优先执行线

这一层只做总括。后面所有细节都统一下沉到 `8.1.x`，避免后续继续开 `8.2 / 8.3` 时层级过高。

#### 8.1.1 当前最高优先级：先跑 `60` 题快速验证线

当前应优先启动的不是直接重做 `140` 题正式线，而是先把之前 `7.2` 左右那次已经抽出来的 `60` 题子集重新跑通一遍，作为新版聚合路线的快速验证集。

这样做的原因是：

- 那 `60` 题当时是比较均衡地抽出来的子集，比当前 `140` 题 retained set 更适合作为快速验证。
- 后续 `7.3` 以后范式已经改过，因此当时旧分数本身不再有效，但这批题作为**快速验证子集**仍然有价值。
- 当前最需要先验证的是：去掉 `family`、明确 `allowed-tools` 硬包络、改成自动聚类聚合以后，`flat / tree` 两条线在同一子集上分别能跑到什么效果。

这条 `60` 题快线当前明确约束如下：

- 直接沿用当前 `7.5` 的训练/聚合/测试大框架，但不再补任何“训练 shortlist 对齐”逻辑；因为现在已经确认，`7.5` 训练里本来就没有题目级 `shortlist`。
- 训练阶段直接按“**无 shortlist**”的事实继续，不额外补一个训练期 shortlist 机制。
- 旧 `family` 相关语义全部禁用：
  - 不进旧 family 桶
  - 不保留旧 family metadata
  - 不按预设 family 聚合
- 训练阶段的大模型仍然接 `gpt-5.2 API`。
- 小模型统一切到上海实验室 API 的 `Qwen3-8B`，优先使用这一条，不再依赖当前服务器上的本地 `Qwen3-8B`。
- /data/xsy/skill-pool/API说明/最新api说明.txt 这里面是qwen3-8b 那个有
- 这条 API `Qwen3-8B` 的执行口径固定为：
  - 开启 thinking
  - 必须走 streaming
  - 关闭 JSON object mode
  - 不再额外做人为上下文长度收缩
  - 依赖现有兜底逻辑承接结构化输出
- 训练完成后，保留训练中成功沉淀下来的 task-local skills。
- 聚合阶段先调用一次 `gpt-5.2 API` 做自动分类，把 retained skills 分成 `6` 组。
- 这次分类结果只做一次，随后共发 `12` 次聚合：
  - `6` 次生成 `flat`
  - `6` 次生成 `tree`
- 测试阶段彻底去掉 `shortlist`：
  - `router` 只根据 `name + description` 选 skill
  - `flat` 版只路由一次
  - `tree` 版是两段式：先路由到 skill，再在 skill 内路由到 mode
  - `executor` 正常执行，但工具边界完全由 `allowed-tools` 负责
- 测试阶段的小模型同样统一用上海实验室 API 的 `Qwen3-8B`，保持 thinking + streaming 口径一致。
- 这一轮快线最终要列表汇报：
  - `flat / tree` 各自的结果
  - 每阶段 token 消耗
  - 每阶段耗时
  - 两版的整体收益对比

也就是说，当前最应该优先落地的一条实验线可以先记成：

> 先用旧 `7.2` 抽出的 `60` 题子集，在“训练无 shortlist、family 全禁用、Qwen3-8B 全改走 API、聚合改成自动聚类后再分别产出 `flat / tree`”的新口径下，跑一轮完整训练、聚合、测试，先拿到一组快验结果。

#### 8.1.2 这次改动的主目标

这次不是在当前 `7.5 / 7.7` 线上做小修，而是要把聚合 skill 的若干核心先验整体替换掉。

本轮已经确认必须改的点如下：

- 题目级 `shortlist` 机制不再作为 skill 侧的核心边界设计；聚合 skill 需要自己通过 `allowed-tools` 提供硬包络。
- `allowed-tools` 的职责重新定义为**executor 可执行工具的硬包络**，用来替代过去 `shortlist` 在 skill 注入阶段带来的工具边界效果。
- 旧的 `family` 预设不再继续保留为正式语义；后续聚合与路由都不应再依赖 `family_id / domain / question_range / route_signals` 这些字段。
- 聚合流程从“先按既定 family 分桶再逐桶聚合”改为“先让模型做一次自动分组，再按组并行聚合”。
- 聚合 skill 的字段需要显式稳定下来，避免 router / executor / 后续统计口径各自消费一套隐含结构。
- 聚合方案需要同时保留两条实验线：
  - `flat`：单层 skill
  - `tree`：两层 skill，最多只保留到 `skill -> mode` 这一层
- 当前已确认：测试阶段对聚合 skill 自带 `references / scripts` 的消费并不稳定，尤其 `scripts` 历史上基本没有正确形成有效收益；因此这次改造先**不把 skill 内脚本资源作为主设计点**。

#### 8.1.3 聚合总流程的新约束

后续正式实现时，聚合总流程应改为下面这个形态：

1. 训练阶段不再把每条 task-local skill 绑定到旧 `family`。
2. 保留训练中迭代成功的 task-local skill。
3. 先调用一次较强模型 API，对 retained skills 做自动分组，得到 `K` 组。
4. 当前主线目标仍然是最终得到 `6` 个 aggregated skills，因此现阶段可令 `K = 6`，但它们的语义不再是预设 family，而是本轮 retained skills 自动形成的 `6` 个簇。
5. 再并行发起 `K` 次聚合 API，每组各自聚成 `1` 个 aggregated skill。
6. 同一份聚类结果应复用给两套聚合线：
   - 一套 `flat`
   - 一套 `tree`

这里最重要的约束是：

- **不再先验指定“第几个 skill 必然是哪一个 family”**。
- 最终 skill 名称、description、边界和内部指导，都由聚合结果本身决定。
- `flat / tree` 的差异只发生在聚合表达层，不应该先分别再做两次聚类。

#### 8.1.4 router / executor 的字段消费约束

本轮已经明确的职责切分如下：

- `router` 只消费：
  - `name`
  - `description`
- `executor` 才消费：
  - `allowed-tools`
  - `SKILL.md` 主体 guidance
  - `references/EXECUTION_GUIDANCE.md`（仅 `tree` 版本）

因此后续所有 prompt 与 schema 设计都要围绕这条原则：

- `description` 是 router 的主匹配入口
- `allowed-tools` 是 executor 的硬包络
- 其余字段只服务 executor 与统计溯源，不再混入额外路由语义

#### 8.1.5 两类 aggregated skill 的共同 frontmatter 约束

不论是 `flat` 还是 `tree`，前置 frontmatter 都统一为同一套字段，只允许 `metadata.skill_shape` 不同。

建议固定为：

```yaml
---
name: ...
description: ...
allowed-tools:
  - ...
compatibility: nlrl_skills.executor.aggregated-v2-flat   # 或 aggregated-v2-tree
metadata:
  schema_version: aggregated_skill_v2
  skill_shape: flat   # 或 tree
  aggregation_model: gpt-5.2
  source_skill_count: ...
  source_task_count: ...
  generated_at: ...
---
```

当前明确不要再保留到聚合 skill frontmatter 里的字段有：

- `family_id`
- `display_name`
- `domain`
- `benchmark_question_range_prior`
- `route_signals`
- `training_buckets`
- 以及任何等价的旧 family 继承字段

这里需要特别记住：

- `metadata` 只保留**溯源与实验记录**，不承载路由语义
- `skill_shape` 明确标识当前聚合 skill 是 `flat` 还是 `tree`

#### 8.1.6 `tree` 版 aggregated skill 字段约束

`tree` 版的目标，是保留一个轻量二层结构，但只到 `mode` 为止，不允许继续往下再套层级。

`tree` 版 `SKILL.md` 建议固定结构如下：

```md
## Execution Profile Index
- Mode A: 1-2 句，说明适用题型、典型输入/目标、与其他 mode 的边界
- Mode B: 1-2 句，说明适用题型、典型输入/目标、与其他 mode 的边界

## Global Execution Rules
- 全局正向执行规则

## Global Guardrails
- 全局负向约束

## Reference Usage
- 仅说明何时再看 `references/EXECUTION_GUIDANCE.md`
```

`tree` 版还允许保留一个额外参考文件：

- `references/EXECUTION_GUIDANCE.md`

这个参考文件里，每个 mode 展开为固定 `5` 个字段：

- `applies_when`
- `preferred_tools`
- `flow_hint`
- `canonical_flows`
- `edge_cases`

额外约束如下：

- `Execution Profile Index` 不能只放 mode label，必须给每个 mode 写 `1-2` 句简短说明，供 skill 内二层路由使用。
- 主 `SKILL.md` 不再重复展开每个 mode 的全部细节。
- `discouraged_patterns` 不单独作为字段保留，相关约束合并到 `edge_cases / guardrails` 里。
- `tree` 版只允许两层：顶层 skill + 一层 mode index。

#### 8.1.7 `flat` 版 aggregated skill 字段约束

`flat` 版的目标，是保留与旧 skill 更接近的单层形态，用来和 `tree` 版做控制变量对比。

`flat` 版 `SKILL.md` 建议固定结构如下：

```md
## High-Level Execution Guidance
- 高层执行骨架

## Common Defaults
- 通用默认策略

## Global Guardrails
- 全局负向约束
```

`flat` 版额外约束如下：

- 不再引入 `Execution Profile Index`
- 不再引入 `MoD`
- 不做 skill 内二层路由
- 不保留 mode 级 reference 展开文件
- executor 直接消费主 `SKILL.md` 的高层 guidance

也就是说，`flat` 与 `tree` 两版最核心的区别只在这里：

- `flat`：router 选完 skill 后，executor 直接按单层 guidance 执行
- `tree`：router 选完 skill 后，executor 还要在 skill 内判断落到哪个 mode，再按 mode guidance 执行

#### 8.1.8 关于 `references / scripts` 的当前确认

本轮已经额外确认了一个容易混淆但很重要的事实：

- 历史上的聚合 skill library 基本上一直只有：
  - `SKILL.md`
  - `references/RUNTIME_GUIDANCE.md`
  - `references/EXECUTION_GUIDANCE.md`
- **聚合 skill library 历史上并没有正式产出过 `scripts/`**

而且从当前主线附近的测试 run 看：

- `references` 虽然会被暴露给 executor，但实际被使用的频率并不稳定
- `scripts` 在聚合 skill 这条线上基本没有形成稳定正收益
- 一些旧日志里出现的 `run_python_script scripts/*.py`，很多其实是 executor 自行猜脚本路径，最终失败，并不是聚合 skill 真正确保了这条能力链

因此本轮重构先采取保守策略：

- 暂时不把 `scripts/` 作为聚合 skill 设计的一部分
- `tree` 版只保留 `references/EXECUTION_GUIDANCE.md`
- 旧的 `references/RUNTIME_GUIDANCE.md` 不再作为新版 schema 的必留文件

#### 8.1.9 当前已经完成的确认与中间产物

截至当前，对这轮 v8 重构已经完成的工作主要有：

- 已经确认旧聚合链路当前仍然是“先按预设 family 分桶，再逐 family 聚合”，这正是后续要替换掉的旧逻辑。
- 已经确认聚合 skill 历史上只有 `references`，没有正式聚合 `scripts`。
- 已经确认在当前主线附近的聚合测试中，`references / scripts` 并没有被稳定正确消费，因此本轮不把它们当作核心优化点。
- 已经确认 `7.5` 训练本身没有题目级 `shortlist`，因此后续训练侧不需要再人为补一个 shortlist 对齐层。
- 已经确认 `completed140` 这条线最终保留下来的 task-local skills 实际是 `94` 个，不是字面上的 `140` 个。
- 已经完成一轮围绕“硬包络 + mode index”的 prompt 草拟与 GPT-5.2 API 聚合试验，现有中间产物包括：
  - `aggregated_skill_library_6_gpt52api_envelopefocus_20260406_r1`
  - `aggregated_skill_library_6_gpt52api_modeindex_20260406_r1`
- 这些中间产物的作用主要是帮助讨论 schema 与 prompt，不应直接视为这轮新版正式基线。

#### 8.1.10 下一上下文接手后的执行顺序与 token/time 统计要求

后续切到新上下文后，建议按下面顺序继续推进：

1. 先锁定之前 `7.2` 左右已经抽好的 `60` 题子集，作为当前最高优先级的快速验证集。
2. 改训练与聚合总流程，禁用旧 family 依赖，并确认训练期继续维持“无题目级 shortlist”的事实口径。
3. 把小模型统一切到上海实验室 API 的 `Qwen3-8B`：
   - training env 用它
   - test env 用它
   - 开 thinking
   - 开 streaming
   - 关闭 JSON object mode
4. 训练阶段仍然使用 `gpt-5.2 API` 负责大模型侧能力，并沉淀 task-local skills。
5. retained-skill 聚合入口改成“先自动分成 `6` 组，再并行聚合”的新机制。
6. 聚类只做一次，然后分别写两套聚合 prompt：
   - `flat`
   - `tree`
7. 基于同一份 `6` 组分类结果，并发产出两套聚合库：
   - `6` 个 `flat`
   - `6` 个 `tree`
8. 测试阶段分别评测这两套 skill：
   - `router` 统一只看 `name + description`
   - `flat` 版不做二层路由
   - `tree` 版在进入 executor 后再做一次 mode 级内部路由
   - 测试期也不再使用 `shortlist`
9. 对 `60` 题快线先出完整报告；如果结果合理，再把同一套新口径推进到 `140` 题正式线。

这轮除了现有准确率/成功率指标以外，还必须把 **token 统计 + 时间统计** 一起补上，而且要按阶段、按模型分别统计并最终汇报。

至少要覆盖：

- 阶段维度：
  - 训练阶段
  - 聚合阶段
  - 测试阶段
- 模型维度：
  - `gpt-5.2`
  - API `Qwen3-8B`
- 指标维度：
  - input tokens
  - output tokens
  - total tokens
  - wall-clock time
  - 如能拿到，再补单阶段并发配置与平均单题耗时

最终至少要能回答下面这些问题：

- `flat` 与 `tree` 两条线在训练、聚合、测试各阶段分别消耗了多少 token
- `flat` 与 `tree` 两条线在训练、聚合、测试各阶段分别消耗了多少时间
- `tree` 相比 `flat` 是否出现了“性能更好且 token 更少、时间也没有显著更差”的情况
- 二层路由带来的额外 token / 时间开销是否值得

因此本轮正式实验的统计口径不再只是 token，而是：

- token 必须有
- time 也必须有

#### 8.1.11 当前这节的简短收口

如果只保留一句最该接着执行的话，可以先记成：

> `8` 这一轮的核心不是继续修旧 `family + shortlist` 路线，而是先用旧 `60` 题子集跑通“训练无 shortlist、family 全禁用、自动聚类后分别聚合成 `flat / tree`、测试时只靠 `name + description` 路由、`allowed-tools` 负责硬包络”的新体系，并把 router 消费字段、executor 消费字段、两版 schema、以及 token/time 统计口径全部一次性定稳。

#### 8.1.12 `SSSAI gpt-5.2 /responses` 当前异常根因、定向修复与最新监控

这一小节只记录本轮针对 `SSSAI gpt-5.2` 训练侧 JSON 失稳问题的排查结论，避免后面又把“训练 prompt 变重了 / 本地 repair 写坏了 / actor critic 逻辑变了”混在一起。

先说已经坐实的事实：

- 当前坏现象主要集中在 `actor / critic` 的 `responses_sse` 路径。
- 旧的成功 `140` 题 rerun 里，训练侧 `actor / critic` 落盘响应统计下来基本没有 repair：
  - actor/critic 响应总数 `1385`
  - `json_repair_attempts > 0` 的数量是 `0`
- 旧 rerun 的同类响应里，服务端落盘 `response.instructions` 还是我们自己的 Actor / Critic prompt。
- 当前新 smoke 的坏响应里，服务端落盘 `response.instructions` 被替换成了一整段 Codex CLI system prompt。
- 旧 rerun 与当前训练配置对比后，`configs/system.train_local_actor_critic_sssai_gpt52.json` 的 actor/critic 形态并没有本质变化，因此不能把锅简单甩给“本地 prompt 改坏了”。

这轮针对 `/responses` 的最小复现实验，结论也已经比较清楚：

- 直接沿用旧形态：
  - `instructions = system prompt`
  - `input = user`
  - 在当前 `SSSAI /responses` 上，critic 经常只回 `0.0`、`{\"reward\":1.0}` 这类错误 JSON 形态，虽然响应更快，但明显不稳定。
- 把 system prompt 不再走 `instructions`，而是直接并进 `input`：
  - `input = [system, user]`
  - 或 `input = [user, user]`
  - critic 会明显更稳，基本都能直接返回完整目标 JSON，对 actor 也同样稳定。
- 因此当前更像是：
  - **`SSSAI /responses` 对 `instructions` 字段的上游兼容性发生了变化**
  - **不是训练逻辑本身坏了**
  - **也不是 repair 机制本身导致首次响应变坏**

基于这个判断，这次只做了一个定向补丁，不做全局大改：

- 文件：
  - `nlrl_skills/llm.py`
- 策略：
  - 只对 `config.name in {"actor", "critic"}` 且 `api_mode = responses_sse` 的调用生效
  - 不再把 system prompt 放进 `instructions`
  - 改成直接内联到 `input`
  - 聚合器仍然保留原来的 payload 形态，不一起改，避免把非关键路径也拖慢

补丁后的即时真实调用验证结果如下：

- 独立回归调用：
  - `critic_old` 连跑 `3` 次
  - `actor_modify_smoke` 连跑 `2` 次
  - `actor_create_old` 跑 `1` 次
- 结果：
  - 所有这些调用的 request 都已经变成 `input=[system,user]`
  - `request_has_instructions = false`
  - `json_repair_attempts = 0`
  - critic 耗时大约 `10-12s`
  - actor 耗时大约 `18-21s`
- 也就是说，当前训练侧最关键的目标已经先达成：
  - **native 首响就能回结构化 JSON**
  - **repair 不再成为常态路径**

然后又继续接了真实 `v8` smoke 链路：

- 运行目录：
  - `runs/v8_fastline_smoke_20260407_jsonfix_r1`
- 当前监控到的训练侧中间状态：
  - 已经出现多轮真实 `actor_create / actor_modify / critic`
  - 例如：
    - `task_02_173` 已经跑到 `iteration_05`
    - `task_03_210` 已经跑到 `iteration_03`
- 到目前已落盘的这些 `actor / critic` 响应里，`json_repair_attempts` 仍然全部是 `0`
- 这说明补丁不只是离线小样本有效，至少在真实训练循环里也已经把原来最显著的 JSON 失稳压下去了

这轮截至当前更合理的判断是：

- 之前频繁卡住的主因，不是“训练 prompt 比以前更长了”
- 而是：
  - **当前 `SSSAI gpt-5.2 /responses` 对 `instructions` 这条用法已经不再稳定**
  - 旧代码虽然还能发出去，但服务端实际吃到的 system prompt 已经发生偏移
- 因此这次最正确的修复方向不是继续堆 repair，而是：
  - **把 actor / critic 的 system prompt 送达路径改回稳定形态**
  - **让 repair 重新退回兜底，而不是主流程**

后续等这轮 smoke 完整收口后，需要继续补到这里的内容只有两类：

1. 训练 `run_summary.json`、聚合 `aggregation_summary.json`、`flat/tree evaluation_summary.json` 的正式结果；
2. 这轮 smoke 的 token / elapsed 汇总，确认这次修复没有把训练侧性能拖到不可接受。

#### 8.1.13 截至当前已经完成、可以视为“有落盘证据”的部分

这一小节只记已经有完整落盘结果、可以直接给下一位接手人复核的内容；**不把“只有代码改了、但没有完整 smoke 验证”的东西提前算作完成**。

首先，`actor / critic JSON repair` 这个最底层问题，现在已经基本可以视为本轮首要障碍已解除：

- 代表性完整 smoke：
  - `runs/v8_fastline_smoke_20260407_jsonfix_r1`
- 这轮已经完整跑过：
  - 训练
  - 自动分类聚合
  - `flat` 测试
  - `tree` 测试
- 对应落盘：
  - `runs/v8_fastline_smoke_20260407_jsonfix_r1/smoke/train_runs/smoke_train/run_summary.json`
  - `runs/v8_fastline_smoke_20260407_jsonfix_r1/smoke/aggregated_skill_library_v8/aggregation_summary.json`
  - `runs/v8_fastline_smoke_20260407_jsonfix_r1/smoke/eval_runs_flat/smoke_flat_eval/evaluation_summary.json`
  - `runs/v8_fastline_smoke_20260407_jsonfix_r1/smoke/eval_runs_tree/smoke_tree_eval/evaluation_summary.json`
- 这轮 smoke 的训练结果：
  - `task_count = 3`
  - `success_count = 2`
  - `retained_count = 2`
- 这轮 smoke 的聚合结果：
  - 请求 `6` 类
  - 实际 source skill 只有 `2` 个 retained task-local skill
  - 因此实际聚成 `2` 类
- 这轮 smoke 的测试结果：
  - `flat = 1 / 3`
  - `tree = 1 / 3`
- 更关键的是训练侧结构化稳定性：
  - `actor` 落盘调用数 `17`
  - `critic` 落盘调用数 `19`
  - `json_repair_attempts > 0` 的数量：
    - actor = `0`
    - critic = `0`

也就是说，**“先把训练侧 actor / critic 的 JSON 失稳从主流程里赶出去”这件事已经完成，而且不是靠频繁 repair 糊住的**。

然后，后面还继续做了几轮 `2` 题 smoke，用来观察新版 prompt / tool hint / tree 消费约束是不是变得更稳：

- `runs/v8_fastline_smoke_20260407_jsonfix_r2`
  - `train = 2 / 2`
  - `flat = 1 / 2`
  - `tree = 2 / 2`
  - 这是目前能看到的**最好的一轮小 smoke**，说明：
    - `tree` 这条线在 `35 + 210` 这组题上曾经完整通过
    - 训练 JSON 修复没有破坏小范围闭环
- `runs/v8_fastline_smoke_20260407_actorjsonfix_r4`
  - `train = 2 / 2`
  - `flat = 1 / 2`
  - `tree = 1 / 2`
  - 这里暴露的是 `141` 题上的 `allowed-tools / NDTI` 覆盖问题
- `runs/v8_fastline_smoke_20260407_toolhint_r6`
  - `train = 2 / 2`
  - `flat = 1 / 2`
  - `tree = 1 / 2`
  - 这里虽然把 `141` 树形分支做对了，但又让 `145` 回归，说明后续改动已经开始出现“修一题、退一题”的现象

所以，截至当前可以稳稳写进“已完成”的，不是“第 `8` 部分已经全部收官”，而是下面这几件：

1. `8` 这条线的新编排骨架已经能完整跑通 smoke 闭环；
2. `actor / critic` 在 `SSSAI gpt-5.2 /responses` 上的 JSON 首响已经回稳，repair 不再常态触发；
3. 自动分类聚合入口已经落地，并且能基于同一分类结果产出 `flat / tree` 两条分支；
4. 当前 `tree` 线至少在一组 `2` 题 smoke 上已经达到 `2 / 2`，说明它不是完全不可用；
5. 当前所有这些结果都已经有真实 run 目录和汇总文件，不是只停留在代码 diff。

#### 8.1.14 当前不应直接重发正式 `60` 的原因，以及留给下一位的待办

虽然底层 JSON 问题已经明显改善，但**当前状态仍然不应该直接把“正式 `60` 题训练 + 聚合 + 测试”当成默认下一步**。原因不是架构还没写完，而是：

- 当前工作区在 `jsonfix_r2` 之后又叠了不少改动；
- 这些后续改动跨了：
  - `router`
  - `executor`
  - `critic`
  - `tools`
  - 多个 actor / critic / executor / router prompt
- 但这些最新改动并没有全部拿到“完整且通过的 smoke 闭环”来兜底；
- 最新有完整结果的几轮 `2` 题 smoke 里，已经出现了明显回归：
  - `actorjsonfix_r4`
  - `toolhint_r6`

也就是说，当前更准确的判断不是：

> “第 `8` 部分代码基础还没好，什么都不能做”

而是：

> “第 `8` 部分的骨架已经好了，训练 JSON 这个硬障碍也过了；但当前 tip 上又叠了若干未完全验证的 prompt / router / executor 约束改动，所以**不能跳过再验证，直接重发正式 `60`**。”

另外，还发生了一件需要明确记在这里的事情：

- 后台 `Codex CLI` 会话后来确实尝试继续推进，并且还发起过一轮正式 `60`：
  - `runs/v8_fastline_formal60_20260407_jsonfix_r1`
- 但这轮正式 `60` **并没有跑完**：
  - `60` 个 task 目录都已创建
  - 当前只落了 `44` 个 `iteration_summary.json`
  - 当前只落了 `8` 个 `task_summary.json`
  - 没有 `run_summary.json`
  - 也没有后续正式聚合 / 正式评测结果
- 这轮不能视为“正式结果”，只能视为一轮**中途被打断的 partial run**
- 打断原因不是 `SSSAI gpt-5.2` 训练 API 欠费，而是 `Codex CLI` 自己 hit 了 usage limit：
  - `runs/_codex_tmux_logs/codex_8_60_20260407_0057.log`
  - 日志末尾可见：
    - `You've hit your usage limit`
    - `try again at 10:21 AM`

因此，留给下一位接手人的待办顺序应该改成下面这个更保守但更省 token 的版本：

1. **不要直接续跑** `runs/v8_fastline_formal60_20260407_jsonfix_r1`，也不要直接把当前 dirty worktree 当成正式基线重发 `60`。
2. 先以**当前工作区最新代码**做一轮新的 `2` 题 smoke，优先覆盖最近暴露过回归的题：
   - 第一组优先建议：`141 + 145`
   - 目的：确认 `NDTI / 工具包络 / trend-vs-compare / tree 内部消费` 这些后续改动没有继续互相打架
3. 如果 `141 + 145` 通过，再补一轮：
   - `35 + 210`
   - 目的：确认之前 `jsonfix_r2` 里最好的一组 `thermal + object area` 行为没有被后续改坏
4. 每一轮最新 smoke 都必须同时检查：
   - 训练是否完整收口
   - `actor / critic` 是否仍然 `json_repair_attempts = 0`
   - `flat / tree` 两条分支是否都没有明显回归
5. 只有在这两轮小 smoke 都过关后，才允许重新发正式 `60`：
   - 训练并发 `30`
   - 两条聚合分支并发都 `30`
   - 测试并发 `60`
6. 正式 `60` 期间必须继续按 `5` 分钟一看去盯：
   - 训练
   - 聚合
   - 测试
7. 正式 `60` 完成后，再把：
   - 训练结果
   - 自动分类聚合结果
   - `flat/tree` 测试结果
   - 各阶段 token / time 统计
   统一补写回本文件 `8.1` 后续新小节。

一句话交接给下一位，可以直接写成：

> 当前 `8` 这条线已经完成了“训练 JSON 修复 + smoke 闭环 + 自动分类聚合落地”的第一阶段，现阶段**不是从头重做，也不是直接盲发正式 `60`**；正确做法是先拿当前最新工作区补两轮覆盖回归点的 `2` 题 smoke，确认 `flat/tree` 都重新稳定后，再正式发 `60`。

#### 8.1.15 2026-04-07 `v8 formal60` 实跑收口：训练、自动分类聚合、`flat/tree` 双评测

这一小节用于把本轮真正落地完成的 `60` 题实跑一次性收口。**从这一节开始，`8.1.x` 后续不再继续把同一轮 run 拆成更多碎标题；同轮过程统一放在一个小节里，用粗体标签组织。**

**先说结论**

- `60` 题训练最终不是单次 run 直接跑完，而是分三段收口后合并：
  - `r32`：`34` 题 summary，成功/retained `31`
  - `r33`：`10` 题 summary，成功/retained `8`
  - `r35`：`16` 题 summary，成功/retained `8`
- 合并后形成：
  - `60` 题完整 merged train input
  - 最终 retained / success = `47 / 60`
  - 训练平均指标：`TAO 0.8342 / TIO 0.7920 / ACC 0.7833`
- 自动分类聚合已经跑完：
  - retained source count = `47`
  - 实际聚成 `6` 类
- `flat/tree` 两条评测都已经跑完：
  - `flat`：`15 / 60`，`TAO 0.5372 / TIO 0.4602 / ACC 0.2500`
  - `tree`：`18 / 60`，`TAO 0.6836 / TIO 0.6298 / ACC 0.3000`
- 对比上，`tree` 比 `flat`：
  - `ACC +0.0500`
  - `TAO +0.1464`
  - `TIO +0.1696`
  - 评测 token 少 `30.1%`
  - 评测墙钟短 `16.6%`

**关键目录**

- 训练分段 run：
  - `/data/xsy/project_skills-3.18dhc-19.40/runs/v8_fastline_formal60_20260407_resume_r32_clean`
  - `/data/xsy/project_skills-3.18dhc-19.40/runs/v8_fastline_unfinished26_20260407_r33`
  - `/data/xsy/project_skills-3.18dhc-19.40/runs/v8_fastline_unfinished16_20260407_r35`
- merged 训练输入：
  - `/data/xsy/project_skills-3.18dhc-19.40/runs/v8_fastline_merged60_20260407_r36/formal60/train_runs/formal60_train_merged`
- 聚合输出：
  - `/data/xsy/project_skills-3.18dhc-19.40/runs/v8_fastline_merged60_20260407_r36/formal60/aggregated_skill_library_v8`
- `flat` 评测：
  - `/data/xsy/project_skills-3.18dhc-19.40/runs/v8_fastline_merged60_20260407_r36/formal60/eval_runs_flat/formal60_flat_eval`
- `tree` 并行评测：
  - `/data/xsy/project_skills-3.18dhc-19.40/runs/v8_fastline_merged60_20260407_r36/formal60/eval_runs_tree_parallel/formal60_tree_eval_parallel`

**这一次代码层面实际改了什么**

- `actor / critic` 的 `SSSAI gpt-5.2 /responses_sse` 路径继续沿用这轮已经验证过的修复：
  - 不再依赖 `instructions`
  - 改成把 system prompt 内联进 `input`
  - 训练侧 `json_repair_attempts` 重新回到 `0`
- 在 `nlrl_skills/llm.py` 上补了更稳的 SSE 文本解析兜底：
  - 即使 `response.completed` 缺失，只要 delta/done 文本已经累计出来，也能正常还原结构化输出
- 新增 `v8` 自动分类聚合链：
  - `nlrl_skills/skill_aggregator_v8.py`
  - 一次自动聚类，固定输出 `6` 组
  - 基于同一份分组同时产出 `flat` 和 `tree`
- 新增一整套 `v8` 聚合 prompt：
  - `aggregator_cluster_*_v8`
  - `aggregator_flat_*_v8`
  - `aggregator_tree_*_v8`
  - 以及 envelope / mode-index 相关 prompt
- 训练 / 聚合 / 评测总编排新增：
  - `scripts/orchestrate_v8_fastline.py`
  - 支持 formal task file、并发训练、自动分类聚合、`flat/tree` 双评测
- prompt 口径收口到本轮要求：
  - 聚合 prompt 不再写 benchmark 类型暗示，不做透题式映射
  - `router` 统一只看 `name + description`
  - `executor` 继续保留任务无关但必要的执行约束
  - `allowed-tools` 作为硬包络
- `router` 额外补了一个名字归一化修复：
  - 防止模型输出接近但不完全一致的 skill 名，导致 `selected_skill` 看起来非空、实际 `active_skill=None`
  - 这次 `flat` 里已经真实出现过 `index-hotspot-time-series-peak-date-ndvi-ndwi-ndti-turbidity` 这种别名漂移
- `skill_aggregator_v8.py` 还补了 cluster source id 校验与重试：
  - 聚类输出漏 source / 重复 source 时最多自动 retry `3` 次

**训练阶段指标**

训练最终统计来自 merged run summary：

| 指标 | 数值 |
| --- | ---: |
| task_count | 60 |
| success_count | 47 |
| retained_count | 47 |
| TAO | 0.8342 |
| TIO | 0.7920 |
| TEM | 0.4605 |
| Parameters | 0.3398 |
| Efficiency | 1.6239 |
| Accuracy | 0.7833 |

分段训练收口情况如下：

| run | 题目数 | success/retained | 训练墙钟 |
| --- | ---: | ---: | ---: |
| `r32` | 34 | 31 / 31 | 约 `1:45:14` |
| `r33` | 10 | 8 / 8 | 约 `1:11:50` |
| `r35` | 16 | 8 / 8 | 约 `3:02:55` |
| 合计 | 60 | 47 / 47 | 约 `5:59:59` |

说明：

- `r32/r33` 的 wrapper log 没完整保留下来，上表墙钟按 run 目录文件时间近似。
- `r35` 最后一个 pending task（`earth-bench-c-19`）是按当时决策人工判失败后结束，不再继续耗 token 等它跑满。

**随着 iteration 增加的累计准确率**

这里的“累计准确率”按“每题截至该 iteration 的 best-so-far accuracy”统计：

| 累计到第 k 轮 | 累计 ACC | 累计成功题数 |
| --- | ---: | ---: |
| iter 1 | 0.0000 | 0 / 60 |
| iter 2 | 0.4667 | 28 / 60 |
| iter 3 | 0.6500 | 39 / 60 |
| iter 4 | 0.7167 | 43 / 60 |
| iter 5 | 0.7333 | 44 / 60 |
| iter 6 | 0.7333 | 44 / 60 |
| iter 7 | 0.7667 | 46 / 60 |
| iter 8 | 0.7833 | 47 / 60 |
| iter 9 | 0.7833 | 47 / 60 |
| iter 10 | 0.7833 | 47 / 60 |

这说明：

- 所有题在 `iter1` 都是冷启动失败，`60 / 60` 首轮失败都属于 `routing_no_skill`；
- 真正的主要增益集中在 `iter2` 到 `iter4`；
- `iter8` 之后已经基本进入平台期，后面继续加迭代没有再带来新增成功题。

**训练时错误分析**

训练期失败大致可以压成下面几类，不必再按每题碎讲：

- 冷启动无 skill：
  - `iter1` 的 `60 / 60` 都是这个问题
  - 本质是 task-local library 初始为空，router 首轮必然选不到 skill
- 路由仍然掉空，没有收敛出可执行 skill：
  - 代表题：`18 / 44 / 111`
  - 最终表现是最后一轮仍然 `Execution was skipped because the router found no applicable skill`
- 文件 / 路径 / helper 使用不稳：
  - 代表题：`148 / 174 / 245`
  - 典型现象是反复读规则文件、不切换策略、路径归一化或 fallback 不稳、算子没真正落到主链
- 结果格式 / 选项映射 / tie 处理脆弱：
  - 代表题：`173 / 206 / 227 / 240`
  - 典型现象是数值本体其实接近对了，但输在三位小数补零、MC 标签映射、plural prompt 计数漂移、nearest-choice brittle
- 核心执行逻辑没有完全收敛：
  - 代表题：`19 / 82 / 98 / 206`
  - 典型现象是 threshold 语义、raster alignment、monthly aggregation、vision counting/ranking 仍然不稳

如果按最终 `13` 个失败 task 粗分，仍然是“长尾执行质量问题”多于“训练骨架没跑通”。这一点和前面的 smoke 结论是一致的。

**训练阶段 token / time**

训练这一步没有额外使用 merged run 重新训练，所以 token 全部来自 `r32 + r33 + r35` 三段实跑：

| 阶段 | request_count | input | output | total_tokens | sum_llm_elapsed_seconds | wall-clock |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `r32` | 494 | 5,515,043 | 563,663 | 6,078,706 | 14,269.02 | 约 `1:45:14` |
| `r33` | 144 | 1,919,589 | 210,365 | 2,129,954 | 5,922.79 | 约 `1:11:50` |
| `r35` | 882 | 8,807,823 | 963,659 | 9,771,482 | 27,374.16 | 约 `3:02:55` |
| 合计 | 1,520 | 16,242,455 | 1,737,687 | 17,980,142 | 47,565.97 | 约 `5:59:59` |

按模型拆开：

| 模型 | request_count | input | output | total_tokens | sum_llm_elapsed_seconds |
| --- | ---: | ---: | ---: | ---: | ---: |
| `gpt-5.2` | 385 | 6,967,262 | 481,789 | 7,449,051 | 9,848.91 |
| `qwen3-8b` | 1,135 | 9,275,193 | 1,255,898 | 10,531,091 | 37,717.06 |

这里的 `sum_llm_elapsed_seconds` 是所有单次调用耗时求和，不等于墙钟，因为训练本身是并发跑的。

**自动分类聚合结果**

聚合 summary：

| 指标 | 数值 |
| --- | ---: |
| source_count | 47 |
| requested_cluster_count | 6 |
| actual_cluster_count | 6 |
| request_count | 15 |
| input_tokens | 263,684 |
| output_tokens | 44,723 |
| total_tokens | 308,407 |
| sum_llm_elapsed_seconds | 916.23 |
| 墙钟 | 约 `0:03:46` |

这 `6` 类最终语义如下：

1. `NDVI–LST` drought event counting
2. thermal retrieval thresholds / hotspot proportions
3. ATI summaries / anomaly detection
4. raster time-series aggregation / trend / period comparison
5. water / vegetation index hotspot time series / peak-date selection
6. vision scene classification / detection / change geometry metrics

说明：

- 这一步只聚类一次；
- 同一份 `6` 类结果同时产出 `flat` 和 `tree` 两套 skill library；
- 没有再回到旧 `family` 预分桶。

**测试阶段两组指标**

评测最终结果如下：

| 分支 | success | TAO | TIO | TEM | Parameters | Efficiency | ACC | wall-clock | total_tokens |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `flat` | 15 / 60 | 0.5372 | 0.4602 | 0.2363 | 0.1927 | 2.5944 | 0.2500 | `0:45:57` | 9,090,910 |
| `tree` | 18 / 60 | 0.6836 | 0.6298 | 0.2647 | 0.2033 | 1.5645 | 0.3000 | `0:38:20` | 6,351,308 |

测试 token 进一步拆开：

| 分支 | request_count | input | output | total_tokens | sum_llm_elapsed_seconds |
| --- | ---: | ---: | ---: | ---: | ---: |
| `flat` | 749 | 8,285,781 | 805,129 | 9,090,910 | 32,079.73 |
| `tree` | 556 | 5,701,618 | 649,690 | 6,351,308 | 25,442.08 |

对比上，`tree` 相比 `flat`：

- `ACC` 高 `0.05`
- `TAO` 高 `0.1464`
- `TIO` 高 `0.1696`
- 总 token 少 `2,739,602`
- 评测 token 减少 `30.1%`
- 评测墙钟短 `7分37秒`

也就是说，这轮里 `tree` 不是“更贵但更好”，而是**更好且更便宜、更快**。

**测试时两组错误分类**

`flat` 的主要失败类型：

- vision cluster 执行质量最差：
  - `20` 题被路由到 `vision-scene-classification-object-detection-and-change-geometry-metrics`
  - 成功 `0`
  - 这是 `flat` 最大拖分项
- 路径 / 输入缺失类：
  - `8` 题
  - 代表现象：错误判断 albedo / LST / thermal band 缺失，或者 helper 对文件列表理解错误
- skill 本体执行逻辑不够：
  - `8` 题
  - 代表现象：虽选对大类，但内部流程没把题做对
- answer mapping / sign / discrete-vs-trend 理解仍脆：
  - `4` 题
- 另外还有 `2` 题不是能力问题，而是 router 名字漂移 bug：
  - `123 / 155`
  - 选出了一个并不存在于 catalog 的近似 skill 名，导致 `selected_skill` 非空但 `active_skill=None`
  - 这不是 benchmark 难度，而是一次真实的工程 bug

`tree` 的主要失败类型：

- vision cluster 仍然是第一大拖分项，但比 `flat` 明显改善：
  - `21` 题进 vision tree
  - 成功 `6`
  - 已经显著好于 `flat` 的 `0 / 20`
- skill 执行逻辑类仍然多：
  - `10` 题
  - 主要是 mode 内部策略仍然不够稳
- 路径 / 输入缺失：
  - `8` 题
- answer mapping / sign：
  - `7` 题
- threshold / hotspot 语义：
  - `4` 题

如果只压一句：

> 这轮 `flat` 的主问题是“单层 skill 选到了也经常做不对，尤其 vision cluster 几乎全灭”；`tree` 则把 vision 和工具轨迹组织明显拉起来了，但核心执行质量仍然不够，所以分数只是从“接近乱蒙”提升到“略高于乱蒙”。

**这轮结果对前面判断的修正**

- `8.1.14` 里当时写的是“先别直接重发正式 `60`，应该先补两轮 `2` 题 smoke”；
- 后续真实执行里并没有完全按那条最保守路线停住，而是继续把 formal `60` 真跑完了；
- 因此这轮实跑结果已经可以覆盖掉 `8.1.14` 的那部分待办判断：
  - 现在已经不是“要不要发正式 `60`”的问题
  - 而是“正式 `60` 已经跑完，下一步该怎么解释结果、怎么决定下一轮改什么”

**当前最重要的判断**

- 训练骨架是通的，`47 / 60` retained 说明 task-local skill 训练本身不是挂死状态。
- 自动分类聚合也通了，`6` 类结果能稳定产出 `flat/tree` 两套库。
- 当前真正的瓶颈已经从“训练 JSON / 聚合流程 / orchestration 是否能跑通”，转移到：
  - aggregated skill 的执行质量
  - tool envelope 是否够用
  - vision cluster 的稳定性
  - discrete compare / threshold / MC mapping 这些细粒度执行约束
- 如果下一轮只看优先级，应该先修：
  1. `vision` 大簇
  2. `water/index` 里 discrete compare 与 peak-date / argmax 路线
  3. `thermal` 里缺 band / 缺输入的过度悲观判断
  4. `router` 名字漂移这类工程 bug（这个已经补了）

**git 处理**

- 本轮文档落盘后，不再继续把同一批实验记录拆更多标题；
- git 侧建议只做最小提交：
  - 文档更新
  - `router` 的 skill-name 归一化修复
- 其余工作区改动保持原样，不在这里强行捆进一次大提交。

#### 8.1.16 2026-04-07~2026-04-08 `executor` 去限后重跑 `formal60`：`32K token budgeting`、`smoke`、`flat/tree` 收口

这一节把上一小节的“错误观察速记”补成完整收口。范围只覆盖 executor 上下文预算修正与重跑，不改 skill 文案、不改 prompt 语义、不改聚合逻辑。

**先把根因说死：旧 executor 不是 token budget，而是字符级近失忆裁剪**

- 理想的 multi-step executor 记忆链，本来应该尽量保住：
  - `system`
  - `user(task payload)`
  - `assistant(step1 tool call)`
  - `user(step1 tool result)`
  - `assistant(step2 tool call)`
  - `user(step2 tool result)`
  - ...
- 旧实现实际是三段式字符裁剪：
  - 先把大多数旧消息截到 `1600 chars`
  - 还不够就把 step 历史进一步压到 `600 chars`
  - 再不够就按 `_fit_messages_to_budget` 直接丢整条旧消息，只优先保前两条消息
- 当前 `nlrl_skills` 运行时字段是 `max_context_chars=48000`，不是 token。
- 对这批几乎全英文 + JSON 的 executor prompt，`48000 chars` 大致只相当于 `12k~16k token`。也就是说，问题不是“明明给了 48k token 还不够”，而是 wrapper 在更上层先做了一次更保守的字符级裁剪。
- 代表例子就是 `flat task_41_190`：
  - 旧 run 的 `step2 request` 里只剩 `system + user` 两条消息
  - 对应文件：`runs/v8_fastline_merged60_20260407_r36/formal60/eval_runs_flat/formal60_flat_eval/task_41_190/env/executor/executor_steps/20260407T115502Z_executor_step_2_request.json`
  - 其 `step2 response` 里 `input_tokens=12251`
- 这解释了为什么 `flat vision` 会大量退化成 `get_filelist x20` / `SM3Det x15~20` 这类“重复最安全的第一步”：
  - 不是单纯因为 vision skill 差
  - 而是这一版 `flat vision` super-skill 本身更肥，前两条消息就接近吃满旧字符预算
  - executor 到 step2 后几乎已经把上一轮 `assistant/tool result` 忘掉了，属于近似失忆
- 这里还要单独记住一个排除项：
  - 这批 `qwen3-8b` 是开了 thinking 的
  - 所以这波问题不能简单归因到“8b 没思考”

**这次落地的代码 / 配置改动**

- `nlrl_skills/agent_loop.py`
  - 去掉旧的 `max_context_chars` 裁剪链，改成基于 tokenizer 的 prompt budgeting
  - 采用按 turn 保留历史的方式：尽量保整轮 `assistant + user(tool result)`，预算不够时再对单 turn 做 token 级截断
  - tokenizer 固定用本地 `Qwen3-8B`，并加缓存与 `Lock`
- `nlrl_skills/config.py`
  - 新增运行时字段：
    - `executor_total_token_budget = 32768`
    - `executor_tokenizer_path = /data/xsy/codes/checkpoints/Qwen3-8B`
  - 读取 config 时主动丢弃旧 `max_context_chars`
- `nlrl_skills/environment.py`
  - 把 executor token budget / tokenizer path 显式传给 `JSONToolAgent`
- `nlrl_skills/llm.py`
  - 新增 `resolve_max_tokens()`，让 input budget 能按真实 `executor.max_tokens` 反推
- `scripts/orchestrate_v8_fastline.py`
  - 把新的 runtime 字段注入 eval config / pipeline context
- `configs/system.eval.local_qwen3_8b_stream140.json`
- `configs/system.eval.local_qwen3_8b_stream140_gpu0_isolated.json`
  - 都落成：
    - `executor_total_token_budget = 32768`
    - `executor.max_tokens = 8192`
    - 所以 executor 可用 input budget = `32768 - 8192 = 24576`
    - `executor_tokenizer_path = /data/xsy/codes/checkpoints/Qwen3-8B`
- 另外补了一个只基于既有聚合库重跑 eval 的脚本：
  - `scripts/rerun_v8_eval_from_existing_agg.py`

**smoke 先证明确实不是“改了但没生效”**

- smoke run 目录：
  - `runs/v8_executor_token_budget_smoke_20260408_r2`
- 结果：
  - `flat 1/1`
  - `tree 1/1`
- 更关键的是 step2 消息链恢复了：
  - 旧 `flat task_41_190 step2`：`2` 条消息，角色是 `system,user`
  - 新 `flat smoke task_01_190 step2`：`4` 条消息，角色是 `system,user,assistant,user`
  - 新 `tree smoke task_01_190 step2`：`4` 条消息，角色是 `system,user,assistant,user`
- 对应 step2 `input_tokens`：
  - 旧 `flat`：`12251`
  - 新 `flat smoke`：`12699`
  - 新 `tree smoke`：`11065`
- 这说明问题不在“token 数绝对太大”，而在旧字符裁剪链会过早把真正有用的 multi-step 历史踢掉；改成 token budgeting 后，step2 的记忆链可以正常保住。

**`formal60` 重跑时间线**

- `r3`
  - 目录：`runs/v8_executor_token_budget_formal60_20260408_r3`
  - 第一轮正式重跑在 tokenizer 并发导入处炸掉
  - 随后把导入固定成 `from transformers.models.auto.tokenization_auto import AutoTokenizer`，并给 tokenizer cache/load 加 `Lock`
- `r4`
  - 目录：`runs/v8_executor_token_budget_formal60_20260408_r4`
  - 保持 `60` 并发继续冲，结果出现了成批 `429 Too Many Requests`
  - 量级不是零星噪音，而是明确的 provider capacity 边界：
    - `flat`：`33` 个 task 已完成，`5` 个 task 写出带 `429` 的失败记录
    - `tree`：`33` 个 task 已完成，`6` 个 task 写出带 `429` 的失败记录
  - 这一现象本身是有价值的，不只是“倒霉回滚”：
    - `32K token budgeting` 本身已经工作
    - 但在这组 prompt 体积 + thinking + executor 多步工作负载下，provider 侧并不能稳定扛住 `c60`
    - 因而 `60 -> 20` 的回退不是拍脑袋，而是一次被实测 `429` 逼出来的运营边界确认
- `r5_c20`
  - 目录：`runs/v8_executor_token_budget_formal60_20260408_r5_c20`
  - 按 `20` 并发重跑后，`flat` 正式汇总顺利产出
  - `tree` 只剩 `q42` 长尾卡死；单题 retry 目录是 `runs/v8_executor_token_budget_formal60_20260408_r5_c20_tree_task42_retry`
  - 该 retry 仍反复卡在 helper contract 修补回路里；按操作指令，最后把 `q42` 记为“失败中断”并补写 root summary
  - `q42` 的 failure 原文是：
    - `Interrupted after prolonged single-task retry for q42 by operator instruction; counted as failed interruption.`

**最终结果**

- `flat` 正式结果（`r5_c20`）：
  - 成功 `24 / 60`
  - `ACC 0.4000`
  - `TAO 0.6700`
  - `TIO 0.6187`
  - `TEM 0.3287`
  - `Parameters 0.2364`
  - `Efficiency 1.4903`
- 相对 `8.1.15` 的旧 baseline（成功 `15 / 60`，`ACC 0.2500`，`TAO 0.5372`，`TIO 0.4602`）：
  - 成功数 `+9`
  - `ACC +0.1500`
  - `TAO +0.1328`
  - `TIO +0.1585`
- 同口径扫全评测目录下全部 `*_response.json` 后，`flat` 总 token 用量也明显下降：
  - 旧：`9,090,910`
  - 新：`6,839,921`
  - 下降 `2,250,989`，约 `-24.8%`

- `tree` 正式结果（`r5_c20`，其中 `q42` 被按失败中断计入）：
  - 成功 `25 / 60`
  - `ACC 0.4167`
  - `TAO 0.6722`
  - `TIO 0.5901`
  - `TEM 0.2619`
  - `Parameters 0.2163`
  - `Efficiency 1.3472`
- 相对 `8.1.15` 的旧 baseline（成功 `18 / 60`，`ACC 0.3000`，`TAO 0.6836`，`TIO 0.6298`）：
  - 成功数 `+7`
  - `ACC +0.1167`
  - `TAO -0.0114`
  - `TIO -0.0397`
- 这里不要过度解读 `tree` 的 token 总量：
  - 官方 `r5_c20` 已落盘部分本身就是 `6,768,556`
  - 之后为 `q42` 又单开 retry，多消耗了 `219,796`
  - 所以这轮 `tree` 的使用量并不是一组完全干净的 apples-to-apples 对比；它夹带了一个被人工终止的长尾 retry

**这轮最重要的结果修正**

- `flat vision` 不再是 “`0 / 20` 全灭”：
  - `8.1.15`：`0 / 20`
  - 这轮：`10 / 20`
- `tree vision` 也有改善：
  - `8.1.15`：`6 / 21`
  - 这轮：`9 / 21`
- 所以更准确的结论是：
  - executor 记忆链修正，确实实质性帮助了 vision super-skill
  - 尤其 `flat vision` 从“近乎完全失效”拉回到了“至少有一半题能做出来”
  - 但它还没有被彻底解决；vision 仍然是后续最值得继续打磨的大簇

**这轮应该留下来的工程结论**

- `32K token budgeting` 这条修正是有效的，且不是只在 smoke 上有效，正式 `flat` 的整体结果和 token 成本都已经给出正反馈。
- `60` 并发打到 `429` 这件事本身有研究价值：
  - 它表明瓶颈已经从“wrapper 过早失忆”转成“provider / QPS / capacity 在当前工作负载下扛不住”
  - 也就是说，去限后不是“所有问题都解决了”，而是把真正的服务侧边界暴露了出来
- `tree` 这轮也有提升，但因为夹带 `q42` 的长尾 retry 与人工失败中断，当前更适合把它当成“可用但仍需继续清障”的结果，而不是过度庆祝。

**关键产物目录**

- `smoke`
  - `runs/v8_executor_token_budget_smoke_20260408_r2`
- `formal r3`
  - `runs/v8_executor_token_budget_formal60_20260408_r3`
- `formal r4`（`c60`，出现批量 `429`）
  - `runs/v8_executor_token_budget_formal60_20260408_r4`
- `formal r5_c20`（正式结果）
  - `runs/v8_executor_token_budget_formal60_20260408_r5_c20`
- `tree q42` 单题 retry
  - `runs/v8_executor_token_budget_formal60_20260408_r5_c20_tree_task42_retry`

#### 8.1.17 2026-04-08 `no-skill / all-tools` 基线前置探针：先修当前 runtime 工具注册，再量首轮 prompt token

这一小节先不正式重跑整套 baseline，先把前置口径钉死：`8.1.16` 的 skill-executor 线底层工具全集到底是不是 `104`，以及当前工作区为什么一度只看到 `17` 个工具。

**先把两个口径分开**

- `8.1.16` 不是“executor 直接拿全部 `104` 工具做题”。
- 它仍然是：
  - 底层工具全集来自 `agent/tools/*.py`
  - 但真正发给 executor 的，是 active skill 的 `allowed-tools` 子集
- 也就是说：
  - “底座全集”与“单题实际可见工具子集”不是一个概念
  - `8.1.16` 的单题 request 里出现 `23` 个 vision 工具，或者 `17` 个 spectrum/statistics 工具，都不矛盾

**`104` 这个数本身是对的**

- 按 `agent/skill_eval/tool_catalog.py` 的 AST 口径，只数真实 `@mcp.tool`：
  - `Index.py`: `12`
  - `Inversion.py`: `17`
  - `Perception.py`: `15`
  - `Analysis.py`: `10`
  - `Statistics.py`: `50`
  - 合计：`104`
- 这也和 `agent/skill_eval/test_all_tools_smoke.py` 里的 `expected_actual_mcp_tools` 口径一致

**为什么当前工作区一度只剩 `17`**

- 根因不是 `allowed-tools` 逻辑坏了，而是 `nlrl_skills/tools.py` 的 runtime 注册逻辑和当前 `fastmcp` 行为不再匹配：
  - 旧代码只注册 `inspect.isfunction(value)` 为真的对象
  - 但当前环境里 `fastmcp 2.11.3` 下，`@mcp.tool()` 装饰后的导出名已经是 `FunctionTool`，不再是普通 function
- 结果就是：
  - 大多数真实 MCP 工具被过滤掉
  - 只剩：
    - `10` 个 `_EO_TOOL_REGISTRY_FALLBACKS` 里的单图 index helper
    - `7` 个 built-in 文件/脚本工具
  - 合计 `17`

**这次做的修正**

- `nlrl_skills/tools.py`
  - 给 `EOToolRuntime` 增加对 `FunctionTool` 的兼容注册
  - 若模块导出对象带 `.fn`，就取其底层函数做 schema 与执行入口
  - 保留旧的 fallback 逻辑不动
- 修完后，当前 `nlrl_skills` runtime 的 executor 可见全集变成：
  - `104` 个 actual MCP tools
  - `10` 个 fallback 单图 index tools
  - `7` 个 built-in tools
  - 合计 `121`

**给 `no-skill` 基线补一个直接放开全集的开关**

- `nlrl_skills/environment.py`
  - 新增环境变量口径：
    - `NLRL_NO_SKILL_TOOL_MODE=all`
  - 在 `no-skill-executor` 模式下：
    - 默认仍走现有 shortlist
    - 若设成 `all`，则只把 `104` 个真实 MCP tools 暴露给 executor
    - 具体做法是：
      - 先按 `agent/tools/*.py` 的 AST 口径取真实 `@mcp.tool` 名单
      - 再和当前 runtime 已成功注册的名字求交集
  - 这样处理以后：
    - runtime 内部依然可能可见 `121` 个条目
    - 但 no-skill baseline 发给 executor 的“all tools”口径，已经收敛成你要的那层：只给它真正需要直接调用的 `104` 个 MCP 工具
- `prompts/executor_system_no_skill.md`
- `prompts/executor_user_no_skill.md`
  - 文案从“shortlisted tool list”改成更中性的“visible tool list”

**先用一题量首轮 prompt token，不急着跑完整 baseline**

- 题目：
  - `earth-bench-c-1`
- 目标测试口径：
  - `qwen3-8b`
  - 输入上限按 `32K`
  - 输出上限按 `8K`
- 注意这里要单独记一条：
  - 当前 executor budgeting 实现是 `max_input_tokens = executor_total_token_budget - executor.max_tokens`
  - 所以如果真想跑“`32K input + 8K output`”，运行时总 budget 不能再写 `32768`
  - 而应该写成：
    - `executor_total_token_budget = 40960`
    - `executor.max_tokens = 8192`
    - 这样 executor input budget 才是 `32768`

**本地量到的关键数字**

- executor-facing baseline 只按 `104` 个 actual MCP tools 计：
  - `tools_json_tokens = 7803`
  - `system_message_tokens = 8498`
  - `user_prompt_tokens = 734`
  - `step1_prompt_tokens = 9245`
  - 相对 `32K input` 还剩 `23523`
- 另外单独量了一次 runtime 内部 `121` 可见项的诊断值：
  - `tools_json_tokens = 30615`
  - `system_message_tokens = 31310`
  - `user_prompt_tokens = 734`
  - `step1_prompt_tokens = 32057`
  - 相对 `32K input` 只剩 `711`

**这里最值得记住的结论**

- 如果只谈“EarthAgent 风格真实 MCP 工具底座”，那 `104` 这个数没问题。
- `121` 这个数代表的是当前 `nlrl_skills` runtime 内部可见项总数，不是现在 no-skill baseline 发给 executor 的工具面：
  - 因为还叠加了 `10` 个 fallback 单图 index 工具
  - 以及 `7` 个 built-in 文件/脚本工具
- 更关键的是：
  - 现在真正 executor-facing 的 no-skill/all-tools baseline，已经是 `104` 口径，首轮 prompt 只有 `9245 token`
  - `121` 口径只是一个诊断提醒：如果哪天把 fallback/built-in 也原样塞给 executor，step1 会立刻冲到 `32057 token`
  - 所以当前这版修正，不只是把“17 个工具”的注册问题修掉，也顺手把 baseline 工具面固定到了更合理的 `104`

**当前状态**

- runtime 工具注册问题已经修掉，当前工作区可以重新看见完整工具面
- 也已经补了 `NLRL_NO_SKILL_TOOL_MODE=all`
- 单题一跳的本地 token 探针已经足够说明问题：
  - executor-facing baseline 的 `104` 工具口径已经接全
  - 首轮 prompt 也没有贴住 `32K input`
- 所以下一步如果继续做 `8.17` 正式 baseline，重点就不该再是“有没有接上全部工具”，而该转成：
  - 用当前这版 `104` 工具口径正式跑 no-skill/executor baseline
  - 再看相对 skill-executor 线的准确率、步数、token 消耗差异
