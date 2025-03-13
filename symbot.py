import discord
import os
import re
import asyncio
import async_timeout
import itertools

from async_timeout import timeout
from functools import partial
from discord import FFmpegPCMAudio, ButtonStyle, Integration
from dotenv import load_dotenv
from discord.ext import commands
from discord.utils import get
from yt_dlp import YoutubeDL


intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents,help_command=None)


load_dotenv()  #โหลดค่าจาก .env
TOKEN = os.getenv("TOKEN")  #ดึงค่า Token


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
    'source_address': '0.0.0.0' # bind to ipv4 since ipv6 addresses cause issues sometimes
}

ffmpeg_options = {
    'options': '-vn',
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5" #แก้ปัญหาเวลาเพลงหยุดเล่นกลางทาง
}

ytdl = YoutubeDL(ytdl_format_options)

class YTDLSource(discord.PCMVolumeTransformer):

    def __init__(self, source, *, data, requester):
        super().__init__(source)
        self.requester = requester

        self.title = data.get('title')
        self.web_url = data.get('webpage_url')

    def __getitem__(self, item: str):
        return self.__getattribute__(item)

    @classmethod
    async def create_source(cls, ctx, search: str, *, loop, download=False):
        loop = loop or asyncio.get_event_loop()

        to_run = partial(ytdl.extract_info, url=search, download=download)
        try:
            data = await loop.run_in_executor(None, to_run)
        except Exception as e:
            nfembed = discord.Embed(title=f'Error', description='```diff\n- Song not found. Please try again\n```', color=0xff0000)
            await ctx.send(embed=nfembed,delete_after=5)
            return None

        if 'entries' in data and len(data['entries']) > 0:
            data = data['entries'][0]
        else:
            nfembed = discord.Embed(title=f'Error', description='```diff\n- Song not found. Please try again\n```', color=0xff0000)
            await ctx.send(embed=nfembed,delete_after=5)
            return None
        addsong_embed = discord.Embed(title=f'Added : ', description=f'```fix\n{data["title"]}\n```' ,color=0x00bdfc)
        addsong_embed.set_footer(text=f'requested by {ctx.author}')
        await ctx.send(embed=addsong_embed,delete_after=4)
        if download:
            source = ytdl.prepare_filename(data)
        else:
            return {'webpage_url': data['webpage_url'], 'requester': ctx.author, 'title': data['title']}

        player = get_player(ctx)
        await player.queue.put(cls(discord.FFmpegPCMAudio(source, **ffmpeg_options), data=data, requester=ctx.author))

        if hasattr(ctx, 'view') and isinstance(ctx.view, audiocontroller):
            ctx.view.update_skip_button()
        else:
            print('No view found')
        await ctx.message.edit(view=ctx.view)
        return cls(discord.FFmpegPCMAudio(source, **ffmpeg_options), data=data, requester=ctx.author)

    @classmethod
    async def regather_stream(cls, data, *, loop):
        """Used for preparing a stream, instead of downloading.
        Since Youtube Streaming links expire."""
        loop = loop or asyncio.get_event_loop()
        requester = data['requester']

        to_run = partial(ytdl.extract_info, url=data['webpage_url'], download=False)
        try:
            data = await loop.run_in_executor(None, to_run)
        except Exception as e:
            nfembed = discord.Embed(title=f'Error', description='```diff\n- Song not found. Please try again\n```', color=0xff0000)
            await requester.send(embed=nfembed,delete_after=5)
            return None
        
        if data is None:
            return None

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

        self.np = None  # Now playing message
        self.volume = .5
        self.current = None

        ctx.bot.loop.create_task(self.player_loop())

    async def player_loop(self):
        await self.bot.wait_until_ready()

        while not self.bot.is_closed():
            self.next.clear()

            try:
                async with timeout(300):
                    source = await self.queue.get()
            except asyncio.CancelledError:
                if self._guild.id in players:
                    del players[self._guild.id]
                raise  
            except TimeoutError:
                if self._guild.id in players:
                    del players[self._guild.id]
                return await self.destroy(self._guild)

            if not isinstance(source, YTDLSource):
                # Source was probably a stream (not downloaded)
                try:
                    source = await YTDLSource.regather_stream(source, loop=self.bot.loop)
                    if source is None:
                        nfembed = discord.Embed(title=f'Error', description='```diff\n- Song not found. Please try again\n```', color=0xff0000)
                        await self.send(embed=nfembed,delete_after=5)
                        continue
                except Exception as e:
                    pass
                    continue
            playing_embed = discord.Embed(title=f'Playing : ', description=f'```fix\n{source.title}\n```', color=0x34ad03)
            playing_embed.add_field(name=f'', value=' - '+source.web_url, inline=False)
            playing_embed.set_footer(text=f"requested by {source.requester}")
            source.volume = self.volume
            self.current = source
            self._guild.voice_client.play(source, after=lambda _: self.bot.loop.call_soon_threadsafe(self.next.set))
            view = audiocontroller(self)
            self.np = await self._channel.send(view=view,embed=playing_embed)
            await self.next.wait()

            # Make sure the FFmpeg process is cleaned up.
            source.cleanup()
            self.current = None

            try:
                await self.np.delete()
            except discord.HTTPException:
                pass

    async def destroy(self, guild):
        if guild.id in players:
            del players[guild.id]
        timeout(5)
        await guild.voice_client.disconnect()
        return self.bot.loop.create_task(self._cog.cleanup(guild))

