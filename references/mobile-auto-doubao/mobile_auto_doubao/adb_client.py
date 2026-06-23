import base64
import subprocess
import time
from pathlib import Path

from .constants import DEFAULT_ADB, DEFAULT_SERIAL


class AdbError(RuntimeError):
    """Raised when an adb command fails or no device can be resolved."""
    pass


class AdbClient:
    def __init__(self, adb: str = DEFAULT_ADB, serial: str | None = DEFAULT_SERIAL):
        """Create an adb client bound to a binary path and optional serial."""
        self.adb = adb
        self.serial = serial

    def command(self, args: list[str], check: bool = True, text: bool = True) -> subprocess.CompletedProcess:
        """Run an adb command and optionally raise when it exits non-zero."""
        command = [self.adb]
        if self.serial:
            command.extend(["-s", self.serial])
        command.extend(args)
        if text:
            result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
        else:
            result = subprocess.run(command, capture_output=True, text=False)
        if check and result.returncode != 0:
            stderr = result.stderr if isinstance(result.stderr, str) else ""
            stdout = result.stdout if isinstance(result.stdout, str) else ""
            raise AdbError(stderr.strip() or stdout.strip() or f"adb failed: {command}")
        return result

    def devices(self) -> list[str]:
        """Return the list of connected adb device serials."""
        result = subprocess.run([self.adb, "devices"], capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)
        devices = []
        for line in result.stdout.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "device":
                devices.append(parts[0])
        return devices

    def resolve_serial(self) -> str:
        """Resolve and cache the active device serial."""
        if self.serial:
            return self.serial
        devices = self.devices()
        if not devices:
            raise AdbError("No connected adb device found.")
        if len(devices) > 1:
            raise AdbError(
                "Multiple adb devices are connected. "
                "Pass an explicit serial with --serial or put device.serial in the task JSON. "
                f"Available devices: {', '.join(devices)}"
            )
        self.serial = devices[0]
        return self.serial

    def tap(self, x: int, y: int) -> None:
        """Tap the given screen coordinate."""
        self.command(["shell", "input", "tap", str(x), str(y)])

    def keyevent(self, code: int) -> None:
        """Send an Android key event code."""
        self.command(["shell", "input", "keyevent", str(code)])

    def scroll_down(self, x1: int = 540, y1: int = 1800, x2: int = 540, y2: int = 850, duration_ms: int = 700) -> dict:
        """Scroll downward, falling back to PAGE_DOWN when swipe injection fails."""
        result = self.command(["shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration_ms)], check=False)
        if result.returncode == 0:
            return {"method": "swipe", "fallback": False}
        self.keyevent(93)  # PAGE_DOWN
        return {
            "method": "keyevent",
            "fallback": True,
            "error": (result.stderr or result.stdout or "").strip(),
        }

    def scroll_up(self, x1: int = 540, y1: int = 520, x2: int = 540, y2: int = 2050, duration_ms: int = 650) -> dict:
        """Scroll upward, falling back to PAGE_UP when swipe injection fails."""
        result = self.command(["shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration_ms)], check=False)
        if result.returncode == 0:
            return {"method": "swipe", "fallback": False}
        self.keyevent(92)  # PAGE_UP
        return {
            "method": "keyevent",
            "fallback": True,
            "error": (result.stderr or result.stdout or "").strip(),
        }

    def text(self, value: str) -> None:
        """Type text via the standard adb input text command."""
        escaped = value.replace("%", "%s").replace(" ", "%s")
        self.command(["shell", "input", "text", escaped])

    def broadcast_text(self, value: str) -> None:
        """Send text through the ADB keyboard broadcast hook."""
        self.command(["shell", "am", "broadcast", "-a", "ADB_INPUT_TEXT", "--es", "msg", value])

    def broadcast_base64_text(self, value: str) -> None:
        """Send UTF-8 text through the ADB keyboard base64 hook."""
        encoded = base64.b64encode(value.encode("utf-8")).decode("ascii")
        self.command(["shell", "am", "broadcast", "-a", "ADB_INPUT_B64", "--es", "msg", encoded])

    def broadcast_clear_text(self) -> None:
        """Clear the focused input through the ADB keyboard broadcast hook."""
        self.command(["shell", "am", "broadcast", "-a", "ADB_CLEAR_TEXT"])

    def list_imes(self) -> list[str]:
        """List installed input methods."""
        result = self.command(["shell", "ime", "list", "-s"])
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    def current_ime(self) -> str:
        """Return the currently selected input method."""
        return self.command(["shell", "settings", "get", "secure", "default_input_method"], check=False).stdout.strip()

    def set_ime(self, ime: str) -> None:
        """Switch the active input method."""
        self.command(["shell", "ime", "set", ime])

    def dump_xml(self) -> str:
        """Dump the current UI hierarchy as XML text."""
        remote = "/sdcard/mobile-auto-doubao-window.xml"
        last_error: Exception | None = None
        for _ in range(3):
            try:
                self.command(["shell", "uiautomator", "dump", remote])
                xml = self.command(["shell", "cat", remote]).stdout
                if xml and "<hierarchy" in xml:
                    return xml
                last_error = AdbError("uiautomator dump did not produce valid hierarchy xml")
            except Exception as exc:
                last_error = exc
                cat_result = self.command(["shell", "cat", remote], check=False)
                xml = cat_result.stdout or ""
                if "<hierarchy" in xml:
                    return xml
            time.sleep(0.3)
        raise AdbError(str(last_error) if last_error else "uiautomator dump failed")

    def screenshot(self, path: str | Path) -> bool:
        """Capture a screenshot to the given path."""
        result = self.command(["exec-out", "screencap", "-p"], check=False, text=False)
        if result.returncode != 0:
            return False
        Path(path).write_bytes(result.stdout)
        return True

    def current_focus(self) -> str:
        """Return the window-manager focus summary."""
        result = self.command(["shell", "dumpsys", "window"], check=False)
        lines = [line.strip() for line in result.stdout.splitlines() if "mCurrentFocus" in line or "mFocusedApp" in line]
        return "\n".join(lines)

    def start_app(self, package: str) -> None:
        """Launch the given package from the launcher."""
        self.command(["shell", "monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1"])
