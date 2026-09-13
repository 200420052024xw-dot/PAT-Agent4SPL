# Controller IR 逐字段解释：通用结构、字段关系与转换含义

## 0. 这份文档怎样读

Controller IR 是用结构化数据描述控制器的方式：保存哪些数据、允许哪些操作、什么条件下执行、执行后怎样影响流程，以及什么时候结束。后续转成 CSP# 并交给 PAT 检查，需要理解的就是这些声明表达的行为。

本文以仓库中的 [`controller_ir.json`](../../controller_ir/controller_ir.json) 为依据，**先解释通用结构，再用当前实例说明**。实例中的邮件、草稿、六个动作和五个阶段，都是某个业务对这些结构的具体使用；换一份 IR，名字、数量和流程都可以不同。

正文按概念之间的依赖顺序展开，不照着 JSON 行号逐段翻译。当前实例的槽、参与者、配置、动作参数和阶段清单放在附录，便于回查。

当前仓库未找到完整的 Controller IR 规范、解释器或示例引用的 `program.handler.*` 实现。因此，下文区分“输入已声明的内容”和“通用转换仍需明确的规则”。样本出现的类型、事件和表达式，不代表完整规范只允许这些取值。

## 1. 全局结构：先知道各部分为什么存在

一个控制器可以先用六个问题理解：

| 问题 | 顶层字段 | 它描述什么 |
|---|---|---|
| 整体要完成什么？ | `goal` | 控制器的用途说明 |
| 需要保存哪些数据？ | `slot_information` | 数据槽的类型、初值及可附带的状态 |
| 操作交给谁执行？ | `participants` | 人或系统的接入定义 |
| 数据需要满足什么业务要求？ | `policies` | 业务检查的条件、要求和输入 |
| 当前在哪一步，接下来去哪？ | `stages` | 阶段、阶段过渡和正常结束条件 |
| 什么条件下可以做什么？ | `actions` | 动作、执行者、参数和执行核心 |

这些字段不是六份互不相关的配置。动作可以读取槽里的数据，交给某个参与者处理；动作成功后可以更新槽的附加状态，阶段条件再读取这个状态决定是否继续。

```text
数据值、附加状态、当前阶段、策略反馈
                  ↓ 条件读取这些信息
       动作是否可执行 / 是否可换阶段 / 是否可结束
                  ↓ 根据调度与执行规则发生行为
          数据或状态更新 / 阶段改变 / 正常结束
                  ↓
             形成新的运行状态
```

上图表达依赖关系。动作、检查、阶段过渡究竟按什么顺序调度，需要执行语义明确，不能仅从 JSON 的排列顺序得出。

### 1.1 结构字段与业务名字

以下是结构示意，尖括号内表示每份 IR 自己声明的名字：

```text
slot_information
└── <槽标识>
    ├── name
    ├── type
    └── default_value

actions
└── <动作标识>
    ├── activate_condition
    ├── executor → participants.<参与者标识>
    └── parameters
```

`type`、`executor` 是结构字段；`newsletter_draft`、`SEARCH_GMAIL`、`console` 是样本中的业务名字。通用转换器按结构读取声明，按名字解析引用，不能把这些具体名字写成内置规则。

### 1.2 定义与运行值

IR 说明“有哪些数据和规则”，控制器运行时还需要保存“现在的值”。样本通过以下路径关联二者：

| 定义位置 | 运行时读取位置 | 对应关系 |
|---|---|---|
| `slot_information.<S>` | `input.slots.<S>` | 槽的定义与当前数据值 |
| `slot_information.<S>.state_schema.<Q>` | `input.slot_states.<S>.<Q>` | 附加状态的定义与当前状态值 |
| `stages.<T>` | `input.current_stage` | 阶段定义与当前所在阶段 |
| `policies.<P>` | `input.policy_feedback` | 策略定义与运行反馈；反馈结构及逐策略对应关系尚未完整声明 |

`S/Q/T/P` 是本文的说明性占位符。`input` 是条件求值的运行上下文入口，不是新增的顶层 IR 字段。

## 2. goal：说明整体用途

`goal` 是字符串，回答“这个控制器用来完成什么任务”。它帮助人理解其他字段的业务背景。

自然语言目标没有直接给出可求值的完成条件。转换时，正常结束条件要从结构化规则读取；若要检查整体目标是否达成，还需要把目标明确表达成可验证的性质。

当前实例的 goal 提到内部通信工作流及其转换来源。这是背景说明，不是已经执行成功的记录。

## 3. slot_information：定义要保存的数据

### 3.1 一个槽是什么

一个槽就是一项有名字的业务数据，既可以由用户提供，也可以由工具产生。订单信息、检索结果、申请材料都可以用槽表达。

`slot_information` 是槽定义字典：每个键标识一个槽，对应对象说明该槽保存什么数据。

| 字段 | 样本中的形式 | 通用含义 |
|---|---|---|
| 槽的名字，例如 `comm_type` | 写在槽定义外面的名字 | 其他地方用这个名字找到该槽 |
| `name` | 字符串 | 槽名；应检查它与外层标识的一致性 |
| `description` | 字符串 | 数据的业务意义及内容要求 |
| `required` | 布尔值 | 声明该项信息是任务需要的 |
| `type` | 字符串 | 数据形式；样本出现 `enum`、`string`、`list` |
| `default_value` | 数据值或 `null` | 槽的初值 |
| `enum_values` | 字符串数组 | 枚举允许的取值 |
| `state_schema` | 对象 | 属于这个槽的附加状态定义，下一节解释 |

