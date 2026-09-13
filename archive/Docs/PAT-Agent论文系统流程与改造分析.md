# PAT-Agent 论文系统流程与改造分析

本文先完整拆解论文中的 PAT-Agent 系统，再据此判断当前项目中哪些步骤可以参考、改造、删除或优化，以及后续需要准备什么形式的输入。

## 0. 全局目标与后续工作的约束

**本项目要建设面向 Controller IR 的通用转换与行为验证能力。当前内部通信文档 IR 是一个分析样本，不是整个系统的业务范围。** 这一要求适用于后续所有概念解释、需求分析、架构设计、实现、测试和诊断，不局限于某个字段或某一篇文档。

后续讨论应先说明通用概念和处理规则，再用具体 IR 举例。设计一个规则时，需要说明换成其他业务、其他标识符、其他数量的槽和动作、其他阶段关系之后，它如何继续成立。

| 工作范围 | 必须遵循的通用要求 |
|---|---|
| 输入规范 | 明确所支持 IR 的结构、类型、表达式和执行语义；不能把一份样本中出现的取值当作完整规范 |
| 数据和状态 | 根据声明建立变量和初值，按引用定位变量；槽名、状态名及数量来自输入 |
| 动作和流程 | 根据动作条件、效果、阶段过渡和终止规则构建行为模型；不预设“收集→检索→生成→展示”这样的业务流程，也不根据动作名字猜效果 |
| 外部行为 | 用明确的接口契约描述 handler、工具、用户输入和策略结果可能造成的状态变化；业务差异通过输入或适配规则表达 |
| 有限状态抽象 | 根据数据类型、实际使用的谓词和领域配置确定抽象；不能统一把所有字符串都压成“有/无”，从而丢失条件需要区分的值 |
| CSP# 生成 | 按支持的 IR 结构和语义应用转换规则；标识符分配、引用解析和模型生成不依赖当前样本的名字 |
| 验证目标 | 根据输入声明生成无死锁、阶段/checkpoint 可达、动作可执行性检查；哪些目标应可达及其期望结果需要统一约定，特定业务性质另行声明 |
| 反例和诊断 | 将模型变量、事件及反例步骤映射回本次输入中的槽、动作、条件和阶段；存在上游溯源信息时再映射到 SPL 源子句 |
| 返修 | 区分转换错误和 IR 本身的行为问题：前者修转换规则，后者定位并提出 IR 修改；不能通过改变模型含义掩盖输入中的问题 |
| 测试与验收 | 使用不同业务、不同结构及故意含错的 IR 验证规则，而不能以当前样本生成成功作为通用能力的验收标准 |

通用性需要通过以下代表性变化检验：一致重命名标识符后，行为和验证结论应保持对应；调整 JSON 字典项顺序时，不应凭排列顺序产生新的执行语义；增加或减少槽、动作、阶段以及引入分支、循环时，模型应忠实反映输入中声明的关系。后两类结构变化可以改变验证结论，测试要检查的是转换是否忠实，而不是结论是否仍然通过。

“适用于所有”指覆盖所约定并支持的 Controller IR 规范，而不是接受任意 JSON 后猜测含义。完整规范尚未明确的部分，应形成待确认的通用语义问题；不支持的结构、缺失效果和无效引用应给出具体诊断。样本暴露的问题应反馈到规范和转换规则层面处理。

以上是后续工作的设计和验收要求，不表示这些能力已经实现。

## 1. 系统总体流程

论文将 PAT-Agent 描述为四个核心组件，但落到仓库代码中，可以拆成“输入准备 + 六个处理步骤”，共七段：

```text
① 用户任务描述与验证要求
        ↓
② 提取常量和变量
        ↓
③ 提取动作、前置条件和状态变化
        ↓
④ 生成逐行自然语言建模计划
        ↓
⑤ 生成完整 CSP#
        ↓
⑥ PAT 验证并与期望结果比较
        ↓
   全部匹配？ ──是──→ 输出 verifiedCode.csp
        │
        否
        ↓
⑦ 根据 trace 返修 CSP#
        ↓
      回到⑥
```

论文中的四个核心组件与上述步骤的关系如下：

| 论文组件 | 实际包含的步骤 |
|---|---|
| Planning LLM | ②常量变量提取、③动作提取、④建模计划生成 |
| Code Generation LLM | ⑤CSP# 生成 |
| Model Checker | ⑥PAT 验证 |
| Repair Loop | ⑦根据 trace 返修并重新验证 |

相关实现位于 [`archive/Automated_Pipelines/Full_Pipeline/pipeline.py`](../Automated_Pipelines/Full_Pipeline/pipeline.py)。

## 2. 每一步的输入与输出

### 2.1 准备系统描述和验证要求

论文将初始信息称为自然语言输入，但仓库中的 Full Pipeline 实际读取结构化的 `PAT.json`，而不是一整段自由文本。

输入形态大致如下：

