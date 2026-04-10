# 2026-04-08 `executor` token budget 与输入窗重跑

## 1. 这轮实验的职责

这篇只记录 `executor` memory / budget 调整后的重跑结果，不再和：

- 初始 `formal60` 首跑
- `no-skill / all-tools`
- `Earth-Agent origin`

混写。

这轮最核心的问题是：

- 当 `executor` 从旧压缩逻辑切到 token budget 后，`formal60` 实际能提升多少

---

## 2. 代码与配置层面的真实变化

### 2.1 代码入口

当前 token-budget 核心逻辑在：

- `nlrl_skills/agent_loop.py:181-247`
- `nlrl_skills/agent_loop.py:295-300`

关键变化是：

- 不再用旧的字符级 `max_context_chars`
- 进入按 tokenizer 计数的 prompt budgeting
- 尽量按 turn 保完整历史
- input budget 由 `executor_total_token_budget - executor.max_tokens` 决定

### 2.2 配置入口

当前 runtime 字段在：

- `nlrl_skills/config.py:45-47`

默认值：

- `executor_total_token_budget = 32768`
- `executor_tokenizer_path = /data/xsy/codes/checkpoints/Qwen3-8B`

---

## 3. 本轮实际分成三档口径

### 3.1 smoke 验证

目录：

- `runs/v8_executor_token_budget_smoke_20260408_r2`

结果：

| 分支 | success | ACC |
| --- | ---: | ---: |
| `flat` | `1 / 1` | `1.0000` |
| `tree` | `1 / 1` | `1.0000` |

说明：

- 这一步主要用于证明“新 budget 逻辑真的生效了”，不承担稳态结论职责

### 3.2 `40960 total / 8192 output`

目录：

- `runs/v8_executor_input32k_output8k_formal60_20260408_r1_flat`
- `runs/v8_executor_input32k_output8k_formal60_20260408_r1_tree`

这里要特别记住：

- `executor_total_token_budget = 40960`
- `executor.max_tokens = 8192`
- 所以 input budget 实际是 `32768`

对应 `config_snapshot.json` 已经验证。

### 3.3 `32768 total / 8192 output`

目录：

- `runs/v8_executor_token_budget_formal60_20260408_r5_c20`

这里的口径是：

- `executor_total_token_budget = 32768`
- `executor.max_tokens = 8192`
- 所以 input budget 实际是 `24576`

这也是当前代码默认值更接近的一档。

---

## 4. 结果表

### 4.1 和首轮 `formal60` 的直接对照

| 口径 | 分支 | success | ACC | TAO | TIO |
| --- | --- | ---: | ---: | ---: | ---: |
| 首轮旧口径 | `flat` | `15 / 60` | `0.2500` | `0.5372` | `0.4602` |
| 首轮旧口径 | `tree` | `18 / 60` | `0.3000` | `0.6836` | `0.6298` |
| `40960 total` | `flat` | `25 / 60` | `0.4167` | `0.6911` | `0.6450` |
| `40960 total` | `tree` | `25 / 60` | `0.4167` | `0.6997` | `0.6354` |
| `32768 total` | `flat` | `24 / 60` | `0.4000` | `0.6700` | `0.6187` |
| `32768 total` | `tree` | `25 / 60` | `0.4167` | `0.6722` | `0.5901` |

证据：

- `runs/v8_fastline_merged60_20260407_r36/.../evaluation_summary.json`
- `runs/v8_executor_input32k_output8k_formal60_20260408_r1_*/.../evaluation_summary.json`
- `runs/v8_executor_token_budget_formal60_20260408_r5_c20/.../evaluation_summary.json`

### 4.2 先看最重要的结论

如果只压一句：

- `executor` 的 token-budget 改造是真正带来分数提升的关键工程改动之一

尤其是 `flat`：

- 从 `15 / 60` 直接抬到了 `24~25 / 60`

这说明旧 `flat` 低分里，有相当一部分来自 memory / budget 机制，不能直接归到 skill 设计本身。

---

## 5. 这轮最关键的结构性变化

### 5.1 `flat` 与 `tree` 的差距显著缩小了

在首轮旧口径里，结论很像：

