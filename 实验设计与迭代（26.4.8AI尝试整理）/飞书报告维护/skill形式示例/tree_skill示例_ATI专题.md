# tree skill 示例：ATI 专题

## 这个示例对应什么

这是一个 `tree skill`，对应的原文件是：

[SKILL.md](/data/xsy/project_skills-3.18dhc-19.40/runs/v8_fastline_merged60_20260407_r36/formal60/aggregated_skill_library_v8/tree/ati-change-monthly-summaries-and-low-ati-anomaly-detection-day-night-thermal-albedo/SKILL.md)

它的 mode 细 guidance 在这里：

[EXECUTION_GUIDANCE.md](/data/xsy/project_skills-3.18dhc-19.40/runs/v8_fastline_merged60_20260407_r36/formal60/aggregated_skill_library_v8/tree/ati-change-monthly-summaries-and-low-ati-anomaly-detection-day-night-thermal-albedo/references/EXECUTION_GUIDANCE.md)

## 这个 skill 可以怎么给别人一句话介绍

这是一条“ATI 总 skill + 内部 mode 分流”的树状 skill，先用顶层 skill 做专题级约束，再在内部按具体题型挑 mode。

## 顶层结构怎么读

### 1. `name`

`ati-change-monthly-summaries-and-low-ati-anomaly-detection-day-night-thermal-albedo`

名字比 `flat` 更长，因为它把几类 ATI 子问题都直接写进了 skill 名称里。

### 2. `description`

作用和 `flat` 一样，还是先给 router 看：

- 这是 ATI 专题
- 输入是日夜温度和反照率
- 目标可能是差值、月均值、阈值占比或异常计数

### 3. `allowed-tools`

这条 `tree skill` 的顶层硬包络也是 `9` 个工具：

- `ATI`
- `calc_batch_image_mean`
- `calc_batch_image_mean_mean`
- `calc_batch_image_mean_threshold`
- `calculate_threshold_ratio`
- `difference`
- `get_filelist`
- `read_file`
- `run_python_script`

这里要专门强调给别人：

- 当前 `tree` 的硬工具包络，依然是顶层 skill 的 `allowed-tools`
- `mode` 本身当前不单独再定义一层新的硬包络
- `mode` 做的是更细的流程 guidance 和工具侧重

## tree 比 flat 多出来的一层是什么

### 1. `Execution Profile Index`

这是 tree 的核心结构。顶层不会直接把所有题型的细节都平铺展开，而是先给出一个 mode 索引。

当前这个 ATI tree skill 里有四个 mode：

1. `Mode A`：两时点 ATI 差值
2. `Mode B`：月度 ATI 均值
3. `Mode C`：低 ATI 阈值占比
4. `Mode D`：低 ATI 异常计数

### 2. 顶层只给共性的全局规则

顶层 skill 里先写共性的规则，例如：

1. 先做 discovery
2. 输出写到 task-local 路径
3. pairing 必须依赖 discovery 出来的文件名
4. 多 block 任务要按统一 phase order 执行

这一步的作用是先把大框架定住。

### 3. mode 选完以后再读细 guidance

顶层最后会明确告诉 executor：

- 先选一个 dominant mode
- 再去读 `references/EXECUTION_GUIDANCE.md`

然后在这个 guidance 文件里，再按 `Mode A / B / C / D` 展开：

- applies_when
- preferred_tools
- flow_hint
- canonical_flows
- edge_cases

这就是 tree 的第二层展开。

## 这个 tree skill 的优点

1. 顶层和细节层分开，结构更清楚。
2. 同一个专题下，不同题型可以拿到更贴切的 guidance。
3. 更适合往渐进式披露的方向继续发展。
4. 当专题继续变复杂时，更容易在内部扩 mode。

## 这个 tree skill 当前最值得强调的一点

演示时最容易被问到的问题就是：

“tree 里 mode 算不算新的工具包络？”

当前答案是：

- 不算

更准确地说：

1. `skill` 顶层 `allowed-tools` 是硬包络
2. `mode` 是软约束，负责进一步说明
3. `mode` 当前主要控制的是：
   - 这题更像哪类子任务
   - 哪些工具在这一类题里更常用
   - 步骤顺序应该怎样安排

## 用 `task_08_44` 这类题怎么举例

如果题目是 ATI 月度变化题，tree 这边的讲法可以是：

1. 先路由到 ATI 总 skill
2. 顶层 `allowed-tools` 先把 executor 限制在 ATI 这一小组工具里
3. 再判断这题更接近“月度 ATI 均值”这一类
4. 然后去读 mode 对应的细 guidance，继续决定 helper、生成、统计和比较的顺序

## 演示时可以怎么总结

如果只讲一句，可以说：

这类 `tree skill` 的核心特点，是先用顶层 skill 管专题级工具包络和共性规则，再在内部按 mode 继续展开更细的执行 guidance；当前 mode 还不是新的硬工具包络，它更像专题内部的渐进式披露。
