# PAT-Agent 旧版 Full Pipeline 源码执行顺序详解

## 0. 本文分析对象与阅读规则

本文只分析旧版 Full Pipeline 的真实代码执行顺序，不按论文概念重新分组。

- 主程序：[`archive/Automated_Pipelines/Full_Pipeline/pipeline.py`](../Automated_Pipelines/Full_Pipeline/pipeline.py)
- 论文数据集：[`archive/Datasets/PAT.json`](../Datasets/PAT.json)
- 语法提示：[`archive/Automated_Pipelines/Full_Pipeline/syntax-dataset.json`](../Automated_Pipelines/Full_Pipeline/syntax-dataset.json)
- RAG 示例库：[`archive/Automated_Pipelines/Full_Pipeline/database-rag-claude.json`](../Automated_Pipelines/Full_Pipeline/database-rag-claude.json)

旧源码注释将主流程标成 Stage 1–7，但实际控制流还包括程序初始化、history 回读、代码块提取、外层代码生成重试、内层返修语法重试等环节。本文按照 Python 实际执行的先后顺序，拆成 17 步。

## 1. 先纠正此前简化 JSON 的问题

此前展示的单个 JSON 对象不能直接交给旧 pipeline，原因有三点。

### 1.1 主程序要求输入是数组，不是单个对象

源码第 985–990 行读取 `./PAT.json`，并对数组中的对象逐个处理：

```json
[
  {
    "modelName": "keyless_car"
  }
]
```

不能直接写成：

```json
{
  "modelName": "keyless_car"
}
```

### 1.2 旧代码直接读取 `subsystemCount`

`gen_const_and_vars()` 第 123 行直接访问：

```python
structuredData['subsystemCount']
```

因此缺少该字段会触发 `KeyError`。只有一个 `motor` 时也必须写：

```json
"subsystemCount": 1
```

### 1.3 自定义可达状态必须声明 `stateName` 和 `reachabilityType`

当且仅当：

```json
"reachabilityType": "customize"
```

旧代码才会读取 `customDescription`。如果省略它，代码会转而读取 `conditions`；如果 `conditions` 也没有，就生成 `no conditions provided`。

可被旧代码正确理解的简化任务至少应是：

```json
[
  {
    "index": 14,
    "modelName": "keyless_car",
    "modelDesc": "A car engine system.",
    "interactionMode": "skip",
    "subsystemCount": 1,
    "subsystems": [
      {
        "name": "motor",
        "description": "The engine can start, stop, drive, consume fuel and refuel."
      }
    ],
    "assertions": [
      {
        "assertionType": "reachability",
        "component": "",
        "stateName": "drivewithoutfuel",
        "reachabilityType": "customize",
        "customDescription": "the car is moving while the fuel is empty",
        "assertionTruth": "Invalid"
      }
    ]
  }
]
```

即便采用以上格式，当前旧脚本仍然不能原样执行，因为其第 987 行硬编码要求输入数组长度必须为 6，而且多个文件路径仍是占位路径。

---

## 2. 步骤 0：Python 加载模块、读取环境变量并创建 LLM 客户端

### 代码位置

`pipeline.py` 第 2–22 行。

### 做了什么

1. 导入 JSON、时间、文件、正则和子进程模块。
2. 导入 OpenAI SDK 和 Anthropic SDK。
3. 导入 TF-IDF 与余弦相似度，用于后面的本地 RAG 检索。
4. 从操作系统环境变量读取两个 API Key。
5. 创建 OpenAI 客户端 `client` 和 Anthropic 客户端 `client_claude`。

### 输入

| 输入 | 来源 | 含义 | 是否必需 |
|---|---|---|---|
| `OPENAI_API_KEY` | 操作系统环境变量 | 调用 Planning LLM（o3-mini） | 是 |
| `CLAUDE_API_KEY` | 操作系统环境变量 | 调用 Code Generation/Repair LLM（Claude） | 是 |

### 输出

| 输出 | 类型 | 含义 |
|---|---|---|
| `client` | `OpenAI` 客户端对象 | 后续常量、变量、动作和自然语言注释生成共用 |
| `client_claude` | `Anthropic` 客户端对象 | 后续 CSP# 生成与返修共用 |

### 异常与停止条件

代码使用 `os.environ[NAME]`，没有默认值。如果任一变量不存在，程序在进入 `main` 之前直接因 `KeyError` 终止。

### 步骤作用总结

为后面两个不同角色的 LLM 初始化连接。此时还没有读取任何系统模型。

---

## 3. 步骤 1：进入主程序，读取 `PAT.json` 并遍历任务

### 代码位置

`pipeline.py` 第 983–991 行。

### 做了什么

1. 判断脚本是否作为主程序运行。
2. 使用相对路径 `./PAT.json` 打开输入。
3. `json.load()` 将其解析为 Python 列表。
4. 用 `assert len(structured_data_list) == 6` 强制要求恰好 6 个任务。
5. 使用 `for i in range(...)` 按列表顺序处理每个任务。
6. 将当前任务保存为 `current_structured_data`。

### 输入位置

代码要求当前工作目录下存在：

```text
./PAT.json
```

仓库归档数据实际位于：

```text
archive/Datasets/PAT.json
```

归档数据包含 26 个任务，与源码硬编码的 6 个不一致，因此不能不加修改直接使用。

### 顶层输入字段表

| 字段 | 类型 | 代码如何使用 | 含义 |
|---|---|---|---|
| `index` | 整数 | pipeline 没有使用 | 数据集编号，仅用于外部标识 |
| `modelName` | 字符串 | 模型描述、输出目录、计时记录、最终数据库 | 整个系统的名称 |
| `modelDesc` | 字符串 | 拼接到动作提取和代码生成上下文 | 系统总体自然语言描述 |
| `interactionMode` | 字符串 | 常量去重提示、总系统组合注释 | 多个 subsystem 的组合方式 |
| `subsystemCount` | 整数 | 写进 LLM prompt | 声明 subsystem 数量 |
| `subsystems` | 数组 | 构造每个进程的自然语言描述 | 系统由哪些子进程组成 |
| `assertions` | 数组 | 生成断言注释、核对断言数量、读取期望结果 | 用户希望验证的性质 |

### `subsystems[]` 字段表

| 字段 | 类型 | 含义 |
|---|---|---|
| `name` | 字符串 | 子系统/进程名称，后续期望对应 CSP# 进程名 |
| `description` | 字符串 | 子系统行为的自然语言描述；Planning LLM 从中推断状态与动作 |

### `assertions[]` 完整字段表

