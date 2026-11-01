# 椿 / 散华 / 守岸人导入队伍

当前源码为 `1.2.26` 边界后趋势收尾候选，位于 `1.2.25` 之上（`1.2.25` = 标记可见时的单帧结束确认，位于 `1.2.24` 之上（`1.2.24` = 越界后单帧结束确认，位于 `1.2.23` 之上（`1.2.23` = 硬预算复核点前移，位于 `1.2.22` 之上（`1.2.22` = 重锁计数与读数可读性解耦，位于 `1.2.21` 之上（`1.2.21` = 输出段丢锁轻量重锁，位于 `1.2.20` 之上（`1.2.20` = 已消耗尾段上跳停滞出口，位于 `1.2.19` 之上（`1.2.19` = 丢锁零读数早退，位于 `1.2.18` 之上（`1.2.18` = 贴地三格停滞出口，位于 `1.2.17` 之上（`1.2.17` = 估算降级只认停滞，位于 `1.2.16` 之上（`1.2.16` = 估算窗口下界修复，位于 `1.2.15` 之上（`1.2.15` = 估算收敛窗口改按秒，位于 `1.2.14` 之上（`1.2.14` = 估算读数收敛，位于 `1.2.13` 之上（`1.2.13` = 回路环读数容差，位于 `1.2.12` 之上（`1.2.12` = 读数不可读时以标记消失收尾，位于 `1.2.11` 之上（`1.2.11` = 零读数未开环早退，位于 `1.2.10` 之上（`1.2.10` = 已消耗稳态尾段出口，位于 `1.2.9` 之上（`1.2.9` = 预算边界读数断流出口，位于 `1.2.8` 之上；`1.2.8` = 三格地板出口，位于 `1.2.7` 之上；`1.2.7` = 中高位稳态出口，位于 `1.2.6` 之上；`1.2.6` = 贴零稳态出口，位于 `1.2.5` 之上；`1.2.5` = 尾段基线改用本段观测峰值、回滚满格滑动窗口，位于 `1.2.4` 之上；`1.2.4` = 满格停滞滑动窗口，位于 `1.2.3` 之上；`1.2.3` = 满格平台期抖动修复，位于 `1.2.2` 之上；`1.2.2` = 满格平台期补按，位于 `1.2.1` 之上；`1.2.1` = 开花尾段判据与起步消耗修复，位于 `1.2.0` 之上；`1.2.0` = 输出段丢锁恢复，位于 `1.1.9` 之上；`1.1.9` = 回切正倒计时保护，基于 `1.1.7` 功能版本，保留 `1.1.6` 六秒回切恢复）。`1.1.8` 连续采样及包装已从生产实现撤下，诊断源码与原包备份在 `downloads/chunsanshou-1.1.9/`，历史采样测试归档到 `downloads/chunsanshou-1.1.8/diagnostic-tests/`。

## 1.2.26 变更

`1.2.25` 两轮实战 5537 / 5573（均值 5555、极差 36、σ≈25.5，8 花 / 7 完成 / 5 干净完成 / 2 宽限 / 1 超时 / 1 未收尾），`boundary_marker=True` 上场 **2 次**且两次都在标记仍可见时按设计收尾，`untargeted` 与 `target lost` 均 0 次，判「有效（样本 2 次）」；但 `tail_extra` 最大仍到 **+0.406**，未达 ≤0.20 验收线。取证：

- `+0.406` 那朵（`resumed=True`、`resume_forte=0.996`）：`bloom tail grace start forte=0.156 output_wall=8.000 samples=44` → `bloom boundary confirm elapsed=8.339 marker_visible=True forte=0.057 status={'reason': 'progressing', 'decreases': 13, 'drop_total': 0.247, 'consumed_ratio': 0.943}`。即边界时读数 0.156 仍在单调下降，需再降 0.1 才到 `BLOOM_COMPLETE_FORTE`，过冲全花在这段真实下降上。
- 另两段 `+0.110` / `+0.077` 同源（边界帧读数略高于结束阈值），`8.077` 那朵属边界断流（`forte=0.329 > 0.25`，1.2.9 正确拒绝）。

`1.2.26` 只加一条趋势门，不动任何既有判据：

- **新增常量**：`BLOOM_BOUNDARY_TREND_FORTE = 0.20`、`BLOOM_BOUNDARY_TREND_RATIO = 0.90`、`BLOOM_BOUNDARY_TREND_DECREASES = 3`、`BLOOM_BOUNDARY_TREND_DROP = 0.15`、`BLOOM_BOUNDARY_TREND_MAX_AGE = 0.25`。
- **新判据**：`bloom_boundary_trend_allowed`——`output_wall >= output_budget` 且宽限已开始后，读数可读且 `<= 0.20`、窗口内下降次数 `>= 3`、累计降幅 `>= 0.15`、样本新鲜 `<= 0.25` 秒、按本段峰值已消耗 `>= 0.90`、本段见过标记、同角色在场、非解放态、`resumed or elapsed >= 5.1` 时按完成收尾。
- **确证字段**：`bloom timing` 行新增 `boundary_trend=`；命中时打印 `bloom boundary trend completion …`。
- **完全不动**：`BLOOM_COMPLETE_FORTE = 0.06`、`BLOOM_ENDED_FORTE = 0.05`、八个硬约束常量、`bloom_tail_status` 因子与顺序、1.2.6–1.2.25 全部出口与门限（含 1.2.25 的 `BLOOM_BOUNDARY_MARKER_RATIO`）。

三个成员文件的 `PACKAGE_ID` 必须同为 `chunsanshou-v1.2.26-bloom-boundary-trend`。

`1.2.26` 尚无实战数据。主指标为 `tail_extra` 最大值降到 ≤0.20（1.2.25 批 +0.406）；反例护栏＝`outside_tail` 真实未打完（1.2.24 批 `forte=0.873` / 消耗 12.4%）与边界断流（1.2.25 批 `forte=0.329` / 消耗 67% / `sample_age=0.89`）必须继续超时、`boundary_confirm` / `boundary_marker` 行为不变、`relock` / `relock_readable` / `budget_guard` 不变、`insufficient_samples` 不得回升到每批 ≥3 次。

## 1.2.25 变更

`1.2.24` 两轮实战 5447 / 5153（均值 5300、极差 294、σ≈208，8 花 / 5 完成 / 3 干净完成 / 4 段超预算 / 3 未收尾），`boundary_confirm=True` **0 次**，`tail_extra` 由 +0.328 恶化到 **+1.360**，判「未验证」。两段过冲取证如下：

- `+0.688` 段（`output_wall=8.688`）：整段读数不可读（`forte=-1.000`），已由 1.2.9 `unreadable_grace` 在 8.641s 收尾（`reason='stale_sample'`、`consumed_ratio=0.844`），1.2.24 通道结构上不可能上场。
- `+1.360` 段（`output_wall=9.360`）：`tail grace start forte=0.032 output_wall=8.032`，读数 0.032 → 0.057（均 `<= BLOOM_COMPLETE_FORTE`）、`consumed_ratio=0.943`，但**开花标记仍在屏**，`bloom_ended` 与 1.2.24 通道同时被 `marker_visible` 挡住，`recheck_start` 与 `forte_rise` 反复重置复核直至 9.297 判 `tail progress lost`。

`1.2.25` 只放开「标记仍可见」这一种形态，不动任何既有门限：

- **新增常量**：`BLOOM_BOUNDARY_MARKER_RATIO = 0.90`。
- **判据变更**：`bloom_boundary_confirm_allowed` 不再一律拒绝 `marker_visible`——标记消失时仍需已消耗 `>= 0.65`，标记可见时要求 `>= 0.90`；其余条件（宽限已开始、读数可读且 `<= BLOOM_COMPLETE_FORTE`、本段见过标记、同角色在场、非解放态、`resumed or elapsed >= 5.1`）不变。
- **确证字段**：`bloom timing` 行新增 `boundary_marker=`，命中时日志多带 `marker_visible=`。
- **完全不动**：`BLOOM_COMPLETE_FORTE = 0.06`、`BLOOM_ENDED_FORTE = 0.05`、八个硬约束常量、`bloom_tail_status` 因子与顺序、1.2.6–1.2.24 全部出口与门限。

三个成员文件的 `PACKAGE_ID` 必须同为 `chunsanshou-v1.2.25-bloom-marker-confirm`。

`1.2.25` 两轮实战 5537 / 5573（均值 5555、极差 36、σ≈25.5，8 花 / 7 完成 / 5 干净完成 / 2 宽限 / 1 超时 / 1 未收尾），`boundary_marker=True` 2 次且两次都在标记仍可见时按设计收尾，1.2.24 那种 +1.360 长过冲未复现，判「有效（样本 2 次）」，但最大过冲仍到 +0.406。原定验收：反例护栏＝`outside_tail` 真实未打完（1.2.24 批 `forte=0.873` / `consumed_ratio=0.124`）必须继续超时、读数不可读形态仍走 1.2.9 / 1.2.12 原通道、`relock` / `relock_readable` / `budget_guard` 行为不变、`insufficient_samples` 不得回升到每批 ≥3 次。

## 1.2.24 变更

`1.2.23` 两轮实战 6211 / 5429（均值 5820、极差 782、σ≈553，8 花 / 7 完成 / 6 干净完成 / 1 宽限 / 1 未收尾），`budget_guard` 确证字段上场（1 朵 28 次复核），但 `tail_extra` 从 +0.171 恶化到 **+0.328**，判「无效」。取证推翻了「复核点太晚」这一前提：

- 8.437s 那朵原文 `bloom tail grace start forte=0.082 output_wall=8.016` → `bloom completion confirmed elapsed=8.324 forte=0.032 ended_frames=2`，8.0→8.328 之间 `check_combat` / `check_target` / 重击补按计数全为 0，`budget_guard=28` 全是空转复核。
- 因此过冲 = 边界后仍在真实消耗（合法延后）+ 结束确认固定要两帧；守 10 秒硬上限的复核点无法压缩它。

`1.2.24` 只加一条越界后的确认通道，不动任何既有判据：

- **新增常量**：`BLOOM_BOUNDARY_CONFIRM_RATIO = 0.65`。
- **新判据**：`bloom_boundary_confirm_allowed`——`output_wall >= output_budget` 后，读数可读且 `<= BLOOM_COMPLETE_FORTE`、开花标记已消失、本段此前见过标记、同角色在场、非解放态、`resumed or elapsed >= 5.1`、按本段峰值已消耗 `>= 0.65` 时按完成收尾。
- **确证字段**：`bloom timing` 行新增 `boundary_confirm=`；命中时打印 `bloom boundary confirm elapsed=… output_wall=… forte=…`。
- **完全不动**：`BLOOM_COMPLETE_FORTE = 0.06`、`BLOOM_ENDED_FORTE = 0.05`、八个硬约束常量、`bloom_tail_status` 因子与顺序、1.2.6–1.2.23 全部出口与门限（含预算内「连续两帧」规则）。

