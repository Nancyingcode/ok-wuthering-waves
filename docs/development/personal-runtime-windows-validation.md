# Personal 运行分发 Windows 验收记录

验证日期：2026-11-04（Asia/Hong_Kong）。本记录补充 [P0 初始交付记录](personal-runtime-p0-validation.md)，保留用户授权上传前的验收状态。后续运行仓库已上传及真实正式源测试以 [运行仓库续验记录](personal-runtime-remote-validation.md) 为准；本轮仍未触发远端 CI。

## 来源与环境

- 工作分支：`feat/personal/runtime-distribution`；已提交的 P0 实现为 `9990eda3171b89facfad99426073d5d5728f37b2`。本次 payload 从该 commit 的 Git 对象导出，版本 `v1.0.4` 仅用于本地验证。
- Windows 10 x64，build 19045；Node 24.22.2、pnpm 11.28.10、PowerShell 7.6.5。
- 经用户授权安装 Rust/Cargo 1.99.1、Microsoft C++ Build Tools 18.11.3（安装版本 `18.11.12328.437`），安装返回 0，无需重启。Rust 压缩包对照官方 SHA-256；Microsoft bootstrapper 的签名验证为有效 Microsoft Corporation。
- 初次依赖下载停滞，使用本机已有系统代理重试；没有修改系统代理、关闭 TLS 校验或更换依赖源。准备的便携构建辅助工具只位于忽略的 `build/windows-acceptance-tools/`。
- 仓库开发测试使用 `.venv` Python 3.12.14。独立安装验证使用启动器实际选择的 Python **3.12.10**，路径为 `build/windows-acceptance/python/`；两套环境分开。
- 本次只追加补丁内的 Rust 用例修正和验收记录，仍未提交；原 P0 提交不变。没有修改业务 Python、requirements、锁文件或缓存 schema。

## 测试与实际调用

| 检查 | 结果与边界 |
| --- | --- |
| 五个受影响 Node 测试文件 | **139 通过，0 失败，0 跳过**；启用 `LAUNCHER_INTEGRATION_SOURCE=build/pyappify`，执行真实固定 checkout 的 Prepare 幂等、凭据门禁、业务 SHA 不同及跨 runner 缓存复用测试 |
| 原始 requirements 下的 Python 全量测试 | 72 模块，1040 项：1032 通过，8 项 OCR 补丁测试跳过，无失败；191.373 秒 |
| 既有 CI OCR 补丁环境下的 Python 全量测试 | **72 模块，1040 通过，0 失败，0 跳过**；151.156 秒。两只轮子按项目既有脚本及固定上游 hash 生成，仅安装到开发 `.venv` |
| 独立运行环境安装 | 原样使用导出的 requirements，以及 `--no-deps --index-url https://pypi.org/simple/`；`pip check` 返回 `No broken requirements found`。未把 CI 补丁轮子或内联框架复制到 Personal payload |
| 关键导入及真实提供方 | `ok`、`pyappify`、Qt、QFluentWidgets、OpenVINO、ONNX OCR、MCP 从独立 Python 的 site-packages 提供；个人页面、队伍加载器及 config 从独立 working 提供，没有旧内联框架遮蔽 |
| MCP 入口 | MCP 2.3.0 的 `MCPServer` 构造成功；没有打开监听器或接管游戏 |
| 生成模板 | 使用生产配置中的 template folder 加载 **296** 个特征；不依赖 `ok_templates/` 开发原图 |
| 实际 OCR | 原始运行依赖真实识别固定图像，输出 `OKWW 12345`，置信度约 0.9600；CPU 路径通过，不代表 NPU 硬件已验证 |
| 声骸模型 | 完整 37,907,104 字节模型在 OpenVINO CPU 编译为 640×640，完成一次实际推理；空白图像返回 0 项，验证可用性，不代表游戏识别准确率 |
| Windows 主界面 | 独立 Pythonw 已创建真实 Qt 主窗口，桌面可访问性树可读，显示“PC版 已断开”；未启动游戏 |
| 个人页面交互 | **用户人工确认通过**：多账号配置正常，队伍可以创建、保存，创建并选中队伍后可切换队内角色。无队伍时下拉框禁用符合 `_refresh_team_list` / `_set_editor_enabled(False)` 的既有逻辑，并提示先创建或导入队伍；不修改此行为 |
| 最终启动器界面 | 从最终 ZIP 解出的 EXE 经 UAC 实际运行，用户人工确认界面及 Official / Personal 来源选项正常；保持未安装状态，未启动游戏；用户已从托盘退出并核对进程结束 |

