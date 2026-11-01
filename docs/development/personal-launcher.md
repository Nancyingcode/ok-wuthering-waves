# 官方版与个人版启动器 / Official and Personal Launcher

`launcher/pyappify.personal.yml` 提供同一个应用的两个更新源：

| 选项 | 代码来源 |
| --- | --- |
| `Official` | 原项目的中国区官方更新仓库 `ok-oldking/ok-ww-update2` |
| `Personal` | 个人仓库 `Nancyingcode/ok-wuthering-waves` |

启动器共享一个应用目录，每次运行当前选中的版本。默认关闭自动启动，采用手动更新，方便先选择版本再运行。

The launcher manages one application with Official and Personal profiles. Auto Start is off and updates are manual by default.

## 构建 / Build

需要 Windows、PowerShell 7、Git、Node.js、pnpm、Rust 和 Visual Studio C++ 构建工具。脚本使用 PyAppify 自带的 pnpm 锁文件，不修改本项目的 Python 依赖。

```powershell
.\scripts\build_personal_launcher.ps1
```

生成的免安装启动器位于 `dist/ok-ww-personal/ok-ww-personal.exe`，压缩包位于 `dist/ok-ww-personal-win32.zip`。首次安装所选版本需要联网下载 Python、代码及依赖。解压后保留同目录的 `pyappify.yml`。

只准备源码和配置、暂不安装构建依赖时，可以运行：

```powershell
.\scripts\build_personal_launcher.ps1 -PrepareOnly
```

The script pins PyAppify v1.2.3 and produces a portable launcher under `dist/`. The package downloads the selected application's runtime on first setup.

## 使用与发布个人版本 / Select and Publish Versions

1. 打开启动器，选择 `Official` 或 `Personal` 并完成安装。
2. 运行期间先点击「停止」，等待程序完全退出后，再点击「更换配置 / Change Profile」。
3. 选择另一个配置，等待代码及依赖切换成功，再点击「启动」。
4. 同一来源的历史版本通过启动器的版本选择功能安装或回退。

个人更新源使用 SSH，与开发仓库的 `origin` 一致。运行启动器的用户需要有可访问 GitHub 的 SSH agent，并加载对应密钥；命令行 Git 可读取私钥，并不代表启动器的 SSH agent 已就绪。

Personal 的 Python 依赖使用官方 PyPI，安装及版本更新均保留当前配置的依赖源，避免镜像同步延迟导致已发布的固定版本无法安装。

个人仓库必须在**包含个人修改的提交**上发布版本标签，例如 `v1.0.0`、`v1.0.1`。仅推送 `personal` 分支不会发布启动器可选版本。首次安装 Personal 前需要存在至少一个版本标签。普通代码更新只需发布新标签；启动器配置或补丁改变后需要重新构建启动器。

本脚本只在本地构建，不执行提交、推送或发布。本项目原有 `.github/workflows/build.yml` 仍绑定原作者的同步仓库和签名服务，不能直接当作个人发布流水线使用。

Stop the current application before changing profiles. Personal versions are Git tags on commits containing your changes, rather than branch names. Personal setup requires GitHub SSH-agent access. The script does not commit, push, or publish.

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