三个成员文件的 `PACKAGE_ID` 必须同为 `chunsanshou-v1.2.24-bloom-boundary-confirm`。

`1.2.24` 两轮实战 5447 / 5153（均值 5300、极差 294、σ≈208，8 花 / 5 完成 / 3 干净完成 / 4 段超预算 / 3 未收尾），`boundary_confirm=True` 0 次、`tail_extra` 恶化到 +1.360，判「未验证」——过冲主因不是两帧确认成本，而是标记仍在屏时读数已达结束阈值仍被挡住。原定验收：反例护栏＝`outside_tail` 真实未打完（1.2.21 批 `forte=0.675` / `consumed_ratio=0.323`）必须继续超时、读数不可读形态仍走 1.2.12 标记消失通道、`relock`/`relock_readable` 行为不变、`insufficient_samples` 不得回升到每批 ≥3 次。

## 1.2.23 变更

`1.2.22` 两轮实战 4961 / 5842（均值 5401.5），7 花全部收尾，`target lost` 与 `untargeted` 均 0 次，判「有效（样本 2 次）」；但 8.468s 那朵 `tail_extra=+0.171`，说明硬预算仍会被跨过一个以上阻塞调用。取证：`tail_extra` 已连续两版为正（1.2.21 批 +0.078、1.2.22 批 +0.171），1.2.17 批更出现过 +2.078（`wall=10.078`，已越过 8+2）。

`1.2.23` 只补预算复核点，不动任何判据与出口：

- **唯一逻辑变更**：`guard_budget` 在 `char.check_combat()`、`char.check_target(True)`、`maintain_bloom_heavy(...)` 与 `self.task.next_frame()` 四个阻塞调用前各复核一次硬预算，不再只能等下一次帧边界才发现越界。
- **确证字段**：`bloom timing` 行新增 `budget_guard=`，统计宽限窗内（`output_wall >= output_budget`）执行的复核次数；`0` 表示本段预算从未耗尽。
- **完全不动**：`bloom_tail_status` 因子与顺序、1.2.6–1.2.22 全部判据与门限、`BLOOM_COMPLETE_FORTE = 0.06`、`BLOOM_ENDED_FORTE = 0.05`、八个硬约束常量。

三个成员文件的 `PACKAGE_ID` 必须同为 `chunsanshou-v1.2.23-bloom-budget-guard`。

`1.2.23` 两轮实战 6211 / 5429（均值 5820、极差 782、σ≈553，8 花 / 7 完成 / 6 干净完成 / 1 宽限 / 1 未收尾），`budget_guard=28` 上场 1 次但 `tail_extra` 恶化到 +0.328，判「无效」——guard 守的是 8+2 秒硬上限，压不下由两帧确认造成的过冲。原定验收：反例护栏＝`outside_tail` 真实未打完（1.2.21 批 `forte=0.675` / `consumed_ratio=0.323`）必须继续超时、`relock` / `relock_readable` 行为不变、`estimate_fallback>0` 与 `estimate_drops>0` 须继续同现、`insufficient_samples` 不得回升到每批 ≥3 次。

## 1.2.22 变更

`1.2.21` 两轮实战 5891 / 5507（均值 5699、极差 384、σ≈271.5，8 花 / 6 完成 / 5 干净完成 / 1 宽限 / 2 未收尾），`relock=1` 上场 1 次且 `recovered=True`，但主指标 `target lost` 未降（1→1），判「有效（样本 1 次）但不解决问题」。取证：

- run1 唯一一次重锁：`bloom target relock count=1 unlocked_frames=3 elapsed=0.707 output_wall=0.703 forte=-1.000`、`recovered=True total_wait=0.093`，属读数不可读形态。
- run2 `target lost during output wall=5.703`：`wait_count=1`、`relock=0`、`estimate_drops=6`，退出状态 `reason='insufficient_samples'`、`samples=0`、`history_reset=retarget` —— 该段读数多数可读，旧逻辑先看「读数可读」就清零丢锁帧数，重锁门永远攒不到 3 帧。

`1.2.22` 只放宽重锁计数，不动任何收尾判据：

- **唯一逻辑变更**：丢锁帧数改由 `has_target()` 单独统计，与读数是否可读解耦；1.2.19 的 `target_unlocked_since` 计时口径逐字不动。
- **确证字段**：`bloom timing` 行新增 `relock_readable=`；每次重锁多打印 `readable=`。
- **完全不动**：`bloom_tail_status` 因子与顺序、1.2.6–1.2.21 全部判据与门限、`BLOOM_COMPLETE_FORTE = 0.06`、`BLOOM_ENDED_FORTE = 0.05`、八个硬约束常量。

三个成员文件的 `PACKAGE_ID` 必须同为 `chunsanshou-v1.2.22-bloom-relock-readable`。

`1.2.22` 两轮实战 4961 / 5842（均值 5401.5、极差 881、σ≈623，7 花 / 7 完成 / 6 干净完成 / 1 宽限 / 0 未收尾），`relock=1` 两次全部 `recovered=True`、`relock_readable=1`，`target lost` 与 `untargeted` 均 0 次，判「有效（样本 2 次）」。主指标仍为 `untargeted` 与 `target lost` 次数下降（1.2.21 批 0 / 1）；反例护栏＝`outside_tail` 真实未打完（1.2.21 批 `forte=0.675` / `consumed_ratio=0.323`）必须继续超时、`estimate_fallback>0` 与 `estimate_drops>0` 须继续同现、`insufficient_samples` 不得回升到每批 ≥3 次。

## 1.2.21 变更

`1.2.20` 两轮实战 3767 / 5745（均值 4756、7 花 / 5 完成 / 5 干净完成 / 0 宽限 / 3 未收尾），`consumed_stall=True` **0 次**，判「未验证」（新出口零上场）。三次未收尾里两次在 3 秒内退出，取证如下：

- run1 `bloom output untargeted wall=3.562`：`output_wall=3.562`、`unlocked_for=2.052`、`wait_count=0`、`heavy_retries=0`，同段 `check_target Release/Retry=80/85`，而 `target lost try retarget` **0 次** —— 锁定在输出第 1.42 秒就丢了，之后没有任何补锁动作，读数只能整段不可读。
- run2 `target lost during output wall=5.093`：整段只有一次重锁（`target lost try retarget 2.0`），耗满 2.078 秒后 `target_enemy failed`，`heavy_retries=2`。

`1.2.21` 只补重锁动作，不改任何收尾判据：

- **新增常量**：`BLOOM_TARGET_RELOCK_FRAMES = 3`、`BLOOM_TARGET_RELOCK_TIMEOUT = 0.5`、`BLOOM_TARGET_RELOCK_MAX = 2`。
- **新判据**：`bloom_target_relock_allowed`——读数不可读且 `has_target()` 为假连续 ≥3 帧、同角色在场、开花标记仍可见、非解放态、本段重锁次数 <2 时补一次 0.5 秒重锁；重锁成功即重置 1.2.19 的丢锁计时，失败则继续累计，仍交回 1.2.19 早退与原超时。
- **预算**：重锁等待仍从 `BLOOM_TARGET_WAIT_MAX = 2.0` / `BLOOM_TARGET_WAIT_TOTAL = 3.0` 池里出，不新开预算、不延长输出窗口。
- **确证字段**：`bloom timing` 行新增 `relock=`；每次重锁打印 `bloom target relock count=…` 与 `bloom target relock result count=… recovered=…`。
- **完全不动**：`bloom_tail_status` 因子与顺序、1.2.6–1.2.20 全部判据（含 1.2.19 的 `untargeted` 与 1.2.20 的 `consumed_stall` 门限）、`BLOOM_COMPLETE_FORTE = 0.06`、`BLOOM_ENDED_FORTE = 0.05`、八个硬约束常量。

三个成员文件的 `PACKAGE_ID` 必须同为 `chunsanshou-v1.2.21-bloom-target-relock`。

`1.2.21` 尚无实战数据。主指标改为 `untargeted` 与 `target lost` 次数下降（1.2.20 批各 1 次）；反例护栏＝`outside_tail` 真实未打完（1.2.20 批 run2 `forte=0.378` / `consumed_ratio=0.62`）必须继续超时、`estimate_fallback>0` 与 `estimate_drops>0` 须继续同现、`insufficient_samples` 不得回升到每批 ≥3 次。

## 1.2.20 变更

`1.2.19` 两轮实战 5311 / 5520（均值 5415.5、极差 209、σ≈147.8，7 花 / 5 完成 / 5 干净完成 / 0 宽限 / 2 未收尾），`untargeted=True` **0 次**，判「未验证」（早退零上场：本批 `insufficient_samples` 归零，没有任何零可读窗口）。两次未收尾取证：

- run1 `event=exit output_wall=8.000 status={'reason': 'insufficient_decreases', 'forte': 0.279, 'samples': 45, 'sample_age': 0.0, 'decreases': 1, 'drop_age': 0.61, 'span': 1.5, 'steady_hold': 0.61, 'steady_range': 0.0, 'steady_flat': False, 'drop_total': 0.025, 'consumed_ratio': 0.72}`、`history_reset=forte_rise`（7.375 时 0.279→0.304）。
- run2 `event=exit output_wall=8.000 status={'reason': 'outside_tail', 'forte': 0.823, 'samples': 32, 'consumed_ratio': 0.174}`，属真实未打完，原超时正确拒绝（反例护栏守住）。

`1.2.20` 只加一条与 `1.2.10` 并列的出口，不动任何既有判据：

- **新增常量**：`BLOOM_TAIL_CONSUMED_STALL_SPAN = 0.4`。
- **新出口**：`bloom_consumed_tail_stall_allowed`——输出预算耗尽后 `reason='insufficient_decreases'`、`drop_total <= BLOOM_TAIL_DROP_TOTAL`、`consumed_ratio >= 0.65`、`steady_range <= 0.005`、尾部平窗 `steady_hold >= 0.4`、两次重击补按已用尽、同角色在场且开花标记仍可见、非解放态时按完成收尾。
- **确证字段**：`bloom timing` 行新增 `consumed_stall=`。
- **完全不动**：`bloom_tail_status` 因子与顺序、1.2.6–1.2.19 全部判据（含 1.2.10 的 `steady_hold>=1.0` 与 1.2.18/1.2.19 出口）、`BLOOM_COMPLETE_FORTE = 0.06`、`BLOOM_ENDED_FORTE = 0.05`、八个硬约束常量。

