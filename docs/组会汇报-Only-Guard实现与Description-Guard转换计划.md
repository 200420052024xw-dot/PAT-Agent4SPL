# Controller_IR 到 CSP#：Only-Guard 实现与 Description + Guard 转换计划

日期：2026-10-11  
用途：组会汇报，可按“当前成果 → 实现方法 → 发现的问题 → 下一步计划”的顺序讲述。

## 1. 本阶段工作的定位

本阶段已经实现以显式 Guard 为依据的 Controller_IR → CSP_IR → CSP# 转换链路，并接入 PAT 检查。通过核对业务案例，发现部分关键分支条件只存在于 description 中，当前模型尚未完整表达这些条件。因此，下一阶段将研究 Description + Guard 联合转换。

本文将当前原有出边的转换方式称为 **Only-Guard**。这是汇报时对转换依据的概括，仓库目前没有同名的模式开关。

需要准确区分两个结论：

- **转换基础已经实现**：能够建立有限模型、生成 CSP#，并支持模型检查。
- **业务语义覆盖仍需完善**：仅依据显式 Guard，不能保证保留 description 中的全部决策要求。

当前版本也会使用自然语言辅助复杂抽象、policy 处理和失败缺口补全，因此 Only-Guard 并不表示整个程序完全不读取 description。其核心边界是：**原有 transitions 的分支条件直接来自 Guard，尚未系统地把出边 description 转成额外约束。**

## 2. Only-Guard 版本我是怎么做的

### 2.1 总体方法：先建立有限模型，再生成 CSP#

我没有让 LLM 直接生成整份 CSP#，而是先把 Controller_IR 转成有明确变量、控制位置、条件和更新的 CSP_IR，再由代码统一输出 CSP#。

```text
Controller_IR
    ↓ 输入结构与引用校验
确定非空 policy 的执行位置和结果判断
    ↓
收集全部 Guard、引用槽位和来源
    ↓
建立统一的槽位有限抽象，并校验判断映射
    ↓
编译 Stage、动作结果、数据写回和原有出边
    ↓
检查失败状态下的出边覆盖，处理失败缺口
    ↓
CSP_IR → CSP#
    ↓
生成检查项 → PAT 检查 → 保存结果与反例
```

这样可以把“业务抽象是否合理”“流程结构是否正确”和“输出语法是否正确”分别检查，并通过来源映射回溯到输入。

### 2.2 先收集全部判断，再统一抽象槽位

字符串、列表等数据可能具有很大的取值空间，不能直接把所有原始值都枚举进模型。我先收集各 Stage 和 policy 使用的判断，再根据这些判断需要区分的信息建立有限状态。

例如，如果一个字符串只被判断是否为空，就可以围绕空值与非空值进行抽象；如果还要判断长度是否超过阈值，就需要进一步区分相应长度范围。缺失、null、空字符串和空列表分别处理，避免混为同一种状态。

同一个槽位在不同 Stage 中使用同一份抽象。跨槽位判断涉及的槽位按依赖关系组成联合组，避免分别抽象后丢失它们之间的关系。

这一步的核心原则是：**会影响已收集判断结果的信息，需要在有限抽象中得到区分。**

### 2.3 明确规则交给代码，复杂情况使用 LLM 联合抽象

布尔、枚举、判空、长度和固定成员等受支持的规则由代码处理。混合类别、未知业务判断及跨槽位依赖等情况，使用 LLM 同时提出有限状态与对应的 Guard 映射。

LLM 的输出不是最终 CSP#，而是结构化结果，例如：有限状态、代表值、哪些状态使判断成立，以及抽象依据。代码随后检查状态引用、代表值实际求值、默认值映射和已知候选覆盖。

涉及完整 Rego 表达式时使用官方 OPA 求值；外层 `all_of`、`any_of`、`not` 保留为布尔条件树。判断为未定义的状态不作为 Guard 成立的状态。

代表值校验可以发现具体不一致，但不能证明未知业务条件在全部原始取值上的完备性。当前实现会保留抽象依据和近似诊断。

### 2.4 把 Stage 展开成具体控制位置

这一部分以真实输入中的 `generate_3p` 为例，逐步展示一个 Stage 如何变成多个 CSP# 进程。

#### 第一步：读取 Stage，明确动作、写回与出边

输入位置为 `/stages/generate_3p`。以下保留原输入中与转换相关的字段：

