# PAT-Agent：生成 CSP# 前各 Stage 的输入输出示例

## 阅读范围与示例约定

本文按旧版 [`Full_Pipeline/pipeline.py`](../archive/Automated_Pipelines/Full_Pipeline/pipeline.py) 的实际代码顺序，讲到首次生成并提取 CSP# 为止。**不包含 PAT 验证、生成重试后的验证逻辑和 trace 返修。不是当前 `src/pipeline.py` 的说明。**

每个 Stage 使用同一种格式：执行者 → 简述作用 → 输入 → 输入的解释 → 输出 → 输出的解释 → 细节补充。源码中的 Stage 4、Stage 5 各有多个内部操作，本文用 `.1`、`.2` 等区分它们，不把这些小编号冒充源码原有注释。

全文用同一个 `motor_demo` 示例贯穿。它参考旧数据集中 `keyless_car` 的 `motor` 子系统，但为了展示字段，补充了明确的初值、动作条件和状态变化，燃油也简化为“空/满”两种状态。**不是旧数据集原文，也不是已经调用 LLM 得到的真实回复。**

常量变量 JSON、动作 JSON、自然语言回复和 CSP# 都是说明性输出，展示一种合理的结果。文中的完整 prompt 则是将这些示例数据代入旧源码后得到的文本；其中 Claude 的示例检索使用归档中的实际 RAG 库。没有实际发起模型 API 请求，也没有宣称示例 CSP# 已经通过 PAT。

先记住三个边界：

- Planning-Agent 在这份自动脚本中落实为对 Planning LLM 的调用；四次调用使用同一个 `get_LLM_answers()`，没有单独的“断言生成”调用。
- `assertions` 由输入 JSON 预先提供。Stage 4 的代码只生成它们的文字说明；实际 `#assert` 由 Claude 写入 CSP#。
- 存在文件里的 `context` 是日志字段，不会自动变成模型上下文。真正发送给模型的是 `messages` 中的 `content`。

### 全流程索引

| 顺序 | 本文编号 | 执行者 | 什么变成什么 |
|---|---|---|---|
| 1 | Stage 0 | code | 环境变量、输入文件 → 客户端和当前任务对象 |
| 2 | Stage 1 | LLM / Planning-Agent，第 1 次调用 | 子系统描述 → 常量变量 JSON 字符串 |
| 3 | Stage 1.后处理 | code | history 中的 JSON 字符串 → 可用于动作提取的对象 |
| 4 | Stage 2 | LLM / Planning-Agent，第 2 次调用 | 系统描述 + 常量变量 JSON → 动作 JSON 字符串 |
| 5 | Stage 3 | code | 动作 history + 原输入的 assertions → 后续步骤的输入 |
| 6 | Stage 4.1 | LLM / Planning-Agent，第 3 次调用 | 常量变量 JSON → 常量变量自然语言注释 |
| 7 | Stage 4.2 | LLM / Planning-Agent，第 4 次调用 | 动作 JSON → 动作自然语言注释 |
| 8 | Stage 4.3 | code | interactionMode + assertions → 系统、状态谓词、断言的文字说明 |
| 9 | Stage 4.4 | code | 三段文字 → 完整建模计划，并保存 |
| 10 | Stage 4.后处理 | code | 计划 history → 当前计划字符串 |
| 11 | Stage 5.1 | code | 当前计划 + RAG 库 → 一组参考注释和代码 |
| 12 | Stage 5.2 | code | 语法文件 → 通用说明和语法规则字符串 |
| 13 | Stage 5.3 | code | 示例、语法规则、原始描述、当前计划 → Claude prompt |
| 14 | Stage 5.4 | LLM / Claude，第 5 次调用 | Claude prompt → 含 CSP# 的回复文本 |
| 15 | Stage 5.后处理 | code | 回复文本 → 最长代码块 → `.csp` 文件 |

---

## Stage 0｜code｜加载配置与输入

### 简述作用

读取两个模型服务的 API key，创建客户端；读取 `PAT.json`，从任务数组中取出当前任务。此时没有调用 LLM。

源码位置：`pipeline.py` 第 15–22 行、第 983–990 行。

### 输入

环境变量：`OPENAI_API_KEY`、`CLAUDE_API_KEY`。这里只说明变量名，不展示密钥。

本例的任务文件内容如下。**仅展示一个任务的结构；旧源码第 987 行要求数组长度恰好为 6，这个单任务文件不能原样通过该检查。**

<!-- example:INPUT -->
```json
[
  {
    "index": 1,
    "modelName": "motor_demo",
    "modelDesc": "A simplified engine-only car system with binary fuel.",
    "interactionMode": "interleaving",
    "subsystemCount": 1,
    "subsystems": [
      {
        "name": "motor",
        "description": "This process models an engine-only car. The engine has states ENGINE_OFF and ENGINE_ON; the car has states CAR_STOPPED and CAR_MOVING; fuel has states FUEL_EMPTY and FUEL_FULL. Initially the engine is off, the car is stopped, and fuel is full. start_engine requires the engine to be off and fuel to be full, and turns the engine on. stop_engine requires the engine to be on and the car to be stopped, and turns the engine off. start_driving requires the engine to be on, the car to be stopped, and fuel to be full, and makes the car move. stop_driving requires the car to be moving and makes it stop. consume_fuel requires the car to be moving and fuel to be full; it makes fuel empty and stops the car in the same action. refuel requires the car to be stopped and fuel to be empty, and makes fuel full."
      }
    ],
    "assertions": [
      {
        "assertionType": "deadlock-free",
        "component": "",
        "assertionTruth": "Valid"
      },
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

### 输入的解释

| 字段或位置 | 类型 | 本例含义 | 在生成 CSP# 前如何使用 |
|---|---|---|---|
| 最外层 `[...]` | 数组 | 要处理的模型任务列表 | 主程序逐个取出对象；不是一个模型内部的进程列表 |
| `index` | 整数 | 示例任务编号 1 | 这段主流程没有用它控制生成，循环另有下标 `i` |
| `modelName` | 字符串 | 模型名字 `motor_demo` | 拼接系统描述、系统说明、输出目录和计时文件名 |
| `modelDesc` | 字符串 | 简化的发动机系统，不是整车系统 | 进入动作提取与 Claude 的原始系统描述 |
| `interactionMode` | 字符串 | `interleaving`，子系统交错组合 | 进入常量变量提取提示词；Stage 4 代码生成 `|||` 的组合说明 |
| `subsystemCount` | 整数 | 只有 1 个子系统 | Stage 1 直接取值；也进入系统描述。代码不会自动校验它是否等于列表长度 |
| `subsystems` | 对象数组 | 子系统列表 | 提供进程名字和行为描述 |
| `subsystems[].name` | 字符串 | 子系统名 `motor` | 期望成为后续 JSON 中的 `processName`，以及 CSP# 进程名 |
| `subsystems[].description` | 字符串 | 上述状态、初值和六种动作的描述 | 供 LLM 分析数据与行为，不是可执行代码 |
| `assertions` | 对象数组 | 两条预先指定的检查问题 | Stage 3 读取，Stage 4 代码转换成文字说明 |
| `assertions[].assertionType` | 字符串 | `deadlock-free` 或 `reachability` | 决定生成哪类检查说明 |
| `assertions[].component` | 字符串 | 空字符串 | 断言针对整个 `motor_demo` 系统；非空时针对指定子系统 |
| `assertions[].stateName` | 字符串 | `drivewithoutfuel` | 状态谓词的名字；本例仅可达性条目需要它 |
| `assertions[].reachabilityType` | 字符串 | `customize` | 表示从 `customDescription` 取得目标状态描述，而不是从 `conditions` 拼接 |
| `assertions[].customDescription` | 字符串 | 没油仍在行驶 | 描述目标状态谓词的含义，不是说它必须出现 |
| `assertions[].assertionTruth` | 字符串 | 无死锁期望 `Valid`；坏状态可达性期望 `Invalid` | 后续 PAT 验证后比较使用；本段不会把它作为期望答案发送给生成模型 |

### 输出

`current_structured_data`：上面数组里的第一个对象。后续 LLM 的业务输入都是从这个对象或它的生成结果取得。

### 输出的解释

它仍是原始任务对象，没有新增常量、变量或动作。

### 细节补充

`PAT.json`、`history/...`、`syntax-dataset.json` 等路径均相对于**进程工作目录**，不是自动相对于脚本目录。旧源码的两个客户端分别使用 OpenAI SDK 与 Anthropic SDK，不能将它们误认为当前项目 `llm/` 的中转站配置逻辑。

---

## Stage 1｜LLM / Planning-Agent｜第 1 次调用：提取常量和变量

### 简述作用

把子系统的行为描述变成结构化的数据定义：有哪些固定值、有哪些会变化的变量、变量初始是什么。

源码位置：`gen_const_and_vars()` 第 118–176 行；LLM 调用第 173 行；主程序调用第 995 行。

### 输入

业务输入：Stage 0 中的 `current_structured_data`，即完整任务对象。

但代码构造 prompt 时，真正选取的只有：`subsystemCount`、`subsystems[].name`、`subsystems[].description` 和 `interactionMode`。**`modelDesc` 和 `assertions` 不会因为在对象里就自动进入此次 prompt。**

下方是本例代入源码后构造的完整 `protmpt_gen_const_and_vars`。其中 MAX_USERS、userCount、positions 是作者提示词内置的格式示例，并非要求本例也建这些变量。

<!-- rendered:STAGE1_PROMPT -->
````text
As an expert in information extraction and analysis in computer science domain, can you extract all the constants and variables involved in each of the 1 processes described?
process 1: process name: motor, process description: This process models an engine-only car. The engine has states ENGINE_OFF and ENGINE_ON; the car has states CAR_STOPPED and CAR_MOVING; fuel has states FUEL_EMPTY and FUEL_FULL. Initially the engine is off, the car is stopped, and fuel is full. start_engine requires the engine to be off and fuel to be full, and turns the engine on. stop_engine requires the engine to be on and the car to be stopped, and turns the engine off. start_driving requires the engine to be on, the car to be stopped, and fuel to be full, and makes the car move. stop_driving requires the car to be moving and makes it stop. consume_fuel requires the car to be moving and fuel to be full; it makes fuel empty and stops the car in the same action. refuel requires the car to be stopped and fuel to be empty, and makes fuel full.
Please structure your analyzed results in the following JSON format:
{
    "processes": [
    {
        "processName": "Process 1",
        "constants": [
        {
            "name": "MAX_USERS",
            "value": "2",
            "description": "Maximum number of users allowed"
        }
        ],
        "variables": [
        {
            "name": "userCount",
            "type": "int",
            "possibleValues": "0 to MAX_USERS",
            "initialValue": "MAX_USERS",
            "description": "Current number of active users"
        },
        {
            "name": "positions",
            "type": "array",
            "possibleValues": "NEAR,FAR,IN",
            "initialValue": "[FAR,FAR]",
            "description": "Positions of the two users"
        }
        ]
    }
    ]
}

Requirements for the content:
1. For constants, they have 3 properties: name, value (should be an integer), and description. For variables, they have 4 properties: name, type (integer, array, etc.), possible values (**please ensure the possible values are defined as constants if necessary**), and description.
2. Constants should be defined before variables. The names of variables and constants (including the possible values of variables) must be unique across all processes and must not conflict with any process name.
3. The interaction mode for the processes is interleaving. If the mode is "none" or "choice", note that constants and variables are likely to overlap significantly across processes. In such cases, ensure that shared constants and variables are defined only once - under the first process - and not duplicated in each individual process.
4. Initial values should be specified based on commonsense or process description.
5. Types of variables should be either int or array. If a variable is an array, it is **MANDATORY** to specify its initial value as a list (i.e., the initialValue field cannot be left empty). If no initial value can be reasonably assigned, it indicates that the **possible values are incomplete** and should include additional entries (e.g., it may be necessary to define a value like "disk_empty").
6. The possible values of a variable should be listed out as concrete values separated by "," without "[]". For example: "ENGINE_OFF, ENGINE_ON". It **should not involve any natural language description** (i.e., cannot be something like 0 to MAX_CROSSING_TIME), each possible value **MUST be either a defined constant or a number**.
7. Descriptions should be clear and concise.
8. For processName, please follow exactly the processName entered by the user without any additional natural language description.
9. Variables whose possible values represent actions, transitions, or system control (e.g., moveSelection, activeControl) rather than data states **should not** be included as variables. These are part of the system behavior and will be handled separately during action extraction.

Please ensure your response is a valid JSON string that can be parsed directly.
````

等价的调用关系与真正的 API 参数是：

```python
get_LLM_answers(
    question=protmpt_gen_const_and_vars,
    context=current_structured_data,
    history="const"
)

