# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

PAT-Agent 读取新版 ControllerIR（JSON，描述多阶段 Agent 流程、槽位与 Rego policy），编译为有限 CSP_IR 并渲染 CSP#，由 LLM 依据 IR 和模型提出检查项，再逐项调用 PAT（Process Analysis Toolkit）做模型检测。检测只报告问题和反例，不自动修改 IR 或模型。

## 当前状态（2026-10-30）

v1（Only-Guard 版本）已存档至 `Versions/Only-Guard-Version/`（实现快照、缺陷记录、代表性运行产物），主目录的 `src/`、`llm/`、`tests/`、`pyproject.toml` 已随之移除。v2（Description + Guard 联合转换）待开发，落地前主目录无法运行下述命令；v1 可按 `Versions/Only-Guard-Version/README.md` 的说明从存档目录运行。v2 需求见 `Versions/Only-Guard-Version/缺陷记录.md`，v1 缺陷分析另见 docs/ 下组会汇报与语义缺口文档。

## 常用命令

Windows 环境，Python 3.11+。仓库自带 `.venv`（`.\.venv\Scripts\python.exe`），以下命令中的 `python` 均可替换为它。

```powershell
python -m pip install -e .                        # 安装依赖（openai、pydantic、python-dotenv）
python -m src convert                             # 完整运行：转换 + AI 检查 + PAT（默认输入 inputs/Controller_IR/Internal-Comms-IR.json）
python -m src convert <input.json> --model-only   # 只生成 model.csp，不生成检查项、不调用 PAT
python -m unittest discover -s tests -v           # 全部测试
python -m unittest tests.test_conversion -v       # 单个测试模块
python -m unittest tests.test_conversion.ConversionTests.test_pipeline_reports_pat_violation_without_repair -v  # 单个测试
```

运行前提：

- `.env` 需含 `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL_ID`（OpenAI 兼容端点，由 `llm/config.py` 读取）。
- PAT 控制台位于 `PAT.Console/Process-Analysis-Toolkit/PAT3.Console.exe`，可用 `--pat-executable` 覆盖。
- 复杂 Rego 表达式需要官方 OPA（验证版本 1.21.0）：设 `OPA_EXECUTABLE`、放入 PATH，或放到 `tmp/opa.exe`。判空、长度、固定成员等简单判断由代码直接解释。
- 其他覆盖项：`--env-file`、`--pat-timeout`、`--output-root`。

状态与退出码：`CONVERTED=0`、`PASS=0`、`VIOLATION=1`、`INCOMPLETE=2`、`ENGINE_ERROR=3`。每次运行在 `runs/controller_ir_to_csp/<时间戳>-<序号>/` 下生成独立目录（model.csp、checks.json、source_map.json、render_map.json、abstraction_registry.json、failure_records.json、verification_results.json、diagnostics.json、manifest.json、llm_trace.json）。

## 架构

### 流水线与入口

```
ControllerIR(JSON) --compile_controller()--> CSP_IR(有限模型) --render_csp()--> CSP# 文本
                                                                      |
                              LLM 提出检查项(attach_checks) + 代码校验 |
                                                                      v
                                              run_pat_checks() 逐项渲染、逐项执行 PAT
```

- 命令行入口 `src.main.main()`；程序调用唯一入口 `src.pipeline.convert_controller_ir()`（`src/pipeline.py`）。
- `src/conversion/compiler.py:compile_controller()` 负责 ControllerIR → CSP_IR；`src/conversion/renderer.py:render_csp()` 负责 CSP_IR → CSP#；`src/verification/pat.py:run_pat_checks()` 逐项调用 PAT（每项检查单独渲染一个 .csp，避免结果混淆）。
- `src/model.py` 定义 CSP_IR 的核心类型：`FiniteVariable`/`StateValue`（有限变量与抽象状态）、`Expr`（有限布尔条件树）、`Edge`/`Process`（流程边与控制位置）、`CSPIR`。

