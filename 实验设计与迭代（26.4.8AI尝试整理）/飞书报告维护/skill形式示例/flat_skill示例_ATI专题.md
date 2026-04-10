# flat skill 示例：ATI 专题

## 这个示例对应什么

这是一个 `flat skill`，对应的原文件是：

[SKILL.md](/data/xsy/project_skills-3.18dhc-19.40/runs/v8_fastline_merged60_20260407_r36/formal60/aggregated_skill_library_v8/flat/apparent-thermal-inertia-ati-summaries-and-anomaly-detection/SKILL.md)

它处理的是一类 ATI 题：

- 先用白天温度、夜间温度和反照率生成 ATI
- 再去做月均值、两时点差值、阈值占比、异常计数这些下游统计

## 这个 skill 可以怎么给别人一句话介绍

这是一条“ATI 生成 + ATI 汇总”的单层 skill，工具包络和执行逻辑都直接写在主 skill 里。

## 顶层结构怎么读

### 1. `name`

`apparent-thermal-inertia-ati-summaries-and-anomaly-detection`

含义很直接：这是一个 ATI 汇总类 skill。

### 2. `description`

这里会告诉 router：

- 输入大概是什么
- 任务目标是什么
- 它适合哪些 ATI 类题目

### 3. `allowed-tools`

当前这条 `flat skill` 的顶层硬包络一共是 `9` 个工具：

- `ATI`
- `calc_batch_image_mean`
- `calc_batch_image_mean_mean`
- `calc_batch_image_mean_threshold`
- `calculate_threshold_ratio`
- `difference`
- `get_filelist`
- `read_file`
- `run_python_script`

这里演示时可以强调：

- 这 `9` 个工具就是 executor 在这条 skill 下能用的硬边界
- 超出这个集合的工具，当前题里不应该再被调用

### 4. `metadata`

这里主要是给实验记录和复盘用的，例如：

- 来自哪个 cluster
- 聚合模型是什么
- 由多少 source skills 聚合而来
- 共享资源有多少

这部分一般不直接决定执行动作。

## 正文 guidance 怎么读

### 1. 先说它回答了什么问题

正文开头会先说明它适合哪几类 ATI 任务：

1. 两个时点做 ATI 差值
2. 一个周期做 ATI 月均值
3. ATI 阈值占比
4. ATI 异常计数

### 2. 再给一条统一执行骨架

这个 `flat skill` 把流程直接写成一条总骨架：

1. `Discovery`
2. `Block definition`
3. `Pairing / selection plan`
4. `ATI` 生成
5. block 内统计
6. 最终比较和答案映射

这里的特点是：

- 所有题型都共用一条主骨架
- 细分情况写在这条骨架下面
- executor 打开这一个文件，就能拿到绝大部分 guidance

### 3. 辅助脚本也在这里统一挂着

这条 skill 同时会列出它能配合使用的脚本资源，例如：

- `pair_ati_inputs.py`
- `plan_ati_by_date.py`
- `select_ati_inputs.py`
- `expected_days_in_month.py`

也就是说，这种 `flat` 写法会把“主逻辑 + 分支逻辑 + 辅助资源”都堆在一条 skill 里。

## 这个 flat skill 的优点

1. 结构简单，拿到一条 skill 就能直接执行。
2. executor 不需要先理解 mode 结构。
3. 对实现和调试比较直接。

## 这个 flat skill 的代价

1. 同一条 skill 里承载的信息更多。
2. 题型一多，正文会继续变长。
3. 小模型读起来更容易感觉“这一条什么都管”。

## 演示时可以怎么总结

如果只讲一句，可以说：

这类 `flat skill` 的核心特点，是把一个专题下常见题型的工具包络和执行 guidance 一次性写在一条主 skill 里，消费起来直接，体积也更容易变重。
