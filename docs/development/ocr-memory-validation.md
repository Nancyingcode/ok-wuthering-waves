# OCR 内存控制实施与验证

日期：2026-11-02，Asia/Hong_Kong。开发分支：`fix/upstream/openvino-ocr-memory-leak`；基线提交：`c6e4cfba65c3c359942d9770e51f2d44d9ff9860`。本轮改动尚未提交、推送或发布。

## 变更与接入

| 文件/依赖 | 变更 | 默认行为 |
| --- | --- | --- |
| `src/ocr_memory.py` | 延续稳定代理；取得代理不创建模型；显式预热；增加可选 OCR 自身闲置回收；一个全程监测所有者；退出与创建重叠清理；失败排空冷却；标量状态快照 | 仍预加载，执行器空闲 60 秒，CPU 128 次/300 秒/512 MiB 预算 |
| `config.py` | 分离 preload、lifecycle、params 和按设备编译参数 | batch=6、requests=1、空编译参数 |
| `patches/ocr/ok_script-2.0.22.patch` | 透传完整 OCR params，保留 logger/角度分类/设备默认值，读取预加载开关 | 原有配置兼容 |
| `patches/ocr/onnxocr_ppocrv5-0.0.22.patch` | 严格参数校验；det/rec/cls → PredictBase → compile_model；CPU/NPU 配置隔离；设备回退诊断；消费后移走 userdata 的 outputs/error | 空配置保持原设备逻辑；不丢弃被拒绝的实验属性 |
| `scripts/build_ocr_wheels.py` | 校验原 wheel 哈希，严格应用补丁，重写版本/RECORD，保存构建来源 | 只产生隔离验证 wheel，不改正式依赖源 |
| `scripts/benchmark_ocr.py` | 固定/真实 ROI 回放、每秒进程采样、逐次形状/输出大小/延迟、汇总和正确性比较 | 有限调用、时间和内存限制 |
| `scripts/benchmark_ocr_runtime.py` | 独立执行器/GUI 启动与通知重载观察，可加载原基线实现 | 隔离配置，不向游戏发送输入 |
| `src/task/ChangeEchoTask.py` | 确认按钮从全屏缩小为 `(0.5, 0.5, 0.85, 0.85)` | 简中匹配文字不变 |
| `src/globals.py` | YOLO 状态快照；显式安全卸载，阻止新检测入场并等待活动检测结束；下一次自动重载 | 不自动卸载，不套用 OCR 阈值 |

依赖构建、安装和回退的完整命令见仓库内 `patches/ocr/README.md`。本轮使用 `ok-script 2.0.22+okww.ocr1`、`onnxocr-ppocrv5 0.0.22+okww.ocr1`；原版本对照从已校验的原 wheel 恢复，在独立新进程中运行。没有将本机路径写入正式依赖。

编译参数默认严格执行。已配置属性不受支持时，该组实验失败，不自动删掉属性重试。原有空配置下 NPU 不可用/编译失败后的 CPU 设备回退继续保留。没有引入自动生产参数回退、OCR 子进程、强制 960 静态输入、工作集修剪或更短轮换预算。

## 环境与实验方法

- Windows 11 build 26200；Python 3.12.15；AMD64 Family 25 Model 33 Stepping 0，32 逻辑 CPU；物理内存 68,640,514,048 字节。
- 新建本工作树 `.venv`，没有可复用的本地虚拟环境；解释器来自已有 Python 3.12。`OpenVINO=2026.3.0`、`psutil=7.2.2`、`numpy=2.5.2`、`OpenCV=5.0.0.93`，所有组保持一致。GUI 使用 Qt 6.12.0，不能把本轮 GUI 数值直接套用历史安装版。
- 纯 OCR 实验固定 `use_npu=False`、CPU、`ONEDNN_MAX_CPU_ISA=AVX2`；GUI 另有生产 `use_npu=True` 对照，明确记录初始化自检。
- 输入清单为 `scripts/ocr_fixed_inputs.json` 和 `scripts/ocr_real_inputs.json`。后者固定四个真实截图 ROI，含数字、中文标题、确认按钮和一个无识别结果的区域。`tacet.result` 标签对应棋盘底部数字 500，并非结算页；该名称不会改变图像内容。空 ROI 的输出也参与一致性比较。
- 每个正式重复是新进程，10 轮创建/卸载，每轮 40 次识别，共 400 次。三次重复共 1,200 次，原始、补丁兼容和 batch=1 各一组。每轮卸载后观察 2 秒；磁盘编译缓存保留并预先跑过筛选，属于热缓存条件。
- 记录模型 SHA-256、包导入路径、源码提交、完整选项、截图形状、裁剪后形状、真实 det/rec 输入 shape、输出 nbytes、UTC 时间和单调经过时间。延迟使用高精度 `perf_counter`；初筛使用较粗的 Windows 单调时钟，仅用于筛选，不混入正式延迟重复。
- 每秒记录 Windows 私有内存和工作集，另在创建/卸载边界采样；表中峰值是采样峰值，不是分配器逐字节峰值。缺失私有内存字段时为 null，不使用工作集替代。
- 低点是每轮卸载后的短观察窗口最小值，不能等同于长时稳定值。宿主已有游戏/GUI，实验后半段游戏有重启和窗口变化，后台负载没有完全受控；延迟结果只支持保守的可选配置判断。