`required: true` 不代表初始已有值，也没有说明由谁填写或何时校验。数据怎样获得，需要结合动作和写回规则判断。

例如，样本的 `comm_type` 是枚举槽，允许 `newsletter/exec_brief`，初值为 `null`。通用处理是“从声明中读取枚举取值和初值”，换一个业务就读取另一组取值。

### 3.2 数据怎样进入形式模型

转换时需要建立槽对应的模型变量。对于字符串、列表等可能无限的数据，需要选择有限状态抽象，也就是只保留检查行为需要区分的信息。

例如，如果全部相关条件只判断列表是否为空，可以考虑用“空/非空”表示；如果条件还判断长度或包含哪个元素，就需要保留更多区别。不能仅因为数据类型是列表，就一律套用同一种抽象。

同样，字符串非 `null` 只说明已有值，不保证内容有效。`description` 中的自然语言要求，需要明确的检查机制才能影响执行或验证。

## 4. state_schema：给一个槽记录额外状态

先列全字段，下面用 JSON 逐个对照。状态的名字由业务自己起，例如 `presented`。

| 写在哪里 | 字段 | 是什么意思 | 例子 |
|---|---|---|---|
| 某个状态里面 | `description` | 解释这个状态记录什么 | 是否已经向用户展示 |
| 某个状态里面 | `type` | 状态值是什么类型 | `boolean`，即 true 或 false |
| 某个状态里面 | `default` | 开始时的值 | `false` |
| 某个状态里面 | `transitions` | 一组“什么时候改成什么”的规则 | 展示成功后改为 true |
| 每条更新规则里面 | `trigger` | 什么时候触发这条规则 | 等待指定动作成功 |
| `trigger` 里面 | `type` | 等待什么事件 | `action_succeeded`，动作成功 |
| `trigger` 里面 | `action` | 等待哪个动作 | `PRESENT_RESULT` |
| 每条更新规则里面 | `set_to` | 把所属状态改成什么值 | `true` |

### 4.1 先看它放在哪，记录什么

用当前草稿举例，只看相关部分：

```json
{
  "slot_information": {
    "newsletter_draft": {
      "type": "string",
      "default_value": null,
      "state_schema": {
        "presented": {
          "type": "boolean",
          "default": false
        }
      }
    }
  }
}
```

这段只说两件事：

- `newsletter_draft` 存草稿内容，是字符串，开始没有内容（`null`）。
- 它下面的 `presented` 记录是否展示过，是布尔值，开始没展示（`false`）。

所以两个 `type` 管的是两个值。草稿生成后可能还没展示：

```text
草稿内容 = "本周项目进展……"
是否展示 = false
```

成功展示后，正文可以不变，只改标记：

```text
草稿内容 = "本周项目进展……"
是否展示 = true
```

你觉得定义重复，是因为这里确实又用了一套“类型 + 初值”来定义变量。也可以把“是否展示”设计成独立的布尔槽；当前 IR 选择把它放在草稿下面，表示它属于这份草稿。下面还会给它写更新规则。

### 4.2 transitions 就是“什么时候把这个状态改成什么”

把上面的 `presented` 展开，它在原文件中是这样定义的：

```json
{
  "presented": {
    "description": "The generated document draft has been presented to the user for review.",
    "type": "boolean",
    "default": false,
    "transitions": [
      {
        "trigger": {
          "type": "action_succeeded",
          "action": "PRESENT_RESULT"
        },
        "set_to": true
      }
    ]
  }
}
```

整段翻成人话：**记录是否展示过，开始为 false；PRESENT_RESULT 动作成功后，改成 true。**

上面的 `description` 是说明文字。下面这块才是更新规则：

```json
{
  "trigger": {
    "type": "action_succeeded",
    "action": "PRESENT_RESULT"
  },
  "set_to": true
}
```

按两句话读：

- **什么时候改？** 看 `trigger`：`action` 指定 PRESENT_RESULT 动作，`type` 指定等它执行成功（action_succeeded）。
- **改成什么？** 看 `set_to`：改成 true。

**改谁？这条规则写在 `presented` 下面，所以改的就是 `presented`。** 等价于下面的伪代码：

```text
如果 PRESENT_RESULT 执行成功：
    presented = true
```

注意两处 `type`：`presented.type` 说变量是布尔值；`trigger.type` 说触发事件是“动作成功”。

### 4.3 为什么 transitions 外面是方括号？

因为一个状态可以有多条更新规则。比如原文件的 `revised`（是否返修过）：

```json
{
  "revised": {
    "type": "boolean",
    "default": false,
    "transitions": [
      {
        "trigger": {
          "type": "action_succeeded",
          "action": "REVISE_NEWSLETTER"
        },
        "set_to": true
      },
      {
        "trigger": {
          "type": "action_succeeded",
          "action": "GENERATE_NEWSLETTER"
        },
        "set_to": false
      }
    ]
  }
}
```

这里省略了 description。方括号里放了两条规则：

