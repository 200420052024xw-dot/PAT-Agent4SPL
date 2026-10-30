# Controller_IR → CSP#：新版实现说明

## 1. 代码现在如何工作

转换顺序如下：

```text
验证 Controller_IR
→ 确定非空 policy 的执行位置和结果判断
→ 收集所有槽位判断，保存完整 Guard 及来源
→ 按依赖分组，使用代码或一次 LLM 调用建立有限域
→ 校验代表值、判断表和默认值映射
→ 生成动作成功／失败、写回和原有 transitions
→ 检查失败覆盖，仅对缺口请求 LLM
→ 保存 CSP_IR、注册表和失败记录，渲染 CSP#
→ 生成检查项并调用 PAT
```

`--model-only` 在模型保存后结束。它仍可能需要 LLM 完成抽象或失败分析。

## 2. 建议的代码阅读顺序

| 文件 | 负责什么 |
|---|---|
| `src/model.py` | 有限状态、条件树、更新和流程边的结构 |
| `src/conversion/registry.py` | 收集判断、按依赖分组、保存统一结果 |
| `src/conversion/domains.py` | 判空、长度、固定成员等代码规则 |
| `src/conversion/rego.py` | 简单求值与官方 OPA 调用 |
| `src/conversion/joint.py` | 单次 LLM 联合抽象及结果校验 |
| `src/conversion/execution.py` | 同一表达式同时读取执行状态和槽位时的判断表 |
| `src/conversion/guards.py` | 将原条件转换为有限条件树 |
| `src/conversion/effects.py`、`updates.py` | 成功写回、枚举展开、联合槽位更新、列表增删 |
| `src/conversion/compiler.py` | 连接 Stage、检查失败覆盖、补充缺口 |
| `src/conversion/renderer.py`、`symbols.py` | 输出合法且可读的 CSP# |
| `src/semantic.py` | LLM 请求格式、调用及脱敏记录 |
| `src/pipeline.py` | 统一入口及运行产物保存 |

代码使用中文注释和文档字符串，辅助函数按一个任务拆分，函数保持在 50 行以内。

## 3. 特殊值与输入声明

没有 `default_value` 时初始化为 MISSING；`default_value: null` 为 NULL；`""` 和 `[]` 为各自类型的 EMPTY。不能互相替代。

可选声明：

| 字段 | 含义 |
|---|---|
| `allow_missing: true` | 即使有默认值，也保留可能缺失的状态 |
| `nullable: true` | 额外保留 null 状态 |
| `allow_null_result: true` | 未知成功结果允许返回 null |
| `enum_values` 包含 null | null 本身就是合法枚举结果 |

初始化 null 不表示动作成功也能返回 null。已知的明确清空写回可以映射到已有 NULL 状态。

顶层 `rego` 声明方言和 imports；默认采用 `{"version":"v1","imports":[]}`。本次验证使用 OPA 1.21.0；代码不自动固定本机 OPA 的发行版本，部署时应保持一致。

Rego Guard 字符串支持 `input.name` 和 `input.slots.name` 两种路径。完整表达式中的赋值、存在性判断和绑定交给 OPA，不拆成几个独立成员判断。原生 `not` 同样交给 OPA；Controller_IR 的 `all_of`／`any_of`／`not` 对象保留为外层布尔条件树。

## 4. LLM 联合抽象的接口

单槽位的混合类别、未知判断，以及跨槽位依赖，调用 `resolve_abstraction(context)`。上下文包含槽位信息、相关 Stage、动作、policy、description、判断编号、来源、Rego 设置和代码候选值。

返回示例：

```json
{
  "states": [
    {"name": "EMPTY", "sample": ""},
    {"name": "SHORT", "sample": "x"},
    {"name": "LONG", "sample": "xxx"}
  ],
  "guards": [
    {"id": "observation_001", "allowed": [0], "undefined": []},
    {"id": "observation_002", "allowed": [2], "undefined": []}
  ],
  "reason": "按判空与长度大于 2 的合法判断组合划分"
}
```

这里两个判断分别是 `input.name == ""` 与 `count(input.name) > 2`。`allowed`／`undefined` 使用 `states` 的数组索引；其余状态表示判断不成立。代码确定初始映射和生成后的名称，LLM 不直接输出 CSP# 文本。

缺失状态使用 `{"name":"MISSING","sample":null,"missing":true}`；显式 null 使用 `{"name":"NULL","sample":null}`。联合组的 `sample` 为槽位名到值的对象，缺失键省略。

若完整表达式还读取 `stage_execution`，该判断的返回项使用 `cases`：每项包含 `status`、`allowed`、`undefined`，覆盖上下文提供的全部 `status_cases`。槽位状态与执行状态分别存储，最多检查三个步骤的 27 种执行状态组合。

同一组只发起一次逻辑上的联合抽象请求，Guard 复用这次结果。仅输出非法 JSON 时，服务接口会要求重发一次；没有有效状态、判断缺失、代表值不符或已知组合遗漏时，转换停止并给出诊断。

## 5. 写回与失败

未知枚举写回展开全部合法枚举结果；已知写回只保留明确值。联合变量写回一个槽位时保持其他槽位的已观察信息；policy 的明确多槽位写回按组同时更新。

列表 `append` 添加一个元素；`remove` 删除第一个匹配元素，没有匹配则保持原值。该约定属于转换器支持的操作语义，其他删除方式需要另外声明并扩展。已知规则会保留长度边界内外、重复成员和其他成员的代表值，避免删除后遗漏可能的成员状态。

失败不执行当前步骤的成功写回；之前的成功更新保留；后续步骤标为 NOT_RUN。失败进入 transitions 后，先检查有限状态覆盖。只有缺口才调用 `resolve_failure`，补充分支限制为：

```text
当前步骤失败 AND 所有原出边均不成立 AND 可选附加条件
```

`run_outcome` 区分 RUNNING、COMPLETED、ABORTED、PAUSED。结束进入 Skip；暂停进入 Stop；明确重试回到当前步骤，保留之前步骤的数据和状态。无法确定时保留缺口并输出 `FAILURE_GAP`，交给 PAT 检查。

`failure_records.json` 保存提议和 `applied` 标记。`applied: true` 表示生成了分支，仍可能有未覆盖部分，需结合诊断查看。

## 6. 当前边界

- LLM 代表值校验能发现已知组合遗漏，不能证明未知业务条件在无限原空间中的完备性；`reason` 和 `ABSTRACTION_APPROXIMATION` 会保留这一限制。
- 未知业务谓词下的增删及跨槽位更新仍依赖所提供的代表值；需要更严格保证时，应补充业务更新关系和完整性证明。
- 检查项不能临时引入原有限域无法区分的新长度或业务条件；遇到这种情况应重新建立抽象。
- 失败补充条件也必须可由当前有限域表达；恢复环境、特殊清理顺序或未声明的操作不会自动编造。
- 当前按约定假设 `guard: true` 不与失败描述冲突，不进行额外冲突分析。
- 代码设有候选规模限制，超限返回诊断，避免静默截断：联合候选最多 4096，固定列表成员最多 8，列表代表长度最多 128，字符串代表长度最多 4096。

## 7. 运行与验证

```powershell
$env:OPA_EXECUTABLE = "F:\PAT-Agent-master\tmp\opa.exe"
.\.venv\Scripts\python.exe -m src convert --model-only
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

测试使用固定 LLM 回答检查请求格式、单次联合调用和结果校验；OPA 与 PAT 使用真实可执行程序。缺少引擎时对应集成测试会显示跳过，不能当作实际引擎验证成功。在线模型的回答质量需要用实际业务输入继续评估。