三个成员文件的 `PACKAGE_ID` 必须同为 `chunsanshou-v1.2.20-bloom-consumed-tail-stall`。

`1.2.20` 尚无实战数据。验收须在 `bloom timing` 行读到 `consumed_stall=True`；反例护栏＝`outside_tail` 真实未打完（本批 `forte=0.823` / 消耗 17.4%）必须继续超时、`estimate_fallback>0` 与 `estimate_drops>0` 须继续同现、`insufficient_samples` 不得回升到每批 ≥3 次。

## 1.2.19 变更

`1.2.18` 两轮实战 4185 / 4842（均值 4513.5、极差 657、σ≈464.6，7 花 / 5 完成 / 3 干净完成 / 2 宽限 / 2 未收尾），`floor_stall=True` **0 次**，判「未验证」（新出口零上场）。两次未收尾同源取证如下：

- run2 `stroke=84` 段：`samples=0`、`estimate_fallback=79`、`estimate_hold=7.986`、`estimate_drops=0`，退出 `status={'reason': 'insufficient_samples', 'forte': -1, 'consumed_ratio': -1.0}`；该窗口内只有 1 帧识别遥测（`budding_forte=0.996 / estimated=True / visible_ticks=39 / team_skill.state='no_bloom_ring'`），`target_state=unlocked`、`target_match=('no_target', …)`、`check_target Retry/Release` 69/65、`heavy_retries=0`、`bloom target wait` 一次都没调用 —— 8 秒里没有锁定目标，读数不可能下降，1.2.17 的「下降即重开窗口」按设计不介入，收敛把窗口老化成零样本。
- run1 `forte=0.724` 段：`outside_tail`、`consumed_ratio=0.273`，属真实未打完，原超时正确拒绝（反例护栏守住）。

结论：零证据断流由 P5 丢锁驱动，不是收敛截断；`1.2.18` 不回滚（形态罕见 ≠ 无效），P5 单独立项。

`1.2.19` 只加一条与 `1.2.11` 并列的早退判据，不动任何收尾判据：

- **新增常量**：`BLOOM_OUTPUT_UNTARGETED_SPAN = 2.0`。
- **懒采样锁态**：仅当读数不可读时才调一次 `has_target()`（可读帧零开销），记录连续丢锁起点。
- **新出口（早退）**：`bloom_output_untargeted`——`elapsed >= BLOOM_NOT_ENGAGED_AFTER`、连续丢锁 >=2.0 秒、开花标记仍在屏、同角色在场、非解放态、窗口零可读样本时以 `outcome='bloom output untargeted'` 收段交回轮转；**不判完成**。
- **确证字段**：`bloom timing` 行新增 `untargeted=`。
- **完全不动**：`bloom_tail_status` 因子与顺序、1.2.6–1.2.18 全部判据（含 1.2.11 的标记不可见门、1.2.18 的 `floor_stall`）、`BLOOM_COMPLETE_FORTE = 0.06`、`BLOOM_ENDED_FORTE = 0.05`、八个硬约束常量。

三个成员文件的 `PACKAGE_ID` 必须同为 `chunsanshou-v1.2.19-bloom-output-untargeted`。

`1.2.19` 尚无实战数据。验收须在 `bloom timing` 行读到 `untargeted=True`；反例护栏＝任何出现过可读读数的段一律不得早退、`floor_stall`/`outside_tail` 既有行为不变、`insufficient_samples` 不得回升到每批 ≥3 次。

## 1.2.18 变更

`1.2.17` 两轮实战 5883 / 4812，7 花 / 6 完成 / 5 干净完成 / 1 未收尾，`estimate_drops` 5/7 朵 >0、`estimate_fallback` 7/7 朵 >0、两轮 `insufficient_samples` 归零，判「有效」。唯一未收尾是 run2 一条 `tail grace timeout`，取证如下：

- 退出 `status={'reason': 'progressing', 'forte': 0.082, 'samples': 41, 'sample_age': 0.172, 'decreases': 6, 'drop_age': 0.657, 'span': 1.36, 'zero_hold': -1.0, 'steady_hold': 0.485, 'steady_range': 0.0, 'drop_total': 0.124, 'consumed_ratio': 0.918}`，`wall=10.078`、`tail_extra=+2.078`、`heavy_retries=2`。
- 读数已贴在三格地板窗内、尾部 0.485 秒纹丝不动、按峰值已消耗 91.8%；只因最近一次下降仅在 0.657 秒前（`drop_age <= 0.75`）使 reason 落在 `progressing`，被 1.2.8 地板出口的 `reason in ('stale_decrease','insufficient_decreases')` 与 `steady_hold >= 1.0` 双重挡住，8 秒软边界不接、10 秒硬上限直接抛 `tail grace timeout`。

`1.2.18` 只新增一条并列出口，不动任何既有判据：

- **新增常量**：`BLOOM_TAIL_FLOOR_STALL_SPAN = 0.4`。
- **新出口**：`bloom_floor_stall_allowed`——输出预算耗尽后，`reason='progressing'`、读数落在 `BLOOM_COMPLETE_FORTE < forte <= BLOOM_TAIL_FLOOR_MAX`、`steady_range <= 0.005`、尾部平窗 `steady_hold >= 0.4`、`consumed_ratio >= 0.65`、两次重击补按已用尽、同角色在场且开花标记仍可见、非解放态时按完成收尾。
- **确证字段**：`bloom timing` 行新增 `floor_stall=`。
- **完全不动**：`bloom_tail_status` 因子与顺序、1.2.6–1.2.17 全部判据、`BLOOM_COMPLETE_FORTE = 0.06`、`BLOOM_ENDED_FORTE = 0.05`、八个硬约束常量。

三个成员文件的 `PACKAGE_ID` 必须同为 `chunsanshou-v1.2.18-bloom-floor-stall`。

`1.2.18` 尚无实战数据。验收须在 `bloom timing` 行读到 `floor_stall=True`；反例护栏＝仍在下降的 `progressing` 段（尾部平窗不足 0.4 秒）必须继续超时、`estimate_drops>0` 与 `estimate_fallback>0` 仍须同时出现、`insufficient_samples` 不得回升到每批 ≥3 次。

## 1.2.17 变更

`1.2.16` 的降级窗口（0.5 秒）在实战里确实上场了，但暴露出一个新的截断面：run1 有一段 `estimate_fallback=62 / estimate_hold=5.791` 的花被判 `insufficient_samples`（`samples=0`、`consumed_ratio=-1`、`history_reset=forte_unknown`），而同段识别序列是 `0.996 → 0.502 → 0.106` —— 上界一直跟着真实消耗在走，却被「连续估算时长超限」整段判成不可读，真实进度被抹成零样本。

同段其余证据：`heavy_retries=0`、`check_target Retry/Release`≈105/100、`target_match=('no_target', 0.918)`、退出帧 forte 裁图 40 格仍近全亮；`not_engaged=False` 与 `marker_end=False` 同时成立只有一种自洽解（二者对 `marker_seen` 的要求互斥），即开花标记仍在屏 —— 该段仍判「真没打完」，根因属 P5 丢锁，不并版。

`1.2.17` 只改降级的触发条件，不动任何判据：

- **新增常量**：`BLOOM_FORTE_ESTIMATE_DROP = 0.015`。
- **降级只认停滞**：新增 `bloom_estimate_progressing(previous, forte)`；估算读数较上一估算帧下降 ≥0.015 时视为有效进度，`estimate_since` 从本帧重开，不再累计到 0.5 秒窗口。
- **确证字段**：`bloom timing` 行新增 `estimate_drops=`（估算读数下降帧数），与 `estimate_fallback=` / `estimate_hold=` / `ring_estimate_streak=` 配对。
- **完全不动**：`BLOOM_FORTE_ESTIMATE_MAX_SECONDS = 0.5`、`budding_forte_from_observations` 换算语义、`bloom_tail_status` 因子与顺序、1.2.6–1.2.16 全部判据、`BLOOM_COMPLETE_FORTE = 0.06`、`BLOOM_ENDED_FORTE = 0.05`、八个硬约束常量。

三个成员文件的 `PACKAGE_ID` 必须同为 `chunsanshou-v1.2.17-bloom-forte-estimate-progress`。

验收：`bloom timing` 须同时读到 `estimate_fallback>0` 与 `estimate_drops>0`（证明降级仍能上场、且不再吃掉下降进度）；反例护栏＝`0.996` 停滞不退（`estimate_drops=0`）的段必须继续降级并超时，`insufficient_samples` 不得回升到每批 ≥3 次。

## 1.2.16 变更

`1.2.15` 两轮实战暴露一个反效果：21 条 `bloom timing` 行的 `estimate_fallback` 全为 0，即秒级收敛窗口一次都没触发，1.2.13 的「偏高上界长期替代精确读数」问题重新放开。取证如下：

- 8 段开花实测 `ring_estimate_streak` 为 7 / 11 / 14 / 19 / 21 / 23，`estimate_fallback` 全 0 ⇒ 23 帧连续估算累计不足 1.2 秒 ⇒ 该段帧间隔 < 0.052 秒（≥19 帧/秒）。1.2.15 文档里按「约 8 帧/秒」推算的等价关系不成立。
- 1.2.14 的 10 帧上限在现场正是约 0.5 秒触发过（`ring_estimate_streak` 18 / 24 / 68 均降级），说明按秒的窗口要落在 0.5 秒量级才够得到。

`1.2.16` 只改窗口数值与遥测，不动任何判据：

- **常量**：`BLOOM_FORTE_ESTIMATE_MAX_SECONDS` 由 `1.2` 改为 `0.5`，仍由 `bloom_estimate_converged` 按连续估算时长判定。
- **新增确证字段**：`bloom timing` 行加 `estimate_hold=`（本段最长连续估算时长，秒），与 `estimate_fallback=`（降级帧数）、`ring_estimate_streak=`（最长连续估算帧数）配对。
- **完全不动**：`budding_forte_from_observations` 换算语义、`bloom_tail_status` 因子与顺序、1.2.6–1.2.15 全部判据、`BLOOM_COMPLETE_FORTE = 0.06`、`BLOOM_ENDED_FORTE = 0.05`、八个硬约束常量。

