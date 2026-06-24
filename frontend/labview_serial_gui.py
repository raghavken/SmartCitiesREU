#!/usr/bin/env python3
"""Python-served Arduino dual-motor PWM serial GUI.

Run:
    python3 labview_serial_gui.py

Install Arduino serial support:
    python3 -m pip install -r requirements.txt

The GUI opens in your browser. Python owns the serial connection; the browser is
only the interface layer, which avoids platform-specific desktop GUI issues.
"""

from __future__ import annotations

import argparse
import json
import queue
import socket
import threading
import time
import webbrowser
from collections import deque
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

try:
    import serial
    from serial.tools import list_ports
except Exception:  # pragma: no cover - lets the demo UI run without pyserial.
    serial = None
    list_ports = None


DEFAULT_BAUD = 115200
DEFAULT_INTERVAL_MS = 20
SAMPLE_INTERVAL_MS = 1000
DEFAULT_TIMEOUT_MS = 500
MAX_POINTS = 240


@dataclass
class SerialConfig:
    port: str
    baud: int
    command: str
    interval_ms: int
    timeout_ms: int
    line_ending: str


class SerialWorker(threading.Thread):
    """Write newline-terminated motor PWM commands and read reply lines."""

    def __init__(self, config: SerialConfig, events: queue.Queue[dict[str, Any]]):
        super().__init__(daemon=True)
        self.config = config
        self.events = events
        self._stop_event = threading.Event()
        self._serial: Optional["serial.Serial"] = None
        self._command = config.command
        self._command_lock = threading.Lock()

    def stop(self) -> None:
        self._stop_event.set()
        if self._serial is not None:
            try:
                self._serial.close()
            except Exception:
                pass

    def set_command(self, command: str) -> None:
        with self._command_lock:
            self._command = command

    def command(self) -> str:
        with self._command_lock:
            return self._command

    def run(self) -> None:
        if serial is None or self.config.port.startswith("DEMO"):
            reason = "pyserial is not installed; running simulated Arduino data."
            if serial is not None:
                reason = "No serial port selected; running simulated Arduino data."
            self._run_demo(reason)
            return

        try:
            serial_kwargs = {
                "port": self.config.port,
                "baudrate": self.config.baud,
                "timeout": max(self.config.timeout_ms, 1) / 1000,
                "write_timeout": max(self.config.timeout_ms, 1) / 1000,
                "exclusive": True,
            }
            try:
                self._serial = serial.Serial(**serial_kwargs)
            except TypeError:
                serial_kwargs.pop("exclusive")
                self._serial = serial.Serial(**serial_kwargs)
            time.sleep(1.8)  # Many Arduino boards reset when the port opens.
            self.events.put({"type": "status", "message": f"Connected to {self.config.port} at {self.config.baud} baud"})
        except Exception as exc:
            self.events.put({"type": "error", "message": f"Could not open {self.config.port}: {exc}"})
            return

        delay_s = max(self.config.interval_ms, 1) / 1000
        sample_delay_s = SAMPLE_INTERVAL_MS / 1000
        last_tx_event = 0.0
        last_tx_command: Optional[str] = None
        last_sample_event = 0.0

        while not self._stop_event.is_set():
            try:
                now = time.time()
                command = self.command()
                if command:
                    payload = f"{command}\n".encode("utf-8")
                    self._serial.write(payload)
                    self._serial.flush()
                    if command != last_tx_command or now - last_tx_event >= 1.0:
                        self.events.put({"type": "tx", "value": command, "raw": payload.decode("utf-8", errors="replace"), "ts": now})
                        last_tx_command = command
                        last_tx_event = now

                raw = self._serial.readline()
                text = raw.decode("utf-8", errors="replace").strip()
                if text:
                    now = time.time()
                    if now - last_sample_event >= sample_delay_s:
                        self.events.put({"type": "rx", "raw": text, "ts": now})
                        last_sample_event = now
            except Exception as exc:
                self.events.put({"type": "error", "message": f"Serial error: {exc}"})
                break

            self._stop_event.wait(delay_s)

        try:
            if self._serial is not None:
                self._serial.close()
        except Exception:
            pass
        finally:
            self.events.put({"type": "status", "message": "Serial loop stopped"})

    def _run_demo(self, reason: str) -> None:
        self.events.put({"type": "status", "message": reason})
        delay_s = max(self.config.interval_ms, 20) / 1000
        last_rx_event = 0.0
        while not self._stop_event.is_set():
            now = time.time()
            if now - last_rx_event >= SAMPLE_INTERVAL_MS / 1000:
                self.events.put({"type": "rx", "raw": f"demo echo: {self.command()}", "ts": now})
                last_rx_event = now
            self._stop_event.wait(delay_s)
        self.events.put({"type": "status", "message": "Demo loop stopped"})


