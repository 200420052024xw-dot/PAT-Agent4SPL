# ControllerIR 到 CSP_IR 的字段来源与转换方式

## 1. 总体原则

`controller_ir`提供业务事实和控制流程；LLM只补充其中没有结构化表达的语义；代码负责提取、转换、编号、校验并构造最终`CSP`数据模型。

获取方式分为三类：

- **代码**：来源明确，可以按固定规则转换。
- **LLM**：ControllerIR只有自然语言，没有结构化答案。
- **代码＋LLM**：代码准备输入，LLM完成语义判断，代码再检查结果。

| CSP_IR部分 | ControllerIR中的主要来源 | 转换方式 |
|---|---|---|
| `constants` | `slot_information`、所有条件表达式、`policies` | 代码＋LLM |
| `variables` | `slot_information`、`state_schema` | 代码＋LLM |
| `process` | `stages` | 代码 |
| `transitions` | `actions`、阶段的`transition`和`termination` | 代码＋LLM |
| `system` | 初始阶段和进程组合关系 | 代码 |
| `predicates` | 策略、终止条件和需要检查的业务状态 | 代码＋LLM |
| `assertions` | 基础检查规则和业务检查要求 | 代码＋LLM |

## 2. constants

| CSP_IR字段 | 来源 | 获取方式 | 具体转换 |
|---|---|---|---|
| `constants` | `slot_information`、所有条件表达式、`policies` | 代码＋LLM | 代码提取明确枚举；LLM补充内容状态的有限化方式 |
| `constants[].name` | `enum_values`、条件中的固定值、抽象后的状态 | 代码 | 按统一规则生成大写名称 |
| `constants[].description` | 槽说明、枚举值、条件含义 | 代码模板 | 使用“某槽处于某状态”的模板生成 |
| `constants[].value` | 没有直接来源 | 代码 | 从0开始为常量分配整数编号 |

例如：

```json
"enum_values": [
  "newsletter",
  "exec_brief"
]
```

可以由代码生成：

```jsonc
[
  {
    "name": "COMM_TYPE_UNSET",
    "description": "尚未确定沟通文档类型",
    "value": 0
  },
  {
    "name": "COMM_TYPE_NEWSLETTER",
    "description": "沟通文档类型为 newsletter",
    "value": 1
  },
  {
    "name": "COMM_TYPE_EXEC_BRIEF",
    "description": "沟通文档类型为 exec_brief",
    "value": 2
  }
]
```

字符串和列表不能直接全部放入有限状态模型。例如：

```text
email_keywords == []
email_keywords != []
```

这说明控制流程只区分“空”和“非空”，因此代码可以生成：

```text
EMAIL_KEYWORDS_EMPTY
EMAIL_KEYWORDS_NONEMPTY
```

但是策略中有这种自然语言：

```text
草稿必须使用英文并且不超过300个单词
```

它无法通过普通代码直接转换。需要让LLM判断应抽象成：

```text
POLICY_NOT_CHECKED
POLICY_ALLOW
POLICY_DENY
```

LLM需要的输入是：

```text
目标
相关槽的名称、类型和说明
policies的激活条件、说明和参数
所有引用policy_feedback的条件
```

## 3. variables

| CSP_IR字段 | 来源 | 获取方式 | 具体转换 |
|---|---|---|---|
| `variables` | `slot_information`、`state_schema`、条件中引用但没有声明的状态 | 代码＋LLM | 已声明槽由代码转换；缺失状态由LLM识别 |
| `variables[].name` | 槽名或状态名 | 代码 | 槽直接使用原名；嵌套状态展开为扁平名称 |
| `variables[].description` | 槽的`description`、状态的`description` | 代码 | 直接复制 |
| `variables[].type` | 槽类型、实际使用方式 | 代码 | 转换为CSP_IR支持的有限类型 |
| `variables[].size` | 固定长度数组的长度 | 代码；缺失时不能猜 | 当前ControllerIR没有数组长度，应优先使用状态抽象 |
| `variables[].element_type` | 数组元素类型 | 代码；缺失时不能猜 | 当前例子通常不需要真正数组 |
| `variables[].options` | 对应常量名称 | 代码 | 填入该变量允许使用的常量名 |
| `variables[].initial_value` | `default_value`或状态的`default` | 代码 | 把原始默认值转换成对应常量或布尔值 |