```json
{
  "kind": "state",
  "description": "依据当前需求和邮件证据生成完整草稿。始终输出全文，成功后进入展示审阅，不直接完成；修改筛选需求必须重新检索。失败保留错误并停止本次运行。",
  "entry": {
    "action": "RESET_VALUE",
    "parameters": {"value": ""},
    "when_success_write_to": {
      "operation": "overwrite",
      "target": "slots.review_feedback"
    }
  },
  "doActivity": {
    "action": "GENERATE_3P",
    "parameters": {
      "requirements": "@slots",
      "fallback": "@slots.use_user_material"
    },
    "when_success_write_to": {
      "operation": "overwrite",
      "target": "slots.draft"
    }
  },
  "exit": null,
  "transitions": [
    {
      "target": "present_and_review",
      "description": "生成/修订成功，必须展示完整草稿并等待新的反馈。",
      "guard": "input.stage_execution.doActivity.execution_status == \"success\""
    }
  ]
}
```

从这些字段读出四件事：先清空旧反馈；再生成草稿；没有 exit 动作；最后检查生成是否成功，成功才走原有出边到审阅阶段。

这里的 `@slots` 等参数用于说明动作上下文，模型不会执行真实的草稿生成调用。转换器把动作抽象成有限结果及其对槽位的更新。

#### 第二步：为非空步骤和 transitions 建立控制位置

一个 Stage 不直接等于一个 CSP# 进程，而是按内部执行位置展开。一般顺序为：

```text
entry → doActivity → exit → 已确定的 policy 评估位置 → transitions
```

本例 `entry`、`doActivity` 非空，`exit` 为空，且没有插入 policy，因此先建立三个位置：

```text
generate_3p_entry
        ↓ entry 成功
generate_3p_doActivity
        ↓ doActivity 成功
generate_3p_transitions
        ↓ 原出边 Guard 成立
present_and_review_doActivity
```

空的 exit 不生成单独进程。进入目标 Stage 时，也不是只写一个 Stage 名称，而是跳到它的第一个有效位置；审阅阶段 entry 为空、doActivity 非空，因此目标是 `present_and_review_doActivity`。

#### 第三步：用三个参数记录当前 Stage 内各步骤的执行状态

每个普通控制位置携带：

```text
(entry_status, do_status, exit_status)
```

对应常量为：

```csp
#define STATUS_NOT_RUN 0;
#define STATUS_SUCCESS 1;
#define STATUS_FAILURE 2;
```

首次进入该 Stage 时，三个参数都是 NOT_RUN。参数不是槽位数据：它们专门记录当前 Stage 内动作的执行情况，使后面的 Guard 可以查询。

| 到达位置与情境 | entry_status | do_status | exit_status |
|---|---|---|---|
| 刚进入 entry | NOT_RUN | NOT_RUN | NOT_RUN |
| entry 成功，到达 doActivity | SUCCESS | NOT_RUN | NOT_RUN |
| doActivity 成功，到达 transitions | SUCCESS | SUCCESS | NOT_RUN |
| entry 失败，直接到达 transitions | FAILURE | NOT_RUN | NOT_RUN |
| doActivity 失败，到达 transitions | SUCCESS | FAILURE | NOT_RUN |

#### 第四步：转换 entry，生成成功写回与失败分支

entry 明确把 `""` 写入 `review_feedback`。由于抽象中空反馈对应 `REVIEW_FEEDBACK_EMPTY`，成功边就生成这个赋值，并把 entry_status 设为 SUCCESS，再进入 doActivity。

```csp
generate_3p_entry(entry_status, do_status, exit_status) =
    [true] generate_3p_entry_success_0
    {slot_review_feedback = REVIEW_FEEDBACK_EMPTY;}
    -> generate_3p_doActivity(STATUS_SUCCESS, do_status, exit_status)
    []
    [true] generate_3p_entry_failure
    -> generate_3p_transitions(STATUS_FAILURE, STATUS_NOT_RUN, STATUS_NOT_RUN);
```

失败边没有清空反馈的赋值，不继续执行 doActivity，直接进入 transitions。这里的事件表示抽象执行结果，不是真实调用 RESET_VALUE。

#### 第五步：转换 doActivity，枚举成功写回结果

doActivity 声明成功后 overwrite 到 `slots.draft`，但没有给出一个固定的返回字符串。因此，转换器根据当前有限域和写回约束展开可能结果。

在下面引用的实际产物中，draft 有 EMPTY、NONEMPTY 两个状态，生成了两条成功边和一条失败边：

```csp
generate_3p_doActivity(entry_status, do_status, exit_status) =
    [true] generate_3p_doActivity_success_0
    {slot_draft = DRAFT_EMPTY;}
    -> generate_3p_transitions(entry_status, STATUS_SUCCESS, exit_status)
    []
    [true] generate_3p_doActivity_success_1
    {slot_draft = DRAFT_NONEMPTY;}
    -> generate_3p_transitions(entry_status, STATUS_SUCCESS, exit_status)
    []
    [true] generate_3p_doActivity_failure
    -> generate_3p_transitions(entry_status, STATUS_FAILURE, STATUS_NOT_RUN);
```

