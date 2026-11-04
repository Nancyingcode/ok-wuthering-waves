# 官方版与个人版启动器 / Official and Personal Launcher

`launcher/pyappify.personal.yml` 提供同一个应用的两个更新源：

| 选项 | 代码来源 |
| --- | --- |
| `Official` | 原项目的中国区官方更新仓库 `ok-oldking/ok-ww-update2` |
| `Personal` | 独立运行仓库 `Nancyingcode/ok-ww-personal-update` 的版本标签（部署前须完成文末门禁） |

启动器共享一个应用目录，每次运行当前选中的版本。默认关闭自动启动，采用手动更新，方便先选择版本再运行。两个选项都编译在启动器自身，与工作目录中的 `pyappify.yml` 无关，因此安装官方版本后下载的官方 `pyappify.yml` 也不会覆盖这两个选项。

The launcher manages one application with Official and Personal profiles baked into the executable. Auto Start is off and updates are manual by default.

## 构建 / Build

需要 Windows x64、PowerShell 7、Git、Node.js、pnpm、Rust 和 Visual Studio C++ 构建工具。CI 使用 Node.js 24、pnpm 11、Rust **1.99.1**；成品指纹记录实际 patch 版本及 Runner 镜像版本。脚本使用 PyAppify 自带的 pnpm 锁文件，不修改本项目的 Python 依赖。

```powershell
.\scripts\build_personal_launcher.ps1
```

脚本固定 PyAppify `v1.2.3`（提交 `52bc6d9`），按需克隆到 `build/pyappify/`，应用 `launcher/pyappify-profiles.patch`，再构建便携启动器和 NSIS 安装包。国内网络无法使用 GitHub SSH 时，可以传入 HTTPS 克隆地址：

```powershell
.\scripts\build_personal_launcher.ps1 -LauncherRepositoryUrl 'https://github.com/ok-oldking/pyappify.git'
```

构建过程中的检查：

1. `git apply --check`：补丁可以干净应用，或此前已经应用过。
2. `pnpm build` 后执行 `pnpm tauri build --no-bundle --config <准备生成的覆盖文件>`：分别计时 TypeScript/Vite 和 Rust 构建。覆盖文件只关闭重复的 beforeBuildCommand，并进入输入指纹；前端只构建一次。Build 完成后再 Test，保证干净检出的 Rust 测试能找到嵌入资源。已通过固定上游 Tauri CLI 的 `build --help` 核对 --config 支持。
3. `cargo test --release --lib launcher_profiles`：双源配置、`configs` 保护等补丁测试；测试失败时不会继续打包安装程序。
4. `pnpm tauri bundle`：复用上一步的二进制生成 NSIS 安装包。

安装包使用 PyAppify 的在线安装模式（`bundle.resources` 为空），安装体积只包含启动器本身，所选来源的代码与依赖在首次安装时下载。

### 产物 / Outputs

| 路径 | 说明 |
| --- | --- |
| `dist/ok-ww-personal-win32-setup.exe` | 双版本启动器安装包，安装后即可选择 `Official` / `Personal` |
| `dist/ok-ww-personal-win32.zip` | 便携版压缩包 |
| `dist/ok-ww-personal/ok-ww-personal.exe` | 便携版可执行文件及 `pyappify.yml`、`icons/` |

只准备源码和配置、暂不安装构建依赖时，可以运行：

```powershell
.\scripts\build_personal_launcher.ps1 -PrepareOnly
```

默认 `-Phase All` 保留一次执行完整构建的行为，也可以逐阶段执行：

```powershell
.\scripts\build_personal_launcher.ps1 -Phase Prepare
.\scripts\build_personal_launcher.ps1 -Phase Build
.\scripts\build_personal_launcher.ps1 -Phase Test
.\scripts\build_personal_launcher.ps1 -Phase Bundle
```

`Build`、`Test`、`Bundle` 只验证准备状态，不隐式重复 Prepare。Prepare 对相同文本、YAML、图标和已应用补丁不重复写入，保持内容与 mtime。Cargo.toml 提前使用 Tauri CLI 的 LF 格式，避免本地 CRLF 检出在构建时被 CLI 改写。它也提前同步独立 PyAppify 检出的 Cargo.lock 中的应用名称，避免首次 Cargo 构建改写锁文件、使准备快照和依赖缓存 key 失效；本项目的依赖锁文件不受影响。

准备记录覆盖固定上游提交、本地构建输入以及准备后的源码内容；输入改变后必须重新 Prepare。Test 要求 Build 成功，Bundle 要求 Build 和 Test 成功；失败重试先清除后续成功记录。外部命令非零退出会停止当前阶段。`-PrepareOnly` 等价于 `-Phase Prepare`，与显式的其他 Phase（包括 All）同时使用会报错。

## 持续集成 / CI

`.github/workflows/build.yml` 保持原来的 `v*` tag push / workflow_dispatch 触发范围：

