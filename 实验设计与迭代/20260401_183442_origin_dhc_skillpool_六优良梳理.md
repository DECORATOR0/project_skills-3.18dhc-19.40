# origin / DHC / skill-pool 六优良梳理

时间：2026-04-01 18:34:42 +0800

这份文档专门把三个对象分开：

- `origin`：`/data/xsy/project_skills-3.18dhc-origin`
- `当前 DHC`：`/data/xsy/project_skills-3.18dhc`
- `当前 skill-pool`：`/data/xsy/skill-pool`

我下面的回答会尽量避免把三者混在一起。

---

## 1. origin 的“三模式”到底是什么

如果说的是 `origin` 项目根目录下 `nlrl_skills.cli` 的核心运行模式，那么它的三个核心运行模式是：

1. `debug-single-task`
2. `train-tasks`
3. `evaluate-tasks`

对应代码在：

- `/data/xsy/project_skills-3.18dhc-origin/nlrl_skills/cli.py`

具体区别：

### 1.1 `debug-single-task`

- 作用：对单个 task 跑一轮完整闭环，主要用于调试和观察。
- 本质：单题训练模式。
- 会跑完整的 `router -> executor/env -> critic -> actor` 闭环。
- 会改 skill 库，会写 experience buffer。
- 适合看单题为什么失败、actor 到底怎么改 skill。

### 1.2 `train-tasks`

- 作用：连续对多个 task 跑训练。
- 本质：批量训练模式。
- 也会经过 `router -> executor/env -> critic -> actor`。
- 会持续更新 `skill_library/` 和 `experience_buffer.jsonl`。
- 用来“长技能库”。

### 1.3 `evaluate-tasks`

- 作用：拿当前 skill 库做纯评估。
- 本质：固定 skill 库的测试模式。
- 不再跑 critic/actor 改 skill。
- 只看当前 skill 库在 benchmark 上的表现。

### 1.4 需要注意的一个混淆点

`origin` 里其实还有另一套“阶段划分”，那是 `agent/skill_eval` 里的三阶段：

1. planning only
2. parameter generation + tool execution
3. final answer selection

这个三阶段是在：

- `/data/xsy/project_skills-3.18dhc-origin/agent/skill_eval/README.md`
- `/data/xsy/project_skills-3.18dhc-origin/agent/skill_eval/staged_runner.py`

所以这里其实有两个“三分法”：

- `nlrl_skills` 的三个运行模式：`debug-single-task / train-tasks / evaluate-tasks`
- `agent/skill_eval` 的三个执行阶段：`plan / parameter+execute / answer`

---

## 2. origin 到底有没有“自己带着 6 个 skill”

### 2.1 要先分清楚：origin 里其实并存两套 skill 体系

#### A. `nlrl_skills` 这套外部 skill 库

这套是项目 README 里写的当前训练出来的 skill 包，存在：

- `/data/xsy/project_skills-3.18dhc-origin/skill_library/`

README 里列的是 4 个 skill：

- `ndvi-lst-tvdi-annual-trend`
- `ndvi-lst-tvdi-spike-count`
- `ndvi-lst-tvdi-single-date-threshold-exceedance`
- `ndvi-lst-tvdi-multidate-area-exceedance-count`

也就是说：

- `origin` 的外部 skill 库不是 6 个
- 而是当时训练出来的 4 个 TVDI 方向 skill

#### B. `agent/skill_eval` 这套代码级 6-skill family

这套在：

- `/data/xsy/project_skills-3.18dhc-origin/agent/skill_eval/skill_router.py`

这里内建了 6 个 `SkillSpec`：

- `earth-spectrum-thermal-retrieval`
- `earth-spectrum-drought-stress`
- `earth-product-timeseries`
- `earth-product-derived-index-change`
- `earth-product-raster-arithmetic`
- `earth-rgb-perception-change`

所以结论是：

- **origin 项目整体并不是“自己带着外部 6 个 skill 包”**
- **但是 `agent/skill_eval` 这条评估子系统内部，确实自带 6 个代码级 skill family**

这两套不是一回事。

### 2.2 origin 的评估测试里，skill 是怎么选的

如果说的是 `origin/agent/skill_eval` 这条评估链，那么：

- 选 skill 不是 LLM router
- 是 `route_skill()` 里的代码路由

代码在：

- `/data/xsy/project_skills-3.18dhc-origin/agent/skill_eval/skill_router.py`

它的逻辑是：

1. 先看题目关键词和文件特征做语义规则分流
2. 如果前面规则没命中，再用题号区间兜底

也就是说：

- **不是一题一题 hardcode**
- **但也不是让模型自由在 6 个 skill 里挑**
- **而是代码先决定 skill bucket**

而且这个路由已经精确到关键词层面，比如：

- `tvdi / drought / dryness / ATI` -> `earth-spectrum-drought-stress`
- `split-window / single-channel / band 31 / band 32 / lst` -> `earth-spectrum-thermal-retrieval`
- `png/jpg/building/airport/centroid/distance/destroyed` -> `earth-rgb-perception-change`

如果关键词没命中：

- `qid <= 100` -> thermal
- `qid <= 188` -> product-timeseries
- 否则 -> rgb

所以 skill 分配本身在这条链上是很强的代码先验。

### 2.3 origin 对工具链顺序是怎么做的

这个地方要特别区分：

#### 先说 `origin` 本身

`origin` 里虽然已经有：

- `/data/xsy/project_skills-3.18dhc-origin/agent/skill_eval/skill_planner.py`

这个模板文件，

**但在 origin 本身，它不是默认主路径。**

origin 的 README 和 `skill_llm_planner.py` 写得很清楚：

- 主路径：`skill-llm-json`
- 次路径：`skill-llm-tagged-fallback`
- 模板：`skill-template-fallback`

也就是：

- origin 默认仍然是 `LLM planner` 产出完整 `tool_sequence`
- `skill_planner.py` 只是 fallback
- 不是像当前 DHC 那样默认强制模板

所以对于 `origin` 来说：

- 有模板文件
- 但**不是默认直接用模板指定工具链**

#### 再说模板文件到底细到什么程度

`skill_planner.py` 不是按题号查 gold trajectory。

它的粒度是：

- 先按 `skill_id` 分到一个模板函数
- 再在模板函数里根据题面关键词、年份、compare/trend 等模式拼出顺序

例如 drought 模板里大致是：

- 先 `get_filelist`
- 如果题面是 ATI 就用 `ATI`，否则用 `compute_tvdi`
- 如果是 `trend/annual`，后面接重复的 `calculate_tif_average`，再 `calc_batch_image_mean`，再 `compute_linear_trend`
- 如果是 `threshold/severity`，后面接 `calculate_threshold_ratio`
- 如果是 `difference/compare`，就做两段再接 `difference`

所以它是：

- **family 级别的规则模板**
- **不是每题一份 gold 查表**

### 2.4 那么 origin 里的“6 个 skill”有没有用

对 **origin 本身** 来说，6 个 skill 不是没用。

因为 origin 默认路径里，6 个 skill 仍然负责：

1. 代码路由
2. 工具 allowlist / shortlist 范围
3. planner prompt 里的 skill-specific guidance
4. 后续参数和执行阶段的上下文约束

所以对 origin 来说更准确的说法是：

- 6 个 skill 不是外部文档 skill
- 而是 **代码里的 6 个运行时 skill family**

真正“让 skill 看起来像没用了”的，不是 origin，而是 **后来的当前 DHC 最新实验**：

- 它把 planner 又进一步强化成了 `skill-template-forced`
- 于是“工具顺序”这一层基本被代码模板接管了

所以请务必区分：

- **origin**：skill 仍然是主路径里的一部分，模板只是 fallback
- **当前 DHC 最新 run**：skill family 仍在，但工具顺序已经基本由代码模板主导

### 2.5 后面流程

你对后半段的理解基本是对的：

- planner 先出工具链
- parameter worker 逐步出参数
- executor 执行工具
- 最后 answer selector 选答案

这一点和你一贯认知是一致的。

---

## 3. 最近一次 DHC vs skill-pool 六优良实验，对比后到底说明什么

这里我对比的是最近一次正式 `6优良` run：

### 3.1 当前 DHC 最新 6优良 run

路径：

- `/data/xsy/project_skills-3.18dhc/runs/skill_eval_full/qwen3_8b_paid_skill_eval_parallel100_url34_sets/20260401_111039/`

总 summary：

- `count = 248`
- `avg_accuracy = 0.3508`
- `avg_efficiency = 0.5936`
- `avg_tool_any_order = 0.5927`
- `avg_tool_in_order = 0.4748`
- `avg_tool_exact_match = 0.4357`
- `avg_parameter_accuracy = 0.001`

并且我统计了全量 `execution_record.json`，结果是：

- `planning_source = skill-template-forced` 的题数：`248 / 248`

也就是说：

- 当前 DHC 这条 run **全部**都是强制模板规划
- 不是 origin 那种“LLM planner 为主，模板 fallback”

### 3.2 当前 skill-pool 最新 6优良 run

路径：

- `/data/xsy/skill-pool/project_skills/runs/evaluate_collaborator6_docref_allqwen/20260401_144845_collaborator6_docref_allqwen3888_global_eval_parallel100_url35_noproxy/`

总 summary：

- `task_count = 248`
- `success_count = 41`
- `Accuracy = 0.1653`
- `TAO = 0.5466`
- `TIO = 0.4564`
- `TEM = 0.3619`
- `Efficiency = 0.5414`
- `Parameters = 0.1758`

此外：

- `no_selected_skill = 4`
- `fallback_count = 3`