模型哈希：

| 模型 | SHA-256 |
| --- | --- |
| det | `d7fe3ea74652890722c0f4d02458b7261d9f5ae6c92904d05707c9eb155c7924` |
| rec | `bf66820f48fa99f779974c4df78e5274a9d8e0458c4137e8c5357e40e2c3faf2` |
| cls | `f4bb53707100c5f3d59ba834eb05bb400369f20aed35d4b26807b1bfadd2a70e` |

## 参数与输出持有验证

实际链路为应用 `ocr.params` → ok-script `_create_ocr_lib` → `ONNXPaddleOcr` → det/rec/启用时 cls → `PredictBase` → `Core.compile_model(config=...)`。生命周期选项不传给 OCR 构造函数。工厂能力标记使旧工厂上的非默认参数明确报错。

真实 CPU 编译回读得到 `NUM_STREAMS=1`、`INFERENCE_NUM_THREADS=16`、`PERFORMANCE_HINT=LATENCY`、`EXECUTION_DEVICES=['CPU']`。requests 已默认为 1，不计为新收益。无需把现有单流/LATENCY 再列为新优化。

`CPU_RUNTIME_CACHE_CAPACITY=200` 和 `0` 均被真实编译调用接受，但不能通过 CompiledModel 回读：记录 `readable=false` 和原生 “Unsupported property” 错误，不伪造实际容量。该内部属性按记录数配置，不是 MiB，也不是精确的 shape 数。磁盘 `CACHE_DIR` 沿用原实现，与 CPU 运行时缓存分别记录。

异步修复保留 `np.array(..., copy=True)`。回调先完成输出/异常写入，再 set 完成事件；消费线程只从自己的 job 中 pop outputs/error，不遍历 userdata，也不在回调仍可能写入时 clear。原生 queue 的最后一个 userdata 仍然存在，但不再额外持有已消费数组或异常链。

本负载原始最后一次 det 输出约 1.523 MiB，rec 输出约 11.221 MiB；这说明修复针对每个请求最后一份输出的有界保留，不能把所有内存下降归给它，更不能称为修复全部 native 泄漏。真实回放记录 rec 宽度恰为 320，不代表其他长文本不能扩宽。

## 对照结果

下表为三次独立重复的范围；内存统一为 MiB。首次调用包含首次构造加首次识别；后续每轮重新构造的时间另存 JSONL。末轮低点不代替每轮低点。

| 配置 | 输入/轮数 | 峰值私有内存 | 末轮回收后低点 | 峰值工作集 | 热推理 P95 | 首次调用 | 重载次数 | 正确性 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 原依赖 batch=6 | 每次 400 调用/10 轮 ×3 | 1395.1–1400.1 | 830.6–832.6 | 364.4–370.4 | 48.08–51.23 ms | 0.538–0.679 s | 每次 9 | 固定参考 |
| 补丁兼容 batch=6 | 同上 | 1391.7–1399.4 | 832.2–836.5 | 362.2–385.8 | 48.06–56.47 ms | 0.568–0.677 s | 每次 9 | 1,200 调用一致 |
| 补丁 batch=1 | 同上 | 1306.8–1316.8 | 829.1–839.9 | 323.3–327.0 | 58.36–71.51 ms | 0.602–0.750 s | 每次 9 | 1,200 调用一致 |

