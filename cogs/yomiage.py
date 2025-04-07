import asyncio
import io
import json
import re
from typing import Dict, Union, List

import aiofiles
import discord
from discord import app_commands
from discord.ext import commands
from voicevox_core import UserDictWord
from voicevox_core.asyncio import (
    Onnxruntime,
    OpenJtalk,
    Synthesizer,
    VoiceModelFile,
    UserDict,
)


class YomiageCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.voicevox: Synthesizer = None
        self.openJTalk: OpenJtalk = None
        self.characters: Dict[str, int] = {}

        self.yomiChannel: Dict[int, discord.TextChannel] = {}
        self.queue: Dict[int, asyncio.Queue] = {}
        self.speaker: Dict[int, int] = {}
        self.dictionary: Dict[int, List[UserDictWord]] = {}
        self.playing: Dict[int, bool] = {}

        self.speakerCommand.autocomplete("speaker")(self.speakersAutoComplete)
        self.dictionaryRemoveCommand.autocomplete("index")(self.indexAutoComplete)

    async def cog_load(self):
        openJTalkDictDir = "../voicevox_core/dict/open_jtalk_dic_utf_8-1.11"
        self.openJTalk = await OpenJtalk.new(openJTalkDictDir)
        self.voicevox = Synthesizer(
            await Onnxruntime.load_once(
                filename="../voicevox_core/onnxruntime/lib/libvoicevox_onnxruntime.so.1.17.3"
            ),
            self.openJTalk,
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
            _speaker: Dict[int, int] = json.loads(await f.read())
            if not isinstance(_speaker, dict):
                self.speaker = {}
            for index, value in _speaker.items():
                self.speaker[int(index)] = value

        _dictionary: Dict[int, list[Dict[str, str]]] = {}
        async with aiofiles.open("./dictionary.json") as f:
            __dictionary: Dict[int, list[Dict[str, str]]] = json.loads(await f.read())
            if not isinstance(__dictionary, dict):
                __dictionary = {}
            for index, value in __dictionary.items():
                __dictionary[int(index)] = value

        for index, value in _dictionary.items():
            self.dictionary[index] = []
            for v in value:
                _dictionary[index].append(
                    UserDictWord(
                        surface=v["surface"],
                        pronunciation=v["pronunciation"],
                        accent_type=v["accent_type"],
                        word_type=v["word_type"],
                        priority=v["priority"],
                    )
                )

    async def cog_unload(self):
        async with aiofiles.open("./speakers.json", "w+") as f:
            await f.write(json.dumps(self.speaker))

        _dictionary: Dict[int, List[Dict[str, str]]] = {}
        for index, value in self.dictionary.items():
            _dictionary[index] = []
            for v in value:
                _dictionary[index].append(
                    {
                        "surface": v.surface,
                        "pronunciation": v.pronunciation,
                        "accent_type": v.accent_type,
                        "word_type": v.word_type,
                        "priority": v.priority,
                    }
                )

        async with aiofiles.open("./dictionary.json", "w+") as f:
            await f.write(json.dumps(_dictionary))

    async def yomiage(self, guild: discord.Guild):
        if self.queue[guild.id].qsize() <= 0:
            if guild.voice_client is not None:
                self.playing[guild.id] = False
            return
        content = await self.queue[guild.id].get()
        self.playing[guild.id] = True

        if not guild.id in self.dictionary.keys():
            self.dictionary[guild.id] = []

        # 単語をロード
        userDict = UserDict()
        for word in self.dictionary[guild.id]:
            userDict.add_word(word)
        await self.openJTalk.use_user_dict(userDict)
        self.voicevox.open_jtalk = self.openJTalk

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
            await self.queue[message.guild.id].put(
                f"{message.author.display_name}さん、{content}{'、添付ファイル' if len(message.attachments) > 0 or len(message.stickers) > 0 else ''}"
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
        if member.id == guild.me.id:
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
        connectTo: Union[discord.VoiceChannel, discord.StageChannel] = None,
        monitorTo: Union[
            discord.TextChannel, discord.VoiceChannel, discord.StageChannel
        ] = None,
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

        if not self.speaker.get(guild.id):
            self.speaker[guild.id] = 1  # 1はずんだもん(ノーマル)
        await connectTo.connect()

        embed = discord.Embed(
            title="✅接続しました！",
            colour=discord.Colour.green(),
        )
        await interaction.followup.send(embed=embed)

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
        await voiceClient.disconnect()

        embed = discord.Embed(
            title="✅切断しました！",
            colour=discord.Colour.green(),
        )
        await interaction.followup.send(embed=embed)

    async def speakersAutoComplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        returnList: list[app_commands.Choice[int]] = []
        for name, value in self.characters.items():
            if name.startswith(current):
                returnList.append(app_commands.Choice(name=name, value=value))
        return returnList[:25]

    @app_commands.command(name="speaker", description="話者を変更します。")
    @app_commands.rename(speaker="話者")
    @app_commands.describe(speaker="空欄にすると話者の一覧を表示します。")
    async def speakerCommand(
        self, interaction: discord.Interaction, speaker: int = None
    ):
        if not speaker:
            embed = discord.Embed(
                title="設定できる話者の一覧",
                description=f"```\n{'\n'.join([name for name in self.characters.keys()])}\n※初期設定はずんだもん (ノーマル)です\n※Discordの制限によりオートコンプリートには25件しか表示されません\n```",
                colour=discord.Colour.blurple(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            guild = interaction.guild

            try:
                self.speaker[guild.id] = speaker

                name = [k for k, v in self.characters.items() if v == speaker][0]
                embed = discord.Embed(
                    title=f"✅話者を`{name}`へ変更しました！",
                    colour=discord.Colour.green(),
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
            except:
                embed = discord.Embed(
                    title=f"その話者は存在しません",
                    colour=discord.Colour.red(),
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)

    dictionaryGroup = app_commands.Group(
        name="dictionary", description="辞書関連のコマンド。"
    )

    def containsNonKatakana(self, text: str):
        return bool(re.search(r"[^\u30A0-\u30FFー]", text))

    @dictionaryGroup.command(name="add", description="辞書に新たな単語を追加します。")
    @discord.app_commands.rename(
        surface="単語",
        pronunciation="発音",
        accentType="アクセントの位置",
        wordType="品詞",
        priority="優先度",
    )
    @discord.app_commands.describe(
        surface="発音を変えたい単語。",
        pronunciation="カタカナのみ受け付けます。",
        accentType="別名アクセント型。",
        wordType="単語の品詞。",
        priority="単語の優先度。",
    )
    @discord.app_commands.choices(
        wordType=[
            discord.app_commands.Choice(name="固有名詞", value="PROPER_NOUN"),
            discord.app_commands.Choice(name="一般名詞", value="COMMON_NOUN"),
            discord.app_commands.Choice(name="動詞", value="VERB"),
            discord.app_commands.Choice(name="形容詞", value="ADJECTIVE"),
            discord.app_commands.Choice(name="語尾", value="SUFFIX"),
        ]
    )
    async def dictionaryAddCommand(
        self,
        interaction: discord.Interaction,
        surface: str,
        pronunciation: str,
        accentType: int,
        wordType: str = "COMMON_NOUN",
        priority: app_commands.Range[int, 1, 9] = 5,
    ):
        guild = interaction.guild

        if self.containsNonKatakana(pronunciation):
            embed = discord.Embed(
                title="発音はカタカナで入力してください", colour=discord.Colour.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        if not guild.id in self.dictionary.keys():
            self.dictionary[guild.id] = []

        self.dictionary[interaction.guild.id].append(
            UserDictWord(
                surface=surface,
                pronunciation=pronunciation,
                accent_type=accentType,
                word_type=wordType,
                priority=priority,
            )
        )

        embed = discord.Embed(
            title=f"✅辞書に単語を追加しました！", colour=discord.Colour.green()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def indexAutoComplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        returnList: list[app_commands.Choice[int]] = []
        for _, value in self.dictionary.items():
            for index, word in enumerate(value):
                if word.surface.startswith(current):
                    returnList.append(app_commands.Choice(name=word, value=index))
        return returnList[:25]

    @dictionaryGroup.command(name="remove", description="辞書から単語を削除します。")
    @discord.app_commands.rename(
        index="単語",
    )
    @discord.app_commands.describe(
        index="辞書から削除したい単語。",
    )
    async def dictionaryRemoveCommand(
        self, interaction: discord.Interaction, index: int
    ):
        guild = interaction.guild

        if not guild.id in self.dictionary.keys():
            self.dictionary[guild.id] = []

        if len(self.dictionary[guild.id]) >= index:
            embed = discord.Embed(
                title="単語が存在しません。", colour=discord.Colour.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        del self.dictionary[interaction.guild.id][index]

        embed = discord.Embed(
            title=f"✅辞書から単語を削除しました！", colour=discord.Colour.green()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(YomiageCog(bot))