### 3.3 先给结论

你怀疑“DHC 那边 skill 选定不难，主要是工具顺序太黄金所以分数异常高”，这个方向 **大体是对的，但要修正一条：不是参数黄金，而是工具链黄金**。

原因如下。

### 3.4 关键对比结果一：DHC 并不是“参数特别黄金”

如果 DHC 真的是靠参数层面“几乎按 gold 调工具”把分数顶上去，那么它的 `parameter_accuracy` 应该很高。

但事实正相反：

- 当前 DHC：`avg_parameter_accuracy = 0.001`
- 当前 skill-pool：`avg_parameter_accuracy = 0.1758`

所以：

- **DHC 的高分不是靠参数精确对齐 gold**
- 至少不是“参数这层更黄金”

### 3.5 关键对比结果二：skill family 的选定不是主分歧点，但也不是完全无关

我把两边同题的 selected skill 做了对比。

结果：

- 同题选到同一个 skill family：`183 / 248 = 73.79%`

这说明：

- skill family 的差异当然存在
- 但它不是“完全对不齐”
- 大多数题两边其实已经落到同一个大类里了

所以你说“skill 的选定倒不是那么困难”，这个判断是有根据的。

### 3.6 关键对比结果三：真正的大分歧在工具链顺序

我再把两边同题的 **planned tool sequence** 做了逐题对比。

结果：

- 同题工具序列完全一样：`33 / 248 = 13.31%`

也就是说：

- 两边就算很多题进了同一个 skill family
- 但最后真正生成出来的工具链大部分还是不一样

这个差异比 skill family 的分歧大得多。

### 3.7 更关键的一条：当两边工具链一样时，分数差几乎消失

我把 248 题分成两桶：

#### A. 两边计划出的工具链完全一样

- 题数：`33`
- DHC 平均准确率：`0.3333`
- skill-pool 平均准确率：`0.3333`

#### B. 两边计划出的工具链不一样

- 题数：`215`
- DHC 平均准确率：`0.3535`
- skill-pool 平均准确率：`0.1395`

这个结果非常关键。

它几乎可以直接说明：

- **这次两边分数差的主因，确实是工具链规划差异**
- 不是简单的 skill family 选错
- 更不是 DHC 在参数层面更黄金

### 3.8 再看“同 skill”的桶，结论还是一样

我又把题分成：

#### A. 两边选到同一个 skill family

- 题数：`183`
- DHC 平均准确率：`0.3279`
- skill-pool 平均准确率：`0.1639`
- DHC 平均 `TAO`：`0.6147`
- skill-pool 平均 `TAO`：`0.5856`
- DHC 平均 `TEM`：`0.4516`
- skill-pool 平均 `TEM`：`0.4000`
- DHC 平均 `parameter_accuracy`：`0.0014`
- skill-pool 平均 `parameter_accuracy`：`0.1949`

也就是说，即使在“skill 已经选对同一类”的题上：

- DHC 仍然明显更高分
- 但它的参数准确率反而更低

所以差异还是主要来自：

- **DHC 的模板工具链更贴 benchmark canonical trajectory**

### 3.9 关于 skill-pool 这边“注入不合理”的问题

这个问题确实存在，而且会进一步拉低 `skill-pool`。

现在这套 docref 外部 skill 在 `skill-pool` 里有一个明显问题：

- `allowed-tools: Read, Glob, Grep, Bash(python *), Edit`

被 `skills.py` 直接按空格拆成：

- `Read,`
- `Glob,`
- `Grep,`
- `Bash(python`
- `*),`
- `Edit`

这些都不是实际 EO 工具名。

所以 `skill-pool` 在 planner 里很容易退回默认工具集，而不是拿到真正合理的 skill-specific tool scope。

这会进一步扩大两边工具链不对齐的问题。

但即便把这个 bug 暂时放一边，上面的统计也已经说明：

- **当前 DHC 最新 run 的高分核心仍然是 template-forced 工具链**

### 3.10 这一段最重要的最终判断

我会把结论压成下面三句：

1. **origin 本身并不是当前 DHC 最新 run 那种“全量强制模板”**；origin 默认仍是 LLM planner 主路径，模板只做 fallback。
2. **当前 DHC 最新 6优良 run 的高分，不是因为参数特别黄金，而是因为工具链顺序被 code template 强约束到了更贴 benchmark 的形态。**
3. **和 skill-pool 的差距，主因更偏向“工具链规划方式不同”，而不是“skill family 根本不会选”。**

---

## 4. 对你问题的直接短答

### Q1

origin 的三个核心运行模式是：

- `debug-single-task`
- `train-tasks`
- `evaluate-tasks`

如果说的是 `agent/skill_eval` 内部，它又有 `plan / parameter+execute / answer` 三阶段。

### Q2

origin 不是“自己带着 6 个外部 skill 包”，而是：

- 外部 `skill_library/` 有 4 个训练出来的 skill
- `agent/skill_eval` 内部有 6 个代码级 skill family

并且在 origin 的 `agent/skill_eval` 里：

- skill 选择是代码路由，关键词优先，题号区间兜底
- 但工具链顺序默认还是 LLM planner 生成
- `skill_planner.py` 模板在 origin 里只是 fallback，不是主路径

所以对 origin 来说，6 个 skill 不是没用。

### Q3

最近一次对比说明：

- 当前 DHC 的参数并不比 skill-pool 更黄金
- 但它的工具链顺序明显更贴 benchmark
- 两边同题同 skill 的比例有 `73.8%`
- 两边同题同工具链的比例只有 `13.3%`
- 当工具链一样时，两边准确率几乎一样
- 当工具链不一样时，DHC 明显更高

所以你那句“主要是工具那边给定得太黄金了导致分数异常高”，基本可以改写成更准确的一句：

> 当前 DHC 最新 run 的优势主要来自 code template 强约束下的 benchmark-faithful 工具链，而不是来自 skill 选择本身，也不是来自参数层面的黄金对齐。

---

## 5. origin 三条主线架构图

下面这张图只针对 `project_skills-3.18dhc-origin`，把你容易混的三条线放在一张图里：

- `nlrl_skills` 训练
- `nlrl_skills` 用 skill 池评估 `agent/skill_eval` 用预置 6 skill family 评估

```mermaid
flowchart TD
    A["project_skills-3.18dhc-origin"]

    A --> B0["nlrl_skills 训练线"]
    A --> C0["nlrl_skills 评估线"]
    A --> D0["agent skill_eval 预置 6 skill 线"]

    B0 --> B1["train-tasks 或 debug-single-task"]
    B1 --> B2["加载 benchmark task"]
    B2 --> B2A{"训练 skill 维护模式"}

    B2A --> B3["现有模式: discover_skills 读取当前 skill 池"]
    B3 --> B4["LLM SkillRouter 读取 router 配置"]
    B4 --> B5["选中 skill 并加载描述与工具范围"]
    B5 --> B6["executor 执行做题"]
    B6 --> B7["评估当前结果"]
    B7 --> B8["critic 给 reward"]
    B8 --> B9["actor 决定改 skill"]
    B9 --> B10["写回或合并 skill 文件"]
    B10 --> B11["skill_library 更新"]

    B2A --> F3["新分支: 每任务并行发起"]
    F3 --> F4["每个 task 只维护一条 skill"]
    F4 --> F5["单条 skill 的字段 / prompt 对齐 direct-executor 测试口径"]
    F5 --> F6["单 executor 执行做题"]
    F6 --> F7["评估当前结果"]
    F7 --> F8["critic 给 reward"]
    F8 --> F9["actor 只改写该 task 对应的单条 skill"]
    F9 --> F10["后续再做聚合 / 去重 / 合并"]
    F10 --> B11

    C0 --> C1["evaluate-tasks"]
    C1 --> C2["加载 benchmark task"]
    C2 --> C3["discover_skills 读取当前 skill 池"]
    C3 --> C4["LLM SkillRouter 仍使用 router 配置"]
    C4 --> C5["选中 skill 并加载工具范围"]
    C5 --> C6["executor 执行做题"]
    C6 --> C7["evaluation_summary"]

    D0 --> D1["加载 benchmark question"]
    D1 --> D2["infer_question_profile 和 shortlist_tools"]
    D2 --> D3["代码 route_skill 路由到 6 个 SKILL_SPECS"]
    D3 --> D4["保留完整 shortlist，并从 skill allowlist 提取 focus tools 作为 guidance"]
    D4 --> D4A{"后半段执行模式"}

    D4A --> D5["多智能体协作 staged"]
    D5 --> D6["LLM planner 在 shortlist 内生成完整工具链"]
    D6 --> D7{"planner 是否失败"}
    D7 --> D8["未失败 进入 parameter worker"]
    D7 --> D9["失败时走 skill_planner 模板 fallback"]
    D9 --> D10["代码生成 tool chain"]
    D8 --> D11["逐步给每个 planned tool 填参数"]
    D10 --> D11
    D11 --> D12["tool runtime 或 executor 执行"]
    D12 --> D13["final answer selector"]

    D4A --> E5["单智能体 direct-executor"]
    E5 --> E6["仍拿到完整 shortlist 与 routed skill guidance"]
    E6 --> E7["同一 executor 自主逐步选工具并执行"]
    E7 --> E8["同一 executor 直接给出最终答案"]
```

### 5.1 看图时只记这几条