### 3.1 变量名称

普通槽直接生成变量：

```text
comm_type → comm_type
email_time_range → email_time_range
```

`state_schema`中的嵌套状态需要展开：

```text
newsletter_draft.presented
→ newsletter_draft_presented

newsletter_draft.revised
→ newsletter_draft_revised
```

### 3.2 变量类型

不能只看槽声明的类型，还要看控制流程如何使用它。

| ControllerIR中的数据 | 实际判断方式 | CSP变量表示 |
|---|---|---|
| `comm_type` | 判断具体枚举值 | `int`加枚举常量 |
| `email_time_range` | 只判断是否为`null` | `int`，取`UNSET/SET` |
| `email_keywords` | 只判断是否为`[]` | `int`，取`EMPTY/NONEMPTY` |
| `email_evidence` | 只判断是否为空 | `int`，取`EMPTY/NONEMPTY` |
| `newsletter_draft` | 只判断是否为`null` | `int`，取`ABSENT/PRESENT` |
| `presented` | 判断`true/false` | `bool` |
| `revised` | 判断`true/false` | `bool` |

### 3.3 缺失变量

ControllerIR中出现了：

```text
input.policy_feedback
```

但它没有在`slot_information`中声明。

代码可以发现“该路径被引用但没有声明”，然后把以下信息交给LLM：

```text
所有包含policy_feedback的条件
全部policies
相关阶段
相关动作
现有变量列表
```

LLM判断它代表：

```text
策略尚未检查
策略允许
策略拒绝
```

然后代码生成类似：

```jsonc
{
  "name": "policy_feedback",
  "description": "策略检查结果",
  "type": "int",
  "size": null,
  "element_type": null,
  "options": [
    "POLICY_NOT_CHECKED",
    "POLICY_ALLOW",
    "POLICY_DENY"
  ],
  "initial_value": [
    "POLICY_NOT_CHECKED"
  ]
}
```

## 4. process

建议把每个`stage`转换成一个CSP进程。

| CSP_IR字段 | 来源 | 获取方式 | 具体转换 |
|---|---|---|---|
| `process` | `stages` | 代码 | 每个阶段生成一个进程 |
| `process[].name` | `stage_name` | 代码 | 直接使用阶段名称 |
| `process[].description` | `stage_goal`和`stage_guidance` | 代码 | 合并为进程说明 |
| `process[].parameters` | ControllerIR目前没有对应信息 | 代码 | 当前统一生成空列表 |

例如：

```jsonc
"collect_requirements": {
  "stage_name": "collect_requirements",
  "stage_goal": "确定用户需要的沟通文档类型……"
}
```

转换为：

```jsonc
{
  "name": "collect_requirements",
  "description": "确定沟通文档类型并收集完整的检索条件。",
  "parameters": []
}
```

当前的参与者：

```text
console
search_gmail
search_google_docs
generate_newsletter
```

不应该直接转换成进程。它们是动作的执行者，在CSP模型中更适合作为事件来源。

## 5. transitions

这里有三种来源：

1. `actions`生成动作转移。
2. `stages[].transition`生成阶段转移。
3. `stages[].termination`生成结束转移。

### 5.1 转移公共字段

| CSP_IR字段 | 来源 | 获取方式 | 具体转换 |
|---|---|---|---|
| `transitions[].process` | 当前动作所属阶段，或者当前`stage_name` | 代码 | 写当前进程名 |
| `transitions[].guard` | 动作、阶段转移或终止条件的`activate_condition` | 代码 | 把ControllerIR条件翻译成CSP表达式 |
| `transitions[].event` | 动作名、阶段转移名或终止事件名 | 代码 | 使用原动作名或生成固定事件名 |
| `transitions[].effects` | `state_schema`、动作的自然语言说明、阶段指导 | 代码＋LLM | 明确更新由代码提取；隐含更新由LLM判断 |
| `next_process.process_name` | 目标阶段；动作完成后通常仍是当前阶段 | 代码 | 从`target_stage_name`获取 |
| `next_process.arguments` | 目标进程参数 | 代码 | 当前全部为空列表 |