```text
返修动作成功 → revised = true
生成动作成功 → revised = false
```

它们是分别等待各自事件的规则，不是要求先返修、再生成的执行步骤。

### 4.4 后面的流程怎样用这些状态？

规则负责改值，条件负责读值。原文件用下面这个条件判断草稿还没展示：

```text
input.slot_states.newsletter_draft.presented == false
```

从左往右读：运行状态 → 槽的附加状态 → 草稿 → 是否展示 → 等于 false。

因此，本例把这些声明连成了：

```text
展示动作要求 presented 为 false
→ 展示动作成功
→ 更新规则把 presented 设为 true
→ 正常结束条件检查 presented 是否为 true
```

结束还要满足草稿存在、策略允许等其他条件。

换一个业务，这套结构仍然是在说：**某个槽的某个状态，在指定事件发生后改成指定值，供后面的条件读取。** 槽名、状态名、动作名都由输入提供。

这里的 `transitions` 更新状态值；后面 `stages` 里的 `transition` 则负责换阶段。名字相近，作用不同。

## 5. participants：定义操作交给谁执行

数据和状态由槽描述，实际操作则交给人或系统。`participants` 定义执行者，动作的 `executor` 引用其中一项。

| 字段 | 样本中的形式 | 含义 |
|---|---|---|
| 执行者的名字，例如 `console` | 写在执行者定义外面的名字 | 动作的 executor 用这个名字指定由谁执行 |
| `interactive_object` | 字符串 | 交互对象类别；样本出现 `human/traditional_system` |
| `description` | 字符串 | 能力与预期行为说明 |
| `interface` | 字符串 | handler 类或接口的定位路径 |
| `participant_config` | 对象 | 接入或初始化执行者需要的配置 |

```text
actions.<动作>.executor
          ↓
participants.<参与者>
          ├── interface：怎样定位接口
          └── participant_config：接口需要哪些配置
```

配置对象内部的键由接口契约决定。某个接口需要 API 地址，另一个可能需要文件路径，不能把当前样本的配置键当成所有参与者的固定字段。

行为验证需要知道执行者可能产生什么结果，以及结果怎样改变状态。接口路径和配置不等于这些行为的完整定义，需要实现或明确的接口契约支持。是否把某项配置纳入模型，取决于它是否影响待验证的行为。

样本中的 `traditional_system` 参与者也可能内部调用 LLM；这个类别名不能独自证明内部实现方式。

## 6. actions：定义允许执行的操作

### 6.1 一个动作包含什么

动作描述一次操作：什么时候允许做、交给谁做、需要什么参数、调用哪个执行核心。

| 字段 | 样本中的形式 | 含义 |
|---|---|---|
| 动作的名字，例如 `PRESENT_RESULT` | 写在动作定义外面的名字 | 其他地方用这个名字指定动作 |
| `name` | 字符串 | 动作名，应检查与外层标识的一致性 |
| `description` | 字符串 | 动作的业务含义 |
| `activate_condition` | 条件字符串或对象 | 什么状态下允许执行 |
| `executor` | 字符串 | 引用参与者 |
| `parameters` | 对象 | 动作参数的定义集合 |
| `execution_core` | 字符串 | 执行核心或操作标识 |

动作标识、参与者标识、执行核心标识分别表达“哪个动作”“由谁执行”“调用哪个操作”。名字可以不同，转换时按位置和引用关系读取，不按名字相似程度猜测。

### 6.2 parameters：需要什么参数，从哪里取值

当前样本的动作参数采用以下结构：

| 字段 | 含义 |
|---|---|
| 参数的名字，例如 `content` | 这个名字下面写该参数的类型、是否必需等要求 |
| `parameter_name` | 再次声明参数名 |
| `parameter_description` | 参数含义，有时还用文字说明预期来源 |
| `required` | 调用时是否需要这个参数 |
| `parameter_type` | 参数类型；样本出现 `string/list` |

参数定义与实际传入值是两回事。例如，参数叫 `date_range`，描述中说来自 `email_time_range` 槽。二者名字不同，但可能存在绑定关系。不同动作的同名参数，也可能来自不同槽。

因此，通用处理需要分别识别参数规格和数据来源。样本主要提供规格，尚未给出完整的结构化绑定规则；不能只凭参数名和槽名相同就认定完成绑定。

### 6.3 动作条件之外，还需要动作效果

要建立行为模型，必须同时回答：

```text
执行前：满足什么条件才能做？
执行中：可能成功、失败，还是有其他结果？
执行后：哪些数据和状态发生什么变化？
```

`activate_condition` 回答第一个问题。`state_schema` 给出了部分动作成功后的附加状态更新，但不自动涵盖业务数据写回。

当前样本的六个动作没有独立的结构化 `effects/state_changes` 字段，`execution_core` 只是名字。不能看到“搜索”“生成”等业务名称，就自行推断动作一定写入哪些槽、一定成功。

通用转换需要显式效果，或者已明确的执行接口行为契约。缺失时应指出具体动作缺少什么信息，避免生成依赖猜测的模型。

## 7. policies：写下要检查的业务要求

### 7.1 这一部分有哪些字段？

`policies` 里面按名字放检查规则。当前文件中，每条规则包含 **4 个字段**：`activate_condition`、`priority`、`description`、`parameters`。

