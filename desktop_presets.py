"""Built-in Windows application sources for the local usage assistant."""

import os
from pathlib import Path
from scan_agents_cn import scan_marvis
from scan_office_cn import scan_qianwen
from scan_coding_cn import scan_qoder


HOME = Path.home()
ROAMING = Path(os.environ.get("APPDATA") or HOME / "AppData" / "Roaming")

# Each source can only sync when backed by an adapter or an explicitly chosen script.
DESKTOP_APPS = {
    "trae-cn": ("TRAE CN", ROAMING / "Trae CN" / "logs", "可读日志里未找到逐次 Token。企业成员可从「账户中心 → 个人用量」导出历史明细；使用自定义模型的调用可查服务商用量。"),
    "marvis": ("Marvis", HOME / ".marvis" / "database", "读取本机 Marvis 用量记录。"),
    "doubao": ("豆包", Path(os.environ.get("LOCALAPPDATA") or HOME / "AppData" / "Local") / "Doubao" / "User Data", "可在豆包「设置 → 订阅与额度管理」查看额度。可读日志和任务轨迹中尚未找到实际 Token。"),
    "qianwen-office": ("千问办公", HOME / ".qwenworkcn" / "logs", "已完成调用有正数 Token 时可同步。本机内置服务的这些字段均为 0。企业自有模型的用量可在对应服务商查看。"),
    "qoder-cn": ("Qoder CN", HOME / ".qoder-cn" / "projects", "仅计入带有明确 Token 数的调用。本机已核对到上下文压缩用量，普通聊天还会记录积分。"),
}

ADAPTERS = {"marvis": scan_marvis, "qianwen-office": scan_qianwen, "qoder-cn": scan_qoder}

# Presets the helper does not offer.
REMOVED_PRESETS = frozenset({"zcode"})


def desktop_sources():
    return [dict(id=key, preset=key, kind="desktop", name=name, app_type=name,
                 root=str(root), script="") for key, (name, root, _) in DESKTOP_APPS.items()]


def can_sync(source):
    return (source["kind"] != "desktop" or bool(source.get("script"))
            or source["preset"] in ADAPTERS)


def source_note(source):
    if source["kind"] == "codebuddy":
        return "在 CC 的「用量统计 → CodeBuddy」中，选「全部」日期查看热力图。只有逐次明细会出现在请求日志中。"
    if source["kind"] == "desktop" and not source.get("script"):
        return DESKTOP_APPS[source["preset"]][2]
    return ""
