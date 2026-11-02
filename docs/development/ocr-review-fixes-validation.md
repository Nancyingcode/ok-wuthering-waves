# OCR 审阅修复验收（R1—R4）

执行时间：2026-11-02 至 2026-11-03，Asia/Hong_Kong。证据根目录：`logs/ocr-memory/review-fixes-20261102-231441/`，下文证据路径相对此目录。历史事实仍见 [第三轮报告](ocr-memory-round3-validation.md)，本轮不改写历史记录。

## 基线与范围

实际起点为 `fix/upstream/openvino-ocr-memory-leak`，HEAD `d36de36b08e2cd52521db32b346093b7780ff766`；修复开始时 staged、unstaged、untracked 均为空。`before-source.zip` 在首次修改前保存 37 个相关文件的原字节，`before-source.json` 记录逐文件 SHA-256、Git 状态、依赖实际路径及 wheel 指纹。没有按历史 HEAD 恢复文件，没有清理工作区，也没有提交或推送本轮改动。

第三轮最终核心实现已经存在于当前提交；在实际快照上核验并增量修复。默认 A 仍是 `preload=true`、`executor_idle`、60 秒 / 128 次 / 300 秒 / 512 MiB，batch=6、requests=1、空编译配置。B 保留为可选的 `preload=false` / `ocr_idle`。`config.py`、依赖补丁、wheel、共享代理所有权和既有预算均未更改。

本轮使用仓库 `.venv/Scripts/python.exe`：Python 3.12.15、ok-script 2.0.22+okww.ocr1、onnxocr-ppocrv5 0.0.22+okww.ocr1、OpenVINO 2026.3.0、PySide6 6.12.0；实机项使用 CPU。未安装或升级依赖。

## 逐项结果

| 项目 | 本轮状态 | 证据及覆盖边界 |
| --- | --- | --- |
| R1 | 已修复，定向回归和默认 60 秒 CPU 验证通过 | `TestOcrIdleLocking` 2 项及 `TestOcrMemory` 54 项；默认闲置、监视线程存活、同代理重载与正常退出均有实测记录 |
| R2 | 已修复，污染拒绝及干净 staging 导入通过 | `TestOcrRelease` 7 项；真实 staging 的 ok 314 / onnxocr 21 个成员完全对应 wheel；未覆盖全套 CI 下载/安装前段或 EXE |
| R3 | 已修复，区间与失败协议回归、短诊断通过 | `TestOcrObserver` 14 项；managed/direct 各 8 次两代，各 8 个尾部目标全部有记录；旧长日志新增健康字段仍为 unknown |
| R4 | 已修复，545 次严格正例及 18 种原日志破坏负例通过 | 545 对输出最大框差 0 px；每组 954 条关联原生记录一致；18 个坏日志 CLI 均非零；实际 audit 和复现入口已接入严格门禁 |
| 补丁交付 | 标准 Git 增量补丁已做检查与真实应用，字节一致 | 基于实际修复前 ZIP；独立临时 Git index，保留行尾与 `.gitattributes`；验证记录见 `patch-validation.json` |
| 原生驻留归因 | 未证实，继续保留 | R1—R4 不定位 native allocator/cache/leak；旧高驻留事实不等于永久泄漏结论 |
| 正式 EXE、真实 NPU、Farm 卡真实暂停/取消 | 未验证 | 不以源码 staging、CPU 或控制替身测试替代这些验收 |
| 历史性能/完整文本期望 | 原有未通过或未建立通过结论，保持原状态 | 本轮不重跑长时间矩阵；不据输出等价宣称所有业务期望已满足或 B 性能更优 |

## R1：空闲时间戳与锁边界

`src/ocr_memory.py` 的所有 `_ocr_idle_since` 读写使用同一调度锁。监视器只读一次旧值，并在锁内同步更新实例值和局部值；锁外显式处理 `None`。任务包装器只短暂持锁重置，调用原方法时已释放；没有改为 RLock，没有将原生排空或清理移入调度锁。缓存锁仍非阻塞获取。

`R1-before.log` 对精确旧实现复现两项失败：读取中被锁外写入 None 导致算术异常，以及包装器锁边界错误。修复后的事件控制测试由第三方解除屏障，使用普通不可重入锁，证明写入方等待正确且原方法能取得同一锁。既有回归覆盖计时重置、后续释放、缓存代理重载、单监视器和退出事件。