# get_LLM_answers() 内真正发送给模型的内容
client.chat.completions.create(
    model="o3-mini-2025-01-31",
    reasoning_effort="high",
    messages=[{"role": "user", "content": protmpt_gen_const_and_vars}]
)
```

### 输入的解释

| 输入字段或片段 | 含义 | 是否真正发给模型 |
|---|---|---|
| `subsystemCount` | 本例要分析 1 个进程 | 是，拼入 prompt |
| 子系统名字及描述 | `motor` 的状态、初值、动作描述 | 是，拼入 prompt |
| `interactionMode` | 共享数据和重复定义等提示所依据的组合模式 | 是，拼入 prompt |
| prompt 中的 JSON 格式示例 | 告诉 LLM 返回 `processes/constants/variables` 等键 | 是；是格式要求，不是当前模型的数据 |
| prompt 中的 Requirements | 命名唯一、初值、变量类型、可能值等要求 | 是 |
| `question` | 包装函数收到的完整 prompt 字符串 | 是，作为 `messages[0].content` |
| `context` | 保存本次业务输入以便记录日志 | **不单独发送**；本次它是完整原始任务对象 |
| `history="const"` | 选择常量变量 history 文件 | 否，Python 内部控制字段 |
| `model` | 旧源码固定的 Planning 模型 ID | 是，API 参数；不是输入 JSON 的模型名称 |
| `reasoning_effort` | 旧源码设为 `high` 的推理强度参数 | 是，API 参数 |
| `messages` | 本次模型收到的消息列表 | 是；只有一条 user 消息 |
| `messages[].role` | `user` 表示这条是用户消息 | 是 |
| `messages[].content` | 上面展示的完整 prompt | 是；之前保存的 history 不会自动附在此处 |

### 输出

说明性 LLM 回复正文如下。本例所有状态都用整数编码，因此变量类型全部为 `int`。

<!-- example:CONSTS -->
```json
{
  "processes": [
    {
      "processName": "motor",
      "constants": [
        {"name": "ENGINE_OFF", "value": 0, "description": "The engine is off"},
        {"name": "ENGINE_ON", "value": 1, "description": "The engine is on"},
        {"name": "CAR_STOPPED", "value": 0, "description": "The car is stopped"},
        {"name": "CAR_MOVING", "value": 1, "description": "The car is moving"},
        {"name": "FUEL_EMPTY", "value": 0, "description": "The fuel is empty"},
        {"name": "FUEL_FULL", "value": 1, "description": "The fuel is full"}
      ],
      "variables": [
        {
          "name": "engine",
          "type": "int",
          "possibleValues": "ENGINE_OFF, ENGINE_ON",
          "initialValue": "ENGINE_OFF",
          "description": "Current engine state"
        },
        {
          "name": "car",
          "type": "int",
          "possibleValues": "CAR_STOPPED, CAR_MOVING",
          "initialValue": "CAR_STOPPED",
          "description": "Current movement state of the car"
        },
        {
          "name": "fuel",
          "type": "int",
          "possibleValues": "FUEL_EMPTY, FUEL_FULL",
          "initialValue": "FUEL_FULL",
          "description": "Current binary fuel state"
        }
      ]
    }
  ]
}
```

### 输出的解释

| 字段 | 类型 | 含义 | 本例说明 |
|---|---|---|---|
| `processes` | 对象数组 | 各进程提取到的数据定义 | 本例只有 `motor` |
| `processes[].processName` | 字符串 | 数据所属的进程名字 | 应与输入的子系统名保持一致 |
| `processes[].constants` | 对象数组 | 不随执行改变的命名值 | 六个状态编码 |
| `constants[].name` | 字符串 | 常量名字 | `ENGINE_OFF` 等，不是会变化的变量 |
| `constants[].value` | 整数 | 常量对应的固定编码 | 本例为 0 或 1；不同名字可以使用相同数值 |
| `constants[].description` | 字符串 | 常量在业务中的含义 | `ENGINE_OFF` 表示发动机关闭 |
| `processes[].variables` | 对象数组 | 会随动作改变的数据 | `engine`、`car`、`fuel` |
| `variables[].name` | 字符串 | 变量名 | 后面的动作 JSON 必须沿用这些名字 |
| `variables[].type` | 字符串 | 数据形式 | 本例为 `int`；prompt 也允许 `array` |
| `variables[].possibleValues` | 字符串 | 允许值的文字列表 | 本例是以逗号分隔的常量名字，不是 JSON 数组，也不是 PAT 已强制执行的范围 |
| `variables[].initialValue` | 本例为字符串；数组变量可为列表 | 模型开始时的值 | 本例是常量名字，如 `ENGINE_OFF`；此时还未转换成 CSP# 表达式 |
| `variables[].description` | 字符串 | 变量记录的业务含义 | `car` 记录车辆是否移动 |

`get_LLM_answers()` 取得的是回复**字符串**，再构造以下日志对象。这里用 Python 变量引用上方完整内容，避免把完整 prompt 和回复再转义重复一遍；不是说文件内会存放这些 Python 变量名。

```python
interaction = {
    "timestamp": "2026-09-13 10:00:00",  # 示意时间
    "question": protmpt_gen_const_and_vars,
    "answerGPT": answer,                 # 上方完整 JSON 的字符串形式
    "context": current_structured_data
}
# 追加到 history 列表，再 json.dump() 到 ./history/const-history.json
```

| 日志字段 | 类型 | 含义 |
|---|---|---|
| `timestamp` | 字符串 | 本次调用的记录时间 |
| `question` | 字符串 | 实际发送的完整 prompt |
| `answerGPT` | 字符串 | 模型回复正文；本例正文内容可以解析成上述 JSON 对象 |
| `context` | 对象 | 本次提取的原始任务对象，包括没有发送给模型的其他字段 |

### 细节补充

- `gen_const_and_vars()` 本身没有显式返回结果；包装函数的返回值在这里没有被接收，后续从 history 文件取得结果。
- 提示词要求有效 JSON，但包装函数不会检验回复是否符合这个 schema。下一步最多尝试解析 JSON，不会系统检查变量名、类型、常量重复等约束。
- 本例将常量 `value` 写成整数，符合文字要求。源码内置格式示例却写了 `"value": "2"`，这是原 prompt 的不一致，不应误认成业务上必须使用字符串。
- `possibleValues` 当前只是规划数据；是否在 CSP# 声明中限制变量范围，要看 Claude 最后怎样写代码。
- 计时写入 `./run_time_record/motor_demo.json` 的 `const-var-time.runTime`，不会进入模型回复。

---

## Stage 1.后处理｜code｜回读常量变量结果

### 简述作用

读取 `const-history.json` 的最后一条 `answerGPT`，尝试 `json.loads()`：把 JSON 字符串变成 Python 对象，给下一次动作提取使用。

源码位置：第 997–1022 行。

### 输入

Stage 1 写入的 `./history/const-history.json`。

### 输入的解释

取 `const_history[-1]["answerGPT"]`，不是取整个 history 数组，也不是按 `index` 查找记录。

### 输出

`latest_const_answer_str` 保留原回复字符串；`processed_tables_for_actions` 是解析后的常量变量对象，即上面的 `processes/constants/variables` JSON。

### 输出的解释

前者供后续生成自然语言注释；后者供 Stage 2 拼接动作提取 prompt。

### 细节补充

正文无法解析为 JSON 时，代码保留原字符串继续使用。history 缺失、为空或 history 文件自身不是合法 JSON 时，跳过当前任务。它默认最后一条记录就是当前任务的结果，没有做任务标识匹配。

---

## Stage 2｜LLM / Planning-Agent｜第 2 次调用：提取动作

### 简述作用

把“系统行为描述 + 已提取的数据定义”变成动作 JSON，明确每个动作的名字、发生条件以及对变量的修改。

源码位置：`gen_actions()` 第 209–272 行；描述拼接函数第 178–207 行；LLM 调用第 267 行；主程序调用第 1026 行。

### 输入

函数输入有两项：

1. `structured_data`：Stage 0 的原始任务对象。
2. `processed_tables`：Stage 1 回读后解析的常量变量对象，完整示例如下。

<!-- rendered:STAGE2_INPUT -->
```json
{
  "processes": [
    {
      "processName": "motor",
      "constants": [
        {
          "name": "ENGINE_OFF",
          "value": 0,
          "description": "The engine is off"
        },
        {
          "name": "ENGINE_ON",
          "value": 1,
          "description": "The engine is on"
        },
        {
          "name": "CAR_STOPPED",
          "value": 0,
          "description": "The car is stopped"
        },
        {
          "name": "CAR_MOVING",
          "value": 1,
          "description": "The car is moving"
        },
        {
          "name": "FUEL_EMPTY",
          "value": 0,
          "description": "The fuel is empty"
        },
        {
          "name": "FUEL_FULL",
          "value": 1,
          "description": "The fuel is full"
        }
      ],
      "variables": [
        {
          "name": "engine",
          "type": "int",
          "possibleValues": "ENGINE_OFF, ENGINE_ON",
          "initialValue": "ENGINE_OFF",
          "description": "Current engine state"
        },
        {
          "name": "car",
          "type": "int",
          "possibleValues": "CAR_STOPPED, CAR_MOVING",
          "initialValue": "CAR_STOPPED",
          "description": "Current movement state of the car"
        },
        {
          "name": "fuel",
          "type": "int",
          "possibleValues": "FUEL_EMPTY, FUEL_FULL",
          "initialValue": "FUEL_FULL",
          "description": "Current binary fuel state"
        }
      ]
    }
  ]
}
```

代码把第一项中的 `modelName`、`modelDesc`、`subsystemCount` 和子系统名字、描述拼成 `descriptions_str`；把第二项转成 `table_info` 字符串。两者一起进入完整 prompt：

<!-- rendered:STAGE2_PROMPT -->
````text
As an expert in reasoning and action extraction, analyze the following system: The user would like to build a system called motor_demo, where A simplified engine-only car system with binary fuel.. There are 1 processes in the system, the descriptions of the processes are as follows: motor: This process models an engine-only car. The engine has states ENGINE_OFF and ENGINE_ON; the car has states CAR_STOPPED and CAR_MOVING; fuel has states FUEL_EMPTY and FUEL_FULL. Initially the engine is off, the car is stopped, and fuel is full. start_engine requires the engine to be off and fuel to be full, and turns the engine on. stop_engine requires the engine to be on and the car to be stopped, and turns the engine off. start_driving requires the engine to be on, the car to be stopped, and fuel to be full, and makes the car move. stop_driving requires the car to be moving and makes it stop. consume_fuel requires the car to be moving and fuel to be full; it makes fuel empty and stops the car in the same action. refuel requires the car to be stopped and fuel to be empty, and makes fuel full.
Given the list of system variables and their possible values in JSON format:
{"processes": [{"processName": "motor", "constants": [{"name": "ENGINE_OFF", "value": 0, "description": "The engine is off"}, {"name": "ENGINE_ON", "value": 1, "description": "The engine is on"}, {"name": "CAR_STOPPED", "value": 0, "description": "The car is stopped"}, {"name": "CAR_MOVING", "value": 1, "description": "The car is moving"}, {"name": "FUEL_EMPTY", "value": 0, "description": "The fuel is empty"}, {"name": "FUEL_FULL", "value": 1, "description": "The fuel is full"}], "variables": [{"name": "engine", "type": "int", "possibleValues": "ENGINE_OFF, ENGINE_ON", "initialValue": "ENGINE_OFF", "description": "Current engine state"}, {"name": "car", "type": "int", "possibleValues": "CAR_STOPPED, CAR_MOVING", "initialValue": "CAR_STOPPED", "description": "Current movement state of the car"}, {"name": "fuel", "type": "int", "possibleValues": "FUEL_EMPTY, FUEL_FULL", "initialValue": "FUEL_FULL", "description": "Current binary fuel state"}]}]}
Follow these steps:
1. **Identify all possible actions for each process**
- Ensure actions are realistic and align with the system behavior.

2. **Determine the conditions for each action**
- Under what conditions should an action occur?
- What values must the system variables have (including variables from other processes) for the action to be valid?
- **Ensure each condition accounts for dependencies between processes**.

3. Organize the analyzed results into a **JSON format**, each action should have **3 properties**: "action_name", "conditions" (exhaustive list of related variables and values they should assume), and "state_changes" associated with the action (affected variables and their respective new value). For both conditions and state changes, please list the variables with their expected value(s) (the values can be defined constants, numeric values, or expressions combining variables with constants or numbers), instead of using any natural language description. An example JSON is as follows:
{
    "processes": [
    {
        "processName": "Process 1",
        "actions": [
        {
            "action_name": "open",
            "conditions": {
            "owner.i": "near",
            "door": ["closed", "locked"],
            "complex_composite_conditions": "((key == with_owner_i and owner.i == in) or (key == in))"
            },
            "state_changes": {
            "door": "open"
            }
        }
        ]
    }
    ]
}

4. All conditions must be exhaustively listed under the "conditions" field. There should **NEVER** be any conditions inside "state_changes". If a state change depends on an additional condition, the action **must be split** into multiple more specific actions, each with deterministic conditions and unconditional state changes.

5. In "conditions", the reserved keyword "complex_composite_conditions" is used to represent complex composite conditions. This is the **only** case where a key may be something other than a variable name. However, using "complex_composite_conditions" is **strongly discouraged** and should **ONLY BE USED WHEN THERE IS ABSOLUTELY NO STANDARD WAY TO EXPRESS THE CONDITIONS**. All expressions inside "complex_composite_conditions" **MUST** be based solely on **DEFINED VARIABLES** and their valid value relationships.

Ensure that:
- Action names (action_name) must be **unique** and consists of alphanumeric characters and underscores **only**.
- All variables used in conditions and state changes **MUST** be declared in the provided list of system variables, and their assigned values MUST come from the corresponding list of defined possible values.
- The **conditions are specific** and use system variables appropriately by considering dependencies between processes.
- **Avoid overly generic statements** - each process should feel logically connected to system behavior.

Note that some actions **may not have any conditions or state changes**. In such cases, the corresponding field should be an empty JSON object. This is especially appropriate when conditions and changes cannot be determined through common sense reasoning or by analyzing the system description, or when the intended conditions or changes cannot be expressed using the existing variables.

Please ensure your response is a valid JSON string that can be parsed directly.
````

等价调用如下：

```python
get_LLM_answers(
    question=prompt_gen_actions,
    context=processed_tables,
    history="action"
)
client.chat.completions.create(
    model="o3-mini-2025-01-31",
    reasoning_effort="high",
    messages=[{"role": "user", "content": prompt_gen_actions}]
)
```

### 输入的解释

| 输入字段或片段 | 含义 | 此次如何使用 |
|---|---|---|
| `structured_data.modelName` | 模型名称 `motor_demo` | 拼接进系统描述 |
| `structured_data.modelDesc` | 模型整体描述 | 拼接进系统描述 |
| `structured_data.subsystemCount` | 进程数量 1 | 拼接进系统描述 |
| `structured_data.subsystems[].name/description` | `motor` 的名字和行为描述 | 拼接进系统描述 |
| `processed_tables.processes` | 先前提取的数据定义列表 | `json.dumps()` 后整体拼入 prompt |
| `processes[].processName` | 数据所属进程名 | 要据此组织该进程的动作 |
| `processes[].constants` | 已定义的命名固定值 | 动作条件和变化应引用这些编码；元素字段见下表 |
| `constants[].name/value/description` | 常量名、编码、含义 | 如 `ENGINE_ON` / 1 / 发动机启动 |
| `processes[].variables` | 已定义的可变数据 | 应只使用这些变量，不另造未声明变量 |
| `variables[].name` | 可引用的变量名 | `engine`、`car`、`fuel` |
| `variables[].type` | 数据形式 | 本例均为 `int` |
| `variables[].possibleValues` | 可用值列表 | 约束条件或状态变化应选什么值 |
| `variables[].initialValue` | 初始取值 | 与系统描述一起帮助理解起点，不是动作每次的执行条件 |
| `variables[].description` | 变量含义 | 帮助关联行为和数据 |
| `question` | 完整 `prompt_gen_actions` | 真正作为 API 的 user 消息正文发送 |
| `context` | 日志里保留的常量变量对象 | 不单独发送；上面的 JSON 已通过 `question` 明确拼入 prompt |
| `history="action"` | history 文件选择参数 | 决定保存到 `action-history.json`，不发给模型 |
| `model`、`reasoning_effort` | 旧代码固定的 Planning 模型与推理参数 | 与 Stage 1 相同 |
| `messages`、`role`、`content` | API 消息列表、消息身份、正文 | 只有当前的一条 user 消息，不会自动读取上次对话 |

### 输出

说明性 LLM 回复正文：

<!-- example:ACTIONS -->
```json
{
  "processes": [
    {
      "processName": "motor",
      "actions": [
        {
          "action_name": "start_engine",
          "conditions": {"engine": "ENGINE_OFF", "fuel": "FUEL_FULL"},
          "state_changes": {"engine": "ENGINE_ON"}
        },
        {
          "action_name": "stop_engine",
          "conditions": {"engine": "ENGINE_ON", "car": "CAR_STOPPED"},
          "state_changes": {"engine": "ENGINE_OFF"}
        },
        {
          "action_name": "start_driving",
          "conditions": {"engine": "ENGINE_ON", "car": "CAR_STOPPED", "fuel": "FUEL_FULL"},
          "state_changes": {"car": "CAR_MOVING"}
        },
        {
          "action_name": "stop_driving",
          "conditions": {"car": "CAR_MOVING"},
          "state_changes": {"car": "CAR_STOPPED"}
        },
        {
          "action_name": "consume_fuel",
          "conditions": {"car": "CAR_MOVING", "fuel": "FUEL_FULL"},
          "state_changes": {"fuel": "FUEL_EMPTY", "car": "CAR_STOPPED"}
        },
        {
          "action_name": "refuel",
          "conditions": {"car": "CAR_STOPPED", "fuel": "FUEL_EMPTY"},
          "state_changes": {"fuel": "FUEL_FULL"}
        }
      ]
    }
  ]
}
```

### 输出的解释

| 字段 | 类型 | 含义 | 本例说明 |
|---|---|---|---|
| `processes` | 对象数组 | 按进程组织的行为结果 | 本例只有 `motor` |
| `processes[].processName` | 字符串 | 动作所属的进程 | 沿用原子系统名 |
| `processes[].actions` | 对象数组 | 该进程可能执行的动作集合 | 列表顺序不等于强制执行顺序 |
| `actions[].action_name` | 字符串 | 动作名字，后续通常成为事件名 | `start_engine` 等 |
| `actions[].conditions` | 对象 | 动作执行前必须满足的条件 | 如发动机关闭且有油，才能启动发动机 |
| `conditions` 中的一般键 | 变量名 | 被判断的变量 | 如 `engine`、`fuel` |
| `conditions` 中的一般值 | 本例为字符串；也可为列表等 | 要求变量具有什么值 | `"ENGINE_OFF"` 表示等于该常量；这是规划表示，还不是 `[engine == ENGINE_OFF]` 代码 |
| `actions[].state_changes` | 对象 | 动作执行后，对变量作出的更新 | 未列出的变量通常保持原值 |
| `state_changes` 中的键 | 变量名 | 要修改的变量 | 如 `engine` |
| `state_changes` 中的值 | 本例为字符串 | 修改后的值或表达式 | `"ENGINE_ON"` 是目标值，不是判断条件 |

本例 `consume_fuel` 的两个更新属于同一个动作：油变空时，车也同时停止。这里明确这样建模，是为了避免把“油变空”和“停车”拆成两个动作而产生中间的无油行驶状态。

保存结构与 Stage 1 相同，只是 `question`、`answerGPT`、`context` 和目标文件不同：

```python
interaction = {
    "timestamp": "2026-09-13 10:00:10",  # 示意时间
    "question": prompt_gen_actions,
    "answerGPT": answer,                 # 上方完整动作 JSON 的字符串形式
    "context": processed_tables
}
# 追加到 ./history/action-history.json
```

| 日志字段 | 类型 | 此次含义 |
|---|---|---|
| `timestamp` | 字符串 | 动作提取的记录时间 |
| `question` | 字符串 | 包含系统描述和常量变量数据的完整动作 prompt |
| `answerGPT` | 字符串 | 本次动作 JSON 的回复正文 |
| `context` | 对象；解析失败时可为字符串 | 输入的常量变量结果，不是完整原始任务 |

### 细节补充

- `actions` 是可发生的行为清单，不是“先启动、再关机、再行驶”的线性流程。如何把它们组织成可执行进程由 Claude 实现。
- 多个普通条件在本例中表达同时满足。若某个值为列表，如 `"door": ["closed", "locked"]`，通常表达该变量取其中任一允许值；最后仍需 Claude 正确翻译。
- 源码还允许保留键 `complex_composite_conditions` 表达复杂条件。它不是变量名。本例不用它，避免影响主线；源码第 393 行的下一次注释 prompt 会优先用它的值作为条件文字。
- prompt 要求条件全部放在 `conditions`，`state_changes` 只放无条件更新。若更新分情况，应拆成多个动作；这是对 LLM 的要求，代码没有确定性的 schema 校验器执行这些检查。
- 生成系统描述的 helper 中，追加 `interactionMode` 的代码已被注释。因此不要认为该 helper 把原始任务全部字段都传给模型。
- `gen_actions()` 不显式返回动作结果；后续仍从 history 回读。计时键为 `action-time.runTime`。

---

## Stage 3｜code｜回读动作并读取输入中的断言

### 简述作用

从动作 history 中取出最后一条回复，再从原始任务读取预先提供的 `assertions`。这是准备输入，不生成新断言，不调用 LLM，也不执行验证。

源码位置：第 1028–1054 行，读取断言在第 1052 行。

### 输入

`./history/action-history.json`；Stage 0 的 `current_structured_data`。

### 输入的解释

动作取 `action_history[-1]["answerGPT"]`；断言取 `current_structured_data.get("assertions", [])`。两者不是因果上的“从动作自动推导检查要求”。

### 输出

`latest_action_answer_str`：Stage 2 的动作 JSON 字符串。`assertions_list`：Stage 0 中的两条断言对象，保持原样。

### 输出的解释

两者和先前的 `latest_const_answer_str` 一起传给 `gen_nl_instructions()`。`assertionTruth` 此时仍在断言对象中，但后续生成注释的代码不会使用它。

### 细节补充

动作 history 缺失、为空或文件自身 JSON 损坏时跳过当前任务。断言列表为空时只打印警告，仍进入 Stage 4。源码包装函数虽然有 `history="assertion"` 的保存分支，但此主流程没有调用它来生成断言。

---

## Stage 4.1｜LLM / Planning-Agent｜第 3 次调用：生成常量变量注释

### 简述作用

把先前提取的常量变量 JSON，再转换成逐行的自然语言注释，供后续 Claude 生成 CSP#。这不是确定性的 JSON 转换，而是再次调用同一个 Planning LLM。

源码位置：`gen_nl_instructions()` 第 360–381 行；具体调用第 373 行。

### 输入

`const_answer_str`：Stage 1 回复的完整字符串。代码尝试解析后得到 `parsed_const_data`，本例解析成功，内容如下：

<!-- rendered:STAGE4_1_INPUT -->
```json
{
  "processes": [
    {
      "processName": "motor",
      "constants": [
        {
          "name": "ENGINE_OFF",
          "value": 0,
          "description": "The engine is off"
        },
        {
          "name": "ENGINE_ON",
          "value": 1,
          "description": "The engine is on"
        },
        {
          "name": "CAR_STOPPED",
          "value": 0,
          "description": "The car is stopped"
        },
        {
          "name": "CAR_MOVING",
          "value": 1,
          "description": "The car is moving"
        },
        {
          "name": "FUEL_EMPTY",
          "value": 0,
          "description": "The fuel is empty"
        },
        {
          "name": "FUEL_FULL",
          "value": 1,
          "description": "The fuel is full"
        }
      ],
      "variables": [
        {
          "name": "engine",
          "type": "int",
          "possibleValues": "ENGINE_OFF, ENGINE_ON",
          "initialValue": "ENGINE_OFF",
          "description": "Current engine state"
        },
        {
          "name": "car",
          "type": "int",
          "possibleValues": "CAR_STOPPED, CAR_MOVING",
          "initialValue": "CAR_STOPPED",
          "description": "Current movement state of the car"
        },
        {
          "name": "fuel",
          "type": "int",
          "possibleValues": "FUEL_EMPTY, FUEL_FULL",
          "initialValue": "FUEL_FULL",
          "description": "Current binary fuel state"
        }
      ]
    }
  ]
}
```

再用 `json.dumps()` 形成 `info`，拼到下面的完整 `prompt1`：

<!-- rendered:STAGE4_1_PROMPT -->
````text
According to the following json data, please generate NL annotation for PAT code generation, for example, to define the number of owners, you should generate such an annotation: // "N": number of owners in the system (set to 2), and to define the constant "far", you should generate this annotation: // "far": represents an owner being out and far away from the car. The annotation for each constant and variable should be generated on a new line. **Note: 1. For variables, there is no need to specify possible values, only specify the initial value for each variable. 2. Generate the annotations for all constants before variables.** The json data is as follows:
{"processes": [{"processName": "motor", "constants": [{"name": "ENGINE_OFF", "value": 0, "description": "The engine is off"}, {"name": "ENGINE_ON", "value": 1, "description": "The engine is on"}, {"name": "CAR_STOPPED", "value": 0, "description": "The car is stopped"}, {"name": "CAR_MOVING", "value": 1, "description": "The car is moving"}, {"name": "FUEL_EMPTY", "value": 0, "description": "The fuel is empty"}, {"name": "FUEL_FULL", "value": 1, "description": "The fuel is full"}], "variables": [{"name": "engine", "type": "int", "possibleValues": "ENGINE_OFF, ENGINE_ON", "initialValue": "ENGINE_OFF", "description": "Current engine state"}, {"name": "car", "type": "int", "possibleValues": "CAR_STOPPED, CAR_MOVING", "initialValue": "CAR_STOPPED", "description": "Current movement state of the car"}, {"name": "fuel", "type": "int", "possibleValues": "FUEL_EMPTY, FUEL_FULL", "initialValue": "FUEL_FULL", "description": "Current binary fuel state"}]}]}
````

等价调用：

```python
data1_interaction = get_LLM_answers(
    question=prompt1,
    context=parsed_const_data,
    history="skip"
)
client.chat.completions.create(
    model="o3-mini-2025-01-31",
    reasoning_effort="high",
    messages=[{"role": "user", "content": prompt1}]
)
```

### 输入的解释

| 输入字段或片段 | 含义 | 此次如何使用 |
|---|---|---|
| `processes` | 进程数据定义列表 | 整体序列化为 JSON 文本进入 prompt |
| `processes[].processName` | 数据所属进程 | 区分每个进程提取的数据；本例为 `motor` |
| `processes[].constants` | 固定值定义列表 | 先生成这些常量的注释 |
| `constants[].name` | 常量名字 | 注释中应保留它 |
| `constants[].value` | 固定编码 | 注释中应说明设置为几 |
| `constants[].description` | 业务含义 | 供 LLM 改写成常量说明 |
| `processes[].variables` | 变量定义列表 | 在常量之后生成变量注释 |
| `variables[].name` | 变量名 | 注释中应保留它 |
| `variables[].type` | 数据形式 | 可以据此说明变量形式；本例为 `int` |
| `variables[].possibleValues` | 允许值列表 | 输入仍有这个字段，但 prompt 明确说不必在变量注释中重复可能值 |
| `variables[].initialValue` | 初始值 | 变量注释必须说明它 |
| `variables[].description` | 变量记录什么 | 供 LLM 改写成变量说明 |
| `prompt1` 中的固定指令 | 每项一行、先常量后变量、变量只需初值 | 限定回复格式，不要求输出 JSON |
| `question` | 完整 `prompt1` | 真正发送的消息正文 |
| `context` | 已解析的常量变量对象 | 包装函数返回记录时保留，不单独发送 |
| `history="skip"` | 本次不写入包装函数的 history 文件 | **只是跳过保存，不是跳过 LLM 调用** |
| `model`、`reasoning_effort` | Planning 模型与推理参数 | 与前两次相同 |
| `messages`、`role`、`content` | 当前 user 消息及正文 | 只发送本次 prompt；没有自动附加完整原始任务或之前的对话 |

### 输出

说明性 LLM 回复正文，是普通文本，不是 JSON，也还不是声明变量的 CSP# 代码：

<!-- example:NL_CONSTS -->
```text
// "ENGINE_OFF": the engine is off (set to 0).
// "ENGINE_ON": the engine is on (set to 1).
// "CAR_STOPPED": the car is stopped (set to 0).
// "CAR_MOVING": the car is moving (set to 1).
// "FUEL_EMPTY": the fuel is empty (set to 0).
// "FUEL_FULL": the fuel is full (set to 1).
// "engine": integer variable recording the engine state (initially ENGINE_OFF).
// "car": integer variable recording whether the car is moving (initially CAR_STOPPED).
// "fuel": integer variable recording the binary fuel state (initially FUEL_FULL).
```

### 输出的解释

这段文本没有新的 JSON 字段，不能把下表中的“名字/含义/初值”误认为实际回复的键。

| 文本组成 | 含义 | 本例对应 |
|---|---|---|
| `//` | 逐行注释形式，便于作为代码生成说明 | 每行说明一个数据项；不是一个可执行声明 |
| 引号里的常量名 | 后续应该定义的常量标识符 | `ENGINE_OFF` 等六项 |
| 常量的文字说明 | 固定值的业务含义 | 发动机关闭、车辆停止等 |
| `set to ...` | 常量应采用的数值 | 0 或 1 |
| 引号里的变量名 | 后续应该声明的变量标识符 | `engine`、`car`、`fuel` |
| 变量的文字说明 | 变量记录什么以及形式 | 本例是整数状态变量 |
| `initially ...` | 变量初值 | 对应先前 JSON 的 `initialValue` |

