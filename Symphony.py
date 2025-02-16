import discord
from discord.utils import get
import youtube_dl
import asyncio
from async_timeout import timeout
from functools import partial
from discord.ext import commands,tasks
import itertools
from itertools import cycle
import os
from discord_buttons_plugin import *
from keep_alive import keep_alive

bot = commands.Bot(command_prefix='!',help_command=None)
buttons = ButtonsClient(bot)
status = cycle(["Symphony | !help","Version Demo | !help"])


#---------------------------------------------------------------------------------------
youtube_dl.utils.bug_reports_message = lambda: ''

ytdl_format_options = {
    'format': 'bestaudio/best',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0'
}

ffmpeg_options = {
    'options': '-vn',
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5" ## 
}

ytdl = youtube_dl.YoutubeDL(ytdl_format_options)

class YTDLSource(discord.PCMVolumeTransformer):

    def __init__(self, source, *, data, requester):
        super().__init__(source)
        self.requester = requester

        self.title = data.get('title')
        self.web_url = data.get('webpage_url')

    def __getitem__(self, item: str):
        """Allows us to access attributes similar to a dict.
        This is only useful when you are NOT downloading.
        """
        return self.__getattribute__(item)

    @classmethod
    async def create_source(cls, ctx, search: str, *, loop, download=False):
        loop = loop or asyncio.get_event_loop()

        to_run = partial(ytdl.extract_info, url=search, download=download)
        data = await loop.run_in_executor(None, to_run)

        if 'entries' in data:
            data = data['entries'][0]

        await ctx.send(':mag:'+'**Searching**'+f'`\n{data["title"]}\n`') 

        if download:
            source = ytdl.prepare_filename(data)
        else:
            return {'webpage_url': data['webpage_url'], 'requester': ctx.author, 'title': data['title']}

        return cls(discord.FFmpegPCMAudio(source, **ffmpeg_options), data=data, requester=ctx.author)

    @classmethod
    async def regather_stream(cls, data, *, loop):
        """Used for preparing a stream, instead of downloading.
        Since Youtube Streaming links expire."""
        loop = loop or asyncio.get_event_loop()
        requester = data['requester']

        to_run = partial(ytdl.extract_info, url=data['webpage_url'], download=False)
        data = await loop.run_in_executor(None, to_run)

        return cls(discord.FFmpegPCMAudio(data['url'], **ffmpeg_options), data=data, requester=requester)

class MusicPlayer:
    """A class which is assigned to each guild using the bot for Music.
    This class implements a queue and loop, which allows for different guilds to listen to different playlists
    simultaneously.
    When the bot disconnects from the Voice it's instance will be destroyed.
    """

    __slots__ = ('bot', '_guild', '_channel', '_cog', 'queue', 'next', 'current', 'np', 'volume')

    def __init__(self, ctx):
        self.bot = ctx.bot
        self._guild = ctx.guild
        self._channel = ctx.channel
        self._cog = ctx.cog

        self.queue = asyncio.Queue()
        self.next = asyncio.Event()

        self.np = None
        self.volume = .5
        self.current = None

        ctx.bot.loop.create_task(self.player_loop())

    async def player_loop(self):
        """Our main player loop."""
        await self.bot.wait_until_ready()

        while not self.bot.is_closed():
            self.next.clear()

            try:
                async with timeout(300):  # 5 minutes...
                    source = await self.queue.get()
            except asyncio.TimeoutError:
                return await self.destroy(self._guild)

            if not isinstance(source, YTDLSource):
                try:
                    source = await YTDLSource.regather_stream(source, loop=self.bot.loop)
                except Exception as e:
                    await self._channel.send(f'There was an error processing your song.\n'
                                             f'```css\n[{e}]\n```')
                    continue

            source.volume = self.volume
            self.current = source

            self._guild.voice_client.play(source, after=lambda _: self.bot.loop.call_soon_threadsafe(self.next.set))
            self.np = await self._channel.send(f'**Playing**:musical_note:  `{source.title}`''-Now!')
            await self.next.wait()
            source.cleanup()
            self.current = None

            try:
                await self.np.delete()
            except discord.HTTPException:
                pass

    async def destroy(self, guild):
        """Disconnect and cleanup the player."""
        del players[self._guild]
        await self._guild.voice_client.disconnect()
        return self.bot.loop.create_task(self._cog.cleanup(guild))