- `tree` 明显优于 `flat`

但进到 token-budget 模式后，结果变成了：

- `40960 total` 下两者都到 `25 / 60`
- `32768 total` 下 `flat = 24`，`tree = 25`

这说明：

- 旧口径下 `tree` 的优势，至少部分来自它对 memory 压力更友好
- 当 memory 条件改善后，`flat` 的真实能力被释放出来了不少

### 5.2 更大的输入窗有帮助，但优势有限

比较两档预算：

- `40960 total` 让 `flat` 比 `32768 total` 多 `1` 个成功题
- `tree` 在 ACC 上持平

这说明：

- 扩窗是有收益的
- 但收益有明显上限
- 继续加窗并不能替代 skill 注入瘦身与执行逻辑修正

---

## 6. 这轮里最该记住的一个样本

旧观察里已经点过一个代表样本：

- `task_08_44 step3`

对应文件：

- `runs/v8_executor_input32k_output8k_formal60_20260408_r1_flat/eval_runs_flat/formal60_flat_eval/task_08_44/env/executor/executor_steps/20260408T041851Z_executor_step_3_request.json`
- `runs/v8_executor_input32k_output8k_formal60_20260408_r1_flat/eval_runs_flat/formal60_flat_eval/task_08_44/env/executor/executor_steps/20260408T041851Z_executor_step_3_response.json`

这个样本说明了两点：

1. 即使 input budget 已经抬到 `32768`，真实 request 仍然会贴顶。
2. 贴顶时并不代表历史完全不保留，而往往是“一边保，一边截，一边仍然贴顶”。

所以这轮实验最不该得出的误读是：

- “既然已经给了更大窗口，memory 问题就解决了”

更准确的说法是：

- token-budget 把问题从“过早近失忆”推进到了“更晚才触顶”

---

## 7. 这轮对当前主线判断带来的修正

### 7.1 旧判断被修正的地方

首轮 `formal60` 后，很容易形成一种倾向：

- `tree` 设计比 `flat` 更对

这轮重跑把这个判断修正成了：

- `tree` 在旧 memory 条件下更稳
- 但 `flat` 并不天然差

### 7.2 仍然没有被推翻的地方

这轮并没有推翻下面这些判断：

- skill 本体过肥仍然是问题
- `vision` 大簇仍然值得优先修
- 扩窗不能替代 skill 注入瘦身

---

## 8. 当前最实用的结论

如果现在要基于这轮结果继续推进，最实用的结论是：

1. `executor` token budget 必须视为当前主线默认能力，不应再回到旧字符模式。
2. 后续评估 `flat/tree` 时，必须注明 budget 口径，否则结果不可比。
3. 当前代码默认值是 `32768 total`，但现有 run 中表现最好的 `flat` 结果来自 `40960 total` 口径。
4. 下一轮优化重点不应只盯预算，而应把注意力转向：
   - skill 注入体积
   - `vision` 执行质量
   - final answer closing

---

## 9. `32K` 输入上限案例补充分析

### 9.1 统计口径与总数

这部分只看 `40960 total / 8192 output` 这组 run，也就是 input budget 实际为 `32768` 的两套结果：

- `runs/v8_executor_input32k_output8k_formal60_20260408_r1_flat`
- `runs/v8_executor_input32k_output8k_formal60_20260408_r1_tree`

统计口径：

- 扫描所有 `env/executor/executor_steps/*_response.json`
- 若 `usage.input_tokens == 32768`，记为一次“executor request 打到 32K”
- 任务数按“该任务至少出现过一次 hit”统计

结果：

| 分支 | 命中任务数 | 命中 step 数 | 命中任务成功数 | 命中任务失败数 | 命中任务 |
| --- | ---: | ---: | ---: | ---: | --- |
| `flat` | `3 / 60` | `14` | `1` | `2` | `task_06_42`, `task_08_44`, `task_12_19` |
| `tree` | `4 / 60` | `27` | `0` | `4` | `task_06_42`, `task_08_44`, `task_11_18`, `task_12_19` |

补一句更直观的结论：

