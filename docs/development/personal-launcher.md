# 官方版与个人版启动器 / Official and Personal Launcher

`launcher/pyappify.personal.yml` 提供同一个应用的两个更新源：

| 选项 | 代码来源 |
| --- | --- |
| `Official` | 原项目的中国区官方更新仓库 `ok-oldking/ok-ww-update2` |
| `Personal` | 个人仓库 `Nancyingcode/ok-wuthering-waves` 的版本标签 |

启动器共享一个应用目录，每次运行当前选中的版本。默认关闭自动启动，采用手动更新，方便先选择版本再运行。两个选项都编译在启动器自身，与工作目录中的 `pyappify.yml` 无关，因此安装官方版本后下载的官方 `pyappify.yml` 也不会覆盖这两个选项。

The launcher manages one application with Official and Personal profiles baked into the executable. Auto Start is off and updates are manual by default.

## 构建 / Build

需要 Windows、PowerShell 7、Git、Node.js、pnpm、Rust 和 Visual Studio C++ 构建工具。脚本使用 PyAppify 自带的 pnpm 锁文件，不修改本项目的 Python 依赖。

```powershell
.\scripts\build_personal_launcher.ps1
```

脚本固定 PyAppify `v1.2.3`（提交 `52bc6d9`），按需克隆到 `build/pyappify/`，应用 `launcher/pyappify-profiles.patch`，再构建便携启动器和 NSIS 安装包。国内网络无法使用 GitHub SSH 时，可以传入 HTTPS 克隆地址：

```powershell
.\scripts\build_personal_launcher.ps1 -LauncherRepositoryUrl 'https://github.com/ok-oldking/pyappify.git'
```

构建过程中的检查：

1. `git apply --check`：补丁可以干净应用，或此前已经应用过。
2. `cargo test --release --lib launcher_profiles`：双源配置、标签检出、`configs` 保护等补丁测试。
3. `pnpm tauri build --no-bundle`：生成便携版可执行文件。
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

## 持续集成 / CI

`.github/workflows/build.yml` 在 `package` 任务中为本仓库（`Nancyingcode/ok-wuthering-waves`）接入个人启动器构建：

- `actions/checkout` 仍按触发标签检出源码，`fetch-depth: 0` 保留版本信息；启动器源码由脚本自己按固定提交检出，不受分支漂移影响。
- 构建前检查 `git`、`node`、`pnpm.cmd`、`cargo.exe` 是否存在，并用 `pnpm/action-setup` 固定 pnpm 11；Rust 使用 `windows-latest` 自带的 stable 工具链。
- 脚本通过 HTTPS 克隆 PyAppify，避免 CI 中缺少 SSH agent。
- 构建完成后校验 `dist/ok-ww-personal-win32-setup.exe`、`dist/ok-ww-personal-win32.zip` 与便携版可执行文件确实存在，再复制到 `pyappify_dist/` 参与制品上传和 Release。
- 原作者的 SignPath 签名步骤只在 `ok-oldking/ok-wuthering-waves` 仓库生效（`SIGN_BUILD` 要求 `github.repository == 'ok-oldking/ok-wuthering-waves'`），个人仓库恒为 `false`，因此产物为未签名安装包，首次运行时可能出现 SmartScreen 提示。已发布的 `v1.0.1` 同样未签名：`ok-ww-win32-China-setup.exe`、`ok-ww-win32-online-setup.exe` 及 zip 内的 `ok-ww.exe` 经 `Get-AuthenticodeSignature` 校验均为 `NotSigned`。
- 官方 `pyappify-action` 会先删除 `pyappify_dist/` 再按根目录 `pyappify.yml` 打包 China / Global，故 `Build portable executable` 与 `Build installers` 两步限定为仅上游仓库执行，避免个人仓库的启动器产物被覆盖。

The packaging job builds the dual-profile launcher with HTTPS, verifies the artifacts, and stages them for release upload.

## 使用与发布个人版本 / Select and Publish Versions

1. 打开启动器，选择 `Official` 或 `Personal` 并完成安装。
2. 运行期间先点击「停止」，等待程序完全退出后，再点击「更换配置 / Change Profile」。
3. 选择另一个配置，等待代码及依赖切换成功，再点击「启动」。
4. 同一来源的历史版本通过启动器的版本选择功能安装或回退。

个人更新源使用 SSH，与开发仓库的 `origin` 一致。运行启动器的用户需要有可访问 GitHub 的 SSH agent，并加载对应密钥；命令行 Git 可读取私钥，并不代表启动器的 SSH agent 已就绪。

Personal 的 Python 依赖使用官方 PyPI，安装及版本更新均保留当前配置的依赖源，避免镜像同步延迟导致已发布的固定版本无法安装。

个人仓库必须在**包含个人修改的提交**上发布版本标签，例如 `v1.0.0`、`v1.0.1`。仅推送 `personal` 分支不会发布启动器可选版本。首次安装 Personal 前需要存在至少一个版本标签。普通代码更新只需发布新标签；启动器配置或补丁改变后需要重新构建启动器。

Stop the current application before changing profiles. Personal versions are Git tags on commits containing your changes, rather than branch names. Personal setup requires GitHub SSH-agent access.

## 验证 / Verification

在隔离目录中安装并启动，避免影响正在运行的启动器、游戏和用户配置：

```powershell
$targetRoot = Join-Path $env:TEMP 'ok-ww-personal-install'
$process = Start-Process -FilePath 'dist/ok-ww-personal-win32-setup.exe' `
    -ArgumentList '/S', "/D=$targetRoot" -Wait -PassThru
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