包装函数返回到内存的结构：

```python
data1_interaction = {
    "timestamp": "2026-09-13 10:00:20",  # 示意时间
    "question": prompt1,
    "answerGPT": answer,                 # 上方完整注释文本
    "context": parsed_const_data
}
data1_content = data1_interaction["answerGPT"]
```

| 返回字段或变量 | 类型 | 含义 |
|---|---|---|
| `timestamp` | 字符串 | 此次调用的时间 |
| `question` | 字符串 | 完整的常量变量注释 prompt |
| `answerGPT` | 字符串 | 注释回复正文，不再是常量变量 JSON |
| `context` | 对象；解析失败可为字符串 | 本次输入的常量变量数据 |
| `data1_content` | 字符串 | 从 `answerGPT` 取出的注释，稍后保存成 `data1` |

### 细节补充

- `history="skip"` 使包装函数直接返回 interaction，不自动保存本次 `question/context` 到常量变量 history。后面保存 `nl-instruction-part.json` 时只保存这段注释正文和时间等字段。
- 此次 prompt 的数据部分只有提取后的 JSON，没有再传原始子系统描述，也没有断言对象。
- 注释未必与输入 JSON 完全等价：LLM 可能漏项或改写错误，代码没有做逐项等价性检查。
- 若原回复不能解析为 JSON，仍把原字符串放进 prompt；若本次 LLM 调用失败，`data1_content` 保持空字符串，后续代码仍可能继续合并其他部分。

