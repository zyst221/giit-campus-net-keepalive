"""Keep a campus network session online through its web authentication portal."""

import argparse
from html.parser import HTMLParser
import json
import logging
import os
from pathlib import Path
import queue
import signal
import subprocess
import threading
import time
import tkinter as tk
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlparse
from urllib.request import HTTPCookieProcessor, Request, build_opener
from http.cookiejar import CookieJar
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText


PORTAL_URL = "http://172.16.7.244/smp/commonauth?nasip=172.16.7.243"
CONNECTIVITY_CHECKS = (
    ("https://www.deepseek.com/", 200, "deepseek.com"),
    ("http://connect.rom.miui.com/generate_204", 204, "connect.rom.miui.com"),
    (
        "http://connectivitycheck.gstatic.com/generate_204",
        204,
        "connectivitycheck.gstatic.com",
    ),
)
POLL_INTERVAL_SECONDS = 10
LOGIN_RETRY_SECONDS = 10
REQUEST_TIMEOUT_SECONDS = 6
CONFIG_DIR = Path.home() / ".config" / "campus-net-keepalive"
CREDENTIALS_FILE = CONFIG_DIR / "credentials.json"
SERVICE_NAME = "campus-net-keepalive.service"


class LoginFormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.action = ""
        self.fields: list[tuple[str, str]] = []
        self._in_form = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "form" and not self._in_form:
            self._in_form = True
            self.action = attributes.get("action") or ""
        elif tag == "input" and self._in_form and attributes.get("name"):
            self.fields.append((attributes["name"], attributes.get("value") or ""))

    def handle_endtag(self, tag: str) -> None:
        if tag == "form" and self._in_form:
            self._in_form = False


class PortalResponseTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self._ignored_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._ignored_depth:
            self._ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth and data.strip():
            self.parts.append(data.strip())