| 字段 | 填什么 | 是什么意思 | 当前例子 |
|---|---|---|---|
| `activate_condition` | 检查条件；样本写成字符串 | 什么时候开始检查 | 草稿不为 null 时检查 |
| `priority` | 整数 | 检查的优先级；数字大小怎样排序，文件没说明 | `1` |
| `description` | 一段文字 | 要检查的具体要求 | 正文是英文、不超过 300 词 |
| `parameters` | 花括号包起来的一组参数 | 把哪些数据交给检查器 | 将草稿内容传进去 |

还有两层由具体业务起的名字，要和上面的字段分开看：

| 写在哪里 | 是什么名字 | 当前例子 |
|---|---|---|
| `policies` 里面 | 检查规则的名字 | `newsletter_draft_requirements` |
| `parameters` 里面 | 检查器接收的参数名字 | `newsletter_draft`，其值 `@slot.newsletter_draft` 表示读取草稿槽 |

四个字段描述规则怎么检查；规则名和参数名由每份 IR 自己填写。当前样本不能证明完整规范还允许哪些其他字段。

### 7.2 对照完整例子看

看原文件这一段：

```json
{
  "policies": {
    "newsletter_draft_requirements": {
      "activate_condition": "input.slots.newsletter_draft != null",
      "priority": 1,
      "description": "The body of newsletter_draft must be written in English and contain no more than 300 words.",
      "parameters": {
        "newsletter_draft": "@slot.newsletter_draft"
      }
    }
  }
}
```

这里的 **`newsletter_draft_requirements` 就是这条规则的名字**，意思是“草稿要求”。它里面放的就是上表列出的四个字段。

JSON 里，冒号左边的名字叫“键”，冒号右边是它对应的值。这里就是：

```text
"newsletter_draft_requirements": { ……检查规则的内容…… }
 └── 规则名字                     └── 这条规则具体怎么定义
```

这个名字用来区分不同规则。真正要检查什么，写在它里面的 description 等字段中。其他 IR 可以给自己的规则起其他名字；通用转换器读取输入里的名字，不固定使用 newsletter_draft_requirements。

### 7.3 连起来怎么读？

上面的例子连起来就是：**有了草稿，就激活“草稿要求”这条检查；把草稿内容交给检查器，要求它是英文，而且不超过 300 词。**

参数这一行：

```json
"newsletter_draft": "@slot.newsletter_draft"
```

左边是检查器接收的参数名，右边表示去取草稿槽里的值。可以读成：

```text
传给检查器的 newsletter_draft 参数 = 当前草稿内容
```

左边也叫 newsletter_draft，是本例的命名选择；参数名与来源槽名不必天然相同，要按明确的绑定关系读取。

### 7.4 检查结果怎样影响流程？

“需要检查”和“检查通过”是两回事。样本后面的流程条件会读取 `input.policy_feedback`，看反馈是否包含 ALLOW（允许）或 DENY（拒绝）。

```text
有了待检查数据
→ 激活策略检查
→ 检查产生反馈
→ 后面的动作或阶段条件读取反馈，决定是否允许继续
```

当前文件没有完整说明检查器怎么实现、反馈怎样初始化或清除、数据改了以后何时重查。这些需要执行规则补齐。

转成 PAT 模型时，可以在明确反馈契约后检查“控制器怎样处理允许或拒绝”。仅把反馈建模成允许/拒绝，并不能证明真实正文一定满足英文和字数要求。

## 8. stages：定义阶段、过渡和正常结束

### 8.1 阶段和动作是什么关系

阶段回答“当前处于流程的哪个位置”，动作回答“现在可以做哪次操作”。一个阶段可以允许多个动作，一个动作是否限制在特定阶段执行，要看它的条件。

样本通过动作条件中的 `input.current_stage` 检查阶段。`actions` 是动作字典，不是从上到下执行的步骤列表。

| 字段 | 样本中的形式 | 含义 |
|---|---|---|
| 阶段的名字，例如 `present_draft` | 写在阶段定义外面的名字 | 其他地方用这个名字指定阶段 |
| `stage_name` | 字符串 | 阶段名，应检查与外层标识的一致性 |
| `stage_goal` | 字符串 | 阶段希望完成什么 |
| `stage_guidance` | 字符串 | 阶段内的操作指导 |
| `is_initial_stage` | 布尔值 | 是否声明为初始阶段 |
| `transition` | 对象或 null | 从该阶段离开的显式过渡集合 |
| `termination` | 对象或 null | 在该阶段正常结束的规则 |

初始阶段的合法数量需要规范约定并检查；样本只有一个。`stage_goal` 和 `stage_guidance` 是文字说明，不能直接当作条件或状态更新。

### 8.2 transition：什么时候去哪个阶段

```text
stages.<源阶段>.transition.<过渡标识>
                           ├── target_stage_name
                           └── activate_condition
```

| 字段 | 含义 |
|---|---|
| 这条跳转的名字，例如 `to_present_draft` | 区分当前阶段的不同跳转规则；不同阶段可以各自使用这个名字 |
| `target_stage_name` | 引用目标阶段 |
| `activate_condition` | 允许这条过渡发生的条件 |

