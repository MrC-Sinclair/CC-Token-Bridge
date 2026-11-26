"""Start CC 用量助手, or perform one manual sync from the command line."""

import argparse
import json
import sys
from pathlib import Path

from bridge_app import load_config, sync_source
from bridge_history import read_days, read_history
from bridge_scan import run_python
from desktop_presets import can_sync, source_note


def sync(config):
    report = {"sources": [], "ok": True}
    for source in config["sources"]:
        result = {"name": source["name"], "id": source["id"]}
        if can_sync(source):
            try:
                result.update(sync_source(config["db_path"], source))
            except Exception as exc:
                result["error"] = str(exc)
                report["ok"] = False
        else:
            result.update(status="unavailable", reason=source_note(source))
        report["sources"].append(result)
    return report


def history(config, daily):
    sources = config["sources"]
    if daily is not None:
        value = daily.strip().casefold()
        matches = [s for s in sources if s["id"].casefold() == value]
        if not matches:
            matches = [s for s in sources if value in (s["app_type"].casefold(), s["name"].casefold())]
        if len(matches) != 1:
            raise ValueError("请用 --history 查看应用列表，再为 --daily 指定一个应用 ID。")
        source = matches[0]
        days = read_days(config["db_path"], source)
        return {"ok": True, "id": source["id"], "name": source["name"], "days": [d._asdict() for d in days]}
    totals = read_history(config["db_path"], sources)
    return {"ok": True, "sources": [dict(id=s["id"], name=s["name"], **totals[s["id"]]._asdict())
                                    for s in sources]}


def main():
    if sys.argv[1:2] == ["--run-python"]:
        return run_python(*sys.argv[2:])
    if len(sys.argv) == 1:
        from bridge_ui import launch
        launch()
        return 0

    parser = argparse.ArgumentParser(description="CC 用量助手")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--sync", action="store_true", help="扫描并同步配置中的应用")
    mode.add_argument("--history", action="store_true", help="查询 CC 已保存的各应用累计用量")
    mode.add_argument("--daily", metavar="应用", help="查询一个应用每天已保存的请求数和 Token")
    parser.add_argument("--config", type=Path, help="指定助手配置文件")
    parser.add_argument("--result", type=Path, help="将 JSON 写入文件，源码版默认输出到终端")
    args = parser.parse_args()
    if args.result is None and sys.stdout is None:
        parser.error("窗口版 EXE 请用 --result 指定 JSON 输出文件。")
    try:
        config = load_config(args.config)
        report = sync(config) if args.sync else history(config, args.daily)
    except Exception as exc:
        report = {"ok": False, "error": str(exc)}
    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.result is not None:
        args.result.write_text(output, encoding="utf-8")
    else:
        print(output)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