三个成员文件的 `PACKAGE_ID` 必须同为 `chunsanshou-v1.2.16-bloom-forte-estimate-floor`。

验收：`bloom timing` 必须读到 `estimate_fallback>0` 至少 1 次（并核对同段 `estimate_hold>=0.500`）；反例护栏＝本批 `forte=0.502` 的 `outside_tail` 与 `forte=0.106` 的 `stale_decrease` 必须继续超时，`insufficient_samples` 不得回升到每批 ≥3 次。

## 1.2.15 变更

`1.2.14` 两轮实战里 `ring_estimate_streak=68` 那次未收尾（`output timeout`、`output_wall=8.063`、`'reason': 'insufficient_samples'`、`samples=0`）已按交接 §7.2 取证完毕，结论是**真没打完**，不是收敛截断：

- 退出帧 forte 裁图为 40 格近全亮（`budding_forte=0.996`、`visible_ticks=39`、`estimated=True`），同帧全屏图 boss `封庭械囧` Lv.100 血条仍 `16010/16010`、`当前队伍积分 1673`、屏上无开花环（`team_skill.state='no_bloom_ring'`）。
- 同段 `target_match=('no_target', 0.918)`、`check_target Retry/Release` 各 182 次、`heavy_retries=0`：8 秒里既没锁到目标也没打出伤害，读数钉在高位是真实剩余量。根因属 P5 丢锁，单独立项，不并进本版。

`1.2.15` 只改收敛窗口的表达方式，不动任何判据：

- **常量替换**：`BLOOM_FORTE_ESTIMATE_MAX_STREAK = 10`（帧数）→ `BLOOM_FORTE_ESTIMATE_MAX_SECONDS = 1.2`（秒）。实测约 8 帧/秒时 10 帧只有约 1.2 秒，20 帧/秒就只剩 0.5 秒，降级时机跟着帧率漂。
- **判定改按秒**：新增 `bloom_estimate_converged(estimate_since, elapsed)`，连续估算持续 ≥ 1.2 秒才把本帧降级为 `forte = -1.0`。
- **确证字段**：`bloom timing` 行新增 `estimate_fallback=`（降级帧数），`ring_estimate_streak=` 保留为帧数遥测。
- **完全不动**：`budding_forte_from_observations` 换算语义、`bloom_tail_status` 因子与顺序、1.2.6–1.2.14 全部判据、`BLOOM_COMPLETE_FORTE = 0.06`、`BLOOM_ENDED_FORTE = 0.05`、八个硬约束常量。

三个成员文件的 `PACKAGE_ID` 必须同为 `chunsanshou-v1.2.15-bloom-forte-estimate-window`。

`1.2.15` 尚无实战数据。验收须在 `bloom timing` 行读到 `estimate_fallback=` 且值 >0；反例护栏仍为 1.2.13 的三条 `outside_tail`（`0.996` 消耗 0.0 / `0.799` 消耗 0.199 / resume `0.675` 消耗 0.256）必须继续超时，且 `insufficient_samples` 不得回升到每批 ≥3 次。

## 1.2.14 变更

`1.2.13` 两轮实战 5329 / 4034（均值 4681.5、极差 1295、σ≈915.7，8 花 / 5 完成 / 3 未收尾），`ring_fallback=True` 命中 7/8 花、`insufficient_samples` 归零，判「有效（因果未证）」。但取证发现估算读数是偏高上界：`budding_forte=0.996` 在整段每次采样都出现（`consumed_ratio=0.0`、`steady_flat=True`），`0.799` 一档 `consumed_ratio=0.199`，两次都是「真没打完」的 `outside_tail`；上界长期替代精确读数，会让「已打完」与「没动」无法区分。

`1.2.14` 只加一条收敛约束，不动任何判据：

- **新增常量**：`BLOOM_FORTE_ESTIMATE_MAX_STREAK = 10`。
- **入账前收敛**：`bloom_output` 里连续估算帧累计超过上限时，本帧读数按不可读处理（`forte = -1.0`），交回 1.2.9 的不可读门与原超时；一旦拿到精确读数或整帧不可读，计数归零。
- **确证字段**：`bloom timing` 行新增 `ring_estimate_streak=`（本段最长连续估算帧数），与 `ring_fallback=` 并列。
- **完全不动**：`budding_forte_from_observations` 的换算语义、`BLOOM_COMPLETE_FORTE = 0.06`、`BLOOM_ENDED_FORTE = 0.05`、八个硬约束常量、`bloom_tail_status` 因子与顺序、1.2.6–1.2.13 全部判据。

三个成员文件的 `PACKAGE_ID` 必须同为 `chunsanshou-v1.2.14-bloom-forte-convergence`。

`1.2.14` 两轮实战 4318 / 4486（均值 4402、极差 168、σ≈118.8），两轮 R 均停在「第1轮（2/5）」、推进 0；6 花 / 5 完成 / 4 干净完成 / 1 宽限 / 1 未收尾，重击 0.17 次每花。`ring_fallback=True` 6/6 花，`ring_estimate_streak` 分别为 18 / 68 / 5 / 10 / 24 / 6，收敛确证字段确认上场；本批 `insufficient_samples` 1 次（低于验收上限 3 次）、`outside_tail` 0 次，`marker_end` / `consumed_tail` / `unreadable_grace` / `not_engaged` 各 0 次，判「有效（收敛按设计生效，副作用待证）」。唯一未收尾是 `ring_estimate_streak=68` 那次的 `output timeout`（`output_wall=8.063`、`'reason': 'insufficient_samples'`、`samples=0`、`consumed_ratio=-1`、`history=[]`、同段 `not_engaged=False` 说明标记仍在屏）：收敛满 10 帧后本帧按不可读处理、窗口老化到空，属设计行为。**已知风险**：无法证明那朵花是否真打完，这是本版唯一可能新增的截断面；1.2.15 须先摊开该段逐帧识别遥测与对应截图取证，再决定是否允许降级期间沿用标记消失收尾。验收仍须读到 `ring_estimate_streak=` 且值 >0。

## 1.2.13 变更

`1.2.12` 修正评估口径后两轮实战 5329 / 5206（均值 5267.5、σ≈87，8 花 / 6 完成 / 6 干净完成 / 0 宽限），`marker_end=True` 上场 1 次，判「有效（样本 1 次）」。剩下两次未收尾同形：`output timeout`（`output_wall=8.015`、`'reason': 'insufficient_samples'`、`samples=0`、`consumed_ratio=-1`、`not_engaged=False`、`marker_end=False`）与 `target lost`（`target_wait=2.000`，P5 丢锁，单列）。

取证：本轮 18 条 `bloom recognition` 遥测里 `visible_ticks=31/36/39`（已过原 `visible < 28` 门限）仍 `state='no_bloom_ring'`、`segments=0`，对应 `budding_forte_pixels` 里「任一格 `not found` 就整帧判 `unknown`」的分支——单格被特效吃掉就让整帧读数作废，随后 8 秒窗口攒不到样本、被兜成 `output timeout`。这不是收尾判据的缝隙，是识别层缺容差。

`1.2.13` 只动识别层：把逐格换算抽成纯 Python 的 `CamellyaTeamVision.budding_forte_from_observations`，并加「高可见度下个别刻度缺失」的容差。

- **新增常量**：`BLOOM_FORTE_MIN_VISIBLE = 28`（沿用原可见度门限）、`BLOOM_FORTE_FALLBACK_MIN_VISIBLE = 34`、`BLOOM_FORTE_MISSING_TOLERANCE = 2`。
- **估算语义**：只有 `visible_ticks >= 34` 且充能段内缺失不超过 2 格时才估算，缺失格按「仍然充能」计入，得到的是**偏高的上界读数**——上界只会让「已打完」更难成立，绝不会让收尾阈值提前触发；估算态标记为 `state='filled_estimated'` 与 `estimated=True`。可见度不足、缺失超过 2 格、或刻度整片不可见时仍返回 `value=-1.0`，与旧行为一致。
- **确证字段**：`bloom timing` 行新增 `ring_fallback=True/False`（本段是否用过估算读数）。
- **完全不动**：`bloom_tail_status` 的因子与顺序、1.2.6–1.2.12 全部判据、`BLOOM_COMPLETE_FORTE = 0.06`、`BLOOM_ENDED_FORTE = 0.05` 与八个硬约束常量。

三个成员文件的 `PACKAGE_ID` 必须同为 `chunsanshou-v1.2.13-bloom-forte-estimate`。

`1.2.13` 两轮实战 5329 / 4034（均值 4681.5、极差 1295、σ≈915.7，8 花 / 5 完成 / 4 干净完成 / 1 宽限 / 3 未收尾，重击 0.63 次每花），`ring_fallback=True` 命中 7/8 花（`False` 仅 1 次，是 resumed 段 0.057 那次读数），判「有效（上场且按设计语义工作，因果未证）」。本批 `insufficient_samples` 0 次（1.2.10–1.2.12 各批均为 3–6 次），39 条识别遥测里 `budding_forte=-1` 只出现在 `visible_ticks=27/9` 两类低可见度帧，容差按设计只在「可见度高 + 个别刻度缺失」时介入。三次未收尾全是 `outside_tail` 真实未打完：`forte=0.996`（`samples=17`、`consumed_ratio=0.0`、`steady_flat=True`）、`forte=0.799`（`consumed_ratio=0.199`）、resume 段 `history_reset=forte_rise`（0.082→0.675、`consumed_ratio=0.256`、预算 2.454 用尽），均被正确拒绝。**已知风险**：估算读数是偏高上界，`0.996` 与 `0.799` 两条 `consumed_ratio` 偏低是否由估算抬高尚未取证，1.2.14 须先摊开对应帧对比精确前缀。验收仍须在日志里读到 `ring_fallback=True` 才算该容差上场，并核对同段读数没有变成 `0.00` 这类被估算拉低的假值；反例护栏＝刻度整片不可见的形态必须仍判 `output timeout`，`outside_tail` 真实未打完（1.2.11 的 `forte=0.576`、消耗 35.8%）必须继续超时。

## 1.2.12 变更

`1.2.11` 两轮实战（4730 / 4801，均值 4765.5，7 花 / 5 完成，重击 0.86 次每花）里未开环早退零触发（`not_engaged=True` 0 次），判「未验证」，但也确认没有误截断：该批没出现「整段零读数且标记不可见」的形态。