转换器从这些声明建立流程图。阶段数量、分支、循环和连接关系来自输入，不预设某个业务路径。

不同源阶段下可以出现同名过渡，不能仅按过渡名合并。多个过渡条件同时成立时采用什么选择规则，也必须明确；JSON 字典排列和策略 priority 不能自行充当阶段过渡优先级。

注意 `transition` 与 `state_schema` 中的 `transitions`：前者是阶段过渡字典，改变流程位置；后者是状态更新规则数组，改变某个附加状态值。

### 8.3 termination：什么时候允许正常结束

`termination` 对象内部的 `activate_condition` 定义正常结束条件，也可以使用组合条件。

`transition: null` 只表示没有声明显式阶段过渡，不代表进入该阶段就立即结束。阶段中仍可能有动作，结束还需要满足终止条件。`termination: null` 则表示该处未声明正常结束规则。

样本在 present_draft 阶段要求：草稿非 null、presented 为 true、反馈包含 ALLOW，三项同时成立才允许正常结束。

转成 PAT 模型时，需要明确表示正常终止，让检查能够区分任务成功结束和流程意外卡住。

### 8.4 文字指导与结构化流程

如果 guidance 说“回到前一步”，但 transition 没有相应过渡，就需要确认执行系统怎样落实这句话。

通用转换不能根据业务常识临时补出路径。若系统允许从自然语言补充行为，需要明确这一步的解析、确认和记录方式。

## 9. 条件与引用：多个区域共用的语法

### 9.1 activate_condition 为什么多次出现

它统一表达“什么条件下允许激活”，具体激活什么由所在位置决定。

| 所在位置 | 允许发生的事情 |
|---|---|
| `actions.<A>.activate_condition` | 执行动作 A |
| `policies.<P>.activate_condition` | 激活策略 P 的检查 |
| `stages.<T>.transition.<E>.activate_condition` | 发生阶段过渡 E |
| `stages.<T>.termination.activate_condition` | 在阶段 T 正常结束 |

条件只读取当前状态并判断真假。赋值来自动作效果和状态更新规则，阶段改变来自被选择的过渡。条件成立也不证明相应行为已经被调度执行。

### 9.2 all_of 与 any_of

条件既可以是表达式字符串，也可以是组合对象。下面是说明性示例，标识符为示意名字，不是原文件摘录：

```json
{
  "all_of": [
    "input.current_stage == \"review\"",
    {
      "any_of": [
        "input.slots.request == null",
        "input.slot_states.request.checked == false"
      ]
    }
  ]
}
```

`all_of` 要求全部子条件成立，`any_of` 要求至少一个子条件成立。整体意思是“处于审核阶段，并且申请缺失或尚未检查”。

转换时应递归解析条件树，并统一处理叶子表达式。空组合、未知运算符等样本未展示的情况，需要完整规范明确。

### 9.3 运行路径、参数引用和运算符

| 写法 | 含义 |
|---|---|
| `input` | 条件求值的运行上下文入口 |
| `input.slots.<S>` | 槽 S 的当前数据值 |
| `input.slot_states.<S>.<Q>` | 槽 S 的状态 Q 的当前值 |
| `input.current_stage` | 当前阶段标识 |
| `input.policy_feedback` | 策略反馈，完整结构仍需明确 |
| `@slot.<S>` | 参数绑定中引用槽值的记号 |
| `@io_channel` | 样本中引用外部交互通道的记号 |
| `==` / `!=` | 相等 / 不等判断，不会赋值 |
| `null` | 没有值，区别于字符串 null 和空字符串 |
| `[]` | 此处表示空列表 |
| `true` / `false` | 布尔值，区别于同名字符串 |
| `contains(x, y)` | 按样本意图检查包含关系，具体含义取决于操作数类型及求值规则 |
| `ALLOW` / `DENY` | 样本使用的策略反馈标签，出现在字符串中 |

JSON 解析器只读出字符串和对象。引用解析、类型检查和条件求值，或者将条件转成模型表达式，是后续处理负责的事情。

## 10. 把所有字段统一看成行为模型

### 10.1 通用转换究竟提取什么

| 模型需要的信息 | IR 来源 | 还需明确的部分 |
|---|---|---|
| 变量与初值 | 槽及附加状态定义 | 数据抽象、未完整声明的反馈状态 |
| 当前流程位置 | 阶段和初始阶段 | 初始阶段合法性及调度语义 |
| 可执行行为 | 动作及其条件 | 参数绑定、可能结果、数据效果 |
| 状态更新 | state_schema 规则及明确的动作效果 | 同时更新、冲突、未赋值状态怎样保持 |
| 流程改变 | 过渡条件和目标 | 多条过渡可用时怎样选择 |
| 正常结束 | termination | 目标模型中的终止表示 |
| 外部检查 | 策略及反馈契约 | 检查时机与反馈生命周期 |
| 反例定位 | 声明位置与引用关系 | 模型事件怎样映射回本次输入 |

可以按以下过程组织后续转换：

```text
读取 IR
→ 校验结构、类型和引用
→ 明确效果与执行语义，建立统一行为表示
→ 应用有限状态抽象
→ 生成 CSP# 及检查
→ 执行 PAT
→ 将结果和反例映射回输入声明
```

