# Controller_IR → CSP# 最终转换方案

本方案覆盖有限状态空间、LLM 联合抽象、Guard 和 Stage 转换。Question1 的当前失败分支处理约定见专项文档；Question2 保留为后续专项设计。

## 一、总体原则

按照 Controller_IR 中实际使用的判断条件，将原始 slot 转换为有限状态空间，再将 Guard 转换为这些有限状态上的条件。

基本要求：

1. 会导致 Guard 判断结果不同的原始值，必须能够在抽象中区分。
2. 同一 slot 在所有 Stage 中使用同一套抽象。
3. 同一 slot 的多个判断必须保持一致，只允许合法组合。
4. `undefined`、显式 `null`、空字符串、空列表分别处理。
5. 明确规则由代码转换；跨类型判断和代码未规定的判断，由 LLM 联合设计 slot 抽象与 Guard。
6. 动作更新 slot 时，相关抽象状态必须同步更新。

## 二、有限状态空间

### 1．String

#### （1）判空／非空 → Boolean

```rego
input.name == ""
input.name != ""

count(input.name) == 0
count(input.name) > 0
```

对有效字符串，可以统一抽象为：

```text
name_is_empty
```

对应关系：

```text
空字符串   → name_is_empty = true
非空字符串 → name_is_empty = false
```

等价判断复用同一个抽象结果。

如果允许字段缺失或显式 `null`，还必须保留对应状态，不能将其归为空字符串。

#### （2）判断长度 → 按条件切割，抽象为 Boolean

```rego
count(input.name) == 3
count(input.name) > 2
count(input.name) <= 5
```

收集全部长度条件，按照它们的判断结果划分有限状态。

例如：

```text
name_length_eq3
name_length_gt2
name_length_le5
```

这些 Boolean 之间必须满足长度条件本身的关系：

```text
name_length_eq3 = true ⇒ name_length_gt2 = true
name_length_eq3 = true ⇒ name_length_le5 = true
```

同一类型的多个长度判断，由代码统一切割和合并。

#### （3）其余判断 → LLM 处理

例如：

```text
字符串是否表示有效日期范围
用户反馈是否表示同意
文本是否包含符合业务要求的信息
```

LLM 必须同时输出该 slot 的有限抽象和相关 Guard 映射。

#### （4）同时出现不同类型的判断 → LLM 联合抽象

例如同一字符串同时涉及：

```text
判空 + 长度
长度 + 日期是否合法
判空 + 是否表示同意
```

交给 LLM 统一设计，避免分别抽象后产生矛盾。

### 2．Boolean

直接保留：

```text
false
true
```

如果原流程允许字段缺失或显式 `null`，补充相应状态。

**`false` 是实际 Boolean 值，不能作为字段缺失或 `null` 的替代值。**

### 3．Enum

保留 Controller_IR 声明的全部枚举值，转换为有限状态。

```text
Controller_IR：
comms_type ∈ {3P, Newsletter}

抽象：
COMMS_TYPE_3P
COMMS_TYPE_NEWSLETTER
```

如果允许字段缺失或显式 `null`，分别保留：

```text
COMMS_TYPE_MISSING
COMMS_TYPE_NULL
```

枚举成功写回未给出具体值时，由代码为每个合法枚举值生成一个成功分支，用 CSP# 的 `[]` 非确定选择探索全部结果；给出了具体值时只生成该结果。初始化用的 NULL／MISSING 不自动作为成功返回值，除非原枚举或返回约束明确允许。

### 4．List

#### （1）判空／非空 → Boolean

```rego
count(input.roles) == 0
count(input.roles) > 0
```

对有效列表，统一抽象为：

```text
roles_is_empty
```

缺失字段、显式 `null` 与空列表分别处理。

#### （2）判断长度 → 按条件切割，抽象为 Boolean

```rego
count(input.roles) == 3
count(input.roles) > 2
count(input.roles) >= 1
count(input.roles) <= 5
```

统一收集长度条件，保留能够区分这些判断的有限状态，并合并等价判断。

