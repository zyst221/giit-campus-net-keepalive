# Campus Network Keepalive

Build the Debian package on Ubuntu or Debian with `dpkg-deb` installed:

```sh
sh packaging/build-deb.sh
sudo apt install ./dist/campus-net-keepalive_1.0.3_all.deb
```

Launch **校园网自动认证** from the desktop application menu. The network icon
in the GNOME top bar shows the monitor state and offers controls to open the
window, start or stop monitoring, and exit. Closing the window hides it in the
top bar; the user-level background service keeps running. Select
**登录后自动启动后台监测** to enable it for future user logins. The checkbox
controls startup only; use Stop in the window or top-bar menu to stop a running
service.

There are no default credentials. Enter your own campus account and password
in the window, then save them before starting the background service.

Service logs are available in the GUI and through:

```sh
journalctl --user -u campus-net-keepalive.service
```

Remove the package with `sudo apt remove campus-net-keepalive`. Saved credentials
are stored in `~/.config/campus-net-keepalive/credentials.json` with restrictive
file permissions, but are not encrypted.