import discord
from discord import app_commands, TextChannel
import logging
import os
import io
import asyncio

import config
from src.scheduler import WeatherScheduler
from src.jmaxml_parser import (
    parse_warning_xml,
    parse_heatstroke_xml,
    parse_commentary_xml,
    parse_early_warning_xml,
)
from src.discord_notifier import (
    create_warning_embed,
    create_heatstroke_embed,
    create_commentary_embed,
    create_early_warning_embed,
)
from src.warning_map import create_warning_map_image
from src.channel_settings import (
    CHANNEL_TYPES,
    get_channel_id,
    set_channel_id,
    get_guild_settings,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger(__name__)
logger.propagate = False

intents = discord.Intents.default()
bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)
scheduler = None

SAMPLE_ALERT_FILE = os.path.join(config.DATA_DIR, "sample_alert.xml")
SAMPLE_HEATSTROKE_FILE = os.path.join(config.DATA_DIR, "sample_heatstroke.xml")
SAMPLE_COMMENTARY_FILE = os.path.join(config.DATA_DIR, "sample_commentary.xml")
SAMPLE_EARLY_WARNING_FILE = os.path.join(config.DATA_DIR, "sample_early_warning.xml")


async def setup_hook():
    await tree.sync()
    logger.info("スラッシュコマンドを自動同期しました。")


bot.setup_hook = setup_hook


@bot.event
async def on_ready():
    global scheduler
    logger.info(f"Botがログインしました: {bot.user}")

    if scheduler is None:
        scheduler = WeatherScheduler(bot)
        logger.info("スケジューラーを開始しました。")


channel_group = app_commands.Group(name="channel", description="通知チャンネルの設定")


@channel_group.command(
    name="set",
    description="このチャンネルを指定種の通知先に設定します",
)
@app_commands.describe(
    type="通知種別 (alert=警報・注意報, heatstroke=熱中症警戒アラート, commentary=気象解説情報)"
)
@app_commands.choices(
    type=[
        app_commands.Choice(name="警報・注意報", value="alert"),
        app_commands.Choice(name="熱中症警戒アラート", value="heatstroke"),
        app_commands.Choice(name="気象解説情報", value="commentary"),
        app_commands.Choice(name="早期注意情報", value="early_warning"),
    ]
)
async def channel_set(
    interaction: discord.Interaction,
    type: app_commands.Choice[str],
):
    if not isinstance(interaction.channel, TextChannel):
        await interaction.response.send_message(
            "このコマンドはテキストチャンネルで実行してください。",
            ephemeral=True,
        )
        return

    if not interaction.permissions.manage_channels:
        await interaction.response.send_message(
            "このコマンドの実行には「チャンネルの管理」権限が必要です。",
            ephemeral=True,
        )
        return

    guild_id = interaction.guild_id
    if guild_id is None:
        await interaction.response.send_message(
            "このコマンドはサーバー内で実行してください。",
            ephemeral=True,
        )
        return

    channel_type = type.value
    channel_id = interaction.channel.id

    set_channel_id(guild_id, channel_type, channel_id)

    type_label = CHANNEL_TYPES.get(channel_type, channel_type)
    await interaction.response.send_message(
        f"{type_label} の通知先を {interaction.channel.mention} に設定しました。",
        ephemeral=True,
    )


@channel_group.command(
    name="show",
    description="現在の通知チャンネル設定を表示します",
)
async def channel_show(interaction: discord.Interaction):
    if not interaction.permissions.manage_channels:
        await interaction.response.send_message(
            "このコマンドの実行には「チャンネルの管理」権限が必要です。",
            ephemeral=True,
        )
        return

    guild_id = interaction.guild_id
    if guild_id is None:
        await interaction.response.send_message(
            "このコマンドはサーバー内で実行してください。",
            ephemeral=True,
        )
        return

    guild_settings = get_guild_settings(guild_id)

    lines = ["**通知チャンネル設定（このサーバー）**", ""]
    for key, label in CHANNEL_TYPES.items():
        ch_id = guild_settings.get(key)
        if ch_id:
            ch = interaction.client.get_channel(ch_id)
            if isinstance(ch, TextChannel):
                lines.append(f"{label}: {ch.mention} (ID: {ch_id})")
            else:
                lines.append(f"{label}: 不明なチャンネル (ID: {ch_id})")
        else:
            fallback_id = get_channel_id(guild_id, key)
            if fallback_id:
                ch = interaction.client.get_channel(fallback_id)
                if isinstance(ch, TextChannel):
                    lines.append(
                        f"{label}: {ch.mention} (ID: {fallback_id}) [config.py]"
                    )
                else:
                    lines.append(f"{label}: 未設定")
            else:
                lines.append(f"{label}: 未設定")

    await interaction.response.send_message("\n".join(lines), ephemeral=True)


tree.add_command(channel_group)