- `nlrl_skills` 的 `router` 是真的 `LLM router`，而且训练和评估都用它。
- `nlrl_skills` 的 skill 来源是外部 `skill_library/*/SKILL.md`，也就是一个可被 actor/critic 持续改写的 skill 池。
- `agent/skill_eval` 这条线没有用 `configs/system.json` 里的 `router`；它的 skill 分配是 `skill_router.py` 里的 `route_skill()` 代码路由。
- `agent/skill_eval` 的 6 个 skill family 是内嵌在 `.py` 里的 `SKILL_SPECS`，不是从外部 skill 池动态发现出来的。
- `origin` 的 `agent/skill_eval` 默认仍是 `LLM planner` 产出 `tool_sequence`，`skill_planner.py` 在 origin 里只是 fallback，不是主路径强制模板。

#### 5.1.1 补图：当前版本里 routed skill 是怎么分发给 executor 的

如果按现在 `project_skills-3.18dhc-19.40` 这条 `direct-executor` 口径去理解，要先记一条：

- `executor` 本身**不负责**六类 skill family 的路由。
- skill route 在 executor 启动前就已经由代码做完了。
- executor 真正接手的是：`shortlist + routed skill metadata + focus_tools + guidance`。
- 下面这张图只补“skill 如何拆成运行时组件送进 direct-executor”，不重画上面的总图。

```mermaid
flowchart TB
    A["question_id + question_text + file_list"]

    subgraph S1["第一层：先从题目证据里反推"]
        B["route_skill()<br/>按关键词 / 文件线索 / qid 兜底<br/>决定 6-skill family"]
        C["infer_question_profile()<br/>推出 domain / intents / file_priors<br/>required / preferred / discouraged / canonical_rules"]
        D["shortlist_tools()<br/>结合 profile 与工具打分<br/>产出 shortlisted tools"]
    end

    subgraph S2["第二层：把 routed skill 拆成运行时可注入部分"]
        B1["SkillSpec<br/>skill_id / display_name / description / tool_allowlist"]
        E["seed allowlist"]
        F["shortlist<br/>当前题的真实可用工具全集"]
        G["focus_tools<br/>= shortlist ∩ seed allowlist"]
        H["skill 基础暴露字段<br/>skill_id / display_name / description"]
        I["_skill_guidance_lines()<br/>family 规则 + profile 规则"]
    end

    subgraph S3["第三层：组 prompt 并交给 direct-executor"]
        J["system prompt<br/>写入 shortlisted tools 全量"]
        K["user prompt<br/>写入 routed skill metadata<br/>+ focus_tools + guidance"]
        L["direct-executor"]
    end

    subgraph S4["第四层：executor 内部执行约束"]
        M["逐步决定下一步 tool"]
        N{"tool 是否在 shortlist 内"}
        O["ToolRuntime 执行"]
        P["直接报错<br/>tool 不在 shortlisted tool list"]
        Q["输出 final answer"]
    end

    A --> B
    A --> C
    C --> D

    B --> B1
    B1 --> E
    D --> F

    E --> G
    F --> G

    B1 --> H
    B1 --> I
    C --> I
    G --> I

    F --> J
    H --> K
    G --> K
    I --> K

    J --> L
    K --> L

    L --> M
    M --> N
    N -->|是| O
    O --> M
    M --> Q
    N -->|否| P
    P --> M
```

看这张图时只记下面几条就够了：

- `route_skill()` 决定的是“你属于哪一个 6-skill family”，它主要吃题面关键词、文件线索和 `qid` 兜底，不是拿一整段 skill description 去做 embedding 式匹配。
- `infer_question_profile()` 和 `shortlist_tools()` 不是从 routed skill 里派生出来的从属步骤；它们是和 `route_skill()` 并行、一起从题目证据里反推运行时约束。
- `tool_allowlist` 在当前版本里不是硬边界，它主要用于和 shortlist 取交集，得到 `focus_tools`，告诉模型“这类题当前 shortlist 里最像核心工具的是哪些”。
- 真正的硬边界是 `shortlist`，不是 `allowlist`。direct-executor 如果选了一个不在 shortlist 里的工具，会直接被拒。
- 当前 executor 实际消费的不是你后来繁衍出来的那 6 份 markdown 文档全文，而是这些文档背后对应的运行时成分：`skill_id / display_name / description / focus_tools / guidance_lines / shortlisted tools`。

### 5.2 一句话对照

- `nlrl_skills`：动态 skill 池系统，`LLM router` 先选 skill，再让 executor 在该 skill 约束下做题。
- `agent/skill_eval`：固定 6-skill 专用评测链，先 `code route skill`，再让 planner 在收紧后的工具空间里生成工具链。

---

## 6. 最近在 `project_skills-3.18dhc-19.40` 上引起的改动

这里单独落一下最近这轮在 `project_skills-3.18dhc-19.40` 上产生的实际改动，以及和后续训练方案相关、但目前主要还停留在架构口径层面的新分支。

### 6.1 已经落到代码里的改动

当前 `linux` 分支最近几次提交主线是：

- `7e05feb fix: run linux smoke eval without proxy`
- `ee3e124 fix: stabilize linux D0 eval runtime`
- `d831205 feat: add direct executor mode for skill eval`

其中最重要的是 `agent/skill_eval` 这条 `D0` 线新增了一个 `direct-executor` 模式。

它的关键点不是“改前半段 skill 选择逻辑”，而是“在保持前半段口径基本对齐的前提下，改后半段执行形态”：

- 仍然先做 `infer_question_profile + shortlist_tools`
- 仍然先做 `route_skill` 路由到内置 `6 skill family`
- 仍然保留完整 `shortlist`
- `skill allowlist` 仍然主要作为 `focus tools / guidance`
- 真正变化的是：后半段不再强制走 `planner -> parameter worker -> executor -> answer selector`
- 新模式下改为单个 `direct-executor` 在同一份 `shortlist + routed skill guidance` 上逐步决定下一步工具

也就是说，新增的是：

- `staged` 多智能体协作后半段
- `direct-executor` 单智能体逐步选工具后半段

而不是：

- 重新发明一套新的 6-skill 路由逻辑
- 用代码直接替模型指定最终工具链

这次在 Linux 上为了让 `D0` 能稳定跑起来，还顺手补了几类稳定性改动：

- 默认不走系统代理，改为显式直连调用
- 增加了 LLM 并发闸门，避免高并发时把接口打崩
- `ToolRuntime` 加了导入锁，避免多线程导入工具模块时互相污染 `sys.argv`
- 网络重试和等待策略做了兼容性增强

### 6.2 6-skill 测试这边目前结论

基于最近一次 `direct-executor` 全量跑完后的结果，当前它比原来的拆分式后半段更值得保留为默认方案。

这次 `direct-executor` 最终汇总大致是：

- `count = 248`
- `avg_accuracy = 0.4476`
- `avg_efficiency = 2.1288`
- `avg_tool_any_order = 0.7145`
- `avg_tool_in_order = 0.6555`
- `avg_tool_exact_match = 0.4125`
- `avg_parameter_accuracy = 0.2338`

对照之前拆分式后半段基线：

- `avg_accuracy = 0.3508`
- `avg_efficiency = 0.5936`
- `avg_tool_any_order = 0.5927`
- `avg_tool_in_order = 0.4748`
- `avg_tool_exact_match = 0.4357`
- `avg_parameter_accuracy = 0.001`

所以当前判断可以简化成一句：

> 如果目标是现阶段 benchmark 实分与整体执行效果，`direct-executor` 比拆分式后半段更优；拆分式只在 `tool_exact_match` 上略占优势。

### 6.3 训练侧新增的“并行单条 skill”分支

这部分目前最重要的是先把口径定清楚。

最近讨论后，训练侧在这份图里新增了一条分支：

- 每个 task 并行发起
- 每个 task 只维护一条 skill
- 这条 skill 的字段、提示组织方式、工具使用口径尽量和最终 `direct-executor` 测试模式对齐
- 后面再做聚合、去重、合并

这样做的原因是：

- 如果最后理想测试模式是“单 executor 消费单条 skill”，那训练阶段最好也尽量产出同口径 skill
- 这样后续注入优良 skill 时，不需要再额外做一层大幅格式映射
- 即使单条 skill 的字段设计看起来有点别扭，也比训练时和测试时走两套完全不同范式更稳

但这里要明确：

- 这条“每任务并行、单条 skill、对齐 direct-executor”的训练线，目前已经在这份架构图里明确加出来了
- 它是当前对后续训练实现方向的确认
- 但它还不是已经提交进 `nlrl_skills` 代码的一整套完整实现

换句话说，当前已经真正落地到代码的是 `D0` 的 `direct-executor` 与 Linux/直连/稳定性修复；训练侧这条新线目前更多是已经确定好的下一步实现口径。

## 7. 2026-04-03 13:18

本段记录针对的对象是：`/data/xsy/project_skills-3.18dhc-19.40`

接下来要做的事情，是把训练侧这条“每任务并行、每个 task 只维护一条 skill、字段与消费口径尽量对齐 `direct-executor`”的新分支真正落到代码里，然后用它开展并行训练，拿到一批单 task 产出的 skill，再进入后续聚合与测试。当前这条新线不再沿用全局 skill 池串行长技能的范式，而是每个 task 各自维护自己的局部训练空间；因此并行时每个 task 都需要独立的 `skill_library_root`、独立的 `experience_buffer_path` 和独立的 run 目录，避免多个 task 在并发时互相污染 skill 文件和经验缓存。

当前口径下，第 1 轮仍然统一留在现有 `critic -> actor` 范式里面，不单独发明一个旁路创建流程。也就是说，第 1 轮因为还没有该 task 的可用 skill，可以跳过正常执行，由 critic 按统一范式给出创建当前 task seed skill 的指令，再由 actor 完成创建；从第 2 轮开始，这个 task 就已经有唯一一条可用 skill，后续不再需要 router，也不再允许 `merge_skills`，原则上也不再允许继续 `create_skill` 去生出第二条 skill，而是固定成 actor 只修改这同一条 skill。这样整个 task 内部形成的是“创建一次，后续只改”的闭环。