#--------------------------------Event----------------------------------------------
@bot.event
async def on_ready():
    change_status.start()
    print(f"Logged in as {bot.user}")

@tasks.loop(seconds=10)
async def change_status():
  await bot.change_presence(activity=discord.Game(next(status)))


#-------------------------------------Commands---------------------------------------

@bot.command() 
async def help(ctx):
  await buttons.send(
		content="Helper",
		channel = ctx.channel.id,
		components = [
			ActionRow([
				Button(

					style = ButtonType().Primary,
					label = "Home",
					custom_id = "Home",
				),

        Button(

          style = ButtonType().Primary,
					label = "Music",
					custom_id = "Music",
        ),
        Button(

          style = ButtonType().Primary,
					label = "Admin",
					custom_id = "admincmd",
        )
			])
		]
	)



@bot.command() 
async def p(ctx,* ,search: str):
    channel = ctx.author.voice.channel
    voice_client = get(bot.voice_clients, guild=ctx.guild)
    
    if voice_client == None:
        await ctx.channel.send(':cyclone:'+'**Loading...**')
        await channel.connect()
        voice_client = get(bot.voice_clients, guild=ctx.guild)

    _player = get_player(ctx)
    source = await YTDLSource.create_source(ctx, search, loop=bot.loop, download=False)

    await _player.queue.put(source)
    await buttons.send(
		content="Audio controller",
		channel = ctx.channel.id,
		components = [
			ActionRow([
				Button(

					style = ButtonType().Primary,
					label = "Resume",
					custom_id = "Resume",

				),

				Button(
					
          style = ButtonType().Primary,
					label = "Pause",
					custom_id = "Pause"

				),
				Button(
					
          style = ButtonType().Primary,
					label = "Skip",
					custom_id = "Skip",
				),
        Button(
          style = ButtonType().Secondary,
					label = "QueueList",
					custom_id = "ql",
        ),
        Button(
          style = ButtonType().Danger,
					label = "Cancel",
					custom_id = "Cancel",
        )
			])
		]
	)


players = {}
def get_player(ctx):
    try:
        player = players[ctx.guild.id]
    except:
        player = MusicPlayer(ctx)
        players[ctx.guild.id] = player
    
    return player

@bot.command()
async def stop(ctx):
    voice_client = get(bot.voice_clients, guild=ctx.guild)
    if voice_client == None:
        await ctx.channel.send("Bot is not connected to vc")
        return

    if voice_client.channel != ctx.author.voice.channel:
        await ctx.channel.send("The bot is currently connected to {0}".format(voice_client.channel))
        return

    voice_client.stop()
    await ctx.send(':no_entry: '+' **Cancel**')


@bot.command()
async def pause(ctx):
    voice_client = get(bot.voice_clients, guild=ctx.guild)
    if voice_client == None:
        await ctx.channel.send("Bot is not connected to vc")
        return

    if voice_client.channel != ctx.author.voice.channel:
        await ctx.channel.send("The bot is currently connected to {0}".format(voice_client.channel))
        return

    voice_client.pause()
    await ctx.send(':pause_button: '+' **Pause**')
    

@bot.command()
async def resume(ctx):
    voice_client = get(bot.voice_clients, guild=ctx.guild)
    if voice_client == None:
        await ctx.channel.send("Bot is not connected to vc")
        return

    if voice_client.channel != ctx.author.voice.channel:
        await ctx.channel.send("The bot is currently connected to {0}".format(voice_client.channel))
        return

    voice_client.resume()
    await ctx.send(':arrow_forward: '+' **Resume**')

    
@bot.command()
async def dc(ctx):
    del players[ctx.guild.id]
    await ctx.voice_client.disconnect()
    await ctx.send(':mobile_phone_off:  '+' **Successfully disconnected**')


@bot.command()
async def queuelist(ctx):
    voice_client = get(bot.voice_clients, guild=ctx.guild)

    if voice_client == None or not voice_client.is_connected():
        await ctx.channel.send("Bot is not connected to vc", delete_after=10)
        return
    
    player = get_player(ctx)
    if player.queue.empty():
        return await ctx.send('Empty')
    
    upcoming = list(itertools.islice(player.queue._queue,0,player.queue.qsize()))
    fmt = '\n'.join(f'**`{_["title"]}`**' for _ in upcoming)
    embed = discord.Embed(title=f'Upcoming - Next {len(upcoming)}', description=fmt)
    await ctx.send(embed=embed)