---

## Stage 4.2｜LLM / Planning-Agent｜第 4 次调用：生成动作注释

### 简述作用

把动作 JSON 转换成逐行自然语言说明：哪个进程中，在什么条件下，可以执行什么动作，会改变什么变量。

源码位置：第 383–403 行；具体调用第 395 行。

### 输入

`action_answer_str`：Stage 2 的完整动作回复字符串。解析成功后得到下面的 `parsed_action_data`：

<!-- rendered:STAGE4_2_INPUT -->
```json
{
  "processes": [
    {
      "processName": "motor",
      "actions": [
        {
          "action_name": "start_engine",
          "conditions": {
            "engine": "ENGINE_OFF",
            "fuel": "FUEL_FULL"
          },
          "state_changes": {
            "engine": "ENGINE_ON"
          }
        },
        {
          "action_name": "stop_engine",
          "conditions": {
            "engine": "ENGINE_ON",
            "car": "CAR_STOPPED"
          },
          "state_changes": {
            "engine": "ENGINE_OFF"
          }
        },
        {
          "action_name": "start_driving",
          "conditions": {
            "engine": "ENGINE_ON",
            "car": "CAR_STOPPED",
            "fuel": "FUEL_FULL"
          },
          "state_changes": {
            "car": "CAR_MOVING"
          }
        },
        {
          "action_name": "stop_driving",
          "conditions": {
            "car": "CAR_MOVING"
          },
          "state_changes": {
            "car": "CAR_STOPPED"
          }
        },
        {
          "action_name": "consume_fuel",
          "conditions": {
            "car": "CAR_MOVING",
            "fuel": "FUEL_FULL"
          },
          "state_changes": {
            "fuel": "FUEL_EMPTY",
            "car": "CAR_STOPPED"
          }
        },
        {
          "action_name": "refuel",
          "conditions": {
            "car": "CAR_STOPPED",
            "fuel": "FUEL_EMPTY"
          },
          "state_changes": {
            "fuel": "FUEL_FULL"
          }
        }
      ]
    }
  ]
}
```

代码序列化得到 `info_action`，构造完整 `prompt2`：

