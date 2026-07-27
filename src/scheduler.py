import discord
from discord import TextChannel
from discord.ext import tasks
import json
import os
import io
import logging
import hashlib
import asyncio
import functools
from datetime import datetime, timezone
from typing import Set, Dict, Any

import config
from src.jmaxml_client import fetch_atom_feed, fetch_xml_content
from src.jmaxml_parser import (
    parse_warning_xml,
    parse_heatstroke_xml,
    parse_commentary_xml,
    parse_early_warning_xml,
    parse_record_rain_xml,
    parse_flood_forecast_xml,
)
from src.discord_notifier import (
    create_warning_embed,
    create_heatstroke_embed,
    create_commentary_embed,
    create_early_warning_embed,
    create_record_rain_embed,
    create_flood_forecast_embed,
)
from src.channel_settings import get_all_channels
from src.warning_map import create_warning_map_image, get_map_executor

logger = logging.getLogger(__name__)


def make_content_hash(parsed_data: Dict[str, Any]) -> str:
    grouped_alerts = parsed_data.get("grouped_alerts", {})
    content_parts = []
    for base, levels in grouped_alerts.items():
        for lv, statuses in levels.items():
            for status, areas in statuses.items():
                sorted_areas = sorted(areas)
                content_parts.append(f"{base}:{lv}:{status}:{','.join(sorted_areas)}")
    content_str = "|".join(sorted(content_parts))
    return hashlib.sha256(content_str.encode("utf-8")).hexdigest()