```json
{
  "modelName": "keyless_car",
  "modelDesc": "描述整个汽车系统",
  "interactionMode": "interleaving",
  "subsystems": [
    {
      "name": "motor",
      "description": "发动机可以启动、关闭、行驶和消耗燃油"
    }
  ],
  "assertions": [
    {
      "assertionType": "reachability",
      "customDescription": "汽车没油时仍在行驶",
      "assertionTruth": "Invalid"
    }
  ]
}
```

论文系统的初始输入实际包括两部分：

```text
系统应该怎样运行
+
用户希望验证什么、期望结果是什么
```

旧数据集位于 [`archive/Datasets/PAT.json`](../Datasets/PAT.json)。

这一步的输出是一个待处理的结构化任务对象，尚未生成 CSP#。

### 2.2 Planning：提取常量和变量

输入：

```text
modelDesc
subsystems[].name
subsystems[].description
subsystemCount
interactionMode
```

Planning LLM 输出 JSON：

```json
{
  "processes": [
    {
      "processName": "motor",
      "constants": [
        {
          "name": "FUEL_EMPTY",
          "value": "0",
          "description": "无燃油"
        }
      ],
      "variables": [
        {
          "name": "fuelLevel",
          "type": "int",
          "possibleValues": "FUEL_EMPTY,FUEL_FULL",
          "initialValue": "FUEL_FULL",
          "description": "当前燃油状态"
        }
      ]
    }
  ]
}
```

这一阶段回答：

```text
系统里有什么状态？
这些状态最开始是什么？
它们可能取哪些有限值？
```

结果原本保存在：

```text
history/const-history.json
```

### 2.3 Planning：提取动作

输入：

```text
原始系统自然语言描述
+
上一步生成的常量变量 JSON
```

输出是动作 JSON：

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

这一阶段回答：

```text
系统能做哪些动作？
动作在什么条件下允许发生？
动作完成后修改哪些状态？
```

这里也是论文中使用常识补充遗漏条件的位置。例如，自然语言没有明确说“没油不能启动”，Planning LLM 可能自行补充该条件。

结果原本保存在：

```text
history/action-history.json
```

### 2.4 Planning：生成详细建模计划

这一步不直接生成 CSP#，而是将前两步的 JSON 改写成逐行自然语言注释。

输入：

```text
常量变量 JSON
+
动作 JSON
+
interactionMode
+
assertions
```

输出类似：

```text
// FUEL_EMPTY represents an empty fuel tank and is set to 0.
// fuelLevel represents the current fuel level, initially FUEL_FULL.

// Definition of the motor subsystem.
// If engineOn is 0 and fuelLevel is not empty,
// start_engine makes engineOn become 1.

// Define drivewithoutfuel as:
// isMoving is 1 and fuelLevel is FUEL_EMPTY.

// Assert that the system cannot reach drivewithoutfuel.
```

这就是论文中的“详细代码生成计划”。它仍然是自然语言，而不是 CSP#。

输出原本保存在：

```text
history/nl-instruction-part.json
history/nl-instruction-claude.json
```

### 2.5 Code Generation：生成 CSP#

输入不只是上一步的计划，而是四部分：

```text
详细自然语言计划
+
原始系统描述
+
CSP# 语法提示
+
RAG 检索出的相似“计划—代码”示例
```

语法提示来自：

```text
syntax-dataset.json
```

RAG 示例来自：

```text
database-rag-claude.json
```

Code Generation LLM 输出完整 CSP#，例如：

```csp
#define FUEL_EMPTY 0;
#define FUEL_FULL 1;

var fuelLevel = FUEL_FULL;
var engineOn = 0;

motor() =
    [engineOn == 0 && fuelLevel > FUEL_EMPTY]
        start_engine{
            engineOn = 1;
        } -> motor();

#define drivewithoutfuel
    (isMoving == 1 && fuelLevel == FUEL_EMPTY);

#assert motor reaches drivewithoutfuel;
```

系统保留两种结果：

```text
original_llm_response.txt
extracted_code.csp
```

如果 PAT 没有产生正常输出，旧系统会将其视为可能的语法错误，并重新调用代码生成 LLM，最多尝试三次。

### 2.6 PAT 验证

输入有两个，缺一不可：

```text
生成的 CSP#
+
原始 assertions 中的期望结果
```

系统先将多个断言拆开：

```text
0.csp：完整模型 + 第一个断言
1.csp：完整模型 + 第二个断言
2.csp：完整模型 + 第三个断言
```

然后逐个交给 PAT。

PAT 原始输出类似：

```text
The Assertion (...) is VALID.

<init -> start_engine -> start_driving>
```

pipeline 再将其整理成：

```json
{
  "assertion": "#assert car reaches drivewithoutfuel;",
  "patResult": "The Assertion ... is VALID.",
  "actualResult": "Valid",
  "desiredOutcome": "Invalid"
}
```

接下来比较：

```text
PAT 实际结果 == 用户期望结果
```

例如：

```text
实际 Valid
期望 Invalid
→ MISMATCH
```

输出包括：

```text
pat_output_0.txt
verification_results.json
mismatch_traces.json
```