当前阶段不提前强行把 skill 字段定死。为了先看性能，skill 只要求最基本能被 executor 消费即可；`name` 和 `description` 仍然需要保留，其余字段先不做严格要求，`allowed-tools`、`scripts/`、`references/` 都可以存在，但不作为当前这条新线必须稳定收敛的 schema。现在 executor 的消费契约也先不重构，继续沿用当前 `nlrl_skills` 这一套做法：把整份 `SKILL.md` 作为 activated skill 丢给 executor，再把资源列表一并给它，如果 skill 明确要求读取 `references/*.md` 或执行 `scripts/*.py`，就由 executor 通过 `read_file` 或 `run_python_script` 自主消费。换句话说，当前阶段的目标不是把 skill 先规整成最终聚合 schema，而是先让单 task 单 skill 的训练闭环跑通，先看结果是否有效。

经验回放这块也需要跟着口径一起调整。新的并行单 task 训练线里，experience buffer 不能再沿用全局跨 task 共享版本，否则并发时会把别的 task 的失败签名检索进来，污染 actor 的修改判断。更合理的方式是每个 task 一份 task-local experience buffer，只在这个 task 自己的多轮 iteration 里保留连续修改记忆；这样既保留“同一个 task 连续十轮修改不能失忆”的优点，又不会把并行 task 之间的经验混在一起。

这条新线的终止条件也先按最简单的口径执行：单 task 在限定 iteration 上限内，如果成功则保留该 skill 进入后续候选池；如果到上限仍然没有成功，则直接丢弃该 skill，不进入后续聚合。等并行训练先跑出一批 task-local skill 以后，再进入下一阶段统一做聚合、去重、合并与重新测试。当前先不提前解决聚合后的最终字段长什么样，而是先把并行训练和单 skill 迭代闭环落到代码，并拿到第一批可观察结果。

### 7.1 2026-04-03 14:53

要求补记：

- 30 题最终仍然要落成明确题号清单，当前采用“先按 bucket 分层，再固定种子抽样，再导出题号文件”的方式。
- 正式训练口径按 `30` 个 task 并发发起，单 task `iteration` 上限配置为 `10`。
- 训练侧 `actor / critic / router` 统一切到 `gpt-5.4`，`executor` 继续用 `qwen3-8b`。
- 训练结束后不直接把 30 条 task-local skill 暴露给执行侧，而是先聚合成 `6` 条 family skill，再用这 `6` 条 skill 回测同一批 `30` 题。

维护记录：

- 已新增并接通 `sample-task-set`、`train-task-local-parallel`、`aggregate-task-local-skills` 三条 CLI 能力，并补了 task-local trainer、30 题分桶抽样器、6-skill 聚合器。
- 并行训练线已经落到代码：每个 task 独立 `skill_library_root`、独立 `experience_buffer_path`、独立 run 目录；第 1 轮强制 `create_skill`，后续轮次强制只 `modify_skill` 当前这 1 条 skill。
- 为了适配当前 API，又补了几处底座修正：`gpt-5.4` 不再发送 `enable_thinking`；`qwen3-8b` 仍按当前中转站要求显式发 `enable_thinking=false`；并发导入 EO tool 时加了全局锁，避免 `sys.argv` 竞争。
- 冒烟并发已经跑通。`/data/xsy/project_skills-3.18dhc-19.40/runs/smoke_task_local_parallel_20260403_v4` 证明了 `47 / 117 / 210` 三题并发下都能完成第 1 轮 `critic -> actor create`，并进入后续 skill 消费与修改阶段。
- 冒烟中又暴露出 executor 消费契约问题：EO tool schema 之前把 `list/float/bool` 全部暴露成了 `string`，导致 `file_list`、`bboxes`、`threshold` 等参数被字符串化，出现 `Failed to open b` 和 bbox 格式错误。随后已修 `nlrl_skills/tools.py`：现在按真实 Python 签名生成 schema，并对字符串化的 list/number/bool 做自动纠偏；同时修了 `write_skill_bundle()`，当 skill 带 `references/` 或 `scripts/` 时会自动把 `read_file` / `run_python_script` 补进 `allowed-tools`。
- 30 题抽样文件已固化在：
  - `/data/xsy/project_skills-3.18dhc-19.40/data/task_sets/parallel_skill_training_seed_20260403/task_set_manifest.json`
  - `/data/xsy/project_skills-3.18dhc-19.40/data/task_sets/parallel_skill_training_seed_20260403/train_spectrum_10.txt`
  - `/data/xsy/project_skills-3.18dhc-19.40/data/task_sets/parallel_skill_training_seed_20260403/train_products_10.txt`
  - `/data/xsy/project_skills-3.18dhc-19.40/data/task_sets/parallel_skill_training_seed_20260403/train_rgb_10.txt`
  - `/data/xsy/project_skills-3.18dhc-19.40/data/task_sets/parallel_skill_training_seed_20260403/train_all_30.txt`

训练、聚合、测试结果：

- 正式 30 题并发训练 run 在：
  - `/data/xsy/project_skills-3.18dhc-19.40/runs/train_task_local_parallel_all30_20260403_v1`
- 这次 run 已经完成了 30 个 task 的第 1 轮 `seed skill` 生成，并开始部分第 2 轮执行；由于 `gpt-5.4` actor/critic 在 30 路场景下延迟较大，本次没有等待全部 task 自然跑满 10 轮，而是直接基于当前已经落盘的 `30` 条 task-local 最新 skill 做聚合。代码层面的 `iteration=10` 配置仍然已经生效，后续若继续长跑，可以直接沿用当前命令口径。
- 为支持这种情况，聚合器已补成可从 partial run 直接扫描每个 task 的最新本地 skill，而不强依赖 `retained_task_skills.json`。本次聚合输出在：
  - `/data/xsy/project_skills-3.18dhc-19.40/runs/train_task_local_parallel_all30_20260403_v1/aggregated_skill_library_seed30`
- 当前已经按 6 个 runtime family 聚合完成：
  - `earth-spectrum-thermal-retrieval`
  - `earth-spectrum-drought-stress`
  - `earth-product-timeseries`
  - `earth-product-derived-index-change`
  - `earth-product-raster-arithmetic`
  - `earth-rgb-perception-change`
- 6-skill 回测 run 在：
  - `/data/xsy/project_skills-3.18dhc-19.40/runs/eval_aggregated6_seed30_on_train30_20260403_v2`
- 当前 30 题总表已经补齐在：
  - `/data/xsy/project_skills-3.18dhc-19.40/runs/eval_aggregated6_seed30_on_train30_20260403_v2/evaluation_summary.json`
- 本次 30 题 / 6-skill / `router=gpt-5.4` / `executor=qwen3-8b` 的总体结果：
  - `task_count = 30`
  - `success_count = 6`
  - `Accuracy = 0.2000`
  - `TAO = 0.7206`
  - `TIO = 0.6838`
  - `TEM = 0.5393`
  - `Parameters = 0.2923`
  - `Efficiency = 1.3647`
- 当前跑通并答对的题号是：
  - `71`
  - `96`
  - `138`
  - `149`
  - `229`
  - `239`
- `177` 和 `219` 在本轮评测里已经完成 router，但 executor 长时间未收敛；为了给这一版留下一份完整 30 题报告，当前总表里把这两题保守记为失败，不做加分，也不做成功推断。

结论：

- 训练侧“每任务并行、每任务单条 skill、本地 experience buffer、后续只修改同一 skill”的代码已经落完并跑通了并发冒烟。
- 当前 30 题版本已经拿到一套可消费的 `6` skill 聚合库和一份完整的 `30` 题测试报告。
- 下一步如果继续往上提分，优先项不是再改主框架，而是继续让 30 题长跑满 10 轮，并针对当前暴露最重的几类失败点继续收紧 skill 文案：时间序列 file grouping、thermal 输出路径回填、ATI/TVDI 配对规则、以及 RGB change/detection 类的 prompt 归一化与输出格式约束。

本次观察补记：

- 这一次的聚合没有等训练真正结束就直接做了，这个口径不成立。后续正式口径里，必须等 `30` 个并行 task 的训练全部结束，或者全部达到明确终止条件之后，才能进入聚合，不能再基于 partial run 提前聚合。
- 不管看训练侧单挑历史分数，还是看聚合后 `6` skill 的回测分数，这一版整体都偏低，目前只落在 `10%+` 到 `20%` 这个区间。
- 当前判断，这一版低分和 `qwen` 侧没有开思考高度相关。之所以当时没有开，是因为这版实现里按“需要稳定产出 JSON / 结构化结果”的保守判断，把思考关掉了；这个判断只适合作为一版打通链路的临时口径，不能作为最终训练口径。
- 因此，`7.1` 这一版只能算“代码打通版 / 流程冒烟版”，还不能作为最终训练结论。后续还需要继续开 `7.2` 迭代，把 `38B` 开思考这条线补齐后，再按完整流程重跑训练、聚合和测试。
- 关于这次聚合产物，绝对路径这里只留 1 条代表路径做存档即可：
  - `/data/xsy/project_skills-3.18dhc-19.40/runs/train_task_local_parallel_all30_20260403_v1/aggregated_skill_library_seed30/earth-spectrum-thermal-retrieval/SKILL.md`