### 5.2 动作属于哪个进程

例如：

```text
input.current_stage == "retrieve_evidence"
```

代码可以确定：

```text
SEARCH_GMAIL属于retrieve_evidence进程
```

转换时，`current_stage`条件不用再放入`guard`，因为`process`字段已经表达了当前阶段。

原条件：

```text
current_stage == retrieve_evidence
并且email_time_range不为空
并且email_keywords不为空
并且email_evidence为空
```

转换后：

```jsonc
{
  "process": "retrieve_evidence",
  "guard": "email_time_range == EMAIL_TIME_RANGE_SET && email_keywords == EMAIL_KEYWORDS_NONEMPTY && email_evidence == EMAIL_EVIDENCE_EMPTY",
  "event": "SEARCH_GMAIL"
}
```

### 5.3 guard

| 原始结构 | CSP表达式 | 获取方式 |
|---|---|---|
| `all_of` | `A && B && C` | 代码 |
| `any_of` | `(A \|\| B \|\| C)` | 代码 |
| 嵌套条件 | 按原层级加括号 | 代码 |
| `input.slots.x` | `x` | 代码 |
| `input.slot_states.a.b` | `a_b` | 代码 |
| `null` | 对应的`UNSET/ABSENT`常量 | 代码 |
| `[]` | 对应的`EMPTY`常量 | 代码 |
| `contains(policy_feedback, "DENY")` | `policy_feedback == POLICY_DENY` | 代码，在抽象变量确定后转换 |

条件结构本身不需要交给LLM重新编写，否则LLM可能改变原条件的逻辑。

### 5.4 effects

这是转换中最需要LLM的部分。

有些更新已经结构化，可以由代码直接获取：

```jsonc
{
  "触发条件": {
    "类型": "action_succeeded",
    "动作": "PRESENT_RESULT"
  },
  "设为": true
}
```

代码可以生成：

```text
newsletter_draft_presented = true
```

但是下面这些更新只写在自然语言中：

```text
SEARCH_GMAIL将结果写入email_evidence
SEARCH_GOOGLE_DOCS将结果写入docs_evidence
GENERATE_NEWSLETTER生成newsletter_draft
REVISE_NEWSLETTER修改newsletter_draft
```

这些需要LLM提取。

每个动作单独调用LLM，输入：

```text
当前action的完整内容
动作所属stage的stage_goal
动作所属stage的stage_guidance
全部slot_information
全部state_schema
对应participant的description
已经生成的变量及其options
```

要求LLM只输出：

```json
{
  "effects": [
    {
      "variable": "email_evidence",
      "value": "EMAIL_EVIDENCE_NONEMPTY"
    }
  ]
}
```

代码随后检查：

- `variable`必须已经声明。
- `value`必须属于该变量的`options`。
- 不允许LLM创建新变量。
- 不允许LLM修改原始guard。

例如`SEARCH_GMAIL`可以生成：

```jsonc
"effects": [
  {
    "variable": "email_evidence",
    "value": "EMAIL_EVIDENCE_NONEMPTY"
  }
]
```

### 5.5 动作完成后去哪里

建议把“执行动作”和“切换阶段”分开。

动作执行后先留在当前进程：

```jsonc
{
  "process": "retrieve_evidence",
  "event": "SEARCH_GMAIL",
  "effects": [
    {
      "variable": "email_evidence",
      "value": "EMAIL_EVIDENCE_NONEMPTY"
    }
  ],
  "next_process": {
    "process_name": "retrieve_evidence",
    "arguments": []
  }
}
```

等阶段转移条件满足后，再执行：