### 转换的核心流程（compiler.py 内部）

1. `input.py` 读取并验证 ControllerIR（支持 JSONC 注释）。
2. `registry.py` 先收集**全部** Stage 和 policy 的判断（observation），同一表达式共用编号，按槽位依赖分组。
3. `domains.py`/`rego.py` 用代码规则处理已知判断模式；`joint.py` 对混合类别、未知判断和跨槽位依赖发起**一次** LLM 联合抽象调用，返回有限状态和 Guard→状态索引映射。
4. `guards.py` 将原条件编译为有限条件树；`execution.py` 处理同时读取执行状态和槽位的判断表；`effects.py`/`updates.py` 处理成功写回、枚举展开和列表增删。
5. compiler 检查失败覆盖：动作失败先查 transitions，全部合法失败状态已有出边时不调用失败补全 LLM；仅对缺口调用 `resolve_failure`，无法确定的保留 `FAILURE_GAP` 警告交给 PAT 查死锁。

### 代码与 LLM 的分工（最重要的设计约束）

- **LLM 从不直接输出 CSP# 文本**。它只返回 JSON（状态、索引映射、提议等），全部由代码校验（代表值用 OPA 复核、候选覆盖检查），校验失败则转换停止并给出诊断。
- `src/semantic.py:SemanticProvider` 协议定义六个语义入口：`resolve_abstraction`、`resolve_guard`、`resolve_failure`、`resolve_effects`、`resolve_policy`、`propose_checks`。`OpenAISemanticProvider` 是实现在线版本；`llm/` 包是底层 OpenAI 兼容客户端。
- 语义调用记录写入 `llm_trace.json`（已脱敏，排除 `participant_config` 和凭据）。

### 命名与可追溯性

- CSP_IR 内部使用内部 ID 和哈希后缀；最终 CSP# 使用可读名称。`symbols.py` 建立映射，`source_map.json`（IR 路径）和 `render_map.json`（内部名→最终名）使 PAT 反例能回溯到 IR 位置。
- 模型先落盘再生成检查项，因此后续 LLM 或 PAT 失败时 `model.csp` 仍可查看；转换有缺口但引用有效时保存 `model.draft.csp`（文件头列出缺口，**不**送入 PAT）。

## 关键约定

- **注释和文档字符串使用中文**；函数保持简短（约 50 行以内），辅助函数只做一件事；每个模块有明确的公开入口。
- 特殊值语义不可互换：无 `default_value` → MISSING；`default_value: null` → NULL；`""`/`[]` → 各自类型的 EMPTY。可选声明 `allow_missing`、`nullable`、`allow_null_result` 控制额外状态。
- 顶层 `rego` 声明方言（默认 v1）和 imports；导入设置影响否定语义，必须与原 policy 一致。
- 候选规模有硬上限，超限返回诊断而非静默截断（联合候选 4096、固定列表成员 8、列表代表长度 128、字符串代表长度 4096）。
- 测试使用固定的 LLM 替身（`FakeSemantic`、`tests/joint_fixtures.py` 的 `JointSemantic`）验证请求格式和单次联合调用；OPA 与 PAT 使用真实可执行程序，缺少引擎时对应集成测试跳过（不算通过）。
- `archive/` 是旧版实现和论文资料，不参与新版转换运行；`Versions/` 是历史版本存档（当前仅 `Versions/Only-Guard-Version/`：v1 完整实现快照、缺陷记录与代表性运行产物，只读，用于 v1/v2 案例对比基线）；`examples/` 是 IR 对应的原始 Agent 动作实现，仅作语义参考。
- 详细转换规则见 `docs/ControllerIR到CSPSharp-最终转换方案.md`，接口与实现边界见 `docs/ControllerIR到CSPSharp-实现说明.md`，失败分支方案见 `docs/ControllerIR到CSPSharp-失败分支转换方案.md`。
