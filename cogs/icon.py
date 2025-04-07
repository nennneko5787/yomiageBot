import os
import random

import aiofiles
import discord
from discord.ext import commands, tasks


class IconChangeCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.iconChangeLoop.start()

    @tasks.loop(hours=1)
    async def iconChangeLoop(self):
        files = [
            f for f in os.listdir("icons") if os.path.isfile(os.path.join("icons", f))
        ]
        file = random.choice(files)

        async with aiofiles.open(f"icons/{file}", "rb") as f:
            data = await f.read()
            await self.bot.user.edit(avatar=data)


async def setup(bot: commands.Bot):
    await bot.add_cog(IconChangeCog(bot))
