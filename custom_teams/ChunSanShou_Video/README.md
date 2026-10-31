# 椿 / 散华 / 守岸人导入队伍

当前源码为 `1.2.8` 三格地板出口候选，位于 `1.2.7` 之上（`1.2.7` = 中高位稳态出口，位于 `1.2.6` 之上；`1.2.6` = 贴零稳态出口，位于 `1.2.5` 之上；`1.2.5` = 尾段基线改用本段观测峰值、回滚满格滑动窗口，位于 `1.2.4` 之上；`1.2.4` = 满格停滞滑动窗口，位于 `1.2.3` 之上；`1.2.3` = 满格平台期抖动修复，位于 `1.2.2` 之上；`1.2.2` = 满格平台期补按，位于 `1.2.1` 之上；`1.2.1` = 开花尾段判据与起步消耗修复，位于 `1.2.0` 之上；`1.2.0` = 输出段丢锁恢复，位于 `1.1.9` 之上；`1.1.9` = 回切正倒计时保护，基于 `1.1.7` 功能版本，保留 `1.1.6` 六秒回切恢复）。`1.1.8` 连续采样及包装已从生产实现撤下，诊断源码与原包备份在 `downloads/chunsanshou-1.1.9/`，历史采样测试归档到 `downloads/chunsanshou-1.1.8/diagnostic-tests/`。

## 1.2.8 变更

`1.2.7` 两轮实测（5631 / 5068，均值 5299.5，8 花 / 5 完成，重击 1.13 次每花）里 1.2.7 的中高位稳态出口一次都没上场（`bloom steady completion` = 0、`bloom timing` 的 `steady=` 全为 `False`），实战第一失败源换成了「三格地板」，而且两次完全同形：21:43:54（`forte=0.082`、`sample_age=0.078`、`decreases=3`、`drop_age=1.188`、`span=1.438`、`steady_hold=1.11`、`steady_range=0.0`、`steady_flat=True`、`drop_total=0.074`、`consumed_ratio=0.918`，以 `tail grace timeout` 收场，`wall=10.093`、`tail_extra=2.093`）与 21:44:31（`forte=0.082`、`samples=26`、`decreases=0`、`drop_age=-1`、`span=1.187`、`steady_hold=1.187`、`steady_range=0.0`、`steady_flat=True`、`consumed_ratio=0.918`，以 `tail progress lost` 收场）。两次都是 `heavy_retries=2` 已用尽、开花标记仍在屏。

0.082（约 8.15%，三格）正好落在三条门限的缝隙里：`0.082 > BLOOM_COMPLETE_FORTE = 0.06` 使 `bloom_ended` 永不成立（`ended_frames=0`）；`0.082 > BLOOM_ENDED_FORTE = 0.05` 使 1.2.6 的 `zero_steady` 不成立（`zero_hold = -1.0`）；`0.082 <= 0.25` 使 `inside_tail = True`、`reason` 只会落在 `stale_decrease` / `insufficient_decreases`，永远不会是 1.2.7 要求的 `outside_tail`。

`1.2.8` 只加这一条出口，`BLOOM_COMPLETE_FORTE = 0.06`、`BLOOM_ENDED_FORTE = 0.05`、1.2.6 贴零判据与 1.2.7 中高位判据一律不动，八个硬约束常量（`BLOOM_OUTPUT_TIMEOUT = 8`、`BLOOM_WALL_TIMEOUT = 16`、`PHASE_TIMEOUT = 24`、`BLOOM_TAIL_GRACE = 2`、`BLOOM_TAIL_RECHECK = 0.4`、`BLOOM_FEEDBACK_TIMEOUT = 1.2`、`BLOOM_TARGET_WAIT_MAX = 2.0`、`BLOOM_TARGET_WAIT_TOTAL = 3.0`）继续不动：

- **地板口径**：新增 `BLOOM_TAIL_FLOOR_MAX = 0.10`（只覆盖三格 0.082 并留识别抖动余量，四格 ≈0.109 与 0.20 以上的中高位仍按原判据）、`BLOOM_TAIL_FLOOR_SPAN = 1.0`、`BLOOM_TAIL_FLOOR_RETRIES = 2`；尾部容差复用 `BLOOM_TAIL_STEADY_TOLERANCE = 0.005`，消耗比复用 `BLOOM_TAIL_CONSUMED_RATIO = 0.65`。
- **`Rotation.bloom_floor_completion_allowed`**：五组条件同时成立才放行——① `output_wall >= output_budget`，与 1.2.7 一样不提前截断真实输出；② 同角色在场、`camellya_budding` 可见、非解放态；③ `heavy_retries >= 2`，队内两次补按已用尽；④ `BLOOM_COMPLETE_FORTE < forte <= BLOOM_TAIL_FLOOR_MAX` 且 `sample_age <= 0.5`；⑤ 尾段 `reason` 只接受 `stale_decrease` / `insufficient_decreases`（已判 `progressing` 的形态继续走宽限，`stale_sample` / `insufficient_samples` / `short_observation` 不放行）、`consumed_ratio >= 0.65`（与 1.2.5 一致，按本段观测峰值计算，峰值不可读时不开放）、尾部 `steady_flat=True` 且 `steady_hold >= 1.0`、`steady_range <= 0.005`。
- **出口形态**：成立时 `outcome=completed`、不进恢复分支；新增 `椿散守 bloom floor completion` 日志与 `capture_bloom_diagnostics('floor_completion', 'three_grid_floor')`，`bloom timing` 行新增 `floor=True/False`（与既有 `steady=` 并列，作为版本确证字段）。
- **完全不动**：`bloom_tail_status` 的因子与先后顺序、`maintain_bloom_heavy` 的重试上限与平台期判据、两个结束确认阈值。

三个成员文件的 `PACKAGE_ID` 必须同为 `chunsanshou-v1.2.8-bloom-floor-exit`。

`1.2.8` 尚无实战数据。实战验收仍按 `.dsh-tmp/任务3-8秒收尾验收基线.md`，并须在日志里读到 `bloom floor completion` 或 `floor=True` 才算该出口上场；`tail_extra` 0.093 过冲、`bloom_resume_budget`、P5 丢锁与 P7 回切继续单列。

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

没有本地 `.venv` 时用 `python`；构建只依赖标准库，默认输出 `dist/ChunSanShou_Video_Codex_1.2.8.zip`。

导入时暂停自动战斗，确认版本 `1.2.8`、三个角色均使用“导入代码”。该导入队伍需要显式导入启用。

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