这是设计说明，不表示转换器已经实现。通用性意味着换名字、换数量、换业务流程后仍按同一套结构规则处理。对于未知类型、未知事件、缺失效果和无效引用，应明确报告位置和缺口。

### 10.2 第一版检查与字段的关系

| 检查 | 要回答的问题 | 需要什么 |
|---|---|---|
| 无死锁 | 是否可能尚未正常结束却无路可走？ | 完整的可用行为、更新和终止语义 |
| 阶段/checkpoint 可达 | 指定流程位置有没有可能到达？ | 从输入提取目标，并明确哪些目标预期可达 |
| 动作可执行性 | 指定动作有没有可能实际发生？ | 必要时添加动作发生的监测标记，不能只检查条件曾为真 |

这三项工程检查对应此前选定的 `deadlockfree` 和 `reachability` 两类断言，LTL 不在第一版范围。特定业务性质需要另行明确。

### 10.3 用不同 IR 验证规则

一致重命名标识符后，行为与检查结论应保持对应。改变 JSON 字典项顺序时，不应凭排列顺序引入新的执行语义。增加槽、动作、分支或循环时，模型要反映新的声明；结果可以随行为改变。

因此，验收需要多个不同业务和结构的样本，也需要无效引用、缺失效果等错误输入。当前实例转换成功，只能证明对这一份输入完成了相应处理。

## 附录 A. 当前实例的业务清单

以下保留原 JSON 的实际名字、配置键和全部动作参数，供回查使用。数量、名字和业务约束均属于当前实例；前文的通用结构不依赖这些具体取值。行号指向原 JSON，配置中的凭据使用占位符。

### 八个实际槽分别是什么？

| 槽键 | 行号 | 类型及默认值 | 业务含义 | 主要关联 |
|---|---|---|---|---|
| `comm_type` | 4 | `enum`，`null` | 要生成新闻通讯还是高管简报 | 收集需求、文档生成；枚举值 `newsletter/exec_brief` |
| `email_time_range` | 15 | `string`，`null` | 检索邮件的时间范围，也是文档覆盖时期 | Gmail 的 `date_range` 参数、文档生成 |
| `email_keywords` | 22 | `list`，`[]` | 邮件主题/正文检索关键词 | `SEARCH_GMAIL` 的 `keywords` 参数 |
| `email_senders` | 29 | `list`，`[]` | 邮件检索所关心的优先发件人地址 | `SEARCH_GMAIL` 的 `sender` 参数 |
| `docs_keywords` | 36 | `list`，`[]` | 文档标题/正文检索关键词 | `SEARCH_GOOGLE_DOCS` 的 `keywords` 参数 |
| `email_evidence` | 43 | `list`，`[]` | 从 Gmail 检索到的相关邮件材料 | 检索阶段退出条件、文档生成输入 |
| `docs_evidence` | 50 | `list`，`[]` | 从 Google Docs 检索到的文档材料 | 检索阶段退出条件、文档生成输入 |
| `newsletter_draft` | 57 | `string`，`null` | 最终要展示的内部通信文档正文 | 生成、策略检查、返修、展示及终止条件 |

`newsletter_draft` 这个名称也用于高管简报，并不因为名字带 newsletter 就只允许一种通信类型。

列表“非空”只说明有元素，字符串“非 null”只说明有值；两者都不能自动证明材料相关、格式正确或内容满足策略。例如 `"" != null` 不等于文本有实质内容。

### 六个实际参与者

| 参与者键 | 行号 | 类别 | 作用 | 对应 interface |
|---|---|---|---|---|
| `console` | 103 | human | 向用户提问、收集回答、展示内容 | `program.handler.ConsoleHandler` |
| `google_service` | 111 | traditional_system | 描述一个统一的 Gmail/Drive 服务入口 | 本例未声明 interface/config，也未被六个动作的 executor 直接引用 |
| `search_gmail` | 115 | traditional_system | 按时间、关键词和发件人检索 Gmail | `program.handler.SearchGmailHandler` |
| `search_google_docs` | 126 | traditional_system | 按关键词搜索文档并提取文本 | `program.handler.SearchGoogleDocsHandler` |
| `generate_newsletter` | 137 | traditional_system | 使用证据及覆盖时期调用 LLM 生成内部文档 | `program.handler.GenerateNewsletterHandler` |
| `revise_newsletter` | 147 | traditional_system | 使用草稿及修改要求返修业务文档 | `program.handler.ReviseNewsletterHandler` |

`google_service` 缺少 interface 不足以单独证明整个 IR 非法：它可能是描述性或抽象参与者，完整 schema 在本仓库未提供。

### participant_config 中的全部配置键

