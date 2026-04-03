# 19.40 运行时六类 Skill 反向映射

这组文档不是去复述外部参考技能库，而是直接从 `19.40` 代码里把运行时真正生效的 6 类内嵌 skill 反向拆出来。

这里的核心判断是：

- 外部 `SKILL.md` 只是参考来源之一，不是 `19.40` 的运行时真相
- `19.40` 里的有效 skill，其实是多段代码拼出来的组合体，不只是 `SkillSpec`
- 后面如果你要做“每任务训练一个 skill，再聚合”的训练线，字段应该对齐这里，而不是对齐外部那 6 份不统一的 markdown

## 运行时里的“有效 skill”由什么组成

`19.40` 里真正会影响 planner / direct-executor 的 skill，可以近似看成下面这个组合：

```text
effective_skill
= SkillSpec
+ route_skill() 语义路由
+ infer_question_profile() 的 intent / file priors / tool policy
+ shortlist_tools() 产出的最终 shortlist
+ _skill_guidance_lines() 的 skill 专项提示
+ direct_executor / planner prompt 里的显式注入字段
```

对应代码入口：

- [`agent/skill_eval/skill_router.py`](../../agent/skill_eval/skill_router.py)
- [`agent/skill_eval/tool_router.py`](../../agent/skill_eval/tool_router.py)
- [`agent/skill_eval/skill_llm_planner.py`](../../agent/skill_eval/skill_llm_planner.py)
- [`agent/skill_eval/direct_executor.py`](../../agent/skill_eval/direct_executor.py)

## 统一字段定义

下面这套字段，是我按 `19.40` 的真实消费方式反推出来的统一 schema。

| 字段 | 含义 | 主要来源 | 主要消费者 |
| --- | --- | --- | --- |
| `skill_id` | 运行时唯一标识 | `skill_router.py` | planner, direct-executor, trace |
| `display_name` | 展示名 | `skill_router.py` | planner, direct-executor |
| `description` | 该 skill 的任务说明 | `skill_router.py` | direct-executor |
| `domain` | 所属大域：`spectrum / products / rgb` | `tool_router.py` | shortlist, planner |
| `benchmark_question_range_prior` | benchmark id 段的兜底先验 | `skill_router.py` | route fallback |
| `semantic_route_signals` | 真正的语义路由触发词/文件线索 | `route_skill()` | route |
| `seed_allowlist` | skill 自己声明的初始工具范围 | `SkillSpec.tool_allowlist` | `focus_tools` 计算 |
| `notable_runtime_extra_candidates` | 即使不在 `seed_allowlist`，也可能被 domain/profile 拉进 shortlist 的工具 | `tool_router.py` | shortlist, direct-executor |
| `focus_tools_rule` | `focus_tools = shortlist ∩ seed_allowlist` | `skill_llm_planner.py` | planner, direct-executor |
| `subfamilies_or_intents` | 该 skill 内部再细分的子任务/意图 | `infer_question_profile()` | shortlist bias, planner |
| `special_file_priors` | 从文件名反推出来的先验 | `_infer_file_priors()` | shortlist bias, planner |
| `planner_guidance` | 专门写进 planner / direct-executor 的技能规则 | `_skill_guidance_lines()` | planner, direct-executor |
| `direct_executor_exposure` | 单智能体执行时真正看到的 skill 信息 | `direct_executor.py` | direct-executor |
| `notes_or_limits` | 这条 skill 当前实现的边界和缺口 | 综合反推 | 设计/训练参考 |

## 这套字段和原始 `SkillSpec` 的关系

原始 `SkillSpec` 只有 6 个字段：

- `skill_id`
- `display_name`
- `description`
- `question_range`
- `tool_allowlist`
- `trigger_keywords`
- `notes`

但这还不够描述运行时 skill。因为：

1. `tool_allowlist` 不是最终执行边界
   真实硬边界是 `shortlist_tools()` 产出的 shortlist。direct-executor 只能执行 shortlist 里的工具，而不是只能执行 `tool_allowlist` 里的工具。

2. `focus_tools` 只是 skill prior，不是 allowlist 本体
   planner / direct-executor 里看到的是 `focus_tools = shortlist ∩ seed_allowlist`，它只是告诉模型“这类 skill 里当前 shortlist 里最像核心工具的是哪些”。

3. `notes` 基本没有直接进 prompt
   `SkillSpec.notes` 主要是静态说明，当前实现里并没有像 `description`、`guidance_lines` 那样被直接注入 planner / direct-executor。

4. 关键约束很多被重编码在别处
   真正强约束来自 `infer_question_profile()` 的 `required/preferred/discouraged`、`canonical_rules`，以及 `_skill_guidance_lines()` 的专门规则。

## 现在最重要的设计结论

如果后面训练侧要外化 skill 字段，至少应该能表达下面这些信息：

- 路由信号
- 初始工具范围
- 运行时可能补充进来的工具范围
- 子任务/intent 划分
- 文件先验
- tool policy: `required / preferred / discouraged`
- canonical repeated-block 规则
- planner/direct-executor 都能消费的 guidance 规则

如果没有这些字段，只保留一个 `tool list + notes`，那就会退回到你前面担心的那个问题：训练出来的 skill 很短，但运行时约束其实都散落在代码里，最后两边对不上。

## 文件列表

- [earth-spectrum-thermal-retrieval.md](./earth-spectrum-thermal-retrieval.md)
- [earth-spectrum-drought-stress.md](./earth-spectrum-drought-stress.md)
- [earth-product-timeseries.md](./earth-product-timeseries.md)
- [earth-product-derived-index-change.md](./earth-product-derived-index-change.md)
- [earth-product-raster-arithmetic.md](./earth-product-raster-arithmetic.md)
- [earth-rgb-perception-change.md](./earth-rgb-perception-change.md)