比较逐项检查文字及框位置（容差 2 像素），允许置信度的浮点差异。batch=1 的采样峰值中位数比原依赖约低 89 MiB，超过原始重复约 5 MiB 的峰值波动；但 P95 超出 10% 预算，不能作为平衡默认值。兼容组的内存峰值没有可确认的大幅改善，P95 中位数接近原始组，但仍有单次超预算波动；不把修补输出引用描述为总体性能保证。

初筛 batch=2 的峰值约 1348.6 MiB、P95 约 47 ms，只有一次短回放，仍为候选，不推荐为已验证最佳。cache=200 的峰值约 1376.8 MiB、P95 约 47 ms；cache=0 约 1375.4 MiB、P95 约 63 ms，与同轮 batch=6 筛选约 1377.6 MiB/47 ms 相比，没有足够内存收益支持启用。初筛时钟较粗，不能与正式高精度重复混为同一统计。

每组完整曲线都有每轮低点。原始组在约 42–44 秒内从首轮约 816–820 MiB 到末轮约 831–833 MiB；补丁组也没有显示低点被完全压平。兼容重复 2 的第 9 轮短观察低点出现 1030.9 MiB，下一轮回到 832.7 MiB，原始记录保留该异常。观察仅覆盖几十秒和 10 轮，不宣称长期收敛，也不将全部进程增量归因于 OCR native。

### 真实 GUI 与长任务

生产兼容配置保留 `use_npu=True`。本机实际 CPU，构造阶段会执行 `okscript` 自检；不能称为“尚未推理”。补丁 GUI 的模型创建在经过约 1.640 秒完成，约 62.156 秒释放，即预热结束后约 60.516 秒。卸载前后相邻采样为私有内存 1208.7→1010.8 MiB、工作集 465.1→355.6 MiB。

懒加载 GUI 使用相同 batch=6、NPU 请求及 WGC 条件。通知触发前（经过 7–8 秒）没有任何模型句柄/实例，私有内存约 972.6 MiB，工作集约 309.1 MiB；相对补丁预加载 GUI 的同阶段约 1208.8 MiB，少约 236 MiB。此收益只属于尚未调用 OCR 的启动阶段。

约 10 秒后通过正常执行器作业触发通知文件识别，首次识别含加载耗时 1.045 秒；约 72.297 秒发生 OCR 自身闲置释放，约 73.203 秒出现第 2 代模型，同一个通知适配器重载后识别耗时 0.542 秒。两次通知结果一致。全屏通知负载的模型峰值约 1674.0 MiB，不能与只做初始化自检的 GUI 峰值直接比较；闲置释放前后为 1668.3→1063.2 MiB 私有内存。

独立真实执行器验证用 2 秒闲置阈值缩短观察：通知首次加载后提交一个不调用 OCR 的长作业，经过约 10.204 秒完成释放，当时长作业仍运行；约 12.875 秒通过原通知代理建立第 2 代，识别成功。这个 2 秒值只用于有限验证，没有写入应用默认值。独占状态、活动推理、失败排空、取消、退出与创建重叠另有回归测试。

GUI 原始首轮（游戏尚未连接、捕获回落 BitBlt）约 1176.1→979.7 MiB。它与后来已连接 WGC 的 GUI 条件不同，不能用两者的约 30 MiB 差异归因于代码；因此另补同 WGC 状态的原依赖 GUI 对照：同阶段私有内存约 1208.0 MiB，释放前后 1207.7→1008.2 MiB，工作集 463.2→352.1 MiB，与补丁兼容 GUI 接近。三份原始记录分别保存。

### 用户指定的完整 BOSS 任务

通过现有注册 `FarmEchoTask`，固定首个强敌 Lv.90 天傀劫煞，队伍为 Hiyuki/Lucilla/Verina；每次先恢复血量，刷取 1 次，1920×1080、WGC、PostMessage、CPU OCR、同一 OpenVINO YOLO 后端。两组均新建服务进程，未主动卸载 YOLO；未领取需消耗资源的奖励。

| 配置 | 任务阶段耗时 | 采样峰值私有内存 | 峰值工作集 | OCR 调用/P95 | 游戏结果 |
| --- | --- | --- | --- | --- | --- |
| 补丁兼容 batch=6/executor_idle | 61.614 s | 1437.1 MiB | 557.5 MiB | 27 / 60.04 ms | 成功击杀，拾取 1 个声骸 |
| batch=1/不预加载/ocr_idle | 66.180 s | 1314.8 MiB | 500.2 MiB | 37 / 30.36 ms | 成功击杀，拾取 1 个声骸 |