- 这次回看也确认了一点：聚合本身不能继续交给规则代码去“直接写 skill”；代码只适合负责 source skill 收集、审计信息落盘、frontmatter 归一化、以及泄漏校验。真正的 family 级聚合内容必须走 prompt，让我或者 `gpt-5.4` 来写。当前已经把这条链路改成 prompt 驱动，并且已经接通 `gpt-5.4` 实际试跑。
- 针对这次聚合 skill 的字段观察，需要额外记一条：消费侧 `SKILL.md` 里保留 `allowed-tools`、family 级 metadata、route signals、以及高层执行流 guidance 是对的；但不应该把 `source_question_ids`、`source_skill_names`、`example_questions` 这类追溯字段，以及 `Qxx` 这种带题号的散例子，直接丢进可消费 skill。
- 后续更合理的聚合口径应该分成两层：
  - 第一层是“消费用聚合 skill”：只保留 executor 真正需要的字段和高层工具流 / 执行流指导，不带题号，不带散例子，不带 source skill name，不带 example question。
  - 第二层是“聚合信息 / 审计信息”：单独维护每次实验这 `6` 个聚合 skill 是由哪些 task-local source skill 聚出来的，用来追溯，不注入 executor。
- 对消费用 skill 来说，`When To Use` 可以继续保留给 router / executor 参考；但 `Learned Workflow Patterns` 需要改成抽象后的高层工具流指导，例如“先验输入分组 -> 生成中间产物 -> 复用返回路径 -> 最后做阈值/比较/趋势/面积统计”，而不是把原题 workflow 连题号一起塞进去。
- 这一次 `30` 道题的正式选题口径也不够好。虽然分大类 / 小类、保证 bucket 覆盖这个方向是对的，但正式训练集不应该再额外偏向“更轻”“文件更少”“长度更短”的样本；这样会把难度分布拉偏，口径不公平。
- 后续正式训练集的选题要求应该是：在保证分类覆盖、bucket 覆盖的前提下，桶内随机抽样，不再沿着长度、文件数或复杂度去偏向简单样本。
- 只有 `smoke` 集可以继续偏简单、偏轻量，用来省钱和排查链路；正式训练 / 聚合 / 测试集不再沿用这个偏轻策略。

### 7.2 2026-04-03 15:43-20:58

这段统一收口这轮 `60` 题并行训练、`6` skill 聚合、以及同批 `60` 题测试，不再重复拆成多个 `7.2`。

已完成 / 已实现：

- `sample-task-set`、`train-task-local-parallel`、`aggregate-task-local-skills` 这三条 CLI 和 task-local 并行训练底座已经接通。
- 每个 task 独立 `skill_library_root`、独立 `experience_buffer_path`、独立 run 目录，以及“首轮只 create、后续只 modify 当前这 1 条 skill”的训练模式已经落地。
- 聚合线已经从“代码直接写消费用 skill”切到“prompt + LLM 聚合”；代码现在只负责 source 收集、审计信息、frontmatter 归一化、泄漏校验和文件落盘。
- 聚合器已经能把“消费用聚合 skill”和“聚合追溯 / audit 信息”拆开维护：前者写进 `SKILL.md` 与 `references/EXECUTION_GUIDANCE.md`，后者单独写进 `_aggregation_info/*.json`。
- 新的消费用聚合 schema 已经按当前观察收紧：不再把 `source_question_ids`、`source_skill_names`、`example_questions`、以及带 `Qxx` 的 raw pattern 直接暴露给 router / executor。
- `gpt-5.4` 的 prompt 聚合调用已经接通并开始实际试跑，说明“聚合交给模型写”这条路线已经不是停留在设计上。

固定参数：

- 正式口径改成同一批 `60` 题：先训练 `60`，再用聚合后的 `6` skill 回测同一批 `60`。
- 训练侧：
  - `task_count = 60`
  - `task_concurrency = 60`
  - `max_iterations_per_task = 10`
  - `max_executor_steps = 20`
- 测试侧：
  - `task_count = 60`
  - `task_concurrency = 60`
  - `evaluation iteration = 1`
  - `max_executor_steps = 20`
- 聚合顺序固定为：
  - `60` 题并行训练完成
  - 只取训练成功的题目的 task-local skill
  - 聚合成 `6` 个 family skill
  - 再投入同一批 `60` 题并行测试
- 角色分工口径：
  - 训练时 `actor / critic` 使用 `gpt-5.4`
  - 测试时按当前实现仍然是 `router / executor = qwen3-8b`
- 消费用 guidance 的要求是：体现 family 级工具规划思路、常见中间产物复用方式、比较 / 趋势 / 阈值 / 区域统计这类高层执行流，但不能细到 benchmark 某一道题的逐步调用，更不能把原题号和散例子直接塞进去。

中间废弃 / 纠偏口径：

- `train_task_local_parallel_all60_20260403_v1` 虽然落盘了 `60` 个 task，但它的 `max_executor_steps = 10`，不符合这轮要求，因此只能算废弃 run，不能拿来当正式结论。
- 后续一度把 `train_task_local_parallel_all60_20260403_v2` 当成正式 run，因为它已经切到 `max_executor_steps = 20`，并且 `60/60` task 全部结束，训练侧 `success_count = 19`，训练侧 `accuracy = 0.3167`。
- 但后来回看确认，`v2` 已经被 request-layer 故障污染，不能继续作为最终口径：
  - 当时大量 task 出现 `403 Forbidden`
  - 进一步 live probe 后，旧 key 明确返回 `insufficient_user_quota`
  - 同时 `/v1/models` 仍能列出 `qwen3-8b`，说明这不是模型名写错
- 这次还额外纠偏出一层配置问题：当时实际加载的是旧 key，不是这轮口头指定要用的“上海实验室 API key”。所以那次 `403 / quota` 只能说明“旧 key 当前额度不足”，不能外推出“上海实验室 key 也欠费”。
- 2026-04-03 晚上重新核对后，已经确认：切回上海实验室 key 后，同一网关下 `qwen3-8b` 和 `gpt-5.4` 的 live probe 都能返回 `200`。因此这轮后续正式训练 / 聚合 / 测试，必须以切 key 之后的 run 为准，不再沿用 `v2` 结论。

最终正式口径（上海 key 收口版）：

- 正式训练 run：
  - `/data/xsy/project_skills-3.18dhc-19.40/runs/train_task_local_parallel_all60_20260403_v3_shlab`
- 聚合后的 `6` 个 family skill：
  - `/data/xsy/project_skills-3.18dhc-19.40/runs/train_task_local_parallel_all60_20260403_v3_shlab/aggregated_skill_library_6`
- 同批 `60` 题并行测试 run：
  - `/data/xsy/project_skills-3.18dhc-19.40/runs/eval_aggregated_all60_20260403_v3_shlab`

这次正式训练 / 聚合 / 测试的最终结果如下：

- 训练侧：
  - `60/60` 题已经全部补齐到 `run_summary.json`
  - `success_count = 31`
  - `retained_count = 31`
  - `Accuracy = 0.5167`
  - `TAO = 0.7878`
  - `TIO = 0.7436`
  - `TEM = 0.5731`
  - `Parameters = 0.3479`
  - `Efficiency = 1.4658`
- 聚合侧：
  - 已经成功聚成 `6` 个 family skill，分别是：
    - `earth-spectrum-thermal-retrieval`
    - `earth-spectrum-drought-stress`
    - `earth-product-timeseries`
    - `earth-product-derived-index-change`
    - `earth-product-raster-arithmetic`
    - `earth-rgb-perception-change`
- 测试侧：
  - 同一批 `60` 题、并发 `60`、`step = 20`、`evaluation iteration = 1`
  - `success_count = 18`
  - `Accuracy = 0.3000`
  - `TAO = 0.6642`
  - `TIO = 0.6195`
  - `TEM = 0.3203`
  - `Parameters = 0.2394`
  - `Efficiency = 1.4424`

本轮观察：

- 第一，切回上海实验室 key 之后，正式训练 `v3_shlab` 和后面的同批 `60` 题测试都没有再出现前面那种成片的 `403 / insufficient_user_quota`。因此这轮正式结果应当只引用 `v3_shlab`，不再引用旧 key 下的 `v2`。
- 第二，这一版真正炸出的核心流程 bug，不是 quota，而是 `run_python_script` 的 stdin 契约。运行器原来把终端 stdin 直接继承给子脚本，但部分训练中生成出来的 helper script 是按 `json.load(sys.stdin)` 写的，于是会直接卡死等输入。后面已经做了两层修正：
  - 代码层：`nlrl_skills/tools.py` 已补 `stdin_json / stdin_text` 支持，并且不再把空 stdin 继承成可交互终端。
  - 提示词层：`tool_agent_protocol.md`、`executor_user.md`、`actor_create_skill.md`、`actor_modify_skill.md`、`actor_merge_skill.md`、`skills.py` 一并补了“如果脚本走 stdin JSON，就必须通过 `stdin_json` 传”的约束。
- 第三，这次 `v3_shlab` 训练在尾部还暴露出一个长尾收口问题：最后有 `4` 个 task 长时间不再新增 `task_summary`，但目录里仍有未收口的部分迭代痕迹。为了不再让整条主线被尾巴卡住，这里采取的是保守收口法：
  - 先停掉不再推进的主进程；
  - 再基于这 `4` 个 task 的最后一轮完整 `iteration_summary` 和当前 skill 目录，补写 `task_summary.json`；
  - 不额外推断成功，不补加分，只按最后完整迭代的 terminal evidence 记账。
  - 也就是说，这 `31/60` 的训练成功数是保守口径，不是刷出来的数。
