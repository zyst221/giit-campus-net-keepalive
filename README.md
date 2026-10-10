# 校园网自动认证

## Windows

### 构建

只在构建电脑上需要安装 Python 3.10 或更高版本，并确保安装时包含 Tcl/Tk。打开项目目录，在命令提示符或 PowerShell 中运行：

```bat
build_windows.bat
```

脚本会安装 PyInstaller 并生成单文件程序 `dist\CampusNetKeepalive.exe`。将此 exe 放到需要运行的 Windows 电脑上即可使用，运行电脑不需要另外安装 Python。

PyInstaller 不支持从 Linux 交叉构建 Windows exe，因此必须在 Windows 上执行构建脚本。

### 使用

1. 双击运行 `CampusNetKeepalive.exe`。
2. 输入校园网账号和密码，点击**保存账号密码**。
3. 点击**开始监测**。程序会定期检查外网连接，并在连接中断时尝试重新认证；当前状态和运行日志显示在窗口中。
4. 点击**停止**可结束监测。关闭窗口也会停止程序；若只想暂时离开窗口，请最小化窗口。

勾选**登录后自动启动后台监测**后，当前 Windows 用户下次登录时会打开程序并自动开始监测。取消勾选即可关闭此设置。

账号密码保存在 `%APPDATA%\campus-net-keepalive\credentials.json`，文件内容未加密。请勿将该文件或 exe 连同账号密码分享给他人。

## Ubuntu / Debian

在已安装 `dpkg-deb` 的 Ubuntu 或 Debian 系统上构建 Debian 安装包：

```sh
sh packaging/build-deb.sh
sudo apt install ./dist/campus-net-keepalive_1.0.2_all.deb
```

从桌面应用菜单启动**校园网自动认证**。GNOME 顶栏中的网络图标会显示监测状态，并提供打开控制窗口、开始或停止监测以及退出等操作。关闭窗口会将程序隐藏到顶栏，用户级后台服务会继续运行。

勾选**登录后自动启动后台监测**可让服务在之后的用户登录时自动启动。此选项只控制自启动；服务运行时，可在窗口或顶栏菜单中点击“停止”。

请在窗口中填写并保存校园网账号和密码，再启动后台服务。

运行日志可在程序窗口中查看，也可以使用以下命令查看：

```sh
journalctl --user -u campus-net-keepalive.service
```

卸载命令：`sudo apt remove campus-net-keepalive`。已保存的账号和密码位于 `~/.config/campus-net-keepalive/credentials.json`，文件权限受到限制，但内容未加密。