例如，对于有效列表：

```rego
count(input.roles) > 0
count(input.roles) >= 1
```

可以复用同一个“非空”抽象。

#### （3）判断固定值是否存在 → 单独抽象为 Boolean

```rego
"admin" in input.roles
input.roles[_] == "admin"
```

在语义等价时，统一抽象为：

```text
roles_has_admin
```

以下写法需要保留变量绑定后再识别：

```rego
some role in input.roles
role == "admin"
```

成员存在与否应按照 Rego 语义解释，包括字段缺失、类型不符和否定表达式的情况。

#### （4）其余判断 → LLM 处理

包括：

```text
是否存在满足业务条件的元素
对象列表中的字段组合判断
列表元素访问及其后续判断
代码未规定的列表操作
```

例如：

```rego
some user in input.users
user.id == 2
user.active == true
```

应理解为：

```text
存在同一个用户，同时满足 id == 2 和 active == true
```

不能拆成两个彼此独立的存在性判断。

#### （5）同时出现不同类型的判断 → LLM 联合抽象

例如：

```text
判空 + 长度
长度 + 成员存在
成员存在 + 对象业务条件
```

LLM 输出合法组合。例如：

```text
roles_is_empty = true ⇒ roles_has_admin = false
roles_is_empty = true ⇒ roles_length_gt2 = false
```

### 5．未设置、null 与空值

统一区分：

| 状态 | 含义 |
|---|---|
| MISSING | 字段不存在，Rego 引用得到 `undefined` |
| NULL | 字段存在，值为 `null` |
| EMPTY | 有效字符串／列表，但内容为空 |
| 其他有限状态 | 按实际判断条件进一步划分 |

