# Agent Instructions

## Python

- When running Python commands in this repository, use the local virtual environment if it exists.
- On Windows/PowerShell, prefer `.\.venv\Scripts\python.exe` when present.
- On POSIX shells, prefer `./.venv/bin/python` when present.
- Fall back to `python` only when no local `.venv` interpreter exists.
- Prefer invoking the interpreter directly, for example `.\.venv\Scripts\python.exe -m pytest`, instead of relying on shell activation.

## MCP 自动测试

- 使用 MCP 接管游戏执行自动测试时，矩阵（Matrix）用例必须停在最终结果页：进入结算/结果页后不点击返回、不退出、不重开，保持现场供截图与核对。
- 停在结果页属于判定通过的一部分；若测试流程包含返回主界面、再战一次等后续步骤，矩阵用例跳过这些步骤。