```jsonc
{
  "process": "retrieve_evidence",
  "guard": "email_evidence == EMAIL_EVIDENCE_NONEMPTY && docs_evidence == DOCS_EVIDENCE_NONEMPTY",
  "event": "to_generate_document",
  "effects": [],
  "next_process": {
    "process_name": "generate_document",
    "arguments": []
  }
}
```

这样PAT可以检查动作执行顺序以及错误分支。

### 5.6 终止条件

`termination.activate_condition`转换成一条通往`Skip`的转移：

```jsonc
{
  "process": "present_draft",
  "guard": "newsletter_draft_presented == true && policy_feedback == POLICY_ALLOW",
  "event": "complete",
  "effects": [],
  "next_process": {
    "process_name": "Skip",
    "arguments": []
  }
}
```

全部由代码生成。

## 6. system

| CSP_IR字段 | 来源 | 获取方式 | 具体转换 |
|---|---|---|---|
| `system.name` | ControllerIR文件名或固定命名规则 | 代码 | 例如`InternalCommsSystem` |
| `system.composition` | 控制器整体结构 | 当前例子由代码确定 | 当前只有一条阶段状态机，使用`single` |
| `system.processes` | `is_initial_stage == true`的阶段 | 代码 | 系统只从初始进程启动 |
| `system.processes[].process` | 初始阶段的`stage_name` | 代码 | 本例为`collect_requirements` |
| `system.processes[].arguments` | 初始进程参数 | 代码 | 当前为空列表 |

本例应当是：

```jsonc
{
  "name": "InternalCommsSystem",
  "composition": "single",
  "processes": [
    {
      "process": "collect_requirements",
      "arguments": []
    }
  ]
}
```

不能把全部阶段都放进`system.processes`，否则就可能表示所有阶段同时启动。后面的阶段应通过`next_process`到达。

对于一般情况，当前ControllerIR没有明确记录：

```text
parallel
interleave
interrupt
```

因此可靠规则只能是：

```text
一个初始阶段状态机 → single
```

如果以后确实存在多个并行控制器，需要在ControllerIR中增加明确的系统组合信息。仅让LLM根据自然语言猜并发关系，不能保证正确。

## 7. predicates

谓词主要来自三类内容：

1. 终止条件。
2. 策略要求。
3. LLM识别出的关键业务状态。

| CSP_IR字段 | 来源 | 获取方式 | 具体转换 |
|---|---|---|---|
| `predicates[].name` | 终止状态、策略名或业务状态 | 代码＋LLM | 结构化条件由代码命名；业务状态由LLM命名 |
| `predicates[].description` | 终止说明、策略说明、阶段目标 | 代码或LLM | 有原文则复制；组合状态由LLM说明 |
| `predicates[].expression` | 终止条件、策略结果、变量组合 | 代码＋LLM | 结构化条件直接翻译；自然语言要求由LLM生成 |

### 7.1 可以直接生成的谓词

终止条件可以由代码转换成：

```jsonc
{
  "name": "task_completed",
  "description": "草稿已展示并且通过策略检查",
  "expression": "newsletter_draft_presented == true && policy_feedback == POLICY_ALLOW"
}
```

### 7.2 需要LLM生成的谓词

例如需要检查：

```text
被策略拒绝的草稿不应该进入完成状态
```

LLM输入：

```text
系统目标
全部变量和options
全部阶段及阶段转移
全部动作的guard和effects
全部policies
已经生成的谓词
```

LLM只能使用已有变量，输出：

```json
{
  "name": "denied_draft_completed",
  "description": "策略拒绝的草稿进入了完成状态",
  "expression": "policy_feedback == POLICY_DENY && newsletter_draft_presented == true"
}
```

代码检查表达式中的所有变量和常量是否存在。

## 8. assertions

建议把断言分成“固定基础检查”和“LLM业务检查”。

### 8.1 公共字段