可以逐条解释：成功返回空草稿时写入 EMPTY；成功返回非空草稿时写入 NONEMPTY；失败时不写入 draft，保留原值，并记录 FAILURE。此前 entry 成功清空反馈的更新仍然保留。

**实际产物允许成功结果为空，不能在汇报时说成“生成成功就必然得到非空草稿”。** 若 description 的“完整草稿”要求成功结果必须非空，就需要进一步把它落实为结果约束；这也说明自然语言不仅可能影响 transition，还可能约束动作结果。

#### 第六步：把原 transition 的 Guard 转成 CSP# 条件

本例原 Guard 为：

```text
input.stage_execution.doActivity.execution_status == "success"
```

转换器知道 doActivity 的状态存放在参数 `do_status`，成功对应常量 `STATUS_SUCCESS`，因此得到：

```csp
[do_status == STATUS_SUCCESS]
```

然后生成跳转事件、更新当前 Stage，并把目标 Stage 的步骤状态重置为 NOT_RUN：

```csp
[do_status == STATUS_SUCCESS] generate_3p_to_present_and_review_0
{current_stage = STAGE_PRESENT_AND_REVIEW;}
-> present_and_review_doActivity(STATUS_NOT_RUN, STATUS_NOT_RUN, STATUS_NOT_RUN)
```

注意：这条原 Guard 没有检查 draft 是否为空。因此在当前模型里，两种成功写回都能进入审阅阶段，不能额外声称出边已经保证了草稿完整。

#### 第七步：检查失败时是否有可执行出边，再补充失败去向

entry 失败时，do_status 为 NOT_RUN；doActivity 失败时，do_status 为 FAILURE。这两种情况下，原有“do_status 为 SUCCESS”的出边都不成立。

因此转换器分别对两个失败位置进行缺口分析。在所引用的运行中，LLM 根据“失败保留错误并停止本次运行”提出 terminate，代码校验后应用，并生成以下分支：

```csp
[(entry_status == STATUS_FAILURE && !(do_status == STATUS_SUCCESS))]
generate_3p_entry_terminate
{run_outcome = RUN_OUTCOME_ABORTED;}
-> generate_3p_failed()
```

```csp
[(do_status == STATUS_FAILURE && !(do_status == STATUS_SUCCESS))]
generate_3p_doActivity_terminate
{run_outcome = RUN_OUTCOME_ABORTED;}
-> generate_3p_failed()
```

失败结束位置为：

```csp
generate_3p_failed() = Skip;
```

`Skip` 表示这个进程结束，但 `run_outcome` 为 ABORTED，区别于进入正常完成阶段的 COMPLETED。并不是所有动作失败都自动生成 Skip：有依据的暂停使用 Stop；无法确定处理方式时保留缺口。

本例实际补全把 Stage 的失败说明用于 entry 和 doActivity 两处，这是该次提议和应用记录中的解释，不意味着所有 Stage 的自然语言失败要求都可以无条件套用到每个步骤。当前模型也没有因此自动建立“错误内容已保存”的变量。

#### 第八步：形成 CSP_IR，再统一渲染

上述步骤先建立结构化对象，而不是直接拼接整个程序：

| 对象 | 本例中的内容 |
|---|---|
| Process | entry、doActivity、transitions，以及补全后的 failed 位置 |
| Edge | entry 成功／失败、doActivity 两种成功／失败、原跳转和两条失败结束边 |
| Guard | true、do_status 为 SUCCESS、失败状态且原出边不成立 |
| Effect | 清空反馈、写入草稿状态、更新 current_stage 或 run_outcome |
| target_args | 传递本 Stage 的执行状态，或重置目标 Stage 的执行状态 |

代码检查进程引用、目标参数数量、写回取值范围等，再由 renderer 统一输出常量、变量和进程定义。本例合计得到 **4 个控制位置、8 条边**，其中 failed 位置和两条失败结束边来自此次失败补全。

#### 第九步：用一条具体路径串起来讲

假设本次清空反馈成功，生成动作也成功，返回非空草稿：

```text
进入 generate_3p_entry(NOT_RUN, NOT_RUN, NOT_RUN)
    ↓ entry 成功：review_feedback 写成 EMPTY
进入 generate_3p_doActivity(SUCCESS, NOT_RUN, NOT_RUN)
    ↓ 生成成功：draft 写成 NONEMPTY
进入 generate_3p_transitions(SUCCESS, SUCCESS, NOT_RUN)
    ↓ do_status == SUCCESS 成立
current_stage 更新为 PRESENT_AND_REVIEW
进入 present_and_review_doActivity(NOT_RUN, NOT_RUN, NOT_RUN)
```

如果 entry 失败，则跳过生成，直接到 transitions，再沿本例补全的失败结束边退出；如果生成失败，则保留原 draft，再沿相应失败结束边退出。