| 字段 | 类型 | 代码是否使用 | 含义 |
|---|---|---|---|
| `assertionType` | 字符串 | 使用 | `deadlock-free`、`reachability` 或 `ltl` |
| `component` | 字符串 | 使用 | 非空时在指定 subsystem 上验证；空时在总系统上验证 |
| `stateName` | 字符串 | 使用 | 要定义并检查的状态谓词名称 |
| `reachabilityType` | 字符串 | 使用 | `customize` 使用自然语言；其他值使用结构化 `conditions` |
| `customDescription` | 字符串 | 使用 | 自定义状态或自定义 LTL 的自然语言描述 |
| `conditions` | 数组 | 使用 | 结构化的变量—值条件 |
| `assertionTruth` | 字符串 | 使用 | 用户期望 PAT 得到 `Valid` 或 `Invalid`；空值默认 `Valid` |
| `ltlLogic` | 字符串 | 使用 | LTL 的自然语言逻辑词；下划线会替换为空格 |
| `ltlTarget` | 字符串 | 使用 | `customize`、`action` 或 `state`，决定如何生成 LTL 注释 |
| `selectedActions` | 数组 | 使用 | `ltlTarget=action` 时列出目标 action |
| `editingFinished` | 布尔值 | 未使用 | 前端编辑状态，不影响 pipeline |

### `conditions[]` 字段表

| 字段 | 类型 | 含义 |
|---|---|---|
| `variable` | 字符串 | 条件引用的变量名 |
| `value` | 字符串 | 期望变量等于的值 |
| `connector` | 字符串 | 多条件间的 `AND`/其他连接符；可达性处理中默认 `AND` |

### 输出

| 输出 | 类型 | 含义 |
|---|---|---|
| `structured_data_list` | 对象数组 | 全部输入任务 |
| `i` | 整数 | 当前任务在输入数组中的位置 |
| `current_structured_data` | 对象 | 当前要进入 Stage 1 的任务 |

### 步骤作用总结

建立整个 pipeline 的任务循环。后面的所有阶段都围绕一个 `current_structured_data` 顺序执行。

---

## 4. 步骤 2：Stage 1——调用 Planning LLM 提取常量和变量

### 代码位置

- 主程序调用：第 993–995 行。
- `gen_const_and_vars()`：第 118–176 行。
- 通用 OpenAI 调用 `get_LLM_answers()`：第 36–89 行。
- 运行时间记录 `save_run_time()`：第 90–113 行。

### 做了什么

1. 遍历 `subsystems`，生成如下文本：

   ```text
   process 1: process name: motor, process description: ...
   ```

2. 将 subsystem 数量、描述和 `interactionMode` 填入 prompt。
3. 要求 o3-mini 从自然语言中提取常量和变量。
4. 调用 `get_LLM_answers(prompt, structuredData, 'const')`。
5. 保存 LLM 问答记录。
6. 保存该阶段耗时。

### 输入字段

| 字段 | 来源 | 用途 |
|---|---|---|
| `subsystemCount` | 当前任务 | 告诉 LLM 应分析几个进程 |
| `subsystems[].name` | 当前任务 | 作为输出中的 `processName` |
| `subsystems[].description` | 当前任务 | 推断常量、变量、初始值和可能值 |
| `interactionMode` | 当前任务 | 提醒 LLM在 `none`/`choice` 下避免重复声明共享变量 |
| `modelName` | 当前任务 | 仅用于运行时间记录文件名 |

### Prompt 对输出施加的全部主要规则

1. 常量字段必须有 `name`、整数 `value`、`description`。
2. 变量需要 `name`、`type`、`possibleValues`、`initialValue`、`description`。
3. 常量先于变量；所有进程中的名称必须唯一，且不能与进程名冲突。
4. `none` 或 `choice` 模式下，共享常量/变量只放在第一个进程，避免重复。
5. 初始值根据常识或描述推断。
6. 变量类型只允许 `int` 或 `array`。
7. 数组必须提供列表形式初值。
8. `possibleValues` 必须是常量或数字的逗号分隔列表，不能写自然语言范围。
9. 不要把用于动作选择或控制流的内容误建模为普通变量。
10. `processName` 必须保持用户输入名称不变。
11. 最终响应必须是可直接解析的 JSON 字符串。

### LLM 目标输出结构

```json
{
  "processes": [
    {
      "processName": "motor",
      "constants": [
        {
          "name": "FUEL_EMPTY",
          "value": "0",
          "description": "empty fuel level"
        }
      ],
      "variables": [
        {
          "name": "fuelLevel",
          "type": "int",
          "possibleValues": "FUEL_EMPTY,FUEL_FULL",
          "initialValue": "FUEL_FULL",
          "description": "current fuel level"
        }
      ]
    }
  ]
}
```

### 输出字段表

| 字段 | 类型 | 含义 |
|---|---|---|
| `processes` | 数组 | 按进程组织的状态定义 |
| `processName` | 字符串 | 这些常量变量所属的进程 |
| `constants` | 数组 | CSP# 中预计生成的常量 |
| `constants[].name` | 字符串 | 常量标识符 |
| `constants[].value` | 字符串/整数文本 | 常量的整数编码 |
| `constants[].description` | 字符串 | 常量语义 |
| `variables` | 数组 | CSP# 中预计生成的状态变量 |
| `variables[].name` | 字符串 | 变量标识符 |
| `variables[].type` | 字符串 | `int` 或 `array` |
| `variables[].possibleValues` | 字符串 | 变量有限取值集合 |
| `variables[].initialValue` | 字符串 | 初始状态 |
| `variables[].description` | 字符串 | 变量语义 |

### 实际持久化输出

`get_LLM_answers()` 构造并追加以下记录到 `./history/const-history.json`：

| 字段 | 含义 |
|---|---|
| `timestamp` | LLM 调用完成时间 |
| `question` | 完整 prompt |
| `answerGPT` | o3-mini 返回的原始文本 |
| `context` | 原始 `structuredData` 对象 |

同时更新：

```text
./run_time_record/{modelName}.json
```

键为 `const-var-time`，内容主要是 `runTime`。

### 函数返回值

`gen_const_and_vars()` 没有显式 `return`，因此主程序不能直接取得结果，只能在下一步重新读取 history 文件。

### 异常行为

- LLM 或文件写入异常只会打印错误；`get_LLM_answers()` 隐式返回 `None`。
- history 文件存在但 JSON 损坏时，第 76–79 行没有捕获 `JSONDecodeError`，会进入外层异常并停止本次函数。

### 步骤作用总结