<!-- rendered:STAGE4_2_PROMPT -->
````text
According to the following json data, please generate the NL annotation for PAT code generation. For each process, start the annotation with an annotation line // Definition of the "process_name" subsystem. (if any variable is involved in the process, please also add the description like: for xxx with index i). To annotate the actions that can happen in processes, we specify the conditions, action name, and the changes that the action introduces for the variables. For example, an annotation might be: //if "owner[i]" is "far", the action "towards.i" makes "owner[i]" become "near" (owner approaches the car). Note that, when processing conditions, if "complex_composite_conditions" exists as a key, its value alone forms the condition and should be directly described without mentioning "complex_composite_conditions". For other entries in conditions, the key and value together form the condition. Now please generate the NL annotations for each process in the json data, ensuring that the annotation for each action will span a new row. The json data is as follows:
{"processes": [{"processName": "motor", "actions": [{"action_name": "start_engine", "conditions": {"engine": "ENGINE_OFF", "fuel": "FUEL_FULL"}, "state_changes": {"engine": "ENGINE_ON"}}, {"action_name": "stop_engine", "conditions": {"engine": "ENGINE_ON", "car": "CAR_STOPPED"}, "state_changes": {"engine": "ENGINE_OFF"}}, {"action_name": "start_driving", "conditions": {"engine": "ENGINE_ON", "car": "CAR_STOPPED", "fuel": "FUEL_FULL"}, "state_changes": {"car": "CAR_MOVING"}}, {"action_name": "stop_driving", "conditions": {"car": "CAR_MOVING"}, "state_changes": {"car": "CAR_STOPPED"}}, {"action_name": "consume_fuel", "conditions": {"car": "CAR_MOVING", "fuel": "FUEL_FULL"}, "state_changes": {"fuel": "FUEL_EMPTY", "car": "CAR_STOPPED"}}, {"action_name": "refuel", "conditions": {"car": "CAR_STOPPED", "fuel": "FUEL_EMPTY"}, "state_changes": {"fuel": "FUEL_FULL"}}]}]}
````

等价调用：

```python
data2_interaction = get_LLM_answers(
    question=prompt2,
    context=parsed_action_data,
    history="skip"
)
client.chat.completions.create(
    model="o3-mini-2025-01-31",
    reasoning_effort="high",
    messages=[{"role": "user", "content": prompt2}]
)
```

### 输入的解释

| 输入字段或片段 | 含义 | 此次如何使用 |
|---|---|---|
| `processes` | 按进程组织的动作结果 | 整体 JSON 序列化后放入 prompt |
| `processes[].processName` | 动作所属进程 | 生成该进程的定义说明行 |
| `processes[].actions` | 该进程的动作列表 | 每个动作生成一行说明，不意味着按列表顺序执行 |
| `actions[].action_name` | 动作名字 | 保留到文字说明，供 Claude 后续生成事件名 |
| `actions[].conditions` | 动作发生前的条件 | 改写成“if ...”之类的自然语言 |
| `conditions` 的一般键 | 被检查的变量名 | 如 `engine`、`car`、`fuel` |
| `conditions` 的一般值 | 要求的取值 | 如 `ENGINE_ON`、`FUEL_FULL` |
| `conditions.complex_composite_conditions` | 可选的复杂布尔关系，本例没有 | 存在时，prompt 要求只用它的值作为条件描述，不显示保留键名 |
| `actions[].state_changes` | 执行动作后的变量更新 | 改写成“makes ... become ...”之类的说明 |
| `state_changes` 的键 | 要修改的变量 | 如 `fuel` |
| `state_changes` 的值 | 修改后的值或表达式 | 如 `FUEL_EMPTY` |
| `prompt2` 中的固定指令 | 每个进程一行标题、每个动作一行、说明条件和变化 | 限定输出文字的组织方式 |
| `question` | 完整 `prompt2` | 实际发送的 user 消息正文 |
| `context` | 动作对象 | 仅作为返回记录的附带输入，不单独发送 |
| `history="skip"` | 跳过包装函数自动写 history | 仍会真正调用 Planning LLM |
| `model`、`reasoning_effort` | Planning 模型与推理参数 | 与前三次相同 |
| `messages`、`role`、`content` | 当前的一条 user 消息 | 没有自动附加常量变量 JSON、断言或原始系统描述 |

### 输出

说明性 LLM 回复正文：

<!-- example:NL_ACTIONS -->
```text
// Definition of the "motor" subsystem.
// If "engine" is "ENGINE_OFF" and "fuel" is "FUEL_FULL", action "start_engine" makes "engine" become "ENGINE_ON".
// If "engine" is "ENGINE_ON" and "car" is "CAR_STOPPED", action "stop_engine" makes "engine" become "ENGINE_OFF".
// If "engine" is "ENGINE_ON", "car" is "CAR_STOPPED", and "fuel" is "FUEL_FULL", action "start_driving" makes "car" become "CAR_MOVING".
// If "car" is "CAR_MOVING", action "stop_driving" makes "car" become "CAR_STOPPED".
// If "car" is "CAR_MOVING" and "fuel" is "FUEL_FULL", action "consume_fuel" makes "fuel" become "FUEL_EMPTY" and "car" become "CAR_STOPPED" in the same action.
// If "car" is "CAR_STOPPED" and "fuel" is "FUEL_EMPTY", action "refuel" makes "fuel" become "FUEL_FULL".
```

### 输出的解释

它也是普通注释文本，没有 JSON 字段。逐项含义如下：

| 文本组成 | 含义 | 本例对应 |
|---|---|---|
| `Definition of ... subsystem` | 这组动作属于哪个进程 | `motor` |
| `If ...` | 动作的发生条件 | 原动作 JSON 的 `conditions` |
| `action "..."` | 发生的动作名字 | 原 JSON 的 `action_name` |
| `makes ... become ...` | 动作更新哪些变量、更新成什么值 | 原 JSON 的 `state_changes` |
| `and` 等连接文字 | 条件或更新之间的组合关系 | 本例中的条件需同时满足；多个更新属于同一动作 |
| 一行动作说明 | 一个可发生的行为分支 | 不是实际的 CSP# 事件定义，也不是强制执行顺序 |

包装函数返回到内存的结构：

```python
data2_interaction = {
    "timestamp": "2026-09-13 10:00:30",  # 示意时间
    "question": prompt2,
    "answerGPT": answer,                 # 上方完整动作注释
    "context": parsed_action_data
}
data2_content = data2_interaction["answerGPT"]
```

| 返回字段或变量 | 类型 | 含义 |
|---|---|---|
| `timestamp` | 字符串 | 本次动作注释生成时间 |
| `question` | 字符串 | 完整动作注释 prompt |
| `answerGPT` | 字符串 | 动作注释正文 |
| `context` | 对象；解析失败可为字符串 | 输入的动作数据 |
| `data2_content` | 字符串 | 取出的动作注释，稍后保存成 `data2` |

### 细节补充

- 它与 Stage 4.1 是两次独立 API 调用，不是一个模型回复的两个字段，也不是另一种不同的 LLM。
- 常量变量的含义没有以完整 JSON 再次传入这一调用；动作数据里引用的符号仍在 prompt 中，但不等于自动带上之前的完整对话。
- 输入解析失败仍保留原字符串；调用失败可能得到空的 `data2_content`。没有确定性代码证明改写前后的动作语义一致。
- `complex_composite_conditions` 规则来自 prompt；代码本身并未先把复杂条件编译成正式表达式再交给 LLM。

---

## Stage 4.3｜code｜拼接总系统、状态谓词与断言说明

### 简述作用

把输入字段按固定模板变成注释文字：说明系统怎样组合、什么算目标状态、需要检查哪些问题。不调用 LLM，也不直接生成 CSP# 定义。

源码位置：第 405–406 行；helper 第 274–355 行。

### 输入

Stage 0 的 `modelName="motor_demo"`、`interactionMode="interleaving"` 和两条 `assertions`。

### 输入的解释

组合模式选用固定 `|||` 说明；空 `component` 选用整体模型；`deadlock-free` 生成无死锁检查说明；`reachability/customize` 读取 `stateName/customDescription`。**不使用 `assertionTruth`。**

### 输出

`data3_content`。下面是本例通过旧 helper 实际得到的文本，开头包含一行空行：

<!-- rendered:ASSERTION_NL -->
````text

// define the motor_demo system: overall system combining all the subsystems interleavingly ("|||"), consider defining the system around the most central variable (e.g., the number of owners) only if appropriate
// define **exactly** the following states and assertions as specified. Do **NOT** add, modify, or omit anything
// assert motor_demo system deadlockfree
// define drivewithoutfuel: the car is moving while the fuel is empty
// assert that the motor_demo system can reach the state "drivewithoutfuel"
````

### 输出的解释

第一条是总系统组合说明；第二条要求代码生成器不增删状态或断言；最后三条分别提出无死锁检查、定义 `drivewithoutfuel` 的含义、检查该谓词是否可达。

### 细节补充

这里“define drivewithoutfuel”仍是一句文字说明，不是 `#define` 语句。“assert ... can reach”也不是要求系统必须到达坏状态，而是要求生成可达性检查。期望 `Invalid` 留到 PAT 验证后比较，不能在这里把问题改成另一个问题。

`interleaving` 对单个子系统没有额外的交错对象；最后的 CSP# 可以只把系统定义为 `motor()`。如果有多个子系统，才需要把它们用 `|||` 组合。

---

## Stage 4.4｜code｜合并并保存完整建模计划

### 简述作用

将两次 LLM 生成的文字和代码拼接的文字连接起来：数据说明 + 动作说明 + 系统及检查说明 → 完整自然语言建模计划。

源码位置：第 408–451 行。

### 输入

`data1_content`、`data2_content`、`data3_content`。

### 输入的解释

三项都是字符串。代码只连接文本，不调用新的 LLM，不根据文字内容自动合并符号或修复冲突。

### 输出

本例的完整 `full_prompt` 如下：

<!-- rendered:FULL_NL -->
````text
// "ENGINE_OFF": the engine is off (set to 0).
// "ENGINE_ON": the engine is on (set to 1).
// "CAR_STOPPED": the car is stopped (set to 0).
// "CAR_MOVING": the car is moving (set to 1).
// "FUEL_EMPTY": the fuel is empty (set to 0).
// "FUEL_FULL": the fuel is full (set to 1).
// "engine": integer variable recording the engine state (initially ENGINE_OFF).
// "car": integer variable recording whether the car is moving (initially CAR_STOPPED).
// "fuel": integer variable recording the binary fuel state (initially FUEL_FULL).
// Definition of the "motor" subsystem.
// If "engine" is "ENGINE_OFF" and "fuel" is "FUEL_FULL", action "start_engine" makes "engine" become "ENGINE_ON".
// If "engine" is "ENGINE_ON" and "car" is "CAR_STOPPED", action "stop_engine" makes "engine" become "ENGINE_OFF".
// If "engine" is "ENGINE_ON", "car" is "CAR_STOPPED", and "fuel" is "FUEL_FULL", action "start_driving" makes "car" become "CAR_MOVING".
// If "car" is "CAR_MOVING", action "stop_driving" makes "car" become "CAR_STOPPED".
// If "car" is "CAR_MOVING" and "fuel" is "FUEL_FULL", action "consume_fuel" makes "fuel" become "FUEL_EMPTY" and "car" become "CAR_STOPPED" in the same action.
// If "car" is "CAR_STOPPED" and "fuel" is "FUEL_EMPTY", action "refuel" makes "fuel" become "FUEL_FULL".

// define the motor_demo system: overall system combining all the subsystems interleavingly ("|||"), consider defining the system around the most central variable (e.g., the number of owners) only if appropriate
// define **exactly** the following states and assertions as specified. Do **NOT** add, modify, or omit anything
// assert motor_demo system deadlockfree
// define drivewithoutfuel: the car is moving while the fuel is empty
// assert that the motor_demo system can reach the state "drivewithoutfuel"
````

保存结构如下。变量分别指向上面已完整展示的三段文本：

```python
# ./history/nl-instruction-part.json 中追加一条
new_part_entry = {
    "timestamp": "2026-09-13 10:00:31",  # 示意时间
    "data1": data1_content,
    "data2": data2_content,
    "data3": data3_content
}

# ./history/nl-instruction-claude.json 中追加一条
new_claude_entry = {
    "timestamp": "2026-09-13 10:00:31",  # 同一个时间
    "fullText": full_prompt
}
```

### 输出的解释

