import asyncio
import io
import json
import re

import aiofiles
import discord
from discord import app_commands
from discord.ext import commands
from voicevox_core.asyncio import Onnxruntime, OpenJtalk, Synthesizer, VoiceModelFile


class YomiageCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.yomiChannel: dict[int, discord.TextChannel] = {}
        self.queue: dict[int, asyncio.Queue] = {}
        self.playing: dict[int, bool] = {}
        self.speaker: dict[int, int] = {}
        self.beforeUser: dict[int, int] = {}
        self.voicevox: Synthesizer = None
        self.characters: dict[str, int] = {}

        self.speakerCommand.autocomplete("speaker")(self.speakersAutoComplete)

    async def cog_load(self):
        OpenJtalkDictDir = "../voicevox_core/dict/open_jtalk_dic_utf_8-1.11"
        self.voicevox = Synthesizer(
            await Onnxruntime.load_once(
                filename="../voicevox_core/onnxruntime/lib/libvoicevox_onnxruntime.so.1.17.3"
            ),
            await OpenJtalk.new(OpenJtalkDictDir),
        )

        for i in range(18):
            print(f"Loading {i}.vvm")
            async with await VoiceModelFile.open(
                f"../voicevox_core/models/vvms/{i}.vvm"
            ) as model:
                await self.voicevox.load_voice_model(model)

                for character in model.metas:
                    for style in character.styles:
                        self.characters[f"{character.name} ({style.name})"] = style.id
            print(f"Loaded {i}.vvm")

        async with aiofiles.open("./speakers.json") as f:
            _speaker: dict = json.loads(await f.read())
            for index, value in _speaker.items():
                self.speaker[int(index)] = value
        if not isinstance(self.speaker, dict):
            self.speaker = {}

    async def cog_unload(self):
        async with aiofiles.open("./speakers.json", "w+") as f:
            await f.write(json.dumps(self.speaker))

    async def yomiage(self, guild: discord.Guild):
        if self.queue[guild.id].qsize() <= 0:
            if guild.voice_client is not None:
                self.playing[guild.id] = False
            return
        content = await self.queue[guild.id].get()
        self.playing[guild.id] = True
        waveBytes = await self.voicevox.tts(content, self.speaker[guild.id])
        wavIO = io.BytesIO(waveBytes)
        source = discord.PCMVolumeTransformer(
            discord.FFmpegPCMAudio(wavIO, pipe=True), 2.0
        )

        voiceClient: discord.VoiceClient = guild.voice_client

        loop = asyncio.get_event_loop()

        def after(e: Exception):
            if voiceClient.is_playing():
                voiceClient.stop()
            if voiceClient.is_connected():
                asyncio.run_coroutine_threadsafe(self.yomiage(guild), loop=loop)

        voiceClient.play(source, after=after)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.content.startswith(self.bot.command_prefix):
            return
        if message.author.bot:
            return
        channel = self.yomiChannel.get(message.guild.id)
        if channel and channel.id == message.channel.id:
            content = message.clean_content
            if len(content) > 100:
                content = content[0:100] + "、長文省略"
            content = re.sub(r"https?://\S+", "、リンク省略、", content)
            content = re.sub(r"<#.*?>", "、チャンネル省略、", content)
            content = re.sub(r"<@.*?>", "、メンション省略、", content)
            content = re.sub(r"<@&.*?>", "、ロールメンション省略、", content)
            content = re.sub(r"<.*?:.*?>", "、絵文字省略、", content)
            if self.beforeUser[message.guild.id] != message.author.id:
                content = f"{message.author.display_name}さん、" + content
                self.beforeUser[message.guild.id] = message.author.id
            await self.queue[message.guild.id].put(
                f"{content}{'、添付ファイル' if len(message.attachments) > 0 or len(message.stickers) > 0 else ''}"
            )
            if not self.playing[message.guild.id]:
                await self.yomiage(message.guild)

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        guild = member.guild
        channel = self.yomiChannel.get(guild.id)
        if not channel:
            return

        # どちらのチャンネルにもいない（何も変化していない）場合は無視
        if before.channel is None and after.channel is None:
            return

        # 読み上げ対象のチャンネルからの退出処理
        if before.channel and before.channel.id == channel.id:
            if after.channel is None or after.channel.id != channel.id:
                await self.queue[guild.id].put(
                    f"{member.display_name}さんが退出しました。"
                )
                if not self.playing[guild.id]:
                    await self.yomiage(guild)

        # 読み上げ対象のチャンネルへの入室処理
        if (
            after.channel
            and after.channel.id == channel.id
            and (before.channel is None or before.channel.id != channel.id)
        ):
            await self.queue[guild.id].put(f"{member.display_name}さんが入室しました。")
            if not self.playing[guild.id]:
                await self.yomiage(guild)

    @app_commands.command(name="join", description="ボイスチャンネルに接続します。")
    @app_commands.rename(connectTo="接続先チャンネル", monitorTo="監視先チャンネル")
    @app_commands.describe(
        connectTo="読み上げたテキストを再生するチャンネル。",
        monitorTo="読み上げ対象チャンネル。",
    )
    async def join(
        self,
        interaction: discord.Interaction,
        connectTo: discord.abc.Connectable = None,
        monitorTo: discord.abc.Messageable = None,
    ):
        voiceClient: discord.VoiceClient = interaction.guild.voice_client
        guild: discord.Guild = interaction.guild

        if voiceClient:
            embed = discord.Embed(
                title="現在別のチャンネルで読み上げしています！",
                description="使用中のボイスチャンネルから切断してから、もう一度コマンドを実行してください。",
                colour=discord.Colour.red(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        if not connectTo:
            if not interaction.user.voice.channel:
                embed = discord.Embed(
                    title="あなたはボイスチャンネルに接続していません！",
                    description="ボイスチャンネルに接続するか、`接続先チャンネル`を指定してください。",
                    colour=discord.Colour.red(),
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return
            connectTo = interaction.user.voice.channel

        if not monitorTo:
            monitorTo = interaction.channel

        await interaction.response.defer(ephemeral=True)

        self.yomiChannel[guild.id] = monitorTo
        self.queue[guild.id] = asyncio.Queue()
        self.playing[guild.id] = False
        self.beforeUser[guild.id] = self.bot.user.id

        if not self.speaker.get(guild.id):
            self.speaker[guild.id] = 1  # 1はずんだもん(ノーマル)
        await connectTo.connect()

        embed = discord.Embed(
            title="✅接続しました！",
            colour=discord.Colour.green(),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

        await self.queue[guild.id].put("接続しました。")
        await self.yomiage(guild)

    @app_commands.command(name="leave", description="ボイスチャンネルから切断します。")
    async def leave(self, interaction: discord.Interaction):
        voiceClient: discord.VoiceClient = interaction.guild.voice_client
        guild: discord.Guild = interaction.guild

        if not voiceClient:
            embed = discord.Embed(
                title="ボイスチャンネルに接続していません",
                colour=discord.Colour.red(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        del self.yomiChannel[guild.id]
        del self.queue[guild.id]
        del self.playing[guild.id]
        del self.beforeUser[guild.id]
        await voiceClient.disconnect()

        embed = discord.Embed(
            title="✅切断しました！",
            colour=discord.Colour.green(),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    async def speakersAutoComplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        returnList: list[app_commands.Choice[int]] = []
        for name, value in self.characters.items():
            if name.startswith(current):
                returnList.append(app_commands.Choice(name=name, value=value))
        return returnList[:25]

    @app_commands.command(name="speaker", description="話者を変更します。")
    async def speakerCommand(self, interaction: discord.Interaction, speaker: int = 1):
        guild = interaction.guild

        self.speaker[guild.id] = speaker

        embed = discord.Embed(
            title="✅話者を変更しました！",
            colour=discord.Colour.green(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(YomiageCog(bot))