独立环境版本：ok-script 2.0.22、pyappify 1.0.13、PySide6 6.11.1、onnxocr-ppocrv5 0.0.22、OpenVINO 2026.3.0、MCP 2.3.0。GUI 验证入口仅在隔离副本中关闭全局 mutex、游戏自动启动和 OCR 预加载，并设置独立窗口标题；原始导出 payload 没有改动。手动运行没有启动器版本上下文，因此内置更新页的 `pyappify_version: None` 不构成真实启动器更新验收。

证据：`build/windows-acceptance-node-final.log`、`build/python-validation/windows-acceptance.json`、`build/python-validation/windows-acceptance-ci.json`、`build/windows-acceptance-pip-check.log`，以及 `build/windows-acceptance/{imports,templates,ocr,echo}.json`。GUI 日志为同目录下 `gui-desktop.stdout.log`、`gui-desktop.stderr.log`；最终启动器的运行和人工确认见 `launcher-gui-process.json`、`launcher-gui-confirmation.json`。桌面工具曾报截图/点击超时，续验时原生窗口清单在两次重试及重置后仍超时；上述人工确认与自动化证据分别记录。

## 启动器构建与产物

前端 TypeScript / Vite 及首次 Rust release EXE 构建通过，release 构建 712.948 秒。随后真实 `launcher_profiles` 运行发现 9 通过、1 失败：独立来源同名 tag 用例使用默认 Auto 标签策略，与生产 `create_fetch_options` 的 All 策略不同，仍取得旧 tag。

以相同 git2 0.21.0 / libgit2 库创建三个独立本地克隆实测：默认策略保留旧 tag，All 与 None 配合显式 refspec 均正确取到新来源 commit。证据为 `build/windows-acceptance/git-tag-probe.log`。最小修正只修改补丁内测试：复用生产 `create_fetch_options`，先断言实际 tag commit，再用实际 commit 设置 HEAD；没有修改生产标签策略，也没有通过延时或放宽断言掩盖失败。

据此重新执行 Prepare / Build / Test，最终 release 构建 **245.524 秒**，真实 Rust **10 通过、0 失败、0 忽略**（33 项不属于此次 profiles 过滤范围），编译及运行 73.205 秒。同名 tag 往返、真实 Python 模块提供方、配置保留、同步失败恢复、安装就绪及长路径用例均执行。

首次 Bundle 下载 NSIS 遇到 `Peer disconnected`。随后从该 CLI 固定的官方 Release 预取 NSIS 3.11 及 nsis_tauri_utils 0.5.3，按其源码规定的 SHA-1 验证，再填入新建工具缓存；校验记录为 `build/windows-acceptance-tools/nsis-verification.json`。单独重试 Bundle **7.119 秒通过**，便携 ZIP 收集 **1.456 秒通过**。没有重跑已通过的 Build/Test，也没有伪造阶段凭据。

最终日志为 `build/windows-acceptance-launcher-final.log`、`build/windows-acceptance-launcher-bundle-final.log`；阶段时间、配方指纹和工具信息为 `build/launcher-metadata/`。重复 Bundle 时 CLI 出现 `__TAURI_BUNDLE_TYPE` 标记未找到的警告；本次最终 EXE、GUI、安装/覆盖/卸载均已验证，未验证 Tauri updater 插件的包自更新。本启动器业务更新仍走既有 Git 路径。

