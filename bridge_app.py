"""Configuration and sync entry points shared by the window and command line."""

import json
import os
import tempfile
import uuid
from pathlib import Path

from bridge_db import import_rows
from bridge_scan import codebuddy_history_root, scan
from codebuddy_archive import scan_codebuddy_archive
from desktop_presets import DESKTOP_APPS, REMOVED_PRESETS, desktop_sources


PRESETS_VERSION = 2


def local_app_data():
    return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")


def default_db_path():
    home = Path.home()
    roaming = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
    store = roaming / "com.ccswitch.desktop" / "app_paths.json"
    if store.is_file():
        try:
            options = json.loads(store.read_text(encoding="utf-8-sig"))
            override = options.get("app_config_dir_override") if isinstance(options, dict) else None
            if isinstance(override, str) and override.strip():
                folder = Path(override.strip()).expanduser()
                if folder.is_dir():
                    return folder.resolve() / "cc-switch.db"
        except (OSError, ValueError):
            pass
    db = home / ".cc-switch" / "cc-switch.db"
    legacy = os.environ.get("HOME", "").strip()
    if not db.exists() and legacy:
        older = Path(legacy) / ".cc-switch" / "cc-switch.db"
        if older.is_file():
            return older.resolve()
    return db


def default_config():
    logs = local_app_data() / "CodeBuddyExtension" / "Logs" / "CodeBuddyIDE"
    source = dict(id="codebuddy-ide", kind="codebuddy", name="CodeBuddy IDE",
                  app_type="codebuddy", root=str(logs), script="")
    return dict(db_path=str(default_db_path()), sources=[source, *desktop_sources()],
                auto_sync=False, desktop_presets_version=PRESETS_VERSION)


def config_path():
    return local_app_data() / "CCUsageBridge" / "config.json"


def _name(value, field):
    if not isinstance(value, str) or not value.strip() or not value.strip().isprintable():
        raise ValueError(f"{field}需要填写可显示的文字。")
    return value.strip()


def new_source(name, app_type, root, script):
    name, app_type = _name(name, "应用名称"), _name(app_type, "CC 应用标识")
    folder, scanner = Path(root).expanduser(), Path(script).expanduser()
    if not root or not folder.is_dir():
        raise ValueError("请选择存在的日志目录。")
    if not scanner.is_file() or scanner.suffix.lower() not in (".py", ".ps1"):
        raise ValueError("请选择本机的 .py 或 .ps1 扫描脚本。")
    return dict(id=str(uuid.uuid4()), name=name, app_type=app_type, kind="custom",
                root=str(folder.resolve()), script=str(scanner.resolve()))


def _validate(config):
    if not isinstance(config, dict) or not isinstance(config.get("sources"), list):
        raise ValueError("配置文件缺少应用列表。")
    if not isinstance(config.get("db_path"), str) or not config["db_path"].strip():
        raise ValueError("配置文件缺少 CC 数据库路径。")
    if type(config.get("auto_sync")) is not bool:
        raise ValueError("自动同步设置无效。")
    ids = set()
    for source in config["sources"]:
        if not isinstance(source, dict) or source.get("kind") not in ("codebuddy", "desktop", "custom"):
            raise ValueError("配置文件包含无法识别的扫描器。")
        if source["kind"] == "desktop" and source.get("preset") not in DESKTOP_APPS:
            raise ValueError("配置文件包含无法识别的桌面应用。")
        for field in ("id", "name", "app_type"):
            _name(source.get(field), field)
        if source["id"] in ids:
            raise ValueError("应用列表包含重复的配置 ID。")
        ids.add(source["id"])
        if not isinstance(source.get("root"), str) or not source["root"].strip():
            raise ValueError("应用缺少日志目录。")
        if source["kind"] == "custom" and (not isinstance(source.get("script"), str)
                                          or not source["script"].strip()):
            raise ValueError("自定义应用缺少扫描脚本。")
    return config


def load_config(path=None):
    location = Path(path) if path is not None else config_path()
    if path is None and not location.exists():
        return default_config()
    try:
        config = json.loads(location.read_text(encoding="utf-8-sig"))
        changed = False
        if isinstance(config, dict) and isinstance(config.get("sources"), list):
            kept = [s for s in config["sources"]
                    if not (isinstance(s, dict) and s.get("preset") in REMOVED_PRESETS)]
            if len(kept) != len(config["sources"]):
                config["sources"] = kept
                changed = True
        config = _validate(config)
        if path is None and "desktop_presets_version" not in config:
            existing = {s["app_type"].casefold() for s in config["sources"]}
            known_ids = {s["id"] for s in config["sources"]}
            config["sources"].extend(s for s in desktop_sources() if s["id"] not in known_ids
                                     and s["app_type"].casefold() not in existing)
            changed = True
        if path is None and config.get("desktop_presets_version") != PRESETS_VERSION:
            config["desktop_presets_version"] = PRESETS_VERSION
            changed = True
        if changed and path is None:
            save_config(config, location)
        return config
    except (ValueError, OSError) as exc:
        raise ValueError(f"无法读取助手配置 {location}。\n{exc}") from exc


def save_config(config, path=None):
    _validate(config)
    location = Path(path) if path is not None else config_path()
    location.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(dir=location.parent, prefix="config-", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            json.dump(config, out, ensure_ascii=False, indent=2)
            out.write("\n")
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, location)
    finally:
        Path(name).unlink(missing_ok=True)


def sync_source(db_path, source):
    path = Path(db_path)
    if not path.is_file():
        raise ValueError(f"找不到 CC Switch 数据库，请在助手中更换路径。\n{path}")
    archives = ()
    if source["kind"] == "codebuddy" and source.get("root"):
        root = Path(source["root"]).expanduser().resolve()
        archives = scan_codebuddy_archive(codebuddy_history_root(root))
    return import_rows(path, source, scan(source), archives)