- 第四，这次同批 `60` 题测试已经完整跑完，最后只有 `18/60` 成功，说明“31 个成功 task-local skill 聚成 `6` 个 family skill”这条线目前还远远谈不上稳定高分；但它至少给出了这一轮真实可复现的正式基线，而不是被旧 key 的 `403` 污染掉的假低分。
- 第五，这次结果也说明训练侧本身还不够强：训练是 `31/60 = 0.5167`，测试是 `18/60 = 0.3000`。也就是说，聚合后掉分当然存在，但前提是训练侧本身就没有达到“点对点基本全训会”的程度，这也是后面需要继续追的核心问题。

这次之后，后续不能再犯的错误也更清楚了：

- 正式 run 起跑前，固定先做两件事：
  - live probe `/v1/models` + `/chat/completions`
  - 核对当前进程实际加载的 key 是否就是用户指定的 key
- 任何 helper script 只要是 `json.load(sys.stdin)` 口径，就不能再默认用旧版 `run_python_script(args=...)` 去碰，必须显式走 `stdin_json`
- 对于这种 `60` 并发训练，后续收口阶段要有明确的“长时间无新 summary 就保守封口”规则，避免整个流程被少数尾巴 task 无限拖住

目前这轮的最终正式口径就是：

- 训练：`31/60`，`Accuracy = 0.5167`
- 聚合：`31` 个成功 task-local skill -> `6` 个 family skill
- 测试：`18/60`，`Accuracy = 0.3000`

这组数后面可以继续迭代，但本轮先以这组真实结果作为结论，不再沿用前面那个被旧 key 和 request-layer 故障污染的 `v2` 口径。

补一个这轮最终结论：

- 当前这条“单 task 单 skill、点对点连续修改到成功为止”的训练线，至少在这版实现上，训练效果仍然明显低于预期。虽然正式训练已经从被污染的 `v2` 纠偏到了 `31/60 = 0.5167`，但对“点对点训练”这个口径来说，`51.67%` 仍然偏低，说明问题不是只出在后面的 `6` skill 聚合；训练阶段本身就已经有相当多的题没有训会。
- 因此，测试侧最后只有 `18/60 = 0.3000` 虽然低，但并不奇怪；因为训练侧本身没有把单条 task-local skill 稳定拉到一个足够高的水平，后面再经过一次聚合，分数继续下掉是自然结果。
- 所以，这一轮最核心的观察，也是下一轮 `7.3` 的起点，不应再先盯聚合，而应先回看训练侧这 `29` 道失败题：训练到底错在什么地方，为什么点对点训练仍然会错这么多题。后续优先级应该是先做训练失败分析，再决定下一步是收紧 prompt、修 tool contract、补 answer mapping、修 family 边界，还是调整 stop rule / 收口策略。

---

### 7.3 2026-04-03 23:37

这一段先统一收口下一轮 `7.3` 的优先级，不再把目标摊太散。

当前更保守、也更符合这轮观察的策略是：

- `iteration` 上限先继续维持 `10`
- 这一次先优先修 `A` 类结构化输出问题
- `B` 类尾部中断先记为异常，不把它混进主优化目标
- `C` 类系统能力 / 执行能力问题先不在这一轮强行处理

也就是说，当前 `7.3` 的主目标不是“直接把分数做高”，而是先把这轮最明确、最工程化、最可验证的结构化失败尽量收掉，同时不动评测口径，不做投机性的答案修补。

#### 7.3.1 起点补充：29 题失败分型（按更接近排查优先级的口径）

基于正式训练 run：

- `/data/xsy/project_skills-3.18dhc-19.40/runs/train_task_local_parallel_all60_20260403_v3_shlab`

这 `29` 道失败题，如果按下一轮真正有用的排查口径，我这里不再拆成 `5` 个完全平级的大类，而是收口成 `3` 个大类：

### A. 结构化输出失败：`9` 题

题号：

- `18 23 42 43 44 155 174 217 245`

这类的本质不是题目本身不会做，而是训练流程没把模型输出吃进去。典型表现是：

- `Expecting value`
- `Invalid control character`
- `Expecting ',' delimiter`
- `Extra data`
- `Unable to locate JSON object in response`

这里要特别注意一层配置事实：

- 这轮正式 run 里，`actor/critic = gpt-5.4`
- 但 `router/executor = qwen3-8b`
- 并且 `router/executor` 都开了 `enable_thinking = true`
- 同时 `executor` 是 `stream = true`
- 运行层虽然在 prompt 里要求“只输出一个 JSON object”，但没有严格 JSON mode，也没有 parse-fail 后的 regenerate/retry

所以这 `9` 题首先应当归到**结构化输出 / orchestration 工程问题**，而不是任务能力问题。

### B. 尾部中断 / 收口异常：`4` 题

题号：

- `81 82 84 89`

这类的共同特征是：

- 前一轮还有完整的 `iteration_summary.json`
- 最后一轮目录里只剩部分 `env/critic` 痕迹
- 没有完整 `actor` 产物
- 也没有标准 `iteration_failure.json`

所以它更像：

- 手动中断
- 会话断开
- 进程被 kill / OOM
- 或者尾部收口逻辑没跑完

这 `4` 题当前可以单独挂起，不建议和 skill/prompt 本身混在一起分析。它们更像“这轮 run 尾巴没收好”，不属于下一轮 `7.3` 的主优化对象。

### C. 系统能力 / 执行能力问题：`16` 题

题号：

- `91 98 111 126 141 148 153 173 180 204 206 223 225 227 240 246`

如果按更接近“能力”的口径，这 `16` 题才是下一轮最该盯的主战场。它们不是结构化 JSON 炸掉，也不是 run 尾部丢失，而是**执行链本身没有把题稳定做对**。

这一类里面还能再分成 `3` 种常见表现，但这三种都应该视为“系统能力 / 执行能力问题”，不必再硬拆成“不是能力”：

#### C1. 答案落地 / choice grounding 错：`9` 题

题号：

- `91 111 153 173 204 206 227 240 246`

这里的“答案落地”不是空泛说法，具体指的是：

- 数值已经算出来了，但没有严格映射到选项
- 坐标 / bbox / centroid 基本对了，但没做“对全部可见选项的最近匹配”
- 排序基本形成了，但没做 tie / noise / ambiguity 的保守决策
- 中间不该 round 的地方提前 round，导致最后 evaluator 记错

所以这类题不能简单说“理论上都该对”。更准确的说法是：

- **核心计算可能已经接近对了**
- **但最终 benchmark 所要求的输出 schema / choice grounding 没被稳定执行**

这本质上仍然是能力问题，只不过是**最后一跳的执行能力**，不是前面工具不会调。

#### C2. 执行契约 / 路径 / 参数 / false blocker 错：`5` 题

题号：

- `98 126 141 148 180`

这类常见症状是：

- tool-returned path 没接住
- batch/list 参数 shape 错
- stringified list / 非原生结构参数传错
- 本来可恢复的错误，被误判成 blocker
- 进入重复 retry / partial execution，最后 iteration 耗尽

如果按你的口径，这类我也同意直接算进“能力问题”。因为它不是纯工程 parser 问题，而是 agent 在执行规则、I/O 契约、恢复策略上没有学稳。

#### C3. 外部工具失败后的 blocker handling 不够：`2` 题

题号：

- `223 225`

这两题的直接触发点是 `ChangeOS` 返回：

- `Failed to call model`

也就是说，**工具本身确实会挂**。这里不是说 transport 一定失败，而是：

- wrapper 这一层调用可能是成功返回的
- 但 payload 里明确写了模型调用失败

这类不能说“全是外部环境锅”。更准确的说法是：

- 外部工具确实可能挂
- 但 agent 是否能识别 payload-level failure
- 是否能做 bounded retry
- 是否能验证 artifact 是否真的生成
- 是否能以 blocker 口径收尾而不是假装完成

这些仍然属于系统能力 / 执行能力的一部分。

### 这一轮失败分型的最终收口版

所以，按更适合下一轮 `7.3` 的口径，可以直接记成：

1. `结构化输出失败`：`9` 题
2. `尾部中断 / 收口异常`：`4` 题
3. `系统能力 / 执行能力问题`：`16` 题

也就是说：

- `JSON` 不是全部，也不是绝大多数
- 但它确实是一块明确、可直接修的硬问题
- 真正剩下来的大头，其实是“系统执行能力”这 `16` 题

---

#### 7.3.2 起点补充：executor token / JSON / 架构观察

还是基于同一个正式训练 run：

- `/data/xsy/project_skills-3.18dhc-19.40/runs/train_task_local_parallel_all60_20260403_v3_shlab`

对这轮 `1698` 条 executor response usage 的补充观察，可以单独记两层：

### A. 输出预算层：`8K` 风险真实存在，但不是大面积撞线

- `completion_tokens > 8192`：`3 / 1698 = 0.18%`
- `completion_tokens > 16384`：`0`
- `completion_tokens > 32768`：`0`
- 最大一次 `completion_tokens = 11676`
- 同时，这轮确实出现过一次明确的 `finish_reason = length`

所以这里更准确的结论是：

- `8K` 不是这轮大面积失败的主因
- 但它不是“完全没风险”，因为确实已经出现了越过 `8K` 的真实 case
- 因此从下一轮 `7.3` 的保守工程口径看，把 executor 的 `max_tokens` 从 `8K` 提到 `16K` 会更稳妥

### B. 更大的结构性问题：不是只缺 JSON，而是上下文会越堆越长

同一轮里，executor 的 prompt usage 也能看到明显膨胀：