实机证据为 `default-idle-explicit.jsonl`：1.453 秒首次通知识别到 `500`；61.938 秒观察到 idle 释放，监视线程仍存活；62.344 秒同一代理 `1376850742208` 重载至 generation 2，结果相同，线程仍存活；退出后 `monitor_alive=false`。使用原默认 60 秒而非缩短阈值。另保留早先未显式记录线程字段的 `default-idle.jsonl`，不将其替代显式记录。

## R2：干净 staging 与完整成员核验

准备器在任何元数据修改前拒绝已有 `ok`、`onnxocr` 或 provenance，包括文件、目录和链接。安装目录只验证预期文件，允许正常缓存而不修改它；交付内容从已核验 wheel 的全部包内成员生成。目标要求路径集合与字节哈希完全相等，拒绝额外同名包、`.pyc`、`.pyd`、`.dll`、缺失或改变内容，以及不符合普通文件约定的 wheel 条目。

现有 inline helper 的“目录存在则跳过复制，但继续更新版本、requirements、deploy”语义已核对。helper 和实际导入探针使用 `-B` 及禁写字节码设置；导入后再次完整核验，最后才写成功 provenance。`R2-before.log` 保留旧实现的污染及前置拒绝失败。

`R2-staging-run.json` 和 `R2-staging.log` 记录一次真实准备成功；`R2-target-members.json` 保存全部 335 个成员哈希与 provenance。真实探针的 `ok`、`ok.task.TaskExecutor`、`onnxocr`、`onnxocr.predict_base` 均解析至新 staging 的相应 wheel 文件，工厂能力标记和编译配置入口通过。资源/模型文件一并保留，不只复制 Python 文件。最终集合复核未产生缓存。

两份 wheel SHA-256（本轮未重建或修改）：

| wheel | SHA-256 |
| --- | --- |
| ok_script-2.0.22+okww.ocr1 | `c06466a9d5efc60457e3c10916ac793a2f51becd398358735ec3d805d21b180a` |
| onnxocr_ppocrv5-0.0.22+okww.ocr1 | `a48309573726f856b5728030b41ac8a1fa1a6dfdfb14df290d681a7dabada6aa` |

validate、synchronize、package 是独立 checkout 的 CI 作业；本轮没有把它们当成同目录重复准备，也未运行完整 CI 前段、正式安装器生成或发布。

## R3：采样区间、名额与完成协议

managed 复用原条件锁与模型状态；direct 使用仅含标量的短锁快照。创建前、模型发布、活动调用和释放相关变化递增 epoch。查询进程内存和 VirtualQuery 时不持生命周期、调度或缓存锁，不等待观察者来串行化模型活动。新共享观察器记录 schema 2、前后状态/epoch、单调开始/结束、目标延迟、迟到量及无效原因。

内存点和分类分别验证完整区间。仅相同 epoch、目标代次一致、前后均卸载且无活动/创建/回收的内存点才能提交基线；分类需再次验证才占用分类名额并允许提前停止。ABA 和下一代创建/已卸载都不能算作目标代纯卸载。

协议按释放代记录唯一 0/2/10/30 秒目标，区分“已记录但无效”和“缺失”。错误通道只保存有界类型、消息和阶段标量，不保留异常或 traceback。完成前停止生产者、排空已接收事件、处理应完成目标并限时 join；只有无错误、无丢失、无缺失且线程退出才写成功。审计也从实际边界和尾部记录重算覆盖，不能只信任 complete 布尔值。摘要入口保留旧 schema 的未知完整性。

| 本轮实机日志 | 调用/代数 | 目标 expected/observed | 无效/缺失 | 结果 |
| --- | --- | --- | --- | --- |
| `R3-managed-short.jsonl` | 8 / 2，自然预算边界 | 8 / 8 | 3 / 0 | 39.468 秒；无错误、无丢失、线程停止，success |
| `R3-direct-final.jsonl` | 8 / 2，手动生命周期归因 | 8 / 8 | 4 / 0 | 38.843 秒；无错误、无丢失、线程停止，success |
| `R3-ordinary.jsonl` | 1 / 1，非诊断 | 0 / 0 | 0 / 0 | 2.094 秒；无新增 30 秒等待，success |

