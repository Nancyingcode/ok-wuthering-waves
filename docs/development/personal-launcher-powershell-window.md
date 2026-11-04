# 安装期间 PowerShell 控制台隐藏修复

日期：2026-11-04（Asia/Hong_Kong）。承接上一轮 [下载恢复修复](personal-launcher-download-recovery.md)，本次只追加安装器修改，现有非 NSIS 补丁块已逐字节核对保持一致。

## 修改

固定 PyAppify 的 `src-tauri/nsis/installer.nsi` 中，`KillInstallDirExecutables` 在覆盖安装前使用 `ExecWait` 启动 PowerShell，占用进程清理期间会显示控制台。改为 `nsExec::ExecToLog`，从启动时隐藏子进程，并补充 `-NoLogo -NonInteractive -WindowStyle Hidden`。输出保留在安装器详情日志中。

NSIS 插件可能返回字符串 `error`，不能继续用数字比较判断成功；改用字符串比较，仅 `0` 视为成功，其他结果继续进入原有重试/取消处理。没有修改进程筛选、清理脚本业务逻辑、权限或 Defender 设置。

修改继续放在现有 `launcher/pyappify-profiles.patch`，没有新增生产依赖，也没有更改 schema 2 缓存门禁。补丁变化会改变缓存指纹。

## 验证

- Node 工作流和真实固定 checkout 缓存回归：70 通过、0 失败、0 跳过。
- 标准 Prepare → Build → Test → Bundle → Metadata 成功；TypeScript/Vite、Rust 13 项专项测试、NSIS 和 ZIP 打包均通过。
- 隔离 NSIS 测试直接提取实际模板的命令及错误分支，验证正常退出 `0`、脚本失败 `2`、无法启动 `error` 三种情况，全部正确。测试 PowerShell 和它启动的控制台子进程自行查询自身控制台状态，均为不可见；带空格的脚本/参数路径正确。
- 控制台可以存在但保持隐藏，因此不把“无可见窗口”描述为“完全没有控制台对象”。验证只涉及本次测试进程，不抓取或控制用户其他窗口。

本地产物和真实覆盖安装结果另见同名 JSON 回执，记录的是源码提交前的验收。后续源码提交与 push 不会替换公开 `v1.0.5` 附件。

首次管理员验收助手的 UAC 被 Windows 返回为“操作已被用户取消”，当时未执行覆盖安装。用户随后从托盘退出启动器，改用普通用户身份完成真实 `/S /NS /UPDATE` 覆盖验收，退出码为 0，没有启动管理员 PowerShell。Python、pip 缓存、资源、配置和用户脚本的文件清单/大小/mtime 全部保留；原 EXE 和卸载器已备份。验收后保持启动器关闭，由用户自行重新打开。

| 产物 | 字节数 | SHA-256 |
| --- | ---: | --- |
| 便携 EXE | 10,373,632 | `68a984cae799a91d99adbcf29a298e4926987051c989132ce0e525db7377e913` |
| 安装包 | 4,021,311 | `ef703a6e082710ce76b92468319305656fe2c7866395551e6c105c39fa4ed803` |
| 便携 ZIP | 5,110,289 | `5580e25085b0a41744547a522ba080464e711e7ef76aeda441be39a355dab966` |

本机已安装 EXE 为 `2b1d8739263fc840bc4145a5215ec289897da3ae86ee4b2065e3e7a624bc8f2c`，相对便携文件只允许 Tauri `UNK` → `NSS` 三字节标记的差异，已完整核验。真实覆盖安装不是无人值守强制结束原应用的测试：用户已先行退出。现有进程清理逻辑没有改动。
