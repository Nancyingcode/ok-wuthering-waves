# AGENTS.md — 椿散守导入队伍测试与验收记录规范

适用范围：本目录（`custom_teams/ChunSanShou_Video`）的任何一次实战或离线测试。更具体于仓库根 `AGENTS.md` 与全局规则，冲突时以本文件为准。

## 1. 每次测试结束必须给出的三段内容

顺序固定，缺任一段视为本次测试未完成汇报。

1. **分轮次明细**：一轮一行，字段见 §2。
2. **汇总与归因**：见 §3。
3. **下一步规划**：见 §4。

## 2. 分轮次明细（每轮一行，必须写 R 与花）

| 字段 | 要求 |
|---|---|
| 轮次 | 第几轮实战，按实际执行顺序编号 |
| 得分 | 结果页「当前队伍得分」，不得用累计总分或历史最高分代替 |
| R（挑战进度） | 结果页「挑战进度 第N轮（x/5）」原文，例如 `第1轮(2/5)`；未推进也要写 |
| 开局角色 | 该轮实际起手角色 |
| 花 | 本轮 `bloom_output` 次数 |
| 完成 | `outcome=completed` 次数 |
| 干净完成 | `tail_extra=0.000` 且 `tail_grace=False` 的次数，逐个列 `output_wall` |
| 宽限 / 超预算 | `tail_extra>0` 的每一个，列 `output_wall` 与 `+超量` |
| 未收尾 | 退出类型（`output timeout` / `tail progress lost` / `tail grace timeout` / `wall timeout` / `target lost`）计数 |
| 重击/花 | `heavy_retries` 合计 ÷ 花数 |
| 墙钟 | 本轮耗时秒 |
| 版本确证字段 | `bloom timing` 行是否带本版字段（`floor=` / `steady=` / `unreadable_grace=` 等），并给出 `True` 出现次数 |

只统计该轮自身数据；`resumed=True` 与 `resumed=False` 必须分开，8 秒口径只对首次开花成立。

## 3. 汇总与归因

- 汇总：均值、中位、极差、样本标准差；合计 R / 花 / 完成 / 干净完成；样本数不足时明确写「样本太小」。
- 未收尾归因：每一个都必须抄 `event=exit output_wall=… status={…} history_reset=… history=[…]` 原文关键字段，并归类为：三格地板 / 中高位稳态 / 贴零 / 零证据断流（`samples=0`、`consumed_ratio=-1`）/ 边界断流（`stale_sample` 且 `consumed_ratio>=0.65`）/ 真实未打完（`outside_tail`）/ 丢锁回切。
- 判定结论只能取以下之一，并写明依据：
  - **有效**：本版出口在实战中上场（确证字段出现 `True`）且行为符合设计；
  - **未验证**：判据一次未上场，即使均值变好也不得判有效；
  - **无效 / 误放行**：反例形态被放行，或确证字段出现但行为与设计不符。
- 均值跨版本不可比，除非极差与 σ 足够小并显式说明；不得用「均值变好」替代判据上场证据。

## 4. 下一步规划（必须给出，且只给一条主线）

1. 本版结论（有效 / 未验证 / 无效）与直接原因一句话。
2. 下一版唯一目标：只允许一处逻辑变更；同一版不得同时动尾段出口与 `tail_extra` 过冲 / `bloom_resume_budget` / P5 / P6 / P7。
3. 开工前必做的取证（可核对的日志行、artifact 路径、需要摊开的字段），先证据后改码。
4. 下一版验收口径：确证字段名、必须出现的日志行、反例护栏清单。
5. 明确不并进下一版的事项清单。

## 5. 硬性红线

- 八个硬约束常量与 `BLOOM_COMPLETE_FORTE` / `BLOOM_ENDED_FORTE` 每版都必须用 `git diff -U0` 复核赋值行 `hits=0`。
- `bloom_tail_status` 的因子与先后顺序不得改动。
- 三处 `PACKAGE_ID` 必须一致，否则整队静默退回内置轮转：这是最容易漏、也最容易被误读成「判据无效」的坑，汇报时必须附装包回执里的前后 `PACKAGE_ID`。
- 离线回归 `python -B -m unittest discover -s tests -p 'TestChunSanShou*.py'` 必须报出：用例数、跳过数、失败数、新增用例清单；唯一允许的跳过是 ok-script 运行时缺失。
- 不得 commit / push / 打 tag，除非用户明确要求。

## 6. 采集命令（实战后照抄）

```powershell
$p='<run.log 或 ok-script.log>'
Select-String -LiteralPath $p -Pattern 'bloom timing outcome=' | ForEach-Object { $_.Line }
Select-String -LiteralPath $p -Pattern 'event=exit output_wall=' | ForEach-Object { $_.Line }
Select-String -LiteralPath $p -Pattern 'bloom floor completion|bloom steady completion|bloom unreadable grace completion|bloom completion confirmed' | ForEach-Object { $_.Line }
```

统计 `heavy_retries`、`tail_extra`、`unreadable_grace=True` 等字段时，用日志里真实存在的匹配串（例如 `'reason': 'stale_sample'` 带引号），不要凭印象写串；命中 0 不等于「没有该形态」，必须先用一条原文校验匹配串。