class audiocontroller(discord.ui.View): #สร้างโดยใช้ discord.ui.View
    def __init__(self, player):
        super().__init__(timeout=None)
        self.player = player
        self.resume_button.disabled = True
        #self.update_skip_button()
        #self.message = None

    # def update_skip_button(self):
    #     if not self.player.queue.empty():  # ถ้าคิวไม่ว่าง
    #         for child in self.children:
    #             if isinstance(child, discord.ui.Button) and child.label == "Skip":
    #                 child.disabled = False  # เปิดใช้งานปุ่ม
    #                 child.style = discord.ButtonStyle.primary  # เปลี่ยนสไตล์ปุ่ม
    #     else:  # ถ้าคิวว่าง
    #         for child in self.children:
    #             if isinstance(child, discord.ui.Button) and child.label == "Skip":
    #                 child.disabled = True  # ปิดใช้งานปุ่ม
    #                 child.style = discord.ButtonStyle.secondary
    #     asyncio.create_task(self.refresh_view())

    # async def send(self, channel):
    #     if self.message is None:
    #         # ส่งข้อความครั้งแรกและเก็บ message ที่ส่งไป
    #         self.message = await channel.send("Your message here", view=self)
    #     else:
    #         # ถ้ามี message อยู่แล้ว (จะเป็นการอัปเดต view ใน message ที่มีอยู่)
    #         await self.message.edit(view=self)

    # async def refresh_view(self):  # สร้างฟังก์ชัน refresh_view เพื่ออัปเดต view
    #     if self.message:
    #         await self.message.edit(view=self)

    @discord.ui.button(label="Resume", style=discord.ButtonStyle.secondary)
    async def resume_button(self, interaction: discord.Interaction, resumebutt: discord.ui.Button):
        voice_client = get(bot.voice_clients, guild=interaction.guild)
        if voice_client == None:
            rsembed = discord.Embed(title=f'Warn !', description='```prolog\nSymBot is not in the voice channel\n```', color=0xe36a0e)
            aa1 = await interaction.response.send_message(embed=rsembed)
            await asyncio.sleep(5)
            await aa1.delete()
            return
        if voice_client.channel != interaction.user.voice.channel:
            rsembed = discord.Embed(title=f'Warn !', description='```prolog\nSymBot is not in your voice channel\n```', color=0xe36a0e)
            aa2 = await interaction.response.send_message(embed=rsembed)
            await asyncio.sleep(5)
            await aa2.delete()
            return
        if voice_client and voice_client.is_paused():
            voice_client.resume()
            resumebutt.style = discord.ButtonStyle.secondary
            resumebutt.disabled = True
            for item in self.children:
                if isinstance(item, discord.ui.Button) and item.label == "Pause":
                    item.style = discord.ButtonStyle.primary
                    item.disabled = False
            await interaction.response.edit_message(view=self)
            ppembed = discord.Embed(title=f'Notification : ', description=f"```fix\nThe Music had been resumed by {interaction.user}```", color=0x242424)
            resume_message = await interaction.followup.send(embed=ppembed, ephemeral=False)
            await asyncio.sleep(5)
            await resume_message.delete()
            return
        elif voice_client.is_playing() and voice_client:
            resumebutt.disabled = True
            qqembed = discord.Embed(title=f'Warn !', description='```fix\nMusic is already playing\n```', color=0xe36a0e)
            await interaction.response.send_message(embed=qqembed, ephemeral=False)
            return
        else:
            await interaction.response.send_message('No music is playing', ephemeral=False)
            return

    @discord.ui.button(label="Pause", style=discord.ButtonStyle.primary)
    async def pause_button(self, interaction: discord.Interaction, pausebutt: discord.ui.Button):
        voice_client = get(bot.voice_clients, guild=interaction.guild)
        if voice_client == None:
            rsembed = discord.Embed(title=f'Warn !', description='```prolog\nSymBot is not in the voice channel\n```', color=0xe36a0e)
            await interaction.response.send_message(embed=rsembed)
        if voice_client.channel != interaction.user.voice.channel:
            rsembed = discord.Embed(title=f'Warn !', description='```prolog\nSymBot is not in your voice channel\n```', color=0xe36a0e)
            await interaction.response.send_message(embed=rsembed)
        if voice_client and voice_client.is_playing():
            if voice_client.channel == interaction.user.voice.channel:
                voice_client.pause()
                pausebutt.style = discord.ButtonStyle.secondary
                pausebutt.disabled = True
                for item in self.children:
                    if isinstance(item, discord.ui.Button) and item.label == "Resume":
                        item.style = discord.ButtonStyle.primary
                        item.disabled = False
                await interaction.response.edit_message(view=self)
                ppembed = discord.Embed(title=f'Notification : ', description=f"```fix\nThe Music had been paused by {interaction.user}```", color=0x242424)
                pause_message = await interaction.followup.send(embed=ppembed, ephemeral=False)
                await asyncio.sleep(5)
                await pause_message.delete()
                return
        elif voice_client.is_paused():
            await interaction.followup.send('Music is already paused', ephemeral=True)
        else:
            await interaction.followup.send('No music is playing', ephemeral=True)
            return
        
    @discord.ui.button(label="Skip", style=discord.ButtonStyle.primary)
    async def skip_button1(self, interaction: discord.Interaction, skipbutt: discord.ui.Button):
        voice_client = get(bot.voice_clients, guild=interaction.guild)
        if voice_client is None:
            rsembed = discord.Embed(title=f'Warn !', description='```prolog\nSymBot is not in the voice channel\n```', color=0xe36a0e)
            await interaction.response.send_message(embed=rsembed)
            return

        if voice_client.channel != interaction.user.voice.channel:
            rsembed = discord.Embed(title=f'Warn !', description='```prolog\nSymBot is not in your voice channel\n```', color=0xe36a0e)
            await interaction.response.send_message(embed=rsembed)
            return
        
        player = get_player(interaction)
        if voice_client.is_playing() or voice_client.is_paused():
            voice_client.stop()
            pbembed = discord.Embed(title=f'Notification : ', description=f"```fix\nThe Music has been skipped {interaction.user}```", color=0x242424)
            await interaction.response.defer()
            skip_message = await interaction.followup.send(embed=pbembed, ephemeral=False)
            await asyncio.sleep(5)
            await skip_message.delete()
        else:
            await interaction.followup.send("No more songs in the queue. Skip button is now disabled.", ephemeral=True)
            return
        
    # @discord.ui.button(label="Skip", style=discord.ButtonStyle.primary)
    # async def skip_button(self, interaction: discord.Interaction, skipbutt: discord.ui.Button):
    #     voice_client = get(bot.voice_clients, guild=interaction.guild)
    #     if voice_client is None:
    #         await interaction.response.send_message("SymBot is not in the voice channel", ephemeral=True)
    #         return

    #     if voice_client.channel != interaction.user.voice.channel:
    #         await interaction.response.send_message("Bot is not in your voice channel", ephemeral=True)
    #         return

    #     player = get_player(interaction)
        
    #     if voice_client.is_playing() or voice_client.is_paused():
    #         skipbutt.style = discord.ButtonStyle.secondary  
    #         voice_client.stop()  

    #         if player.queue.empty(): 
    #             skipbutt.disabled = True  
    #             await interaction.response.defer()  
    #             await interaction.followup.send("No more songs in the queue. Skip button is now disabled.", ephemeral=True)
    #         else:
    #             await interaction.response.send_message(f"The Music has been skipped by {interaction.user.mention}", ephemeral=False)
            
    #     else: 
    #         skipbutt.disabled = True  
    #         await self.refresh_view()  
    #         await interaction.message.edit(view=self)
    #         return

    @discord.ui.button(label="Queue", style=discord.ButtonStyle.primary)
    async def queue_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        voice_client = get(bot.voice_clients, guild=interaction.guild)
        if voice_client == None or not voice_client.is_connected():
            rsembed = discord.Embed(title=f'Warn !', description='```prolog\nSymBot is not in the voice channel\n```', color=0xe36a0e)
            await interaction.response.send_message(embed=rsembed)
            return
        player = get_player(interaction)
        if voice_client.channel != interaction.user.voice.channel:    
            rsembed = discord.Embed(title=f'Warn !', description='```prolog\nSymBot is not in your voice channel\n```', color=0xe36a0e)
            await interaction.response.send_message(embed=rsembed)
            return
        queueqq = list(itertools.islice(player.queue._queue, 0, 5)) if hasattr(player.queue, '_queue') else []
        if queueqq:
            queue_list = '\n'.join(f'{index + 1}. {_["title"]}' for index, _ in enumerate(queueqq))
        else:
            queue_list = ""
        embed = discord.Embed(title=f'Queue list - {len(queueqq)}', color=0x00bdfc)
        embed.add_field(name="", value=queue_list, inline=True)
        embed.set_footer(text="requested by " + str(interaction.user))
        await interaction.response.defer()
        message = await interaction.followup.send(embed=embed, ephemeral=False)
        await asyncio.sleep(5)
        await message.delete()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        voice_client = get(bot.voice_clients, guild=interaction.guild)
        if voice_client == None:
            rsembed = discord.Embed(title=f'Warn !', description='```prolog\nSymBot is not in the voice channel\n```', color=0xe36a0e)
            await interaction.response.send_message(embed=rsembed)
            return
        if voice_client.channel != interaction.user.voice.channel:
            rsembed = discord.Embed(title=f'Warn !', description='```prolog\nSymBot is not in your voice channel\n```', color=0xe36a0e)
            await interaction.response.send_message(embed=rsembed)
            return
        player = get_player(interaction)
        if voice_client.is_playing() or voice_client.is_paused():
            player.queue._queue.clear()
            voice_client.stop()
            ppembed = discord.Embed(title=f'Notification : ', description=f"```fix\nThe Music had been Stoped by {interaction.user}```", color=0x242424)
            stop_message = await interaction.response.send_message(embed=ppembed, ephemeral=False,delete_after=5)
        else:
            await interaction.channel.send("No music is playing", delete_after=5)

