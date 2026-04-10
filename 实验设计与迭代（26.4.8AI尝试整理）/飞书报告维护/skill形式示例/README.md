# skill 形式示例

## 这个文件夹是做什么的

这里不是正式运行用的 skill 文件，这里是为了汇报和演示准备的中文讲解版。

当前 `v8` 聚合后总共有 `6` 个 skill。每个 skill 都有自己的顶层 `allowed-tools`，也就是这个 skill 被激活后 executor 能使用的硬工具包络。

如果激活的是 `tree skill`，后面还会在 skill 内部继续选一个 `mode`。这里要单独强调：

- `skill` 的 `allowed-tools` 是硬包络
- `mode` 负责更细的执行 guidance、流程顺序和工具侧重
- 当前 `mode` 不单独再定义一层新的硬工具包络

## 这里放了什么

1. `flat_skill示例_ATI专题.md`
2. `tree_skill示例_ATI专题.md`

这两个例子选的是同一个 ATI 专题，对比时比较直观：

- `flat`：把同一专题的执行逻辑写成一整条 skill
- `tree`：先给一个总 skill，再在内部按 mode 细分

## 演示时建议怎么讲

### 先讲共性

1. 顶层都有 `name / description / allowed-tools / metadata`
2. `router` 主要看 `name + description`
3. `executor` 主要看 `allowed-tools + SKILL.md 主体`

### 再讲差异

1. `flat` 把所有 guidance 直接写在主 skill 里
2. `tree` 会先给一个 `Execution Profile Index`
3. executor 先选 mode，再去读 `references/EXECUTION_GUIDANCE.md`
4. 这样 guidance 可以更细，内部也更适合做渐进式披露

## 原始来源

- `flat` 原文件：
  [SKILL.md](/data/xsy/project_skills-3.18dhc-19.40/runs/v8_fastline_merged60_20260407_r36/formal60/aggregated_skill_library_v8/flat/apparent-thermal-inertia-ati-summaries-and-anomaly-detection/SKILL.md)
- `tree` 原文件：
  [SKILL.md](/data/xsy/project_skills-3.18dhc-19.40/runs/v8_fastline_merged60_20260407_r36/formal60/aggregated_skill_library_v8/tree/ati-change-monthly-summaries-and-low-ati-anomaly-detection-day-night-thermal-albedo/SKILL.md)
- `tree` 的 mode guidance：
  [EXECUTION_GUIDANCE.md](/data/xsy/project_skills-3.18dhc-19.40/runs/v8_fastline_merged60_20260407_r36/formal60/aggregated_skill_library_v8/tree/ati-change-monthly-summaries-and-low-ati-anomaly-detection-day-night-thermal-albedo/references/EXECUTION_GUIDANCE.md)
