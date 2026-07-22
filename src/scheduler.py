import discord
from discord import TextChannel
from discord.ext import tasks
import json
import os
import logging
import hashlib
import asyncio
from datetime import datetime, timezone
from typing import Set, Dict, Any

import config
from src.jmaxml_client import fetch_atom_feed, fetch_xml_content
from src.jmaxml_parser import parse_warning_xml
from src.discord_notifier import create_warning_embed
from src.channel_settings import get_all_channels

logger = logging.getLogger(__name__)


def make_content_hash(parsed_data: Dict[str, Any]) -> str:
    """通知内容の実質的なハッシュを生成する"""
    grouped_alerts = parsed_data.get("grouped_alerts", {})
    content_parts = []
    for base, levels in grouped_alerts.items():
        for lv, statuses in levels.items():
            for status, areas in statuses.items():
                sorted_areas = sorted(areas)
                content_parts.append(f"{base}:{lv}:{status}:{','.join(sorted_areas)}")
    content_str = "|".join(sorted(content_parts))
    return hashlib.sha256(content_str.encode("utf-8")).hexdigest()


class WeatherScheduler:
    def __init__(self, bot: discord.Client):
        self.bot = bot
        self.processed_events: Set[str] = set()
        self.notified_hashes: Set[str] = set()
        self.last_check_time: str = ""
        self.load_state()

        self.check_warnings.start()

    # ==========================================
    # 状態の保存・復元
    # ==========================================
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

    # ==========================================
    # 警報・注意報の監視 (1分ごと)
    # ==========================================
    @tasks.loop(minutes=1)
    async def check_warnings(self):
        """1分ごとに気象庁Atomフィードをチェックし、新しい警報・注意報を通知する"""
        urls_to_check = [config.JMA_ATOM_REGULAR_URL]

        for url in urls_to_check:
            entries = await asyncio.to_thread(fetch_atom_feed, url)
            if not entries:
                continue

            for entry in entries:
                title = entry["title"]
                entry_updated = entry.get("updated", "")
                entry_link = entry.get("link", "")

                if "気象警報・注意報（Ｒ０６）" not in title:
                    continue
                if "時系列" in title:
                    continue

                key_source = f"{title}|{entry_updated}" if entry_updated else title
                unique_key = hashlib.sha256(key_source.encode("utf-8")).hexdigest()

                if unique_key in self.processed_events:
                    continue

                if self.last_check_time and entry_updated:
                    if entry_updated <= self.last_check_time:
                        self.processed_events.add(unique_key)
                        continue

                logger.info(f"新しい警報情報を検出: {title}")

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
                        # 1ギルド失敗しても他ギルドへは送り続ける
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

    # ==========================================
    # before_loop
    # ==========================================
    @check_warnings.before_loop
    async def _before_check_warnings(self):
        await self.bot.wait_until_ready()

    def stop(self):
        self.check_warnings.cancel()