@bot.command()
async def skip(ctx):
    voice_client = get(bot.voice_clients, guild=ctx.guild)

    if voice_client == None or not voice_client.is_connected():
        await ctx.channel.send("Bot is not connected to vc", delete_after=10)
        return

    if voice_client.is_paused():
        pass
    elif not voice_client.is_playing():
        return

    voice_client.stop()
    await ctx.send(':track_next:'+' **Skip**')

@bot.command()
async def kick(ctx, member : discord.Member):
    try:
        await member.kick(reason=None)
        await ctx.send("**Successfully Kicked** "+":athletic_shoe: " + member.mention) 
    except:
        await ctx.send("bot does not have the kick members permission!")

@bot.command()
async def ban(ctx, member : discord.Member):
    try:
        await member.ban(reason=None)
        await ctx.send("**Successfully Banned** "+":no_entry_sign: " + member.mention) 
    except:
        await ctx.send("bot does not have the kick members permission!")

@bot.command()
async def unban(ctx,* , member):
  
  banned_users = await ctx.guild.bans()
  member_name, member_discriminator = member.split('#')

  for ban_entry in banned_users:
    user = ban_entry.user

    if (user.name,user.discriminator) == (member_name, member_discriminator):
      await ctx.guild.unban(user)
      await ctx.send(f'**Successfully Unbanned** :negative_squared_cross_mark:  {user.mention}')
      return


#---------------------------------------Buttons------------------------------------------------

@buttons.click
async def Resume(ctx):
  voice_client = get(bot.voice_clients, guild=ctx.guild)
  voice_client.resume()

@buttons.click
async def Pause(ctx):
  voice_client = get(bot.voice_clients, guild=ctx.guild)
  voice_client.pause()

@buttons.click
async def Skip(ctx):
    voice_client = get(bot.voice_clients, guild=ctx.guild)    
    if voice_client.is_paused():
      pass
    elif not voice_client.is_playing():
      return

    voice_client.stop()

@buttons.click
async def Cancel(ctx):
 voice_client = get(bot.voice_clients, guild=ctx.guild)
 voice_client.stop()

@buttons.click
async def Home(ctx):
    emBed1 = discord.Embed(title="Invite Bot",url="https://discord.com/api/oauth2/authorize?client_id=889443084461563914&permissions=8&scope=bot", color=0xffb300)
    emBed1.set_image(url="https://cdn.discordapp.com/attachments/887666939793666062/889800093828530186/20210921_160727.png")
    emBed1.set_author(name="Symphony - Home Page") 

    await ctx.channel.send(embed=emBed1)

@buttons.click
async def Music(ctx):
    emBed = discord.Embed(title="Music Command", color=0xffb300)
    emBed.add_field(name='`!p` Play Audio',value='**`!pause`** **Pause Audio**', inline=False)
    emBed.add_field(name='`!resume` Resume Audio',value='**`!skip`** **Skip Audio**',inline=False)
    emBed.add_field(name='`!queueList` Show QueueList',value='**`!dc`** **Disconnect**',inline=False)
    await ctx.channel.send(embed=emBed)

@buttons.click
async def ql(ctx):
  player = get_player(ctx)
  upcoming = list(itertools.islice(player.queue._queue,0,player.queue.qsize()))
  fmt = '\n'.join(f'**`{_["title"]}`**' for _ in upcoming)
  embedql = discord.Embed(title=f'Upcoming - Next {len(upcoming)}', description=fmt)
  await ctx.channel.send(embed=embedql)

@buttons.click
async def admincmd(ctx):
  emBedadmin = discord.Embed(title="Admin Command", color=0xffb300)
  emBedadmin.add_field(name='`!kick` kick member',value='**`!Ban`** **Ban member**', inline=False)
  emBedadmin.add_field(name='`!unban` Unban member',value="⠀",inline=False)
  await ctx.channel.send(embed=emBedadmin)

#-----------------------------------------------------------------------------------

keep_alive()
bot.run(os.environ['TOKEN'])