先前两份 direct 短日志也保留。最终 direct 使用最终观察器源码；managed 短日志与最终版本之间只调整了查询开始时间戳放在首个状态快照后的位置及删除无用 import，未改变区间判据或状态提交逻辑。未用短诊断推算长时间内存表现。

`TestOcrObserver` 覆盖内存期间创建、ABA、单独分类失效/可重试、重复边界、下一代观测、冷却不足、查询异常、队列溢出和 join 超时。四种失败另经真实 Python 子进程验证：均非零退出，终态 incomplete，无 completed。未观察到 high 不属于失败，也未为凑分类增加等待。

旧长日志通过新审计后 `observer_health` / `interval_completeness` 为 unknown。旧 direct 与 factory 重叠的两个分类继续排除；其他旧区间也不被伪造为新协议已验证。旧完整 OCR 输入/输出仍可供 R4 使用。

## R4：完整固定序列严格门禁

`compare --strict` 验证参考和候选各自 JSONL、完成记录/调用数、运行内部 run_id、0..N-1 唯一顺序、逐 index label、图像 shape、输入身份及完整 OCR 结果结构。不同运行之间无需相同 run_id；完成后正常 shutdown 兼容。第三轮 managed 的输入哈希从同运行同 index 原生记录关联；det/rec 及 recognition 已有哈希不能互相冲突。

结果要求四点二维有限数值框、精确文本；每坐标容差仍为 2 px，置信度差异允许，合法空检测可相等。中间 tensor shape 不作为不同配置的统一门槛。详细差异最多保存 100 例，总数完整累计；结构或身份未验证不能 overall equal。兼容比较保留并明确 `scope=observed_labels`。

复用第三轮原始文件，未修改其字节或重新跑长实验：

| 原始日志 | SHA-256 |
| --- | --- |
| diagnostic-managed-retry.jsonl | `4e0bfb4294219f571d6c7d4bc15ce35f90b42864360356400b8c831b50f55454` |
| diagnostic-direct.jsonl | `8d59e25926dd26d7ff1ff55ea302cf1380b1f423ac15941647c18fc8038879ee` |

`R4-545-final-strict.json`：545/545，运行完整性、输入身份、结构和输出等价均通过，最大框差 0 px、差异数 0，CLI 退出 0。`R4-native-954.json` 独立逐条对齐每组 954 条原生记录的 index、label、generation、model、input_sha256、shapes、dtype、输出字节数，全部一致。这里 input_sha256 是传入图像/ROI 字节，不能称为预处理 tensor 哈希。

`R4-negative-cli.json` 及 `negative-545-utf8/` 保存原日志复制破坏后的 18 种负例、实际命令、候选哈希、JSON 结果和非零退出码：仅首条、无完成、错误调用数、错误哈希/shape、缺失身份、原生身份冲突、重复/缺失/换序序号、混合运行、截短框/坐标、NaN/Infinity、坏 JSON、文字改变和坐标差 3 px。7 项比较器测试另覆盖恰好 2 px、仅置信度/中间 shape 改变、空结果和 121 个差异的完整累计。

`R4-before-false-positive.json` 使用 before ZIP 中的精确旧比较源码，重现“仅首条”及“仅首条且 hash/shape 错误”仍 equal 的缺陷；修复后的真实 CLI 拒绝它们。`R4-audit-negative-command.json` 证明实际审计入口也以非零拒绝坏日志。`audit-old-545/` 和实跑的 `reproduce-compare/` 证明严格参数已经用于验收入口。旧归档 `reproduce.ps1` 保留原文，新增维护入口为 `scripts/reproduce_ocr_review.ps1`。

## 定向回归与可复现命令

本轮最终执行合计 99 项，0 失败、0 跳过：IdleLocking 2、Memory 54、Backend 8、Release 7、Benchmark 7、Observer 14、ReplayCompare 7。原始输出在 `final-*.log` / `final-temp-*.log`，统一复现入口实跑输出在 `reproduce-checks/`。首次 Release 复测因沙箱默认 TEMP 拒绝写入而失败；切换本工作区的临时目录后通过，失败日志保留。证据辅助脚本首次读取 CLI 输出遇 Windows 默认编码失败，设置 PYTHONIOENCODING=utf-8 后完成，两个尝试均保留。

