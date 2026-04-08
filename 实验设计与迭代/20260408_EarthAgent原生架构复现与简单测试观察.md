# 2026-04-08 Earth-Agent 原生架构复现与简单测试观察

## 1. 背景与目标

本专题用于单独记录一次针对 `Earth-Agent` 原始架构的本地复现、最小兼容适配、以及基于本地 `formal60` 子集做的快速测试观察。

这份记录的定位不是聚合 skill 线，也不是当前 `flat/tree` executor 主线，而是：

- 单独看 `Earth-Agent origin` 这一套原生 ReAct + MCP 工具调用架构，在本地环境中是否能跑通。
- 单独看它在 `Qwen3-8B` 条件下的行为特征，尤其是：
  - 是否容易爆上下文
  - 是否容易在长轨迹后收不回 final answer
  - 成功跑完和答对之间差距有多大

## 2. 代码与数据落地位置

本次使用的本地代码仓和数据位置如下：

- 原始仓库克隆并重命名为：
  - `/data/xsy/Earth-Agent-origin`
- benchmark 数据接入：
  - `/data/xsy/Earth-Agent-origin/benchmark/data -> /data/xsy/project_skills-3.18dhc-19.40/benchmark/data`
- `60` 题子集来源：
  - `/data/xsy/project_skills-3.18dhc-19.40/data/task_sets/parallel_skill_training_seed_20260403_formal60/train_all_60.txt`
- smoke 子集来源：
  - `/data/xsy/project_skills-3.18dhc-19.40/data/task_sets/parallel_skill_training_seed_20260403_formal60/smoke_3.txt`

## 3. 原始架构事实确认

先确认几个对后续判断非常关键的事实。

### 3.1 Earth-Agent 的单题求解范式

原始 `Earth-Agent` 是典型的 ReAct 式单线程 agent：

- LLM 读当前问题
- 发起 MCP tool call
- 接收 tool result
- 把历史消息继续累积回上下文
- 再决定下一步

也就是说，它对单题的“记忆”本质上就是完整会话历史累积，并不是单独的外部长期记忆模块。

### 3.2 原始仓库没有显式上下文截断/压缩机制

原始 `origin` 代码里，没有看到针对长轨迹的稳健上下文保护，例如：

- 没有显式 token budgeting
- 没有 message 压缩
- 没有旧步骤裁剪
- 没有摘要回写

因此如果 provider 端不兜底，理论上到长题时就很容易直接打爆上下文。

### 3.3 原始仓库默认脚本并不适合直接本地跑这次实验

原始 `scripts/langchain_qwen3_32B_autoplan.py` 存在几个直接影响本次测试的问题：

- 默认写死 `qwen3-32b`
- 默认没有本次要求的 `stream=True + enable_thinking=True`
- 没有方便的 `question_ids` 过滤
- 默认顺序跑固定切片
- 本地路径和环境变量处理不够稳

因此本次采取的是“保留原始架构，新增最小兼容入口”的方式，而不是大改原始脚本。

## 4. 本地最小兼容适配

这一步的原则是：尽量不改原始架构，只补本地运行必需项。

### 4.1 环境

实际使用的 Python 环境：

- `/data/xsy/miniconda3/envs/earth-bench-skill-eval`

补齐的关键依赖包括：

- `fastmcp`
- `langchain`
- `langchain-openai`
- `langgraph`
- `langchain-mcp-adapters`
- `mcp`

同时确认该环境里可正常导入：

- `osgeo`
- `rasterio`
- `langchain_openai`
- `langgraph`

### 4.2 API 接口口径

本次 `Qwen3-8B` 使用上海实验室 API，且按实际要求固定为：

- `model = qwen3-8b`
- `streaming = True`
- `enable_thinking = True`
- 不启用 JSON object mode

本次实际配置来源：

- `/data/xsy/skill-pool/API说明/最新api说明.txt`

对应本地配置文件：

