import discord
from discord import app_commands
from discord.ext import commands


class HelpCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="help", description="このボットの使い方を確認します")
    async def helpCommand(self, interaction: discord.Interaction):
        embed = (
            discord.Embed(
                title=f"{self.bot.user.display_name} の使い方",
                description="何かわからないことがあったら[サポートサーバー](https://discord.gg/PN3KWEnYzX)に来てください。",
                colour=discord.Colour.blurple(),
            )
            .add_field(
                name="/join", value="ボイスチャンネルに接続し、読み上げを開始します。"
            )
            .add_field(
                name="/leave",
                value="ボイスチャンネルから切断し、読み上げを終了します。",
            )
            .add_field(name="/speaker", value="話者を変更します。")
        )
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(HelpCog(bot))