@bot.command()
async def pppppleums(ctx):
    player = get_player(ctx)
    view = audiocontroller(player)
    await ctx.send('Controller', view=view)

players = {}   
def get_player(interaction):
            guild_id = interaction.guild.id
            if guild_id not in players:
                players[guild_id] = MusicPlayer(interaction)
            return players[guild_id]

class TestView(discord.ui.View): #สร้างใน _init_ mrthod และกำหนดค่าให้กับ button
    def __init__(self):
        super().__init__(timeout=None)
        button = discord.ui.Button(label="Test", style=discord.ButtonStyle.primary)
        button.callback = self.test_button  # กำหนด callback
        self.add_item(button)
        button1 = discord.ui.Button(label="Test1", style=discord.ButtonStyle.primary)
        button1.callback = self.test_button  # กำหนด callback
        self.add_item(button1)


    async def test_button(self, interaction: discord.Interaction):
        await interaction.response.send_message('Hello World', ephemeral=True)

@bot.command()
async def test3647pleumlnwza(ctx):
    view = TestView()
    await ctx.send('Test', view=view)

@bot.command()
async def ttt(ctx):
    ppembed = discord.Embed(title=f'Notification : ', description=f"```fix\nThe Music had been resumed by {ctx.author}```", color=0x242424)
    await ctx.send(embed=ppembed)
