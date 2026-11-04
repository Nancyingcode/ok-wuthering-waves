# Personal 运行仓库上传与 Windows 续验

日期：2026-11-04（Asia/Hong_Kong）。本记录承接 [前轮 Windows 验收](personal-runtime-windows-validation.md)。前轮“空仓库、未上传、正式源未验证”的结论是历史状态；本轮用户明确要求先上传运行内容，再收尾验收。

## 实际上传状态

- 已上传公开运行仓库 [Nancyingcode/ok-ww-personal-update](https://github.com/Nancyingcode/ok-ww-personal-update)。`main` 与运行 tag `v1.0.4` 指向 `dc34ceffc28e35a660e90dde6b520ca811ae77f0`，tree 为 `0940e736472e5c4cecdd8a6bd522489940d585be`。
- 共用发布器执行原子 push，并重新核对远端 refs、全部可达历史、tree 和每个 blob。只有一个独立运行提交，没有导入开发历史。另一次独立公共 git2 冷克隆及逐项 blob 验证也通过。
- 业务来源为本地 P0 提交 `9990eda3171b89facfad99426073d5d5728f37b2`；运行文件 425 个、42,049,821 字节。manifest SHA-256 为 `f0af9db0d196804bf831887f11d131b9c5100aa1ddf61f1166d1cc9a6a03295f`，payload SHA-256 为 `5495007524cdcce3f53058bc08a8d4fbabc8c2163135a43df87146f8a27c9d16`。
- 此次是用户授权的本地运行仓库初始化，使用本机 Git Credential Manager 的登录，凭据仅在内存中传递。没有读取 Actions secret 内容，也没有验证 `PERSONAL_RUNTIME_TOKEN` 在 Actions 中的实际写权限。
- **截至上述运行上传和 Windows 验收完成时，没有 push 开发分支或源码 tag，没有创建源码/启动器 GitHub Release，也没有触发 CI。** 回执没有可信 Actions run ID；不能据此填造 CI 通过或首次迁移批准。后续源码提交授权见下节。
- 已公开的运行 `v1.0.4` 固定映射上述业务 SHA。后续将本轮启动器修正提交为新源码 SHA 时，应使用新的正式版本号（例如下一版 `v1.0.5`），不能覆盖或重新解释现有运行 tag。

证据：`build/windows-acceptance/runtime-bootstrap-publication.json`、`runtime-git-transfer.json`、`final-artifact-runtime-binding.json`。

## 验收发现及代码修正

真实 Official / Personal 往返暴露两个问题，均在 `launcher/pyappify-profiles.patch` 中最小修正：

1. Official 当前使用 `ok-pyside-minimal 6.12.0.post2`，Personal 使用完整 PySide6 6.11.1，两种发行包拥有同一组 Qt 文件。原流程仅执行 pip install，返回 Personal 后残留旧 minimal 包元数据，`pip check` 报 Shiboken 版本冲突。新增小型内嵌 Python 辅助脚本：仅检测到两种 Qt 供应方式冲突时卸载已安装的 Qt 组，再按目标原始 requirements 安装；同时清理共享所有权，避免卸载旧包时破坏已被 pip 判为满足的目标包。相同供应方式及纯业务更新不做清理，不修改目标 requirements，不清空 Python/pip 缓存或用户文件。
2. 来源安装成功后，明确写入本次实际 checkout 的 tag，修正 Official 已是 `v3.7.17` 而界面仍记录 Personal `v1.0.4` 的状态。

另修正真实 checkout 的 Node 测试 fixture：固定上游 checkout 中补丁新增的资产文件尚未被 Git 跟踪，复制准备树时需包含这些文件，才能正确验证反向 patch 检查及 Prepare 幂等性。

本轮修正及验收记录随当前工作分支提交；原 P0 提交不变。缓存继续使用 schema 2，业务 SHA 不进入配方指纹。补丁变化使新指纹自然变为 `f0830859d45cee6faed6c8d01f1d78f09b1570598d9d385608fecf04a00db867`。

## 发布署名更正与源码提交

用户随后明确授权“现在就更正，然后将代码提交，push”。通过 GitHub 连接器及现有 Git 登录的 `/user` API 核实账号为 Nancyingcode、数字 ID 为 29879289；共用运行发布器的 author/committer 改为 `Nancyingcode <29879289+Nancyingcode@users.noreply.github.com>`。新增真实临时 Git 发布测试，断言两种身份，并确认继承的四个 Git 作者/提交者环境变量不能覆盖它。

署名更正后重新执行五个受影响的 Node 测试文件：**140 通过，0 失败、0 跳过**，含真实固定 checkout 的 Prepare/缓存集成检查；此前已通过的启动器配方输入逐项 hash 仍一致，无需重建 EXE。记录为 `build/windows-acceptance-node-identity-final.log`。

本次源码目标是 `origin/feat/personal/runtime-distribution`，包含原 P0 实现、Windows 切源修正及验收记录；不合并到 personal，不创建版本 tag 或 Release。已公开的运行 `v1.0.4` 提交和署名保持原样。远程仍保存 SSH 地址；本机 SSH 认证失败时，仅对本次传输使用已登录 GCM 的 HTTPS 认证，不修改远程 URL 或全局身份。

## 已完成检查

| 项目 | 结果 |
| --- | --- |
| Node 受影响五个测试文件 | 139 通过，0 失败、0 跳过；真实固定 checkout 的 Prepare 和跨 runner 缓存回归执行 |
| Rust `launcher_profiles` | 11 通过，0 失败、0 忽略；新增 Qt 冲突/幂等/其他包保留检查 |
| 新版 Build/Test/Bundle | 全部通过；Rust release 构建 306.395 秒，测试编译和运行 102.318 秒，NSIS 7.552 秒，ZIP 2.964 秒 |
| 原 Python 全量回归 | 沿用前轮 72 模块、1040 通过、0 跳过结果；业务 Python 和测试模块未修改 |
| 真实公共源冷安装 | 第一版最终 EXE 在全新短目录 `C:\wwp0-n4` 成功，239.777 秒；独立 Python、客户端 pip 缓存均为空起步，代理服务器缓存不在测量范围 |
| 原始运行依赖 | `pip check`、关键导入、296 模板、实际 OCR 及 echo 模型 CPU 推理通过；保持原样 requirements 和 PyPI Personal 安装参数 |
| 新版真实公共来源往返 | Official → Personal → Official → Personal 四次成功；实际 tag 为 Official `v3.7.17`、Personal `v1.0.4`，不是同名 tag 测试 |
| 模块和 Qt 提供方 | Official 使用 working 中的内联 ok/pyappify、Qt 6.12；Personal 使用 site-packages、Qt 6.11.1。返回 Personal 后 pip check、模板、OCR、echo 均通过 |
| 运行中保护 | 实际 managed Python 的受控等待进程存在时，最终 EXE 拒绝切源；停止该自有进程后允许切源 |
| 用户数据 | 配置、队伍、用户脚本和资源缓存四类合成哨兵均按 hash 保留；共享 Python.exe 的 hash 与修改时间不变 |
| 纯代码更新与回退 | 最终 EXE 通过仓库局部 URL 重定向访问本地 fixture，更新到私有 `v9.9.9` 后回退公共 `v1.0.4`；新增受管理代码随更新出现、随回退删除。Python 全目录及 pip 缓存的文件清单/大小/mtime 不变，资源另做前后完整 SHA-256 比较，配置/脚本/资源缓存不变；测试 URL 重定向及临时客户端 tag 已移除 |
| 最终 EXE 同名 tag | 本地 Official / Personal fixture 都叫 `v1.0.4`，四次往返逐次断言实际 commit：Official 为 `3e94945e08b815be19529f9730ce7fa2815b59e4`、Personal 为公开 `dc34ceff…`；模块、Qt 提供方正确，最终 pip check、OCR/echo 均通过。没有向公开仓库增加测试 tag |
| 最终 EXE 失败状态 | 长目录实际 pip 失败的现场保留为未安装；新版普通更新实际返回“先完成不完整安装的 setup”，没有错误地转为就绪 |
| 新版 NSIS | 当前用户独立首次安装、`/S /NS /UPDATE` 覆盖、静默卸载均退出 0，四类合成用户数据保留，主 EXE 和卸载注册项移除；清理设置键前核对其确由本次安装创建且指向独立目录 |
| 新版最终 EXE 冷安装 | 从最终 ZIP 在全新短目录 `C:\wwp0-n4f` 解出 EXE，首次 Git/Python/pip 缓存均为空；实际公开 tag 安装退出 0，连同全部探针共 **307.563 秒**。`pip check`、导入、296 模板、真实 OCR、echo CPU 推理均通过，最终 Personal `v1.0.4`、关闭自动启动、手动更新 |
| 新版最终 EXE GUI | CUA 直接打开实际 `C:\wwp0-n4\ok-ww-personal.exe`，原生窗口路径及标题已核对。截图两次超时后，用户人工确认界面正常、Personal、已安装 `v1.0.4`、自动启动关闭；没有点击运行 |

前轮已由用户人工确认多账号页面、队伍创建/保存、创建队伍后的角色切换。本轮再次单独确认新版最终启动器 GUI；对应 EXE 和确认方式分别记录，没有将旧版本人工结果代替新版本结果。本轮没有启动游戏或 MCP 监听器。

证据：`build/windows-acceptance-node-qt-final.log`、`build/windows-acceptance-launcher-qt-{build,test,bundle}.log`、`build/launcher-metadata/`、`build/windows-acceptance/remote-short-*.json`、`behavior-qt-RoundTrip.json`、`qt-switch-final-*.json`、`local-update-state.json`、`behavior-same-tag-RoundTrip.json`、`behavior-qt-FailedUpdateGuard.json`、`nsis-qt-final.json`。

验收脚本曾有两处口径/实现错误，失败记录保留：Git 包装函数与命令重名造成递归，进程以调用深度溢出退出，尚未开始应用更新；改为显式 `git.exe` 后重跑。资源第一次比较将启用了 Windows 文本换行转换的 working 文本与原始 Git blob 直接比较，改为安装前后资源完整 hash 比较；公开 Git blob 与 manifest 的原始字节一致性仍由独立 `verifyCommit` 检查，没有放宽发布校验。

## 当前最终产物

| 文件 | 字节数 | SHA-256 |
| --- | ---: | --- |
| `dist/ok-ww-personal/ok-ww-personal.exe` | 10,361,856 | `7c72695877792cb249940cf55225905b5f9eb897b89a78d9c93cce0af9d7d635` |
| `dist/ok-ww-personal-win32.zip` | 5,103,693 | `ff48d0fbb869a94e847a76f7e71aaab46b1ed0e386039daee97632bc6c79e5a3` |
| `dist/ok-ww-personal-win32-setup.exe` | 4,015,474 | `a464a2a22afea98c898c4769c35ab1eeb63bd79a72e0ad380d1ca77857cbd1dd` |

新版成品缓存与配方、工具和 Build/Test/Bundle 回执验证通过。此前 EXE `0e58246d…` 的回执保留为历史；不能拿旧 EXE 的测试替代新版专有路径的验收。

安装器内 EXE 的 SHA-256 为 `6240f0eb3f4806909efc3dc94edef29ec7f343120ed9e1e74bb56304ecfd05a9`；与便携 EXE 逐字节比较，大小完全相同，唯一差别是 Tauri 包类型标记的三个字节 `UNK` → `NSS`。安装/覆盖的验证对便携原始字节只执行这一个明确变换后计算完整 hash，不把其他差异列入豁免；没有修改二进制或打包流程。

## 测量口径与已知限制

- 正式仓库独立 git2 冷克隆：收到 **35,424,466 字节、451 个 Git 对象**。这是 Git 对象传输计数，不含 HTTP/TLS 开销，也不包含 Python 或 wheels。
- 匹配 Personal 原始 requirements 的客户端 wheel 响应正文缓存：57 个、**430,542,465 字节**，无缺项。客户端缓存最初为空；这是实际缓存正文，不是整次安装的线路总流量，未统计索引页、元数据和 TLS 开销。
- 新版最终冷安装后，Python 和依赖逻辑占用 **1,264,345,460 字节、14,326 文件**；working 为 63,832,332 字节、430 文件，已含探针生成的缓存；Git 缓存 35,467,377 字节，pip 缓存 436,994,120 字节。占用不是下载量，也不与前轮手工安装的占用混用。详见 `final-footprint.json`。
- 纯代码更新的独立同版本 git2 本地 fixture 测量收到 1,174 字节、4 个新对象；实际最终 EXE 更新/回退另证实跳过依赖同步、解释器和缓存未变。这是本地 fixture 增量，不宣称为正式公网第二个版本的线路测量。
- 首次在工作树深目录安装时，PySide6 QML 文件路径达到约 284 字符，pip 实际失败；保持 `installed=false`、无可启动版本。相同公开源在短安装目录成功。没有修改系统 LongPathsEnabled；Git 长路径测试通过不代表 pip 可绕过 Windows 路径限制。
- 前轮裸 `/S` 同版本重装 90 秒超时仍是已知未通过项；支持的 `/S /NS /UPDATE` 覆盖路径单独验收，不互相替代。
- 游戏运行、NPU、真实私人配置迁移及精确线路总流量不在已验证结果中。

## 部署缺项与交付边界

源码仓库的 Actions Repository 配置已通过 GitHub API 只读核对：`PERSONAL_RUNTIME_REPOSITORY=Nancyingcode/ok-ww-personal-update`、`PERSONAL_RUNTIME_BRANCH=main`，`PERSONAL_RUNTIME_TOKEN` secret 存在；`PERSONAL_RUNTIME_MIGRATION_APPROVAL` 尚不存在。检查没有读取 secret 值或使用该 secret 做写入试验。

本地已生成 `build/windows-acceptance/migration-approval-draft.json`，绑定实际公开运行 commit、manifest、当前 YAML/patch、最终安装包/ZIP hash 和真实检查结果。它仍缺公开的 GitHub 验收证据 URL；当前产物也没有可信 Actions run。**该文件是草稿，不配置为正式批准，不用它绕过同 run/实际附件门禁。** 后续 CI 产物 hash 变化时应重新核对和验收对应文件。

尚需后续授权及远端执行：将当前功能分支整合到 personal，让发布工具和工作流进入目标源码分支，运行可信 CI 并实测 Actions token 的跨仓库写权限，上传可审查验收证据、补齐对应产物的迁移批准，再执行源码/启动器正式发布。本次功能分支 push 不代表源码 Release 已发布。

Windows 已知边界仍为：裸 `/S` 同版本重装超时；深安装目录受系统路径策略限制，推荐使用已验证的短目录或默认安装目录（默认目录未单独冷安装测量）；游戏/NPU 和真实私人配置迁移未测。配置保留证据来自隔离环境的合成哨兵及此前人工队伍检查。

全部后续汇总为 `build/windows-acceptance/final-summary.json`。测试客户端已恢复公共 Personal commit，局部 URL 重定向及私有客户端 tag 已移除；隔离数据和失败现场保留。新版启动器 GUI 留在桌面供用户查看，自动启动关闭。