将 subsystem 自然语言第一次结构化为“有限状态由哪些常量和变量组成”。

---

## 5. 步骤 3：主程序从 history 回读 Stage 1 输出

### 代码位置

`pipeline.py` 第 997–1023 行。

### 做了什么

1. 打开 `./history/const-history.json`。
2. 取得数组最后一条记录。
3. 读取最后一条的 `answerGPT`。
4. 尝试 `json.loads(answerGPT)`。
5. 如果 LLM 输出不是合法 JSON，则保留原始字符串，而不是立即终止。
6. history 不存在、损坏、为空或结果为 `None` 时，跳过当前任务，进入下一个输入任务。

### 输入

| 输入 | 来源 | 含义 |
|---|---|---|
| `const-history.json` | 上一步全局 history 文件 | 所有历史常量变量请求与回答 |
| 最后一项 `answerGPT` | history 尾部 | 代码假定它就是当前任务的最新回答 |

### 输出

| 输出变量 | 可能类型 | 含义 |
|---|---|---|
| `latest_const_answer_str` | 字符串 | 原始 LLM 输出 |
| `processed_tables_for_actions` | 字典或字符串 | 下一步动作提取的状态输入 |

### 重要风险

history 是跨任务追加的全局文件，代码只取最后一项，没有任务 ID 校验。如果当前 LLM 调用失败但旧 history 仍有内容，后续可能误用前一个任务的输出。

### 步骤作用总结

用磁盘 history 充当 Stage 1 和 Stage 2 之间的接口。

---

## 6. 步骤 4：Stage 2——调用 Planning LLM 提取动作

### 代码位置

- 主程序调用：第 1024–1026 行。
- `_generate_descriptions_for_actions_helper()`：第 178–207 行。
- `gen_actions()`：第 209–272 行。
- `get_LLM_answers()`：第 36–89 行。

### 做了什么

1. 将 `modelName`、`modelDesc`、`subsystemCount` 和每个 subsystem 的名称/描述拼成系统上下文。
2. 将 Stage 1 输出序列化为 JSON 文本；失败时直接使用原始字符串。
3. 要求 o3-mini 为每个进程提取 action。
4. 每个 action 必须包含发生条件和确定性的状态变化。
5. 保存到 `action-history.json`。
6. 记录 `action-time`。

### 输入

| 输入 | 来源 | 含义 |
|---|---|---|
| `modelName` | 原始任务 | 系统名称 |
| `modelDesc` | 原始任务 | 整体上下文 |
| `subsystemCount` | 原始任务 | 进程数量 |
| `subsystems[].name/description` | 原始任务 | 每个进程的行为描述 |
| `processed_tables_for_actions` | Stage 1 回读结果 | 已知常量、变量、取值和初值 |

`interactionMode` 在 `_generate_descriptions_for_actions_helper()` 中的处理代码已经被注释，因此这一阶段的系统描述不会实际带上组合模式；只有 Stage 1 prompt 和后续断言注释仍会使用它。

### Prompt 对动作输出施加的全部主要规则

1. 为每个进程识别全部可能动作。
2. 为每个动作找出所有相关变量条件，包括跨进程依赖。
3. 输出 `action_name`、`conditions`、`state_changes` 三部分。
4. `conditions` 和 `state_changes` 只能引用已经声明的变量和值，不能用自然语言代替。
5. 所有条件必须放在 `conditions`；不能把条件塞入 `state_changes`。
6. 如果状态变化依赖额外条件，必须将动作拆成多个确定性动作。
7. 复杂条件可使用保留键 `complex_composite_conditions`，但不鼓励。
8. action 名称必须唯一，且只能包含字母、数字和下划线。
9. 无法确定条件或变化时允许输出空对象。
10. 最终响应必须是合法 JSON。

### LLM 目标输出结构

```json
{
  "processes": [
    {
      "processName": "motor",
      "actions": [
        {
          "action_name": "start_engine",
          "conditions": {
            "engineOn": "0",
            "fuelLevel": "FUEL_FULL"
          },
          "state_changes": {
            "engineOn": "1"
          }
        }
      ]
    }
  ]
}
```

### 输出字段表

| 字段 | 类型 | 含义 |
|---|---|---|
| `processes` | 数组 | 按进程组织的动作集合 |
| `processName` | 字符串 | action 所属进程 |
| `actions` | 数组 | 该进程的行为列表 |
| `action_name` | 字符串 | CSP# event 候选名称 |
| `conditions` | 对象 | event 可执行的前置条件，后续对应 guard |
| `conditions.<variable>` | 字符串/数组 | 某变量需要满足的值或值集合 |
| `complex_composite_conditions` | 字符串 | 无法用普通键值表示的组合逻辑 |
| `state_changes` | 对象 | event 发生后变量的新值，后续对应 effect |

### 实际持久化输出

追加到：

```text
./history/action-history.json
```

记录仍是：

```json
{
  "timestamp": "...",
  "question": "完整动作提取 prompt",
  "answerGPT": "LLM 原始输出",
  "context": "Stage 1 输出"
}
```

同时将 `action-time` 写入运行时间文件。

### 函数返回值

`gen_actions()` 没有显式返回动作 JSON。

### 步骤作用总结

在已经确定状态变量的基础上，建立“条件—动作—状态变化”关系。

---

## 7. 步骤 5：源码标注的 Stage 3——回读动作并准备断言

### 代码位置

`pipeline.py` 第 1028–1055 行。

### 做了什么

1. 打开 `./history/action-history.json`。
2. 读取最后一项的 `answerGPT`。
3. 检查常量变量原始回答和动作原始回答是否存在。
4. 从当前任务中取得 `assertions`。
5. 没有 assertions 时只打印警告，不停止。

### 输入

| 输入 | 来源 | 含义 |
|---|---|---|
| `action-history.json` 最后一项 | Stage 2 | 当前动作提取结果；代码没有验证它是否属于当前任务 |
| `latest_const_answer_str` | Stage 1 回读 | 常量变量原始 JSON 文本 |
| `current_structured_data.assertions` | 原始任务 | 用户验证要求和期望结果 |

### 输出

| 输出变量 | 类型 | 含义 |
|---|---|---|
| `latest_action_answer_str` | 字符串 | 动作 LLM 的原始回答 |
| `assertions_list` | 数组 | Stage 4 用于生成断言注释，Stage 6 用于比较期望结果 |

### 停止/跳过条件

- action history 不存在、为空或损坏：跳过当前任务。
- 常量回答或动作回答为空：跳过当前任务。
- assertions 为空：继续运行，但后面生成模型可能没有断言。

