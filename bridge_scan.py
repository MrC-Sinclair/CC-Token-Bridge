"""Read CodeBuddy IDE step logs and run user-selected scanner scripts."""

import contextlib
import json
import os
import re
import runpy
import shutil
import subprocess
import sys
import tempfile
import traceback
from datetime import date, datetime
from pathlib import Path
from desktop_presets import ADAPTERS, source_note


_STEP = re.compile(
    rb"^\[([^\]]+)\] \[[^\]]*\] \[BaseAgent:[^\]]*\] "
    rb"\[[^\]]*\]\s*notifyStepEnd,"
)
_OUTPUT_STEP = re.compile(
    rb"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+) \[[^\]]*\] "
    rb"\[BaseAgent:[^\]]*\] \[[^\]]*\]\s*notifyStepEnd,"
)
_USAGE = json.JSONDecoder()
_TOKEN_FIELDS = ("input", "output", "cache_read", "cache_write")


def _parse_step(line, pattern=_STEP):
    header = pattern.match(line)
    if not header or not line.endswith(b"\n"):
        return None
    identifiers, separator, tail = line[header.end():].partition(b", usage:")
    if not separator:
        return None
    message = next(
        (field.strip()[10:].strip() for field in identifiers.split(b",")
         if field.strip().startswith(b"messageId:")), b""
    )
    if message in (b"", b"null", b"undefined"):
        return None
    try:
        usage, _ = _USAGE.raw_decode(tail.lstrip().decode("utf-8"))
        if not isinstance(usage, dict):
            return None
        prompt, output = usage["inputTokens"], usage["outputTokens"]
        read, write = usage.get("cacheTokens", 0), usage.get("cachedWriteTokens", 0)
        if any(type(n) is not int or n < 0 for n in (prompt, output, read, write)):
            return None
        if prompt == output == 0:
            return None
        stamp = header[1].decode("ascii").replace("/", "-")
        timestamp = int(datetime.fromisoformat(stamp).timestamp())
        return dict(id=message.decode("utf-8"), timestamp=timestamp,
                    input=max(0, prompt - read - write), output=output,
                    cache_read=read, cache_write=write, model="unknown")
    except (ValueError, UnicodeError, KeyError, OverflowError):
        return None


def codebuddy_history_root(root):
    local = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    extension = (local / "CodeBuddyExtension").resolve()
    if root == extension / "Logs" / "CodeBuddyIDE":
        return extension / "Data"
    return root


def _codebuddy_logs(root):
    if root.is_dir():
        for day in sorted(root.iterdir()):
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day.name) or not day.is_dir():
                continue
            try:
                date.fromisoformat(day.name)
            except ValueError:
                continue
            for path in sorted(day.glob("*.log")):
                yield path, _STEP
    local = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    roaming = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    output_root = roaming / "CodeBuddy CN" / "logs"
    # Pair the native directories only for the default profile. Custom imported
    # folders must never silently pull in the current machine's application logs.
    if root == (local / "CodeBuddyExtension" / "Logs" / "CodeBuddyIDE").resolve():
        root = output_root
    for path in sorted(root.glob("*/window*/exthost/Tencent-Cloud.coding-copilot/*.log")):
        yield path, _OUTPUT_STEP


def _codebuddy(root):
    seen = set()
    found_file = False
    for path, pattern in _codebuddy_logs(root):
        if not path.is_file():
            continue
        found_file = True
        try:
            with path.open("rb") as reader:
                for line in reader:
                    step = _parse_step(line, pattern)
                    if step is not None and step["id"] not in seen:
                        seen.add(step["id"])
                        yield step
        except FileNotFoundError:
            # The IDE can rotate a file between enumeration and opening.
            continue
    if not found_file and not root.is_dir() and not codebuddy_history_root(root).is_dir():
        raise ValueError(f"找不到日志目录：{root}")


def run_python(script, root, output_path, error_path):
    """Run a .py scanner in a bundled windowed child with UTF-8 streams."""
    with open(output_path, "w", encoding="utf-8") as out, open(
        error_path, "w", encoding="utf-8"
    ) as err, contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            sys.argv = [script, root]
            sys.path.insert(0, str(Path(script).parent))
            runpy.run_path(script, run_name="__main__")
        except SystemExit as exc:
            if exc.code is None or isinstance(exc.code, int):
                return exc.code or 0
            print(exc.code, file=err)
            return 1
        except Exception:
            traceback.print_exc(file=err)
            return 1
    return 0