- `prompt_tokens >= 8000`：`441 / 1698 = 25.97%`
- `prompt_tokens >= 16000`：`152 / 1698 = 8.95%`
- `prompt_tokens >= 32000`：`20 / 1698 = 1.18%`
- 最大一次 `prompt_tokens = 65850`

这说明当前 executor 的风险不只是“没开严格 JSON”。

更根本的点在于：当前它是单条 `ReAct` 式 loop，同一个 step 里既要：

- 读完整历史
- 判断下一步是否继续
- 选择下一个工具
- 生成本次参数
- 最后再决定何时收尾

对长 step 任务，这种做法会天然带来两种压力：

- 历史 observation 越积越长，prompt 会持续膨胀
- 一旦本步参数 payload 很大，还会把超长内容直接塞进 JSON `arguments` 里

所以这里的最新观察可以收口成一句话：

- `JSON` 问题是可修的工程问题
- 但就算把 JSON mode / parse-fail retry 补上，也不能自动解决“长 step 任务上下文越堆越长”的结构性问题

### C. 这部分对 `7.3` 的落地含义

当前更适合直接记进 `7.3` 的结论是：

1. 先把 executor `max_tokens` 提到 `16K`
2. 先把结构化输出问题修掉
3. 先不急着重构 executor 架构
4. 但可以在 `7.3` 下额外挂一个“后续可探索方向”：
   - 压缩 observation / 做历史摘要
   - 减少大 payload 直接写进 JSON `arguments`
   - 必要时把“选下一步工具”和“生成复杂参数”适度拆线

也就是说，这部分目前先作为**架构观察与后续探索方向**记录，不作为这一轮必须立刻落地的重构项。

---

#### 7.3.3 起点补充：10 个 Iteration 的累计成功量

还是基于同一个正式训练 run：

- `/data/xsy/project_skills-3.18dhc-19.40/runs/train_task_local_parallel_all60_20260403_v3_shlab`

这里统计的口径是：

- 对每道题，取它第一次 `task_success = true` 的 iteration 作为“成功落点”
- 然后按 iteration 做累计成功量 / 累计成功率

这里直接只保留累计量：

- `iter 1 -> 0`
- `iter 2 -> 15`
- `iter 3 -> 24`
- `iter 4 -> 28`
- `iter 5 -> 29`
- `iter 6 -> 29`
- `iter 7 -> 31`
- `iter 8 -> 31`
- `iter 9 -> 31`
- `iter 10 -> 31`

### 这张图的直接含义

- `2-4` 轮是主要增益区
- `5-7` 轮是长尾补收益区
- `8-10` 轮在这次正式 run 里没有任何新增收益

这里再补一个很重要的口径，避免误读：

- `81 82 84 89` 这 `4` 题属于“尾部中断 / 收口异常”，不是正常完整地跑到外层上限后失败。
- 因此，这 `4` 题**不能**拿来证明“第 `7/9` 轮还在稳定产生有效训练收益”，也**不能**拿来反推“iteration 上限必须继续维持到 `9-10`”。
- 真正能用于判断 iteration 是否有必要的，还是上面那张“首次成功 iteration 的累计曲线”。

因此，这张累计曲线在当前更适合作为“分布观察”，而不适合作为立刻下调 iteration 上限的唯一依据。

对下一轮 `7.3`，这里先收口成更保守的版本：

1. 保留这张累计量观察，说明收益主要集中在前 `4-7` 轮
2. 当前先不依据这张图直接把 `max_iterations_per_task` 从 `10` 往下压
3. 在结构化输出、执行契约、尾部收口这些问题还没完全修稳之前，iteration 上限先**保守维持 `10`**
4. 等前面这些更硬的问题修掉之后，再重新看是否有必要把 stop rule 往 `7` 或更低收

也就是说，当前这部分的作用主要是：

- 先告诉我们“增益分布在哪里”
- 但还不直接推动“把 `10` 改成 `7`”

下一轮更优先的，仍然是先修：

- 结构化输出
- executor 的输出预算与长 JSON 风险
- 尾部 interrupted / incomplete 的收口标记

#### 7.3.4 这一次已经实现 / 已收紧的内容

这一次围绕 `A` 类结构化输出问题，已经先把最直接的一圈工程修复落到代码：

- `nlrl_skills/utils.py`
  - JSON 抽取从“只看整段 / 首尾大括号”改成了更稳的多阶段策略：
    - 先剥 `<think>...</think>` 和 fence
    - 再尝试控制字符转义
    - 再扫描候选 JSON object
    - 并优先接受更像 top-level agent output 的对象，避免把嵌套小对象误当成整条回复
- `nlrl_skills/llm.py`
  - `chat_json()` 现在新增 parse-fail 后的定向 repair retry
  - 如果第一次回复不是合法单个 JSON object，会把 parser error 回灌给模型，要求它只重发一个合法 JSON object
- `nlrl_skills/agent_loop.py`
  - executor 每一步不再自己裸调 `extract_json_object()`
  - 而是统一走 `chat_json()`，因此 executor step 也能吃到同样的 repair retry
- `prompts/tool_agent_protocol.md`
  - 补了明确约束：禁止输出 `json.dumps(...)`、`",".join(...)`、f-string、`${...}` 这类 pseudo-code / 占位符式 JSON
- `nlrl_skills/tools.py`
  - `run_python_script()` 现在会把 `dict/list/tuple` 形参数自动序列化成 JSON 字符串再传给脚本
  - 也就是说，如果模型真的给了 concrete JSON 值，运行层可以直接兜住
- `configs/system.json`
  - `actor / critic` 继续保持 `gpt-5.4`
  - `router / executor` 继续保持 `qwen3-8b`
  - 当前默认把四个角色的 `stream` 全部先收成 `false`
  - executor 的 `max_tokens` 从 `8192` 提到 `16384`
  - `max_iterations_per_task` 继续保留 `10`

这里要特别记一条边界，避免误读：

- 这次没有改 evaluator
- 没有改答案映射逻辑去“补分”
- 没有改 stop rule 去人为挑有利口径
- 没有去碰 `C` 类能力问题

所以这次修改的性质就是：

- **只修结构化输出稳健性**
- **不做会把分数虚高的投机改动**

#### 7.3.5 接下来怎么开展 7.3 实验

如果后面要把这轮工作交给别人继续做，建议按下面这个顺序开展，不要再把目标摊太散：

1. 先用当前代码和配置，直接重跑训练侧 `60` 题并行 task-local 训练
   - 继续保持 `max_iterations_per_task = 10`
   - 先观察 `A` 类结构化失败是否明显下降
2. 重跑时重点单独记录三类现象：
   - parse-fail / malformed JSON 是否还出现
   - `finish_reason = length` 是否还出现
   - 尾部 `interrupted / incomplete` 是否仍然存在
3. 如果 `A` 类显著下降，再重新统计：
   - 训练成功数
   - 首次成功 iteration 的累计曲线
   - `29` 题失败分型是否发生结构变化
4. 只有在 `A` 类明显收掉之后，才进入下一步：
   - 再看 `B` 类尾部收口要不要补更明确的 interrupted-finalize
   - 再看 `C` 类能力问题要先打哪一块

当前更推荐的 `7.3` 实验理解是：

- 第一阶段：先验证结构化修复是否生效
- 第二阶段：再决定是否补尾部收口
- 第三阶段：最后才进入能力问题

而不是一上来就同时：

- 改架构
- 改 stop rule
- 改能力策略
- 改评测口径

这样后面别人接手时，也更容易分清这一次改动到底验证了什么、没有验证什么。

#### 7.3.5 补充约束 / 易误解点（2026-04-04）

下面这些点后面很容易被误读，这里单独补一段，作为当前执行口径的补充说明：

1. 本轮后续正式口径已经不是前面这段里写的 `60` 题了，而是**全量 `248` 题**。
   - 不再重新抽样
   - 不再回到旧的 `60` 题验证口径
   - 除非我后面明确再改口，否则这轮继续推进时默认就是全量 `248`

2. 启动正式 run 之前，**不要机械相信仓库里现成配置文件里的硬编码 key**。
   - API 口径以 `/data/xsy/skill-pool/API说明/最新api说明.txt` 为准
   - 之前已经出现过：仓库里还残留旧 key，但真实可用口径已经换成上海实验室这条
   - 所以正式大跑前，必须先核对当前 key / base url / 模型权限，而不是直接看到 `system.json` 就盲跑

3. 关于 `qwen3-8b`，这里要特别记清楚：
   - 当前这个中转站如果要让 `qwen3-8b` **开 thinking**，就应该走**流式**
   - 同时不要强行要求 API 侧 `json object`
   - 这一点在 `/data/xsy/skill-pool/API说明/最新api说明.txt` 里已经写得很明确

4. 因此，前面 `7.3.4` 里那句“当前默认把四个角色的 `stream` 全部先收成 `false`”，不能再被当成当前的通用操作结论。
   - 那种写法过于简化，后面很容易把人带偏
   - 对当前主线更安全的理解应该是：
     - `actor / critic = gpt-5.4`
     - `router / executor = qwen3-8b`
     - 如果 `router / executor` 继续保留 thinking，就应当按这条 API 约束走流式，而不是简单照搬“全部 false”

5. 当前 `nlrl_skills` 主线代码本身，并不是靠 API 侧 `json_object` 才能工作。
   - `nlrl_skills/llm.py` 现在的做法，本质上是普通 chat 返回文本，再由本地的 JSON 抽取与 repair 逻辑处理
   - 所以这里“关闭 json object”的要求，并不等于当前主线就跑不起来
   - 真正要避免的是：把 API 侧强结构化输出要求和 `qwen3-8b` thinking 流式口径混着用，最后把配置搞成互相打架