- `validate` 每次验证当前业务提交，`synchronize` 仍依赖 validate。fork 的 `package_personal` 与 validate 并行；原 `package` 仅在上游执行，并继续依赖 synchronize。
- `publish` 等待以上四个 job，要求 validate、synchronize 和对应仓库的 package 路径成功；互斥路径 skipped 不阻断发布。失败、取消、手动构建或普通分支 push 均不能发布。
- 打包 job 的 checkout 仍使用当前 tag / 提交及完整历史，PyAppify 则固定提交并通过 HTTPS 克隆。缓存中的旧 launcher 来源 SHA 只用于追溯，不替换本次业务 tag / SHA。
- personal job 显式设置 `SIGN_BUILD=false`。上游的同步、SignPath 签名、官方 pyappify-action 和分发步骤保持原行为。
- 只有实际 package 路径上传一个 `release-assets`。个人路径只 stage 安装包与 ZIP，保持现有补发入口的文件白名单兼容；完整成品命中无需独立便携 EXE，完整本地构建仍保留所有 dist 输出。

### Python 全量验证 / Python Validation

`validate` 在两个独立 Windows runner 上分片。每个 runner 仍通过原 `run_test.py` 为每个模块单独启动 Python，保留 Qt / OCR / SDK 进程隔离、AVX2 兼容设置、真实 OCR 推理和原有测试内容。分片不会让测试并发写同一工作目录的配置与日志。

`scripts/run_python_tests.py` 按文件名稳定排序后，用零基 `strategy.job-index` / `strategy.job-total` 分配全部 `tests/*.py`，覆盖原来的 `Test*.py`、`test_*.py` 及其他根目录 Python 文件。当前矩阵只有分片维度，Python 版本统一为 3.12。无模块、空分片、非法参数、子进程失败或缺少有效 unittest 结果均使验证失败；失败模块之后的其他模块继续执行，两个分片也不会互相取消。

每个分片上传独立的 `python-validation-<index>`，记录完整文件列表及 SHA-256、所选模块、每模块测试数、跳过数、退出码和耗时，可核对分片并集完整且不重复。`synchronize` / `publish` 仍等待整个 validate 矩阵成功，没有 continue-on-error 或缓存测试结果。验证只浅取当前提交及全部当前文件/LFS 内容；上游同步与打包 job 继续保留完整历史。依赖与本项目 dev extras 合并为一次 pip 安装，仍检查本项目能否正常构建安装。

本地全量执行与分片示例：

```powershell
.\run_tests.ps1
.\run_tests.ps1 -ShardIndex 0 -ShardCount 2
.\run_tests.ps1 -ShardIndex 1 -ShardCount 2
```

手动测量 Python 验证时，在 **Windows Release → Run workflow** 勾选 `validate_only=true`。该模式运行全部 Python 分片及构建/发布工具回归，保留 synchronize 检查，跳过两条 package 路径；不生成 release-assets，也不适合作为 Publish Existing Build 的来源。默认 false 的普通手动构建保持完整流程。tag push 始终运行正常打包路径，忽略该手动开关；发布门禁仍要求所有验证及对应 package 成功，手动运行始终不发布。

