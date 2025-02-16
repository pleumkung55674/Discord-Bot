import os
import random
import discord
from discord.ext import commands

#commands bot
bot = commands.Bot(command_prefix='!', case_insensitive = True)

BloxFruit = discord.Embed(title="Slash Hub Scirpt", description="",color=0x73ff00)
BloxFruit.add_field(name="[☀️☁️ UPDATE 17] Blox Fruits",value='```lua\n_G.Test = true\nloadstring(game:HttpGet("Working"))();\n```')

LegendofSpeed = discord.Embed(title="Slash Hub Scirpt", description="",color=0x73ff00)
LegendofSpeed.add_field(name="Legends Of Speed ⚡",value='```lua\n_G.TextColor = Color3.fromRGB(255,255,0)\n_G.SchemeColor = Color3.fromRGB(0, 255, 255)\nloadstring(game:HttpGet("https://raw.githubusercontent.com/DevHubScript/Legend-of-Speed/main/Script.lua"))();\n```')

MurderMys2 = discord.Embed(title="Slash Hub Scirpt", description="",color=0xb469e9)
MurderMys2.add_field(name="Murder Mystery 2",value='```lua\n_G.Test = true\nloadstring(game:HttpGet("Working"))();\n```')


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")


class Dropdown(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(
                label="[☀️☁️ UPDATE 17] Blox Fruits", description=""
            ),
            discord.SelectOption(
                label="Legends Of Speed ⚡", description=""
            ),
            discord.SelectOption(
                label="Murder Mystery 2", description=""
            ),
        ]
        super().__init__(
            placeholder="Select_1",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):

      if self.values[0] == "[☀️☁️ UPDATE 17] Blox Fruits":
        await interaction.response.send_message(embed=BloxFruit, ephemeral=True)
      elif self.values[0] == "Legends Of Speed ⚡":
        await interaction.response.send_message(embed=LegendofSpeed, ephemeral=True)
      elif self.values[0] == "Murder Mystery 2":
        await interaction.response.send_message(embed=MurderMys2, ephemeral=True)

class MyView(discord.ui.View):
    def __init__(self):
        super().__init__()
        self.add_item(Dropdown())
    



@bot.command()
async def script(ctx):
    view = MyView()
    await ctx.send("Select Script", view=view)

if __name__ == "__main__":

  bot.run(os.environ['token'])



