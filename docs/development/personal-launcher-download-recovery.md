# Release 下载停在 7% 的复现与修复

日期：2026-11-04（Asia/Hong_Kong）。复现版本为已公开的 `v1.0.5` 安装包，安装后 EXE SHA-256 为 `b6129524053060297591f7131b9c5d63c7c26d75214f834fee24f35827397873`，与 CI 安装器验收一致。

## 原因及原实例恢复

用户从桌面打开启动器后，Git 下载停留在 `Fetching objects: 7.0% (32 / 454)`。Windows 当前启用的系统 HTTP 代理为本机 `127.0.0.1:10808`，但该实例的连接实际直达 GitHub `:443`；临时 pack 文件长时间不增长。libgit2 的 AUTO 代理选项读取 Git/环境代理配置，没有自动继承这套 Windows Internet Settings。

前轮冷安装验收助手明确从系统代理生成 `HTTPS_PROXY` / `HTTP_PROXY`，因而没有覆盖“从桌面打开、无代理环境变量”的情况。这是验收环境与实际启动环境的差异；不能用之前的成功结果声称这一场景已验证。

在核对用户实例的路径和二进制 hash 后，仅停止该实例，用进程级环境变量指定已存在的系统代理重试。Git 包成功下载，约 35.4 MB；没有更换远程地址或删除已下载缓存。随后发现首次中断留下 unborn HEAD，虽然 tag 和完整 Git 对象已拉取，旧启动器仍报 `reference 'refs/heads/main' not found`。核对 `v1.0.5` 对应公开运行 commit 后初始化缓存仓库 HEAD，再次执行 setup。

原实例于 21:07 完成安装并重新打开界面：Personal、`v1.0.5`、installed=true、auto_start=false。该恢复使用原 Release 二进制，尚不代表公开安装包已包含下述修复。

## 启动器补丁

- 没有显式 Git 或代理/绕过环境设置时，读取 Windows 用户启用的静态系统代理，按远程 HTTP/HTTPS 协议选取代理，并保留系统 bypass 规则；不修改系统、全局 Git 或进程环境。显式配置仍交给 libgit2 原有行为处理，未增加 PAC 支持。
- 新下载和缓存仓库 fetch 共用字节进度回调。即使对象百分比不变，字节增长时也按约一秒间隔更新已下载大小，避免大型模型对象下载期间一直只显示 7%。百分比仍按对象数计算。
- 仅在未安装应用的首次下载重试中恢复 unborn HEAD，使用已获取的版本 tag；不替换有效 HEAD，不删除已有 pack 或无关缓存文件。

修改限于现有 `launcher/pyappify-profiles.patch`，没有新增生产依赖，没有修改 schema 2 缓存机制。补丁内容属于缓存指纹输入，变化会使旧成品缓存失效。

## 验证

- Rust `launcher_profiles`：13 通过、0 失败、0 忽略，含新增代理协议/bypass 和中断仓库恢复用例，以及已有来源切换、长路径和数据保留回归。
- Node 五个受影响文件首次为 139 通过、1 失败；唯一失败为验收终端 PATH 缺少已安装的 rustc。补齐工具链 PATH 后单独重跑真实 checkout/缓存集成用例，1 通过、0 失败，无跳过。合计 140 个不同用例均完成验证，没有修改测试或放宽缓存门禁。
- TypeScript 与 Vite build 通过。原生 Build 首次遇到另一项目持有的 Cargo 全局缓存锁，仅停止本工作树的等待进程后尝试离线重建，没有停止其他项目。
- 后续标准 Build → Test → Bundle 全部成功。构建后的 Cargo 专项测试仍实际执行 13 项，0 失败、0 忽略；NSIS 和便携 ZIP 均完成打包，没有用手工回执替代门禁。schema 2 成品 manifest 在相同离线构建环境下验证通过；首次 Metadata 因缺少 `CARGO_NET_OFFLINE` 造成环境指纹不符，保留失败后用正确环境重跑，未放宽校验。
- 实际新 EXE 在短目录独立验证：真正移除 HTTP/HTTPS/ALL/NO_PROXY 后，冷克隆成功，观察到本机 `:10808` 代理连接；再将缓存仓库 HEAD 指向不存在的分支模拟中断，第二次请求成功恢复公开 `v1.0.5` commit，已有 pack 的 SHA-256 不变，无关缓存哨兵保留。
- 原用户安装环境的 `pip check` 通过，实际 ok/pyappify 从该环境 site-packages 导入，Qt 为 6.11.1。

隔离验收有三次脚本/环境问题，失败记录保留，未计入通过：第一次 CLI 请求被已打开的旧实例接收，之后隔离新 EXE；第二次 PowerShell `$null` 实际留下空代理变量，改用 `[NullString]::Value` 并断言变量不存在；第三次版本数组的空 error 成员被 PowerShell 误判为真，改为检查各成员是否真的有 error 属性。最终完整验证以 `native-proxy-state.json` 的通过结果为准。

| 本地修复产物 | 字节数 | SHA-256 |
| --- | ---: | --- |
| `ok-ww-personal.exe` | 10,373,632 | `a11cb7c60380405d5864ff03fd3ac67e1bd335c10d6ee466cf687554739dd0da` |
| `ok-ww-personal-win32-setup.exe` | 4,019,827 | `66be662aeaece7bd78605a0d0074a92e37b52100d3fe66b6094d15f8ecd7a09f` |
| `ok-ww-personal-win32.zip` | 5,110,286 | `14ac8d185edcd7e3c6c8b3f3a63cd69b8a14ac211d026327fb4cda4fa7a464d4` |

本记录对应源码提交前的本地验收。公开 `v1.0.5` 附件仍是原始 CI 二进制；后续源码提交与 push 不会替换附件，本地修复产物不能与之混称。

本机于 21:31 使用新 NSIS 的 `/S /NS /UPDATE` 路径完成启动器更新，原 EXE 已备份到隔离验收目录。安装后 EXE SHA-256 为 `2c4a46283075b16eac61aae9cb1edd66855fe722e364461cd51e90518dfb6871`，与上述便携文件的唯一差异为 Tauri 的 `UNK` → `NSS` 三字节标记。Python、pip 缓存、资源、配置及用户脚本的文件清单、大小和 mtime 均保留；移除代理环境变量后重新打开实际安装界面，仍为 Personal `v1.0.5`、已安装、自动启动关闭、应用未运行。

公开摘要见 [本地修复验收回执](personal-launcher-download-recovery.json)。完整本机回执为 `build/release-install-recovery/local-fix-install-state.json`；后续若发布新版，应绑定新源码提交和实际 CI 产物，不能把本轮本地产物称为已公开的 CI Release。

原实例回执保存在 `build/release-install-recovery-resume/state.json`；首轮失败现场及专项回归日志保存在 `build/release-install-recovery/`。
