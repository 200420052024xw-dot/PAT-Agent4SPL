# Controller_IR → CSP#：Question1 失败分支转换方案

## 一、当前约定

本方案以 Controller_IR 的 Guard 和 description 为转换依据。

**主要流程：先检查失败状态下的 transitions；没有可用出边时，再调用 LLM 分析失败处理。**

当前默认 Guard 与 description 不存在“失败后必须停止，但已有出边仍允许继续”的冲突。该情况记录为后续事项，当前不增加专门的冲突分析或 LLM 调用。

本方案涉及 entry、doActivity、exit 的失败处理。Question2 的枚举探索规则见 [最终转换方案](ControllerIR到CSPSharp-最终转换方案.md)。

## 二、成功／失败的抽象

### 1. 命名

保留原有动作 Process，并用事件表示结果：

```text
Process：
xxx_entry
xxx_doActivity
xxx_exit
xxx_transitions

结果事件：
xxx_entry_success / xxx_entry_failure
xxx_doActivity_success / xxx_doActivity_failure
xxx_exit_success / xxx_exit_failure
```

CSP# 的事件名与 Process 名不得重名。具体失败处理位置使用独立 Process 名，例如 `xxx_entry_failure_handling`。

对结果不透明且可能失败的动作，保留成功与失败两种可能；存在明确结果约束时，按约束生成。只有 success 的出边，并不表示动作不可能失败。

### 2. 执行状态

```text
NOT_RUN：尚未执行
SUCCESS：执行成功
FAILURE：执行失败
```

原 Rego 判断使用明确的执行状态比较：

```rego
input.stage_execution.entry.execution_status == "success"
input.stage_execution.entry.execution_status == "failure"
```

映射为 CSP# 的有限值比较：

```csp
entry_status == STATUS_SUCCESS
entry_status == STATUS_FAILURE
```

不能用“不等于 success”代替 failure，因为它可能包含 NOT_RUN；缺失字段还涉及 Rego 的 undefined 语义。

## 三、步骤执行与状态更新

### 1. 成功

```text
记录当前步骤 SUCCESS
→ 执行 when_success_write_to 的抽象写回
→ 进入下一非空步骤
→ 最后进入 xxx_transitions
```

成功写回与相应结果事件的状态更新在 CSP# 中一起执行。

### 2. 失败

```text
记录当前步骤 FAILURE
→ 不执行该步骤的 when_success_write_to
→ 保留此前成功步骤已经产生的更新
→ 后续未执行步骤保持 NOT_RUN
→ 进入 xxx_transitions，检查已有出边
```

例如 entry 已经清空旧证据，而 doActivity 检索失败，不能把 entry 已完成的清空更新撤销。

若原文明确规定失败后执行清理 exit 等特殊步骤，需要单独表达，不能用上述常规失败路径覆盖该规定。

## 四、transitions 覆盖检查

### 1. 检查对象

检查的是失败状态下 Guard 是否成立，而不是 transitions 是否非空，也不是是否存在字面上的 failure Guard。

将该失败位置的执行状态代入原 Guard，并结合合法的有限 slot 状态求值。Rego 表达式必须按固定的 OPA 版本和 imports 解释。

### 2. 处理规则

| 检查结果 | 转换方式 |
|---|---|
| 所有合法失败状态都有可用出边 | 使用现有出边，不调用失败补全 LLM |
| 只有部分合法失败状态没有可用出边 | 调用 LLM，仅补充这些缺口 |
| 所有合法失败状态都没有可用出边 | 调用 LLM，分析该步骤的失败后继 |

例如，已有分支为：

```text
FAILURE 且 can_retry == true → 重试
```

则只补充 `FAILURE 且 can_retry == false` 时的处理，不覆盖已有重试分支。

### 3. 补充分支的范围

设现有出边的已转换条件为 G1、G2、……、Gn。补充分支只适用于：

```text
当前步骤失败
AND 所有已有出边条件均不成立
AND LLM 提出的、经校验的附加条件（如有）
```

“所有已有出边均不成立”应基于完成 Rego 语义转换后的条件计算，不能直接对原始 Rego 文本做简单取反。

多条已有出边同时成立时，保留原模型中的选择，不由失败补全擅自增加优先级。

## 五、LLM 失败补全

### 1. 调用时机

仅在覆盖检查发现失败状态没有可用出边时调用。

### 2. Input