仓库根目录 PowerShell 中执行；每次输出目录必须是新路径。以下阶段按需单独运行，不自动串成长实验：

```powershell
.\scripts\reproduce_ocr_review.ps1 -Stage checks
.\scripts\reproduce_ocr_review.ps1 -Stage compare
.\scripts\reproduce_ocr_review.ps1 -Stage audit
.\scripts\reproduce_ocr_review.ps1 -Stage idle
.\scripts\reproduce_ocr_review.ps1 -Stage staging
.\scripts\reproduce_ocr_review.ps1 -Stage managed
.\scripts\reproduce_ocr_review.ps1 -Stage direct -Reference <上一命令生成的 managed.jsonl>
```

直接复用旧日志的真实门禁命令：

```powershell
$env:PYTHONIOENCODING = 'utf-8'
.\.venv\Scripts\python.exe scripts/benchmark_ocr.py compare --strict --reference logs/ocr-memory/round3-20261102-211933/diagnostic-managed-retry.jsonl logs/ocr-memory/round3-20261102-211933/diagnostic-direct.jsonl
```

本轮 staging 实际命令和 cwd 保存在 `R2-staging-run.json`。8 次 managed 使用 `--protocol calls --calls 8 --period 1 --seconds 15 --cooldown 31 --manifest scripts/ocr_real_inputs.json --diagnostics`；direct 用第三轮 `direct-fix-reference.jsonl` 的两代各四次序列。完整原始 argv、输出与退出结果汇总见证据包的命令记录及复现脚本。离线破坏夹具生成器 `validate_ocr_review_evidence.py` 一并交付，在仓库根目录用本地解释器和包含 before ZIP、短日志及 staging 的原证据目录运行；输出独占，需要新的目录或按脚本复制相关输入，避免覆盖本次证据。

## 源码指纹和标准补丁

`source-hashes.csv` 给出修复前后全部相关路径、字节数及 SHA-256；新增文件的 before 为空。`after-source.json` / `after-source.zip` 为最终快照。核心文件：

| 文件 | before SHA-256 | after SHA-256 |
| --- | --- | --- |
| src/ocr_memory.py | `a1b6b8f13f2712b1747ca7d456be0502281e9916bef020526424770ef6fa9d1e` | `f4311908aa1b839c46012123616d852367eb3247d19c714064cd4ada1289f6a8` |
| scripts/prepare_ocr_release.py | `c40f1638b16ebe5dc166b0d3d63021782e0dcb622a41146c97f6b3d1262ca84f` | `1d8d2ba65c1c53a1b5a20eab7b9c1e9e30ba9a611f57b6337e787933f8f573dd` |
| scripts/benchmark_ocr.py | `ac830564a38f29e6cb0d22e1081c2b6c3197d602dce78289bffb1fb2033b5334` | `8db4a6d6b54a645ba1cbd2320d11d1ef2c0e720242c862718888d276de8e50e5` |

`ocr-review-fixes.diff` 由临时仓库将精确 before 字节加入独立 index、`git write-tree` 保存基线，再覆盖最终字节并 `git add -A`，以 `git diff --cached --binary --full-index <before-tree>` 生成。临时 Git 设置 core.autocrlf=false、core.safecrlf=false，保留 `.gitattributes`；未统一转换源码行尾，未使用真实工作区 index，也未创建提交。

另一个精确 before 副本执行 `git apply --check`，再真实 `git apply`，对所有文件字节、增删集合和 Git 文件模式逐项比较最终快照。未用 --reject、忽略空白或强制覆盖。应用命令（在精确 before 副本中）：

```powershell
git -c core.autocrlf=false apply --check <ocr-review-fixes.diff 的绝对路径>
git -c core.autocrlf=false apply <ocr-review-fixes.diff 的绝对路径>
```

`patch-validation.json` 记录完整 argv、临时树身份、补丁 SHA-256、逐文件验证结果及真实 index 未改变证明；`build_review_patch.py` 交付可复现生成过程。证据包另含被复用的原始 545 次日志和短参考、前后快照、回归输出、坏日志副本、staging 成员指纹及报告；不复制整个安装环境或伪装为安装器。