取证（`logs/mcp/86393594…/run.log`）：`target lost` 那段（`output_wall=7.766`、`target_wait=1.297`）并非全程零读数——18:00:45 有 `bloom heavy recovery reason=forte_stalled forte=0.823 marker=True`，`samples=0` 是 `timed_target_enemy` 的 `progress.clear()` 抹掉的，所以把早退扩到 `retarget` 会误截断真实输出，该改法作废。真正漏网的是另一条：`bloom_ended` 要求 `0 <= forte <= BLOOM_COMPLETE_FORTE`，读数整段不可读时永不成立；1.2.11 的早退又要求标记不可见，于是「开花真打完、标记已消失、回路环读不出来」只能烧满 8 秒判 `output timeout`。

`1.2.12` 只动这一处：新增 `Rotation.bloom_marker_end_allowed`，与 `bloom_ended` 并列参与结束确认（本段见过标记 + 当前标记消失 + 同角色在场 + 非解放态 + 读数仍不可读 + `resumed or elapsed >= 5.1`，连续两帧仍由 `ended_frames` 保证），并把 1.2.11 的早退收紧为「本段从未见过标记」；读数可读时一律走原 `BLOOM_COMPLETE_FORTE`，不用标记替代数值证据。

- **出口形态**：`outcome=completed`，新增 `椿散守 bloom marker end completion` 日志与 `capture_bloom_diagnostics('marker_end', 'marker_gone_unreadable')`，`bloom timing` 行新增 `marker_end=True/False`（本版确证字段）。
- **完全不动**：`bloom_tail_status` 的因子与顺序、1.2.6–1.2.10 五条尾段判据、`BLOOM_COMPLETE_FORTE = 0.06`、`BLOOM_ENDED_FORTE = 0.05` 与八个硬约束常量。

三个成员文件的 `PACKAGE_ID` 必须同为 `chunsanshou-v1.2.12-bloom-marker-end`。

`1.2.12` 两轮实测：第 1 轮 4302（脚本有效，4 花 / 3 完成 / 1 干净完成 / 2 宽限 / 1 次 `output timeout`），第 2 轮中途画面 HUD 显示 `当前队伍积分 5791`（这是战斗中积分、不是结算得分；脚本判 invalid 的直接原因是评估口径缺陷——开花输出会按住重击直到收尾，期间没有离散输入，被 `NO_INPUT_TIMEOUT=8` 误判成 `CombatStallError: Camellya sent no input for 8.1s`，随后把战斗帧存成了结果页，`result_ocr.json` 只有 `Lv.100封庭械圈`、`00:00:20`、`8117/16010`，`events.jsonl` 里 `rotation_end` 与 `stall` 同时刻、`heartbeat` 的 `last_input_age=6.593` 可证）；该批 `marker_end=True` 0 次，验收首判「未验证」；但随后定位到评估口径缺陷（开花输出会按住重击直到收尾，期间没有离散输入，被 `NO_INPUT_TIMEOUT=8` 误判成 `CombatStallError`），修正后重跑两轮全部有效：5329 / 5206（均值 5267.5、极差 123、σ≈87），`marker_end=True` 在 `bloom timing` 行命中（`wall=7.172`、`ended_frames=2`、`tail_extra=0.000`），1.2.12 因此改判「有效（样本 1 次）」。第 1 轮那次 `output timeout` 原文为 `output_wall=8.031`、`'reason': 'insufficient_samples'`、`samples=0`、`consumed_ratio=-1`、`history=[]`，同段 `not_engaged=False`，证明标记仍在屏而回路环整段读不到 —— 1.2.9 不可读门、1.2.11 未开环、1.2.12 标记结束三条均按设计拒绝，属识别层断流，是当前第一失败源。验收须在日志里读到 `bloom marker end completion` 或 `marker_end=True` 才算该出口上场，并核对同段 `ended_frames>=2`；反例护栏＝读数可读时的 `outside_tail`（如 1.2.11 那次 `forte=0.576`、消耗 35.8%）必须仍判 `output timeout`，出现 `0 <= forte <= 0.06` 却走标记路径也属异常。

## 1.2.11 变更

`1.2.10` 两轮实战（5289 / 5301，均值 5295，8 花 / 6 完成，重击 0 次）里已消耗稳态出口零触发（`consumed_tail=True` 0 次、`bloom consumed tail completion` 0 次），判「未验证」；本批 `insufficient_decreases` / `outside_tail` / `stale_sample` 各 0 次，目标形态未复现。两次未收尾同形：`output_wall=8.047` 与 `8.999`，都是 `cast confirmed` 之后整段 `samples=0`、`consumed_ratio=-1`、`history=[]`、`heavy_retries=0`，同帧识别遥测 `team_skill.state='no_bloom_ring'`、`legacy_red_percent=0.000000`、`camellya_budding` 不可见。

该形态已跨版本出现 1.2.8 两次 / 1.2.9 两次 / 1.2.10 两次，是当前批次第一失败源；回路环没上屏时 `bloom_tail_status` 只能判 `insufficient_samples`，1.2.8 地板 / 1.2.9 不可读 / 1.2.10 已消耗三条出口都要求至少一个可读样本，判据层无从下手。`1.2.11` 因此不给收尾判据再加出口，而是加「早退」：

- **新增常量**：`BLOOM_NOT_ENGAGED_AFTER = 2.0`。
- **`Rotation.bloom_output_never_engaged`**：`elapsed >= 2.0` + 同角色在场 + `camellya_budding` 不可见 + 非解放态 + 本帧 `forte` 不可读 + 本段一个可读样本都没有，才早退。任何出现过可读读数、或开花标记可见的形态一律交回原判据，避免截断真实输出。
- **出口形态**：`outcome=bloom not engaged`，不抛异常、不新开预算、不动任何收尾门限，新增 `椿散守 bloom not engaged` 日志与 `capture_bloom_diagnostics('not_engaged', 'bloom_ring_absent')`，`bloom timing` 行新增 `not_engaged=True/False`（本版确证字段）。
- **完全不动**：`bloom_tail_status` 的因子与顺序、1.2.6 / 1.2.7 / 1.2.8 / 1.2.9 / 1.2.10 五条判据、两个结束阈值、八个硬约束常量。

三个成员文件的 `PACKAGE_ID` 必须同为 `chunsanshou-v1.2.11-bloom-not-engaged`。

`1.2.11` 两轮实战 4730 / 4801（均值 4765.5，极差 71，σ≈50.2，7 花 / 5 完成 / 4 干净完成 / 1 宽限，重击 0.86 次每花），`not_engaged=True` 0 次、`bloom not engaged` 0 次，验收判「未验证」而非无效：本批没出现「整段零读数且标记不可见」的形态，早退一次未上场，也因此没有发生误截断。两次未收尾分别是 `output timeout`（`'reason': 'outside_tail'`、`forte=0.576`、`samples=25`、`consumed_ratio=0.358`，真实未打完）与 `target lost during output`（`'reason': 'insufficient_samples'`、`samples=0`、`history_reset=retarget`、`target_wait=1.297`，目标等待清窗后零读数）；早退未触发说明在场/标记/解放态三道里有一道不成立，1.2.12 须先取证再改判据。验收仍须在日志里读到 `bloom not engaged` 或 `not_engaged=True` 才算该早退上场，并核对早退时刻 `wall` 明显小于 8 秒；同时必须确认没有「本段出现过可读样本却被早退」的误截断。

## 1.2.10 变更

`1.2.9` 两轮实战（3702 / 5320，均值 4511，8 花 / 3 完成，重击 0.5 次每花）里断流出口零触发（`unreadable_grace=True` 0 次、`bloom unreadable grace completion` 0 次），判「未验证」；本批 `stale_sample` 形态 0 次，目标形态未复现。四次超时里出现一条三条既有出口全部漏掉的新形态：`output_wall=8.000`、`reason=insufficient_decreases`、`forte=0.279`、`samples=13`、`sample_age=0.0`、`decreases=0`、`consumed_ratio=0.697`、`steady_flat=True`、`steady_hold=1.437`、`heavy_retries=0`。它高于 1.2.8 地板窗（`>0.10`），又因 `<=0.25` 让 `inside_tail=True`、`reason` 永远不是 1.2.7 要求的 `outside_tail`，1.2.9 只接不可读帧。

`1.2.10` 只加这一条出口，`BLOOM_COMPLETE_FORTE = 0.06`、`BLOOM_ENDED_FORTE = 0.05`、1.2.6 / 1.2.7 / 1.2.8 / 1.2.9 四条判据与八个硬约束常量一律不动：

- **新增常量**：`BLOOM_TAIL_CONSUMED_SPAN = 1.0`；消耗比复用 `BLOOM_TAIL_CONSUMED_RATIO = 0.65`，容差复用 `BLOOM_TAIL_STEADY_TOLERANCE = 0.005`。
- **`Rotation.bloom_consumed_tail_allowed`**：`output_wall >= output_budget` + 同角色在场、标记可见、非解放态 + 本帧读数可读 + 尾段 `reason` 只收 `insufficient_decreases` / `stale_decrease` + `sample_age <= 0.5` + `consumed_ratio >= 0.65` + `steady_flat=True` 且 `steady_hold >= 1.0`、`steady_range <= 0.005`。**不设补按门**：唯一实测形态 `heavy_retries=0`，加了就接不住。
- **反例护栏**：`outside_tail` 的 0.823（消耗 10.7%）与 0.700（消耗 26.3%）是真实未打完，必须仍判 `output timeout`；消耗比公式本身已把 `forte > 0.35` 的读数挡在外面。
- **出口形态**：`outcome=completed`，新增 `椿散守 bloom consumed tail completion` 日志与 `capture_bloom_diagnostics('consumed_tail', 'stalled_after_consumption')`，`bloom timing` 行新增 `consumed_tail=True/False`（本版确证字段），插入点仍在 `check_recheck_deadline()` 之前。

三个成员文件的 `PACKAGE_ID` 必须同为 `chunsanshou-v1.2.10-bloom-consumed-tail`。

`1.2.10` 两轮实战 5289 / 5301（均值 5295，极差 12，8 花 / 6 完成，重击 0 次），`consumed_tail=True` 0 次、`bloom consumed tail completion` 0 次，验收判「未验证」而非无效：目标形态未复现，判据一次未上场。实战验收仍按 `.dsh-tmp/任务3-8秒收尾验收基线.md`，并须在日志里读到 `bloom consumed tail completion` 或 `consumed_tail=True` 才算该出口上场；0.823 / 0.700 型 `outside_tail` 若被放行即为误放行。

## 1.2.9 变更

