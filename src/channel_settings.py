import json
import os
import logging
from typing import Optional, Dict, List

import config

logger = logging.getLogger(__name__)

CHANNEL_SETTINGS_FILE = os.path.join(config.DATA_DIR, "channel_settings.json")

CHANNEL_TYPES = {
    "alert": "警報・注意報",
}


def load_channel_settings() -> Dict[str, Dict[str, int]]:
    if not os.path.exists(CHANNEL_SETTINGS_FILE):
        return {}
    try:
        with open(CHANNEL_SETTINGS_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger.error(f"チャンネル設定の読み込みに失敗: {e}")
        return {}

    if not isinstance(raw, dict):
        logger.error("チャンネル設定ファイルの形式が不正です")
        return {}

    cleaned: Dict[str, Dict[str, int]] = {}
    for guild_id_str, types in raw.items():
        if not isinstance(types, dict):
            logger.warning(
                f"旧形式または不正なエントリをスキップ: key={guild_id_str} "
                f"（/channel set で再設定してください）"
            )
            continue
        cleaned[guild_id_str] = {
            ch_type: int(ch_id)
            for ch_type, ch_id in types.items()
            if isinstance(ch_id, int)
        }
    return cleaned


def save_channel_settings(settings: Dict[str, Dict[str, int]]) -> None:
    try:
        os.makedirs(config.DATA_DIR, exist_ok=True)
        with open(CHANNEL_SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
    except IOError as e:
        logger.error(f"チャンネル設定の保存に失敗: {e}")


def get_channel_id(guild_id: int, channel_type: str) -> Optional[int]:
    settings = load_channel_settings()
    guild_settings = settings.get(str(guild_id), {})
    if channel_type in guild_settings:
        return guild_settings[channel_type]

    if channel_type == "alert":
        return config.ALERT_CHANNEL_ID or None
    return None


def set_channel_id(guild_id: int, channel_type: str, channel_id: int) -> None:
    settings = load_channel_settings()
    guild_key = str(guild_id)
    if guild_key not in settings:
        settings[guild_key] = {}
    settings[guild_key][channel_type] = channel_id
    save_channel_settings(settings)
    logger.info(
        f"チャンネル設定を更新: guild={guild_id}, {channel_type} -> {channel_id}"
    )


def get_all_channels(channel_type: str) -> List[int]:
    settings = load_channel_settings()
    channel_ids: List[int] = []
    seen = set()
    for types in settings.values():
        ch_id = types.get(channel_type)
        if ch_id is not None and ch_id not in seen:
            seen.add(ch_id)
            channel_ids.append(ch_id)

    if not channel_ids:
        if channel_type == "alert" and config.ALERT_CHANNEL_ID:
            channel_ids.append(config.ALERT_CHANNEL_ID)

    return channel_ids


def get_guild_settings(guild_id: int) -> Dict[str, int]:
    settings = load_channel_settings()
    return settings.get(str(guild_id), {})