### 步骤作用总结

这是磁盘数据回读和完整性检查，不进行新的模型推理。

---

## 8. 步骤 6：Stage 4——再次调用 Planning LLM 生成自然语言建模计划

### 代码位置

- 主程序调用：第 1057–1059 行。
- `gen_nl_instructions()`：第 357–453 行。
- `_process_assertions_for_nl_helper()`：第 274–355 行。

### 先明确这个阶段由谁生成

Stage 4 不是普通的本地格式转换，而是一个“**两次 LLM 生成 + 一次代码拼接**”的混合步骤：

| 执行顺序 | 生成内容 | 实际生成者 | 源码位置 |
|---|---|---|---|
| 1 | 常量和变量的自然语言注释 `data1_content` | OpenAI Planning LLM；通过 `get_LLM_answers(..., 'skip')` 调用 | 第 371–375 行 |
| 2 | subsystem、条件、action 和状态变化的自然语言注释 `data2_content` | OpenAI Planning LLM；通过 `get_LLM_answers(..., 'skip')` 调用 | 第 393–397 行 |
| 3 | 系统组合方式和 assertion 的自然语言注释 `data3_content` | Python 函数 `_process_assertions_for_nl_helper()` 按输入字段拼接 | 第 405–406 行；辅助函数第 274–355 行 |
| 4 | 完整建模计划 `full_prompt` | Python 将上述三段按顺序连接 | 第 408–409 行 |

因此，从整个 Stage 4 来看，它确实属于 Planning LLM 阶段，并且实际连续调用 Planning LLM 两次；只是第三部分的系统组合说明和断言说明没有再交给 LLM 自由生成。

### 8.1 常量变量注释生成

输入：Stage 1 的 `answerGPT`。

处理：

1. 尝试将字符串解析成 JSON；失败时保留原文。
2. 构造 prompt，要求每个常量、变量输出一行注释。
3. 常量注释先于变量。
4. 变量只写初值，不再写可能值集合。
5. 第一次调用 OpenAI Planning LLM：`get_LLM_answers(..., history='skip')`。

`history='skip'` 表示这次 OpenAI 调用不会写入 const/action history，而是直接返回 interaction 对象。

输出变量：

```text
data1_content
```

内容是常量变量的自然语言逐行说明。

### 8.2 动作注释生成

输入：Stage 2 的 `answerGPT`。

处理：

1. 尝试解析动作 JSON；失败时保留原文。
2. 要求每个进程先生成一行定义说明。
3. 每个 action 单独一行，描述条件、动作名和状态变化。
4. `complex_composite_conditions` 的值直接作为条件描述，不显示这个保留键名。
5. 第二次调用 OpenAI Planning LLM：`get_LLM_answers(..., history='skip')`。

输出变量：

```text
data2_content
```

### 8.3 确定性生成总系统和断言注释

输入：原始任务的 `interactionMode` 和 `assertions_list`。

这一部分不调用 LLM。

#### `interactionMode` 分支

| 值 | 生成给 Code Generation LLM 的说明 |
|---|---|
| `interleaving` | 用 `|||` 交错组合所有 subsystem |
| `parallel` | 用 `||` 并行组合 |
| `choice` | 用 `[]` 选择组合，并提示 subsystem 返回总系统 |
| `none` | 不定义统一组合；由进程之间按描述移交控制 |
| `skip` | 不添加组合说明 |
| 其他字符串 | 作为自定义交互描述原样交给 LLM理解 |

#### `assertionType` 分支

| 类型 | 使用的字段 | 生成内容 |
|---|---|---|
| `deadlock-free` | `component` | “assert 某系统或子系统 deadlockfree” |
| `reachability` + `customize` | `stateName`、`customDescription` | 定义自然语言状态，并说明该状态应可达 |
| `reachability` + 非 `customize` | `stateName`、`conditions` | 将条件拼成 `variable = value`，再说明该状态应可达 |
| `ltl` + `customize` | `customDescription` | 原样加入自定义 LTL 自然语言说明 |
| `ltl` + `action` | `ltlLogic`、`selectedActions` | 说明系统以某种 LTL 关系执行目标 actions |
| `ltl` + `state` | `ltlLogic`、`stateName`、`conditions` | 定义状态并说明相应 LTL 要求 |

`component` 为空时，断言目标是 `{modelName} system`；非空时目标是指定 subsystem。

注意：`assertionTruth` 在此处没有加入自然语言计划。它只在 PAT 执行后用于比较实际结果，避免让 Code Generation LLM 把“期望 Invalid”误写成另一条性质。

输出变量：

```text
data3_content
```

### 8.4 合并并保存计划

三部分按顺序拼接：

```text
full_prompt = data1_content + data2_content + data3_content
```

实际写出两个 history 文件。

#### `nl-instruction-part.json`

| 字段 | 含义 |
|---|---|
| `timestamp` | 生成时间 |
| `data1` | 常量变量自然语言注释 |
| `data2` | 动作自然语言注释 |
| `data3` | 总系统与断言自然语言注释 |

#### `nl-instruction-claude.json`

| 字段 | 含义 |
|---|---|
| `timestamp` | 生成时间 |
| `fullText` | 三部分合并后的完整 Code Generation 输入计划 |

同时记录 `nl-annotation-time`。

### 函数返回值

`gen_nl_instructions()` 没有显式返回 `full_prompt`，所以下一步仍需从磁盘 history 回读。

### 步骤作用总结

再次调用 Planning LLM，把常量变量 JSON 和动作 JSON 分别生成为逐行自然语言注释；随后由 Python 补上系统组合与断言说明，并合并成供 Claude 生成 CSP# 的完整文字计划。这是论文 Planning LLM 的最后阶段。

---

## 9. 步骤 7：主程序回读完整建模计划

### 代码位置

`pipeline.py` 第 1061–1084 行。

### 做了什么

1. 打开 `./history/nl-instruction-claude.json`。
2. 读取最后一项。
3. 取得其中的 `fullText`。
4. 如果 history 不存在、为空、损坏或没有 `fullText`，跳过当前任务。

### 输入

| 输入 | 含义 |
|---|---|
| `nl-instruction-claude.json` 最后一项 | 全局累计的自然语言建模计划 |
| `fullText` | 当前代码生成阶段的主体输入 |

### 输出

```text
retrieved_full_nl_prompt
```

类型为字符串。

### 重要风险

与前面一样，代码只读取全局 history 最后一项，没有校验任务身份。

### 步骤作用总结

使用 history 文件把 Stage 4 结果传递到 Stage 5。