任务阶段不含恢复血量。候选单次耗时增加约 7.4%，超过初始 5% 预算；只各一轮，战斗输入时序和 OCR 调用组成也不同，不能用表中任务 OCR P95 反驳固定序列的延迟退化，或据此宣布吞吐改善。该任务对照支持功能成功和本次采样内存变化，不能构成稳定的任务性能结论。

原非管理员服务的首次按键验证返回 PostMessage error 5，且只读确认游戏已提升权限；仅退出并提升本次自有服务后，地图开关验证成功。用户原有 GUI/MCP 保留。本次自有测试服务在两轮完成后已退出。完整任务首次元数据沿用通用环境的 `mcp=false` 字段，同行 `mcp_enabled=true` 及服务/作业记录是实际状态；后续及交付脚本已修正，不改写原始日志。

### YOLO 的独立贡献与显式释放

先做独立控制，再以相同已解码并持续持有的 `absorb.png` 正样本各重复 3 次；OCR 从未创建，截图、通知、MCP 条件固定。控制组在约 785.9–786.7 MiB 私有内存保持平稳；加载、首次检测、后续 5 次固定检测并静置后约 979.9–983.9 MiB。

通过交付的 `Globals.release_yolo()` 只释放 YOLO 资源后，私有内存约 809.6–815.2 MiB，三次分别回收 164.8、169.8、170.3 MiB。随后重载加检测耗时 0.449–0.498 秒，均得到与释放前相同的 1 个正样本框，实际设备为 CPU。

这支持所测 YOLO 路径存在独立的任务后驻留贡献，也显示仍有约 23–29 MiB 残留不能由模型对象销毁直接解释。不能用它解释从未初始化 YOLO 的 GUI 启动窗口。卸载接口关闭新检测入场、等待同步检测全部返回，清除局部引用后在锁外清理；下一次检测恢复。默认不调用此接口，不启用自动卸载，等待更长任务/设备验证后再决定启用时机。

### 本轮检查

原始 40 项 OCR 内存测试和 5 项配置测试重新通过。最终相关测试共 92 项：OcrMemory 47、OcrBackend 8、OcrBenchmark 2、Config 5、ChangeEchoOcrROI 1（含 1280×720、1920×1080、3840×2160 三个子场景）、WaitLogin 4、TacetElimination 21、YoloMemory 4，全部通过。后端与 YOLO 测试包括模拟并发风险及真实 CPU 推理，不能写为 NPU 已通过。

修改过的 Python 文件通过语法编译，`pip check`、`git diff --check` 和 `mkdocs build --strict` 通过；两个补丁 wheel 重复构建后 SHA-256 完全相同。真实 CPU 的未知编译属性实验明确失败，没有回退为默认参数。源码没有配置 Python typecheck/lint。本轮不生成安装包、不替换安装版。

## 配置建议与 P5 判断

1. **兼容默认**：维持应用当前 `preload=true`、`executor_idle`、60/128/300/512，batch=6、requests=1、空编译参数。已保留为默认与回退配置。
2. **可选懒加载**：只改 `preload=false`、`idle_policy=ocr_idle`，仍用 batch=6 和原保护预算。启动阶段能避免 OCR 模型驻留；首次调用和每次闲置回收后的调用承担加载成本。真实 GUI/通知已验证，尚不能把一组战斗对照称为所有任务的平衡最优。
3. **更小 batch 候选**：batch=1 的确降低本回放及 BOSS 样本峰值，但违反热推理/任务时间预算，不推荐为默认。batch=2、cache=200 仍只是筛选候选，不另列“已验证最佳”。

没有触发 P5：目前未将长期上升明确定位到 OCR native，短回放也不足以判断持续低点趋势。暂不做形状分桶、trim 或 OCR 进程隔离。后续需要延长同一真实序列/任务的多轮观察，区分 Python、原生中间缓冲与其他组件，并在主要增长明确归因后再启动相应优化。

本轮已量化启动预加载、已消费输出额外持有和所测 YOLO 路径的贡献；剩余 GUI/运行库/截图/分配器占用仍没有完整归因。历史 1908.1 MiB 是工作集，不能与卸载后的私有内存相减。没有把“Released”日志或弱引用销毁当作所有原生内存归还的证明。