- `/data/xsy/Earth-Agent-origin/agent/config_qwen3_8b_shlab.json`

### 4.3 新增的最小运行入口

为了不污染原始 `32B` 脚本，本次新增了：

- `/data/xsy/Earth-Agent-origin/scripts/langchain_qwen3_8b_autoplan.py`
- `/data/xsy/Earth-Agent-origin/scripts/run_qwen3_8b_formal60_parallel.sh`

作用分别是：

- `langchain_qwen3_8b_autoplan.py`
  - 保持 Earth-Agent 原生 ReAct + MCP 调用结构
  - 补 `question_ids` / `question_id_file` / `batch_dir`
  - 补 `streaming=True`
  - 补 `enable_thinking=True`
  - 记录每题结果摘要
- `run_qwen3_8b_formal60_parallel.sh`
  - 把 `60` 题分成 `20` 个 batch
  - 每个 batch 负责 `3` 题
  - 统一汇总结果

### 4.4 必要路径修复

原始 `Perception.py` 里把 `model_results.csv` 路径硬编码到了旧机器目录：

- `/root/autodl-tmp/Earth-Agent/benchmark/model_results.csv`

本次改成相对仓库根目录读取，否则 RGB/Perception 相关题会直接失效。

### 4.5 为本轮实验额外加入的 32K/8K 约束

这一步不是原始仓库自带能力，而是本轮测试为了回答“会不会爆上下文”这个问题，显式补上的实验约束。

在 `/data/xsy/Earth-Agent-origin/scripts/langchain_qwen3_8b_autoplan.py` 中额外加入了：

- 输入 token 上限：`32768`
- 输出 token 上限：`8192`
- 本地 tokenizer：`Qwen/Qwen3-8B`

具体做法：

- 每次发请求前，先把 `messages + tools + tool_choice` 等请求载荷序列化
- 用 `Qwen/Qwen3-8B` tokenizer 估算输入 token
- 若估算值 `> 32768`，直接抛 `InputContextLimitExceeded`
- 同时强制 `max_tokens = 8192`

这意味着：

- 本轮“上下文爆掉”不是 provider 随机报错，而是显式按本地 `32K/8K` 预算硬约束出来的
- 所以这次统计的“爆上下文题数”可直接用于判断在 `32K/8K` 约束下原生架构的稳健性

## 5. smoke 与正式 60 题测试

### 5.1 smoke 观察

smoke 过程中主要看的是：

- 环境是否能把 `104` 个 MCP 工具正常加载出来
- `Statistics / Inversion / Perception` 三类链路是否都能被真实进入

smoke 过程中已经暴露出一个非常典型的原生架构问题：

- 某些题即便 benchmark 文件都在，`Qwen3-8B` 也会在第一步就编造不存在的文件名
- 这类问题不是环境错，而是 agent 规划与工具参数对齐能力不够稳

典型样例：

- `35` 题中，模型一开始就会编造 `thermal_band31.tif` 这类不存在的文件
- 后续虽会尝试纠偏，但最终仍经常收不回 final answer

### 5.2 正式 60 题运行目录

本次正式 `20` 并发运行目录：

- `/data/xsy/Earth-Agent-origin/evaluate_langchain/qwen3_8b_shlab_AP_formal60_20260408_160119`

汇总产物：

- `/data/xsy/Earth-Agent-origin/evaluate_langchain/qwen3_8b_shlab_AP_formal60_20260408_160119/run_summary.json`
- `/data/xsy/Earth-Agent-origin/evaluate_langchain/qwen3_8b_shlab_AP_formal60_20260408_160119/merged_results_summary.json`

## 6. 本轮测试结果

### 6.1 运行层结果

在 `32K` 输入 / `8K` 输出约束下，`60` 题总结果如下：

- 总题数：`60`
- 成功跑完：`57`
- 运行失败：`3`

运行失败的 `3` 题中：

- `158`
  - `InputContextLimitExceeded`
  - 估算输入：`34622`
  - 超过上限：`32768`