1. Stage 名称及失败步骤。
2. Stage、动作及相关迁移的 description。
3. 当前 Stage 的全部 transitions 和 Guard。
4. 当前执行状态及未覆盖的有限状态条件。
5. 相关 slot 的抽象结果。
6. 已有 Stage 目标和可用控制位置。
7. Controller_IR 来源路径。

### 3. Output

```text
failure_behavior：terminate / pause / edge / retry / unresolved
target：已有目标；不适用时为空
condition：额外适用条件；没有时为空
evidence：支持失败处理的原文片段
reason：处理依据和解释
```

如果原文明确要求外部恢复，还需给出有原文依据的恢复条件及恢复位置。信息不足时返回 unresolved，不能编造恢复事件。

### 4. 校验与存储

代码检查原文依据、目标存在性、条件引用和补充分支范围。校验后的处理记录存入统一模型记录，并保留 stage、step、缺口条件、reason 和来源路径，随后由代码生成 CSP#。

## 六、失败处理的 CSP# 表达

| 原文明确含义 | CSP# 表达 |
|---|---|
| 失败后结束本次运行 | 标记 ABORTED 后进入 Skip |
| 暂停，当前建模范围没有恢复行为 | 标记 PAUSED 后进入具名暂停位置，以 Stop 表示阻塞 |
| 失败后跳到已有 Stage | 进入目标 Stage 的第一个有效部分 |
| 明确规定重试或恢复 | 按原文条件连接相应步骤，保留明确的更新规则 |
| 无法确定处理含义 | 记录 unresolved，保留缺口；不能当作已完成失败处理 |

`Skip` 表示终止，必须通过 ABORTED 标记或失败事件与业务完成区分。`Stop` 没有后续行为，不会自行恢复；可恢复暂停需要额外的环境及恢复行为模型。

真正的失败缺口不能默认改成 Skip，以免将缺少处理伪装成正常终止。

## 七、转换检查

1. 成功、失败与未执行状态能够区分。
2. 失败路径不执行成功写回。
3. 失败前已完成的状态更新得到保留。
4. 已有有效失败迁移没有被覆盖。
5. 新增处理只适用于未覆盖状态。
6. LLM 结果有原文依据，目标及 Guard 引用有效。
7. 失败结束、暂停阻塞与业务完成能够区分。
8. 生成的 CSP# 通过 PAT 语法及相关模型检查。

暂停阻塞可能导致 deadlockfree 检查不通过。应结合 PAUSED 状态和业务要求解释结果，不能仅为通过无死锁检查而将 Stop 改为 Skip。

## 八、完整流程

```text
编译动作结果分支和原 transitions
    ↓
构造各步骤可能的合法失败状态
    ↓
检查失败状态下现有 Guard 的覆盖情况
    ↓
全部覆盖 ───────────→ 使用现有出边
    ↓ 存在缺口
LLM 根据 description 和缺口分析失败处理
    ↓
代码校验原文依据、目标、条件和适用范围
    ↓
仅为缺口生成失败结束／暂停／跳转／重试分支
    ↓
保存来源、reason 和未解决诊断
    ↓
生成 CSP_IR → CSP# → PAT 检查
```

## 九、后续事项：guard: true 与失败说明冲突

记录的假设情况：

```text
description：失败后停止本次运行
transition：guard 为 true，仍允许进入后续业务阶段
```

这种情况下，仅检查“失败后有没有路”可能漏掉文字规定与出边的冲突。

当前核对的 `examples/Internal-Comms-IR.json` 和 `inputs/Controller_IR/Internal-Comms-IR.json` 中，明确规定失败停止的检索、生成、修订阶段，其出边均要求 doActivity 成功，未发现上述冲突。

**当前默认该冲突不会出现，暂不实现专门处理；后续再讨论冲突检测和消解规则。**

## 十、语法依据

- [OPA/Rego Policy Language](https://www.openpolicyagent.org/docs/policy-language)
- [PAT CSP# Process Definitions](https://www.comp.nus.edu.sg/~pat/OnlineHelp/scr/3.1%20CSP%20Module/3.1.1.2%20Process%20Definitions.htm)

失败结束与暂停的最小 CSP# 模型已经用仓库 PAT 验证：失败状态均可达；进入 Skip 的版本通过无死锁检查，进入 Stop 的版本不通过无死锁检查。验证文件位于 `tmp/question1_semantics_review/`。