---

## 10. 步骤 8：初始化 Stage 5/6 外层代码生成重试循环

### 代码位置

`pipeline.py` 第 1086–1096 行。

### 做了什么

初始化：

```python
gen_count = 0
max_gen_attempts = 3
verified_successfully = False
```

循环条件：

```text
生成次数小于 3
并且
尚未得到能够被 PAT 正常解析的代码
```

### 输入

```text
retrieved_full_nl_prompt
current_structured_data
```

### 输出

此步骤只建立控制变量，不产生文件。

### 变量含义

| 变量 | 含义 |
|---|---|
| `gen_count` | 已调用代码生成 LLM 的次数 |
| `max_gen_attempts` | 最大生成次数，固定为 3 |
| `verified_successfully` | 名称有误；实际表示 PAT 输出格式/语法检查没有出现空结果，不代表所有性质匹配 |

### 步骤作用总结

允许 CSP# 语法或 PAT 输出异常时重新生成代码，而不是立即进入逻辑返修。

---

## 11. 步骤 9：Stage 5——检索示例、组装 prompt 并调用 Claude 生成 CSP#

### 代码位置

- 主程序调用：第 1091–1098 行。
- `gen_code()`：第 521–590 行。
- RAG `_get_most_relevant_rag_example_basic()`：第 455–483 行。
- Claude 调用 `_get_claude_code_completion()`：第 485–519 行。

### 11.1 本地 RAG 检索

输入：

| 输入 | 来源 | 含义 |
|---|---|---|
| `full_nl_prompt` | Stage 4 | 当前任务的完整自然语言建模计划 |
| `database-rag-claude.json` | 本地文件 | 多组历史 `nl`—`code` 示例 |

处理：

1. 读取 RAG 数据库。
2. 收集所有非空 `entry["nl"]`。
3. 使用所有示例 NL 加当前计划拟合 `TfidfVectorizer`。
4. 计算当前计划与每个示例的余弦相似度。
5. 选择分数最高的一条。

输出：

| 字段 | 含义 |
|---|---|
| `retrieved_nl` | 最相似示例的自然语言计划 |
| `retrieved_code` | 该示例对应的 CSP# |

文件不存在、JSON 损坏、输入为空或数据库没有有效 NL 时，返回两个空字符串。

### 11.2 读取 CSP# 语法提示

输入文件：

```text
./syntax-dataset.json
```

输出字段：

| 字段 | 含义 |
|---|---|
| `general_info` | CSP#/PAT 总体说明 |
| `pitfalls_rules` | 分号、常量、数组、进程、组合与断言等语法规则 |

文件不存在或损坏时，两段提示保持空字符串，但代码继续调用 Claude。

### 11.3 重新拼接原始系统描述

调用 `_generate_descriptions_for_actions_helper()`，得到：

```text
系统名 + modelDesc + subsystem 数量 + 每个 subsystem 的名称和描述
```

### 11.4 构造最终 Code Generation prompt

Prompt 的实际组成顺序是：

```text
Claude 的 PAT 专家角色
→ general_info
→ pitfalls_rules
→ RAG 示例 NL
→ RAG 示例 CSP#
→ 原始系统总体描述
→ 当前任务的 full_nl_prompt
→ 要求输出 PAT code
```

### 11.5 调用 Claude

固定参数：

| 参数 | 值/含义 |
|---|---|
| 模型 | `claude-3-7-sonnet-20250219` |
| `max_tokens` | 8192 |
| messages | 只有一条 user message，即完整 prompt |

读取 `response.content[0].text` 作为回答。

### 实际持久化输出

追加到 `./history/claude-code.json`：

| 字段 | 含义 |
|---|---|
| `timestamp` | 调用时间 |
| `question` | 完整 Code Generation prompt |
| `answerClaude` | Claude 原始回答，可能包含 Markdown 围栏和解释 |
| `PAT` | 固定空字符串，当前代码未使用 |

同时记录带时间戳键名的 `codegen-time_*`。

### 函数返回值

`gen_code()` 没有返回生成代码，主程序下一步再次读取 history。

### 重要错误风险

如果当前 Claude 调用失败，`_get_claude_code_completion()` 返回空字符串且不会追加新 history；但 `gen_code()` 不把失败状态返回主程序。主程序随后仍可能读取 `claude-code.json` 的旧最后一项，误把旧任务代码当作当前代码。

### 步骤作用总结

使用语法提示和相似示例增强 Claude，将自然语言计划转换成包含模型和断言的完整 CSP#。

---

## 12. 步骤 10：回读 Claude 输出、提取最长代码块并保存候选 CSP#

### 代码位置

- 回读与保存：第 1100–1143 行。
- `_extract_longest_code_block()`：第 956–981 行。

### 做了什么

1. 打开 `./history/claude-code.json`。
2. 读取最后一项 `answerClaude`。
3. 无内容时退出外层生成循环。
4. 用正则查找三反引号代码块。
5. 有多个代码块时选字符数最多的一个。
6. 没有代码围栏时，把整个回答当作 CSP#。
7. 提取结果为空时进入下一次外层生成尝试。
8. 创建模型输出目录。
9. 写出 Claude 原始回答和提取后的代码。

### 输入

| 输入 | 含义 |
|---|---|
| `claude-code.json` 最后一项 | 最近一次被记录的 Claude 回答 |
| `answerClaude` | 原始文本 |
| `modelName` | 构造模型目录名 |

### 输出

| 输出 | 含义 |
|---|---|
| `retrieved_generated_code` | Claude 原始回答字符串 |
| `longest_code_block` | 准备交给 PAT 的 CSP# 候选字符串 |
| `original_llm_response.txt` | 原始回答文件 |
| `extracted_code.csp` | 提取后的 CSP# 文件 |

### 路径问题

输出目录使用硬编码占位字符串：

```text
path_to_your_project_directory/Automated_Pipelines/Full_Pipeline/generated_code/{modelName}
```

使用前必须人工替换。

### 重要覆盖问题

下一步 `verify_code(..., is_refine=False)` 发现该模型目录已存在时，会删除目录下的所有普通文件。因此刚写出的 `original_llm_response.txt` 和 `extracted_code.csp` 很可能马上被删除。

### 步骤作用总结

从 LLM 的自由文本回答中取出最像完整 CSP# 的部分，作为初始候选模型。

---

## 13. 步骤 11：Stage 6——拆分断言、运行 PAT、解析并比较期望结果

### 代码位置

- 主程序调用：第 1145–1148 行。
- `_split_code_and_assertions()`：第 593–627 行。
- `verify_code()`：第 629–859 行。