可选低内存配置示例（作为整个 `ocr` 项，或传给 runtime 的 JSON 文件）：

```json
{
  "lib": "onnxocr",
  "preload": false,
  "lifecycle": {
    "idle_policy": "ocr_idle",
    "idle_seconds": 60,
    "max_calls": 128,
    "max_age_seconds": 300,
    "process_growth_mib": 512
  },
  "params": {
    "use_openvino": true,
    "use_npu": true,
    "rec_batch_num": 6,
    "openvino_num_requests": 1,
    "openvino_compile_config": {"CPU": {}, "NPU": {}}
  }
}
```

## 原始证据与重跑入口

本机原始 JSONL、测试日志、环境 freeze、GUI 记录和 MCP 画面位于 `logs/ocr-memory/`，受 gitignore 保护。没有提交敏感截图或大日志。

```powershell
$env:ONEDNN_MAX_CPU_ISA = 'AVX2'
.\.venv\Scripts\python.exe scripts/benchmark_ocr.py replay `
  --manifest scripts/ocr_real_inputs.json --output logs/ocr-memory/my-run.jsonl `
  --repeats 10 --cycles 10 --settle 2
.\.venv\Scripts\python.exe scripts/benchmark_ocr.py summarize logs/ocr-memory/my-run.jsonl
.\.venv\Scripts\python.exe scripts/benchmark_ocr.py compare `
  --reference logs/ocr-memory/original-repeat-1.jsonl logs/ocr-memory/my-run.jsonl
.\.venv\Scripts\python.exe scripts/benchmark_ocr_runtime.py `
  --mode executor --seconds 70 --output logs/ocr-memory/startup.jsonl
```

`replay --config` 接受 JSON `{ "params": { ... } }`；`runtime --config` 接受整个 OCR 项配置。输出文件使用独占创建，不能覆盖旧实验；每次改用新文件名。外部进程采样可使用 `benchmark_ocr.py sample --pid PID --seconds 120 --output ...`。

GUI 使用 `--mode gui`；`--legacy` 从固定基线提交读取原 ManagedOCR 实现，但仍需先恢复原依赖，才能称为原始 GUI 对照。GUI 设置保存在该次输出旁的独立目录，MCP 与触发任务关闭，原用户 GUI 不被关闭。源码 GUI 中 PyAppify 更新检查报告没有安装版版本信息，与 OCR 结果无关，原日志保留该错误。

完整任务的薄 MCP 入口为 `scripts/benchmark_farm_echo.py`，只委托注册的 `FarmEchoTask`，先回血后刷首个强敌一次，并输出 `memory.jsonl`、任务结果和开始/结束画面。可用 `scripts/ocr_benchmark_config.py:config` 启动隔离服务；`OCR_BENCHMARK_CONFIG` 环境变量指定上述 OCR JSON。该配置固定 CPU OCR，不切换 YOLO 后端。通过服务发现取得 URL/令牌后调用 `run_task`，传入脚本绝对路径；令牌不要打印到日志。运行前保持游戏及现有自动化处于可接管状态。

YOLO 独立重跑入口：

```powershell
.\.venv\Scripts\python.exe scripts/benchmark_yolo.py --output logs/ocr-memory/yolo-control.jsonl
.\.venv\Scripts\python.exe scripts/benchmark_yolo.py --load --reload --output logs/ocr-memory/yolo-loaded.jsonl
```

正式 YOLO 结论使用 `yolo-api-*`（protocol=3）记录。早期 `yolo-*`、`yolo-v2-*` 保留为探索数据，不与正式表混合。原始依赖/补丁回放分别见 `original-repeat-*`、`patched-*-repeat-*`；任务证据为 `mcp/f1715a77369440158d838558801c6e17/` 和 `mcp/9788be9ec9484b68815b37aad008a219/`。

## 回滚与边界

默认保留兼容配置。需要恢复原行为时，使用预加载、executor_idle、60/128/300/512、batch=6、requests=1、空编译参数，重启后新代理/监测器接管；再按补丁说明恢复原依赖。ROI 的单行改动和 YOLO 观测可独立回退。

GUI 任务卡 Start、真实 NPU、不同截图后端/宽屏分辨率、完整长时内存收敛，不能由离线测试替代。登录二次确认、声骸其他标题及无音结算等待缺少对应实时/固定图像证据，本轮没有扩大 ROI 修改范围。没有同帧去重或全局降采样。