class AppState:
    def __init__(self) -> None:
        self.events: queue.Queue[dict[str, Any]] = queue.Queue()
        self.samples: deque[dict[str, Any]] = deque(maxlen=MAX_POINTS)
        self.worker: Optional[SerialWorker] = None
        self.running = False
        self.status = "Select a serial port, then press Connect."
        self.command_text = "electric_motor_pwm=0,internal_combustion_engine_pwm=0"
        self.last_tx = ""
        self.last_rx = ""
        self.lock = threading.Lock()

    def ports(self) -> list[dict[str, str]]:
        if list_ports is None:
            return [{"device": "DEMO", "description": "Demo mode - install pyserial for Arduino ports"}]
        ports = [
            {"device": port.device, "description": port.description or port.device}
            for port in list_ports.comports()
        ]
        ports.sort(key=self._port_sort_key)
        if not ports:
            ports.append({"device": "DEMO", "description": "Demo mode - no serial ports found"})
        return ports

    @staticmethod
    def _port_sort_key(port: dict[str, str]) -> tuple[int, str]:
        device = port["device"].lower()
        description = port["description"].lower()
        text = f"{device} {description}"
        if "usbmodem" in text or "usbserial" in text or "arduino" in text:
            return (0, device)
        if "bluetooth" in text or "debug-console" in text:
            return (2, device)
        return (1, device)

    def start(self, config: SerialConfig) -> dict[str, Any]:
        old_worker: Optional[SerialWorker]
        with self.lock:
            old_worker = self.worker
            if old_worker is not None:
                old_worker.stop()
                self.worker = None
                self.running = False
        if old_worker is not None:
            old_worker.join(timeout=1.0)
        with self.lock:
            self._clear_pending_events()
            self.samples.clear()
            self.status = "Opening serial resource..."
            self.command_text = config.command
            self.last_tx = ""
            self.last_rx = ""
            self.running = True
            self.worker = SerialWorker(config, self.events)
            self.worker.start()
        return {"ok": True, "status": self.status}

    def _clear_pending_events(self) -> None:
        while True:
            try:
                self.events.get_nowait()
            except queue.Empty:
                break

    def set_command(self, value: str) -> dict[str, Any]:
        with self.lock:
            self.command_text = value
            if self.worker is not None:
                self.worker.set_command(value)
        return {"ok": True, "command": self.command_text}

    def stop(self) -> dict[str, Any]:
        old_worker: Optional[SerialWorker]
        with self.lock:
            old_worker = self.worker
            if old_worker is not None:
                old_worker.stop()
                self.worker = None
            self.running = False
            self.status = "Stop requested."
        if old_worker is not None:
            old_worker.join(timeout=1.0)
        return {"ok": True, "status": self.status}

    def drain_events(self) -> dict[str, Any]:
        drained: list[dict[str, Any]] = []
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            drained.append(event)
            if event["type"] == "rx":
                self.samples.append(event)
                self.last_rx = str(event.get("raw", ""))
            elif event["type"] == "tx":
                self.last_tx = str(event.get("raw", ""))
            elif event["type"] in {"status", "error"}:
                self.status = event["message"]
                if event["message"].endswith("stopped") or event["type"] == "error":
                    self.running = False

        return {
            "events": drained,
            "running": self.running,
            "status": self.status,
            "command": self.command_text,
            "lastTx": self.last_tx,
            "lastRx": self.last_rx,
            "samples": list(self.samples),
        }


STATE = AppState()


class LabviewRequestHandler(BaseHTTPRequestHandler):
    server_version = "DualMotorPwmConsole/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{self.address_string()} - {fmt % args}")

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            self._send_html(INDEX_HTML)
        elif path == "/api/ports":
            self._send_json({"ports": STATE.ports()})
        elif path == "/api/events":
            self._send_json(STATE.drain_events())
        else:
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/connect":
            try:
                body = self._read_json()
                config = SerialConfig(
                    port=str(body.get("port", "DEMO")),
                    baud=int(body.get("baud", DEFAULT_BAUD)),
                    command=str(body.get("command", "")),
                    interval_ms=int(body.get("intervalMs", DEFAULT_INTERVAL_MS)),
                    timeout_ms=int(body.get("timeoutMs", DEFAULT_TIMEOUT_MS)),
                    line_ending=str(body.get("lineEnding", "lf")),
                )
            except Exception as exc:
                self._send_json({"ok": False, "error": f"Invalid serial settings: {exc}"}, HTTPStatus.BAD_REQUEST)
                return
            self._send_json(STATE.start(config))
        elif path == "/api/stop":
            self._send_json(STATE.stop())
        elif path in {"/api/command", "/api/pwm"}:
            try:
                body = self._read_json()
                value = str(body.get("command", body.get("value", ""))).strip()
                if not value:
                    raise ValueError("command cannot be empty")
            except Exception as exc:
                self._send_json({"ok": False, "error": f"Invalid command: {exc}"}, HTTPStatus.BAD_REQUEST)
                return
            self._send_json(STATE.set_command(value))
        else:
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))

    def _send_html(self, html: str) -> None:
        data = html.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


INDEX_HTML = Path(__file__).with_name("index.html").read_text(encoding="utf-8")


def choose_port(preferred: int) -> int:
    if preferred != 0:
        return preferred
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Arduino dual-motor PWM serial GUI.")
    parser.add_argument("--host", default="127.0.0.1", help="Host/interface to bind. Default: 127.0.0.1")
    parser.add_argument("--port", type=int, default=0, help="HTTP port. Default: choose a free port")
    parser.add_argument("--no-browser", action="store_true", help="Do not automatically open the browser")
    args = parser.parse_args()

    port = choose_port(args.port)
    server = ThreadingHTTPServer((args.host, port), LabviewRequestHandler)
    url = f"http://{args.host}:{port}/"
    print(f"Arduino dual-motor PWM serial GUI running at {url}")
    print("Press Ctrl+C to stop.")
    if not args.no_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        STATE.stop()
        server.server_close()
        print("\nServer stopped.")


if __name__ == "__main__":
    main()