需要特别注意：论文系统不是简单地让所有断言变成 `VALID`，而是让 PAT 的实际结果与用户指定的 `assertionTruth` 一致。

### 2.7 根据 trace 返修

输入：

```text
当前完整 CSP#
+
失败断言
+
PAT 实际结果
+
用户期望结果
+
反例或 witness trace
```

具体形态如下：

```json
{
  "assertion": "#assert car reaches drivewithoutfuel;",
  "trace": "<init -> start_engine -> start_driving>",
  "current_result": "Valid",
  "desired_result": "Invalid"
}
```

旧代码先根据 trace 生成返修提示：

- 死锁：检查 trace 末端状态为何没有出口。
- 初始状态即失败：检查常量和变量初值。
- 普通 trace：优先检查最后一个 action 的 guard 是否过弱。
- 如果最后一个 action 没问题，再向前检查其他 action。

然后把这些内容和当前 CSP# 一起交给 Code Generation LLM。

输出：

```text
refined_code_1.csp
```

新模型随后回到 PAT 验证阶段。返修最多进行五轮：

```text
CSP#₀ → PAT → trace₀
CSP#₁ → PAT → trace₁
CSP#₂ → PAT → ...
```

成功时输出：

```text
verifiedCode.csp
```

失败时输出最后一个 CSP# 和剩余 mismatch。

## 3. 对当前项目的参考与裁剪建议

| 论文步骤 | 对当前项目的价值 | 建议 |
|---|---|---|
| ①结构化任务输入 | 必须参考 | 改造成 Controller IR + 验证要求 |
| ②常量变量提取 | Controller IR 已包含大量状态信息 | 删除 LLM 提取，改成确定性读取 |
| ③动作提取 | Controller IR 已包含 action 和条件 | 原则上删除；但必须先补齐机器可读 effects |
| ④自然语言建模计划 | 主要用于帮助 LLM 生成 CSP# | 删除 |
| ⑤LLM 生成 CSP# | 对固定结构而言不够稳定 | 改成确定性转换器；LLM 仅辅助不支持的情况 |
| 语法提示和 RAG | 用于减少 LLM 语法错误 | 从主流程删除，可保留为测试资料 |
| 代码生成失败后随机重试 | 确定性转换器不需要 | 删除，改成明确的转换错误 |
| ⑥PAT 验证 | 核心验证能力 | 保留并优化 |
| 实际结果与期望结果比较 | 论文的重要设计 | 保留；当前 pipeline 需要恢复对期望 `Invalid` 的支持 |
| ⑦trace 返修 | 当前任务的重点 | 保留并重点优化 |
| 全局 history 文件 | 容易覆盖和混淆实验 | 删除；使用 `results/MMDD-HHMM-00X` |
| verified model 数据库 | 暂时不做模型复用时价值较低 | 删除或继续归档 |

## 4. 后续需要准备的输入

从论文系统看，只有 `controller_ir.json` 还不完全够。建议最少准备三类输入：

这里的文件名表示输入职责，不限定具体业务或固定路径。以下含 `newsletter_draft` 等名字的内容仅演示一份实例如何提供配置；通用流程应读取每次输入的声明。第一版自动检查以无死锁、阶段/checkpoint 可达和动作可执行性为范围，示例中的额外业务性质不作为所有控制器的内置要求。

```text
1. 行为模型
   controller_ir.json

2. 验证目标及期望结果
   verification_properties.json

3. 有限状态抽象规则
   abstraction_config.json
```

建议的调用输入形态：

```json
{
  "controller_ir": "controller_ir/controller_ir.json",
  "properties": [
    {
      "id": "no_deadlock",
      "type": "deadlock_free",
      "expected": "Valid"
    },
    {
      "id": "no_premature_termination",
      "type": "reachability",
      "condition": "terminated_before_presented",
      "expected": "Invalid"
    }
  ],
  "abstractions": {
    "newsletter_draft": ["absent", "present"],
    "email_evidence": ["empty", "nonempty"],
    "policy_feedback": ["none", "allow", "deny"]
  }
}
```

三部分的职责分别是：

- Controller IR 回答“系统如何运行”。
- properties 回答“希望 PAT 检查什么以及期望什么结果”。
- abstractions 将字符串、列表等无限数据压缩为 PAT 可以穷举的有限状态。

如果没有验证目标和期望结果，PAT 只能检查自动生成的通用性质，例如死锁、阶段可达和 action 可达，不能知道具体业务安全要求。

如果没有有限状态抽象规则，真实邮件、文档正文、字符串和列表无法直接形成适合穷举的状态空间。

## 5. 最终保留的论文骨架

最值得保留的是：

```text
结构化输入
→ 形式模型
→ PAT 实际结果与期望结果比较
→ trace
→ 定点返修
→ 重新验证
```

论文中主要为“自然语言生成 CSP#”服务的多层 LLM Planning、自然语言计划和 RAG Code Generation 可以从主流程移除。

当前项目真正需要改造的核心，是使用确定性的 `Controller IR → CSP#` 转换，替换论文中的常量变量提取、动作提取、自然语言建模计划和 LLM 代码生成阶段。