| 字段 | 类型 | 含义 |
|---|---|---|
| `timestamp` | 字符串 | 本次完整计划保存的时间 |
| `data1` | 字符串 | Planning LLM 生成的常量变量注释 |
| `data2` | 字符串 | Planning LLM 生成的动作注释 |
| `data3` | 字符串 | Python 模板生成的系统、状态谓词和断言说明 |
| `fullText` | 字符串 | `data1_content + "\n" + data2_content + "\n" + data3_content` |

### 细节补充

history 顶层均是数组，代码追加记录后重写文件；文字在实际 JSON 中会以字符串保存，换行表现为 `\n`。`gen_nl_instructions()` 不显式返回完整计划。计时写入 `nl-annotation-time.runTime`，它包括两次 LLM 调用及本阶段的拼接、保存，不是额外的模型输出。

---

## Stage 4.后处理｜code｜回读完整计划

### 简述作用

从已保存的计划 history 取出最后一条 `fullText`，准备交给代码生成函数。

源码位置：第 1061–1084 行。

### 输入

`./history/nl-instruction-claude.json`。

### 输入的解释

读取 `nl_claude_history[-1].get("fullText")`，不是把整个 history 发给模型。

### 输出

`retrieved_full_nl_prompt`，内容就是 Stage 4.4 的完整计划字符串。

### 输出的解释

它作为 `gen_code(current_structured_data, retrieved_full_nl_prompt)` 的第二项输入；第一项仍是原始任务对象。

### 细节补充

文件缺失、非法 JSON、空 history 或空 `fullText` 都会跳过当前任务。没有按任务 ID 匹配记录。

---

## Stage 5.1｜code｜本地检索一组参考示例

### 简述作用

把当前计划与示例库中已有的自然语言注释进行文本相似度比较，选出一组 `nl/code`，作为 Claude 的参考。这里没有调用 LLM，也没有调用 embedding API。

源码位置：`gen_code()` 第 526–530 行；检索 helper 第 455–483 行。

### 输入

Stage 4.4 的完整计划；`./database-rag-claude.json`，归档文件目前有 6 条示例。

### 输入的解释

库中每条 `nl` 是参考模型的自然语言注释，`code` 是对应的 CSP#。代码使用 `TfidfVectorizer` 和余弦相似度，只按文字选相似项。

### 输出

`retrieved_example = {"nl": matched_entry["nl"], "code": matched_entry["code"]}`，再拆成 `retrieved_nl` 和 `retrieved_code`。

<!-- rendered:RAG_NOTE -->
本例使用固定的上述注释，按旧 helper 的默认 TF-IDF/余弦相似度公式进行本地等价计算，选中归档库的**第 5 条**（Python 下标 4）：包含两位车主、钥匙、车门和发动机的整车参考示例。它的自然语言注释 6796 个字符，参考代码 3393 个字符。这个编号是本例文字对应的检索结果，不是源码写死的选择。

### 输出的解释

这两项是参考示例的输入/输出配对，不是当前 `motor_demo` 的最终代码。它们的完整内容可以在 Stage 5.4 的完整 prompt 中看到。

### 细节补充

相似度最高不代表语义完全相同。库缺失、非法 JSON、没有可用注释或检索输入为空时，helper 返回空 `nl/code`，后面仍可能调用 Claude。

---

## Stage 5.2｜code｜读取语法提示

### 简述作用

把本地语法提示文件里的两段文字读取出来，准备拼入 Claude prompt。

源码位置：第 533–545 行。

### 输入

`./syntax-dataset.json`。

### 输入的解释

文件的 `general_info` 是 PAT 的通用说明；`pitfalls_rules` 是分号、常量、变量、数组、进程、组合和断言等语法提示。它们都是文字，不是程序执行的规则校验器。

### 输出

`syntax_general_info` 和 `syntax_pitfalls_rules` 两个字符串。

### 输出的解释

分别进入最终 prompt 的 `General Information` 和 `Pitfalls and Syntax Guidelines` 两部分。

### 细节补充

读取失败时保留空字符串，程序不一定终止。本文完整 prompt 保留归档文件原文，不能把库中的所有提示都当作本文新验证过的严格语义结论。

---

## Stage 5.3｜code｜组装 Claude prompt

### 简述作用

将五类材料按固定顺序连接成一次模型调用的正文：通用说明、语法提示、参考示例、当前系统原始描述、当前完整建模计划。

源码位置：第 547–570 行。

### 输入

`syntax_general_info`、`syntax_pitfalls_rules`、`retrieved_nl`、`retrieved_code`、原始 `structured_data`、完整 `full_nl_prompt`。

### 输入的解释

原始任务在此只通过 `_generate_descriptions_for_actions_helper()` 转成 `system_description_for_prompt`，不是把完整任务 JSON 原封不动发送。因此，原始 `assertionTruth` 没有进入这段系统描述。

### 输出

`final_code_gen_prompt`，完整示例见下一节。

### 输出的解释

`### Example` 是参考任务；`### System Annotation` 才是当前任务。文字指令要求以当前 annotation 为主要依据，必要时参考原始系统描述。

### 细节补充

这一步只是字符串拼接，不调用 LLM，不生成最终进程结构，不把 JSON 自动编译成 CSP#。

---

## Stage 5.4｜LLM / Claude｜第 5 次调用：生成 CSP#

### 简述作用

把系统描述、文字建模计划、语法提示及参考示例转换成实际 CSP#：声明常量变量，定义进程行为和系统，定义状态谓词，写出 `#assert` 检查指令。

源码位置：`gen_code()` 第 574–577 行；Claude helper 第 485–519 行；主程序第 1097 行。

### 输入

本例的完整 `final_code_gen_prompt` 如下。内容较长，展开即可看到所有材料，不省略参考代码或语法规则。

<details>
<summary>展开：代入本例数据并使用实际归档 RAG 库得到的完整 Claude prompt</summary>

<!-- rendered:CLAUDE_PROMPT -->
````text
You are an expert in PAT (Process Analysis Toolkit), and you already possess a strong understanding of PAT concepts as outlined in the documentation. As a reminder, here are a few key guidelines:
--- Quick Reference ---
General Information: 1. PAT's system implementation is process-oriented, centering around events, processes, and their interactions to model and verify system behaviors.
2. PAT extends the classic process algebra, Communicating Sequential Processes (CSP), by supporting standard CSP syntax while introducing additional constructs such as mutable variables, imperative-style state updates, and model-checking capabilities. While CSP process interaction remains the foundation, PAT incorporates features reminiscent of C#, such as structured variable declarations, conditional expressions, and imperative-style assignments. This allows for more expressive modeling but also introduces syntax and semantics that differ from traditional CSP.
3. For model checking, we focus on three aspects: Deadlock-freeness, Reachability, and LTL properties

Pitfalls and Syntax Guidelines: 1. Semicolons
In PAT, semicolons are generally required after all statements; the only exception is inside process expressions (e.g., with ->, [], or |||), where semicolons are not used.
Example: #define N 5;
Example: var board[N*N];
Example: D() = e2 -> p3 -> D();
Example: E() = B() [] C();
Example: #assert A deadlockfree;

2. Constants
Example: #define max 5;
Example: #define N 2;

3. Variables
Example: var knight = 0;
Example: var engine = off;

4. Array initialization:
4.1. Remember to use square brackets [] to list out the values of the array.
Example: var owner[N] = [far, far];
Example: var board=[3, 5, 6, 0, 2, 7];

4.2. For PAT's 2d arrays, it's important to note that their values **must be** initialized with a single flat list, nested lists aren't supported.
Example: var board[2][3] = [0,0,0,1,1,1];

5. Enumerations
Remember to use curly brackets {} immediately after the keyword enum, enclosing the variables without specifying a name.
Example: enum {unlock, lock, open};
Example: enum {off, on};

6. System Definitions:
6.1. Guarded conditions
Remember to use square brackets [] to enclose the guard condition that needs to be satisfied to execute the guarded process.
Syntax: [cond] Process
Example: [owner[i] == far]
Example: [key == i && owner[i] == in]
Example: [owner[i] == near || owner[i]==far]

6.2. Process definition:
Remember that each process definition should typically transition back to itself or another process using the -> operator to ensure continuity. If a process does not transition, it will terminate upon execution, which may lead to unintended deadlocks or premature termination unless explicitly intended.
Example: VM() = insertcoin -> coffee -> VM();
Example: owner_pos(i) = [owner[i] == far] towards.i{owner[i] = near;} -> owner_pos(i);
Data operations can take place inside events:
Example: towards.i{owner[i] = near;}

6.3. Process assembling:
Three options available to assemble different processes into a system: interleaving, parallel composition, or choices.
- Interleaving: used when multiple processes can run concurrently without barrier synchronization.
  Syntax: ||| x:{0..n} @ P(x)
  Example: car() = ||| i:{0..N-1} @ (owner_pos(i) ||| key_pos(i) ||| door_op(i) ||| motor(i));
- Parallel composition: used when multiple processes run with barrier synchronization.
  Syntax: || x:{0..n} @ P(x);
  Example: College() = ||x:{0..N-1}@(Phil(x)||Fork(x));
- Choices: used when only one process will be selected to execute at a time.
  Syntax: [] x:{0..n} @ P(x);
- Special Syntax: assembling **infinite** number of processes.
  Syntax: |||{..} @ (P() ||| Q());
- Special Syntax: **interrupt** is a reserved keyword to define interruption.
  Syntax: sys = (|||i:{0..N-1} @ (process1(i) ||| process2(i))) interrupt (action1 -> action2 -> Stop);

7. Assertions
7.1. Deadlock-freeness checks
Syntax: use the keyword "deadlockfree".
Example: #assert System deadlockfree;

7.2. Reachability checks
Syntax: define states and use the keyword "reaches" in the assertion to check whether the defined states are reachable.
Example: #define goal1  (!on && dim == 50); #assert System reaches goal1;

7.3. LTL properties
Syntax: all states (if any are involved) need to be separately defined before composing the LTL formula in the assertion. The LTL operators should only appear in assertions, not state definitions.
Example: #assert System |= [](turnOn -> <> turnOff);
Examples: #define light50 (on && dim==50); #assert System |= []<>light50;

Your task is to generate the PAT code given the corresponding natural language annotation for the system.
### Example:
**Input NL Annotation:** // "N": number of owners in the system (set to 2)

// enumerations used in the model
// "far": represents an owner being out and far away from the "car"
// "near": represents an owner being near and close enough to interact with the "car" (e.g., open/lock the "door" if holding the "key")
// "in": represents an owner being inside the "car"

// "off": indicates the "engine" is "off"
// "on": indicates the "engine" is "on"

// "unlock": indicates the "door" is unlocked (but closed)
// "lock": indicates the "door" is locked (and must be closed)
// "open": indicates the "door" is open

// "incar": indicates the "key" is put inside the "car"
// "faralone": indicates the "key" is put outside and far away

// "owner": array representing each owner's position; initially, all owners are assumed to be "far" from the "car"

// "engine": status of the car's engine; initially set to "off"
// "door": status of the car's door; initially set to "lock"
// "key": position of the key fob; initially, it is with the first owner (index 0)
// "moving": indicates whether the "car" is moving (0 for stop, 1 for moving); initially, the "car" is not moving
// "fuel": represents the available fuel; initially 10 units (e.g., 1 unit for a short drive, 5 units for a long drive)

// Definition of the "owner_pos" subsystem for an owner with index i
// if "owner[i]" is "far", the action "towards.i" makes "owner[i]" become "near" (owner approaches the car)
// alternative branch separator
// if "owner[i]" is "near", the action "goaway.i" makes "owner[i]" become "far" (owner moves away from the car)
// alternative branch separator
// if "owner[i]" is "near", the "door" is "open", and the "car" is not "moving", the action "getin.i" makes "owner[i]" become "in" (owner enters the car)
// alternative branch separator
// if "owner[i]" is "in", the "door" is "open", and the "car" is not "moving", the action "goout.i" makes "owner[i]" become "near" (owner exits the car)