def make_heatstroke_hash(parsed_data: Dict[str, Any]) -> str:
    parts = [
        parsed_data.get("area_name", ""),
        parsed_data.get("target_datetime", ""),
        parsed_data.get("info_type", ""),
    ]
    for place, value in parsed_data.get("wbgt_readings", []):
        parts.append(f"wbgt:{place}:{value}")
    for place, value in parsed_data.get("temp_readings", []):
        parts.append(f"temp:{place}:{value}")
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def make_commentary_hash(parsed_data: Dict[str, Any]) -> str:
    parts = [
        parsed_data.get("head_title", ""),
        parsed_data.get("target_datetime", ""),
        parsed_data.get("info_type", ""),
        parsed_data.get("headline_text", ""),
        parsed_data.get("overview", ""),
        parsed_data.get("disaster_matters", ""),
        parsed_data.get("comment_text", ""),
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def make_early_warning_hash(parsed_data: Dict[str, Any]) -> str:
    parts = [
        parsed_data.get("head_title", ""),
        parsed_data.get("target_datetime", ""),
        parsed_data.get("info_type", ""),
    ]
    for area in parsed_data.get("areas", []):
        for kind in area.get("kinds", []):
            for r in kind.get("ranks", []):
                parts.append(
                    f"{area['name']}:{kind['type']}:{r['time_id']}:{r['rank']}"
                )
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def make_record_rain_hash(parsed_data: Dict[str, Any]) -> str:
    parts = [
        parsed_data.get("head_title", ""),
        parsed_data.get("target_datetime", ""),
        parsed_data.get("info_type", ""),
        parsed_data.get("serial", ""),
        parsed_data.get("headline_text", ""),
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def make_flood_forecast_hash(parsed_data: Dict[str, Any]) -> str:
    parts = [
        parsed_data.get("head_title", ""),
        parsed_data.get("target_datetime", ""),
        parsed_data.get("info_type", ""),
        parsed_data.get("serial", ""),
        parsed_data.get("headline_text", ""),
    ]
    for mt in parsed_data.get("main_texts", []):
        parts.append(f"{mt.get('station', '')}:{mt.get('text', '')}")
    for ws in parsed_data.get("water_stations", []):
        for s in ws.get("series", []):
            parts.append(
                f"{ws['station']}:{s['time_id']}:{s.get('level_m', '')}:{s.get('level_rank', '')}"
            )
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


class WeatherScheduler:
    def __init__(self, bot: discord.Client):
        self.bot = bot
        self.processed_events: Set[str] = set()
        self.notified_hashes: Set[str] = set()
        self.last_check_time: str = ""
        self.load_state()
        self.check_warnings.start()

    def load_state(self):
        if os.path.exists(config.PROCESSED_EVENTS_FILE):
            try:
                with open(config.PROCESSED_EVENTS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.processed_events = set(data.get("events", []))
                    self.notified_hashes = set(data.get("hashes", []))
                    self.last_check_time = data.get("last_check", "")
                logger.info(
                    f"状態を読み込みました: "
                    f"イベント {len(self.processed_events)} 件, "
                    f"ハッシュ {len(self.notified_hashes)} 件"
                )
            except (json.JSONDecodeError, IOError) as e:
                logger.error(f"状態の読み込みに失敗しました: {e}")
        else:
            self.last_check_time = datetime.now(timezone.utc).isoformat()
            logger.info(
                f"初回起動のため、現在時刻を最終チェック時刻に設定: {self.last_check_time}"
            )

    def save_state(self):
        events_list = list(self.processed_events)[-500:]
        hashes_list = list(self.notified_hashes)[-500:]
        try:
            os.makedirs(config.DATA_DIR, exist_ok=True)
            with open(config.PROCESSED_EVENTS_FILE, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "events": events_list,
                        "hashes": hashes_list,
                        "last_check": self.last_check_time,
                        "updated": datetime.now(timezone.utc).isoformat(),
                    },
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
        except IOError as e:
            logger.error(f"状態の保存に失敗しました: {e}")

    @tasks.loop(minutes=1)
    async def check_warnings(self):
        urls_to_check = [config.JMA_ATOM_REGULAR_URL]
        for url in urls_to_check:
            entries = await asyncio.to_thread(fetch_atom_feed, url)
            if not entries:
                continue
            for entry in entries:
                title = entry["title"]
                entry_updated = entry.get("updated", "")
                entry_link = entry.get("link", "")

                if "時系列" in title:
                    continue

                is_warning = "気象警報・注意報（Ｒ０６）" in title
                is_heatstroke = "熱中症警戒アラート" in title
                is_early_warning = "早期注意情報" in title
                is_record_rain = "記録的短時間大雨情報" in title
                is_flood = "指定河川洪水予報" in title
                is_commentary = (
                    not is_warning
                    and not is_heatstroke
                    and not is_early_warning
                    and not is_record_rain
                    and not is_flood
                    and "気象情報" in title
                )
                if not (
                    is_warning
                    or is_heatstroke
                    or is_early_warning
                    or is_record_rain
                    or is_flood
                    or is_commentary
                ):
                    continue

                key_source = f"{title}|{entry_updated}" if entry_updated else title
                unique_key = hashlib.sha256(key_source.encode("utf-8")).hexdigest()

                if unique_key in self.processed_events:
                    continue

                if self.last_check_time and entry_updated:
                    if entry_updated <= self.last_check_time:
                        self.processed_events.add(unique_key)
                        continue

                logger.info(f"新しい情報を検出: {title}")

                if is_heatstroke:
                    await self._process_heatstroke(entry_link, unique_key)
                    continue

                if is_early_warning:
                    await self._process_early_warning(entry_link, unique_key)
                    continue

                if is_record_rain:
                    await self._process_record_rain(entry_link, unique_key)
                    continue

                if is_flood:
                    await self._process_flood_forecast(entry_link, unique_key)
                    continue

                if is_commentary:
                    await self._process_commentary(entry_link, unique_key)
                    continue

                xml_content = await asyncio.to_thread(fetch_xml_content, entry_link)
                if not xml_content:
                    self.processed_events.add(unique_key)
                    continue

                parsed_data = parse_warning_xml(xml_content)
                if not parsed_data:
                    self.processed_events.add(unique_key)
                    continue

                grouped_alerts = parsed_data.get("grouped_alerts", {})

                has_significant = any(
                    lv >= 2
                    for levels in grouped_alerts.values()
                    for lv in levels.keys()
                )
                if not has_significant:
                    self.processed_events.add(unique_key)
                    self.save_state()
                    continue

                if config.WARNING_TARGET_AREAS:
                    filtered_grouped = {}
                    for base, levels in grouped_alerts.items():
                        filtered_levels = {}
                        for lv, statuses in levels.items():
                            filtered_statuses = {}
                            for status, areas in statuses.items():
                                filtered_areas = [
                                    area
                                    for area in areas
                                    if any(
                                        t["name"] in area or t["code"] in area
                                        for t in config.WARNING_TARGET_AREAS
                                    )
                                ]
                                if filtered_areas:
                                    filtered_statuses[status] = filtered_areas
                            if filtered_statuses:
                                filtered_levels[lv] = filtered_statuses
                        if filtered_levels:
                            filtered_grouped[base] = filtered_levels
                    grouped_alerts = filtered_grouped

                if not grouped_alerts:
                    self.processed_events.add(unique_key)
                    self.save_state()
                    continue

                parsed_data["grouped_alerts"] = grouped_alerts
                content_hash = make_content_hash(parsed_data)

                if content_hash in self.notified_hashes:
                    logger.info(f"同じ内容の通知が既に送信済みのためスキップ: {title}")
                    self.processed_events.add(unique_key)
                    self.save_state()
                    continue

                embed = create_warning_embed(parsed_data)

                loop = asyncio.get_running_loop()
                image_bytes = await loop.run_in_executor(
                    get_map_executor(),
                    functools.partial(
                        create_warning_map_image,
                        area_levels=parsed_data.get("area_levels", {}),
                        title=parsed_data.get(
                            "head_title", "気象警報・注意報 発表範囲"
                        ),
                    ),
                )
                if image_bytes:
                    embed.set_image(url="attachment://warning_map.png")

                sent_any = False
                for ch_id in get_all_channels("alert"):
                    channel = self.bot.get_channel(ch_id)
                    if not isinstance(channel, TextChannel):
                        logger.warning(f"警報チャンネルが見つかりません: ID={ch_id}")
                        continue
                    try:
                        if image_bytes:
                            file = discord.File(
                                io.BytesIO(image_bytes), filename="warning_map.png"
                            )
                            await channel.send(embed=embed, file=file)
                        else:
                            await channel.send(embed=embed)
                        sent_any = True
                    except discord.DiscordException as e:
                        logger.error(f"Discord送信に失敗しました: channel={ch_id}, {e}")

                if sent_any:
                    logger.info(f"警報通知を送信しました: {title}")

                self.processed_events.add(unique_key)
                self.notified_hashes.add(content_hash)
                self.save_state()

            if entries:
                valid_updates = [
                    e.get("updated", "") for e in entries if e.get("updated", "")
                ]
                if valid_updates:
                    latest_updated = max(valid_updates)
                    if latest_updated > self.last_check_time:
                        self.last_check_time = latest_updated
                        self.save_state()

    async def _process_heatstroke(self, entry_link: str, unique_key: str):
        xml_content = await asyncio.to_thread(fetch_xml_content, entry_link)
        if not xml_content:
            self.processed_events.add(unique_key)
            return

        parsed_data = parse_heatstroke_xml(xml_content)
        if not parsed_data:
            self.processed_events.add(unique_key)
            return

        content_hash = make_heatstroke_hash(parsed_data)
        if content_hash in self.notified_hashes:
            logger.info("同じ内容の熱中症警戒アラートが送信済みのためスキップ")
            self.processed_events.add(unique_key)
            self.save_state()
            return

        embed = create_heatstroke_embed(parsed_data)

        loop = asyncio.get_running_loop()
        image_bytes = await loop.run_in_executor(
            get_map_executor(),
            functools.partial(
                create_warning_map_image,
                area_levels={},
                heatstroke_area_names=[parsed_data.get("area_name", "")],
                heatstroke_special=parsed_data.get("is_special", False),
                title=f"熱中症警戒アラート: {parsed_data.get('area_name', '')}",
            ),
        )
        if image_bytes:
            embed.set_image(url="attachment://heatstroke_map.png")

        sent_any = False
        for ch_id in get_all_channels("heatstroke"):
            channel = self.bot.get_channel(ch_id)
            if not isinstance(channel, TextChannel):
                logger.warning(f"警報チャンネルが見つかりません: ID={ch_id}")
                continue
            try:
                if image_bytes:
                    file = discord.File(
                        io.BytesIO(image_bytes), filename="heatstroke_map.png"
                    )
                    await channel.send(embed=embed, file=file)
                else:
                    await channel.send(embed=embed)
                sent_any = True
            except discord.DiscordException as e:
                logger.error(f"熱中症警戒アラート送信失敗: channel={ch_id}, {e}")

        if sent_any:
            logger.info(
                f"熱中症警戒アラートを送信しました: {parsed_data.get('area_name')}"
            )

        self.processed_events.add(unique_key)
        self.notified_hashes.add(content_hash)
        self.save_state()

    async def _process_early_warning(self, entry_link: str, unique_key: str):
        xml_content = await asyncio.to_thread(fetch_xml_content, entry_link)
        if not xml_content:
            self.processed_events.add(unique_key)
            return

        parsed_data = parse_early_warning_xml(xml_content)
        if not parsed_data:
            self.processed_events.add(unique_key)
            return

        content_hash = make_early_warning_hash(parsed_data)
        if content_hash in self.notified_hashes:
            self.processed_events.add(unique_key)
            self.save_state()
            return

        embed = create_early_warning_embed(parsed_data)

        sent_any = False
        for ch_id in get_all_channels("early_warning"):
            channel = self.bot.get_channel(ch_id)
            if not isinstance(channel, TextChannel):
                logger.warning(f"早期注意情報チャンネルが見つかりません: ID={ch_id}")
                continue
            try:
                await channel.send(embed=embed)
                sent_any = True
            except discord.DiscordException as e:
                logger.error(f"早期注意情報送信失敗: channel={ch_id}, {e}")

        if sent_any:
            logger.info(f"早期注意情報を送信しました: {parsed_data.get('head_title')}")

        self.processed_events.add(unique_key)
        self.notified_hashes.add(content_hash)
        self.save_state()

    async def _process_record_rain(self, entry_link: str, unique_key: str):
        xml_content = await asyncio.to_thread(fetch_xml_content, entry_link)
        if not xml_content:
            self.processed_events.add(unique_key)
            return

        parsed_data = parse_record_rain_xml(xml_content)
        if not parsed_data:
            self.processed_events.add(unique_key)
            return

        content_hash = make_record_rain_hash(parsed_data)
        if content_hash in self.notified_hashes:
            self.processed_events.add(unique_key)
            self.save_state()
            return

        embed = create_record_rain_embed(parsed_data)

        sent_any = False
        for ch_id in get_all_channels("alert"):
            channel = self.bot.get_channel(ch_id)
            if not isinstance(channel, TextChannel):
                logger.warning(f"警報チャンネルが見つかりません: ID={ch_id}")
                continue
            try:
                await channel.send(embed=embed)
                sent_any = True
            except discord.DiscordException as e:
                logger.error(f"記録的短時間大雨情報送信失敗: channel={ch_id}, {e}")

        if sent_any:
            logger.info(
                f"記録的短時間大雨情報を送信しました: {parsed_data.get('head_title')}"
            )

        self.processed_events.add(unique_key)
        self.notified_hashes.add(content_hash)
        self.save_state()

    async def _process_flood_forecast(self, entry_link: str, unique_key: str):
        xml_content = await asyncio.to_thread(fetch_xml_content, entry_link)
        if not xml_content:
            self.processed_events.add(unique_key)
            return

        parsed_data = parse_flood_forecast_xml(xml_content)
        if not parsed_data:
            self.processed_events.add(unique_key)
            return

        content_hash = make_flood_forecast_hash(parsed_data)
        if content_hash in self.notified_hashes:
            self.processed_events.add(unique_key)
            self.save_state()
            return

        embed = create_flood_forecast_embed(parsed_data)

        sent_any = False
        for ch_id in get_all_channels("alert"):
            channel = self.bot.get_channel(ch_id)
            if not isinstance(channel, TextChannel):
                logger.warning(f"警報チャンネルが見つかりません: ID={ch_id}")
                continue
            try:
                await channel.send(embed=embed)
                sent_any = True
            except discord.DiscordException as e:
                logger.error(f"指定河川洪水予報送信失敗: channel={ch_id}, {e}")

        if sent_any:
            logger.info(
                f"指定河川洪水予報を送信しました: {parsed_data.get('head_title')}"
            )

        self.processed_events.add(unique_key)
        self.notified_hashes.add(content_hash)
        self.save_state()

    async def _process_commentary(self, entry_link: str, unique_key: str):
        xml_content = await asyncio.to_thread(fetch_xml_content, entry_link)
        if not xml_content:
            self.processed_events.add(unique_key)
            return

        parsed_data = parse_commentary_xml(xml_content)
        if not parsed_data:
            self.processed_events.add(unique_key)
            return

        content_hash = make_commentary_hash(parsed_data)
        if content_hash in self.notified_hashes:
            self.processed_events.add(unique_key)
            self.save_state()
            return

        embed = create_commentary_embed(parsed_data)

        sent_any = False
        for ch_id in get_all_channels("commentary"):
            channel = self.bot.get_channel(ch_id)
            if not isinstance(channel, TextChannel):
                logger.warning(f"解説情報チャンネルが見つかりません: ID={ch_id}")
                continue
            try:
                await channel.send(embed=embed)
                sent_any = True
            except discord.DiscordException as e:
                logger.error(f"気象解説情報送信失敗: channel={ch_id}, {e}")

        if sent_any:
            logger.info(f"気象解説情報を送信しました: {parsed_data.get('head_title')}")

        self.processed_events.add(unique_key)
        self.notified_hashes.add(content_hash)
        self.save_state()

    @check_warnings.before_loop
    async def _before_check_warnings(self):
        await self.bot.wait_until_ready()

    def stop(self):
        self.check_warnings.cancel()