| 最终产物 | 字节数 | SHA-256 |
| --- | ---: | --- |
| `dist/ok-ww-personal/ok-ww-personal.exe` | 10,360,320 | `0e58246db40e40cd0948ad11674f01b147c44e139cc535cff9af03df7a268c47` |
| `dist/ok-ww-personal-win32-setup.exe` | 4,014,192 | `0cf936177628a621bf490d91ec259a061051af663ea8fedd037c2bc4339ebc95` |
| `dist/ok-ww-personal-win32.zip` | 5,102,211 | `7582f46cb4513865e28425a46bf49b35d15ab21c724714a0f8e24d93932d5a1c` |

ZIP 的 EXE、YAML、icon.png 与输出目录逐字节一致；真实 x86-64 EXE 包含新 Personal URL 及 Official URL，不含旧 Personal 开发 Git URL。完整记录为 `build/windows-acceptance/portable-artifact.json`。schema 2 缓存校验通过，当前配方指纹为 `edb5b1a181473b71614475635f66eb1258b8f5be487dfffdf918e3281c4303cb`，业务 SHA 与兼容启动器来源不同及跨 runner 复用规则保持不变。

真实产物与当前 URL/补丁 hash、Build/Test/Bundle 凭据、运行 payload 校验均通过；共用运行发布器的本地 dry-run 返回 `externalWrites=false`。证据为 `build/windows-acceptance/validation-summary.json`、`runtime-dry-run.json`。本地 metadata 没有可信 Actions run ID，不能作为正式发布的同 run 证明，CI 发布门禁仍需后续获准 push 后执行。

## Windows 安装、覆盖与失败状态

| 项目 | 实际结果 |
| --- | --- |
| NSIS 首次安装 | 在预先确认无同名产品注册项后，以 `/S /NS /D=<独立目录>` 安装；退出码 0，安装路径、版本 1.2.3、卸载程序存在，安装 EXE hash 与最终 ZIP 一致 |
| NSIS 支持的覆盖模式 | `/S /NS /UPDATE /D=<同一独立目录>` 退出码 0；配置、队伍、用户脚本、缓存四类合成哨兵逐项 hash 不变，EXE 仍与最终包一致 |
| NSIS 卸载 | 用户退出最终启动器后，独立安装的静默卸载退出码 0；主程序及卸载注册项移除，四类哨兵保留；清理的安装设置键是本次新建且值指向验收目录的键 |
| 裸 `/S` 同版本重装 | **未通过**：90 秒内没有完成，不能记为成功。固定模板的同版本维护/卸载分支可能等待交互，桌面工具故障使当时提示无法核对。保留失败记录，未为此扩大修改固定的安装器模板；不要将该结果与已通过的 `/UPDATE` 覆盖混用 |

证据为 `build/windows-acceptance/nsis-install.json`、`nsis-update.json`、`nsis-uninstall.json`、`nsis-reinstall.json`。这证明独立 NSIS 安装与合成数据保留，不代表真实旧用户的 Git 业务迁移或依赖缓存更新已验收。

首轮 EXE 的 UAC 曾被取消，记录为 `launcher-empty-source-result.json`，不计为执行。续验改用最终 ZIP 的 EXE 后，用户允许 UAC，Personal setup 实际运行，退出码 1；配置保持 `Personal`、`installed=false`、`auto_start=false`、版本列表为空，未创建可启动的混合安装。正式版本查询接口再次返回 `Git clone failed`，见 `launcher-final-cli.json`、`launcher-version-response.json` 及 `launcher-final-portable/logs/`。

随后相同 git2 版本、fetch/代理配置的独立原生探针成功克隆该仓库，确认 `empty=Ok(true)`、默认分支 HEAD 不存在，见 `git-clone-probe.log`。因此只将上述 EXE 结果计为**失败后未就绪状态通过**；不把较早的 clone 错误解释成已验证的“缺 tag”提示，也不把原生探针当作最终 EXE 冷安装成功。正式源可安装性仍待真实 tag。