### 13.1 将当前代码追加到 refinement history

即使这是初始验证，代码也会向 `./history/claude-refinement.json` 追加：

| 字段 | 含义 |
|---|---|
| `timestamp` | 保存时间 |
| `processedCode` | 本轮准备验证的完整 CSP# |

文件名容易误导：它既保存返修代码，也保存初始代码。

### 13.2 创建或清理验证目录

输入参数：

| 参数 | 类型 | 含义 |
|---|---|---|
| `structured_data` | 对象 | 原始任务，尤其提供模型名、assertions 和 expected outcome |
| `code_to_verify` | 字符串 | 本轮完整 CSP# |
| `is_refine` | 布尔值 | 是否为返修轮；默认 `False` |
| `refine_round` | 整数 | 当前返修轮次；默认 0 |

路径分支：

```text
初始验证：generated_code/{modelName}/
返修验证：generated_code/{modelName}/refine_round_{N}/
```

初始验证时，如果目录已存在，删除其中所有普通文件；返修验证不清理旧文件。

### 13.3 拆分断言

输入：完整 CSP# 字符串。

处理：

1. 正则寻找未被 `//` 注释的连续 `#define` 行和一条 `#assert`。
2. 从主体中删除这些 define/assert 块。
3. 收集所有断言前的 define。
4. 对 define 去重并保持顺序。
5. 为每个断言重新组合“公共主体 + 全部收集的 define + 单条 assert”。

输出：

```text
code_blocks: List[str]
```

每个元素只包含一条 active assertion。

数量必须满足：

```text
len(code_blocks) == len(structured_data['assertions'])
```

否则立即返回：

```python
([], True, True)
```

### 13.4 为每条断言运行 PAT

对第 `i` 条断言写出：

```text
{folder_path}/{i}.csp
{folder_path}/pat_output_{i}.txt
```

命令选择：

| 断言文本 | 命令 |
|---|---|
| 包含 `reaches` 或 `deadlockfree` | `mono PAT3.Console.exe -csp -engine 1 input output` |
| 其他断言（主要为 LTL） | `mono PAT3.Console.exe -csp input output` |

每条断言超时时间固定为 300 秒，且使用 `check=True`。

旧代码无论操作系统都使用 Mono；这也是后来 Windows 版本需要改造的地方。

### 13.5 解析 PAT 输出

输入：`pat_output_{i}.txt`。

处理：

1. 空文件令 `any_empty=True`。
2. 截取以下两个标记之间的文本：

   ```text
   ********Verification Result********
   ********Verification Setting********
   ```

3. 找不到标记时，将结果写成“可能语法错误”并令 `any_empty=True`。
4. 从 CSP# block 中查找第一条 `#assert` 行。
5. 使用正则 `is\s+(\w+)` 解析 PAT 文字。
6. 捕获到 `VALID` 时记为 `Valid`；捕获到其他单词时一律记为 `Invalid`。例如 `is NOT valid` 捕获 `NOT`，因此得到 `Invalid`。

每条断言先生成：

| 字段 | 含义 |
|---|---|
| `assertion` | 本次验证的 `#assert` 文本 |
| `patResult` | PAT Verification Result 区域的原始文本 |
| `actualResult` | 解析后的 `Valid` 或 `Invalid` |

### 13.6 与用户期望结果比较

按照数组位置，将第 `i` 个 PAT 结果与第 `i` 个原始 assertion 配对。

期望结果读取规则：

```text
assertions[i].assertionTruth 非空 → 使用该值
assertionTruth 为空              → 默认 Valid
```

然后补充：

| 字段 | 含义 |
|---|---|
| `desiredOutcome` | 用户期望 PAT 返回的结果 |

比较规则：

```text
actualResult == desiredOutcome → MATCH
actualResult != desiredOutcome → MISMATCH
```

这意味着：

```csp
#assert car reaches drivewithoutfuel;
```

如果 PAT 返回 `Valid`、用户期望 `Invalid`，则是 MISMATCH。PAT 的 `Valid` 只是表示“危险状态确实可达”，并不等于业务正确。

### 13.7 提取 mismatch trace

对每条 mismatch：

1. 将 `patResult` 按行拆分。
2. 找第一条以 `<` 开头且包含 `->` 的行。
3. 找到则作为 trace；找不到则使用 `<init>`。

生成字段：

| 字段 | 含义 |
|---|---|
| `assertion` | 未匹配的断言 |
| `trace` | PAT 给出的执行路径；缺失时为 `<init>` |
| `current_result` | PAT 实际结果 |
| `desired_result` | 用户期望结果 |

### 13.8 保存验证结果

存在 mismatch 时：

```text
{folder_path}/mismatch_traces.json
```

初始轮还会复制一份到：

```text
./history/mismatch_traces.json
```

不存在 mismatch 时：

```text
{folder_path}/verifiedCode.csp
```

并向：

```text
./database-algorithm.json
```

追加：

| 字段 | 含义 |
|---|---|
| `model_name` | 模型名 |
| `verified_code` | 完整 CSP# |

无论是否 mismatch，正常流程都会保存：

```text
{folder_path}/verification_results.json
```

同时用带时间戳的 `verification-time_*` 键记录耗时、`hasMismatch` 和 `codegenFailed`（这里实际传入 `any_empty`）。

### 函数返回值

```python
(verification_results, has_mismatch, any_empty)
```

| 返回值 | 含义 |
|---|---|
| `verification_results` | 每条断言的实际/期望结果列表 |
| `has_mismatch` | 是否至少一条实际结果与期望结果不同 |
| `any_empty` | 是否出现空输出、无法解析、数量不一致或其他被归为语法/执行错误的问题 |

### 异常返回

PAT 超时、非零退出或其他执行异常时直接返回：

```python
([], True, True)
```

### 步骤作用总结

将完整 CSP# 拆成逐断言任务，调用 PAT，并把 PAT 的原始真假判断进一步转换为“是否符合用户期望”。

---

## 14. 步骤 12：外层生成重试判断

### 代码位置

`pipeline.py` 第 1150–1171 行。

### 做了什么

#### `any_empty == False`

```python
verified_successfully = True
```

退出外层生成循环。

注意：即使 `has_mismatch=True`，也会把 `verified_successfully` 设为真。该变量实际表示“PAT 成功解析代码”，不是“全部性质通过”。

#### `any_empty == True`

- 如果还未达到 3 次：重新进入 Stage 5，再调用 Claude 生成整份 CSP#。
- 如果已经达到 3 次：尝试写 `regeneration_errors.json`。