6. 后面如果再把任务交给外部 agent / Codex 去跑，提示词应当尽量**短、准、只写确认过的东西**。
   - 这个文档本身已经很详细
   - 不需要为了“看起来完整”再额外脑补一堆命令、配置结论或约束
   - 尤其是没有 live 核对过的模型 / stream / key / API 兼容关系，不要在提示词里写死

#### 7.3.5 全量 `248` 题执行补记（2026-04-04 03:30 CST）

这次是按本段当前口径继续往下做，但先核对真实代码 / 配置是否已经对齐，再决定能不能直接大跑。结论先写在前面：

- `actor / critic = gpt-5.4`
- `router / executor = qwen3-8b`
- `router / executor` 继续保留 `stream = true`
- `HTTP_PROXY / HTTPS_PROXY` 继续显式置空，走直连 `http://35.220.164.252:3888/v1`
- `qwen3-8b` 的 `max_tokens` **不能**按口头要求直接提到 `16384`
- live probe 已确认：这个网关当前会对 `qwen3-8b + max_tokens > 8192` 直接返回 `400 InvalidParameter`
- 因此这轮实际对齐口径是：
  - `qwen3-8b` 输出预算继续保留 `8192`
  - `16K` 落在**上下文侧**，不是强行改 `qwen3-8b` 的输出上限

这次真正核出来的一处未对齐点，不在 `agent.skill_eval`，而在 `nlrl_skills` 主训练线：

- `agent.skill_eval/config.py` 里已经是 `MAX_CONTEXT_CHARS = 16384`
- 但 `nlrl_skills` 的 `JSONToolAgent` 之前没有任何历史裁剪
- 旧的 `v5` 监视里，长任务 executor prompt 已经真实膨到：
  - `task_85_85` 某步 `prompt_tokens = 27359`
  - 同时伴随 `400` / JSON 抽取失败 / 超长文件列表任务不稳定
- 这说明“上下文放到 `16K`”在训练主线里实际上**还没有落到代码**

所以这轮没有直接拿旧代码继续硬跑，而是先把这条缺口补上，再重开正式 run。补丁只做了最小对齐，不改主线训练范式：

- `nlrl_skills/config.py`
  - 新增 `runtime.max_context_chars = 16384`
- `configs/system.json`
  - 显式补上 `"max_context_chars": 16384`
- `nlrl_skills/agent_loop.py`
  - executor 每次调用前会按 `16K` 预算压缩历史消息
  - 只保留最近几条 tool result 为完整上下文，其余历史做截断
- `nlrl_skills/environment.py`
  - 把 `runtime.max_context_chars` 接到 `JSONToolAgent`

补丁后先做了 1 题烟测，不直接拿大跑当试错：

- 烟测 run：
  - `/data/xsy/project_skills-3.18dhc-19.40/runs/smoke_context16k_task1_20260404`
- `task 1` 旧 run 曾在后续 iteration 出现 `400`
- 补丁后同题 smoke 的 executor usage 明显下降：
  - `prompt_tokens = 3177 / 3240 / 3928 / 3202 / 3266`
- 这说明上下文裁剪确实生效，不是只改了配置名义值

在此基础上，正式全量 run 重开为：

- 旧 `v5` 只保留为“未对齐代码下的核查 / 监视 run”，不再算正式结论：
  - `/data/xsy/project_skills-3.18dhc-19.40/runs/train_task_local_parallel_all248_20260404_v5`
- 对齐后的正式全量 run：
  - `/data/xsy/project_skills-3.18dhc-19.40/runs/train_task_local_parallel_all248_20260404_v6_ctx16k`

这次训练过程中的主要监视观察如下：

- 对齐后的 `v6_ctx16k` 前段明显更稳
- 2026-04-04 `03:01:18` CST 左右复核时，训练进程 `CPU` 约 `59.7%`
- 对齐后 executor usage 到停止时始终是：
  - `finish_reason_counts = {'stop': 3614}`
  - `json_repair_attempts_total = 0`
  - `prompt_max = 6753`
  - `prompt_tokens >= 8000`：`0`
  - `prompt_tokens >= 16000`：`0`
- 也就是说，这轮对齐后至少在已完成部分里：
  - 没再看到旧 `v5` 那种 prompt 直接膨到 `8K+ / 16K+ / 27K+`
  - 没再看到 `finish_reason = length`
  - 没再出现旧 `v5` 前段那一串 JSON 截断 / 未闭合 / `400 Bad Request`

训练结果这次只能分两段看，不能把整轮混成一个总分：

第一段是**额度耗尽前**的有效观察期：

- 监视到 2026-04-04 `03:26:51` CST 时：
  - `task_summaries = 67`
  - `success = 52`
  - `retained = 52`
  - `iteration_failures = 2`
- 这时失败结构已经明显变了：
  - 不再以结构化输出 / 长上下文爆掉为主
  - 主要剩下的是：
    - `iteration_limit_reached`
    - 极少量 `empty_stream`
    - 极少量 `create_skill requires target_skill_name`

第二段是**2026-04-04 03:27 CST 之后**的外部额度故障期：

- 从这时起，失败数开始批量跳涨
- 最终在我主动停机收口时，`v6_ctx16k` 的部分落盘结果是：
  - `task_summaries = 246`
  - `success = 54`
  - `retained = 54`
  - `iteration_summaries = 554`
  - `iteration_failures = 177`
  - 仍有 `2` 个 task 没来得及写出 `task_summary.json`
  - 缺失的是：
    - `task_57_57`
    - `task_96_96`
- 但这后半段已经不能拿来当模型 / skill 结论，因为失败桶已经被外部 `403` 污染：
  - `403_or_quota = 175`
  - `missing_target_skill_name = 1`
  - `empty_stream = 1`
- 对应 `task_summary` 的丢弃理由也已经被 `403` 主导：
  - `iteration_1_env_or_critic_error = 114`
  - `iteration_limit_reached = 15`
  - `iteration_2_env_or_critic_error = 13`

这里必须把 live probe 结果单独记死，避免后面误判：

- 2026-04-04 `03:30:41` CST
- `GET /models = 200`
- 但直接 `POST /chat/completions`：
  - `gpt-5.4 = 403`
  - `qwen3-8b = 403`
- 返回内容都是：
  - `insufficient_user_quota`
  - 文案里明确写了“用户额度不足”

因此这轮训练的正确结论不是“全量 `248` 题正式训练完成”，而是：

- 对齐核查完成了
- 训练主线里之前漏掉的 `16K` 上下文裁剪已经补上
- 补丁后前段监视结果说明：
  - 结构化输出问题确实显著下降
  - 长上下文膨胀问题也被明显压住
- 但这轮**没有条件形成可信的全量正式结论**
- 原因不是代码继续崩，而是外部 key 在中途真实欠费 / 超额，导致后半段大面积 `403`

聚合和测试这次都没有继续往下做，原因也必须写清楚：

- 不是我主动改口径
- 不是我觉得训练前段结果不好就提前停
- 而是同一个 key 对：
  - `gpt-5.4`
  - `qwen3-8b`
  都已经 live probe 到 `403 insufficient_user_quota`
- 这意味着：
  - 训练没法继续
  - 聚合也没法继续
  - 测试也没法继续

当前能确定的性能判断如下：

- `A` 类结构化 / JSON / 长上下文问题，在这轮对齐后已经不是主矛盾
- 至少在额度耗尽前的有效观察期里，这一层表现比旧 `v5` 明显好
- 当前真正剩下来的更像是：
  - 部分 task 的 skill 语义还不够稳
  - 更重要的是 actor / critic 的闭环修改质量不够
- 这点在 `iteration_limit_reached` 样本里很明显，尤其是：
  - `thermal-retrieval`
  - `ASTER TES`
  - `ATI`
- 例如这轮里：
  - `task_25_25`
  - `task_68_68`
  这种样本已经不是工具流完全不会了，而是：
  - 工具顺序基本对
  - critic 连续指出同一类 band / path / 语义问题
  - 但 actor 十轮内还是没把 skill 改到能过
- 所以这轮之后，我的判断是：
  - 问题已经不只是 skill schema 本身
  - 更要开始看 actor / critic 自身的行为质量
  - 尤其要看它们是不是在“指出问题”和“真正把 skill 改对”之间还存在明显断层

下一步建议按这个顺序来，不要再发散：

1. 先解决当前 key 的额度问题，再重跑这条已经对齐后的 `v6_ctx16k` 口径。
   - 当前这条 run 不能当正式全量结论
   - 但代码口径已经对齐，直接沿用即可

2. 额度恢复后，重跑顺序仍然保持：
   - 全量 `248` 题训练
   - 聚合成 `6` skill
   - 全量 `248` 题测试

3. 重跑时继续重点盯这几个指标：
   - `prompt_tokens >= 8000 / 16000` 是否重新出现
   - `finish_reason = length` 是否重新出现
   - `403 / quota` 之外，是否还会回到 JSON 截断族

4. 如果额度恢复后结构化问题仍然像这次前半段一样被压住，那么下一轮分析重点就不该再放在“JSON 修没修好”上，而应转去看：
   - actor / critic 对 skill 的修改是否真能收敛
   - ASTER TES / thermal-retrieval / ATI 这类具体 family 的失败是不是已经主要卡在语义与约定，而不是 skill 框架

5. 顺手还可以补两条小修，但它们都不属于这轮主结论：
   - `create_skill requires target_skill_name`
   - `Streaming response produced empty content`
   这两条目前都是边角单例，不应喧宾夺主。