@bot.event
async def on_ready():
    print(f'Symbot Online')

@bot.command()
async def help(ctx):
    embed = discord.Embed(title="📜 Help Menu", description="List of available commands:", color=0x00bdfc)
    embed.add_field(name="!play", value="Play music <Url> or <Title>", inline=True)
    embed.add_field(name="!stop", value="Stop the music", inline=True)
    embed.add_field(name="!pause", value="Pause the music", inline=True)
    embed.add_field(name="!resume", value="Resume the music", inline=True)
    embed.add_field(name="!queue", value="Show the music queue", inline=True)
    embed.add_field(name="!skip", value="Skip the current music", inline=True)
    embed.add_field(name="!dc", value="Disconnect the bot from the voice channel", inline=True)
    await ctx.send(embed=embed)

@bot.command()
async def play(ctx, *, url=None):
    #if isinstance(ctx.view, audiocontroller):
        #ctx.view.update_skip_button()  # อัพเดตปุ่ม Skip
        #await ctx.view.refresh_view()
    if url is None:
        yyembed = discord.Embed(title=f'Notification : ', description=f"```fix\nPlease Input Link [!play <url> <title>]\n```", color=0x242424)
        await ctx.send(embed=yyembed, delete_after=5)
        return
    if not ctx.author.voice:
        qtembed = discord.Embed(title=f'Notification : ', description=f"```fix\nYou are not in Voice Channel\n```", color=0x242424)
        await ctx.channel.send(embed=qtembed, delete_after=5)
        return
    channel = ctx.author.voice.channel
    voice_client = get(bot.voice_clients, guild=ctx.guild)

    if voice_client == None:
        if not ctx.author.voice:
            poembed = discord.Embed(title=f'Notification : ', description=f"```fix\nPlease join the Voice channel\n```", color=0x242424)
            await ctx.channel.send(embed=poembed, delete_after=5)
            return
        await ctx.channel.send('Bot was joined', delete_after=5)
        await channel.connect()
        voice_client = get(bot.voice_clients, guild=ctx.guild)

    YDL_OPTION = {"format": 'bestaudio/best', 'noplaylist': 'True'}
    FFMPEG_OPTIONS = {'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5', 'options': '-vn'}

    if voice_client.channel != ctx.author.voice.channel:
        bhembed = discord.Embed(title=f'Notification : ', description=f"```fix\nSymbot is not in your voice channel\n```", color=0x242424)
        await ctx.channel.send(embed=bhembed, delete_after=5)
        return

    await ctx.typing()
    _player = get_player(ctx)
    source = await YTDLSource.create_source(ctx, search=url, loop=bot.loop, download=False)

    await _player.queue.put(source)
    await asyncio.sleep(3)
    await ctx.message.delete()
    #if isinstance(ctx.view, audiocontroller):
        #ctx.view.update_skip_button()  # อัพเดตปุ่ม Skip
        #await ctx.view.refresh_view()  # รีเฟรช view เพื่อให้เห็นการเปลี่ยนแปลง
    #else:
        #print('No view found1')