#### 本例常量与完整的本 Stage 进程片段

下面按“常量 → 变量 → Stage 内部进程 → 跳转”集中展示。常量编码和进程内容依据现有产物，换行做了整理。**这是完整大模型中的局部片段**，目标 `present_and_review_doActivity` 的定义在下一 Stage；这里不把它伪造为 Skip，也不把局部片段称为可独立运行的完整程序。

```csp
#define STATUS_NOT_RUN 0;
#define STATUS_SUCCESS 1;
#define STATUS_FAILURE 2;

#define REVIEW_FEEDBACK_EMPTY 0;
#define REVIEW_FEEDBACK_NONEMPTY 1;
#define DRAFT_EMPTY 0;
#define DRAFT_NONEMPTY 1;

#define STAGE_INITIAL 0;
#define STAGE_GENERATE_3P 8;
#define STAGE_PRESENT_AND_REVIEW 12;
#define RUN_OUTCOME_RUNNING 0;
#define RUN_OUTCOME_ABORTED 2;

// 以下变量是整个模型共享的变量，原模型只声明一次。
var slot_review_feedback = REVIEW_FEEDBACK_EMPTY;
var slot_draft = DRAFT_EMPTY;
var current_stage = STAGE_INITIAL;
var run_outcome = RUN_OUTCOME_RUNNING;

generate_3p_entry(entry_status, do_status, exit_status) =
    [true] generate_3p_entry_success_0
    {slot_review_feedback = REVIEW_FEEDBACK_EMPTY;}
    -> generate_3p_doActivity(STATUS_SUCCESS, do_status, exit_status)
    []
    [true] generate_3p_entry_failure
    -> generate_3p_transitions(STATUS_FAILURE, STATUS_NOT_RUN, STATUS_NOT_RUN);

generate_3p_doActivity(entry_status, do_status, exit_status) =
    [true] generate_3p_doActivity_success_0
    {slot_draft = DRAFT_EMPTY;}
    -> generate_3p_transitions(entry_status, STATUS_SUCCESS, exit_status)
    []
    [true] generate_3p_doActivity_success_1
    {slot_draft = DRAFT_NONEMPTY;}
    -> generate_3p_transitions(entry_status, STATUS_SUCCESS, exit_status)
    []
    [true] generate_3p_doActivity_failure
    -> generate_3p_transitions(entry_status, STATUS_FAILURE, STATUS_NOT_RUN);

generate_3p_transitions(entry_status, do_status, exit_status) =
    [do_status == STATUS_SUCCESS] generate_3p_to_present_and_review_0
    {current_stage = STAGE_PRESENT_AND_REVIEW;}
    -> present_and_review_doActivity(STATUS_NOT_RUN, STATUS_NOT_RUN, STATUS_NOT_RUN)
    []
    [(entry_status == STATUS_FAILURE && !(do_status == STATUS_SUCCESS))]
    generate_3p_entry_terminate
    {run_outcome = RUN_OUTCOME_ABORTED;}
    -> generate_3p_failed()
    []
    [(do_status == STATUS_FAILURE && !(do_status == STATUS_SUCCESS))]
    generate_3p_doActivity_terminate
    {run_outcome = RUN_OUTCOME_ABORTED;}
    -> generate_3p_failed();

generate_3p_failed() = Skip;
```

实际模型见 [20261010-212818-000/model.csp](../runs/controller_ir_to_csp/20261010-212818-000/model.csp)，补全依据见同目录的 [failure_records.json](../runs/controller_ir_to_csp/20261010-212818-000/failure_records.json)。这次运行的 manifest 为 INCOMPLETE、模型已生成、检查数量为 0，因此本例证明的是实际生成结构，不是一次 PAT 检查通过记录。

#### 其他 Stage 如何套用同一套规则

| Stage 结构 | 转换方式 |
|---|---|
| entry 为空，doActivity 非空 | 进入 Stage 时直接进入 doActivity |
| 有 exit | doActivity 成功后进入 exit；exit 成功后才进入后续位置 |
| 三个动作都为空 | 若有 policy 则先进入 policy，否则直接进入 transitions |
| 有已确定的 policy | 放在动作步骤之后、transitions 之前，多项依次连接 |
| 多条 transitions | 每条独立编译 Guard，同一位置的出边用 `[]` 连接 |
| kind 为 termination | 生成终止进程 Skip；当前实现不再展开其动作步骤 |

每个非空动作步骤遵守相同规则：

- 成功时执行声明的成功写回，再进入后续位置。
- 失败时不执行当前步骤的成功写回，保留此前已完成的更新，并进入当前 Stage 的 transitions。
- 记录各步骤的 NOT_RUN、SUCCESS、FAILURE 状态，使 Guard 能引用执行结果。

