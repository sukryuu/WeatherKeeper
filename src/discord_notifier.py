import discord
from discord.utils import utcnow
from typing import Dict, Any
import logging

logger = logging.getLogger(__name__)

COMMENTARY_COLOR = 0x0070C0

LABELS = {
    5: "特別警報",
    4: "危険警報",
    3: "警報",
    2: "注意報",
}

COLORS = {
    5: 0x000000,  # 黒
    4: 0x7A008A,  # 紫
    3: 0x8A0000,  # 赤
    2: 0xC9A100,  # 黄色
}


def create_warning_embed(data: Dict[str, Any]) -> discord.Embed:
    grouped_alerts = data.get("grouped_alerts", {})
    headline_text = data.get("headline_text", "")
    head_title = data.get("head_title", "")

    max_level = 2
    for base, levels in grouped_alerts.items():
        for lv in levels.keys():
            if lv > max_level:
                max_level = lv

    embed = discord.Embed(
        title=f"{head_title or data.get('control_title', '気象警報・注意報')}",
        color=COLORS.get(max_level, COLORS[2]),
        timestamp=utcnow(),
    )

    lines = []
    if headline_text:
        lines.append(headline_text)
        lines.append("")

    if not grouped_alerts:
        if not headline_text:
            embed.description = "詳細情報はありません。"
        else:
            embed.description = "\n".join(lines)
        return embed

    for base, levels in grouped_alerts.items():
        for lv in sorted(levels.keys(), reverse=True):
            label = LABELS.get(lv, "注意報")
            for status, areas in levels[lv].items():
                status_text = f"({status})"
                lines.append(f"**レベル{lv} {base}{label}{status_text}**")
                lines.append(", ".join(areas))
                lines.append("")

    embed.description = "\n".join(lines)
    return embed


def wbgt_risk(value: int) -> str:
    if value >= 31:
        return "危険"
    elif value >= 28:
        return "厳重警戒"
    elif value >= 25:
        return "警戒"
    return "注意"


def create_heatstroke_embed(data: Dict[str, Any]) -> discord.Embed:
    area_name = data.get("area_name", "")
    info_type = data.get("info_type", "発表")
    target_label = data.get("target_label", "")
    call_to_action = data.get("call_to_action", "")
    wbgt = data.get("wbgt_readings", [])
    temps = data.get("temp_readings", [])

    title = f"熱中症警戒アラート: {area_name}"
    if info_type and info_type != "発表":
        title += f" ({info_type})"

    embed = discord.Embed(
        title=title,
        color=0x9400D3,
        timestamp=utcnow(),
    )

    lines = []
    if target_label:
        lines.append(f"**対象日: {target_label}**")
        lines.append("")
    if call_to_action:
        lines.append(call_to_action)
        lines.append("")
    if wbgt:
        lines.append("**日最高暑さ指数(WBGT)予測**")
        for place, value in wbgt:
            lines.append(f"{place}: {value} ({wbgt_risk(value)})")
        lines.append("")
    if temps:
        lines.append("**予想最高気温**")
        for place, value in temps:
            lines.append(f"{place}: {value}度")
        lines.append("")

    embed.description = "\n".join(lines)
    embed.set_footer(text="出典: 気象庁・環境省 熱中症警戒アラート")
    return embed


def create_commentary_embed(data: Dict[str, Any]) -> discord.Embed:
    head_title = data.get("head_title", "")
    scope = data.get("scope", "")
    headline_text = data.get("headline_text", "")
    tags = data.get("tags", [])
    overview = data.get("overview", "")
    disaster = data.get("disaster_matters", "")
    additional = data.get("additional", "")
    comment = data.get("comment_text", "")
    obs = data.get("observations", [])
    fc = data.get("forecasts", [])

    embed = discord.Embed(
        title=head_title or f"{scope}気象解説情報",
        color=COMMENTARY_COLOR,
        timestamp=utcnow(),
    )

    lines = []
    if tags:
        lines.append("対象現象: " + " / ".join(tags))
        lines.append("")
    if headline_text:
        lines.append(headline_text)
        lines.append("")

    if comment:
        lines.append(comment)
    else:
        if overview:
            lines.append("**[概況]**")
            lines.append(overview)
            lines.append("")
        if disaster:
            lines.append("**[防災事項]**")
            lines.append(disaster)
            lines.append("")
        if obs:
            lines.append("**[降水量の実況]**")
            for o in obs[:5]:
                lines.append(f"{o['station']}: {o['value']}")
            lines.append("")
        if fc:
            lines.append("**[雨量の予想]**")
            for f in fc:
                if f.get("element"):
                    lines.append(f["element"])
                for p in f.get("periods", []):
                    lines.append(f"・{p['label']}: " + " / ".join(p["areas"]))
                lines.append("")
        if additional:
            lines.append("**[補足]**")
            lines.append(additional)

    desc = "\n".join(lines)
    if len(desc) > 4000:
        desc = desc[:3997] + "..."
    embed.description = desc
    embed.set_footer(text="出典: 気象庁")
    return embed