@tree.command(
    name="test_alert",
    description="サンプルXMLから防災情報のテストメッセージを送信します",
)
@app_commands.describe(type="テストする情報の種別")
@app_commands.choices(
    type=[
        app_commands.Choice(name="警報・注意報", value="alert"),
        app_commands.Choice(name="熱中症警戒アラート", value="heatstroke"),
        app_commands.Choice(name="気象解説情報", value="commentary"),
        app_commands.Choice(name="早期注意情報", value="early_warning"),
    ]
)
async def test_alert(
    interaction: discord.Interaction,
    type: app_commands.Choice[str],
):
    await interaction.response.defer(ephemeral=True)

    if type.value == "alert":
        sample_file = SAMPLE_ALERT_FILE
    elif type.value == "heatstroke":
        sample_file = SAMPLE_HEATSTROKE_FILE
    elif type.value == "early_warning":
        sample_file = SAMPLE_EARLY_WARNING_FILE
    else:
        sample_file = SAMPLE_COMMENTARY_FILE

    if not os.path.exists(sample_file):
        await interaction.followup.send(
            f"サンプルファイルが見つかりません: `{sample_file}`",
            ephemeral=True,
        )
        return

    try:
        with open(sample_file, "r", encoding="utf-8") as f:
            xml_content = f.read()
    except IOError as e:
        logger.error(f"サンプルファイル読み込みエラー: {e}")
        await interaction.followup.send(
            "サンプルファイルの読み込みに失敗しました。", ephemeral=True
        )
        return

    if type.value == "alert":
        parsed_data = parse_warning_xml(xml_content)
    elif type.value == "heatstroke":
        parsed_data = parse_heatstroke_xml(xml_content)
    elif type.value == "early_warning":
        parsed_data = parse_early_warning_xml(xml_content)
    else:
        parsed_data = parse_commentary_xml(xml_content)

    if not parsed_data:
        await interaction.followup.send(
            "サンプルXMLのパースに失敗しました", ephemeral=True
        )
        return

    image_bytes = None
    try:
        if type.value == "alert":
            embed = create_warning_embed(parsed_data)
            grouped_alerts = parsed_data.get("grouped_alerts", {})
            if not grouped_alerts:
                await interaction.followup.send(
                    "パース結果に警報データが含まれていません", ephemeral=True
                )
                return

            image_bytes = await asyncio.to_thread(   # ★ to_thread
                create_warning_map_image,
                area_levels=parsed_data.get("area_levels", {}),
                title=parsed_data.get("head_title", "気象警報・注意報 発表範囲"),
            )

            max_level = max(lv for levels in grouped_alerts.values() for lv in levels)
            kind_count = len(grouped_alerts)
            area_count = len(
                {
                    a
                    for levels in grouped_alerts.values()
                    for statuses in levels.values()
                    for areas in statuses.values()
                    for a in areas
                }
            )
            summary = (
                f"種別: {kind_count}件 / 対象地域: {area_count}件 / 最大レベル: {max_level}\n"
                f"タイトル: {parsed_data.get('head_title', '---')}"
            )
        elif type.value == "heatstroke":
            embed = create_heatstroke_embed(parsed_data)

            image_bytes = await asyncio.to_thread(   # ★ to_thread
                create_warning_map_image,
                area_levels={},
                heatstroke_area_names=[parsed_data.get("area_name", "")],
                heatstroke_special=parsed_data.get("is_special", False),
                title=f"熱中症警戒アラート: {parsed_data.get('area_name', '')}",
            )

            wbgt = parsed_data.get("wbgt_readings", [])
            temps = parsed_data.get("temp_readings", [])
            summary = (
                f"地域: {parsed_data.get('area_name', '---')}\n"
                f"対象日: {parsed_data.get('target_label', '---')}\n"
                f"WBGT予測: {wbgt}\n"
                f"予想最高気温: {temps}"
            )
        elif type.value == "early_warning":
            embed = create_early_warning_embed(parsed_data)
            areas = parsed_data.get("areas", [])
            area_names = [a["name"] for a in areas]
            kind_types = set()
            for a in areas:
                for k in a.get("kinds", []):
                    kind_types.add(k["type"])
            summary = (
                f"タイトル: {parsed_data.get('head_title', '---')}\n"
                f"対象区域: {', '.join(area_names) if area_names else 'なし'}\n"
                f"現象: {', '.join(sorted(kind_types)) if kind_types else 'なし'}"
            )
        else:
            embed = create_commentary_embed(parsed_data)
            summary = (
                f"種別: {parsed_data.get('scope', '')}気象解説情報\n"
                f"タイトル: {parsed_data.get('head_title', '---')}"
            )
    except Exception as e:
        logger.exception(f"テストメッセージ生成中にエラー: {e}")
        await interaction.followup.send(
            "テストメッセージの生成中にエラーが発生しました。", ephemeral=True
        )
        return

    if image_bytes:
        embed.set_image(url="attachment://test_map.png")

    if image_bytes:
        file = discord.File(io.BytesIO(image_bytes), filename="test_map.png")
        await interaction.followup.send(
            content=summary, embed=embed, file=file, ephemeral=True
        )
    else:
        await interaction.followup.send(content=summary, embed=embed, ephemeral=True)


@tree.command(name="ping", description="Botの応答確認")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("Pong!")


if __name__ == "__main__":
    if not config.DISCORD_TOKEN:
        logger.error("DISCORD_TOKEN が設定されていません。")
        exit(1)
    bot.run(config.DISCORD_TOKEN)
