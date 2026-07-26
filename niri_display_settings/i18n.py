"""Tiny built-in translation table (English base, Simplified Chinese)."""

import locale
import os

_ZH = {
    "Display Settings": "Niri显示设置",
    "Apply": "应用",
    "Refresh state": "刷新状态",
    "Arrangement": "排列",
    "Drag monitors to rearrange them": "拖动显示器以调整排列",
    "Monitor": "显示器",
    "Enabled": "启用此显示器",
    "Resolution": "分辨率",
    "Refresh Rate": "刷新率",
    "Scale": "缩放",
    "Transform": "旋转",
    "Variable Refresh Rate": "可变刷新率 (VRR)",
    "Focus at startup": "启动时聚焦",
    "Only one monitor can be focused at startup": "启动时聚焦的显示器全局唯一",
    "Off": "关闭",
    "On": "开启",
    "On demand": "按需 (on-demand)",
    "Enable VRR only when an application requests it": "仅在应用请求时启用 VRR",
    "Normal": "无旋转",
    "Configuration File": "配置文件",
    "Output blocks are written to this file": "output 配置块将写入此文件",
    "Choose…": "选择…",
    "Choose config file": "选择配置文件",
    "This file is not included from config.kdl": "此文件未被 include 进 config.kdl",
    "Fix": "修复",
    "Keep these display settings?": "保留这些显示设置吗？",
    "Settings will revert in {n} seconds.": "{n} 秒后将自动恢复原设置。",
    "Revert": "恢复",
    "Keep": "保留",
    "Settings reverted": "已恢复原设置",
    "Settings applied and saved": "设置已应用并保存",
    "Backup saved as {path}": "备份已保存为 {path}",
    "Validation failed": "配置校验失败",
    "The config file was not modified. niri reported:": "配置文件未被修改。niri 报错：",
    "Config restored from backup. niri reported:": "已从备份恢复配置。niri 报错：",
    "Failed to apply preview": "实时预览失败",
    "Cannot disable the last enabled monitor": "不能禁用最后一台启用的显示器",
    "Include line added to config.kdl (backup: {path})": "已在 config.kdl 加入 include（备份：{path}）",
    "No changes to apply": "没有需要应用的修改",
    "niri is not running or not reachable": "niri 未运行或无法连接",
    "Not connected": "未连接",
    "_transform_90": "90°",
    "_transform_180": "180°",
    "_transform_270": "270°",
    "_transform_flipped": "翻转",
    "_transform_flipped-90": "翻转 + 90°",
    "_transform_flipped-180": "翻转 + 180°",
    "_transform_flipped-270": "翻转 + 270°",
}

_EN_SPECIAL = {
    "_transform_90": "90°",
    "_transform_180": "180°",
    "_transform_270": "270°",
    "_transform_flipped": "Flipped",
    "_transform_flipped-90": "Flipped + 90°",
    "_transform_flipped-180": "Flipped + 180°",
    "_transform_flipped-270": "Flipped + 270°",
}


def _is_zh() -> bool:
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        v = os.environ.get(var)
        if v:
            return v.lower().startswith("zh")
    try:
        loc = locale.getlocale()[0]
        return bool(loc) and loc.lower().startswith("zh")
    except Exception:
        return False


_ZH_ACTIVE = _is_zh()


def _(s: str) -> str:
    if _ZH_ACTIVE:
        return _ZH.get(s, _EN_SPECIAL.get(s, s))
    return _EN_SPECIAL.get(s, s)