`1.2.8` 三轮实战（5121 / 5230 / 4331，均值 4894，11 花 / 8 完成，重击 0.45 次每花）里地板出口零触发（`floor=True` 0 次、`bloom floor completion` 0 次），判「未验证」。三次未收尾退出时 `forte` 分别是 `-1` / `-1` / `0.156`，没有一次落在 `(BLOOM_COMPLETE_FORTE, BLOOM_TAIL_FLOOR_MAX]` 地板窗内，原「放开重击门」的归因作废：真正的第一失败源是「输出预算耗尽那一刻回路读数不可读」。

取证（2026-11-01，MCP artifact `8679e3375ccd4d2ea1893e5b0a29388e`）：8.016 / 8.047 两段 `cast confirmed` 之后整段 8 秒 `budding_forte=-1`、`samples=0`、`consumed_ratio=-1`、`heavy_retries=0`、`history=[]`，同段 `check_target Retry/Release` 刷屏，属识别 / 丢锁层；9.000 段可读证据完整（`history` 到 8.046 `forte=0.156`、`consumed_ratio=0.836`、9.000 时 `sample_age=0.954`、`samples=5`），首帧不可读起于 8.562，随后 0.4 秒复核到期被判 `tail progress lost`，属判据层。`1.2.9` 只接 9.000 形态，另两段按反例护栏继续判 `output timeout`。

`1.2.9` 只加这一条出口，`BLOOM_COMPLETE_FORTE = 0.06`、`BLOOM_ENDED_FORTE = 0.05`、1.2.6 贴零判据、1.2.7 中高位判据与 1.2.8 地板判据一律不动，八个硬约束常量（`BLOOM_OUTPUT_TIMEOUT = 8`、`BLOOM_WALL_TIMEOUT = 16`、`PHASE_TIMEOUT = 24`、`BLOOM_TAIL_GRACE = 2`、`BLOOM_TAIL_RECHECK = 0.4`、`BLOOM_FEEDBACK_TIMEOUT = 1.2`、`BLOOM_TARGET_WAIT_MAX = 2.0`、`BLOOM_TARGET_WAIT_TOTAL = 3.0`）继续不动，`bloom_tail_status` 的因子与先后顺序也不动：

- **新增常量**：`BLOOM_TAIL_UNREADABLE_MAX_AGE = 1.0`（连续断流上限，同时是最后可读样本年龄上限）、`BLOOM_TAIL_UNREADABLE_MIN_SAMPLES = 3`、`BLOOM_TAIL_UNREADABLE_FORTE_MAX = 0.25`；消耗比复用 `BLOOM_TAIL_CONSUMED_RATIO = 0.65`。
- **`Rotation.bloom_unreadable_grace_allowed`**：六组条件同时成立才放行——① `output_wall >= output_budget` 且 `output_wall - output_budget <= BLOOM_TAIL_GRACE`（不新开预算，宽限到期仍由 `check_hard_budget` 抛 `tail grace timeout`）；② 本帧 `forte` 不可读（`not 0 <= forte <= 1`）；③ 同角色在场、`camellya_budding` 可见、非解放态；④ 尾段 `reason` 只接受 `insufficient_samples` / `stale_sample`（`progressing` 继续走宽限，其余一律不接）；⑤ `samples >= 3` 且 `sample_age <= 1.0`（即连续不可读不超过 1.0 秒）；⑥ 最后可读读数 `<= 0.25` 且 `consumed_ratio >= 0.65`（按本段观测峰值，峰值不可读时不开放）。
- **出口形态**：成立时 `outcome=completed`、不进恢复分支；新增 `椿散守 bloom unreadable grace completion` 日志与 `capture_bloom_diagnostics('unreadable_grace', 'boundary_read_blackout')`，`bloom timing` 行新增 `unreadable_grace=True/False`（与 `steady=` / `floor=` 并列，作为本版确证字段）。插入点刻意排在 `check_recheck_deadline()` 之前：9.000 段正是复核 0.4 秒到期被判死，判据再准也来不及上场。
- **完全不动**：`record_bloom_progress` 的入账链（`forte < 0` 仍只按 1.5 秒裁剪、不清窗）、`maintain_bloom_heavy` 的重试上限与平台期判据、`bloom_entry_recheck_allowed` 的两个分支。

三个成员文件的 `PACKAGE_ID` 必须同为 `chunsanshou-v1.2.9-bloom-unreadable-grace`。

`1.2.9` 两轮实战 3702 / 5320（均值 4511，极差 1618，σ≈1144，8 花 / 3 完成，重击 0.5 次每花），`unreadable_grace=True` 0 次、`bloom unreadable grace completion` 0 次，验收判「未验证」而非无效：判据一次都没上场。四次超时归因：2 次 `insufficient_samples`（`samples=0`、`consumed_ratio=-1`，零证据形态属识别层，被正确拒绝）、1 次 `outside_tail`（`forte=0.823`，真实未打完）、1 次 `insufficient_decreases`（`forte=0.279`、`samples=13`、`sample_age=0.0`、`consumed_ratio=0.697`、`steady_flat=True`、`steady_hold=1.437`，落在 1.2.8 地板窗 ≤0.10 与 1.2.7 中高位窗 0.20–0.75 且需 `outside_tail` 之间的缝隙，留待 1.2.10）。实战验收仍按 `.dsh-tmp/任务3-8秒收尾验收基线.md`，并须在日志里读到 `bloom unreadable grace completion` 或 `unreadable_grace=True` 才算该出口上场，读不到即仍判「未验证」，不许拿均值变好当通过；8.016 / 8.047 型零证据形态必须仍以 `output timeout` 退出，否则是误放行。`tail_extra` 过冲、`bloom_resume_budget`、P5 丢锁与 P7 回切继续单列。

## 1.2.8 变更

`1.2.7` 两轮实测（5631 / 5068，均值 5299.5，8 花 / 5 完成，重击 1.13 次每花）里 1.2.7 的中高位稳态出口一次都没上场（`bloom steady completion` = 0、`bloom timing` 的 `steady=` 全为 `False`），实战第一失败源换成了「三格地板」，而且两次完全同形：21:43:54（`forte=0.082`、`sample_age=0.078`、`decreases=3`、`drop_age=1.188`、`span=1.438`、`steady_hold=1.11`、`steady_range=0.0`、`steady_flat=True`、`drop_total=0.074`、`consumed_ratio=0.918`，以 `tail grace timeout` 收场，`wall=10.093`、`tail_extra=2.093`）与 21:44:31（`forte=0.082`、`samples=26`、`decreases=0`、`drop_age=-1`、`span=1.187`、`steady_hold=1.187`、`steady_range=0.0`、`steady_flat=True`、`consumed_ratio=0.918`，以 `tail progress lost` 收场）。两次都是 `heavy_retries=2` 已用尽、开花标记仍在屏。

0.082（约 8.15%，三格）正好落在三条门限的缝隙里：`0.082 > BLOOM_COMPLETE_FORTE = 0.06` 使 `bloom_ended` 永不成立（`ended_frames=0`）；`0.082 > BLOOM_ENDED_FORTE = 0.05` 使 1.2.6 的 `zero_steady` 不成立（`zero_hold = -1.0`）；`0.082 <= 0.25` 使 `inside_tail = True`、`reason` 只会落在 `stale_decrease` / `insufficient_decreases`，永远不会是 1.2.7 要求的 `outside_tail`。

`1.2.8` 只加这一条出口，`BLOOM_COMPLETE_FORTE = 0.06`、`BLOOM_ENDED_FORTE = 0.05`、1.2.6 贴零判据与 1.2.7 中高位判据一律不动，八个硬约束常量（`BLOOM_OUTPUT_TIMEOUT = 8`、`BLOOM_WALL_TIMEOUT = 16`、`PHASE_TIMEOUT = 24`、`BLOOM_TAIL_GRACE = 2`、`BLOOM_TAIL_RECHECK = 0.4`、`BLOOM_FEEDBACK_TIMEOUT = 1.2`、`BLOOM_TARGET_WAIT_MAX = 2.0`、`BLOOM_TARGET_WAIT_TOTAL = 3.0`）继续不动：

- **地板口径**：新增 `BLOOM_TAIL_FLOOR_MAX = 0.10`（只覆盖三格 0.082 并留识别抖动余量，四格 ≈0.109 与 0.20 以上的中高位仍按原判据）、`BLOOM_TAIL_FLOOR_SPAN = 1.0`、`BLOOM_TAIL_FLOOR_RETRIES = 2`；尾部容差复用 `BLOOM_TAIL_STEADY_TOLERANCE = 0.005`，消耗比复用 `BLOOM_TAIL_CONSUMED_RATIO = 0.65`。
- **`Rotation.bloom_floor_completion_allowed`**：五组条件同时成立才放行——① `output_wall >= output_budget`，与 1.2.7 一样不提前截断真实输出；② 同角色在场、`camellya_budding` 可见、非解放态；③ `heavy_retries >= 2`，队内两次补按已用尽；④ `BLOOM_COMPLETE_FORTE < forte <= BLOOM_TAIL_FLOOR_MAX` 且 `sample_age <= 0.5`；⑤ 尾段 `reason` 只接受 `stale_decrease` / `insufficient_decreases`（已判 `progressing` 的形态继续走宽限，`stale_sample` / `insufficient_samples` / `short_observation` 不放行）、`consumed_ratio >= 0.65`（与 1.2.5 一致，按本段观测峰值计算，峰值不可读时不开放）、尾部 `steady_flat=True` 且 `steady_hold >= 1.0`、`steady_range <= 0.005`。
- **出口形态**：成立时 `outcome=completed`、不进恢复分支；新增 `椿散守 bloom floor completion` 日志与 `capture_bloom_diagnostics('floor_completion', 'three_grid_floor')`，`bloom timing` 行新增 `floor=True/False`（与既有 `steady=` 并列，作为版本确证字段）。
- **完全不动**：`bloom_tail_status` 的因子与先后顺序、`maintain_bloom_heavy` 的重试上限与平台期判据、两个结束确认阈值。

三个成员文件的 `PACKAGE_ID` 必须同为 `chunsanshou-v1.2.8-bloom-floor-exit`。

`1.2.8` 三轮实战 5121 / 5230 / 4331（均值 4894，11 花 / 8 完成，重击 0.45 次每花），地板出口零触发（`floor=True` 0 次、`bloom floor completion` 0 次），验收判「未验证」而非无效：判据一次都没上场。实战验收仍按 `.dsh-tmp/任务3-8秒收尾验收基线.md`，并须在日志里读到 `bloom floor completion` 或 `floor=True` 才算该出口上场；`tail_extra` 0.093 过冲、`bloom_resume_budget`、P5 丢锁与 P7 回切继续单列。