| 配置键 | 所在参与者 | 类型 | 含义 |
|---|---|---|---|
| `io_channel` | console | 字符串，本例为引用记号 | `@io_channel` 表示要从外部运行上下文取得交互通道；不是硬编码的一段用户回答 |
| `path_of_credentials` | search_gmail、search_google_docs | 字符串 | Google OAuth 客户端凭据文件位置，不是业务证据槽 |
| `path_of_token` | search_gmail、search_google_docs | 字符串 | 缓存授权 token 的文件位置；不能与 PAT 的状态变量混为一谈 |
| `proxy_host` | search_gmail、search_google_docs | 字符串 | 网络代理主机地址 |
| `proxy_port` | search_gmail、search_google_docs | 整数 | 网络代理端口 |
| `api_key` | generate_newsletter | 字符串 | 业务文档生成模型的 API 凭据；本文不复制原值 |
| `base_url` | generate_newsletter | 字符串 | 模型 API 服务入口地址 |
| `model_name` | generate_newsletter | 字符串 | 要调用的模型标识，不是草稿标题 |
| `BASE_URL` | revise_newsletter | 字符串 | 返修文档时使用的模型服务地址；大小写属于真实键名的一部分 |
| `OPENAI_API_KEY` | revise_newsletter | 字符串 | 返修文档的 API 凭据配置；同样不展示原值 |
| `OPENAI_MODEL` | revise_newsletter | 字符串 | 返修文档使用的模型标识 |

配置结构例子（**已替换为占位符**）：

```json
{
  "participant_config": {
    "api_key": "<API 凭据，不展示>",
    "base_url": "<模型 API 地址>",
    "model_name": "<模型 ID>"
  }
}
```

这两组模型配置使用不同键名：生成使用小写 `api_key/base_url/model_name`，返修使用大写 `OPENAI_API_KEY/BASE_URL/OPENAI_MODEL`。不要仅凭含义相似就认为 handler 会自动兼容它们。

此外，生成参与者的文字描述说从 env 读配置，而文件也提供 `participant_config`。究竟是覆盖 env、注入 env、构造参数，还是未被实际消费，需要 handler 实现才能确定。

---

### 五个实际阶段

| 阶段键 | 行号 | 要做什么 | 文件声明的离开方式 |
|---|---|---|---|
| `collect_requirements` | 169 | 收集通信类型与邮件/文档检索条件 | 需求槽均已具备时进入 retrieve_evidence |
| `retrieve_evidence` | 190 | 获取 Gmail 和 Google Docs 证据 | 两类证据非空且类型、时间已有值时进入 generate_document |
| `generate_document` | 210 | 根据证据生成草稿 | 满足草稿及数据条件可进入 present_draft；反馈含 DENY 可进入 revise |
| `present_draft` | 235 | 向用户展示草稿 | 无显式下一阶段；草稿存在、已展示且反馈含 ALLOW 时正常终止 |
| `revise` | 251 | 根据拒绝反馈修改草稿 | revised 标记为 true 时进入 present_draft |

本例初始阶段明确，但 JSON 中对象项的排列顺序不是“自动依次执行”。是否发生过渡由条件和运行系统的选择规则共同决定。

### 每条实际过渡的名字和含义

| 所在阶段及过渡键 | 目标阶段 | 激活条件的含义 |
|---|---|---|
| collect_requirements.`to_retrieve_evidence` | retrieve_evidence | comm_type、email_time_range 非 null；email_keywords、email_senders、docs_keywords 非空 |
| retrieve_evidence.`to_generate_document` | generate_document | email_evidence、docs_evidence 非空；comm_type、email_time_range 非 null |
| generate_document.`to_present_draft` | present_draft | newsletter_draft、comm_type、email_time_range 非 null；两类 evidence 非空 |
| generate_document.`to_revise` | revise | policy_feedback 包含 DENY |
| revise.`to_present_draft` | present_draft | newsletter_draft 的 revised 标记为 true |

同样叫 `to_present_draft` 的两条边，分别在两个父阶段内，条件不相同，不能合并成一条规则。

有草稿、证据齐全且反馈含 DENY 时，generate_document 的两条过渡条件可能同时满足。**文件没有明确给出过渡优先级/选择顺序，因此这里只能提出待澄清问题，不能断言运行时必然冲突。** policies.priority 不能自行拿来替代阶段优先级。

### 六个实际动作的名字、执行者和核心

| 动作键 | 行号 | executor | execution_core | 要做什么 |
|---|---|---|---|---|
| `ASK_SLOT` | 266 | console | `ask_slot` | 向用户询问某项需求槽的值 |
| `PRESENT_RESULT` | 294 | console | `present_result` | 向用户展示内容 |
| `SEARCH_GMAIL` | 315 | search_gmail | `SEARCH_GMAIL` | 检索邮件证据 |
| `SEARCH_GOOGLE_DOCS` | 351 | search_google_docs | `search_google_docs` | 检索文档证据 |
| `GENERATE_NEWSLETTER` | 373 | generate_newsletter | `generate_newsletter` | 根据证据和要求生成文档草稿 |
| `REVISE_NEWSLETTER` | 415 | revise_newsletter | `revise_newsletter` | 根据反馈修改草稿，并按说明意图写回 newsletter_draft |

例如 Gmail 动作的三层名字是：

```text
动作标识 SEARCH_GMAIL
  → executor 指向 participants.search_gmail
  → interface 指向 program.handler.SearchGmailHandler
  → execution_core 为 SEARCH_GMAIL
```

这些名字在当前文件中有大小写差异，不要自行统一大小写。注册表/handler 是否匹配该核心，要看外部实现。

### 每个动作的实际激活条件

