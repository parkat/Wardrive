#!/usr/bin/env python3
"""wardrive_ui — interactive terminal UI for warDrive capture sessions.

Usage:  sudo python3 wardrive_ui.py
        (Must run as root since wardrive.sh requires root privileges)
"""

import curses
import os
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
WARDRIVE_SH = SCRIPT_DIR / "wardrive.sh"
WEBAPP_PY   = SCRIPT_DIR / "webapp" / "main.py"
DB_PATH     = SCRIPT_DIR / "processing" / "wardrive.db"
PID_DIR     = SCRIPT_DIR / "capture" / "pids"
CMD_FILE    = SCRIPT_DIR / "capture" / "wardrive.cmd"

MAX_LOG_LINES   = 500
STATS_REFRESH_S = 5
UI_TICK_MS      = 500

# Color pair indices
CP_HEADER = 1
CP_GOOD   = 2
CP_BAD    = 3
CP_WARN   = 4
CP_INFO   = 5
CP_NORMAL = 6
CP_CMD    = 7
CP_BORDER = 8


class WarDriveUI:
    def __init__(self, stdscr):
        self.stdscr = stdscr
        self.log_lines: deque = deque(maxlen=MAX_LOG_LINES)
        self.log_lock = threading.Lock()
        self.stats: dict = {}
        self.stats_lock = threading.Lock()

        self.cmd_input = ""
        self.cmd_history: list = []
        self.cmd_history_idx = -1
        self.log_scroll = 0
        self.running = True

        self.wardrive_proc = None
        self.webapp_proc   = None
        self.session_start = None

        self._init_colors()
        self._init_dirs()
        self._start_wardrive()

        threading.Thread(target=self._log_reader, daemon=True).start()
        threading.Thread(target=self._stats_poller, daemon=True).start()

    # ── Initialization ────────────────────────────────────────────────────────

    def _init_colors(self):
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(CP_HEADER, curses.COLOR_BLACK,   curses.COLOR_WHITE)
        curses.init_pair(CP_GOOD,   curses.COLOR_GREEN,   -1)
        curses.init_pair(CP_BAD,    curses.COLOR_RED,     -1)
        curses.init_pair(CP_WARN,   curses.COLOR_YELLOW,  -1)
        curses.init_pair(CP_INFO,   curses.COLOR_CYAN,    -1)
        curses.init_pair(CP_NORMAL, curses.COLOR_WHITE,   -1)
        curses.init_pair(CP_CMD,    curses.COLOR_MAGENTA, -1)
        curses.init_pair(CP_BORDER, curses.COLOR_CYAN,    -1)

    def _init_dirs(self):
        try:
            PID_DIR.mkdir(parents=True, exist_ok=True)
            CMD_FILE.parent.mkdir(parents=True, exist_ok=True)
            CMD_FILE.write_text("")
        except Exception as e:
            self._log(f"[ui] WARNING: could not init capture dirs: {e}")

    def _start_wardrive(self):
        self.session_start = datetime.now(timezone.utc)
        try:
            self.wardrive_proc = subprocess.Popen(
                [str(WARDRIVE_SH), "--no-tee"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            self._log(f"[ui] wardrive.sh launched (PID {self.wardrive_proc.pid})")
        except Exception as e:
            self._log(f"[ui] ERROR: could not launch wardrive.sh: {e}")

    # ── Background threads ────────────────────────────────────────────────────

    def _log(self, line: str):
        with self.log_lock:
            self.log_lines.append(line)

    def _log_reader(self):
        if not self.wardrive_proc or not self.wardrive_proc.stdout:
            return
        try:
            for line in self.wardrive_proc.stdout:
                self._log(line.rstrip("\n"))
            rc = self.wardrive_proc.wait()
            self._log(f"[ui] wardrive.sh exited (code {rc})")
        except Exception as e:
            self._log(f"[ui] log reader error: {e}")

    def _stats_poller(self):
        while self.running:
            self._refresh_stats()
            time.sleep(STATS_REFRESH_S)

    def _refresh_stats(self):
        if not DB_PATH.exists():
            return
        try:
            with sqlite3.connect(str(DB_PATH), timeout=2) as db:
                db.row_factory = sqlite3.Row
                row = db.execute(
                    "SELECT * FROM sessions WHERE ended_at_utc IS NULL "
                    "ORDER BY started_at_utc DESC LIMIT 1"
                ).fetchone()
                stats: dict = {}
                if row:
                    sid = row["session_id"]
                    stats["session_id"]  = sid
                    stats["started_at"]  = row["started_at_utc"]
                    stats["wifi_count"]  = db.execute(
                        "SELECT COUNT(DISTINCT bssid) FROM wifi_obs WHERE session_id=?", (sid,)
                    ).fetchone()[0] or 0
                    stats["bt_count"]    = db.execute(
                        "SELECT COUNT(DISTINCT address) FROM bt_obs WHERE session_id=?", (sid,)
                    ).fetchone()[0] or 0
                    stats["rf_count"]    = db.execute(
                        "SELECT COUNT(DISTINCT device_id) FROM rf_obs WHERE session_id=?", (sid,)
                    ).fetchone()[0] or 0
                with self.stats_lock:
                    self.stats = stats
        except Exception:
            pass

    # ── Collector / webapp helpers ────────────────────────────────────────────

    def _collector_status(self) -> dict:
        status = {}
        for name in ("wifi", "sdr", "wideband", "esp32", "gps"):
            pid_file = PID_DIR / f"{name}.pid"
            running = False
            if pid_file.exists():
                try:
                    pid = int(pid_file.read_text().strip())
                    os.kill(pid, 0)
                    running = True
                except (OSError, ValueError, ProcessLookupError):
                    pass
            status[name] = running
        return status

    def _webapp_running(self) -> bool:
        return bool(self.webapp_proc and self.webapp_proc.poll() is None)

    def _send_cmd(self, cmd: str):
        try:
            with open(str(CMD_FILE), "a") as f:
                f.write(cmd + "\n")
        except Exception as e:
            self._log(f"[ui] WARNING: could not write command: {e}")

    def _stop_collector(self, name: str):
        pid_file = PID_DIR / f"{name}.pid"
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text().strip())
                os.kill(pid, signal.SIGTERM)
                self._log(f"[cmd] Sent SIGTERM to {name} supervisor (PID {pid})")
            except (OSError, ValueError) as e:
                self._log(f"[cmd] Error stopping {name}: {e}")
        else:
            self._log(f"[cmd] {name}: no PID file — not running")

    def _start_collector(self, name: str):
        self._send_cmd(f"start:{name}")
        self._log(f"[cmd] Requested restart: {name}")

    def _start_webapp(self):
        if self._webapp_running():
            self._log("[cmd] Webapp is already running on :8000")
            return
        try:
            self.webapp_proc = subprocess.Popen(
                [sys.executable, str(WEBAPP_PY)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._log(f"[cmd] Webapp started (PID {self.webapp_proc.pid}) — http://localhost:8000")
        except Exception as e:
            self._log(f"[cmd] Error starting webapp: {e}")

    def _stop_webapp(self):
        if not self._webapp_running():
            self._log("[cmd] Webapp is not running")
            return
        try:
            self.webapp_proc.terminate()
            self._log("[cmd] Webapp stopped")
        except Exception as e:
            self._log(f"[cmd] Error stopping webapp: {e}")

    def _stop_session(self):
        self._log("[ui] Shutting down...")
        if self._webapp_running():
            self.webapp_proc.terminate()
        if self.wardrive_proc and self.wardrive_proc.poll() is None:
            self.wardrive_proc.terminate()
            try:
                self.wardrive_proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.wardrive_proc.kill()

    def _run_enrichment(self):
        def _worker():
            self._log("[enrich] Starting post-processing enrichment...")
            try:
                proc = subprocess.run(
                    [sys.executable, str(SCRIPT_DIR / "processing" / "enrich.py")],
                    capture_output=True, text=True, timeout=600,
                )
                for line in proc.stdout.splitlines():
                    self._log(f"[enrich] {line}")
                if proc.returncode != 0:
                    self._log(f"[enrich] Error: {proc.stderr[:300]}")
                else:
                    self._log("[enrich] Done")
            except Exception as e:
                self._log(f"[enrich] Failed: {e}")
        threading.Thread(target=_worker, daemon=True).start()

    # ── Command handler ───────────────────────────────────────────────────────

    def handle_command(self, raw: str):
        cmd = raw.strip().lower()
        if not cmd:
            return
        self._log(f"[cmd] > {raw.strip()}")
        parts = cmd.split()

        if parts[0] in ("q", "quit", "exit"):
            self._stop_session()
            self.running = False

        elif parts[0] == "webapp" and len(parts) > 1:
            sub = parts[1]
            if sub in ("on", "start"):
                self._start_webapp()
            elif sub in ("off", "stop"):
                self._stop_webapp()
            else:
                self._log("[cmd] Usage: webapp on | webapp off")

        elif parts[0] == "stop" and len(parts) > 1:
            target = "esp32" if parts[1] == "ble" else parts[1]
            if target in ("wifi", "sdr", "esp32", "gps", "wideband"):
                self._stop_collector(target)
            else:
                self._log("[cmd] Unknown collector. Use: wifi | sdr | ble | gps | wideband")

        elif parts[0] == "start" and len(parts) > 1:
            target = "esp32" if parts[1] == "ble" else parts[1]
            if target in ("wifi", "sdr", "esp32", "gps", "wideband"):
                self._start_collector(target)
            else:
                self._log("[cmd] Unknown collector. Use: wifi | sdr | ble | gps | wideband")

        elif parts[0] == "enrich":
            self._run_enrichment()

        elif parts[0] == "help":
            self._log("[cmd] Commands: q/quit  webapp on/off  stop/start wifi|sdr|ble|gps|wideband  enrich")

        else:
            self._log(f"[cmd] Unknown: '{cmd}' — type 'help' for a list")

    # ── Drawing ───────────────────────────────────────────────────────────────

    def draw(self):
        h, w = self.stdscr.getmaxyx()
        if h < 8 or w < 40:
            try:
                self.stdscr.addstr(0, 0, "Terminal too small — resize to at least 40x8"[:w - 1])
            except curses.error:
                pass
            self.stdscr.refresh()
            return

        self.stdscr.erase()

        top_h  = min(20, max(10, h // 3))
        log_h  = max(3, h - 1 - 1 - top_h - 1 - 1 - 1)
        left_w = w * 6 // 10
        right_w = w - left_w

        sep1_y = 1
        top_y  = sep1_y + 1
        sep2_y = top_y + top_h
        log_y  = sep2_y + 1
        sep3_y = log_y + log_h
        cmd_y  = sep3_y + 1

        self._draw_header(0, w)
        self._draw_hline(sep1_y, 0, w)
        self._draw_stats_panel(top_y, 0, top_h, left_w)
        self._draw_vline(top_y, left_w, top_h)
        self._draw_help_panel(top_y, left_w + 1, top_h, right_w - 1)
        self._draw_hline(sep2_y, 0, w)
        self._draw_log_panel(log_y, 0, log_h, w)
        if sep3_y < h:
            self._draw_hline(sep3_y, 0, w)
        if cmd_y < h:
            self._draw_cmd_input(cmd_y, w)

        self.stdscr.refresh()

    def _safe_addstr(self, y, x, text, attr=curses.A_NORMAL):
        try:
            self.stdscr.addstr(y, x, text, attr)
        except curses.error:
            pass

    def _draw_header(self, y, w):
        title = " warDrive "
        now_s = datetime.now().strftime("%H:%M:%S")
        if self.session_start:
            e = (datetime.now(timezone.utc) - self.session_start).total_seconds()
            uptime = f"{int(e // 3600):02d}:{int((e % 3600) // 60):02d}:{int(e % 60):02d}"
        else:
            uptime = "00:00:00"
        with self.stats_lock:
            sid = self.stats.get("session_id", "starting...")
        right = f" {uptime} | {now_s} "
        mid   = f" Session: {sid}"

        self._safe_addstr(y, 0, " " * w, curses.color_pair(CP_HEADER) | curses.A_BOLD)
        self._safe_addstr(y, 0, title, curses.color_pair(CP_HEADER) | curses.A_BOLD)
        mid_x = len(title) + 1
        if mid_x + len(mid) < w - len(right):
            self._safe_addstr(y, mid_x, mid[:w - mid_x - len(right) - 1],
                              curses.color_pair(CP_HEADER))
        right_x = max(0, w - len(right))
        self._safe_addstr(y, right_x, right, curses.color_pair(CP_HEADER) | curses.A_BOLD)

    def _draw_hline(self, y, x, w):
        self._safe_addstr(y, x, "─" * w, curses.color_pair(CP_BORDER))

    def _draw_vline(self, y, x, h):
        attr = curses.color_pair(CP_BORDER)
        for i in range(h):
            try:
                self.stdscr.addstr(y + i, x, "│", attr)
            except curses.error:
                pass

    def _panel_title(self, y, x, w, label):
        dashes = max(0, w - len(label) - 1)
        line   = "─" + label + "─" * dashes
        self._safe_addstr(y, x, line[:w], curses.color_pair(CP_BORDER) | curses.A_BOLD)

    def _draw_stats_panel(self, y, x, h, w):
        self._panel_title(y, x, w, " LIVE DATA ")
        row = y + 1

        with self.stats_lock:
            stats = dict(self.stats)
        collectors = self._collector_status()
        webapp_up  = self._webapp_running()

        db_start_str = stats.get("started_at")
        if db_start_str:
            try:
                db_start = datetime.fromisoformat(db_start_str.replace("Z", "+00:00"))
                elapsed  = (datetime.now(timezone.utc) - db_start).total_seconds()
            except Exception:
                elapsed = 0.0
        elif self.session_start:
            elapsed = (datetime.now(timezone.utc) - self.session_start).total_seconds()
        else:
            elapsed = 0.0

        dur = f"{int(elapsed // 3600):02d}:{int((elapsed % 3600) // 60):02d}:{int(elapsed % 60):02d}"

        def stat_row(label, value, vcp=CP_GOOD):
            nonlocal row
            if row >= y + h:
                return
            self._safe_addstr(row, x + 2, f"{label:<14}", curses.color_pair(CP_INFO))
            self._safe_addstr(row, x + 16, str(value)[:max(0, w - 18)],
                              curses.color_pair(vcp) | curses.A_BOLD)
            row += 1

        def section(label):
            nonlocal row
            row += 1
            if row >= y + h:
                return
            self._safe_addstr(row, x + 2, label[:max(0, w - 3)],
                              curses.color_pair(CP_INFO) | curses.A_BOLD)
            row += 1

        stat_row("Duration:", dur, CP_NORMAL)
        section("Device Counts")
        stat_row("WiFi APs:", stats.get("wifi_count", "—"))
        stat_row("BLE Devices:", stats.get("bt_count", "—"))
        stat_row("RF Devices:", stats.get("rf_count", "—"))

        section("Collectors")
        coll_display = [
            ("wifi",     "WiFi    (Kismet)"),
            ("sdr",      "SDR     (rtl_433)"),
            ("wideband", "Wideband SDR"),
            ("esp32",    "BLE     (ESP32)"),
            ("gps",      "GPS     (gpspipe)"),
        ]
        for cname, clabel in coll_display:
            if row >= y + h:
                break
            up   = collectors.get(cname, False)
            icon = "✓" if up else "✗"
            cp   = CP_GOOD if up else CP_BAD
            self._safe_addstr(row, x + 2, f" {icon} {clabel}"[:max(0, w - 3)],
                              curses.color_pair(cp))
            row += 1

        section("Web Explorer")
        if row < y + h:
            icon  = "✓" if webapp_up else "✗"
            cp    = CP_GOOD if webapp_up else CP_BAD
            label = "http://localhost:8000" if webapp_up else "not running"
            self._safe_addstr(row, x + 2, f" {icon} {label}"[:max(0, w - 3)],
                              curses.color_pair(cp))

    def _draw_help_panel(self, y, x, h, w):
        self._panel_title(y, x, w, " COMMANDS ")

        commands = [
            ("q / quit",    "stop session & exit"),
            ("",            ""),
            ("webapp on",   "start web explorer"),
            ("webapp off",  "stop web explorer"),
            ("",            ""),
            ("stop wifi",   "stop WiFi collector"),
            ("stop sdr",    "stop SDR collector"),
            ("stop ble",    "stop BLE collector"),
            ("stop gps",    "stop GPS collector"),
            ("",            ""),
            ("start wifi",  "restart WiFi"),
            ("start sdr",   "restart SDR"),
            ("start ble",   "restart BLE"),
            ("start gps",   "restart GPS"),
            ("",            ""),
            ("enrich",      "run post-enrichment"),
            ("",            ""),
            ("↑ / ↓",       "command history"),
            ("PgUp / PgDn", "scroll log"),
        ]

        row       = y + 1
        col_split = min(16, w // 2)

        for cmd, desc in commands:
            if row >= y + h:
                break
            if not cmd:
                row += 1
                continue
            self._safe_addstr(row, x + 1, f"{cmd:<{col_split - 1}}"[:col_split - 1],
                              curses.color_pair(CP_WARN) | curses.A_BOLD)
            desc_max = w - col_split - 2
            if desc_max > 0:
                self._safe_addstr(row, x + col_split, desc[:desc_max],
                                  curses.color_pair(CP_NORMAL))
            row += 1

    def _draw_log_panel(self, y, x, h, w):
        self._panel_title(y, x, w, " LOG ")

        with self.log_lock:
            lines = list(self.log_lines)

        disp_rows = h - 1
        total     = len(lines)
        scroll    = min(self.log_scroll, max(0, total - disp_rows))
        end_idx   = total - scroll
        start_idx = max(0, end_idx - disp_rows)
        visible   = lines[start_idx:end_idx]
        pad       = disp_rows - len(visible)

        for i, line in enumerate(visible):
            draw_y = y + 1 + pad + i
            if draw_y >= y + h:
                break
            llow = line.lower()
            if "error" in llow:
                cp = CP_BAD
            elif "warn" in llow:
                cp = CP_WARN
            elif line.startswith("[cmd]"):
                cp = CP_CMD
            elif line.startswith("[ui]"):
                cp = CP_INFO
            else:
                cp = CP_NORMAL
            self._safe_addstr(draw_y, x, line[:w - 1], curses.color_pair(cp))

        if scroll > 0:
            indicator = f" ↑ {scroll} lines  "
            self._safe_addstr(y + h - 1, max(0, x + w - len(indicator) - 1),
                              indicator, curses.color_pair(CP_WARN) | curses.A_BOLD)

    def _draw_cmd_input(self, y, w):
        prompt  = "CMD> "
        visible = self.cmd_input[:w - len(prompt) - 2]
        self._safe_addstr(y, 0, prompt, curses.color_pair(CP_INFO) | curses.A_BOLD)
        self._safe_addstr(y, len(prompt), visible, curses.color_pair(CP_NORMAL))
        try:
            self.stdscr.move(y, len(prompt) + len(visible))
        except curses.error:
            pass

    # ── Input handling ────────────────────────────────────────────────────────

    def handle_input(self, key: int):
        if key == -1:
            return

        if key in (curses.KEY_ENTER, ord("\n"), ord("\r")):
            cmd = self.cmd_input.strip()
            self.cmd_input = ""
            self.cmd_history_idx = -1
            if cmd:
                self.cmd_history.insert(0, cmd)
                self.handle_command(cmd)

        elif key in (curses.KEY_BACKSPACE, 127, 8):
            self.cmd_input = self.cmd_input[:-1]

        elif key == curses.KEY_UP:
            if self.cmd_history:
                self.cmd_history_idx = min(self.cmd_history_idx + 1,
                                           len(self.cmd_history) - 1)
                self.cmd_input = self.cmd_history[self.cmd_history_idx]

        elif key == curses.KEY_DOWN:
            self.cmd_history_idx -= 1
            if self.cmd_history_idx < 0:
                self.cmd_history_idx = -1
                self.cmd_input = ""
            else:
                self.cmd_input = self.cmd_history[self.cmd_history_idx]

        elif key == curses.KEY_PPAGE:
            self.log_scroll += 10

        elif key == curses.KEY_NPAGE:
            self.log_scroll = max(0, self.log_scroll - 10)

        elif key == 27:
            self.cmd_input = ""

        elif 32 <= key <= 126:
            self.cmd_input += chr(key)

    # ── Main loop ─────────────────────────────────────────────────────────────

    def run(self):
        curses.curs_set(1)
        self.stdscr.nodelay(True)
        self.stdscr.timeout(UI_TICK_MS)
        while self.running:
            self.draw()
            try:
                key = self.stdscr.getch()
            except curses.error:
                key = -1
            self.handle_input(key)


def main(stdscr):
    ui = WarDriveUI(stdscr)
    try:
        ui.run()
    finally:
        ui._stop_session()


if __name__ == "__main__":
    if os.geteuid() != 0:
        print("ERROR: wardrive_ui.py must run as root (use: sudo python3 wardrive_ui.py)")
        sys.exit(1)
    curses.wrapper(main)
