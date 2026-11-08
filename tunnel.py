"""Windows TUN tunnel integration for PULSEWAVE. Requires an elevated process."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any


def is_windows_admin() -> bool:
    if os.name != "nt":
        return False
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def build_tun_config(link: str, interface_name: str = "PULSEWAVE") -> dict[str, Any]:
    # Reuse the parser/outbound implementation already used by the checker.
    from multi_tester import parse_proxy_link, is_supported_server, build_outbound

    data = parse_proxy_link(link.strip())
    if not is_supported_server(data):
        raise ValueError("Конфигурация не поддерживается или заполнена не полностью.")

    outbound = build_outbound(data, "proxy")
    return {
        "log": {"level": "info", "timestamp": True},
        "dns": {
            "servers": [
                {"type": "https", "tag": "dns-remote", "server": "1.1.1.1", "detour": "proxy"}
            ],
            "final": "dns-remote",
            "strategy": "prefer_ipv4"
        },
        "inbounds": [
            {
                "type": "tun",
                "tag": "tun-in",
                "interface_name": interface_name,
                "address": ["172.19.0.1/30"],
                "auto_route": True,
                "strict_route": True
            }
        ],
        "outbounds": [outbound, {"type": "direct", "tag": "direct"}, {"type": "block", "tag": "block"}],
        "route": {
            "auto_detect_interface": True,
            "final": "proxy",
            "rules": [
                {"action": "sniff"},
                {"protocol": "dns", "action": "hijack-dns"}
            ]
        }
    }


class TunnelController:
    def __init__(self, singbox_exe: Path, config_path: Path, log_path: Path):
        self.singbox_exe = Path(singbox_exe)
        self.config_path = Path(config_path)
        self.log_path = Path(log_path)
        self.process: subprocess.Popen | None = None
        self._log_stream = None

    @property
    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def prepare(self, link: str) -> None:
        if not self.singbox_exe.is_file():
            raise FileNotFoundError(f"Не найден Sing-box: {self.singbox_exe}")
        config = build_tun_config(link)
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        check = subprocess.run(
            [str(self.singbox_exe), "check", "-c", str(self.config_path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
        if check.returncode != 0:
            details = "\\n".join(part.strip() for part in (check.stdout, check.stderr) if part and part.strip())
            details = details or "Неизвестная ошибка проверки конфигурации"
            raise RuntimeError(f"Sing-box отклонил конфигурацию:\n{details}")

    def start(self, link: str) -> None:
        if self.is_running:
            raise RuntimeError("Туннель уже запущен.")
        if not is_windows_admin():
            raise PermissionError("Для TUN-подключения требуется запуск от имени администратора.")
        self.prepare(link)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_stream = self.log_path.open("a", encoding="utf-8", buffering=1)
        self._log_stream.write("\\n=== PULSEWAVE TUN startup ===\\n")
        self._log_stream.flush()
        try:
            self.process = subprocess.Popen(
                [str(self.singbox_exe), "run", "-c", str(self.config_path)],
                cwd=str(self.singbox_exe.parent), stdout=self._log_stream,
                stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
            )
            # Sing-box can reject a configuration or fail to create the adapter
            # immediately after process creation. Detect that before reporting success.
            import time
            time.sleep(0.8)
            code = self.process.poll()
            if code is not None:
                self.process = None
                self._log_stream.flush()
                try:
                    details = self.log_path.read_text(encoding="utf-8", errors="replace")[-7000:]
                except OSError:
                    details = "Не удалось прочитать журнал Sing-box."
                raise RuntimeError(
                    f"Sing-box завершился сразу после запуска (код {code}).\\n"
                    f"Подробности в {self.log_path.name}:\\n{details[-5000:]}"
                )
        except Exception:
            if self._log_stream:
                try:
                    self._log_stream.close()
                except Exception:
                    pass
                self._log_stream = None
            raise

    def stop(self) -> None:
        proc = self.process
        if proc is None:
            return
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        self.process = None
        if self._log_stream:
            try:
                self._log_stream.close()
            except Exception:
                pass
            self._log_stream = None