- `32K` 贴顶并不普遍，主要集中在长时间序列、超大 file list、或逐日 / 逐 acquisition 执行的题
- 两个分支合并后是 `7` 个“任务-分支案例”，对应 `4` 个唯一题号：`18 / 19 / 42 / 44`

证据字段在：

- `.../env/executor/executor_steps/*_response.json` 的 `usage.input_tokens`
- `.../env/state.json`
- `.../evaluation_summary.json`

### 9.2 `flat` 命中案例

#### 9.2.1 `task_08_44`：早期大 file list + helper 重试，把窗口很快打满

命中 step：

- `3 / 4 / 5 / 6 / 15 / 17 / 19 / 20`

过程特征：

- 第 1 步 `get_filelist` 已经把 `2019-06` 到 `2019-09` 的大量白天 / 夜间温度与 albedo 文件带进上下文
- 第 2 步 `sort_raster_timeseries.py` 因 regex 不匹配，直接把整批文件都丢进 `dropped`
- 后续多次 `run_python_script` 一直在重复“按月分组”的动作
- helper 返回的是 `benchmark/data/question44/2019_06_*.tif` 这类 glob 占位符，executor 又把它当真实文件路径喂给 `calc_batch_image_mean`

这里的核心问题有三层：

- helper / regex 错配
- glob 占位符泄漏进后续工具调用
- repeated identical call 持续回灌整批大文件列表

结果：

- 20 步基本耗在分组失败与重复重试上
- 任务没有真正进入 ATI 月度统计，更没有走到 trend / largest decrease 判断

#### 9.2.2 `task_06_42`：中后段长链补算，把 32K hit 推迟到了 step 17 之后

命中 step：

- `17 / 18 / 19 / 20`

过程特征：

- 前 4 步先经历 ATI 配对脚本持续失败
- 第 5 步开始改成手工逐日 `ATI`
- 先顺着算到 `2023-05-08`
- 第 13 步 `calculate_threshold_ratio` 又先用错了 `mode='lt'`
- 改成 `below` 后，又连续暴露出 `2023-05-09`、`05-10`、`05-11`、`05-12` 的 ATI 文件根本没准备好

所以这题的主问题集中在：

- 长链逐日补算
- 批量 ATI 生成没有闭环
- threshold 阶段每次都在推动“再补一天，再试一次”

结果：

- 32K hit 出现在很后面，说明它更像历史累积的后果
- 真正伤分的是执行粒度过细和中间产物始终不完整
- 最后 step 用尽，没有有效 closing

#### 9.2.3 `task_12_19`：也 hit 了 32K，但仍然成功

命中 step：

- `6 / 7`

过程特征：

- 前半段连续卡在 MODIS 文件名解析：date regex 不对，helper 入参也不对
- 上下文很快变肥，所以在第 6、7 步已经 hit `32768`

但这个任务仍然成功，说明：

- “打到 32K”本身不等于必败
- 如果后续链路很短，closing 也比较直接，任务仍有机会收住

`flat` 小结：

- `flat` 的 hit 分成两类：一类是大 file list + helper 重试导致的早期贴顶；另一类是长链单日补算导致的后期贴顶
- 前一类更危险，因为任务会一直停在“准备阶段”里打转

### 9.3 `tree` 命中案例

#### 9.3.1 `task_11_18`：典型的“逐 acquisition 执行到 step budget 耗尽”

命中 step：

- `5 / 9 / 10 / 12 / 13 / 14 / 15 / 16 / 17 / 18 / 19 / 20`

过程特征：

- 第 2 步 helper 先因 key 不对报错
- 第 3 步修正后成功拿到 acquisition 分组
- 之后第 4 到 20 步几乎全是“一次 acquisition 一个 `band_ratio`”
- 到结束前都还停留在 PWV raster 生产阶段

这题的根因非常清楚：

- 时间序列太长
- 执行粒度过细
- yearly aggregation / trend computation 被整段挤掉了

这里 `32K` 更像是一个伴随症状：

- 历史中累积了大量成功的中间产物路径
- 但真正让任务失败的是“产物生产链太长，聚合链来不及开始”

#### 9.3.2 `task_12_19`：helper 契约来回修，接着又进入逐日执行

命中 step：

