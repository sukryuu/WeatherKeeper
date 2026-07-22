import discord
from discord.utils import utcnow
from typing import Dict, Any
import logging

logger = logging.getLogger(__name__)

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