def encrypt_password(password: str, exponent_hex: str, modulus_hex: str) -> str:
    exponent = int(exponent_hex, 16)
    modulus = int(modulus_hex, 16)
    high_index = (modulus.bit_length() - 1) // 16
    chunk_size = 2 * high_index
    if chunk_size <= 0:
        raise ValueError("认证页面返回了无效的 RSA 公钥。")

    plaintext = password.encode("latin-1")
    plaintext += b"\0" * (-len(plaintext) % chunk_size)
    encrypted_blocks = []
    for offset in range(0, len(plaintext), chunk_size):
        block = int.from_bytes(plaintext[offset : offset + chunk_size], "little")
        encrypted = pow(block, exponent, modulus)
        digit_count = max(1, (encrypted.bit_length() + 15) // 16)
        encrypted_blocks.append(
            "".join(
                f"{(encrypted >> (16 * digit)) & 0xFFFF:04x}"
                for digit in range(digit_count - 1, -1, -1)
            )
        )
    return " ".join(encrypted_blocks)


def is_connected(opener) -> bool:
    for url, expected_status, expected_domain in CONNECTIVITY_CHECKS:
        try:
            with opener.open(url, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                hostname = urlparse(response.geturl()).hostname or ""
                matches_domain = hostname == expected_domain or hostname.endswith(
                    "." + expected_domain
                )
                if response.status == expected_status and matches_domain:
                    return True
        except (HTTPError, URLError, TimeoutError, OSError):
            continue
    return False


def authenticate(opener, username: str, password: str) -> tuple[int, str]:
    with opener.open(PORTAL_URL, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        page_url = response.geturl()
        page = response.read().decode("gbk", errors="replace")

    parser = LoginFormParser()
    parser.feed(page)
    fields = dict(parser.fields)
    if not parser.action or "module" not in fields or "exponent" not in fields:
        raise RuntimeError("认证页缺少表单或 RSA 公钥，无法提交登录。")

    fields["userId"] = username
    fields["password"] = encrypt_password(password, fields["exponent"], fields["module"])
    fields["kind"] = "preLogin"
    body = urlencode(fields, encoding="gbk", errors="replace").encode("ascii")
    request = Request(
        urljoin(page_url, parser.action),
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": page_url,
        },
    )
    with opener.open(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        response_page = response.read().decode("gbk", errors="replace")
        response_status = response.status

    response_parser = PortalResponseTextParser()
    response_parser.feed(response_page)
    response_text = " ".join(response_parser.parts)
    return response_status, response_text[:180] or "门户未返回可读提示"


def load_credentials() -> tuple[str, str]:
    try:
        saved = json.loads(CREDENTIALS_FILE.read_text(encoding="utf-8"))
        username = saved.get("username")
        password = saved.get("password")
        if isinstance(username, str) and isinstance(password, str):
            return username, password
    except (OSError, ValueError, AttributeError):
        pass
    return "", ""


def save_credentials(username: str, password: str) -> None:
    CONFIG_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(CONFIG_DIR, 0o700)
    descriptor = os.open(
        CREDENTIALS_FILE,
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
        0o600,
    )
    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        json.dump({"username": username, "password": password}, file)
        file.write("\n")
    os.chmod(CREDENTIALS_FILE, 0o600)


def monitor_network(
    username: str,
    password: str,
    stop_event: threading.Event,
    emit,
) -> None:
    opener = build_opener(HTTPCookieProcessor(CookieJar()))
    previous_connection_state = None
    next_login_attempt = 0.0
    emit("监测已启动；每 10 秒检查一次外网。")

    while not stop_event.is_set():
        connected = is_connected(opener)
        if connected:
            if previous_connection_state is not True:
                emit("外网已连通。")
        else:
            if previous_connection_state is not False:
                emit("外网不可用，准备重新认证。")
            if time.monotonic() >= next_login_attempt and not stop_event.is_set():
                try:
                    response_status, response_text = authenticate(
                        opener, username, password
                    )
                    emit(
                        f"门户已响应（HTTP {response_status}）：{response_text}"
                    )
                except (
                    HTTPError,
                    URLError,
                    TimeoutError,
                    OSError,
                    RuntimeError,
                    ValueError,
                ) as error:
                    emit(f"认证失败：{error}")
                next_login_attempt = time.monotonic() + LOGIN_RETRY_SECONDS
        previous_connection_state = connected
        if stop_event.wait(POLL_INTERVAL_SECONDS):
            break

    emit("监测已停止。")


def run_daemon() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    stop_event = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop_event.set())
    signal.signal(signal.SIGINT, lambda *_: stop_event.set())
    username, password = load_credentials()
    if not username or not password:
        logging.error("尚未配置校园网账号密码，请先打开控制面板并保存认证信息。")
        return
    monitor_network(username, password, stop_event, logging.info)


def systemctl(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["systemctl", "--user", *arguments],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )


class CampusNetApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("校园网自动认证")
        self.root.geometry("760x640")
        self.root.minsize(620, 520)
        self.root.configure(bg="#edf2f0")
        icon_path = Path("/usr/share/icons/hicolor/128x128/apps/campus-net-keepalive.png")
        if not icon_path.is_file():
            icon_path = Path(__file__).resolve().parent / "packaging" / "campus-net-keepalive.png"
        self.window_icon = None
        if icon_path.is_file():
            self.window_icon = tk.PhotoImage(file=str(icon_path))
            self.root.iconphoto(True, self.window_icon)

        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("TFrame", background="#edf2f0")
        style.configure("TLabel", background="#edf2f0", foreground="#1f302c")
        style.configure("Title.TLabel", font=("TkDefaultFont", 16, "bold"))
        style.configure("Hint.TLabel", foreground="#52635e")
        style.configure("TButton", padding=(10, 6))

        self.events: queue.Queue[tuple[str, str]] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.last_log_clear = time.monotonic()
        self.username_var = tk.StringVar()
        self.password_var = tk.StringVar()
        self.show_password_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="未启动")
        self.auto_clear_var = tk.StringVar(value="60")
        self.service_available = False
        self.service_log_lines: set[str] = set()
        autostart_enabled = False
        try:
            systemctl("daemon-reload")
            unit_state = systemctl(
                "show", SERVICE_NAME, "--property=LoadState", "--value"
            )
            self.service_available = (
                unit_state.returncode == 0 and unit_state.stdout.strip() == "loaded"
            )
            if self.service_available:
                autostart_enabled = systemctl("is-enabled", SERVICE_NAME).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            pass
        self.autostart_var = tk.BooleanVar(value=autostart_enabled)
        self.Gtk = None
        self.indicator = None
        self.tray_start_item = None
        self.tray_stop_item = None

        self._build_ui()
        username, password = load_credentials()
        self.username_var.set(username)
        self.password_var.set(password)
        self._setup_indicator()
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self.root.after(150, self._drain_events)

    def _build_ui(self) -> None:
        container = ttk.Frame(self.root, padding=18)
        container.pack(fill="both", expand=True)
        container.columnconfigure(0, weight=1)
        container.rowconfigure(3, weight=1)

        ttk.Label(container, text="校园网自动认证", style="Title.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 14)
        )

        credentials = ttk.LabelFrame(container, text="认证信息", padding=12)
        credentials.grid(row=1, column=0, sticky="ew")
        credentials.columnconfigure(1, weight=1)
        ttk.Label(credentials, text="账号").grid(
            row=0, column=0, sticky="w", padx=(0, 10), pady=5
        )
        ttk.Entry(credentials, textvariable=self.username_var).grid(
            row=0, column=1, sticky="ew", pady=5
        )
        ttk.Label(credentials, text="密码").grid(row=1, column=0, sticky="w", padx=(0, 10), pady=5)
        self.password_entry = ttk.Entry(
            credentials, textvariable=self.password_var, show="*"
        )
        self.password_entry.grid(row=1, column=1, sticky="ew", pady=5)
        ttk.Checkbutton(
            credentials,
            text="显示密码",
            variable=self.show_password_var,
            command=self._toggle_password,
        ).grid(row=1, column=2, padx=(10, 0))
        ttk.Button(
            credentials, text="保存账号密码", command=self._save_credentials
        ).grid(row=2, column=1, sticky="w", pady=(8, 0))
        ttk.Label(
            credentials,
            text="保存在本机用户配置目录，文件权限受限但内容未加密。",
            style="Hint.TLabel",
        ).grid(row=3, column=0, columnspan=3, sticky="w", pady=(8, 0))

        controls = ttk.Frame(container)
        controls.grid(row=2, column=0, sticky="ew", pady=12)
        self.start_button = ttk.Button(controls, text="开始监测", command=self._start)
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(
            controls, text="停止", command=self._stop, state="disabled"
        )
        self.stop_button.pack(side="left", padx=(8, 16))
        ttk.Label(controls, textvariable=self.status_var, style="Hint.TLabel").pack(
            side="left"
        )
        self.autostart_button = ttk.Checkbutton(
            controls,
            text="登录后自动启动后台监测",
            variable=self.autostart_var,
            command=self._toggle_autostart,
            state="normal" if self.service_available else "disabled",
        )
        self.autostart_button.pack(side="right")

        logs = ttk.LabelFrame(container, text="运行日志", padding=10)
        logs.grid(row=3, column=0, sticky="nsew")
        logs.columnconfigure(0, weight=1)
        logs.rowconfigure(1, weight=1)
        toolbar = ttk.Frame(logs)
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Button(toolbar, text="清空日志", command=self._clear_logs).pack(side="left")
        ttk.Label(toolbar, text="自动清除（分钟，0 关闭）", style="Hint.TLabel").pack(
            side="left", padx=(16, 6)
        )
        ttk.Spinbox(
            toolbar,
            from_=0,
            to=1440,
            increment=5,
            width=6,
            textvariable=self.auto_clear_var,
        ).pack(side="left")
        self.log_view = ScrolledText(
            logs,
            height=12,
            wrap="word",
            state="disabled",
            background="#ffffff",
            foreground="#23332f",
            relief="flat",
            padx=8,
            pady=8,
        )
        self.log_view.grid(row=1, column=0, sticky="nsew")

    def _setup_indicator(self) -> None:
        try:
            import gi

            gi.require_version("Gtk", "3.0")
            gi.require_version("AyatanaAppIndicator3", "0.1")
            from gi.repository import AyatanaAppIndicator3, Gtk
        except (ImportError, ValueError) as error:
            self._append_log(f"顶栏指示器不可用：{error}")
            return

        self.Gtk = Gtk
        self.indicator = AyatanaAppIndicator3.Indicator.new(
            "campus-net-keepalive",
            "network-offline",
            AyatanaAppIndicator3.IndicatorCategory.APPLICATION_STATUS,
        )
        self.indicator.set_title("校园网自动认证")
        self.indicator.set_status(
            AyatanaAppIndicator3.IndicatorStatus.ACTIVE
        )

        menu = Gtk.Menu()
        show_item = Gtk.MenuItem.new_with_label("打开控制面板")
        show_item.connect("activate", lambda *_: self._show_window())
        menu.append(show_item)
        self.tray_start_item = Gtk.MenuItem.new_with_label("开始监测")
        self.tray_start_item.connect("activate", lambda *_: self._start())
        menu.append(self.tray_start_item)
        self.tray_stop_item = Gtk.MenuItem.new_with_label("停止监测")
        self.tray_stop_item.connect("activate", lambda *_: self._stop())
        menu.append(self.tray_stop_item)
        menu.append(Gtk.SeparatorMenuItem())
        quit_item = Gtk.MenuItem.new_with_label("退出并停止")
        quit_item.connect("activate", lambda *_: self._quit_app())
        menu.append(quit_item)
        menu.show_all()
        self.indicator.set_menu(menu)
        self._update_indicator(False)

    def _update_indicator(self, active: bool) -> None:
        if self.indicator is None:
            return
        self.indicator.set_icon_full(
            "network-transmit-receive" if active else "network-offline",
            "校园网监测中" if active else "校园网监测已停止",
        )
        if self.tray_start_item is not None:
            self.tray_start_item.set_sensitive(not active)
        if self.tray_stop_item is not None:
            self.tray_stop_item.set_sensitive(active)

    def _show_window(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _quit_app(self) -> None:
        if self.service_available:
            try:
                systemctl("stop", SERVICE_NAME)
            except (OSError, subprocess.TimeoutExpired) as error:
                self._append_log(f"停止后台服务失败：{error}")
        else:
            self.stop_event.set()
        self.root.destroy()

    def _toggle_password(self) -> None:
        self.password_entry.configure(show="" if self.show_password_var.get() else "*")

    def _save_credentials(self) -> None:
        username = self.username_var.get().strip()
        password = self.password_var.get()
        if not username or not password:
            messagebox.showwarning("无法保存", "账号和密码不能为空。", parent=self.root)
            return
        try:
            save_credentials(username, password)
        except OSError as error:
            messagebox.showerror("保存失败", str(error), parent=self.root)
            return
        self._append_log("账号和密码已保存到本机用户配置目录。")

    def _toggle_autostart(self) -> None:
        enabled = self.autostart_var.get()
        if enabled:
            username = self.username_var.get().strip()
            password = self.password_var.get()
            if not username or not password:
                self.autostart_var.set(False)
                messagebox.showwarning(
                    "无法启用自启动", "请先填写账号和密码。", parent=self.root
                )
                return
            try:
                save_credentials(username, password)
            except OSError as error:
                self.autostart_var.set(False)
                messagebox.showerror("保存失败", str(error), parent=self.root)
                return

        try:
            result = systemctl("enable" if enabled else "disable", SERVICE_NAME)
        except (OSError, subprocess.TimeoutExpired) as error:
            result = None
            message = str(error)
        else:
            message = result.stderr.strip() or result.stdout.strip()
        if result is None or result.returncode != 0:
            self.autostart_var.set(not enabled)
            messagebox.showerror(
                "设置失败", message or "无法更新用户服务自启动状态。", parent=self.root
            )
            return
        self._append_log("已启用登录后后台自启动。" if enabled else "已关闭登录后自启动。")

    def _start(self) -> None:
        username = self.username_var.get().strip()
        password = self.password_var.get()
        if not username or not password:
            messagebox.showwarning("无法启动", "请先填写账号和密码。", parent=self.root)
            return
        if self.service_available:
            try:
                save_credentials(username, password)
                result = systemctl("start", SERVICE_NAME)
            except (OSError, subprocess.TimeoutExpired) as error:
                messagebox.showerror("启动失败", str(error), parent=self.root)
                return
            if result.returncode != 0:
                messagebox.showerror(
                    "启动失败",
                    result.stderr.strip() or result.stdout.strip(),
                    parent=self.root,
                )
                return
            self._append_log("已启动后台监测服务。")
            self._refresh_service()
            return
        if self.worker is not None and self.worker.is_alive():
            return

        self.stop_event = threading.Event()
        self.worker = threading.Thread(
            target=self._monitor,
            args=(username, password, self.stop_event),
            daemon=True,
        )
        self.worker.start()
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.status_var.set("正在监测")

    def _stop(self) -> None:
        if self.service_available:
            try:
                result = systemctl("stop", SERVICE_NAME)
            except (OSError, subprocess.TimeoutExpired) as error:
                messagebox.showerror("停止失败", str(error), parent=self.root)
                return
            if result.returncode != 0:
                messagebox.showerror(
                    "停止失败",
                    result.stderr.strip() or result.stdout.strip(),
                    parent=self.root,
                )
                return
            self._append_log("已停止后台监测服务。")
            self._refresh_service()
            return
        self.stop_event.set()
        self.stop_button.configure(state="disabled")
        self.status_var.set("正在停止")

    def _monitor(
        self, username: str, password: str, stop_event: threading.Event
    ) -> None:
        opener = build_opener(HTTPCookieProcessor(CookieJar()))
        previous_connection_state = None
        next_login_attempt = 0.0
        self.events.put(("log", "监测已启动；每 10 秒检查一次外网。"))

        while not stop_event.is_set():
            connected = is_connected(opener)
            if connected:
                if previous_connection_state is not True:
                    self.events.put(("log", "外网已连通。"))
            else:
                if previous_connection_state is not False:
                    self.events.put(("log", "外网不可用，准备重新认证。"))
                if time.monotonic() >= next_login_attempt and not stop_event.is_set():
                    try:
                        response_status, response_text = authenticate(
                            opener, username, password
                        )
                        self.events.put(
                            (
                                "log",
                                f"门户已响应（HTTP {response_status}）：{response_text}",
                            )
                        )
                    except (
                        HTTPError,
                        URLError,
                        TimeoutError,
                        OSError,
                        RuntimeError,
                        ValueError,
                    ) as error:
                        self.events.put(("log", f"认证失败：{error}"))
                    next_login_attempt = time.monotonic() + LOGIN_RETRY_SECONDS
            previous_connection_state = connected
            if stop_event.wait(POLL_INTERVAL_SECONDS):
                break

        self.events.put(("log", "监测已停止。"))

    def _append_log(self, message: str) -> None:
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        self.log_view.configure(state="normal")
        self.log_view.insert("end", f"[{timestamp}] {message}\n")
        line_count = int(self.log_view.index("end-1c").split(".")[0])
        if line_count > 1000:
            self.log_view.delete("1.0", f"{line_count - 500}.0")
        self.log_view.see("end")
        self.log_view.configure(state="disabled")

    def _clear_logs(self) -> None:
        self.log_view.configure(state="normal")
        self.log_view.delete("1.0", "end")
        self.log_view.configure(state="disabled")
        self.last_log_clear = time.monotonic()

    def _drain_events(self) -> None:
        while True:
            try:
                _, message = self.events.get_nowait()
            except queue.Empty:
                break
            self._append_log(message)

        interval_text = self.auto_clear_var.get().strip()
        try:
            interval_minutes = max(0, int(interval_text))
        except ValueError:
            interval_minutes = 0
        if (
            interval_minutes
            and time.monotonic() - self.last_log_clear >= interval_minutes * 60
        ):
            self._clear_logs()
            self._append_log("日志已按计划自动清空。")

        if self.worker is not None and not self.worker.is_alive():
            self.start_button.configure(state="normal")
            self.stop_button.configure(state="disabled")
            if self.status_var.get() != "未启动":
                self.status_var.set("已停止")
        if self.service_available:
            self._refresh_service()
        elif self.indicator is not None:
            active = self.worker is not None and self.worker.is_alive()
            self._update_indicator(active)
        if self.Gtk is not None:
            while self.Gtk.events_pending():
                self.Gtk.main_iteration_do(False)
        self.root.after(1000, self._drain_events)

    def _refresh_service(self) -> None:
        try:
            active = systemctl("is-active", SERVICE_NAME).returncode == 0
            logs = subprocess.run(
                [
                    "journalctl",
                    "--user",
                    "--unit",
                    SERVICE_NAME,
                    "-n",
                    "50",
                    "--no-pager",
                    "--output=short-iso",
                ],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return

        self.status_var.set("后台监测中" if active else "后台已停止")
        self.start_button.configure(state="disabled" if active else "normal")
        self.stop_button.configure(state="normal" if active else "disabled")
        self._update_indicator(active)
        if logs.returncode == 0:
            for line in logs.stdout.splitlines():
                if line and line not in self.service_log_lines:
                    self.service_log_lines.add(line)
                    self._append_log(line)

    def _close(self) -> None:
        if self.indicator is not None:
            self.root.withdraw()
            return
        if not self.service_available:
            self.stop_event.set()
        self.root.destroy()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--daemon", action="store_true")
    arguments = parser.parse_args()
    if arguments.daemon:
        run_daemon()
        return

    root = tk.Tk()
    CampusNetApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()