错误信息字段：

| 字段 | 含义 |
|---|---|
| `attempts` | 已生成次数 |
| `last_error_time` | 最后失败时间 |
| `verification_results` | 最后一次可获得的验证结果 |

该错误路径使用 `./generated_code/...`，与其他位置的 `root_path/.../generated_code/...` 不一致，而且写文件前没有确保目录存在，可能再次失败。

### 步骤作用总结

把“PAT/语法无法正常处理”与“逻辑结果不符合期望”分开：前者重新生成整份代码，后者进入 Stage 7 定点返修。

---

## 15. 步骤 13：Stage 7 前置准备

### 代码位置

`pipeline.py` 第 1173–1216 行。

### 进入条件

必须同时满足：

```text
verified_successfully == True
has_mismatch == True
```

也就是 CSP# 能被 PAT 正常处理，但至少一条结果不符合用户期望。

### 做了什么

1. `current_code = longest_code_block`，以初始候选为返修起点。
2. 最大返修轮数设为 5。
3. 初始化 `refine_count=0` 和 `all_mismatches_fixed=False`。
4. 创建模型主目录。
5. 优先读取全局 `./history/mismatch_traces.json`。
6. 如果读取失败，从 `verification_results` 中重新构造 mismatch。
7. fallback 没有真实 trace 时统一使用 `<init>`。
8. 将初始结果保存为 `verification_results_refine_0.json`。

### 输入

| 输入 | 来源 | 含义 |
|---|---|---|
| `longest_code_block` | Stage 5 | 初始 CSP# |
| `mismatch_traces.json` | Stage 6 | 优先使用的失败断言与 trace |
| `verification_results` | Stage 6 | mismatch 文件不可用时的 fallback |
| `modelName` | 原始任务 | 输出目录名 |

### 输出/状态变量

| 变量/文件 | 含义 |
|---|---|
| `current_code` | 当前返修基线 |
| `mismatches` | 当前待修问题列表 |
| `max_refine_attempts=5` | 最大返修轮数 |
| `verification_results_refine_0.json` | 返修前的基线结果 |

### 步骤作用总结

准备 trace 驱动返修循环所需的基线模型和失败集合。

---

## 16. 步骤 14：将 mismatch 转换成返修指导

### 代码位置

`_process_mismatch_traces()` 第 861–908 行，由 `gen_refine()` 第 919–920 行调用。

### 输入字段

每个 mismatch 包含：

| 字段 | 含义 |
|---|---|
| `assertion` | 失败断言 |
| `trace` | 相关 PAT 路径 |
| `current_result` | 当前实际结果 |
| `desired_result` | 目标结果 |

### 三个处理分支

#### 分支 A：断言包含 `deadlockfree`

生成指导：

```text
分析 trace 最终状态是否没有任何 outgoing transition，重点检查 trace 中动作，并确保系统无死锁。
```

#### 分支 B：trace 等于 `<init>`

生成指导：

```text
失败发生在初始化，检查断言相关变量初值、常量定义和可能值。
```

#### 分支 C：普通 trace

1. 按 `->` 拆分 action。
2. 将最后一项视为最高优先级可疑 action。
3. 建议先检查最后 action 的 guard 是否太弱、是否缺少必要条件。
4. 如果最后 action 没问题，再检查此前 action。
5. 多个 mismatch 之间加入额外过渡提示。

### 输出

```text
processed_messages: str
```

它是给 Claude 的自然语言返修指导，不是结构化 patch。

### 已知局限

普通 trace 分支主要假设问题来自“guard 太弱”，更适合“危险状态意外可达”的安全问题；对于期望 `Valid` 但实际 `Invalid` 的可达性/活性问题，往往需要放宽 guard 或补充 transition，这套固定指导可能方向错误。

### 步骤作用总结

把 PAT 的结果和事件序列转换成 LLM 可读的局部修改建议。

---

## 17. 步骤 15：每轮调用 Claude 返修 CSP#，最多进行三次语法尝试

### 代码位置

- 外层返修循环：第 1217–1263 行。
- `gen_refine()`：第 910–954 行。
- 代码块提取：第 956–981 行。

### 外层循环条件

```text
refine_count < 5
并且尚未全部修复
并且 mismatches 非空
```

每一轮内部还有：

```python
for i in range(3)
```

用于给返修 LLM 最多三次产生可被 PAT 处理的代码的机会。

### `gen_refine()` 输入

| 输入 | 含义 |
|---|---|
| `structured_data` | 原始任务；主要使用 `modelName` |
| `current_code` | 上一轮被接受的完整 CSP# |
| `mismatches` | 当前失败断言、trace 和期望差异 |
| `refine_round` | 当前返修轮次 |

### 返修 prompt 组成

```text
PAT 专家角色
→ 上一轮完整 CSP#
→ processed_messages 返修指导
→ 只修改相关代码
→ 其他代码不要改变
→ 特别不要删除分号
→ 返回修订后的 PAT code
```

旧 prompt 没有明确要求保持原 `#assert` 完全不变，因此 LLM 可能通过修改断言“修复”结果。这是后来新版 pipeline 增加断言保护的原因。

### Claude 调用与输出

仍调用 `_get_claude_code_completion()`，固定模型和 8192 token。

但传入的 history 路径仍是：

```text
./history/claude-code.json
```

不是 `claude-refinement.json`。

`gen_refine()` 返回 Claude 原始文本，并另外保存：

```text
refined_code_{round}.csp
```

注意该文件可能仍包含 Markdown 围栏，因为保存发生在代码块提取之前。

### 提取并保存返修候选

主程序从原始回答提取最长代码块，写出：

| 文件 | 含义 |
|---|---|
| `original_refined_{round}.txt` | Claude 原始返修回答 |
| `extracted_refined_{round}.csp` | 提取后的 CSP# 候选 |

### 代码缺陷

提取后第 1230 行检查的是旧变量：

```python
if longest_code_block == "":
```

而不是当前轮的：

```python
longest_refined_block
```

因此当前返修代码块为空时可能没有被正确拦截。

### 返修候选验证

调用：

```python
verify_code(
    current_structured_data,
    longest_refined_block,
    is_refine=True,
    refine_round=refine_count
)
```

输出进入：

```text
generated_code/{modelName}/refine_round_{N}/
```

如果 `refine_any_empty=True`，内层循环再次请求 Claude；否则接受候选：

```python
current_code = longest_refined_block
```

### 步骤作用总结

根据 trace 局部修改当前 CSP#，并要求 PAT 至少能够正常解析；每个逻辑返修轮最多有三次语法候选机会。