未知枚举成功结果展开为全部合法枚举值；列表增删等更新按转换器支持的规则映射到有限状态。

### 2.5 原有出边直接按 Guard 编译

对每条 transition，我保留其目标 Stage，将 Guard 编译成有限条件树，再生成类似下面的 CSP# 分支：

```text
[有限条件] 跳转事件 {状态更新} → 目标进程
```

跳转时更新当前 Stage；进入终止阶段时记录完成结果。当前同一控制位置的多条出边统一用 `[]` 连接。多个 Guard 同时成立时，模型允许在相应分支之间选择，尚未单独表达业务优先级或不同的选择来源。

**Only-Guard 的关键就在这一步：原有出边是否可执行由显式 Guard 决定，description 中尚未结构化的业务限制不会自动加入条件。**

### 2.6 检查失败覆盖，并保留未解决的缺口

对于动作失败后的去向，先检查原有 Guard 是否已经覆盖合法的有限失败状态。只有存在缺口时，才请求 LLM 根据原文分析额外的失败处理方式。

补充分支限制在以下范围，避免覆盖原有可执行出边：

```text
当前步骤失败 ∧ 所有原出边均不成立 ∧ 可选附加条件
```

有明确依据时，可以生成结束、暂停、跳转或重试；无法确定时保留 `FAILURE_GAP` 警告。这类警告不会一律阻止模型输出，但不能被解释为失败处理已经完整。

当前约定还暂不额外分析 `guard: true` 与失败说明之间的冲突。

### 2.7 保存模型、来源与检测结果

当前链路保存以下主要产物：

| 产物 | 作用 |
|---|---|
| `model.csp_ir.json`、`model.csp` | 查看有限模型及最终 CSP# |
| `abstraction_registry.json` | 核对槽位抽象、判断映射和依据 |
| `source_map.json`、`render_map.json` | 将模型名称和反例事件关联到输入位置 |
| `failure_records.json` | 查看失败补全提议及是否实际应用 |
| `checks.json`、`verification_results.json` | 查看检查项、PAT 结果及反例 |
| `diagnostics.json`、`manifest.json` | 查看转换缺口和运行状态 |

模型先保存，再生成检查项和执行 PAT。`--model-only` 可以只运行转换。检测结束后报告问题和反例，不自动修改原 IR 或模型。

### 2.8 汇报例子一：Only-Guard 如何完成一次转换

先用一个简化的教学例子讲清楚已有方法。下面不是业务输入的完整片段，而是用于说明转换过程的最小流程：

```text
槽位：draft，初值为空字符串
阶段：generate → review
generate 的动作：生成草稿，成功后写入 draft
generate → review 的 Guard：动作成功 ∧ draft != ""
```

**第一步，确定需要保留的数据区别。** 如果整个例子对 draft 只判断是否为空，且没有 null 或缺失值，就可抽象为 `EMPTY`、`NONEMPTY` 两种状态，而不用保存草稿全文。

| 原始值举例 | 有限状态 | `draft != ""` 的结果 |
|---|---|---|
| `""` | EMPTY | 假 |
| `"本季度工作总结……"` | NONEMPTY | 真 |
| `"下季度计划……"` | NONEMPTY | 真 |

**第二步，表示动作执行与写回。** 动作成功时，按成功结果更新 draft 并记录 SUCCESS；失败时记录 FAILURE，不执行当前步骤的成功写回。假设例子中的生成动作成功结果限定为非空，则成功后 draft 为 NONEMPTY。这个限制是例子的前提，不能仅凭“动作成功”自动推断所有业务动作都返回非空内容。

**第三步，编译 Guard 和跳转。** 原来的字符串判断变成有限状态判断，流程结构可示意为：

```text
generate 的 transitions：
    [do_status == SUCCESS ∧ draft == NONEMPTY] → review
```

这段是便于讲解的伪代码，不是仓库生成文件的逐字摘录。实际渲染还包括事件、状态更新、参数和名称映射。

**第四步，检查行为。** 成功且草稿非空时允许进入 review；失败或草稿为空时，这条出边不能执行。若没有其他失败出边，则继续进入失败缺口分析。

组会上可以这样解释：

> 我的方法是先看 Guard 需要区分什么信息，把原始数据压缩为有限状态，再把动作结果、写回和条件跳转连接起来。这个例子里，Guard 只关心草稿是否为空，所以不需要把草稿全文放进模型。只要分支的关键业务条件已经完整写在 Guard 中，这种转换就能表达该出边的启用条件。

## 3. 当前可以汇报的成果与验证边界

目前完成的工作包括：输入校验、统一有限抽象、显式 Guard 编译、动作执行与写回、Stage 连接、失败缺口处理、CSP# 输出，以及 PAT 检查接口和来源追溯。

