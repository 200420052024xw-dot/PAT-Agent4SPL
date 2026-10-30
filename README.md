# PAT-Agent ControllerIR 转换与检测

当前实现读取新版 ControllerIR，生成有限的 CSP_IR 与 CSP#，由 AI 根据 IR 和模型提出检查项，再用 PAT 检测。检测结束后只报告问题和反例，不自动修改 IR 或模型。

## 运行

需要 Python 3.11+、项目依赖、有效的 `.env` 中 `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL_ID`，以及仓库内的 PAT Console。已有 `.venv` 时可将下列命令中的 `python` 换成 `.\.venv\Scripts\python.exe`。

```powershell
python -m pip install -e .
python -m src convert
python -m src convert inputs/Controller_IR/Internal-Comms-IR.json
python -m src convert inputs/Controller_IR/Internal-Comms-IR.json --model-only
python -m unittest discover -s tests -v
```

默认输入是 `inputs/Controller_IR/Internal-Comms-IR.json`。`--env-file`、`--pat-executable`、`--pat-timeout`、`--output-root` 可覆盖对应设置。每次运行在 `runs/controller_ir_to_csp/` 下创建独立目录。

完整 Rego 表达式需要官方 OPA。可通过 `OPA_EXECUTABLE` 指向可执行文件，也可将 OPA 放入 PATH；Windows 项目内的 `tmp/opa.exe` 也会被识别。本次验证使用 OPA 1.21.0。只有判空、长度、固定成员等简单判断时，代码可以直接解释。Rego 方言默认 `v1`；输入顶层可设置 `"rego": {"version": "v0", "imports": [...]}`。导入设置会影响否定语义，必须与原 policy 一致。

## 运行产物

| 文件 | 内容 |
|---|---|
| `model.csp_ir.json` / `model.csp` | 已校验的有限模型及 PAT 文本 |
| `model.draft.csp_ir.json` / `model.draft.csp` | 转换有缺口时供人工核对的草稿，文件头列出缺口，不运行 PAT |
| `checks.json` | AI 生成、经代码校验的检查项及 IR 依据 |
| `source_map.json` | 变量、控制位置、事件、检查项对应的 IR 位置 |
| `render_map.json` | 内部 ID、有限值编码与最终 CSP# 名称、常量的对应关系 |
| `abstraction_registry.json` | 统一槽位状态、判断编号、Guard 树、真／假／未定义判断表及抽象依据 |
| `failure_records.json` | 失败补全提议、所属步骤、来源及是否实际应用 |
| `verification_results.json` | PAT 实际结果、预期结果与反例路径 |
| `diagnostics.json` / `manifest.json` | 转换缺口、检查违反情况及运行状态 |
| `llm_trace.json` | 排除 `participant_config` 和凭据后的语义调用记录 |

状态及退出码：`CONVERTED=0`、`PASS=0`、`VIOLATION=1`、`INCOMPLETE=2`、`ENGINE_ERROR=3`。`--model-only` 只完成模型转换，输出 `model.csp` 并返回 `CONVERTED`，不生成检测项或调用 PAT；`PASS` 表示已执行的检查与预期一致。`VIOLATION` 表示检测发现问题，转换和 PAT 仍已完成；`INCOMPLETE` 表示 IR 或补全结果无法形成可靠有限模型。

完整转换模型在 AI 检查项生成前保存，后续服务或检测失败也可查看 `model.csp`。转换中存在缺口，但已构建的模型引用和有限域有效时，保存 `model.draft.csp`、对应 CSP_IR 和 `source_map.draft.json`；输入结构无效或模型引用无效时无法生成草稿。命令行会打印实际 CSP# 文件路径，`manifest.json` 的 `model_file` 与 `model_complete` 标明产物及完整性。

`model.csp_ir.json` 保留内部 ID、哈希后缀和有限值的数字编码。最终 `model.csp` 使用可读的变量、进程及事件名称，并为有限值生成具名常量；初始化、条件、写回、参数和断言统一使用该映射。仅清洗后实际重名或遇到保留字时添加序号。`source_map.json` 使用最终名称，使 PAT 反例可以回溯到 IR；内部名称与最终名称的关系保存到 `render_map.json`。草稿对应保存 `render_map.draft.json`。

转换先收集全部 Stage 和 policy 的判断。同一槽位的混合类别或未知判断通过一次 LLM 调用同时返回状态、Guard 和依据；跨槽位依赖组成联合有限变量。代码核对代表值的 Rego 求值和已知候选覆盖，但代表值验证不能证明未知业务条件的完整性，相关近似会写入诊断。缺失、null、空字符串和空列表分别保留。

动作失败后先检查 transitions；全部合法失败状态已有出边时不调用失败补全 LLM。缺口补充分支限定为“当前步骤失败且所有原出边均不成立”。有依据的失败结束标记为 ABORTED 并进入 Skip；暂停标记为 PAUSED 并进入 Stop；明确的重试回到当前步骤。未知枚举成功写回展开全部合法枚举值，初始化 null／缺失不会自动成为成功结果。

没有依据的失败去向保留为 `FAILURE_GAP` 警告，可由 PAT 检查死锁；不会阻止输出 `model.csp`。警告同时写入模型注释和 `diagnostics.json`，并提供给 AI 检查项生成。模型转换完成不表示原流程已经通过验证。按当前约定，暂不额外处理 `guard: true` 与失败描述冲突的情况。

## 代码入口

- `src.main.main()`：命令行入口。
- `src.pipeline.convert_controller_ir()`：程序调用入口。
- `src.conversion.compile_controller()`：ControllerIR 到 CSP_IR。
- `src.conversion.renderer.render_csp()`：CSP_IR 到 CSP#。
- `src.verification.run_pat_checks()`：逐项调用 PAT。

各功能模块有公开入口，辅助函数负责单一任务。新增注释使用中文，函数保持简短。`archive/` 是旧实现和论文资料；`Versions/` 是历史版本存档（当前为 Only-Guard v1 快照及其缺陷记录）。二者均不参与新版转换运行。

详细转换规则见 [最终转换方案](docs/ControllerIR到CSPSharp-最终转换方案.md)，接口、代码阅读顺序及实现边界见 [实现说明](docs/ControllerIR到CSPSharp-实现说明.md)。