- `23`
  - `429 insufficient_quota`
- `81`
  - `429 insufficient_quota`

因此本轮真正的“上下文爆掉”题数为：

- `1 / 60`

### 6.2 简单准确率口径

这里的准确率不是 benchmark 官方完整评测脚本，而是基于运行结果对 `Autonomous Planning` 的 `whitelist` 做的简单比对。

口径分两档：

- 严格 exact 对比：`12 / 60`
- 对 `<Answer>B<Answer>` 这类可恢复格式做一次轻微归一化后的语义对比：`13 / 60`

因此，这轮更合理的结论是：

- `57` 不是答对数，而是“成功跑完数”
- 真正能对上的题，大约只有 `12~13 / 60`

## 7. 工具 step 统计

这里把“tool step”定义为轨迹里实际落下来的 `tool` 返回次数。

全体 `60` 题的工具 step 分布：

- `0 step`: `18`
- `1 step`: `8`
- `2 step`: `6`
- `3 step`: `6`
- `4 step`: `7`
- `5 step`: `4`
- `6 step`: `8`
- `7 step`: `1`
- `12 step`: `1`
- `16 step`: `1`

平均值：

- 全体平均：`2.82 step/题`
- 成功跑完的 `57` 题平均：约 `2.96 step/题`

最高工具 step 的题：

- `91`: `16 step`
- `18`: `12 step`
- `76`: `7 step`
- `5 / 19 / 22 / 35 / 98 / 223 / 231 / 240`: `6 step`

观察点：

- 原生架构在这批题上并没有普遍走很长轨迹
- 平均只有约 `3` 个工具 step
- 也正因为平均轨迹不算很长，所以本轮只爆了 `1` 个上下文

## 8. 为什么会有大量 `No answer found`

### 8.1 先说结论

`No answer found` 不是模型原样输出的字符串，而是本地运行脚本的答案提取函数在拿不到最终 `ai` 文本时给出的兜底值。

对应代码在：

- `/data/xsy/Earth-Agent-origin/scripts/langchain_qwen3_8b_autoplan.py`

具体逻辑：

- `extract_answer_from_response(...)`
- 如果倒序扫描不到可解析的最终 `ai` 消息，就返回 `"No answer found"`

### 8.2 数量

本轮 `57` 个成功跑完题里，有：

- `24` 题被记成 `No answer found`

也就是说，`No answer found` 不是边角现象，而是原生架构在 `Qwen3-8B` 下的主要失败形态之一。

### 8.3 这 24 题的 step 分布

`No answer found` 的 `24` 题，工具 step 分布如下：

- `0 step`: `9`
- `1 step`: `2`
- `2 step`: `2`
- `3 step`: `2`
- `4 step`: `2`
- `5 step`: `1`
- `6 step`: `4`
- `7 step`: `1`
- `12 step`: `1`

这说明：

- 它不是单纯“因为轨迹太长所以收不回答案”
- 其中相当一部分甚至一步工具都没跑起来

### 8.4 按停点类型拆解

把这 `24` 题按最后停在哪一类消息上拆开：

- 最后停在 `user`：`6` 题
  - 说明只有用户消息，后续没有任何有效 agent 输出
  - 题号：`3, 105, 123, 153, 186, 206`
- 最后停在 `assistant`：`3` 题
  - 说明模型只生成了 tool call，但没有真正进入稳定执行
  - 题号：`112, 145, 188`
- 最后停在 `tool`：`15` 题
  - 说明工具其实跑了，但 tool result 之后没有再收回最终答案

### 8.5 三类典型 failure pattern

#### Pattern A: 第一跳就空

例如：

- `3`
- `105`
- `123`

日志里只有一条 `user`，没有任何 assistant/tool。

这类更像是：

- 模型流式返回没有形成有效最终 `ai` message
- 或 LangGraph / provider 这一跳没有稳定回写可提取内容

#### Pattern B: 生成了 tool call，但参数是占位/伪数据