players = {} #เก็บข้อมูลของเพลงที่เล่นอยู่ ในแต่ละเซิฟเวอร์ ในกรณีที่บอทเราอยู่หลายเซิฟ
def get_player(ctx):
    try:
        player = players[ctx.guild.id]
    except KeyError:
        player = MusicPlayer(ctx)
        players[ctx.guild.id] = player
    return player


#@bot.command()
#async def play(ctx ,url=None):
    #if url is None:
        #await ctx.channel.send("Please Input Link `!play <url>`")
        #return
    #if not ctx.author.voice:
        #await ctx.channel.send("คุณไม่อยู่ใน Voice Channel")
        #return
    #channel = ctx.author.voice.channel
    #voice_client = get(bot.voice_clients, guild = ctx.guild) 
    
    #if voice_client == None: #ป้องกันไม่ให้ Connectซ้ำ มันจะEror
        #if not ctx.author.voice:
            #await ctx.channel.send("เข้าห้องก่อน")
            #return
        #await ctx.channel.send('Bot was joined')
        #await channel.connect()
        #voice_client = get(bot.voice_clients, guild = ctx.guild)
        


    #YDL_OPTION = {"format" : 'bestaudio/best', 'noplaylist' : 'True'} #false = เอา Playlist
    #FFMPEG_OPTIONS = {'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5','options': '-vn'}

    #if not voice_client.is_playing():
        #try:
            #with YoutubeDL(YDL_OPTION) as ydl: 
                #info = ydl.extract_info(url, download=False)
            #URL = info["url"]
            #voice_client.play(discord.FFmpegPCMAudio(URL, **FFMPEG_OPTIONS))
            #voice_client.is_playing()
        #except Exception as error:
            #Err = re.search(r'\[.+?\]', str(error))
            #if Err:
                #error_output = Err.group(0)
                #print(f"เกิดข้อผิดพลาดไม่สามารถเล่นเพลงได้: {error_output}")
                #await ctx.channel.send(f"เกิดข้อผิดพลาดไม่สามารถเล่นเพลงได้")
            #else:
                #await ctx.channel.send(f"เกิดข้อผิดพลาดไม่สามารถเล่นเพลงได้")
    #else:
            #await ctx.channel.send('is playing a Music')
            #return