| 动作 | 条件的含义 |
|---|---|
| ASK_SLOT | 当前是 collect_requirements，且五个需求槽至少缺一个 |
| PRESENT_RESULT | 当前是 present_draft，草稿非 null，且 presented 为 false |
| SEARCH_GMAIL | 当前是 retrieve_evidence；类型、时间非 null；邮件关键词、发件人非空；email_evidence 为空 |
| SEARCH_GOOGLE_DOCS | 当前是 retrieve_evidence；类型非 null；文档关键词非空；docs_evidence 为空 |
| GENERATE_NEWSLETTER | 当前是 generate_document；类型、时间非 null；两类证据非空；草稿为 null |
| REVISE_NEWSLETTER | 当前是 revise；草稿非 null；反馈包含 DENY |

检索条件里的 `email_evidence == []` / `docs_evidence == []` 表示当前还没有相应证据，避免在已有证据时继续激活该检索。它不能区分“从未检索”和“已经检索过但结果为空”，除非运行系统另有状态。

GENERATE_NEWSLETTER 要求草稿为 null，所以“已有草稿后再生成”并不是该动作现有条件允许的行为。REVISE_NEWSLETTER 则要求已有草稿。

### ASK_SLOT 的参数

位置：第 284–291 行。

| 参数键 | parameter_name | parameter_type | required | 含义/预期来源 |
|---|---|---|---|---|
| `slot_name` | slot_name | string | true | 本次要向用户询问哪个槽；应由动作选择逻辑确定，不是固定草稿正文 |

文件没有用 enum 或结构化规则限定 slot_name 只能选择那五个缺失需求槽，需要执行层保证选择正确。

### PRESENT_RESULT 的参数

位置：第 305–312 行。

| 参数键 | parameter_name | parameter_type | required | 含义/预期来源 |
|---|---|---|---|---|
| `content` | content | string | true | 要展示的内容；按 present_draft 的 guidance，本任务预期展示 newsletter_draft，但此参数没有显式槽引用 |

### SEARCH_GMAIL 的参数

位置：第 329–348 行。

| 参数键 | parameter_name | parameter_type | required | 含义/说明中的来源 |
|---|---|---|---|---|
| `date_range` | date_range | string | true | 邮件检索时间范围，来源槽 email_time_range |
| `keywords` | keywords | list | true | 邮件关键词，来源槽 email_keywords |
| `sender` | sender | list | true | 优先发件人列表，来源槽 email_senders；名字虽然是单数，实际声明类型是 list |

### SEARCH_GOOGLE_DOCS 的参数

位置：第 363–370 行。

| 参数键 | parameter_name | parameter_type | required | 含义/说明中的来源 |
|---|---|---|---|---|
| `keywords` | keywords | list | true | 文档检索关键词，来源槽 docs_keywords |

两个动作都叫 keywords 的参数，来源槽不同，不能只按参数名进行全局绑定。

### GENERATE_NEWSLETTER 的参数

位置：第 387–412 行。

| 参数键 | parameter_name | parameter_type | required | 含义/说明中的来源 |
|---|---|---|---|---|
| `email_evidence` | email_evidence | list | true | 已检索的邮件材料，来源同名槽 |
| `docs_evidence` | docs_evidence | list | true | 已检索的文档材料，来源同名槽 |
| `email_time_range` | email_time_range | string | true | 文档覆盖时期，来源同名槽 |
| `comm_type` | comm_type | string | true | newsletter 或 exec_brief，来源同名枚举槽；作为参数仍可以是字符串 |

### REVISE_NEWSLETTER 的参数

位置：第 426–439 行。

| 参数键 | parameter_name | parameter_type | required | 含义/说明中的来源 |
|---|---|---|---|---|
| `newsletter_draft` | newsletter_draft | string | true | 当前要修改的草稿，来源同名槽 |
| `policy_feedback` | policy_feedback | string | true | 修改要求；说明中说来源可以是策略反馈或最新 console 用户输入 |

这里的参数 policy_feedback 明确声明为 string，但条件中的 `input.policy_feedback` 没有独立类型声明。如何从策略结果组织成修改指令，不能用同名关系代替转换定义。

此动作的条件仍要求 feedback 包含 DENY；文字说明虽然提到用户修改意见，也没有另外声明“不含 DENY 的用户意见也能激活动作”。

---

## 附录 B. 从实例中的缺口提炼通用问题

| 样本观察 | 需要在通用规范或转换设计中明确的问题 |
|---|---|
| 动作效果、参数来源主要靠文字说明 | 如何提供可解析的绑定、结果和状态效果？ |
| generate_document 的展示与返修条件可能同时成立 | 多条过渡同时可用时如何选择？ |
| guidance 提到 ASK_CLARIFY 和回退，但缺少对应结构化动作或过渡 | 自然语言指导与结构化行为怎样保持一致？ |
| 草稿变化后没有明确重置 presented | 数据变化时，相关附加状态如何失效或重置？ |
| 策略反馈多处被读取，但生命周期未完整定义 | 反馈怎样初始化、合并、清除和重新计算？ |
| 空检索结果与尚未检索共用同一个槽值 | 抽象是否需要区分尝试、失败和空结果？ |
| 展示成功就更新标记，但内容没有显式槽绑定 | 怎样保证所记录的动作结果对应正确的数据对象？ |

这些是待明确的语义问题，并非已经验证的运行 bug。解决时应形成可复用的规则，再用当前实例及其他 IR 检验规则是否忠实表达输入。