| CSP_IR字段 | 来源 | 获取方式 |
|---|---|---|
| `assertions[].type` | 检查种类 | 基础检查由代码确定，业务检查由LLM选择 |
| `assertions[].process` | `system.name` | 代码 |
| `assertions[].predicate` | 已生成的谓词名 | 代码或LLM选择 |
| `assertions[].formula` | LTL公式 | LLM生成，代码检查引用 |
| `assertions[].expected` | 性质是应该成立还是不应该成立 | 代码根据检查意图填写 |
| `assertions[].description` | 检查目的 | 代码模板或LLM生成 |

### 8.2 无死锁检查

完全由代码生成：

```jsonc
{
  "type": "deadlock_free",
  "process": "InternalCommsSystem",
  "predicate": null,
  "formula": null,
  "expected": "VALID",
  "description": "检查系统是否可能在未正常结束时无动作可执行"
}
```

### 8.3 正常结束状态是否可达

终止谓词已经由代码生成，因此也可以由代码生成：

```jsonc
{
  "type": "reachability",
  "process": "InternalCommsSystem",
  "predicate": "task_completed",
  "formula": null,
  "expected": "VALID",
  "description": "检查系统是否存在一条能够正常完成任务的执行路径"
}
```

### 8.4 禁止状态是否可达

LLM先根据业务含义提出禁止状态谓词，代码生成断言：

```jsonc
{
  "type": "reachability",
  "process": "InternalCommsSystem",
  "predicate": "denied_draft_completed",
  "formula": null,
  "expected": "INVALID",
  "description": "检查被策略拒绝的草稿是否可能进入完成状态"
}
```

### 8.5 LTL时序检查

LTL不能只根据字段机械生成，需要LLM理解动作之间应有的先后关系。

LLM输入：

```text
goal
stages及其顺序
actions的名称、说明、guard和effects
policies
生成后的变量
生成后的进程和转移
生成后的谓词
```

例如LLM可以提出：

```text
执行GENERATE_NEWSLETTER以后，最终应当展示草稿或修改草稿
```

输出：

```json
{
  "formula": "[](GENERATE_NEWSLETTER -> <>(PRESENT_RESULT || REVISE_NEWSLETTER))",
  "expected": "VALID"
}
```

代码需要检查：

- 公式中的事件必须存在。
- 公式中的谓词必须存在。
- 括号和时序操作符是否合法。
- LLM不能引用ControllerIR中不存在的动作。

## 9. 推荐的实际转换顺序

```text
第一步：代码读取并校验ControllerIR
        ↓
第二步：代码收集所有条件中使用到的槽、状态和固定值
        ↓
第三步：代码生成明确的常量和变量
        ↓
第四步：LLM补充自然语言业务数据的有限状态抽象
        ↓
第五步：代码生成阶段进程
        ↓
第六步：代码生成动作转移、阶段转移和结束转移
        ↓
第七步：LLM只推断动作中没有结构化记录的effects
        ↓
第八步：代码生成system
        ↓
第九步：代码生成终止谓词和基础断言
        ↓
第十步：LLM生成额外业务谓词和LTL检查
        ↓
第十一步：Pydantic校验完整CSP_IR
        ↓
第十二步：确定性代码把CSP_IR输出成CSP#
```

## 10. 当前数据模型需要修正的地方

目前`controller_ir/csp_ir.jsonc`和`data/model.py`并不一致：

| 问题 | JSONC | 当前model.py |
|---|---|---|
| 转移所属进程 | 字符串 | `Process`对象 |
| 下一进程 | `{process_name, arguments}` | `Process`对象 |
| 系统中的进程 | `{process, arguments}` | `Process`对象列表 |
| 谓词表达式 | `expression` | `expressions` |
| System说明 | JSONC中没有 | model中必填 |
| Variable类型 | 应有具体类型 | 目前只有`Literal[""]` |
| effects内部结构 | 尚未明确 | 当前是`list[Any]` |

合理的近期顺序是：

1. 确定`effects`的内部结构。
2. 修正Pydantic数据模型，使其与`csp_ir.jsonc`完全一致。
3. 实现常量和变量的代码转换。
4. 接入LLM生成缺失的状态抽象和动作效果。
