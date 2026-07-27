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
    embed.set_footer(text="ソース: 気象庁・環境省")
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
    embed.set_footer(text="ソース: 気象庁")
    return embed

EARLY_WARNING_COLOR = 0x4A89DC


def create_early_warning_embed(data: Dict[str, Any]) -> discord.Embed:
    head_title = data.get("head_title", "早期注意情報（明後日まで）")
    info_type = data.get("info_type", "")
    areas = data.get("areas", [])

    title = head_title
    if info_type and info_type != "発表":
        title += f" ({info_type})"

    embed = discord.Embed(
        title=title,
        color=EARLY_WARNING_COLOR,
        timestamp=utcnow(),
    )

    if info_type == "取消":
        embed.description = "この早期注意情報は取り消されました。"
        embed.set_footer(text="ソース: 気象庁")
        return embed

    if not areas:
        embed.description = "警報級の可能性が「高」または「中」の区域はありません。"
        embed.set_footer(text="ソース: 気象庁")
        return embed

    lines = []
    for area in areas:
        lines.append(f"**{area['name']}**")
        for kind in area["kinds"]:
            rank_groups: Dict[str, list] = {}
            for r in kind["ranks"]:
                rank_groups.setdefault(r["rank"], []).append(r["time_name"])
            parts = []
            for rank in ("高", "中"):
                if rank in rank_groups:
                    periods = ", ".join(rank_groups[rank])
                    parts.append(f"{rank}({periods})")
            lines.append(f"  {kind['type']}: {' / '.join(parts)}")
        lines.append("")

    desc = "\n".join(lines)
    if len(desc) > 4000:
        desc = desc[:3997] + "..."
    embed.description = desc
    embed.set_footer(text="ソース: 気象庁")
    return embed


RECORD_RAIN_COLOR = 0xB40000


def create_record_rain_embed(data: Dict[str, Any]) -> discord.Embed:
    head_title = data.get("head_title", "記録的短時間大雨情報")
    info_type = data.get("info_type", "")
    serial = data.get("serial", "")
    headline_text = data.get("headline_text", "")

    title = head_title
    if serial:
        title += f" (第{serial}報)"

    embed = discord.Embed(
        title=title,
        color=RECORD_RAIN_COLOR,
        timestamp=utcnow(),
    )

    lines = []
    if headline_text:
        lines.append(headline_text)
    if info_type and info_type != "発表":
        lines.append("")
        lines.append(f"情報種別: {info_type}")

    embed.description = "\n".join(lines) if lines else "詳細情報はありません。"
    embed.set_footer(text="ソース: 気象庁")
    return embed


FLOOD_COLORS = {
    5: 0x000000,
    4: 0x7A008A,
    3: 0xB40000,
    2: 0xDBAF00,
    1: 0x4A89DC,
}


