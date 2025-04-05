import os

import discord
import dotenv
from discord.ext import commands

dotenv.load_dotenv()

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot("yomiage#", intents=intents)


@bot.event
async def setup_hook():
    await bot.load_extension("cogs.yomiage")
    await bot.load_extension("cogs.presence")
    await bot.tree.sync()


bot.run(os.getenv("discord"))
