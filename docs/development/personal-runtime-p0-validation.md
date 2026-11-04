# Personal 运行分发 P0 本地交付记录

验证日期：2026-11-04（Asia/Hong_Kong）。本记录区分代码实现、本地证据和实际部署，不能作为 Windows 冷安装验收记录。

## 基线与代码实现

- 工作分支：`feat/personal/runtime-distribution`，从干净的 `personal` 建立；本地提交见该分支 Git 历史，未推送、未合并。
- 已完整读取当前 AGENTS、条件/状态规则、跨工具入口及匹配的 Fork 规则。SSH 只读查询确认远端 `personal` 为 `ad319183df6b3c4460b4a9bfc4d8b142ac2b9913`，与本地基线一致。
- 附件基线 `a4b92106` 之后主要新增 OCR 内存/诊断、OCR 轮子发布验证及鉴尤守队伍代码。保留这些改动；Personal payload 按规格原样读取业务 commit 的 requirements，不使用 CI 内联后的依赖清单。
- 新增 `deploy.personal.txt`、Git 对象导出器及共用运行发布器。禁止路径、特殊 Git 文件类型、LFS pointer、大小写冲突和超预算均失败；提交后的 tree/blob 及远端 tag 重新核对。远端所有 tag 历史也要验证，防止客户端获取隐藏的开发历史。
- 正常 tag 发布解析 tag 的 commit，与构建 SHA 核对；补发读取所选成功 run 的 `head_sha` 和同 run 的两类 artifact，工具 checkout 保持在可信 personal。两入口共用草稿恢复、附件哈希、运行 atomic push、迁移验收和公开逻辑。
- 原缓存仍为 schema 2，继续允许业务 SHA 与缓存启动器 SHA 不同，以及经过验证的跨 runner 复用。迁移 URL/必要补丁变更自然更新配方指纹；未把普通业务 SHA 加入缓存键。
- 在原 launcher patch 内提前持久化未就绪状态，禁止从未完成 setup 进入普通更新。用上一来源 index 建立受管理文件记录，切源时仅同步 tracked 文件、删除旧受管理文件，保留未知用户文件及持久化目录；避免旧内联框架/未跟踪缓存遮蔽 pip 模块。
- 部署、验收 JSON、重试、迁移和回退详见 [启动器说明](personal-launcher.md#personal-运行分发p0)。

## 真实业务树导出产物

`v1.0.4` 只用于本地预览；已读取本地现有 tag，最高为 `v1.0.3`。没有预留或发布这个新版本。

| 项目 | 实际结果 |
| --- | --- |
| 业务 source SHA | `ad319183df6b3c4460b4a9bfc4d8b142ac2b9913` |
| source tracked blob 字节数 | 1,056,667,890 |
| payload 字节数（不含 manifest） | 42,049,821，425 文件 |
| payload 门禁 | 62,914,560 字节，通过 |
| echo.onnx | 保留全部 37,907,104 字节 |
| payload SHA-256 | `5495007524cdcce3f53058bc08a8d4fbabc8c2163135a43df87146f8a27c9d16` |
| manifest SHA-256 | `8170123136a73e12592abdd8f9dbfc5752c41e6414ddb22267417fb85a701d02` |
| policy SHA-256 | `4025ef2f114989b035962930ae7d77d20497e34ac43a6fa39dfae6000f1b90b5` |
| 交付 ZIP 字节数 | 36,728,710 |
| ZIP SHA-256 | `6e32edd562e7292bd1047aebe1920a6495061da3940e185cce9d00f4ff2532d8` |

本地文件：`build/personal-runtime-preview/payload/`、其内 `runtime-manifest.json`、外部 `runtime-report.json`、`build/personal-runtime-preview-v1.0.4.zip`。ZIP 是可检查的运行 payload，不能代替启动器 EXE/ZIP。归档成员集合与全部 payload hash、manifest 原始字节逐项验证通过。

真实 payload 还发布到**本地独立 bare fixture** `build/personal-runtime-preview-remote.git`，运行 commit 为 `5dc931b12a91400363aba28d9aa4b5d98b4bc52e`、tree 为 `ba4467b6cfafa161d8eee90e959e6698592a9e1b`，提交及远端回读校验通过。这不是 GitHub 发布，不能把该 commit 当作已部署版本。

首次 Git 传输、Python/工具下载、冷 wheel 下载、冷安装总网络量、纯代码更新网络量及实际安装占用均**未实测**，报告中保持 null。本地 ZIP 和 Git object storage 不代表真实客户端下载量。

## 本地验证结果

| 检查 | 结果与边界 |
| --- | --- |
| 受影响的五个 Node 测试文件 | 139 项：138 通过，1 跳过，0 失败；跳过的是需完整真实工具链的原 launcher integration 用例 |
| E1–E6、发布来源/旧 EXE 拦截、幂等/冲突、阶段故障及并发 | 本地 Git/API fixtures 通过；包括旧 URL 但自身指纹/测试凭据一致的缓存仍被拒绝 |
| 首次迁移门禁 | 正向 fixture 验证可恢复；缺失验收、验收与远端 tag/实际 launcher 不匹配时拒绝公开 |
| 无写入 dry-run | 真实业务 payload 校验通过，无外部写入；完整 Release dry-run 由 fixture 覆盖。真实 EXE/metadata 尚不可用，不能记作真实启动器兼容验证 |
| actionlint 1.7.12 | 两个修改的工作流通过；工具从官方 Release 下载并核对官方 SHA-256，未加入生产依赖 |
| 补丁及 Prepare | 固定 PyAppify `52bc6d986aae93615673960256549f51f13c92b6`；补丁应用/反向检查通过，重复 Prepare 内容及 mtime 一致 |
| 独立 Python 回归 | 项目新建 `.venv`，Python 3.12.14；`TestOcrRelease`、`TestRequirementsClosure`、`TestValidationRunner` 共 19 项，18 通过，依赖闭包 1 项因缺包跳过 |
| Python 全量门禁 | 已尝试全部 72 个模块；运行器初始化因缺少 `ok` 失败，实际业务测试数为 0。不能记为通过或新增代码导致的业务断言失败 |
| Rust launcher_profiles / EXE / NSIS | 未执行：当前环境未提供 Rust/Cargo、MSVC。补丁新增的真实双源 Git/module-provider、失败同步、安装就绪及配置继承测试仍需 CI 执行 |
| git diff --check | 通过；未修改 requirements 或依赖锁文件 |

真实业务树再次导出的 payload/manifest 摘要相同，证据在 `build/runtime-reproducibility.json`；使用最终发布器重试上述本地运行 tag，commit 保持不变。发布完成后的幂等调用只核对附件及已存在运行 tag，返回完成，不再要求重新提供首次迁移验收。该路径另由 `build/runtime-release-final-verified.log` 的补发回归验证。

关键本地证据：`build/runtime-node-final-verified.log`、`build/runtime-python-focused.log`、`build/runtime-python-tests.log`、`build/python-validation/runtime-local.json`、`build/runtime-actionlint.log`、`build/runtime-prepare-validation.json`、`build/runtime-dry-run.json`、`build/runtime-real-payload-local-publish.json`、`build/runtime-archive-validation.json`。测试 API 回执属于 synthetic fixtures，不是外部发布记录。

## 实际发布状态与部署缺项

未创建 GitHub 运行仓库、未配置远端凭据、未创建真实版本/tag/Release、未运行或触发 CI、未生成新的 EXE。开发分支未向远端推送；本地代码提交不代表部署。

目标 `Nancyingcode/ok-ww-personal-update` 经连接器查询返回 404，经 SSH 查询返回 Repository not found；只能确认当前身份不可读取，不能据此区分“尚未创建”与“无访问权限”。部署需补齐或确认：

1. 在已批准可见性下创建空历史目标仓库，或授予所需访问；公开下载的 HTTPS URL 必须可读。不得 fork 开发仓库或导入开发 refs。
2. 源码 Actions variables：`PERSONAL_RUNTIME_REPOSITORY=Nancyingcode/ok-ww-personal-update`、`PERSONAL_RUNTIME_BRANCH=main`（或经核对的一致分支）。远端 CI 配置当前尚未核实。
3. Actions secret：`PERSONAL_RUNTIME_TOKEN`，对目标 Contents 写权限的有限凭据；源码 `GITHUB_TOKEN` 只用于源码 tag/Release。当前未取得、未验证跨仓库写权限。
4. 合入并完成可信 Windows 构建/全量业务门禁，取得含新 URL/补丁的真实 EXE、ZIP 与同 run metadata。旧成功 run 不满足此次迁移条件。
5. 从草稿对应产物完成下述真实验证，再填写 `PERSONAL_RUNTIME_MIGRATION_APPROVAL`；当前未填写、未声称验收通过。

## 尚未完成的 Windows 验证

- 真实 `launcher_profiles` 编译运行、前端/Rust/NSIS/ZIP 构建及产物兼容检查。
- 新运行源冷安装，使用启动器实际 Python/pip 参数，执行 `pip check`，检查 ok/pyappify/Qt/OCR/MCP 的真实导入与模块路径。
- 主界面、个人页面、多账号、自定义队伍、生成模板、OCR 与声骸模型实际调用。
- 独立历史下 Official → Personal → Official 的同名版本切换、失败恢复和回退，包括实际依赖安装失败与进程运行中保护。
- 迁移前后配置/队伍/用户脚本和其他必要数据核对；纯业务更新时 Python、pip 和资源缓存复用。
- 上述所有网络量和安装占用测量。没有进行游戏操作或 Matrix 用例。

回退无需清空用户目录或重写历史。未上线时普通回退/修复本次变更；上线后保留已公开运行 tag，选择已验证旧版本或发布新的修复版本。草稿阶段按回执恢复，不能覆盖已有 tag 的含义。
