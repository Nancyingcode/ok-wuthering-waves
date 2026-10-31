# 椿 / 散华 / 守岸人导入队伍

当前源码为 `1.2.0` 输出段丢锁恢复候选，位于 `1.1.9` 之上（`1.1.9` = 回切正倒计时保护，基于 `1.1.7` 功能版本，保留 `1.1.6` 六秒回切恢复）。`1.1.8` 连续采样及包装已从生产实现撤下，诊断源码与原包备份在 `downloads/chunsanshou-1.1.9/`，历史采样测试归档到 `downloads/chunsanshou-1.1.8/diagnostic-tests/`。

## 1.2.0 变更

`1.1.9` 手测诊断（`downloads/chunsanshou-1.1.9/manual-3140-4580/诊断分析.txt`）定位出一条独立故障链：输出段丢锁等待按相位剩余时间申请预算，空等 11.687 秒，等待结束即撞 `camellya_burst: phase timeout`；该异常又被 `CombatCheck.in_combat` 的通用 `except Exception` 吞成 `None`，最终改写成脱战重入。

`1.2.0` 只处理这条链路，不放宽开花识别、输出上限、尾段判据和重试上限：

- **丢锁等待独立预算**：新增 `BLOOM_TARGET_WAIT_MAX = 2.0`（单次）与 `BLOOM_TARGET_WAIT_TOTAL = 3.0`（开花段累计）。等待预算不再由相位剩余时间给出，单次与累计各自封顶；预算耗尽或重锁失败即交回队内局部恢复，不再空等，也不再靠相位超时脱困。
- **开花段局部战斗判定**：开花输出段临时以 `do_check_in_combat` 取代 `CombatCheck.in_combat`，段内 `RotationTimeout` 直接冒泡回 `bloom_output` 的局部恢复，不再被改写成脱战；丢锁失败只在下一帧一次性交回恢复，此后真实脱战仍按原生语义处理。覆盖与 `target_enemy` 一样在 `finally` 中成对还原。
- **全局日志修复**：`src/combat/CombatCheck.py` 的 `logger.error(f'do_check_in_combat:', e)` 改为 `logger.error('do_check_in_combat: %s', e, exc_info=True)`。原写法消息里没有占位符却传了 `e`，格式化抛 `TypeError`，整条记录被 logging 丢弃，异常文本从未进日志。此行不改变控制流。

三个成员文件的 `PACKAGE_ID` 必须同为 `chunsanshou-v1.2.0-target-wait-budget`；`_team_members_match()` 要求一致，不一致会让整队静默退回内置轮转。

## 1.1.9 依据与范围（保留）

1.1.8 单场用户得分 4547，4R、3 次新开花、3 次收尾，没有输出超时或重击补按。本场中途回切前原图尚余 53 秒，原生锁敌约三秒返回失败，随后脱战并在约 0.068 秒后重入同一挑战。实际失败瞬间没有完整图，HUD 是否被变奏遮挡尚不确定。

1.1.9 只在椿回守岸人的临时 `in_combat` 判断中，使用已有 `get_abyss_remaining_time()`：

- 队伍 HUD 可读、没有显式结束条件，且读取到正倒计时，才以挑战仍在继续处理瞬时丢锁。
- 复用现有约 0.4 秒缓存；读取前后及后续输入仍受原回切预算检查约束。
- 倒计时为零/不可读、HUD 不可读、接口不可用或有显式结束条件时，沿用原生检测。
- 如果原生回切检查仍返回 False，单独保存 `combat_check_failed` 故障图与本次尝试耗时；它不等于局部超时。

原六秒总回切预算、首次 4.5 秒与最多一次剩余预算重试保持不变；散华和守岸人只更新同包身份。没有把 F 提示当作打断依据：诊断场第三花的全部 14 个样本都有 F 提示，回路仍正常耗尽。

1.1.8 连续采样三段占用执行线程约 2.873 秒，约为输出墙钟的 12.2%，另有后台 PNG 开销。如果失败时 HUD/倒计时不可读，正计时保护可能不触发。

## 实测状态与已知缺陷

1.1.9 已完成两场手测，分数 3140、4580，另有一场由用户主动取消；没有完成独立确认，整体稳定性筛选未通过。

- 正倒计时保护共触发三次，三次交接均有确认。
- 输出段锁敌曾空等约 11.7 秒，随后阶段超时被全局战斗检测吞成脱战；`1.2.0` 针对该路径。
- 一次回切等待结束时守岸人已在场、倒计时仍有 8.36 秒，但没有重新对账，仍误判脱战；`1.2.0` 未改动回切后的新帧对账。
- 尾段识别断档仍在：故障帧实测约 8.15%（三格）大于 `BLOOM_COMPLETE_FORTE = 0.06`，既进不了完成确认也凑不满两次下降，仍会走 `output timeout`；`1.2.0` 未改动该判据。

`1.2.0` 尚无实战数据；离线回归只说明控制流按设计改道，不能证明故障已在实战中解决。

## 构建与导入

```powershell
python custom_teams/ChunSanShou_Video/build.py
```

没有本地 `.venv` 时用 `python`；构建只依赖标准库，默认输出 `dist/ChunSanShou_Video_Codex_1.2.0.zip`。

导入时暂停自动战斗，确认版本 `1.2.0`、三个角色均使用“导入代码”。该导入队伍需要显式导入启用。

## 验证与证据

```powershell
python -B -m unittest discover -s tests -p 'TestChunSanShou*.py' -v
```

- 构建与归档用例使用系统临时目录，不再依赖工作区 `downloads` 的可写性。
- 导入器用例需要 `ok` 运行时（`ok-script`）；解释器缺少该模块时自动跳过，其余用例照常执行。
- `-B` 避免在只读子目录写 `__pycache__`。

以下为本地诊断产物，目录默认被 Git 忽略，不随源码提交：

- 诊断报告：`downloads/chunsanshou-1.1.8/manual-4547/诊断分析.txt`。
- 1.1.9 手测分析：`downloads/chunsanshou-1.1.9/manual-3140-4580/诊断分析.txt`。
- 候选校验与源码差异：`downloads/chunsanshou-1.1.9/verification.json`、`from-1.1.7.diff`。
- 旧包/源码：同目录 `ChunSanShou_Video_Codex_1.1.8.zip` 与 `baseline/`；`dist/` 中保留 1.1.7、1.1.8 原包。