仓库已有转换、抽象、失败处理、渲染及部分真实引擎集成测试。本次汇报材料依据代码和文档整理，没有重新运行测试，因此不在这里给出最新通过数量。正式汇报具体测试结果时，应使用对应运行记录，并区分固定 LLM 回答测试、真实引擎测试和实际业务输入测试。

需要避免将“成功生成 CSP#”直接表述为“已经完整保留业务语义”，也不能将部分检查通过表述为整个业务流程已经正确。

## 4. 为什么要向 Description + Guard 扩展

### 4.1 发现的问题：关键决策要求没有全部写进 Guard

在当前业务案例中，`present_and_review → completed` 的 description 要求用户明确接受当前展示版本，且没有同时要求修改或提问。但 Guard 主要检查动作成功和草稿非空。

| 对比项 | 表达的条件 |
|---|---|
| description | 用户明确接受当前版本，且没有同时要求修改或提问 |
| 显式 Guard | 动作成功且草稿非空 |
| 当前生成模型 | 后两项满足时，完成分支即可执行 |
| 缺失的约束 | 反馈是否表示接受，以及是否同时包含修改或提问 |

因此，“用户要求修改，但动作成功且草稿非空”这样的情况，在当前模型中仍可能选择完成分支。这里是根据代码得到的行为分析，并非本次重新运行 PAT 得到的反例。

同一阶段的部分其他出边使用 `guard: true`，但 description 分别描述修改、提问或含糊反馈。忽略这些说明后，本应依据不同反馈选择的分支会成为同时可选分支。

### 4.2 对模型检查的影响

PAT 检查的是转换后的模型。如果“必须接受才能完成”没有进入模型，那么即使 PAT 正确执行，也不能据此说明原流程满足这一业务要求。

因此，下一步的重点是补齐影响流程行为的语义，再检查这些语义是否满足设计要求。单纯修改分支连接运算符无法补回缺失的决策条件。

### 4.3 汇报例子二：用户要求修改，模型却允许完成

这是当前业务输入中的真实出边，来源为 [Internal-Comms-IR.json](../inputs/Controller_IR/Internal-Comms-IR.json) 的 `/stages/present_and_review/transitions/0`：

```json
{
  "target": "completed",
  "description": "用户明确接受刚刚展示的这一版，且没有同时要求修改或提问。",
  "guard": {
    "all_of": [
      "input.stage_execution.doActivity.execution_status == \"success\"",
      "input.slots.draft != \"\""
    ]
  }
}
```

汇报时给出具体情境：已经展示了一份非空草稿，PRESENT 成功返回，用户回复“第二段请改一下，先不要定稿”。这条回复不是接受，但原 Guard 的两个条件仍然满足。

| 条件 | 当前情境中的结果 | 当前完成出边是否检查 |
|---|---|---|
| PRESENT 执行成功 | 真 | 是 |
| 草稿非空 | 真 | 是 |
| 用户明确接受 | 假 | 否 |
| 没有同时要求修改或提问 | 假 | 否 |

因此，Only-Guard 生成的完成出边仍然可执行。这里强调的是模型**允许**完成，不是每次都一定选择完成；只要多出了一条违背原文要求的允许路径，就说明两者的行为约束不一致。

```text
业务要求：展示草稿 → 用户要求修改 → 修订 → 再次展示 → 等待明确接受
当前模型允许：展示动作成功、草稿非空 → completed
遗漏的信息：用户要求修改，而不是明确接受
```

用户回复写入 `review_feedback`，但完成出边没有读取它的接受或修改含义。当前模型也未必能区分这两类回复，因此不能把上面的业务情境当作已经在现有模型中完整编码的反例轨迹。这是输入和代码静态核对发现的缺口，本次没有重新运行 PAT。

下一步的条件应补充为以下形式，其中新增业务谓词及其有限表达尚待实现：

```text
动作成功 ∧ 草稿非空
∧ 明确接受当前展示版本
∧ 没有同时要求修改
∧ 没有同时提问
```

组会上可以直接说：

> 展示成功只能说明动作执行成功，不能说明用户接受。当前 Guard 没有表达这一区别，所以模型允许过早完成。我需要把 description 中的接受条件真正放进状态和分支判断里。

### 4.4 汇报例子三：三个 `guard: true` 丢失了不同的分支意图

同一阶段还有三条真实出边，来源为 `/stages/present_and_review/transitions/3` 至 `/5`：

| 目标阶段 | description 表达的业务前提 | Guard |
|---|---|---|
| revise_slot | 用户明确修改需求或检索条件，先更新槽位 | true |
| ask_clarification | 反馈含糊，无法判断接受还是修改，或修改内容存在歧义 | true |
| explanation | 用户提出需要说明的概念、用途或草稿内容问题 | true |

