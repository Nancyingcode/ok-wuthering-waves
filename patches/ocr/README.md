# OCR 依赖补丁

这些补丁基于 PyPI 的 `ok-script==2.0.22` 和 `onnxocr-ppocrv5==0.0.22`，固定原始 wheel 的 SHA-256。OpenVINO 始终使用 `2026.3.0`。不修改安装版，也不直接编辑 `site-packages`。

应用顺序为 `ok_script-2.0.22.patch`、`onnxocr_ppocrv5-0.0.22.patch`；两个包各自独立构建。第一个补丁提供参数透传、预加载入口和能力标记；第二个补丁提供严格参数校验、CPU/NPU 分开的编译参数、实际属性诊断，以及已消费异步输出和异常的引用清理。补丁保留输出复制、队列排空和原有设备探测逻辑。

在项目环境准备好后运行：

```powershell
.\.venv\Scripts\python.exe scripts/build_ocr_wheels.py --download
.\.venv\Scripts\python.exe -m pip install --no-deps --force-reinstall `
  build/ocr-wheels/patched/ok_script-2.0.22+okww.ocr1-py3-none-any.whl `
  build/ocr-wheels/patched/onnxocr_ppocrv5-0.0.22+okww.ocr1-py3-none-any.whl
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe run_test.py tests/TestOcrBackend.py
```

Windows 受限环境的临时目录不可写时，先将本次进程的 `TEMP`、`TMP` 指向项目中可写的临时目录。安装前结束使用本环境的验证进程。

构建器校验原 wheel 哈希，以严格上下文应用补丁，更新 METADATA 和 RECORD，并在 dist-info 内加入 `okww-ocr-source.json`。产物使用本地版本号 `+okww.ocr1`，不会冒充未修改的发布包。重复构建得到相同 wheel 哈希。被补丁文件和补丁文本使用 LF；其余 wheel 成员保留原始内容。

这是一条隔离验证用的依赖接入路径。没有把不存在于索引的版本写入正式 `requirements.txt`；正式发布前应将两个通用补丁分别合入对应依赖仓库、发布固定版本并更新应用依赖。应用会在旧工厂无法透传非默认参数时明确报错，避免把忽略参数的运行误作成功实验。

回退依赖：

```powershell
.\.venv\Scripts\python.exe -m pip install --no-deps --force-reinstall --no-index `
  --find-links build/ocr-wheels/upstream ok-script==2.0.22 onnxocr-ppocrv5==0.0.22
```

同时恢复兼容配置：`preload=true`、`idle_policy=executor_idle`、60 秒闲置、128 次调用、300 秒年龄、512 MiB 进程增长，batch=6、requests=1、空编译参数。参数在下次模型创建时生效；生命周期策略在新代理创建时固定，完整切换请重启应用。

A 是默认配置。B 只改变 `preload=false`、`idle_policy=ocr_idle`，作为可选策略保留；它避免未使用 OCR 时的预加载，并承担首次使用和闲置后的加载成本。第二轮任务证据没有证明 B 稳定优于 A，不作为“平衡默认”。两种策略均可通过恢复上述 A 配置并重启回退。

验证命令、结果和限制见 [实施报告](../../docs/development/ocr-memory-validation.md)。

## 依赖能力契约

应用侧 `ManagedOCR` 实现预加载开关和生命周期，原版 `ok-script 2.0.22` 仍可使用兼容默认，也可使用应用侧的懒加载/闲置策略。不能把此能力归因于后端补丁。

非默认 batch、请求数或其他透传参数需要工厂的 `ocr_params_passthrough` 能力；缺失时在执行器初始化阶段报错。非空 OpenVINO 编译参数还要求 OnnxOCR `PredictBase` 构造函数具备 `openvino_compile_config` 入口，只有工厂补丁时也会明确拒绝。实际编译属性是否可用仍由真实编译验证，不能用版本字符串替代。默认空配置保留原依赖路径。

## 本地更新包接入

```powershell
.\.venv\Scripts\python.exe scripts/build_ocr_wheels.py `
  --bundle build/ocr-local-validation.zip
```

