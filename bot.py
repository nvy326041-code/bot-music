import asyncio
import os
import re
import traceback
from collections import deque

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
import yt_dlp

load_dotenv()
BOT_TOKEN = os.getenv("DISCORD_TOKEN")

# ================== CẤU HÌNH THÔNG BÁO ==================
YOUR_GUILD_ID = 1400489475154514002          # ← ID SERVER CỦA BẠN (đã thay)
NOTIFICATION_CHANNEL_ID = 1496189946464043171  # ← ID KÊNH THÔNG BÁO (đã thay)

COMMAND_PREFIX = "!"
EMBED_COLOR = 0x1DB954

# ================== YTDL + FFMPEG ==================
YTDL_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": False,
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch",
    "http_headers": {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36"
    },
}

FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}

class YTDLSource(discord.PCMVolumeTransformer):
    ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)

    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get("title", "Unknown")
        self.url = data.get("webpage_url", "")
        self.duration = data.get("duration", 0)
        self.thumbnail = data.get("thumbnail", "")
        self.uploader = data.get("uploader", "Unknown")

    @classmethod
    async def from_url(cls, url, *, loop=None):
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: cls.ytdl.extract_info(url, download=False))
        if "entries" in data:
            data = data["entries"][0]
        return cls(discord.FFmpegPCMAudio(data["url"], **FFMPEG_OPTIONS), data=data)

    @staticmethod
    def fmt_dur(sec):
        if not sec: return "Live"
        m, s = divmod(int(sec), 60)
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

class MusicPlayer:
    def __init__(self, guild_id):
        self.guild_id = guild_id
        self.queue = deque()
        self.current = None
        self.volume = 0.5

    def add(self, sources):
        self.queue.extend(sources)

    def next(self):
        if self.queue:
            self.current = self.queue.popleft()
            return self.current
        self.current = None
        return None

    def clear(self):
        self.queue.clear()
        self.current = None

class MusicCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.players = {}

    def get_player(self, guild_id):
        if guild_id not in self.players:
            self.players[guild_id] = MusicPlayer(guild_id)
        return self.players[guild_id]

    def _after(self, guild, error=None):
        player = self.get_player(guild.id)
        source = player.next()
        if source and guild.voice_client:
            guild.voice_client.play(source, after=lambda e: self._after(guild, e))

    @app_commands.command(name="play", description="Phát nhạc (link hoặc tên bài)")
    async def play(self, interaction: discord.Interaction, query: str):
        await interaction.response.defer()

        if not interaction.user.voice:
            return await interaction.followup.send("❌ Vào voice channel trước!")

        vc = interaction.guild.voice_client
        if not vc:
            vc = await interaction.user.voice.channel.connect()

        player = self.get_player(interaction.guild.id)

        if not query.startswith("http"):
            query = f"ytsearch:{query}"

        try:
            source = await YTDLSource.from_url(query)
        except Exception as e:
            return await interaction.followup.send(f"❌ Lỗi: {str(e)[:150]}")

        player.add(source)

        if not vc.is_playing():
            vc.play(source, after=lambda e: self._after(interaction.guild, e))
            embed = discord.Embed(title="Now Playing", description=f"[{source.title}]({source.url})", color=EMBED_COLOR)
            embed.add_field(name="Duration", value=YTDLSource.fmt_dur(source.duration))
            if source.thumbnail:
                embed.set_thumbnail(url=source.thumbnail)
            await interaction.followup.send(embed=embed)
        else:
            await interaction.followup.send(f"✅ Đã thêm: **{source.title}**")

    @app_commands.command(name="skip", description="Bỏ qua bài")
    async def skip(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if vc and vc.is_playing():
            vc.stop()
            await interaction.response.send_message("⏭️ Skipped!")
        else:
            await interaction.response.send_message("❌ Không có bài đang phát.")

    @app_commands.command(name="stop", description="Dừng và rời kênh")
    async def stop(self, interaction: discord.Interaction):
        player = self.get_player(interaction.guild.id)
        player.clear()
        vc = interaction.guild.voice_client
        if vc:
            vc.stop()
            await vc.disconnect()
        await interaction.response.send_message("✅ Đã dừng.")

    @app_commands.command(name="queue", description="Xem hàng chờ")
    async def queue_cmd(self, interaction: discord.Interaction):
        player = self.get_player(interaction.guild.id)
        if not player.queue and not player.current:
            return await interaction.response.send_message("📭 Hàng chờ trống.")
        msg = "**Hàng chờ:**\n"
        if player.current:
            msg += f"▶️ {player.current.title}\n"
        for i, s in enumerate(list(player.queue)[:10], 1):
            msg += f"{i}. {s.title}\n"
        await interaction.response.send_message(msg)

    @app_commands.command(name="leave", description="Rời kênh thoại")
    async def leave(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if vc:
            self.get_player(interaction.guild.id).clear()
            await vc.disconnect()
        await interaction.response.send_message("✅ Đã rời kênh.")

# ================== THÔNG BÁO JOIN / LEAVE ==================
@bot.event
async def on_voice_state_update(member, before, after):
    if member.guild.id != YOUR_GUILD_ID:
        return

    channel = bot.get_channel(NOTIFICATION_CHANNEL_ID)
    if not channel:
        return

    if before.channel is None and after.channel is not None:
        embed = discord.Embed(
            description=f"**{member.mention}** đã tham gia **{after.channel.name}**",
            color=0x00FF7F
        )
        embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)
        embed.set_thumbnail(url=member.display_avatar.url)
        await channel.send(embed=embed)

    elif before.channel is not None and after.channel is None:
        embed = discord.Embed(
            description=f"**{member.mention}** đã rời **{before.channel.name}**",
            color=0xFF4444
        )
        embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)
        embed.set_thumbnail(url=member.display_avatar.url)
        await channel.send(embed=embed)

# ================== BOT SETUP ==================
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix=COMMAND_PREFIX, intents=intents)

@bot.event
async def on_ready():
    print(f"✅ Bot online: {bot.user}")
    await bot.add_cog(MusicCog(bot))
    await bot.tree.sync()
    print("✅ Đã sync lệnh")

bot.run(BOT_TOKEN)