假设用户只问：“这里的 3P 是什么意思？”按说明，这一情境需要解释概念，不是修改检索条件，也不是含糊地接受或拒绝。

但当前三条 Guard 都为 true。在这三条出边之间，模型没有依据用户问题来限制选择：

```text
当前转换的简化示意：
    [true] → revise_slot
    [] [true] → ask_clarification
    [] [true] → explanation
```

**问题不是模型没有分支，而是模型不知道各分支何时适用。** 即使所有阶段都可达，也不能据此说明分支选择符合业务要求。

Description + Guard 的目标是建立需求变更、反馈歧义、概念问题等有限条件，再据此启用对应出边；如果一条回复同时包含多种意图，还需要明确处理顺序或组合规则。

### 4.5 汇报例子四：只看成功状态，无法判断是否还需要自循环

来源为 `/stages/revise_slot/transitions/0`：

```json
{
  "target": "revise_slot",
  "description": "同一回复还有尚未应用的明确值；已完成的相对修改不得重复。",
  "guard": "input.stage_execution.doActivity.execution_status == \"success\""
}
```

假设用户明确提供的唯一修改已经写入成功。业务上没有剩余修改，自循环不应再因为同一条要求而继续执行；但显式 Guard 只检查成功，因此这条自循环仍被启用。

这里缺少两类信息：是否还有尚未应用的修改，以及当前修改是否已经处理。仅保留槽位当前值和动作成功状态，不能充分表达“只处理剩余修改、不得重复应用”的历史要求。由此可以判断自循环条件过宽，但不能仅凭这一条出边断言实际程序一定无限循环。

下一步需要评估如何有限地记录剩余修改和已处理情况，并将它们用于循环条件及动作更新规则。对这类 description，单独添加一个反馈类别也不够。

### 4.6 归纳：哪些情况下 Only-Guard 不够

| 不足的情况 | 对应例子 | 遗漏后可能出现的行为 |
|---|---|---|
| description 补充了 Guard 没有的必要条件 | 明确接受才能完成 | 未接受也允许完成 |
| description 决定多个分支各自的适用情境 | 修改、澄清、解释都使用 true | 不相关的分支也可被选择 |
| description 约束历史、次数或处理进度 | 尚有剩余修改才自循环 | 修改已处理仍允许重复进入 |

Only-Guard 能表达已结构化的条件；当决定行为的信息只存在于 description 中时，仅编译 Guard 就不足以表达原流程。这也是下一阶段扩展的具体依据。

## 5. 下一步：Description + Guard 联合转换

以下内容是下一阶段的设计计划，尚未实现。

### 5.1 先明确两者如何共同决定行为

首先确认 Controller_IR 的执行约定：Guard 是分支的必要前提，还是完整决策条件；description 是否用于进一步选择分支；多个分支同时满足时是否存在优先级。

对于“Guard 给出必要前提、description 补充业务条件”的出边，目标形式是：

```text
有效出边条件 = 原 Guard ∧ 从 description 提取的业务条件
```

这个形式不能机械应用于所有说明。背景文字、动作要求、条件补充和冲突要求需要分别识别；当 description 要求放宽或否定原 Guard 时，不能直接取交集，而应报告冲突并确定解释依据。

### 5.2 先提取语义，再建立有限抽象

计划增加一个语义整理步骤，从 transition 及相关 Stage、动作、policy 的说明中识别影响行为的信息，形成带原文依据的结构化提议。

```text
读取 Guard 与相关 description
    ↓
提取业务条件、结果类别、更新要求和必要的历史信息
    ↓
检查歧义、冲突及分支选择约定
    ↓
汇总原有 Guard 和新增判断
    ↓
统一建立有限抽象
    ↓
编译有效条件与更新 → CSP_IR → CSP# → PAT
```

新增判断应在有限抽象之前收集。例如，若需要区分“接受”和“修改”，就必须先把相关信息纳入抽象；不能在原模型只区分“反馈为空或非空”之后，再直接添加无法表达的接受条件。

### 5.3 保留条件来源，并检查模型中的信息是否足够

每项补充语义计划记录原文、输入路径、解释后的条件、所需变量及未解决问题。LLM 可以提出解释，代码负责校验结构、引用、有限值和可检查的一致性。

对于“接受当前版本”这样的要求，仅有“反馈类别为接受”还可能不够。如果流程允许修改草稿或需求，还需要判断反馈对应哪个版本。此类历史关联应明确建模，或记录为尚未覆盖的语义。

暂不要求模拟真实用户请求和响应协议；优先把流程决策需要的反馈结果与关联信息抽象成有限状态。

### 5.4 以审阅阶段完成第一轮验证