实测基线为 [运行 #6](https://github.com/Nancyingcode/ok-wuthering-waves/actions/runs/41377826101)，优化后为 [运行 #7](https://github.com/Nancyingcode/ok-wuthering-waves/actions/runs/41380592629)（代码提交 `32489fb`，手动 `validate_only=true`）：

| 阶段 | 基线 | 优化后 |
| --- | ---: | ---: |
| 全部 validate 等待时间 | 229s | 182s，减少 47s / 20.5% |
| 单个 validate job | 229s | 两个分片分别 182s、161s |
| checkout | 52s | 35s、34s |
| 依赖安装 | 56s | 69s、52s |
| Run tests 步骤 | 95s | 54s、53s |
| Python 测试 | 62 个模块 / 936 项 | 63 个模块 / 946 项，零跳过 |

两个报告的模块并集完整且不重复；逐项核对基线 62 个测试文件的 SHA-256 和测试数均未变化，保留原有 936 项，并新增 10 项分片、进程隔离与失败传播回归。两个分片分别执行 32 / 31 个模块、528 / 418 项测试，均成功；发布工具回归同时通过。手动验证工作流总计 201s，package / publish 均 skipped，没有生成 release-assets、版本标签或 Release。

该样本使用 Python 3.12.10，两个 Windows runner 镜像分别为 `20261021.278.1`、`20261030.294.1`；硬件分配、依赖缓存和镜像滚动会影响时间，不能承诺每次固定缩短。两个 validate job 的时长合计 343s，高于基线 229s；优化目标是缩短等待时间，runner 总用量没有等比例下降。手动验证模式跳过打包，其 201s 整体时长不与旧版完整构建的整体时长比较。

### 构建耗时与缓存 / Build Time and Caches

先 Prepare，再恢复任何 Rust 缓存，避免准备阶段清理不完整的源码目录时删除 target。完整成品使用 `actions/cache/restore` / `save`，只接受精确 key，没有 restore-keys。未命中时继续使用 pnpm 下载缓存及 Rust 编译依赖缓存；Rust `shared-key: personal-launcher` 代替隐含的 job key，仍保留工具链、Cargo 清单和锁文件 hash，因此迁移后的第一次可能 miss。该语义见 [rust-cache 的 action 输入](https://github.com/Swatinem/rust-cache/blob/v2/action.yml)。

完整 key 格式为 `personal-launcher-win-x64-v2-<sha256>`，由 `scripts/personal_launcher_cache.cjs` 生成，输入包括：

- 固定 PyAppify commit、准备后的源码及锁文件内容。
- profiles 补丁、personal YAML、两个图标、构建脚本及实际参与的辅助脚本；继续保守纳入 build.yml，包含其中声明的 Rust、Node、pnpm 工具选择条件。
- release profile、默认 features、Windows x64 MSVC target，以及影响编译/打包的环境覆盖值摘要。
- 构建平台与架构（Windows x64）。实际 Node、pnpm、rustc、Cargo、PowerShell、MSVC、OS 小版本及 Runner ImageOS / ImageVersion 只记录来源，不再单独使已测试成品失效。

输入按稳定的相对路径排序、对内容计算 SHA-256，必要文件缺失直接失败。不使用 mtime、当前业务提交/tag/分支、普通 Python 源码、requirements 或业务测试作为失效项。`force_launcher_rebuild` 也不改变指纹。不支持改变输出位置的 `CARGO_TARGET_DIR` / `CARGO_BUILD_TARGET` 或外部 `TAURI_CONFIG`；本地使用这些覆盖时需先清除后再进入缓存流程。

缓存目录 `build/launcher-cache/` 仅包含安装包、ZIP、manifest.json。manifest 记录 schema、完整指纹与输入、上游 SHA、原 launcher 构建仓库/提交/run URL、原构建的 `buildTools`、launcher_profiles 通过记录，以及两个文件的字节数和 SHA-256。测试记录同时绑定成品指纹和原工具环境摘要，工具来源缺失、被改动或与测试收据不符均拒绝复用。校验通过后才复用，跳过 pnpm 依赖恢复/install、Rust 缓存恢复/build、launcher_profiles 和 bundle；当前业务 validate 照常执行。缓存校验失败会给出原因、清理本任务独立暂存目录并重建。仅本次 Build、Test、Bundle 全成功才生成新 manifest、保存成品缓存；业务测试失败仍禁止发布。

成品复用与一次构建的阶段收据使用不同的判定：成品要求相同源码、构建配方、目标和环境覆盖，允许使用另一 Runner 已通过测试的二进制；本次新构建的 Build → Test → Bundle 收据仍绑定全部实际工具版本，同一次构建中换工具必须重新执行前面的阶段。`fingerprint.json` / report 的 `tools` 记录当前 Runner，manifest 的 `buildTools` / report 的 `launcherBuildTools` 保留实际产物来源，不会在命中时改写为当前 Runner。

v2 与旧 v1 缓存隔离，首次需要完整构建。回归覆盖实际出现过的 `20261021.278.1` → `20261030.294.1` 工具版本变化，并在真实固定版本源码的集成用例中切换 ImageVersion，验证 key 不变、原测试与哈希仍可核对，同时旧构建阶段收据被拒绝。

v2 已在独立分支 `feat/personal/launcher-cache-policy` 的代码提交 `aaf0e10` 上完成两次完整手动 CI：

| 样本 | 整体耗时 | personal package | 全部 Python 验证 | 成品缓存 |
| --- | ---: | ---: | ---: | --- |
| [首次构建 / 41384839758](https://github.com/Nancyingcode/ok-wuthering-waves/actions/runs/41384839758) | 8m55s | 521s | 165s | v2 miss；Rust / pnpm hit；6 项真实 launcher_profiles 通过后保存 |
| [精确命中 / 41385591910](https://github.com/Nancyingcode/ok-wuthering-waves/actions/runs/41385591910) | 3m11s | 108s | 172s | manifest 与两项产物校验通过；跳过依赖恢复、编译、profiles 测试编译、打包及保存 |

命中样本比首次建立缓存的整体等待时间减少 344s（64.3%），personal package 减少 413s（79.3%）；这反映完整构建与成品命中路径的差别，首次格式迁移仍需要构建。两次都使用 `20261030.294.1` 镜像，因此实际 CI 证明精确恢复及成品一致性；跨镜像稳定性由使用真实版本记录的回归和切换 ImageVersion 的真实源码集成用例证明，不能将这两次 CI 写成实际跨镜像命中。

两次均执行全部 63 个 Python 模块 / 952 项测试，零跳过；逐项核对发布 `v1.0.3` 的测试文件内容与测试数均未变化，分片完整且不重复。构建/发布工具回归为 108 项（107 通过，唯一的真实源码集成用例在通用 validate 中按原规则跳过，并在 package 的 40 项专门检查中执行通过）。发布工作流、条件和失败门禁未修改，两次 Publish 均 skipped，没有创建标签或 Release。

两次 key 均为 `personal-launcher-win-x64-v2-f307bc69599daeba6a996c00483bed3be2b157f20c3e2ca06dcc08beebe98f00`；命中时保留首次运行的来源、工具记录和测试收据，独立下载的原始产物字节一致：

| 文件 | 字节数 | SHA-256 |
| --- | ---: | --- |
| `ok-ww-personal-win32-setup.exe` | 4,018,375 | `fce7678864a7719f6699ca3a63a076ad06fec6bc1aced6e263c05c50d611509f` |
| `ok-ww-personal-win32.zip` | 5,109,950 | `59027018ba205882e35eeaa87fa4607b84e45c3ca7c78b2277a87a35af493683` |

EXE/ZIP 上传继续使用 `compression-level: 0`。输入、manifest 和计时信息单独上传为 `launcher-build-metadata`，不会进入 release-assets。`report_personal` 在验证/构建结束（包括缓存保存 post-action）后采集各 job / action 的实际时间，单独上传 `launcher-run-timings`；GitHub run 页面提供完整工作流墙钟时间。脚本分别记录 Prepare、install、TypeScript/Vite、Rust 正式构建、测试编译/执行、NSIS、ZIP 等阶段耗时；Rust 阶段包含编译和链接，不能把日志空白视作链接耗时。

### 手动构建与回退 / Manual Builds and Fallback

1. Actions → **Windows Release** → **Run workflow**，选择相应分支，默认 `force_launcher_rebuild=false`，尝试复用精确成品。
2. 需要热依赖重建时勾选 `force_launcher_rebuild=true`：跳过成品恢复，保留 Rust/pnpm 缓存，不删除远端缓存或覆盖同 key。
3. 合入默认分支 `personal` 后，选择 **personal** 手动运行一次建立共享缓存，再运行一次观察成品命中。开发分支缓存不等于默认分支预热。手动构建不创建 tag 或 Release。
4. 成功运行的 release-assets 可按下节 **Publish Existing Build** 补发，版本标签始终绑定所选业务构建 run 的 head_sha。
5. 缓存损坏时先用强制重建绕过；远端 key 不可覆盖，维护时递增辅助脚本的 `SCHEMA`（同时使准备状态失效），在 personal 上重新预热。不自动删除远端缓存、不添加随机 key。需要将编译器或打包工具的安全修复永久纳入成品时，更新构建配方中的版本选择条件或递增 `SCHEMA`；单次 force 不会替换已有的同 key 成品。

GitHub 缓存按 ref 隔离；不同 tag 之间不能直接互读，但可以读取默认分支的精确缓存，见 [GitHub 缓存访问限制](https://docs.github.com/en/actions/reference/workflows-and-actions/dependency-caching#restrictions-for-accessing-a-cache)。跨 tag 行为需要后续正常发布运行观察，本轮不为性能验证创建版本。v2 允许默认分支预热的同配方成品跨 Runner 常规更新复用，保留精确 key、原测试收据和文件校验，不使用模糊匹配。

本地完整构建始终可直接运行 All，不依赖远端成品缓存。要检查成品缓存流程，可以在相同环境依次执行（先完成 Prepare / All）：

```powershell
node scripts/personal_launcher_cache.cjs fingerprint
.\scripts\build_personal_launcher.ps1 -Phase All
node scripts/personal_launcher_cache.cjs manifest
$env:LAUNCHER_CACHE_HIT = 'true'
node scripts/personal_launcher_cache.cjs verify
node scripts/personal_launcher_cache.cjs stage
Remove-Item Env:\LAUNCHER_CACHE_HIT
```

本地验证必须保持环境一致：CI 的 `CARGO_INCREMENTAL=0` 在指纹计算前固定，避免 Rust 缓存 action 随后设置该值造成输入漂移。

### 验证与性能记录 / Validation and Measurements

```powershell
node --test tests/test_personal_launcher_cache.cjs tests/test_launcher_workflow.cjs tests/test_publish_existing_build.cjs
# 准备真实固定版本源码后启用幂等性、跨业务 SHA 复用、损坏缓存与 stage 集成用例：
$env:LAUNCHER_INTEGRATION_SOURCE = (Resolve-Path build/pyappify).Path
node --test tests/test_personal_launcher_cache.cjs
Remove-Item Env:\LAUNCHER_INTEGRATION_SOURCE
actionlint -shellcheck= -pyflakes= .github/workflows/build.yml .github/workflows/publish-existing-build.yml
```

CI 保留 Python 全量验证及现有 6 项 launcher_profiles 测试；只有已经通过相同输入测试的成品才可跳过后者。本地测试覆盖输入变化/排序/缺失、manifest 与内容损坏、失败传播、实际准备幂等性、stage 白名单及发布条件矩阵。

| 样本 | 来源与条件 | 可用数据 |
| --- | --- | --- |
| 历史冷构建 | [41366113840](https://github.com/Nancyingcode/ok-wuthering-waves/actions/runs/41366113840)，业务 SHA `01e1146`，Windows 镜像 `windows-2025-vs2026 / 20261021.278.1`，rustc 1.99.1、pnpm 11.28.10，pnpm/Rust miss | 墙钟 20m18s；validate 3m29s；package 16m38s；Rust release 10m15s；测试编译 3m56s、执行 0.12s；约 9.13 MB 成品 |
| 本轮本地完整构建 | 基线 `d98e2e3` + 本地未提交 diff；Windows 10.0.26200 x64、Node 24.17.0、pnpm 11.8.0、rustc/Cargo 1.99.1、PowerShell 7.6.5、VS Build Tools 18.7.11911.148；已预热本地 Rust target，测试目标首次编译 | All 从 Prepare 到 Bundle 完成约 **301.8s**；install 0.68s、前端 4.80s、Rust 164.70s、测试编译与执行 116.39s（6 项通过）、NSIS 5.92s、ZIP 1.08s。安装包 4,016,627 B，ZIP 5,109,974 B |
| 本轮本地成品校验与 stage | 同一指纹及真实构建文件，无远端恢复/上传 | verify 返回 reuse_launcher=true，校验并 stage 两项文件共 **3.23s**；ZIP 内三项文件与便携目录逐项 hash 一致；该时间不包含指纹生成、GitHub cache restore/upload 或业务验证，不能视为 CI 墙钟 |
| CI 完整构建 | [运行 #4 / 41375483752](https://github.com/Nancyingcode/ok-wuthering-waves/actions/runs/41375483752)，提交 `8bf2ebb`；成品 miss、Rust 依赖 miss、pnpm hit；镜像 `20261021.278.1`，Node 24.22.1、pnpm 11.28.10、Rust/Cargo 1.99.1、PowerShell 7.6.7、MSVC 18.11.12315.225 | 整体 **18m21s**；validate **3m36s**，personal package **18m06s**，两者并行；Rust 616.15s，测试编译与执行 234.36s（6 项通过）；成功保存精确成品缓存，Publish skipped |
| CI 镜像变更诊断 | [运行 #5 / 41377246188](https://github.com/Nancyingcode/ok-wuthering-waves/actions/runs/41377246188)，同一提交；镜像变为 `20261030.294.1`，Node 24.22.2、PowerShell 7.6.9、MSVC 18.11.12328.437 | 准备后的源码输入及环境覆盖摘要完全相同，工具版本变化使指纹正确失效；停止重复编译后检查元数据，未把这次取消的运行计为命中或成功构建样本，也未完成新镜像预热 |
| 新流程依赖热构建 | 在 personal 手动运行，force=true；以报告中实际缓存命中值为准 | 待 CI 测量，不以历史冷构建估算为实测收益 |
| CI 完整成品命中 | [运行 #6 / 41377826101](https://github.com/Nancyingcode/ok-wuthering-waves/actions/runs/41377826101)，同一提交、同一 `20261021.278.1` 镜像与工具链，force=false | 整体 **4m08s**；validate **3m49s**、personal package **1m27s**；精确命中并核验 manifest / 文件，跳过 pnpm 和 Rust 依赖恢复、build、launcher_profiles、bundle 及成品保存；Publish skipped |

每次比较同时查看 metadata 的业务 SHA、工具链、两级缓存状态、原 launcher 来源、文件大小/hash，与 run-timings 的 job/action 时间。热依赖路径中 Rust 的精确命中和部分恢复可能不同，应结合 action 日志判断。整体墙钟、job 时间与内部子阶段有包含关系，不相加。并行化按旧样本推算约移出 3m29s 串行等待；成品命中收益需真实运行验证，不承诺固定耗时。本轮不改 Rust profile，不开展 ThinLTO 实验。

本轮本地 97 项工具回归测试、6 项真实 launcher_profiles、完整 Rust/NSIS/ZIP 构建、manifest 校验与白名单 stage 均通过；本地记录保存在当时工作区的 `build/launcher-metadata/local-validation.json`。本地机器与历史 GitHub Runner 的硬件、缓存及 pnpm patch 版本不同，不能据此计算优化比例。随后合入 personal 的提交 `8bf2ebb` 已通过以上 CI 验证：两次成功运行均执行 62 个模块、936 项 Python 测试（无跳过），成品命中不跳过当前业务验证。普通分支推送和这些手动构建均未创建版本。

运行 #4 和 #6 的 key 均为 `personal-launcher-win-x64-v1-3f2133a8fd6b877a1d6d00a34e8a87a7fb445faa8ea8c10ecb09715428fda1fd`。#6 的 manifest 保留原 launcher 来源 #4，两次 release-assets 独立下载后均恰好包含两项文件，逐项大小和 SHA-256 完全相同：

| 文件 | 字节数 | SHA-256 |
| --- | ---: | --- |
| `ok-ww-personal-win32-setup.exe` | 4,016,566 | `ca8839ae811b944c7e169618de6c4a8f05b99af11485d5d64d3b23b667fe835e` |
| `ok-ww-personal-win32.zip` | 5,109,951 | `d6494715753f31521d84e3858af5c226412ab00963e459289c37b212bc8230c4` |

以上 #4–#6 使用旧 v1 策略，#5 的真实失效原因是工具链变化。v2 已将这些实际版本移到来源记录；源码、配方、目标和环境覆盖相同即可复用已测试成品。测试准备步骤中模拟的损坏缓存日志属于回归用例，应与实际 Verify complete launcher cache 步骤区分。默认 `vswhere` 查询会漏掉 Build Tools，工具来源记录使用 `-products * -prerelease -requires ...VC.Tools.x86.x64` 获取实际安装版本。

安装/来源切换交互、强制重建的完整依赖热构建及跨 tag 访问尚未实测；跨 tag 验证留待正常发布观察，不为性能测试创建 tag 或 Release。上述测量中 job 和子阶段存在包含关系，整体时间使用 run 起止时间，不能相加。

### 发布校验值 / Release Checksums

`publish` 在下载最终制品后生成并自检 `SHA256SUMS.txt`，与 EXE/ZIP 一起上传到 Release。清单覆盖本次上传的其他文件，不包含清单自身；使用 GNU `sha256sum` 格式和排序后的文件名。校验发生在所有打包和签名操作之后，避免记录签名前的哈希。

Windows 下载后运行以下命令，将输出与 Release 清单中的同名文件对照；ZIP 可用同样方法核对：

```powershell
Get-FileHash .\ok-ww-personal-win32-setup.exe -Algorithm SHA256
```

Linux 在清单和文件所在目录运行 `sha256sum --check SHA256SUMS.txt`。SHA-256 能核对下载是否与发布清单一致；如果发布文件与清单被同时替换，哈希不能验证发布者身份，需要可信数字签名或构建来源证明提供进一步保障。

The packaging job builds the dual-profile launcher with HTTPS, verifies the artifacts, and stages them for release upload.

### 发布已有构建 / Publish an Existing Build

已经成功的手动构建可以直接发布，无需再次编译。`.github/workflows/publish-existing-build.yml` 提供独立网页入口，合入默认分支后可在 GitHub 上操作：

1. 打开 Actions → **Publish Existing Build** → **Run workflow**。
2. `Use workflow from` 选择 `personal`。
3. `version` 填写新版本号，例如 `v1.0.2`；测试版可填写 `v1.0.3-beta.1`，工作流会自动标记为 prerelease。
4. `run_id` 填写已经成功的 **Windows Release** 运行 ID。例如构建页面 URL 为 `.../actions/runs/41366113840`，则填写 `41366113840`。
5. 点击 **Run workflow**。入口会下载原始 `release-assets`，生成 `SHA256SUMS.txt`，上传安装包、便携 ZIP 与校验清单，再发布 Release。运行摘要和 Release 说明会记录来源运行及源码提交。

新标签绑定到**来源构建的 `head_sha`**，不使用发布入口运行时的最新提交。当前构建 `41366113840` 对应 `01e1146a743a9bc31745befae3d6cb27f50f7fe0`；即使入口在之后的提交上运行，发布这批产物的标签仍指向 `01e1146`。

入口要求来源为本仓库成功完成的 `build.yml` 运行，其提交已经合入 `personal`，且分别存在一个未过期的 `release-assets` 和同 run 的 `launcher-build-metadata`。失败、未完成、PR/Fork、未合入的构建或已过期产物都会被拒绝。产物必须包含非空的 `ok-ww-personal-win32-setup.exe` 和 `ok-ww-personal-win32.zip`，并通过内嵌 URL、迁移补丁输入摘要、schema 2、固定上游、原始工具与测试收据及文件哈希检查。上面的历史构建示例不满足新 URL/补丁要求，不能用于此次迁移。

已有标签只有在指向同一个业务源码提交时才允许复用。两个入口共用“源码草稿及附件 → 运行 commit/tag 及远端字节确认 → Windows 迁移验收 → 公开源码 Release”的顺序。同输入重试会核对草稿身份及已有附件的实际摘要，只补缺失附件；来源或哈希不符时保留现场并拒绝。不要删除匹配草稿或已发布运行 tag 来重试。

入口使用工作流自带的 `GITHUB_TOKEN` 创建源码标签和 Release；运行仓库发布另需 `PERSONAL_RUNTIME_TOKEN`。根据 [GitHub 的工作流触发规则](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow#triggering-a-workflow-from-a-workflow)，源码令牌创建的标签不会触发另一次标签构建，补发入口直接调用同一运行发布器完成同步。入口不安装游戏依赖、不编译、不重新压缩或签名原产物，上传内容保持原始构建字节不变。

Use **Publish Existing Build** with a version and a successful Windows Release run ID to publish the original artifacts and checksums without rebuilding. The tag always points to the source run's commit.

### 签名方案 / Signing Options

当前个人双版本产物未接入签名。可选方案（核对日期：2026-11-02）：

| 方案 | 费用与限制 | 对当前项目的适用性 |
| --- | --- | --- |
| [SignPath Foundation](https://signpath.org/terms) | 符合条件的开源项目可免费申请；需审核、自有项目配置、可验证构建及每次发布的人工批准 | 可优先评估，但 Fork 必须满足其上游与代码维护要求，不能直接复用原作者的组织、项目和凭据 |
| [Microsoft Artifact Signing](https://learn.microsoft.com/en-us/azure/artifact-signing/quickstart) | 付费云签名；公开信任的个人开发者身份目前只面向美国、加拿大，组织身份另有地区限制 | 适合符合身份与地区条件且希望通过 CI 签名的发布者；中国大陆、香港的个人身份目前不在该范围 |
| [商业 IV/OV 证书与云签名](https://www.ssl.com/guide/esigner-pricing-for-code-signing/) | 证书与云签名分别核算；例如 SSL.com IV/OV eSigner 最低档为每月 20 美元、20 次签名，证书另计；需身份审核 | 可评估个人 IV 证书；GitHub 托管 Runner 适合云签名，硬件 Token 通常需要自托管签名设备 |
| 自签名证书或单独签署校验清单 | 无需购买公开信任证书，但验证者需要另行信任证书或公钥 | 可用于内部核对，不能等同于受 Windows 默认信任的 Authenticode 签名 |

接入 Authenticode 时，应先签便携 EXE，再打包 ZIP/NSIS，再签最终安装包并添加可信时间戳，最后生成校验清单。有效签名能识别发布者和文件变更；[微软说明](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation)指出，新签名文件仍可能出现 SmartScreen 提示，EV 证书也不再保证首次下载免提示。

## 使用与发布个人版本 / Select and Publish Versions

1. 打开启动器，选择 `Official` 或 `Personal` 并完成安装。
2. 运行期间先点击「停止」，等待程序完全退出后，再点击「更换配置 / Change Profile」。
3. 选择另一个配置，等待代码及依赖切换成功，再点击「启动」。
4. 同一来源的历史版本通过启动器的版本选择功能安装或回退。

Personal 公开运行下载使用 HTTPS，不要求用户配置开发仓库的 SSH agent。开发仓库的 Git origin 仍使用 SSH。新 URL 需要重建启动器；仅更新 working 目录的 YAML 不会替换旧 EXE 的内嵌 profiles。

Personal 的 Python 依赖使用官方 PyPI，安装及版本更新均保留当前配置的依赖源，避免镜像同步延迟导致已发布的固定版本无法安装。

源码标签绑定**通过验证的业务提交**，运行仓库以同名标签发布白名单快照，manifest 记录业务 SHA；两个仓库的 commit SHA 不同。仅推送 `personal` 不发布版本。首次公开新启动器前必须已有正式运行 tag，并完成真实目标源冷安装。普通代码变更继续复用兼容的缓存启动器；配置或补丁变化使成品指纹自然失效。

Stop the application before changing profiles. Personal downloads use HTTPS and immutable runtime tags. An older executable needs replacement to migrate its embedded source URL.

## 验证 / Verification

在隔离目录中安装并启动，避免影响正在运行的启动器、游戏和用户配置：

```powershell
$targetRoot = Join-Path $env:TEMP 'ok-ww-personal-install'
$process = Start-Process -FilePath 'dist/ok-ww-personal-win32-setup.exe' `
    -ArgumentList '/S', "/D=$targetRoot" -WindowStyle Hidden -Wait -PassThru
if ($process.ExitCode -ne 0) { throw "Installer exited with $($process.ExitCode)." }
Get-ChildItem -LiteralPath $targetRoot -Force
```

启动 `$targetRoot\ok-ww-personal.exe` 后，窗口标题为 `ok-ww - Official / Personal`，配置选项里应同时出现 `Official` 与 `Personal`；切到 `Personal` 安装一次，确认代码来自个人仓库的版本标签而不是官方仓库。验证完成后删除该临时目录即可，便携目录和安装目录都不会覆盖已有安装。

## 启动器兼容修正 / Launcher Compatibility

`launcher/pyappify-profiles.patch` 只应用于 `build/pyappify/` 中的独立启动器源码：

- 保留启动器定义的两个来源，避免下载官方版本后被其 `pyappify.yml` 覆盖。
- 安装或切换时先使用选中的仓库，再检出该仓库的版本标签。
- 运行中禁止切换；切换失败后禁止启动未完成安装的版本；重复启动不会创建第二个应用进程。
- 切换时替换代码并保留 `configs/` 中的用户设置。
- 同步所有 Git 已跟踪文件及其父目录，即使 `.gitignore` 也匹配到它们，避免遗漏已发布的自定义界面模块。
- 在克隆检出文件前启用仓库的 Windows 长路径支持，避免素材下载完成后报 `Filename too long`；错误提示保留底层原因。

待 PyAppify 上游提供这些行为后，可移除对应补丁并重新构建。项目根目录的官方 `pyappify.yml` 和游戏自动化代码保持原有用途。

The patch keeps launcher profiles authoritative, switches the Git source before setup, and prevents switching while running or starting an incomplete installation.

## Personal 运行分发（P0）

运行仓库必须新建为空仓库，不 fork、不导入开发 refs 或 `.git`。只维护配置的一个分支和显式发布的版本标签。发布器拒绝开发历史、未知分支、非运行 manifest、同名 tag 身份冲突，以及 attributes/filter 改写后的字节差异；不 force push。两个发布 job 共用跨版本串行组，非快进竞争另有最多三次重新读取/重试，分支和 tag 使用 atomic push。

`deploy.personal.txt` 递归保留运行源码、Qt/OCR/MCP 所需资源、`assets/templates`、`echo.onnx` 和翻译，不包含原图、测试、文档、工作流、开发队伍包或本地数据。requirements 直接来自业务 commit，原样保留，通过 pip 提供框架；不会调用 OCR 发布的内联准备逻辑，也不会复制 CI site-packages。当前 validate 中原有 OCR 轮子验证继续执行；这不能替代对原始 requirements 的真实目标源安装验证。

导出与运行发布 dry-run（PowerShell，可直接运行；预览版本号不代表授权正式发布）：

```powershell
$businessSha = git rev-parse 'personal^{commit}'
$preview = 'build/personal-runtime-preview-new'
node scripts/export_personal_runtime.cjs --source-sha $businessSha --version v1.0.4 --policy deploy.personal.txt --output $preview
node .github/scripts/publish_personal_runtime.cjs --dry-run "$preview/payload"
node --test tests/test_personal_runtime_export.cjs tests/test_personal_runtime_publish.cjs tests/test_publish_existing_build.cjs tests/test_personal_launcher_cache.cjs tests/test_launcher_workflow.cjs
```

输出目录必须位于项目 build/ 或系统临时目录下且全新，脚本不清空现有目录。`payload/runtime-manifest.json` 不含机器名和时间；业务文件清单/总字节数/整体 hash 均排除 manifest 自身。外部 `runtime-report.json` 记录 manifest hash、按目录/大文件的体积和 60 MiB 门禁。相同 SHA、版本和策略导出结果相同；脏工作区及 release preparation 不改变结果。Git 传输、Python/wheel 下载、安装占用及纯代码更新网络量独立记录，未测字段为 null，不能以约 42 MB 文件快照宣称整套安装网络收益。

运行发布 dry-run 只校验真实 payload，不假装已生成 EXE 或已验证 launcher metadata；共用 Release 流程的完整无写入 dry-run 由 API/bare-Git fixture 回归覆盖。实际提交后与远端 tag 的 tree/blob 校验也由本地 bare remote 回归覆盖。发布前仍须完成真实 Windows 构建/安装门禁。

部署时在源码仓库配置：

| 位置 | 名称 | 值/范围 |
| --- | --- | --- |
| Actions variable | `PERSONAL_RUNTIME_REPOSITORY` | `Nancyingcode/ok-ww-personal-update`，必须与内嵌 URL 一致 |
| Actions variable | `PERSONAL_RUNTIME_BRANCH` | 例如 `main`；运行仓库唯一发布分支 |
| Actions secret | `PERSONAL_RUNTIME_TOKEN` | 仅目标运行仓库 Contents 写权限的凭据；可使用平台签发且仍有效的安装 token，不使用源码 `GITHUB_TOKEN` 假设跨仓库权限 |
| Actions variable | `PERSONAL_RUNTIME_MIGRATION_APPROVAL` | 下述真实冷安装验收 JSON；缺失时运行 tag 可发布，但源码 Release 停在草稿 |

运行仓库需按已批准的访问范围建立，公开客户端 HTTPS 下载必须可读。客户端、manifest、remote URL 和日志中均不保存 token。凭据仅经子进程环境用于 HTTP 授权；临时仓库中的 origin 使用不带凭据的 SSH URL。

首次部署顺序：建立目标空仓库及前三项配置 → 选择已验证业务 SHA 的兼容构建 → 从普通 tag 或 Publish Existing Build 入口建立草稿并发布运行 tag → 下载**该草稿的原始 EXE/ZIP**到独立 Windows 安装目录完成验收 → 配置验收 JSON → 重跑同版本、同构建入口，恢复并公开匹配草稿。缺少任何本地兼容检查时，在外部写入前拒绝；缺少冷安装记录时不公开新启动器。不得为了测试公开正式版本。

验收 JSON 的 `inputs` 从当前启动器 YAML/patch 的 SHA-256 得到，`launcherArtifacts` 原样取同 run 的 `manifest.json.artifacts`（保留两个文件的 name/bytes/sha256），其余身份字段取 `personal-runtime-publication` 回执；`evidenceUrl` 指向保存实际 Windows 验收记录的 GitHub 页面。以下布尔值只有在实际检查通过后才可改为 true，模板本身不能通过门禁：

```json
{
  "runtimeRepository": "Nancyingcode/ok-ww-personal-update",
  "version": "v1.0.4",
  "runtimeCommit": "填写远端运行 commit",
  "businessSourceSha": "填写业务 commit",
  "manifestHash": "填写远端 manifest SHA-256",
  "payloadHash": "填写 payload SHA-256",
  "inputs": {
    "launcher/pyappify-profiles.patch": "填写补丁 SHA-256",
    "launcher/pyappify.personal.yml": "填写 YAML SHA-256"
  },
  "launcherArtifacts": [],
  "checks": {
    "coldInstall": false,
    "pipCheck": false,
    "imports": false,
    "gui": false,
    "templates": false,
    "ocr": false,
    "echo": false,
    "sourceSwitch": false,
    "userData": false
  },
  "evidenceUrl": "https://github.com/填写实际验收记录"
}
```

门禁重新读取验收正式 tag 的远端 manifest 与 commit；不接受 prerelease 作为首次迁移基线。后续业务发布可复用这一验收记录，但 YAML、补丁及实际 EXE/ZIP 必须仍匹配。新启动器二进制或迁移输入变化时重新验收。两个仓库没有整体事务：上传失败不发布运行 tag；运行 push 失败保留草稿；公开失败保留运行 tag 及草稿，直接重试。回执和报告独立上传，不扩大 Release 附件白名单。

迁移和回退：先停止应用，更换带新源的启动器，重新选择 Personal 并安装，核对多账号、常用任务及 `configs/custom_teams`。切源前先持久化未就绪状态；fetch、同步或 pip 失败后完成 setup 才允许启动/更新。同步仅复制当前 index 的受管理文件，使用上一来源 index 引导迁移记录，只删除以前发布过而当前已消失的文件；保留未知用户文件、配置、自定义脚本、日志、Python 与 pip 缓存。新源码不再提供的内联框架文件会移除，repo 中未跟踪的旧框架缓存不复制到 working。

旧 `.git` 中的大对象不会自动回收，本轮不执行清空应用目录或磁盘清理。上线前可普通回退本轮代码；上线后不能移动已发布 tag，应选择已验证旧运行版本或发布新的修复版本。需要恢复旧分发机制时重新构建、验证来源正确的启动器，保留用户数据；失败的混合目录不能标记为可启动。