- `8 / 9 / 10 / 11 / 12 / 13 / 17 / 18 / 19 / 20`

过程特征：

- 第 3、4、7 步都卡在 helper contract 上，像 `data_dir`、`filenames` 这类字段不断漂移
- 第 8 步之后转成逐日 `band_ratio`
- 第 18 到 20 步才开始做 `get_percentile_value_from_image`
- 到 step 20 也只来得及看前 3 天的 percentile

这题说明：

- tree 在 helper 指导上更依赖脚本契约
- 一旦前面来回修 schema，后面就很容易被迫走“每天一步”的保守路线
- hit `32K` 后仍然在处理中间件，最终没有时间完成 peak value 汇总与 final answer

#### 9.3.3 `task_08_44`：方向比 `flat` 更对，但最后给了伪 blocker

命中 step：

- `6 / 7`

过程特征：

- tree 比 `flat` 多做了一步 `read_file("references/ATI_pairing_notes.md")` 式的指导读取
- 思路上知道要做时间戳归一化
- 但重新配对后仍拿到空结果
- 最终把“配对失败”解释成“缺少 albedo 文件”

这一步的问题在于：

- `get_filelist` 其实已经读到了大量 `Beijing_2019-.._albedo.tif`
- blocker 结论与已有观测不一致

所以这是一个很典型的：

- 配对 / 归一化失败
- 然后输出伪 blocker

#### 9.3.4 `task_06_42`：helper 连续报错后，只做了单日证据就提前收口

命中 step：

- `7 / 8 / 9`

过程特征：

- 前半段多个 ATI helper 连续 traceback
- 后面只算了 `2023-05-01` 一天 ATI
- 随后就做了一次 `calculate_threshold_ratio`
- final 直接给出 “约 `96.47%`，但和选项不匹配”

这里的问题很明确：

- 证据覆盖范围严重不足
- 只做了单日样本，就把整月题收掉了

这类错误和 `flat task_06_42` 不一样：

- `flat` 是补算链太长，最终收不住
- `tree` 是 helper 报错后过早收口，证据量不够

`tree` 小结：

- `tree` 的 hit 更常见，而且更像“中后段持续贴顶”
- 原因主要来自更细的流程拆分和更保守的逐块执行
- 这能减少明显乱跳，但在长时间序列题里很容易把 step budget 和 32K 窗口一起吃满

### 9.4 错误类型归类

把这些 hit 案例压成几类，可复用的错误模式大致是：

| 错误类型 | 具体表现 | `flat` 案例 | `tree` 案例 |
| --- | --- | --- | --- |
| helper / schema / regex 错配 | `files` / `filenames` / `data_dir` / date regex 用错，导致脚本空返回或 traceback | `task_08_44`, `task_12_19` | `task_06_42`, `task_11_18`, `task_12_19` |
| 配对或路径语义错误 | glob 占位符被当作真实路径；已有 albedo 仍被判成缺失 | `task_06_42`, `task_08_44` | `task_08_44` |
| 长链枚举导致聚合来不及开始 | 一天一步或一次 acquisition 一步，中间产物很多，聚合与 closing 被挤掉 | `task_06_42` | `task_11_18`, `task_12_19` |
| closing 失败 | 没有 final、给出伪 blocker、或只用局部证据就提前作答 | `task_06_42`, `task_08_44` | `task_06_42`, `task_08_44`, `task_11_18`, `task_12_19` |

最后把这部分和第 6 节连起来看，更准确的判断是：

- `32K` 扩窗已经把问题推迟到了更后面
- 当前剩下的 hit 案例，核心矛盾主要落在执行链本身
- 更主要的失败来源是 helper 契约不稳、批处理粒度过细、以及 closing 太晚或太早

所以如果后面要继续压这类失败，最有效的方向会是：

1. 把 `files / filenames / file_list / data_dir` 这类 helper schema 再收紧一层。
2. 给 EO 长序列题补更强的批处理入口，减少“一天一步 / 一次 acquisition 一步”。
3. 当 executor 已经出现 repeated-call 模式，且 `usage.input_tokens` 多次贴 `32768` 时，尽快切摘要态或中断重试链。