先集中处理 `present_and_review`，识别接受、修改、提问和含糊反馈对应的条件及目标。具体类别是否互斥，应依据原文确定；对于同时要求修改和提问等组合，明确组合规则或保留歧义。

建议按以下顺序推进：

| 阶段 | 交付内容 | 验收重点 |
|---|---|---|
| 语义整理 | 说明原文、结构化提议与缺口记录 | 关键业务条件有来源，冲突可见 |
| 条件建模 | 所需有限状态和有效 Guard | 新增判断可表达，不丢失原 Guard |
| 案例对比 | 同一输入的两个版本模型 | 展示新增约束改变了哪些允许路径 |
| PAT 检查 | 有针对性的性质和结果 | 仅动作成功不能直接导致完成；修改或提问时不能进入完成 |

完成第一轮后，再扩展到其他 Stage 的自然语言条件和必要的历史状态。

## 6. 组会上可以直接讲的版本

> 这阶段我完成了 Controller_IR 到 CSP_IR，再到 CSP# 的基础转换，并接入了 PAT 检查。当前版本的原有分支主要按显式 Guard 转换，我把它概括为 Only-Guard 版本。
>
> 实现上，我先收集整个流程使用的判断，根据这些判断建立统一的槽位有限抽象。明确规则由代码处理，复杂判断由 LLM 同时提出抽象状态和 Guard 映射，再由代码校验。之后，把 Stage 展开成动作步骤、成功失败结果、数据写回和阶段跳转，形成 CSP_IR，最后统一生成 CSP#。对于失败后没有出路的状态，还会检查是否存在有原文依据的补充分支，并保存诊断和来源。
>
> 具体看真实的 generate_3p Stage：它的 entry 清空旧反馈，doActivity 生成草稿，exit 为空。我先建立 entry、doActivity、transitions 三个控制位置。entry 成功时写入反馈为空，并进入生成位置；失败则不写回，直接进入 transitions。生成位置按有限结果展开：这次产物里有空草稿成功、非空草稿成功，以及失败三条边。成功边写回 draft 并记录 do_status 为 SUCCESS，失败边保留原 draft 并记录 FAILURE。到 transitions 后，把原 Guard 中的执行成功转换成 do_status 等于 SUCCESS，成立就更新 current_stage 并进入审阅阶段的第一个有效位置，同时重置目标阶段的三个步骤状态。对于两处失败状态，原出边都不成立，因此此次运行根据失败说明补出了 ABORTED 结束分支。这样，一个 Stage 就从输入字段一步步变成了四个控制位置和八条边。
>
> 但是审阅后的完成分支就暴露了问题。用户说“第二段请改一下，先不要定稿”，展示动作仍可能成功，草稿也非空，所以当前完成 Guard 仍然成立。但 description 要求用户明确接受，且没有修改或提问，因此这时本来不该完成。Only-Guard 丢掉了这个必要条件。
>
> 还有三条分别对应修改需求、澄清和解释的出边，Guard 都是 true。用户只问“3P 是什么意思”，模型却仍允许选择修改需求的分支。另一个例子是修改槽位后的自循环：说明要求还有未处理修改才循环，Guard 却只检查动作成功。这些例子说明，缺失的不只是判断表达式，也包括意图和处理历史。
>
> 下一步，我准备扩展到 Description + Guard 联合转换。先识别说明中影响流程的业务条件，整理成有原文依据的结构化表达，再和原 Guard 一起建立有限抽象、生成有效条件。同时明确处理冲突、歧义和多个分支同时成立的情况。第一轮会以审阅阶段的接受、修改和提问分支为案例，比较两个版本允许的路径，并用 PAT 检查完成条件是否符合原设计。

## 7. 汇报内容的代码依据

| 实现内容 | 对应文件 |
|---|---|
| 判断收集、依赖分组、统一抽象注册 | [registry.py](../src/conversion/registry.py) |
| 有限域规则、联合抽象与求值 | [domains.py](../src/conversion/domains.py)、[joint.py](../src/conversion/joint.py)、[rego.py](../src/conversion/rego.py) |
| Guard 编译 | [guards.py](../src/conversion/guards.py) |
| Stage、动作结果、原有出边与失败处理 | [compiler.py](../src/conversion/compiler.py) |
| 成功写回和列表更新 | [effects.py](../src/conversion/effects.py)、[updates.py](../src/conversion/updates.py) |
| CSP# 渲染和名称映射 | [renderer.py](../src/conversion/renderer.py)、[symbols.py](../src/conversion/symbols.py) |
| 模型保存、检查和结果报告 | [pipeline.py](../src/pipeline.py) |

进一步说明见：[实现说明](ControllerIR到CSPSharp-实现说明.md)、[语义缺口与改进分析](ControllerIR到CSPSharp-语义缺口与改进分析.md)。