// Definition of the "key_pos" subsystem for an owner with index i
// if "key" is with owner i and "owner[i]" is "in", the action "putincar.i" sets "key" to "incar" (key placed inside the car)
// alternative branch separator
// if "key" is with owner i and "owner[i]" is "far", the action "putaway.i" sets "key" to "faralone" (key stored away outside the car)
// alternative branch separator
// if either "key" is "faralone" and "owner[i]" is "far", or "key" is "incar" and "owner[i]" is "in", the action "getkey.i" returns "key" to owner i

// Definition of the "door_op" subsystem for an owner with index i
// if "key" is with "owner[i]", "owner[i]" is "near", "door" is "lock", and the "car" is not "moving", the action "unlockopen.i" sets "door" to "open" (unlocking and opening the door)
// alternative branch separator
// if "owner[i]" is "near", "door" is "unlock", and the "car" is not "moving", the action "justopen.i" sets "door" to "open" (simply opening the door)
// alternative branch separator
// if "door" is not "open" and "owner[i]" is "in", the action "insideopen.i" sets "door" to "open" (an inside command to open the door)
// alternative branch separator
// if "door" is "open", the action "close.i" sets "door" to "unlock" (closing the door without locking)
// alternative branch separator
// if "door" is "unlock" and "owner[i]" is "in", the action "insidelock.i" sets "door" to "lock" (locking the door from inside)
// alternative branch separator
// if "door" is "unlock", "owner[i]" is "near", and "key" is with owner i, the action "outsidelock.i" sets "door" to "lock" (locking the door from outside)

// Definition of the "motor" subsystem for an owner with index i
// if "owner[i]" is "in", and either "key" is with owner i or is "incar", and "engine" is "off" with nonzero "fuel", the action "turnon.i" sets "engine" to "on" (starting the engine)
// alternative branch separator
// if "engine" is "on", "owner[i]" is "in", and the "car" is not "moving", the action "startdrive.i" sets "moving" to 1 (starting to drive)
// alternative branch separator
// if the "car" is "moving" with nonzero "fuel", the action "shortdrive.i" decreases "fuel" by 1; if "fuel" becomes 0, "engine" is set to "off" and "moving" becomes 0 (modeling a short drive)
// alternative branch separator
// if the "car" is "moving" and "fuel" is greater than 5, the action "longdrive.i" decreases "fuel" by 5; if "fuel" becomes 0, "engine" is set to "off" and "moving" becomes 0 (modeling a long drive)
// alternative branch separator
// if "engine" is "on", the car is "moving", and "owner[i]" is "in", the action "stop.i" sets "moving" to 0 (stopping the car)
// alternative branch separator
// if "fuel" is 0 and "engine" is "off", the action "refill" resets "fuel" to 10 (refueling the car)
// alternative branch separator
// if "engine" is "on", the car is not "moving", and "owner[i]" is "in", the action "turnoff.i" sets "engine" to "off" (turning off the engine)

// "car": overall system combining the "motor", "door_op", "key_pos", and "owner_pos" subsystems interleavingly for each owner (from index 0 to "N"-1)

// defines "runwithoutowner": the car is "moving" while both "owner[0]" and "owner[1]" are "far" (testing if the car can move without any owner present)
// defines "ownerdrivetogether": the car is "moving" with both "owner[0]" and "owner[1]" being "in" (testing if both owners can drive together)
// defines "keylockinside": the "key" is "incar" and the "door" is "lock" while neither "owner[0]" nor "owner[1]" is "in" (testing if the key can be inadvertently locked inside)
// defines "drivewithoutengineon": the car is "moving" while the "engine" is "off" (testing if the car can move without the engine running)
// defines "drivewithoutfuel": the car is "moving" while "fuel" is 0 (testing if the car can move without fuel)
// defines "drivewithoutkeyholdbyother": the car is "moving" with "owner[1]" "in", "owner[0]" "far", and "key" with owner 0 (testing if the car can be driven by owner 1 when the proper key holder owner 0 is not in the car)

// assert that the "car" system is deadlock free, meaning it will not get stuck in a state where no further moves are possible and has not terminated successfully
// assert in LTL that the "car" system always eventually performs the action "longdrive.0" (i.e., it is always the case that longdrive.0 will eventually occur)
// assert that the "car" system can reach the state "keylockinside"
// assert that the "car" system can reach the state "runwithoutowner"
// assert that the "car" system can reach the state "ownerdrivetogether"
// assert that the "car" system can reach the state "drivewithoutengineon"
// assert that the "car" system can reach the state "drivewithoutfuel"
// assert that the "car" system can reach the state "drivewithoutkeyholdbyother"
**Expected Output:** #define N 2;        

#define far 0;      
#define near 1;     
#define in 2;       

#define off 0;      
#define on 1;       

#define unlock 0;   
#define lock 1;     
#define open 2;     

#define incar -1;    
#define faralone -2; 

var owner[N];       
var engine = off;   
var door = lock;    
var key = 0;        
var moving = 0;     
var fuel = 10;      

owner_pos(i) = 
          [owner[i] == far]towards.i{owner[i] = near;} -> owner_pos(i) 
          [] 
          [owner[i] == near]goaway.i{owner[i] = far;} -> owner_pos(i) 
          [] 
          [owner[i] == near && door == open && moving == 0]getin.i{owner[i] = in;} -> owner_pos(i) 
          [] 
          [owner[i] == in && door == open && moving == 0]goout.i{owner[i] = near;} -> owner_pos(i);

key_pos(i) = 
          [key == i && owner[i] == in]putincar.i{key = incar;} -> key_pos(i)  
          [] 
          [key == i && owner[i] == far]putaway.i{key = faralone;} -> key_pos(i) 
          [] 
          [(key == faralone && owner[i] == far) || (key == incar && owner[i] == in)]getkey.i{key = i;} -> key_pos(i);

door_op(i) = 
          [key == i && owner[i] == near && door == lock && moving == 0]unlockopen.i{door = open;} -> door_op(i) 
          [] 
          [owner[i] == near && door == unlock && moving == 0]justopen.i{door = open;} -> door_op(i) 
          [] 
          [door != open && owner[i] == in]insideopen.i{door = open;} -> door_op(i) 
          [] 
          [door == open]close.i{door = unlock;} -> door_op(i) 
          [] 
          [door == unlock && owner[i] == in]insidelock.i{door = lock;} -> door_op(i) 
          [] 
          [door == unlock && owner[i] == near && key == i]outsidelock.i{door = lock;} -> door_op(i);

motor(i) = 
          [owner[i] == in && (key == i || key == incar) && engine == off && fuel != 0]turnon.i{engine = on;} -> motor(i) 
          [] 
          [engine == on && owner[i] == in && moving == 0]startdrive.i{moving = 1;} -> motor(i) 
          [] 
          [moving == 1 && fuel != 0]shortdrive.i{fuel = fuel - 1; if (fuel == 0) { engine = off; moving = 0; }} -> motor(i) 
          [] 
          [moving == 1 && fuel > 5]longdrive.i{fuel = fuel - 5; if (fuel == 0) { engine = off; moving = 0; }} -> motor(i) 
          [] 
          [engine == on && moving == 1 && owner[i] == in]stop.i{moving = 0;} -> motor(i) 
          [] 
          [fuel == 0 && engine == off]refill{fuel = 10;} -> motor(i) 
          [] 
          [engine == on && moving == 0 && owner[i] == in]turnoff.i{engine = off;} -> motor(i);

car = (|||i:{0..N-1} @ (motor(i) ||| door_op(i) ||| key_pos(i) ||| owner_pos(i))); 

#define runwithoutowner (moving == 1 && owner[0] == far && owner[1] == far); 
#define ownerdrivetogether (moving == 1 && owner[0] == in && owner[1] == in); 
#define keylockinside (key == incar && door == lock && owner[0] != in && owner[1] != in); 
#define drivewithoutengineon (moving == 1 && engine == off); 
#define drivewithoutfuel (moving == 1 && fuel == 0); 
#define drivewithoutkeyholdbyother (moving == 1 && owner[1] == in && owner[0] == far && key == 0);

#assert car deadlockfree; 
#assert car |= []<> longdrive.0; 
#assert car reaches keylockinside; 
#assert car reaches runwithoutowner; 
#assert car reaches ownerdrivetogether; 
#assert car reaches drivewithoutengineon; 
#assert car reaches drivewithoutfuel; 
#assert car reaches drivewithoutkeyholdbyother;

Given the general system description: The user would like to build a system called motor_demo, where A simplified engine-only car system with binary fuel.. There are 1 processes in the system, the descriptions of the processes are as follows: motor: This process models an engine-only car. The engine has states ENGINE_OFF and ENGINE_ON; the car has states CAR_STOPPED and CAR_MOVING; fuel has states FUEL_EMPTY and FUEL_FULL. Initially the engine is off, the car is stopped, and fuel is full. start_engine requires the engine to be off and fuel to be full, and turns the engine on. stop_engine requires the engine to be on and the car to be stopped, and turns the engine off. start_driving requires the engine to be on, the car to be stopped, and fuel to be full, and makes the car move. stop_driving requires the car to be moving and makes it stop. consume_fuel requires the car to be moving and fuel to be full; it makes fuel empty and stops the car in the same action. refuel requires the car to be stopped and fuel to be empty, and makes fuel full., now generate the PAT code corresponding to the **following system annotation**. Refer to the system description **only** if explicitly guided in the annotation, or if there is a contradiction between the annotation and the description.

### System Annotation:
// "ENGINE_OFF": the engine is off (set to 0).
// "ENGINE_ON": the engine is on (set to 1).
// "CAR_STOPPED": the car is stopped (set to 0).
// "CAR_MOVING": the car is moving (set to 1).
// "FUEL_EMPTY": the fuel is empty (set to 0).
// "FUEL_FULL": the fuel is full (set to 1).
// "engine": integer variable recording the engine state (initially ENGINE_OFF).
// "car": integer variable recording whether the car is moving (initially CAR_STOPPED).
// "fuel": integer variable recording the binary fuel state (initially FUEL_FULL).
// Definition of the "motor" subsystem.
// If "engine" is "ENGINE_OFF" and "fuel" is "FUEL_FULL", action "start_engine" makes "engine" become "ENGINE_ON".
// If "engine" is "ENGINE_ON" and "car" is "CAR_STOPPED", action "stop_engine" makes "engine" become "ENGINE_OFF".
// If "engine" is "ENGINE_ON", "car" is "CAR_STOPPED", and "fuel" is "FUEL_FULL", action "start_driving" makes "car" become "CAR_MOVING".
// If "car" is "CAR_MOVING", action "stop_driving" makes "car" become "CAR_STOPPED".
// If "car" is "CAR_MOVING" and "fuel" is "FUEL_FULL", action "consume_fuel" makes "fuel" become "FUEL_EMPTY" and "car" become "CAR_STOPPED" in the same action.
// If "car" is "CAR_STOPPED" and "fuel" is "FUEL_EMPTY", action "refuel" makes "fuel" become "FUEL_FULL".

// define the motor_demo system: overall system combining all the subsystems interleavingly ("|||"), consider defining the system around the most central variable (e.g., the number of owners) only if appropriate
// define **exactly** the following states and assertions as specified. Do **NOT** add, modify, or omit anything
// assert motor_demo system deadlockfree
// define drivewithoutfuel: the car is moving while the fuel is empty
// assert that the motor_demo system can reach the state "drivewithoutfuel"

The PAT code should be:
### Response:
````

</details>

等价调用和实际 API 参数：

```python
generated_code_output = _get_claude_code_completion(
    prompt_text=final_code_gen_prompt,
    history_file_path="./history/claude-code.json"
)
response = client_claude.messages.create(
    model="claude-3-7-sonnet-20250219",
    max_tokens=8192,
    messages=[{"role": "user", "content": final_code_gen_prompt}]
)
```

### 输入的解释