## 1.2.7 变更

`1.2.6` 三轮实测（4844 / 5293 / 5504，11 花 / 8 完成 / 重击 0.73 次每花）里贴零判据一次都没触发，反而把 P3（中高位稳态零消耗）顶成本版第一失败源：19:11:31 的读数卡在 `forte=0.724` 整整 8 秒（`decreases=0`、`drop_age=-1`、`drop_total=0.0`、`span=1.5`、`samples=38`），同段两次补按都是 `bloom heavy recovery reason=forte_stalled resumed=True forte=0.724 marker=True target=True`，即 `heavy_retries=2` 已用尽而开花标记仍在屏；8 秒预算恰好耗完（`wall=8.063`），最后以 `outside_tail` 判 `output timeout`。问题不是「时间不够」，而是收尾分类错：已经打完的输出被当成超时送进恢复分支。

`1.2.7` 只改这一处，`BLOOM_OUTPUT_TIMEOUT = 8`、`BLOOM_WALL_TIMEOUT = 16`、`PHASE_TIMEOUT = 24`、`BLOOM_TAIL_GRACE = 2`、`BLOOM_TAIL_RECHECK = 0.4`、`BLOOM_FEEDBACK_TIMEOUT = 1.2`、`BLOOM_TARGET_WAIT_MAX = 2.0`、`BLOOM_TARGET_WAIT_TOTAL = 3.0` 与 `1.2.6` 的贴零判据一律不动：

- **中高位稳态口径**：新增 `BLOOM_TAIL_STEADY_SPAN = 1.0`、`BLOOM_TAIL_STEADY_TOLERANCE = 0.005`、`BLOOM_TAIL_STEADY_MIN = 0.20`、`BLOOM_TAIL_STEADY_MAX = 0.75`、`BLOOM_TAIL_STEADY_RETRIES = 2`；`Rotation.bloom_steady_status` 从窗口尾部向前扫「读数在 0.5% 容差内完全一致」的连续样本，跨度 ≥1.0 秒且 ≥3 帧时 `steady_flat=True`，并把 `steady_hold` / `steady_range` / `steady_flat` 一并进 `bloom tail decision` 日志。
- **与「在打但读数不动」的区分证据**：`Rotation.bloom_steady_completion_allowed` 要求五条同时成立——① 只在输出预算耗尽后判定（`output_wall >= output_budget`），绝不提前截断真实输出；② 同角色在场、`camellya_budding` 标记仍可见、不在解放态；③ `heavy_retries >= 2`，队内两次补按已用尽；④ 读数落在 0.20–0.75（上限刻意压在 0.75，使验收基线 D1/D3 的 0.799 满格平台仍按超时处理）且 `sample_age <= 0.5`（采集链活着，不是采样停摆）；⑤ 整段 `decreases = 0` 且 `drop_total <= 0.005`（实测消耗 12–16%/s，1 秒内必跨一格，读数完全不动只可能来自识别冻结），同时尾段 reason 仍是 `outside_tail`，贴零与已判 `progressing` 的形态不走这条路。
- **出口形态**：成立时按 `outcome=completed` 正常收尾，不再抛 `RotationTimeout` 进恢复；新增 `椿散守 bloom steady completion` 日志与 `bloom timing` 行的 `steady=True/False` 字段作为版本确证。
- **完全不动**：`bloom_tail_status` 的因子与先后顺序（含 `1.2.6` 的 `zero_steady`）、`insufficient_decreases` / `short_observation` 判据、`maintain_bloom_heavy` 的重试上限与平台期判据一律原样。

三个成员文件的 `PACKAGE_ID` 必须同为 `chunsanshou-v1.2.7-bloom-steady-exit`。

`1.2.7` 两轮实测 5631 / 5068（均值 5299.5，8 花 / 5 完成，重击 1.13 次每花），验收 **PARTIAL / 未验证**：中高位稳态出口一次都没上场（`bloom steady completion` = 0、`bloom timing` 的 `steady=` 全为 `False`），三次未自然收尾里两次是 `forte=0.082` 的三格地板、一次是 P5 丢锁。19:05:07 的 resume 形态（`heavy_retries=1`、`output_budget=4.828`）不会被这条出口接走，仍判失败并继续留在「`bloom_resume_budget` 单独立项」里——这正是把 P3 本体与 resume 变体分开的用意。实战验收仍按 `.dsh-tmp/任务3-8秒收尾验收基线.md`。

## 1.2.6 变更

`1.2.5` 两轮实测（5266 / 5907，8R / 7 花 / 3 完成，重击 0.86 次每花）留下一个两侧都不给出口的形态：读数已经贴零并稳定超过 0.75 秒，尾段判据却因「最近一次下降过旧」判 `stale_decrease`，而 `bloom_ended` 又要求 `camellya_budding` 标记消失，标记仍在屏时同样不成立。18:25:38（`forte=0.000`、`consumed_ratio=1.0`、`samples=55`、`drop_total=0.106`、`drop_age=1.046`）走进 `completion_recheck_start` 后 0.4 秒仍 `insufficient_decreases`，以 `entry recheck timeout` 收场；18:28:46（`forte=0.032`、`consumed_ratio=0.968`、`samples=34`、`drop_total=0.049`、`drop_age=0.953`）直接 `output timeout`。

`1.2.6` 只改这一处，`BLOOM_OUTPUT_TIMEOUT = 8`、`BLOOM_WALL_TIMEOUT = 16`、`PHASE_TIMEOUT = 24`、`BLOOM_TAIL_GRACE = 2`、`BLOOM_TAIL_RECHECK = 0.4`、`BLOOM_FEEDBACK_TIMEOUT = 1.2`、`BLOOM_TARGET_WAIT_MAX = 2.0`、`BLOOM_TARGET_WAIT_TOTAL = 3.0` 与满格 / 非满格补按判据一律不动：

- **贴零稳态出口**：新增 `BLOOM_TAIL_ZERO_SPAN = 0.75`。窗口尾部连续贴零（`forte <= BLOOM_ENDED_FORTE = 0.05`）的读数跨度 ≥0.75 秒、样本数 ≥3 且最新样本仍新鲜时，`bloom_tail_status` 直接判 `progressing`，不再由 `drop_age` 阻断，随后按既有 `BLOOM_TAIL_GRACE` 宽限等 `bloom_ended` 成立。
- **只贴零才放行**：中高位稳态（P3，0.20–0.70 纹丝不动）没有贴零样本，仍判 `stale_decrease`；`insufficient_samples` / `outside_tail` / `stale_sample` 的先后顺序不变，`insufficient_decreases` 与 `short_observation` 判据原样保留。
- **诊断可读**：`bloom_tail_status` 返回值新增 `zero_hold`（尾部贴零跨度），与既有 `drop_age` / `span` / `drop_total` 一起进 `bloom tail decision` 日志。

三个成员文件的 `PACKAGE_ID` 必须同为 `chunsanshou-v1.2.6-bloom-zero-steady`。

`1.2.6` 已完成三轮实战（4844 / 5293 / 5504，11 花 / 8 完成 / 7 次零宽限，重击 0.73 次每花），验收 **PARTIAL**：贴零判据一次都没触发（三条 `event=exit` 的 `zero_hold` 全为 `-1.0`），三次未自然收尾里 2 次是 P3、1 次是 P5，因此本轮不能作为该改动的验收证据。离线回归只说明贴零形态多出一个出口；实战验收仍按 `.dsh-tmp/任务3-8秒收尾验收基线.md` 的判据，并按轮分别统计 R / 花 / 完成。

## 1.2.5 变更

`1.2.4` 实测（5000 / 4621，6 花 / 3 完成 / 3 零宽限，重击 1.67 次每花）是四版里最差的一版：滑动窗口判的是「降幅速率」而不是「等级」，真实消耗 12–16%/s 时窗口不触发（这是对的），但帧稀疏或读数上跳时峰值挂在高位，反而更晚触发；`count=1` 的 `output` 落在 0.988 / 1.597 / 1.144 / 0.307 / 1.235 / 1.656 秒，六次里只有一次进 0.4 档。同一窗口还留下两次「R 了没开花」。结论：`1.2.1` → `1.2.4` 这条补按时机线没有净收益，就此停手。

`1.2.5` 只改两处，`BLOOM_OUTPUT_TIMEOUT = 8`、`BLOOM_WALL_TIMEOUT = 16`、`PHASE_TIMEOUT = 24`、`BLOOM_TAIL_GRACE = 2`、`BLOOM_TAIL_RECHECK = 0.4`、`BLOOM_FEEDBACK_TIMEOUT = 1.2`、`BLOOM_TARGET_WAIT_MAX = 2.0`、`BLOOM_TARGET_WAIT_TOTAL = 3.0` 一律不动：

- **回滚满格滑动窗口**：删除 `BLOOM_HEAVY_STALL_WINDOW` / `BLOOM_HEAVY_STALL_RANGE` 与 `early_retry_used` 快速通道，恢复 `1.2.3` 的满格平台期 0.4 秒判据 + 非满格 0.9 秒判据。贴 0.9 边界的抖动（实测 `0.897` 档）重新退回 0.9 秒规则，首次补按会晚到约 2 秒——按汇总文档，这条线不再继续调。
- **尾段基线改用本段观测峰值**：新增 `Rotation.bloom_tail_baseline`，`consumed_ratio = 1 - forte / peak`，峰值随可读样本更新，不可读帧不参与。`1.2.4` 在 17:57:59 的 `outside_tail`（`forte = 0.304`、`consumed_ratio = 0.245`，反推基线约 0.403）来自 cast 结束后头一两帧的低读数；改用峰值后同一帧 `consumed_ratio ≈ 0.68`，配合 `drop_total` / `drop_age` 会判 `progressing` 并靠宽限收尾。`timed_target_enemy` 处仍整体作废基线，防用旧基线骗宽限。

三个成员文件的 `PACKAGE_ID` 必须同为 `chunsanshou-v1.2.5-bloom-peak-baseline`。

`1.2.5` 尚无实战数据。实战仍按 `.dsh-tmp/任务3-8秒收尾验收基线.md` 的判据验收，建议每版 2–3 轮并保留完整 `ok-script.log` 段；若要更干净的归因，可先只留回滚测一轮，再叠加基线修复。

## 1.2.4 变更

