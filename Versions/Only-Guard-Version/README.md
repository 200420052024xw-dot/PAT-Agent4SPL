# Only-Guard 版本存档（v1）

存档日期：2026-10-30。这是 Description + Guard 联合转换（v2）开发前，第一版完整实现的快照。

## 定位

以显式 Guard 为唯一出边依据的 ControllerIR → CSP_IR → CSP# → AI 检查 → PAT 链路。能建立有限模型、生成 CSP# 并支持模型检查（转换基础已实现），但 description 中的业务决策要求未进入出边条件（业务语义覆盖不完整）。

**实际缺陷见本目录 [缺陷记录.md](缺陷记录.md)**，它是 v2 的需求依据。转换规则与实现细节见仓库 `docs/` 下的 ControllerIR到CSPSharp 系列文档与组会汇报。

## 内容

| 内容 | 说明 |
|---|---|
| `src/`、`llm/`、`tests/`、`pyproject.toml` | v1 完整实现快照（已清除 `__pycache__`） |
| `runs/20261010-212818-000/` | v1 代表性运行产物（组会汇报中 generate_3p 示例的引用来源；`runs/` 在主仓库被 gitignore，此处为固化副本） |
| `缺陷记录.md` | 实际缺陷记录与 v2 需求要求 |

## 路径假设与运行方式

代码按仓库根目录布局编写：`inputs/`、`.env`、`PAT.Console/`、`tmp/opa.exe` 均为仓库根相对路径，本存档不复制这些资产，沿用主仓库的 `.venv` 与可执行程序。从存档目录运行需显式指回仓库根：

```powershell
cd Versions\Only-Guard-Version
..\..\.venv\Scripts\python.exe -m src convert ..\..\inputs\Controller_IR\Internal-Comms-IR.json --env-file ..\..\.env --pat-executable "..\..\PAT.Console\Process-Analysis-Toolkit\PAT3.Console.exe" --output-root runs
```

OPA 需设 `OPA_EXECUTABLE` 指向仓库 `tmp/opa.exe` 或置于 PATH。`tests/` 引用仓库根的 inputs 与 PAT，在存档位置直接运行会因路径假设失败，作为 v1 行为规约参考保留。

## 与 v2 的关系

- v2 在仓库 `src/` 上继续开发，本存档保持只读。
- 本存档的可运行副本用于**案例对比**（同一输入在两个版本下的模型差异、允许路径差异）与 v1 行为核对，这是组会汇报 §5.4 验收计划的一部分。
- 不要在本存档上继续开发；发现 v1 缺陷时先更新 `缺陷记录.md`，再到 v2 中处理。