@bot.command()
async def dc(ctx):
    voice_client = get(bot.voice_clients, guild=ctx.guild)
    if voice_client.channel != ctx.author.voice.channel:
        rsembed = discord.Embed(title=f'Warn !', description='```prolog\nSymBot is not in your voice channel\n```', color=0xe36a0e)
        await ctx.channel.send(embed=rsembed)
        return
    if voice_client and voice_client.is_connected():
        del players[ctx.guild.id]
        await voice_client.disconnect()
        tyembed = discord.Embed(title=f'Notification : ', description=f"```fix\nBot has been disconnected by {ctx.author.mention}\n```", color=0x242424)
        await ctx.channel.send(embed=tyembed, delete_after=5)
    else:
        tqembed = discord.Embed(title=f'Notification : ', description=f"```fix\nBot has been disconnected by {ctx.author.mention}\n```", color=0x242424)
        await ctx.channel.send(embed=tqembed, delete_after=5)

@bot.command()
async def stop(ctx):
    voice_client = get(bot.voice_clients, guild=ctx.guild)
    if voice_client == None:
        tgembed = discord.Embed(title=f'Warn !', description='```prolog\nSymBot is not in the voice channel\n```', color=0xe36a0e)
        await ctx.channel.send(embed=tgembed)
        return
    if voice_client.channel != ctx.author.voice.channel:
        emyubed = discord.Embed(title=f'Warn !', description='```prolog\nSymBot is not in your voice channel\n```', color=0xe36a0e)
        await ctx.channel.send(embed=emyubed, delete_after=5)
        return
    player = get_player(ctx)
    if voice_client.is_playing() or voice_client.is_paused():
        player.queue._queue.clear()
        voice_client.stop()
        rrembed = discord.Embed(title=f'Notification : ', description=f'```fix\nThe Music has been stopped by {ctx.author.mention}\n```', color=0xe36a0e)
        await ctx.channel.send(embed=rrembed, delete_after=5)
    else:
        tuembed = discord.Embed(title=f'Notification : ', description=f'```fix\nNo music is playing\n```', color=0xe36a0e)
        await ctx.channel.send(embed=tuembed, delete_after=5)