`1.2.3` 实测两轮（5423 / 5624，8R / 8 花 / 6 收尾）说明上一版只修好了一半：向上抖动确实不再重置计时，但首次补按仍会晚——`0.947` 档 2.231 秒、`0.897` 档 2.050 秒、`0.922` 档 1.082 / 1.059 秒，而同期也有 0.414 / 0.744 / 0.462 秒的准时样本。真正的抖动源有两个：`forte >= 0.9` 这个硬阈值本身（读数跌破 0.9 就清空平台期），以及 `changed` 判据里的「相邻帧差值 ≥0.015」（一帧跌 1.5% 就重置停滞起点）。

`1.2.4` 只改这一处，`BLOOM_OUTPUT_TIMEOUT = 8`、`BLOOM_WALL_TIMEOUT = 16`、`PHASE_TIMEOUT = 24`、`BLOOM_TAIL_GRACE = 2`、`BLOOM_TAIL_RECHECK = 0.4`、`BLOOM_FEEDBACK_TIMEOUT = 1.2` 一律不动：

- **滑动窗口判停滞**：新增 `BLOOM_HEAVY_STALL_WINDOW = 0.4` 与 `BLOOM_HEAVY_STALL_RANGE = 0.03`；最近 0.4 秒内（跨度 ≥0.3 秒、≥3 帧）峰值与当前值之差 <3% 即判停滞并补按，不再依赖 `forte` 绝对值，也不受相邻帧差值重置影响。
- **每段只提前补按一次**：`early_retry_used` 标记保证快速通道每段只触发一次，第二次仍由原 0.9 秒判据决定，避免连发。
- **尾段判据与其余上限原样**：`1.2.1` 的不可读不清窗 / 累计降幅 / 相对起点 0.65 与重试上限 2 次全部保留；`forte < 0.9` 的 0.9 秒判据与满格 0.4 秒判据同时保留。

三个成员文件的 `PACKAGE_ID` 必须同为 `chunsanshou-v1.2.4-bloom-heavy-window`。

`1.2.4` 尚无实战数据。离线回归只说明抖动不再推迟补按；实战仍按 `.dsh-tmp/任务3-8秒收尾验收基线.md` 的判据验收。

## 1.2.3 变更

`1.2.2` 两批实测（3675 / 5692 与约 5000 / 6038）已经在日志里坐实一条规律：`forte = 0.996`（贴顶、没有向上抖动空间）的首次补按落在 0.436 / 0.582 / 0.404 秒，而 `forte = 0.922`（有向上抖动空间）的落在 1.112 / 1.231 / 2.172 秒。原因是 `1.2.2` 把「相对峰值再抬升 ≥0.015」也算作状态变化，读数向上跳动时把停滞起点重置了，0.4 秒判据因此被反复推迟；第二次补按同样被推迟，伤害是双份的。

`1.2.3` 只改这一处，`BLOOM_OUTPUT_TIMEOUT = 8`、`BLOOM_WALL_TIMEOUT = 16`、`PHASE_TIMEOUT = 24`、`BLOOM_TAIL_GRACE = 2`、`BLOOM_TAIL_RECHECK = 0.4`、`BLOOM_FEEDBACK_TIMEOUT = 1.2` 一律不动：

- **向上抖动只抬高峰值**：平台期只有「已真实消耗掉峰值的 3% 以上」才算状态变化并重新起算；向上跳动只更新 `peak`，不重置 `since`。
- **尾段判据与补按上限不变**：`1.2.1` 的不可读不清窗 / 累计降幅 / 相对起点 0.65 原样保留；重试上限仍为 2 次。
- **非满格判据原样**：`forte < 0.9` 时仍走原来的 0.9 秒停滞判据。

（1.2.3 的）三个成员文件 `PACKAGE_ID` 同为 `chunsanshou-v1.2.3-bloom-plateau-jitter`。

`1.2.3` 尚无实战数据。离线回归只说明向上抖动不再推迟补按；实战仍按 `.dsh-tmp/任务3-8秒收尾验收基线.md` 的判据验收。

## 1.2.2 变更

`1.2.1` 单场实测（5059 分，16:49–16:59 日志窗口）证明尾段判据已经生效：16:57:30 那次以 40→42 条证据判 `progressing`、靠 0.641 秒宽限收尾，而旧判据下同一形态必然只剩 1 个样本并直接 `output timeout`；日志里能读到 `drop_total` / `consumed_ratio`，确认跑的是新包。但起步消耗只修好一半——首次补按时间点落在 0.447 / 0.604 / 1.271 / 2.032 秒，其中 16:56 那次 1.271 秒才补第一次、第二次补按后回路仍停在 0.922，8 秒时只消耗 40.2%，以 `outside_tail` 超时。

根因：`maintain_bloom_heavy` 的停滞计时用「相邻帧差值 ≥0.015」判状态变化。满格附近的识别抖动（上下 1-2 格）会让差值反复越过 0.015，从而不断把停滞起点重置，0.4 秒判据被无限推迟。

`1.2.2` 只改这一处，`BLOOM_OUTPUT_TIMEOUT = 8`、`BLOOM_WALL_TIMEOUT = 16`、`PHASE_TIMEOUT = 24`、`BLOOM_TAIL_GRACE = 2`、`BLOOM_TAIL_RECHECK = 0.4` 一律不动：

- **满格平台期独立计时**：`forte >= 0.9` 时用独立的 `plateau` 记峰值与起点，只有「相对峰值再抬升 ≥0.015」或「已真实消耗掉峰值的 3% 以上」才算状态变化；抖动不再重置计时。
- **补按后清空平台期**：第二次补按仍要再攒 0.4 秒，不会连发。
- **非满格判据原样**：`forte < 0.9` 时仍走原来的 0.9 秒停滞判据。

（1.2.2 的）三个成员文件 `PACKAGE_ID` 同为 `chunsanshou-v1.2.2-bloom-plateau-stall`。

`1.2.2` 尚无实战数据。离线回归只说明抖动不再推迟补按，实战仍按 `.dsh-tmp/任务3-8秒收尾验收基线.md` 的判据验收。

## 1.2.1 变更

`1.2.0` 两轮实测（`downloads/chunsanshou-1.2.0/2轮实测结论-20261031.md`）显示 A1/L2 均已生效，但三次输出超时都属「打完了、量不出来」，且每次开花后有 0.9–3.1 秒回路不下降。`1.2.1` 只修这两条，`BLOOM_OUTPUT_TIMEOUT = 8` 与 `BLOOM_WALL_TIMEOUT = 16` 一律不动：

- **本帧消耗先入账**：主循环改为先读 `current_forte` 并 `record_bloom_progress`，再做软预算判定。原顺序是 `check_budget` 先于入账，8 秒边界那一帧的下降从不参与判定（`诊断分析.txt` 第 51 行；实测 09:07:48 只剩 1 个样本）。
- **满格停滞 0.4 秒即补按**：新增 `near_full_stalled`——`forte >= 0.9` 且停滞 ≥0.4 秒就补按重击，不再等满 0.9 秒。实测三次开花后回路停在 0.947 / 0.996 达 0.9–3.1 秒，白吃 8 秒预算的 1/4–2/5。
- **不可读帧不清窗**：`record_bloom_progress` 读到 -1 时只淘汰过期样本并返回 `forte_unknown`，不再 `samples.clear()`。
- **小幅抬升不清窗**：仅确认回满（≥ `BLOOM_TAIL_RESET_FORTE = 0.25`）才 `forte_rise` 清窗，避免一次颜色抖动抹掉整段下降证据。
- **累计降幅判据**：新增 `bloom_tail_consumption`，由「逐帧各降 ≥1.5% 计数 ≥2」改为「窗口累计降幅 ≥ `BLOOM_TAIL_DROP_TOTAL = 0.03`」+「最新单帧仍在下降」。
- **相对尾段门限**：保留绝对 `0.25`，新增「以本段首个可读回路为基准已消耗 ≥ `BLOOM_TAIL_CONSUMED_RATIO = 0.65`」；基线不可读时不开放相对判据；`timed_target_enemy` 清窗处一并作废基线，防用旧基线骗宽限。

（1.2.1 的）三个成员文件 `PACKAGE_ID` 同为 `chunsanshou-v1.2.1-bloom-tail-consumption`。

`1.2.1` 尚无实战数据；离线回归与 `bloom_output` 闭环仿真只说明判据逻辑与反例护栏正确，不能证明实战消耗速率。验收判据见 `.dsh-tmp/任务3-8秒收尾验收基线.md`。

## 1.2.0 变更

`1.1.9` 手测诊断（`downloads/chunsanshou-1.1.9/manual-3140-4580/诊断分析.txt`）定位出一条独立故障链：输出段丢锁等待按相位剩余时间申请预算，空等 11.687 秒，等待结束即撞 `camellya_burst: phase timeout`；该异常又被 `CombatCheck.in_combat` 的通用 `except Exception` 吞成 `None`，最终改写成脱战重入。

`1.2.0` 只处理这条链路，不放宽开花识别、输出上限、尾段判据和重试上限：

- **丢锁等待独立预算**：新增 `BLOOM_TARGET_WAIT_MAX = 2.0`（单次）与 `BLOOM_TARGET_WAIT_TOTAL = 3.0`（开花段累计）。等待预算不再由相位剩余时间给出，单次与累计各自封顶；预算耗尽或重锁失败即交回队内局部恢复，不再空等，也不再靠相位超时脱困。
- **开花段局部战斗判定**：开花输出段临时以 `do_check_in_combat` 取代 `CombatCheck.in_combat`，段内 `RotationTimeout` 直接冒泡回 `bloom_output` 的局部恢复，不再被改写成脱战；丢锁失败只在下一帧一次性交回恢复，此后真实脱战仍按原生语义处理。覆盖与 `target_enemy` 一样在 `finally` 中成对还原。
- **全局日志修复**：`src/combat/CombatCheck.py` 的 `logger.error(f'do_check_in_combat:', e)` 改为 `logger.error('do_check_in_combat: %s', e, exc_info=True)`。原写法消息里没有占位符却传了 `e`，格式化抛 `TypeError`，整条记录被 logging 丢弃，异常文本从未进日志。此行不改变控制流。

（1.2.0 的）三个成员文件 `PACKAGE_ID` 同为 `chunsanshou-v1.2.0-target-wait-budget`；`_team_members_match()` 要求一致，不一致会让整队静默退回内置轮转。

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

没有本地 `.venv` 时用 `python`；构建只依赖标准库，默认输出 `dist/ChunSanShou_Video_Codex_1.2.26.zip`。

导入时暂停自动战斗，确认版本 `1.2.26`、三个角色均使用“导入代码”。该导入队伍需要显式导入启用。

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