输出 ZIP 使用 `deploy.txt` 中应用源码/资源的目录布局，包含相对路径的补丁 wheel、每个成员的 SHA-256、独立设置和启动入口。它是本地源码/更新包验证产物，不是 PyAppify Windows 安装器。输出 ZIP 独占创建，重跑时换新文件名；wheel 本身保持确定性构建。

解压到新的测试目录，用兼容 Python 3.12 的绝对解释器路径安装：

```powershell
./install-validation.ps1 -Python C:/path/to/python.exe
./.runtime/Scripts/pythonw.exe main_validation.py
./.runtime/Scripts/pythonw.exe main_validation.py --candidate
```

安装脚本先核对包内文件哈希，再新建 `.runtime` 并通过生成的相对 `requirements.txt` 安装，拒绝覆盖已存在的 `.runtime`。候选只覆盖 preload 与 idle_policy。验证启动器使用独立配置且关闭 MCP，不替换日常安装版。完整 GUI 验证还可在包内通过 `scripts/benchmark_ocr_gui.py` 运行。

项目的正式 EXE/安装器由 `.github/workflows/build.yml` 中 PyAppify action 构建、签名及发布。本地 ZIP 接入不能作为该安装器已通过的证据；正式发布仍需将补丁纳入受支持的依赖发布链。

## 正式部署目录的补丁核验

CI 的准备步骤使用固定来源 wheel：先构建并安装补丁，再通过 `prepare_ocr_release.py` 调用现有内联工具，同时内联 `ok` 和 `onnxocr`。准备器在修改元数据前拒绝任何已有目标包目录或 provenance，要求使用全新 staging。它核对安装依赖的预期成员，但从 wheel 复制全部包内文件，不复制安装缓存。目标路径集合和每个文件哈希必须与 wheel 完全相等，额外同名包、字节码或原生文件也会失败。

现有 inline helper 继续更新版本、requirements 和 deploy；helper 与实际导入探针均使用 `-B`，之后重新检查完整成员集合。探针验证 `onnxocr.predict_base` 等模块实际来自 staging，且具备补丁入口。全部通过后才写成功 provenance；不清理安装目录或已有 staging。部署清单会加入 OnnxOCR 和 `ocr-release-provenance.json`。

以下命令会修改指定部署源码中的版本、requirements 和 deploy.txt，应只在 CI 干净检出或独立测试目录执行：

```powershell
python scripts/build_ocr_wheels.py --download
python -m pip install --no-deps --force-reinstall build/ocr-wheels/patched/ok_script-2.0.22+okww.ocr1-py3-none-any.whl build/ocr-wheels/patched/onnxocr_ppocrv5-0.0.22+okww.ocr1-py3-none-any.whl
python -m pip check
python scripts/prepare_ocr_release.py --app <独立部署源码目录> --wheels build/ocr-wheels/patched --tag <本地产物版本>
```

独立测试仍须使用该目录对应的 `.venv` / 隔离解释器；上例的 `python` 是 CI 已选择的 Python 3.12。它只完成可核验的部署源码接入，不证明 PyAppify EXE 已生成、安装或发布。签名、上传、远程同步与日常安装版替换仍需各自授权和直接验证。

## 可选生命周期诊断

`OKWW_OCR_DIAGNOSTICS=1` 为单个验证进程启用标量预算日志，默认关闭，不改变保护阈值。`benchmark_ocr_runtime.py --diagnostics` 另加有界弱引用和固定阶段内存分类观测；该模式用于归因，不能代替无诊断开销的性能验收。具体有界命令和结论见第三轮报告。

审阅修复后的诊断 schema 2 使用生命周期 epoch 检查整个内存/分类查询区间，逐代记录 0/2/10/30 秒唯一目标。无效点仍保留但不提交分类；缺失、线程异常、事件丢失或退出超时均以非零退出且无成功终态收口。普通非诊断模式不新增 30 秒等待。旧日志的新增健康字段保持 unknown。

完整固定序列验收使用 `benchmark_ocr.py compare --strict`；默认兼容模式的 `scope=observed_labels` 仅核对已经出现的标签输出。审计入口和新的 `scripts/reproduce_ocr_review.ps1 -Stage compare` 已调用严格门禁；第三轮归档脚本保留历史原文，不再作为本轮验收入口。实际结果、短诊断和标准补丁验证见 [审阅修复报告](../../docs/development/ocr-review-fixes-validation.md)。