def create_flood_forecast_embed(data: Dict[str, Any]) -> discord.Embed:
    head_title = data.get("head_title", "指定河川洪水予報")
    info_type = data.get("info_type", "")
    serial = data.get("serial", "")
    headline_text = data.get("headline_text", "")
    level = data.get("level", 0)
    main_texts = data.get("main_texts", [])
    affected_cities = data.get("affected_cities", [])
    rainfall_text = data.get("rainfall_text", "")
    rainfall_series = data.get("rainfall_series", [])
    water_stations = data.get("water_stations", [])

    title = head_title
    if serial:
        title += f" (第{serial}報)"

    color = FLOOD_COLORS.get(level, 0x4A89DC)

    embed = discord.Embed(
        title=title,
        color=color,
        timestamp=utcnow(),
    )

    lines = []

    if headline_text:
        lines.append(headline_text)
        lines.append("")

    if main_texts:
        lines.append("**[概要]**")
        for mt in main_texts:
            header = mt["station"]
            if mt["location"]:
                header += f"({mt['location']})"
            lines.append(f"{header}")
            lines.append(mt["text"])
            lines.append("")

    if affected_cities:
        lines.append(f"浸水想定地域: {', '.join(affected_cities)}")
        lines.append("")

    if rainfall_text or rainfall_series:
        lines.append("**[雨量情報]**")
        if rainfall_text:
            lines.append(rainfall_text)
        if rainfall_series:
            lines.append("")
            lines.append("流域平均雨量:")
            for rs in rainfall_series:
                lines.append(f"  {rs['label']}: {rs['value']}{rs['unit']}")
        lines.append("")

    if water_stations:
        lines.append("**[水位・流量情報]**")
        for ws in water_stations:
            header = ws["station"]
            if ws["location"]:
                header += f"({ws['location']})"
            lines.append(header)

            has_discharge = any(s.get("discharge") for s in ws["series"])

            for s in ws["series"]:
                parts = [s["label"]]
                if s.get("level_m"):
                    unit = s.get("unit", "m")
                    parts.append(f"{s['level_m']}{unit}")
                if s.get("level_rank"):
                    parts.append(f"レベル{s['level_rank']}")
                if has_discharge and s.get("discharge"):
                    d_unit = s.get("discharge_unit", "m3/s")
                    parts.append(f"{s['discharge']}{d_unit}")
                lines.append("  " + " / ".join(parts))
            lines.append("")

    if info_type and info_type != "発表":
        lines.append(f"情報種別: {info_type}")

    desc = "\n".join(lines)
    if len(desc) > 4000:
        desc = desc[:3997] + "..."
    embed.description = desc
    embed.set_footer(text="ソース: 気象庁")
    return embed


VOLCANO_ERUPTION_COLOR = 0xB40000


def create_volcano_eruption_embed(data: Dict[str, Any]) -> discord.Embed:
    head_title = data.get("head_title", "噴火速報")
    info_type = data.get("info_type", "")
    volcano_activity = data.get("volcano_activity", "")
    affected_areas = data.get("affected_areas", [])

    embed = discord.Embed(
        title=head_title,
        color=VOLCANO_ERUPTION_COLOR,
        timestamp=utcnow(),
    )

    lines = []
    if volcano_activity:
        lines.append(volcano_activity)
        lines.append("")

    if affected_areas:
        lines.append(f"対象市町村等: {', '.join(affected_areas)}")
        lines.append("")

    if info_type and info_type != "発表":
        lines.append(f"情報種別: {info_type}")

    embed.description = "\n".join(lines) if lines else "詳細情報はありません。"
    embed.set_footer(text="出典: 気象庁")
    return embed

VOLCANO_OBSERVATION_COLOR = 0xCC0000


def create_volcano_observation_embed(data: Dict[str, Any]) -> discord.Embed:
    head_title = data.get("head_title", "噴火に関する火山観測報")
    info_type = data.get("info_type", "")
    headline_text = data.get("headline_text", "")
    volcano_name = data.get("volcano_name", "")
    crater_name = data.get("crater_name", "")
    plume_height = data.get("plume_height", "")
    plume_direction = data.get("plume_direction", "")
    other_observation = data.get("other_observation", "")
    event_time = data.get("event_time", "")

    embed = discord.Embed(
        title=head_title,
        color=VOLCANO_OBSERVATION_COLOR,
        timestamp=utcnow(),
    )

    lines = []
    if volcano_name:
        lines.append(f"**火山名**: {volcano_name}")
    if event_time:
        lines.append(f"**日時**: {event_time}")

    if headline_text:
        lines.append("")
        lines.append(headline_text)

    if crater_name or plume_height or plume_direction or other_observation:
        lines.append("")
        lines.append("**【噴火の詳細】**")
        if crater_name:
            lines.append(f"火口: {crater_name}")
        if plume_height:
            lines.append(f"噴煙: {plume_height}")
        if plume_direction:
            lines.append(f"流向: {plume_direction}")
        if other_observation:
            lines.append("")
            lines.append("**【観測情報】**")
            lines.append(other_observation)

    if info_type and info_type != "発表":
        lines.append("")
        lines.append(f"情報種別: {info_type}")

    embed.description = "\n".join(lines) if lines else "詳細情報はありません。"
    embed.set_footer(text="出典: 気象庁")
    return embed