@bot.command()
async def pause(ctx):
    voice_client = get(bot.voice_clients, guild=ctx.guild)
    if voice_client == None:
        rsembed = discord.Embed(title=f'Warn !', description='```prolog\nSymBot is not in the voice channel\n```', color=0xe36a0e)
        await ctx.channel.send(embed=rsembed)
        return
    if voice_client.channel != ctx.author.voice.channel:
        rsembed = discord.Embed(title=f'Warn !', description='```prolog\nSymBot is not in your voice channel\n```', color=0xe36a0e)
        await ctx.channel.send(embed=rsembed)
        return
    voice_client.pause()
    uoembed = discord.Embed(title=f'Notification : ', description=f'```fix\nThe Music has been puased by {ctx.author}\n```', color=0xe36a0e)
    await ctx.channel.send(embed=uoembed,delete_after=5)

@bot.command()
async def resume(ctx):
    voice_client = get(bot.voice_clients, guild=ctx.guild)
    if voice_client == None:
        rsembed = discord.Embed(title=f'Warn !', description='```prolog\nSymBot is not in the voice channel\n```', color=0xe36a0e)
        await ctx.channel.send(embed=rsembed)
        return
    if voice_client.channel != ctx.author.voice.channel:
        rsembed = discord.Embed(title=f'Warn !', description='```prolog\nSymBot is not in your voice channel\n```', color=0xe36a0e)
        await ctx.channel.send(embed=rsembed)
        return
    voice_client.resume()
    ipembed = discord.Embed(title=f'Notification : ', description=f'```fix\nThe Music had been resumed by {ctx.author}\n```', color=0xe36a0e)
    await ctx.channel.send(embed=ipembed,delete_after=5)

@bot.command()
async def queue(ctx):
    voice_client = get(bot.voice_clients, guild=ctx.guild)
    if voice_client == None or not voice_client.is_connected():
        rsembed = discord.Embed(title=f'Warn !', description='```prolog\nSymBot is not in the voice channel\n```', color=0xe36a0e)
        await ctx.channel.send(embed=rsembed)
        return
    
    player = get_player(ctx)
    if voice_client.channel != ctx.author.voice.channel:    
        rsembed = discord.Embed(title=f'Warn !', description='```prolog\nSymBot is not in your voice channel\n```', color=0xe36a0e)
        await ctx.channel.send(embed=rsembed)
        return
    queueqq = list(itertools.islice(player.queue._queue, 0, 5))
    if queueqq:
        queue_list = '\n'.join(f'{index + 1}. {_["title"]}' for index, _ in enumerate(queueqq))
    else:
        queue_list = ""

    embed = discord.Embed(title=f'Queue list - {len(queueqq)}', description="", color=0x00bdfc)
    embed.add_field(name=queue_list, value="", inline=True)
    embed.set_footer(text="requested by " + str(ctx.author))
    await ctx.send(embed=embed, delete_after=10)

@bot.command()
async def skip(ctx):
    voice_client = get(bot.voice_clients, guild=ctx.guild)
    if voice_client == None or not voice_client.is_connected():
        rsembed = discord.Embed(title=f'Warn !', description='```prolog\nSymBot is not in the voice channel\n```', color=0xe36a0e)
        await ctx.channel.send(embed=rsembed)
        return
    
    if voice_client.is_paused():
        pass
    elif not voice_client.is_playing():
        await ctx.channel.send("No Music is playing",delete_after=5)
        return
    if voice_client.channel != ctx.author.voice.channel:    
        rsembed = discord.Embed(title=f'Warn !', description='```prolog\nSymBot is not in your voice channel\n```', color=0xe36a0e)
        await ctx.channel.send(embed=rsembed)
        return
    voice_client.stop()
    tmembed = discord.Embed(title=f'Notification : ', description=f'```fix\nThe Music has been skiped by {ctx.author}\n```', color=0xe36a0e)
    await ctx.channel.send(embed=tmembed,delete_after=5)
    
bot.run(TOKEN)
