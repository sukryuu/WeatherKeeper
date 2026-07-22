import discord
from discord import app_commands, TextChannel
import logging
import os

import config
from src.scheduler import WeatherScheduler
from src.jmaxml_parser import parse_warning_xml
from src.discord_notifier import create_warning_embed
from src.channel_settings import (
    CHANNEL_TYPES,
    get_channel_id,
    set_channel_id,
    get_guild_settings,
)

# ログ設定
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger(__name__)
logger.propagate = False

# Bot初期化
intents = discord.Intents.default()
bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)
scheduler = None

SAMPLE_ALERT_FILE = os.path.join(config.DATA_DIR, "sample_alert.xml")


# ==========================================
# 自動同期処理
# ==========================================
async def setup_hook():
    await tree.sync()
    logger.info("スラッシュコマンドを自動同期しました。")


bot.setup_hook = setup_hook


# ==========================================
# イベントハンドラ
# ==========================================
@bot.event
async def on_ready():
    global scheduler
    logger.info(f"Botがログインしました: {bot.user}")

    if scheduler is None:
        scheduler = WeatherScheduler(bot)
        logger.info("スケジューラーを開始しました。")


# ==========================================
# /channel コマンドグループ
# ==========================================
channel_group = app_commands.Group(name="channel", description="通知チャンネルの設定")


@channel_group.command(
    name="set",
    description="このチャンネルを指定種の通知先に設定します",
)
@app_commands.describe(type="通知種別 (alert=警報・注意報)")
@app_commands.choices(
    type=[
        app_commands.Choice(name="警報・注意報", value="alert"),
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


# ==========================================
# /test_alert
# ==========================================
@tree.command(
    name="test_alert",
    description="サンプルXMLから警報通知のテストメッセージを送信します",
)
@app_commands.describe(send_to_channel="警報チャンネルにも送信する場合はtrue")
async def test_alert(
    interaction: discord.Interaction,
    send_to_channel: bool = False,
):
    await interaction.response.defer(ephemeral=True)

    if not os.path.exists(SAMPLE_ALERT_FILE):
        await interaction.followup.send(
            f"サンプルファイルが見つかりません: `{SAMPLE_ALERT_FILE}`",
            ephemeral=True,
        )
        return

    try:
        with open(SAMPLE_ALERT_FILE, "r", encoding="utf-8") as f:
            xml_content = f.read()
    except IOError as e:
        logger.error(f"サンプルファイル読み込みエラー: {e}")
        await interaction.followup.send(
            "サンプルファイルの読み込みに失敗しました。", ephemeral=True
        )
        return

    parsed_data = parse_warning_xml(xml_content)
    if not parsed_data:
        await interaction.followup.send(
            "サンプルXMLのパースに失敗しました", ephemeral=True
        )
        return

    grouped_alerts = parsed_data.get("grouped_alerts", {})
    if not grouped_alerts:
        await interaction.followup.send(
            "パース結果に警報データが含まれていません", ephemeral=True
        )
        return

    embed = create_warning_embed(parsed_data)

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

    await interaction.followup.send(content=summary, embed=embed, ephemeral=True)

    if send_to_channel:
        guild_id = interaction.guild_id
        if guild_id is None:
            await interaction.followup.send(
                "サーバー内でのみチャンネル送信できます", ephemeral=True
            )
        else:
            ch_id = get_channel_id(guild_id, "alert")
            channel = interaction.client.get_channel(ch_id) if ch_id else None
            if isinstance(channel, TextChannel):
                try:
                    await channel.send(embed=embed)
                    await interaction.followup.send(
                        f"{channel.mention} にも送信しました", ephemeral=True
                    )
                except discord.DiscordException as e:
                    logger.error(f"チャンネル送信に失敗: {e}")
                    await interaction.followup.send(
                        "チャンネル送信に失敗しました", ephemeral=True
                    )
            else:
                await interaction.followup.send(
                    "警報チャンネルが未設定または見つかりません", ephemeral=True
                )


# ==========================================
# /ping
# ==========================================
@tree.command(name="ping", description="Botの応答確認")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("Pong!")


# ==========================================
# 起動
# ==========================================
if __name__ == "__main__":
    if not config.DISCORD_TOKEN:
        logger.error("DISCORD_TOKEN が設定されていません。")
        exit(1)
    bot.run(config.DISCORD_TOKEN)
