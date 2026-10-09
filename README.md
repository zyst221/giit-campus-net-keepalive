# 校园网自动认证

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