---

## 18. 步骤 16：判断返修成功、更新 mismatch 或输出最终失败结果

### 代码位置

`pipeline.py` 第 1264–1344 行。

### 分支 A：`refine_has_mismatch == False`

1. 设置 `all_mismatches_fixed=True`。
2. 将 `current_code` 写为模型主目录下的 `verifiedCode.csp`。
3. 将 `{model_name, verified_code}` 追加到 `database-algorithm.json`。
4. 退出返修循环。

`verify_code()` 在无 mismatch 时已经在 `refine_round_N` 目录保存过 `verifiedCode.csp` 并追加过数据库；主循环又保存和追加一次，因此可能产生重复数据库记录。

### 分支 B：仍有 mismatch

优先读取：

```text
refine_round_{N}/mismatch_traces.json
```

作为下一轮输入。

读取失败时，从本轮 `refine_verification_results` 重建 mismatch，并再次尝试从 `patResult` 找 trace；找不到则使用 `<init>`。

然后开始下一逻辑返修轮。

### 分支 C：达到五轮仍未完成，或 mismatch 数据中断

保存：

```text
final_refined_code.csp
refinement_summary.json
```

`refinement_summary.json` 字段：

| 字段 | 含义 |
|---|---|
| `rounds` | 实际返修轮数 |
| `all_fixed` | 是否全部匹配期望 |
| `remaining_mismatches` | 剩余 mismatch 数量 |
| `completion_time` | 完成时间 |

### 不进入 Stage 7 的情况

| 条件 | 结果 |
|---|---|
| 初始 PAT 正常且无 mismatch | `verify_code()` 已直接保存 `verifiedCode.csp` |
| 三次生成都存在语法/PAT 输出问题 | 不进入逻辑返修 |
| 前面任一 history 回读失败 | 当前任务直接跳过 |

### 步骤作用总结

在“成功模型”“继续返修”“达到上限仍失败”三个结果之间作最终分流。

---

## 19. 完整真实调用顺序

```text
导入模块、读取 Key、创建两个 LLM 客户端
→ 读取 ./PAT.json
→ 断言任务数量必须等于 6
→ 对每个任务：
   → gen_const_and_vars
      → OpenAI/o3-mini
      → const-history.json
      → runtime
   → 从 const-history 读取最后一项
   → gen_actions
      → OpenAI/o3-mini
      → action-history.json
      → runtime
   → 从 action-history 读取最后一项
   → 取得 assertions
   → gen_nl_instructions
      → OpenAI 生成常量变量注释
      → OpenAI 生成动作注释
      → Python 生成组合/断言注释
      → nl-instruction-part.json
      → nl-instruction-claude.json
   → 从 nl-instruction-claude 读取最后一个 fullText
   → 最多三次初始代码生成：
      → TF-IDF RAG 检索
      → 读取 syntax-dataset
      → Claude 生成 CSP#
      → claude-code.json
      → 提取最长代码块
      → verify_code
         → 拆成单断言 CSP#
         → 每条调用 PAT
         → 解析 Valid/Invalid
         → 按位置读取 assertionTruth
         → 生成 MATCH/MISMATCH
         → 保存 verification/mismatch/verifiedCode
      → PAT/语法输出异常：重新生成
      → PAT 正常：退出生成重试
   → 如果存在 mismatch：
      → 读取 mismatch trace
      → 最多五轮逻辑返修：
         → trace 转自然语言建议
         → Claude 返修完整 CSP#
         → 每轮最多三次语法候选
         → PAT 重新验证
         → 无 mismatch：保存 verifiedCode
         → 有 mismatch：更新 trace，下一轮
      → 五轮后仍失败：保存 final_refined_code 和 summary
→ 处理下一个输入任务
```

## 20. 最终输入输出文件总表

### 外部输入

| 文件/变量 | 作用 |
|---|---|
| `OPENAI_API_KEY` | Planning LLM 凭据 |
| `CLAUDE_API_KEY` | 代码生成/返修 LLM 凭据 |
| `PAT.json` | 系统描述、subsystems、assertions、expected outcomes |
| `syntax-dataset.json` | CSP# 语法提示 |
| `database-rag-claude.json` | NL—CSP# 示例库 |
| `PAT3.Console.exe` | 模型检查器 |

### 中间输出/下一步输入

| 文件 | 生产者 | 消费者 |
|---|---|---|
| `const-history.json` | Stage 1 | Stage 2、Stage 4 |
| `action-history.json` | Stage 2 | Stage 3、Stage 4 |
| `nl-instruction-part.json` | Stage 4 | 审计/调试，不被后续主流程读取 |
| `nl-instruction-claude.json` | Stage 4 | Stage 5 |
| `claude-code.json` | Stage 5/Stage 7 | 主程序代码块提取 |
| `claude-refinement.json` | `verify_code()` | 仅记录，不参与主控制流 |
| 单断言 `i.csp` | Stage 6 | PAT |
| `pat_output_i.txt` | PAT | Stage 6 解析器 |
| `mismatch_traces.json` | Stage 6 | Stage 7 |

### 最终或阶段性输出

| 文件 | 含义 |
|---|---|
| `verification_results.json` | 每条断言的实际结果和期望结果 |
| `verifiedCode.csp` | 全部实际结果与期望结果匹配的 CSP# |
| `final_refined_code.csp` | 达到上限仍未全部匹配时的最后代码 |
| `refinement_summary.json` | 失败返修概要 |
| `database-algorithm.json` | 历史 verified model 库 |
| `run_time_record/{model}.json` | 各阶段运行时间和验证标记 |

## 21. 用于后续裁剪的直接结论

严格按代码追踪后，可以确定：

1. Controller IR 若能直接提供有限状态、初值、action guard 和 effect，可替换步骤 2–8，即常量变量提取、动作提取、两次 NL 注释生成及其 history 回读。
2. 确定性 IR→CSP# 转换器可以替换步骤 9–12 中的 RAG、语法 prompt、Claude 初始生成和随机语法重试。
3. PAT 验证、实际结果与 `assertionTruth` 比较、mismatch trace 提取必须保留。
4. trace 返修可以保留，但应修正“总是假设 guard 太弱”的单向启发式，并保护原断言不被修改。
5. 全局追加 history + 读取最后一项的接口应删除，改为一次 run 一个独立目录并显式传递返回值。
6. 要想完整替换 Planning LLM，Controller IR 输入必须显式提供当前缺失的 effect 或等价状态迁移信息，否则转换器仍需猜测动作执行后的状态。