Rego 中引用未定义值的表达式也可能未定义，包括 `!=`。因此不能直接把 `!=` 转换为 `==` 的 Boolean 取反。参见 [Rego 官方说明](https://www.openpolicyagent.org/docs/policy-language)。

默认值初始化按原流程执行：

```text
明确的默认值 null → NULL
默认值 ""        → 字符串 EMPTY
默认值 []        → 列表 EMPTY
```

没有声明默认值，不代表默认值就是 `null`。如果原流程保证初始化后字段一定存在，MISSING 可以被标记为不可达。

### 6．多个 Boolean 的实际存储

逻辑上保留各判断的 Boolean 含义；实际模型可以：

1. 使用多个 Boolean，并限制合法组合。
2. 使用一个有限变量，编码所有合法组合。

**建议默认使用第二种，Guard 仍以各判断的含义表示。**

例如：

```text
roles_state = EMPTY
    对应：is_empty=true，has_admin=false，length_gt2=false
```

这样可以减少无效组合，也便于一次更新全部相关判断。

## 三、LLM 联合抽象

### 1．调用条件

满足任一条件时调用 LLM：

1. 同一 slot 同时涉及不同类型的判断。
2. 存在代码未规定的判断。
3. 判断涉及需要共同解释的多个 slot。

“多个判断”按语义类别识别，而不是只看使用了几个运算符。

```text
多个普通长度比较 → 代码处理
判空 + 长度      → LLM 联合抽象
长度 + 成员存在  → LLM 联合抽象
未知业务判断     → LLM 联合抽象
```

### 2．Input

一次调用提供：

1. 相关 `slot_information`。
2. 使用这些 slot 的全部 Stage。
3. 完整 Guard 及其变量绑定、作用范围。
4. 整体 description、slot description、Stage description。
5. 相关动作参数及写回说明。
6. 已有代码规则和必须遵守的约束。
7. 判断编号及 Controller_IR 来源路径。
8. 固定的 OPA 版本和 Rego imports。

默认以单个 slot 为单位调用；存在跨 slot 依赖时，以相关 slot 组为单位调用。

### 3．Output

保持原来的三项输出：

```text
（1）slot_information 抽象结果
（2）Guard 抽象结果
（3）reason
```

具体内容扩展为：

| 输出 | 必须包含 |
|---|---|
| slot_information 抽象结果 | 有限状态、状态含义、初始状态映射、合法组合和相关更新规则 |
| Guard 抽象结果 | 原判断编号、引用的抽象状态、条件树或条件树片段 |
| reason | 抽象依据、合并依据、原文来源及近似之处 |
| unresolved，可选 | 无法可靠确定的部分及原因 |

对于每个抽象状态，记录相关判断是：

```text
true / false / undefined
```

这里的 `undefined` 用于保留 Rego 求值语义；最终迁移条件只允许原 Guard 成立的状态。

### 4．单次调用的内部顺序

```text
确定有限状态
→ 判断各状态下原 Rego 条件的结果
→ 生成引用这些状态的 Guard 映射
→ 一次返回全部结果
```

允许一次调用完成两项设计，但 Guard 必须引用本次确定的有限状态。

### 5．代码校验

LLM 结果存入正式注册表前，代码检查：

- 状态、判断编号和来源引用有效。
- 原始判断全部有对应结果。
- Guard 只引用已声明状态和变量。
- 判空、长度、成员等明确规则没有矛盾。
- 默认值能够映射到抽象状态。
- 更新结果属于合法状态或合法组合。
- 无法确定的条件没有被静默改成恒真。

明确条件可使用代表输入与 OPA 求值结果对照。样本对照只能验证所覆盖情况，不能代替对业务抽象完整性的判断。

## 四、统一存储与收集机制

建立一份**抽象注册表**，统一存储代码和 LLM 的结果。

### 1．存储内容

```text
Slot 抽象记录
├─ 原始 slot 信息
├─ 有限状态及含义
├─ 初始状态映射
├─ 合法组合
├─ 更新规则
└─ reason 与来源

判断映射记录
├─ 判断编号
├─ 原 Rego 表达式或表达式体
├─ 涉及的 slot
├─ 变量绑定与作用范围
├─ 各状态下的判断结果
└─ 来源路径

Guard 树记录
├─ Guard 编号
├─ 与／或／非及其他结构
├─ 判断编号引用
└─ 来源路径
```

### 2．收集顺序

先收集全部用途，再进行抽象：

```text
扫描所有 Stage 和相关动作
→ 收集每个 slot 的全部判断及更新用途
→ 判断由代码处理还是由 LLM 联合处理
→ 收集并校验抽象结果
→ 存入注册表
```

不能每读到一个 Stage 就独立抽象，否则后续出现新判断时，可能需要重建已有状态和 Guard。

### 3．统一生成条件

满足以下条件后，开始生成模型：

```text
所有相关 slot 已完成抽象
所有原判断都有映射
所有 Guard 引用均可解析
所有必要更新都有抽象规则
阻止完整转换的未解决部分均已解决
允许保留的近似及其依据已明确记录
```

出现新的判断或更改抽象时，需要使相关旧映射失效，重新校验后才能生成。

## 五、Guard 转换

### 1．先解析并存入树结构

Guard 按 OPA/Rego 语义解析，保留：

```text
与／或／非
比较和函数调用
成员判断
变量赋值与绑定
存在性判断
作用范围
```

Controller_IR 的 `all_of`、`any_of` 等外层组合，也应保留为树节点。

不能只依赖字符串替换。

### 2．使用抽象注册表改写

```text
原 Guard 树
→ 查找其中各判断的抽象映射
→ 替换为有限状态条件
→ 保留原逻辑结构和绑定语义
→ 生成 CSP# Boolean Guard
```

Rego 的否定必须按固定版本及 imports 解释，不能一律替换为 CSP# 的 `!`。参见 [Rego 否定语义](https://www.openpolicyagent.org/docs/policy-reference/keywords/not)。

### 3．CSP# 表达

使用具名常量、有限变量和条件宏：

```csp
enum { ROLES_MISSING, ROLES_NULL, ROLES_EMPTY, ROLES_SHORT, ROLES_LONG };

var roles_state = ROLES_EMPTY;

#define roles_is_empty (roles_state == ROLES_EMPTY);
#define roles_length_gt2 (roles_state == ROLES_LONG);
```

迁移使用 CSP# 的 Guarded Process 形式：

```text
[条件] 事件 -> 目标 Process
```

代码负责最终命名、连接和渲染。参见 [CSP# 官方语法](https://www.comp.nus.edu.sg/~pat/OnlineHelp/scr/3.1%20CSP%20Module/3.1.1.2%20Process%20Definitions.htm)。

## 六、Stage 转换

### 1．命名规则

```text
xxx_entry
xxx_doActivity
xxx_exit
xxx_transitions
```

统一清洗非法字符，仅在名称冲突时增加区分标记。

### 2．常规执行顺序

```text
xxx_entry
→ xxx_doActivity
→ xxx_exit
→ xxx_transitions
```

`entry`、`doActivity`、`exit` 为 `null` 时，跳过该部分。

### 3．目标指向第一个有效部分

```text
目标 entry 非空
    → target_entry

entry 为空，doActivity 非空
    → target_doActivity

entry、doActivity 为空，exit 非空
    → target_exit

三个动作部分都为空
    → target_transitions
```

终止 Stage 使用对应终止 Process。

### 4．动作更新抽象状态

动作更新原 slot 时，同步更新该 slot 的抽象表示。

```text
更新列表
→ 更新判空结果
→ 更新长度判断结果
→ 更新成员存在结果
```

使用一个有限变量编码组合时，一次写入对应状态；使用多个 Boolean 时，相关更新必须一起完成，避免出现中间的不一致组合。

`append`、`remove` 等操作需要按照原状态和操作语义建立抽象更新关系，不能只任意选择一个合法终态。

### 5．其他结构

Controller_IR 中存在 policy 时，需要明确其执行位置，并将其中相关判断和更新纳入同一套收集与抽象机制，不能在模型中遗漏。

## 七、转换检查内容

生成 CSP# 前检查：

1. **输入完整性**：slot、动作、Stage 目标和 Guard 引用有效。
2. **抽象完整性**：所有相关判断均已收集，特殊值正确区分。
3. **组合一致性**：同一 slot 的多个判断不会产生矛盾状态。
4. **Guard 一致性**：保留 Rego 的逻辑、未定义值和变量绑定语义。
5. **更新一致性**：动作更新与有限状态变化相符。
6. **流程完整性**：非空步骤没有遗漏，`null` 正确跳过，目标连接正确。
7. **生成有效性**：CSP# 名称、变量范围、条件和 Process 引用通过检查。
8. **依据可追溯**：抽象状态、Guard 和更新都能够定位回 Controller_IR。
9. **近似明确**：保守近似和无法处理的部分必须显式记录。

明确无法处理的部分不能伪装成完整转换结果。

## 八、完整转换流程

```text
Controller_IR
    ↓
结构检查，固定 Rego 版本与 imports
    ↓
解析并保存 Guard 树
    ↓
收集所有 slot 的判断与更新用途
    ↓
代码抽象明确规则
LLM 联合抽象跨类型／未知判断
    ↓
校验并存入抽象注册表
    ↓
全部依赖齐全后，连接和改写 Guard
    ↓
生成 Stage 各部分及抽象更新
    ↓
连接目标 Process
    ↓
生成 CSP_IR
    ↓
渲染 CSP#
    ↓
语法与模型有效性检查
```

## 九、专项状态

- **Question1**：已整理当前失败分支处理方案，见 [失败分支转换方案](ControllerIR到CSPSharp-失败分支转换方案.md)。主要流程为先检查失败状态下的 transitions，没有可用出边时再调用 LLM。`guard: true` 与失败说明冲突的情况已记录，当前默认不会出现，后续再处理。
- **Question2**：未知枚举成功写回由代码展开全部合法枚举值，以 CSP# `[]` 非确定选择探索；具体写回值明确时只保留该值。

Question1 的当前约定以专项文档为准。以上规则已接入转换器，代码接口和实现边界见 [实现说明](ControllerIR到CSPSharp-实现说明.md)。