前六项是代码用于组装 prompt 的变量或文字部分，不是额外的 API 请求字段。

| 输入字段或文字部分 | 类型 | 含义 | 在最终输入中的位置 |
|---|---|---|---|
| `syntax_general_info` | 字符串 | PAT 通用说明 | `General Information` |
| `syntax_pitfalls_rules` | 字符串 | 归档中的语法提示 | `Pitfalls and Syntax Guidelines` |
| `retrieved_nl` | 字符串 | 参考示例的文字注释 | `Input NL Annotation`，不是当前模型 |
| `retrieved_code` | 字符串 | 与参考注释配套的 CSP# | `Expected Output`，不是本次已经生成的结果 |
| `system_description_for_prompt` | 字符串 | 当前 `motor_demo` 的原始整体及子系统描述 | `Given the general system description` |
| `full_nl_prompt` | 字符串 | 当前完整计划，含数据、动作、系统和检查说明 | `System Annotation` |
| `prompt_text` | 字符串 | 包装函数的完整输入正文 | 真正送入 `messages[0].content` |
| `history_file_path` | 字符串 | 保存回复的文件位置 | 仅 Python 内部使用，不发送给 Claude |
| `model` | 字符串 | 旧源码固定的 Claude 模型 ID | API 参数，不是模型业务名称 `motor_demo` |
| `max_tokens` | 整数 | 最大输出 token 数，此处设为 8192 | API 参数，不保证一定输出这么长 |
| `messages` | 对象数组 | 本次调用的消息列表 | API 参数；只有一条 user 消息 |
| `messages[].role` | 字符串 | 消息身份 `user` | API 消息字段 |
| `messages[].content` | 字符串 | 上面完整展开的 prompt | API 消息字段；没有另发 `context` 或完整历史对话 |

### 输出

下面是说明性的完整 CSP# 输出。本例假设 Claude 回复只包含这一个 Markdown 代码块；后处理会移除围栏，提取内部代码。

<!-- example:CSP -->
```csp
#define ENGINE_OFF 0;
#define ENGINE_ON 1;
#define CAR_STOPPED 0;
#define CAR_MOVING 1;
#define FUEL_EMPTY 0;
#define FUEL_FULL 1;

var engine = ENGINE_OFF;
var car = CAR_STOPPED;
var fuel = FUEL_FULL;

motor() =
    ([engine == ENGINE_OFF && fuel == FUEL_FULL]
        start_engine{engine = ENGINE_ON;} -> motor())
    []
    ([engine == ENGINE_ON && car == CAR_STOPPED]
        stop_engine{engine = ENGINE_OFF;} -> motor())
    []
    ([engine == ENGINE_ON && car == CAR_STOPPED && fuel == FUEL_FULL]
        start_driving{car = CAR_MOVING;} -> motor())
    []
    ([car == CAR_MOVING]
        stop_driving{car = CAR_STOPPED;} -> motor())
    []
    ([car == CAR_MOVING && fuel == FUEL_FULL]
        consume_fuel{fuel = FUEL_EMPTY; car = CAR_STOPPED;} -> motor())
    []
    ([car == CAR_STOPPED && fuel == FUEL_EMPTY]
        refuel{fuel = FUEL_FULL;} -> motor());

motor_demo = motor();

#define drivewithoutfuel (car == CAR_MOVING && fuel == FUEL_EMPTY);

#assert motor_demo deadlockfree;
#assert motor_demo reaches drivewithoutfuel;
```

### 输出的解释

CSP# 是代码文本，没有 `processes/actions/conditions` 这些 JSON 键。对应关系如下：

| 代码组成 | 作用 | 从什么输入变过来 |
|---|---|---|
| 六条 `#define` 常量 | 为状态定义固定编码 | 常量注释中的名字、含义和数值 |
| `var engine/car/fuel` | 声明会变化的变量并设置初值 | 变量注释中的名字及初值 |
| `motor()` | 定义发动机子系统进程 | 动作注释的 subsystem 名和行为集合 |
| `[engine == ... && fuel == ...]` 等 | 动作分支的守卫条件 | 动作 JSON 的 `conditions`，经过动作注释表达 |
| `start_engine` 等六个事件名 | 表示具体发生的动作 | 动作 JSON 的 `action_name` |
| `{engine = ENGINE_ON;}` 等 | 事件中的数据更新 | 动作 JSON 的 `state_changes`，经过动作注释表达 |
| `consume_fuel` 中两个赋值 | 在一个事件中更新油量与移动状态 | “同一个动作使油变空并停车”的说明 |
| `-> motor()` | 完成动作后回到进程，继续选择后续动作 | Claude 根据持续行为生成的控制结构；JSON 里没有一个叫 `nextProcess` 的字段 |
| 分支间的 `[]` | 在当前可执行分支中选择一个执行 | Claude 用来组织动作集合；**不等于本例的 interactionMode 改成 choice** |
| `motor_demo = motor();` | 定义整体系统；本例只有一个子系统 | 总系统组合说明；多个子系统时可用 `|||` |
| `#define drivewithoutfuel ...` | 定义目标状态谓词 | `stateName/customDescription` 形成的状态文字说明 |
| `#assert motor_demo deadlockfree;` | 要求 PAT 检查无死锁 | 第一条输入断言的说明 |
| `#assert motor_demo reaches drivewithoutfuel;` | 要求 PAT 检查目标谓词是否可达 | 第二条输入断言的说明 |

Claude helper 读取 `response.content[0].text`，然后构造日志：

```python
interaction = {
    "timestamp": "2026-09-13 10:00:50",  # 示意时间
    "question": final_code_gen_prompt,
    "answerClaude": answer,              # 本例为包有 Markdown 围栏的完整 CSP# 回复
    "PAT": ""
}
# 追加保存到 ./history/claude-code.json
```

| 回复或日志字段 | 类型 | 含义 |
|---|---|---|
| `response.content` | 内容块列表 | Claude SDK 返回的消息内容；旧代码只读取第一块 |
| `response.content[0].text` | 字符串 | 旧代码实际取得的回复正文 |
| `timestamp` | 字符串 | 生成回复的记录时间 |
| `question` | 字符串 | 实际发送给 Claude 的完整 prompt |
| `answerClaude` | 字符串 | 原始回复，可能带解释文字、围栏或多个代码块 |
| `PAT` | 字符串 | 旧结构保留字段，此时为空；不是 PAT 验证已经通过 |

### 细节补充

- 上面的代码是示例，不是实测模型回复，更不是 PAT 的验证结论。真正调用 Claude 时可能生成不同的变量命名、进程结构或错误代码。
- 本次输入没有把 `assertionTruth="Invalid"` 作为答案要求传给 Claude。它应该生成“坏状态是否可达”的检查，而不是为了满足期望答案删除行为或把断言改成另一条性质。
- 从 JSON 到文字，再到代码，并不是一个由确定性规则保证等价的编译过程。进程结构、守卫表达式、数据更新和谓词表达式都需要 Claude 正确理解文字。
- helper 返回回复字符串，同时写 history；但 `gen_code()` 没有显式返回该字符串，主程序仍从 history 回读。如果调用失败，旧控制流没有可靠地避免读到之前的最后一条记录。
- 这一步首次调用之前，Planning LLM 共调用四次：提数据、提动作、写数据注释、写动作注释。此处 Claude 是第 5 次 LLM 调用；后面生成重试、返修产生的调用不在本文范围。
- 计时键形如 `codegen-time_2026-09-13-10-00-50.runTime`，由 `gen_code()` 记录，不属于 Claude 回复字段。

---

## Stage 5.后处理｜code｜回读回复、提取最长代码块并保存

### 简述作用

从 Claude history 回读最后一条回复；用正则匹配 Markdown 代码块，选字符数最多的一块；保存原始回复和提取出的 CSP#。

源码位置：主程序第 1100–1143 行；提取函数第 956–981 行。

### 输入

`./history/claude-code.json` 中最后一条 `answerClaude`。

### 输入的解释

输入是完整回复字符串，可能包含说明文字及多个代码块，不保证全是 CSP#。

### 输出

- `retrieved_generated_code`：原始回复。
- `longest_code_block`：本例 Stage 5.4 的完整 CSP#，去掉 Markdown 围栏。
- `original_llm_response.txt`：原始回复文本。
- `extracted_code.csp`：提取的候选代码。

### 输出的解释

两个文件保存到源码构造的模型目录：

```text
path_to_your_project_directory/Automated_Pipelines/Full_Pipeline/generated_code/motor_demo/
    original_llm_response.txt
    extracted_code.csp
```

这是**旧代码的占位路径表达**，不是声明当前项目一定有这个实际目录，也不是当前 `results/MMDD-HHMM-00X` 的设计。本文未修改路径、未创建这些运行产物。

### 细节补充

找不到代码围栏时，函数返回整段去掉首尾空白的文本；选中最长代码块不代表它语法正确，也不代表它是想要的模型。本文到提取结束为止：下一步才进入 PAT 验证。

---

## 最后对照：输入形态如何一路变化

| 数据 | 起始形态 | 中间形态 | 最终形态 |
|---|---|---|---|
| 子系统 | `subsystems[].name/description` | `processName` + 该进程的注释 | CSP# 的进程定义 |
| 常量 | 描述中的命名状态 | `constants[]` → 每项一行注释 | `#define ENGINE_OFF 0;` 等 |
| 变量 | 描述中的会变化的数据 | `variables[]` → 变量含义和初值的注释 | `var engine = ENGINE_OFF;` 等 |
| 动作 | 描述中的行为 | `actions[]` → 条件、动作、变化的逐行说明 | 守卫 + 事件 + 更新 + 后续进程 |
| 组合模式 | `interactionMode` | 代码生成的系统组合说明 | 整体系统进程，多个交错子系统时使用 `|||` |
| 状态谓词 | `stateName/customDescription` | 代码生成的状态含义说明 | `#define drivewithoutfuel (...);` |
| 检查要求 | 输入 `assertions[]` | 代码生成的断言说明 | `#assert ...` |
| 期望结果 | 输入 `assertionTruth` | 本段不放入生成 prompt | 留给后续 PAT 验证结果比较，**不是 CSP# 中的控制规则** |

## 文件与返回值速查

| 文件/变量 | 保存内容或作用 | 下一步如何取得 |
|---|---|---|
| `PAT.json` | 任务对象数组 | 主程序加载并遍历 |
| `const-history.json` | 时间、提数据 prompt、JSON 回复字符串、原始任务 context | 取最后一条 `answerGPT` |
| `action-history.json` | 时间、提动作 prompt、JSON 回复字符串、常量变量 context | 取最后一条 `answerGPT` |
| `data1_interaction/data2_interaction` | 两次注释调用的返回对象 | 直接取各自 `answerGPT`；包装函数不写 history |
| `nl-instruction-part.json` | 时间及 `data1/data2/data3` 三段文字 | 主要用于保留分段计划；不是 Claude 直接加载整个文件 |
| `nl-instruction-claude.json` | 时间及完整 `fullText` | 主程序取最后一条 `fullText` |
| `database-rag-claude.json` | 参考注释和参考代码配对 | TF-IDF/余弦相似度选出一条 |
| `syntax-dataset.json` | `general_info/pitfalls_rules` 提示文字 | 字符串读取并拼接 |
| `claude-code.json` | 时间、完整 Claude prompt、原始回复、空 `PAT` 字段 | 主程序取最后一条 `answerClaude` |
| `run_time_record/motor_demo.json` | 各步骤的耗时记录 | 不作为下一次 LLM 的输入 |
| `original_llm_response.txt` | 原始 Claude 回复 | 留档 |
| `extracted_code.csp` | 提取后的候选 CSP# | 后续交给 PAT 验证；本文不继续执行 |

**一句话总结：四次 Planning LLM 将描述变成数据、动作和两段注释；代码读取既有断言并补齐说明、检索和组装；Claude 再将全部材料变成 CSP#。**
