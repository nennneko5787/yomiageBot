import discord
from discord.ext import commands, tasks


class PresenceCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        self.presenceLoop.start()

    @tasks.loop(seconds=20)
    async def presenceLoop(self):
        await self.bot.change_presence(
            activity=discord.Game(
                f"{len(self.bot.voice_clients)} / {len(self.bot.guilds)} サーバーで読み上げ"
            )
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(PresenceCog(bot))