def _run_script(script, root, output_path, error_path):
    env = os.environ.copy()
    env.update(PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    kind = script.suffix.casefold()
    bundled = False
    if kind == ".py":
        frozen = getattr(sys, "frozen", False)
        interpreter = shutil.which("python.exe") if frozen else sys.executable
        # Windows' App Installer alias can open the Store instead of running a script.
        if frozen and (not interpreter or "windowsapps" in Path(interpreter).as_posix().casefold().split("/")):
            bundled = True
            command = [sys.executable, "--run-python", script, root, output_path, error_path]
        else:
            command = [interpreter, script, root]
    elif kind == ".ps1":
        shell = shutil.which("powershell.exe") or shutil.which("pwsh")
        if not shell:
            raise ValueError("未找到 PowerShell。")
        # Static command, with user-selected paths passed exclusively as values.
        # Preserve the machine's configured PowerShell execution policy.
        env.update(CCTOKEN_SCAN_SCRIPT=str(script), CCTOKEN_SCAN_ROOT=str(root))
        command = [shell, "-NoProfile", "-NonInteractive", "-Command",
                   "$ErrorActionPreference='Stop';"
                   "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false);"
                   "& $env:CCTOKEN_SCAN_SCRIPT $env:CCTOKEN_SCAN_ROOT;"]
    else:
        raise ValueError("扫描脚本应为 .py 或 .ps1 文件。")
    with contextlib.ExitStack() as files:
        # Windowed packaged Python writes to its own files (sys.stdout is None).
        out = subprocess.DEVNULL if bundled else files.enter_context(output_path.open("wb"))
        err = subprocess.DEVNULL if bundled else files.enter_context(error_path.open("wb"))
        result = subprocess.run(command, cwd=script.parent, env=env, stdin=subprocess.DEVNULL,
                                stdout=out, stderr=err, check=False,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode:
        detail = ""
        if error_path.is_file():
            with error_path.open("rb") as errors:
                errors.seek(max(0, errors.seek(0, os.SEEK_END) - 2048))
                detail = errors.read().decode("utf-8", errors="replace").strip()
        raise ValueError(f"扫描脚本退出码 {result.returncode}。{detail}")


def _event(value):
    if not isinstance(value, dict):
        raise ValueError("每行应为一个 JSON 对象。")
    identity = value.get("id")
    if not isinstance(identity, str) or not identity.strip():
        raise ValueError("id 应为稳定且非空的字符串。")
    stamp = value.get("timestamp")
    if type(stamp) is not int or not 0 <= stamp <= 253402300799:
        raise ValueError("timestamp 应为 Unix 时间戳（秒）。")
    row = {"id": identity, "timestamp": stamp}
    for field in _TOKEN_FIELDS:
        n = value.get(field) if field in ("input", "output") else value.get(field, 0)
        if type(n) is not int or not 0 <= n <= 2**32 - 1:
            raise ValueError(f"{field} 应为 0 到 4294967295 之间的整数。")
        row[field] = n
    model = value.get("model") or "unknown"
    if not isinstance(model, str):
        raise ValueError("model 应为字符串。")
    row["model"] = model
    return row


def scan(source):
    """Yield token usage only. Stable IDs are preserved for database deduplication."""
    if not source.get("root"):
        raise ValueError("请选择日志目录。")
    root = Path(source["root"]).expanduser().resolve()
    if source["kind"] == "codebuddy":
        yield from _codebuddy(root)
        return
    if not root.is_dir():
        raise ValueError(f"找不到日志目录：{root}")
    if source["kind"] == "desktop" and not source.get("script"):
        scanner = ADAPTERS.get(source["preset"])
        if scanner is None:
            raise ValueError(source_note(source))
        yield from scanner(root)
        return
    if source["kind"] not in ("custom", "desktop"):
        raise ValueError(f"未知扫描方式：{source['kind']}")
    script = Path(source["script"]).expanduser().resolve()
    if not script.is_file():
        raise ValueError(f"找不到扫描脚本：{script}")
    with tempfile.TemporaryDirectory(prefix="cc-token-scan-") as temp:
        output_path, error_path = Path(temp) / "usage.jsonl", Path(temp) / "stderr.txt"
        _run_script(script, root, output_path, error_path)
        with output_path.open("rb") as reader:
            for index, raw in enumerate(reader, 1):
                if not raw.strip():
                    continue
                try:
                    yield _event(json.loads(raw.decode("utf-8-sig")))
                except (UnicodeError, ValueError) as exc:
                    raise ValueError(f"扫描脚本输出第 {index} 行无效：{exc}") from exc