## 文件体积与测量口径

| 指标 | 实测值 |
| --- | --- |
| 源 commit tracked blob 总字节数 | 1,056,761,518 |
| 运行 payload，不含 manifest | **42,049,821 字节，425 文件**，小于 60 MiB 门禁 |
| payload SHA-256 | `5495007524cdcce3f53058bc08a8d4fbabc8c2163135a43df87146f8a27c9d16` |
| 当前 commit 对应 manifest SHA-256 | `f0af9db0d196804bf831887f11d131b9c5100aa1ddf61f1166d1cc9a6a03295f` |
| 当前 payload 本地预览 ZIP | 35,514,016 字节，426 个成员逐项验证；SHA-256 `57fb307ad246e7b9328aada1fc00eb424f41e74214b075e0f1d7145276ef6693`。它不是启动器 ZIP |
| 实际 Python 下载包 | 32,399,361 字节；SHA-256 `8649692de846c56a7189d6dae5c322ab20deb1b5908b6f39426b62a36f39415d` |
| 独立 Python 与依赖文件占用 | 1,336,133,052 字节，15,658 文件 |
| 执行探针及 GUI 后的 working 占用 | 65,046,642 字节，含 21,642,612 字节生成缓存、Python bytecode、配置和日志；这是当时采样的逻辑文件字节数 |
| 正式源 Git 首次传输、冷 wheel 下载、冷安装总网络量、纯代码更新网络量 | **未实测**。本次 pip 使用已有缓存，不能将 wheel 解析大小或本地 Git fixture 当成冷网络下载量 |

导出证据位于 `build/windows-acceptance-export/payload/`、`build/windows-acceptance-export-summary.json`；当前预览包为 `build/windows-acceptance-personal-runtime-v1.0.4.zip`，逐项校验记录为 `build/windows-acceptance/runtime-archive.json`。占用采样为 `build/windows-acceptance/footprint.json`。安装副本会产生缓存，不能再把它当作不可变 staging 送入发布器。

## 实际发布状态与未完成验收

用户已创建公开运行仓库 `Nancyingcode/ok-ww-personal-update`。此前 refs API 返回 409；续验连接器仍返回 `size=0`、public、默认分支 main，原生 Git 探针也确认空仓库。没有正式运行 tag，无法从该地址完成真实首次安装及往返切源。

用户截图已确认 Repository variables：`PERSONAL_RUNTIME_REPOSITORY=Nancyingcode/ok-ww-personal-update`、`PERSONAL_RUNTIME_BRANCH=main`，以及 Repository secret `PERSONAL_RUNTIME_TOKEN` 的存在。没有读取 secret 内容，也未通过写入测试验证其目标 Contents 权限。`PERSONAL_RUNTIME_MIGRATION_APPROVAL` 仍需真实正式源验收后填写。

以下项目仍不能记为全部通过：

1. 从正式运行 tag 通过最终启动器完成全新安装，以及绑定该 tag、实际 EXE/ZIP hash 的首次迁移验收。
2. 最终启动器中的 Official → Personal → Official 同名版本往返、回退、真实 pip 失败与进程运行中保护。Rust 本地 Git 用例只能证明其覆盖的逻辑。
3. 裸 `/S` 同版本重装的自动化路径；个人页面、多账号、队伍编辑/保存、角色切换及最终启动器来源选项已由用户人工确认，通过方式与自动化测试区分。
4. 真实旧用户安装迁移时配置、队伍、用户脚本的前后核对，以及纯业务更新的解释器、pip 和资源缓存复用。
5. 固定版本及缓存条件下的正式源 Git、wheel、总网络量和纯代码更新量测量；未做游戏/NPU 硬件验收。

上述正式源步骤依赖后续明确授权的运行 tag 发布；本次遵守“不 push 或发布”，不为验收创建公开版本、不填造批准记录。已有代码实现和本地成功测试不等于完成部署。