例如：

- `112`

它直接生成：

- `compute_linear_trend`

但参数里是：

- `/* list of nighttime light intensity values from 2013 to 2024 */`

也就是模型没有先取数，而是直接把“应该存在的数据”写成伪参数发出去，随后轨迹就停了。

这类问题本质上是：

- 规划存在
- 但 grounded tool argument 不成立

#### Pattern C: 工具都跑出来了，但没有 final answer closing

例如：

- `35`

这题实际经历了：

- 先猜错文件名
- 再用 `get_filelist` 纠偏
- 再成功跑出 `split_window`
- 再成功拿到热点比例结果

但最后停在 tool result，没有回一个 `A/B/C/D`。

这一类是本轮最典型的问题：

- 不是工具不能用
- 不是上下文爆了
- 而是 answer-closing 失败

## 9. 对 Earth-Agent 原生架构的几条核心观察

### 9.1 原生架构在 `Qwen3-8B` 下的主要问题不是上下文，而是闭环收尾能力

本轮在显式 `32K/8K` 约束下，只爆了 `1` 题上下文。

但同时：

- `24` 题是 `No answer found`
- 只有 `12~13` 题对上 benchmark

因此当前瓶颈更像是：

- tool planning 和 grounded execution 不稳
- tool result 后的 final answer closing 更不稳

而不是“平均每题都爆上下文”。

### 9.2 `成功跑完` 和 `答对` 是两回事

这次最容易误读的点就是：

- `57/60` 是成功跑完
- 不是答对 `57/60`

实际上原生架构在这次测试中的表现更接近：

- 大部分题能把流程跑起来
- 但最终正确率仍然很低

### 9.3 低 step 也可能完全失败

很多 `No answer found` 题：

- `0 step`
- 或 `1 step`

所以“轨迹短”不代表“稳”。

在 `Qwen3-8B` 这种较小模型下，很多题甚至在进入真实工具链之前就已经失去闭环能力。

### 9.4 长轨迹题确实更接近风险边界，但不是主流失败形态

本轮唯一显式爆上下文的是：

- `158`

这说明：

- 长题/长轨迹的确存在 context risk
- 但在这批 `60` 题里，它还不是最主要的失败来源

## 10. 对后续实验的启示

如果后面还要继续把 `Earth-Agent origin` 作为对照组，可以明确记住下面几点：

- 若目标是看“原生架构在小模型下是否稳”，重点不要只盯上下文爆不爆
- 更要看：
  - 第一跳能否稳定起步
  - 工具参数是否 grounded
  - 工具结果后能否稳定收尾给 final answer

若后面还要继续做原生线的对照，建议至少补三类增强：

- 强制 final answer closing 提示与后处理
- 更稳的工具参数纠错/重试
- 更显式的长上下文预算与裁剪

否则会出现一种典型现象：

- 爆上下文题数看起来不多
- 但大量题其实死在“没收回答案”或“收回了错误答案”

## 11. 当前可直接复用的关键产物

可直接回看和复用的路径如下：

- 原始仓库本地副本：
  - `/data/xsy/Earth-Agent-origin`
- 本轮新增运行入口：
  - `/data/xsy/Earth-Agent-origin/scripts/langchain_qwen3_8b_autoplan.py`
- 本轮并发 launcher：
  - `/data/xsy/Earth-Agent-origin/scripts/run_qwen3_8b_formal60_parallel.sh`
- 本轮正式运行目录：
  - `/data/xsy/Earth-Agent-origin/evaluate_langchain/qwen3_8b_shlab_AP_formal60_20260408_160119`
- 本轮结果汇总：
  - `/data/xsy/Earth-Agent-origin/evaluate_langchain/qwen3_8b_shlab_AP_formal60_20260408_160119/run_summary.json`
  - `/data/xsy/Earth-Agent-origin/evaluate_langchain/qwen3_8b_shlab_AP_formal60_20260408_160119/merged_results_summary.json`

