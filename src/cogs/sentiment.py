import re
import boto3
import typing
import discord
import json
import asyncio
import string
import random
import datetime
from discord.ext import commands
from openrobot import api_wrapper

from googleapiclient import discovery, errors as googleapi_errors

from jishaku.functools import executor_function

from collections import Counter

from cogs.utils import is_guild_owner, SentimentWarnings, SentimentMostWarnings

def sentiment_warnings_check():
    def predicate(ctx):
        if ctx.command == ctx.bot.get_command('sentiment warnings') and (member := ctx.kwargs.get('member')):
            if member.id == ctx.author.id:
                return True
            else:
                return False

    return commands.check(predicate)

sentiment_check = commands.check_any(commands.is_owner(), commands.has_permissions(manage_guild=True, manage_messages=True), commands.guild_only(), is_guild_owner(), sentiment_warnings_check())

class Sentiment(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

        self.perspective = discovery.build(
            "commentanalyzer",
            "v1alpha1",
            developerKey=self.bot.config.get('authentication').get('perspective'),
            discoveryServiceUrl="https://commentanalyzer.googleapis.com/$discovery/rest?version=v1alpha1",
            static_discovery=False,
        )

        if self.bot.config.get('authentication').get('aws') and all(bool(x) for x in self.bot.config.get('authentication').get('aws').values()):
            self.rekognition = boto3.client(
                "rekognition",
                aws_access_key_id=self.bot.config.get('authentication').get('aws').get('id'),
                aws_secret_access_key=self.bot.config.get('authentication').get('aws').get('secret'),
                region_name=self.bot.config.get('authentication').get('aws').get('region'),
            )

            self.transcribe = boto3.client(
                "transcribe",
                aws_access_key_id=self.bot.config.get('authentication').get('aws').get('id'),
                aws_secret_access_key=self.bot.config.get('authentication').get('aws').get('secret'),
                region_name=self.bot.config.get('authentication').get('aws').get('region'),
            )

            self.s3 = boto3.client(
                "s3",
                aws_access_key_id=self.bot.config.get('authentication').get('aws').get('id'),
                aws_secret_access_key=self.bot.config.get('authentication').get('aws').get('secret'),
                region_name=self.bot.config.get('authentication').get('aws').get('region'),
            )
        else:
            self.rekognition = boto3.client("rekognition")
            self.transcribe = boto3.client("transcribe")
            self.s3 = boto3.client("s3")

        self.s3_bucket = self.bot.config.get('authentication').get('aws').get('s3-bucket')

        self.lock = asyncio.Lock()

    @executor_function
    def _run_perspectiveapi(self, text: str) -> dict:
        analyze_request = {
            'comment': { 'text': text },
            'requestedAttributes': {'TOXICITY': {}}
        }

        response = self.perspective.comments().analyze(body=analyze_request).execute()

        return response

    async def _run_openrobotapi(self, text: str) -> dict:
        x: api_wrapper.SentimentResult = await self.bot.openrobot.sentiment(text)

        task_id = x.task_id

        while x.result not in ['COMPLETED', 'FAILED']:
            x = await self.bot.openrobot.sentiment_get(task_id)

        return x

    async def run_sentiment(self, text: str) -> dict:
        #sentiment_method = self.bot.config.get('authentication').get('sentiment').get('use')
        #
        # if sentiment_method == 'try-both prioritize-perspective':
        #     try:
        #         x = await self._run_perspectiveapi(text)
        #         method = "perspective"
        #     except:
        #         x = None
        #         method = None

        #     if not x:
        #         try:
        #             x = await self._run_openrobotapi(text)
        #             method = "openrobot"
        #         except:
        #             x = None
        #             method = None
        # elif sentiment_method == 'try-both prioritize-openrobot':
        #     try:
        #         x = await self._run_openrobotapi(text)
        #         method = "openrobot"
        #     except:
        #         x = None
        #         method = None

        #     if not x:
        #         try:
        #             x = await self._run_perspectiveapi(text)
        #             method = "perspective"
        #         except:
        #             x = None
        #             method = None
        # elif sentiment_method == 'perspective':
        #     try:
        #         x = await self._run_perspectiveapi(text)
        #         method = "perspective"
        #     except:
        #         x = None
        #         method = None
        # elif sentiment_method == 'openrobot':
        #     try:
        #         x = await self._run_openrobotapi(text)
        #         method = "openrobot"
        #     except:
        #         x = None
        #         method = None
        # else:
        #     raise ValueError(f'Invalid sentiment method: {sentiment_method}')

        x = await self._run_perspectiveapi(text)

        return x

    @commands.group(invoke_without_command=True, usage="[msg or content]")
    async def sentiment(self, ctx: commands.Context, msg_or_content: discord.Message | str = commands.Option(None, description='The message ID/URL or a specific text.')):
        """
        Configures the sentiment check of messages
        """

        if ctx.invoked_subcommand is None:
            if not msg_or_content and not ctx.message.reference:
                return await ctx.send_help(ctx.command)

            if ctx.message.reference and not msg_or_content:
                msg_or_content = ctx.message.reference.resolved

            if isinstance(msg_or_content, discord.Message):
                content = msg_or_content.content
            else:
                content = msg_or_content

            try:
                response = await self.run_sentiment(content)
            except googleapi_errors.HttpError as e:
                return await ctx.send(f'Error: `{e.reason}`')

            if not response:
                return await ctx.send("Could not find any sentiment data")

            percent = response['attributeScores']['TOXICITY']['summaryScore']['value'] * 100

            is_negative = percent > 75

            if is_negative:
                embed = discord.Embed(title="Sentiment:", description=f"That message is negative with a sentiment percentage of `{round(percent, 1)}%`", color=discord.Colour.red())
            else:
                embed = discord.Embed(title="Sentiment:", description=f"That message is positive with a sentiment percentage of `{round(percent, 1)}%`", color=discord.Colour.green())

            await ctx.reply(embed=embed)

    @sentiment.command('setup')
    @sentiment_check
    async def sentiment_setup(self, ctx: commands.Context):
        """
        Setup the sentiment check
        """

        config = await self.bot.pool.fetchrow("SELECT * FROM sentiment WHERE guild_id = $1", ctx.guild.id)

        if config:
            await ctx.reply("You have already configured the sentiment check! Do you want to reconfigure? Say `yes` or `no`.")

            choice = await self.bot.wait_for('message', check=lambda m: m.author == ctx.author and m.channel == ctx.channel and m.content.lower() in ['y', 'yes', 'ye', 'no', 'n'])

            if choice.content.lower() in ['y', 'yes', 'ye']:
                await ctx.reply("Reconfiguring...")
            else:
                return await ctx.reply("Cancelled.")

        await ctx.reply("Welcome to the setup for the sentiment check!\n\nWe will ask you a question.\n\nTo abort the setup, say `cancel` or `stop`.")

        check = lambda m: m.author == ctx.author and m.channel == ctx.channel

        await ctx.reply("Please mention the channel you want to log sentiments.")

        while True:
            try:
                modlog = await self.bot.wait_for('message', check=check, timeout=60)
            except asyncio.TimeoutError:
                return await ctx.send("Took to long to respond. Please try again later.")

            if modlog.content.lower() in ['cancel', 'stop']:
                return await ctx.send("Cancelled.")

            try:
                modlog = await commands.TextChannelConverter().convert(ctx, modlog.content)
            except:
                await ctx.send("That is not a valid channel. Please try again.")
                continue
            else:
                break

        if config:
            await self.bot.pool.execute("DELETE FROM sentiment WHERE guild_id = $1", ctx.guild.id)

        await self.bot.pool.execute("INSERT INTO sentiment (guild_id, modlog_channel) VALUES ($1, $2)", ctx.guild.id, modlog.id)

        await ctx.send("Setup complete!")

        # await ctx.reply("Please mention the channel ")

        # regex = re.compile(r'(<#\d+>+|none)', re.IGNORECASE)

        # while True:
        #     chan = await self.bot.wait_for('message', check=check)
            
        #     if channels := regex.findall(m.content):
        #         if channels[0] == "none":
        #             break
        #         elif 'none' in [x.lower() for x in channels] and len(channels) != 1:
        #             await ctx.reply("You can only use `none` if you did not provide any channels.")
        #             continue
        #         else:
        #             channel = []
        #             for chan in channels:
        #                 try:
        #                     channel.append(await commands.TextChannelConverter().convert(ctx, chan))
        #                 except:
        #                     await ctx.send("I cannot figure out ")
        #                 break

        # def check(m):
        #     if m.author == ctx.author and m.channel == ctx.channel:
        #         if channels := regex.findall(m.content):
        #             if channels.lower()[0] == 'none':
        #                 return True
        #             elif 'none' in [x.lower() for x in channels] and len(channels) != 

        #     return False

    @sentiment.command('warnings', aliases=['warns'])
    @sentiment_check
    async def sentiment_warnings(self, ctx: commands.Context, *, member: discord.Member = commands.Option(None, description='The member to check warnings for.')):
        """
        Shows the sentiment warnings leaderboard or a specific member's sentiment warnings.
        """
        
        # if member is None, do leaderboard for most sentiment warnings.
        # If not, show the warnings for that member.

        if member:
            warnings = await self.bot.pool.fetch("""
            SELECT * FROM user_warnings WHERE guild_id = $1 AND case_type = 'sentiment' AND user_id = $2
            """, ctx.guild.id, member.id)

            if not warnings:
                return await ctx.send(f"No warnings found for {'you' if member == ctx.author else member.mention}!", allowed_mentions=discord.AllowedMentions.none())

            return await SentimentWarnings(warnings, per_page=1).start(ctx, timeout=180)
        else:
            warnings = await self.bot.pool.fetch("""
            SELECT * FROM user_warnings WHERE guild_id = $1 AND case_type = 'sentiment'
            """, ctx.guild.id)

            if not warnings:
                return await ctx.send("No sentiment warnings found in this server!")

            # Doing the count logic:
            c = Counter([x['user_id'] for x in warnings]) # Have a Counter to generate the amount of the user's sentiment warnings

            data_count = dict(c) # Convert the Counter to a dict with a format of {user_id: amount}

            l = [{'user_id': k, 'warns': v} for k, v in data_count.items()] # Make the dict into a list of dicts with the user_id and amount of warnings

            l.sort(key=lambda x: x['warns'], reverse=True) # Sort the amount of warnings

            # Starting the pagination
            await SentimentMostWarnings(l, per_page=10).start(ctx, timeout=180)

    @sentiment.command('my-warnings', aliases=['mywarnings', 'my_warnings', 'my-warning', 'mywarning', 'my_warning', 'my-warns', 'mywarns', 'my_warns'])
    async def my_warnings(self, ctx: commands.Context):
        """
        Shows your sentiment warnings in this server.
        """
        
        return await self.sentiment_warnings(ctx, member=ctx.author)

    @sentiment.group('settings', invoke_without_command=True, aliases=['config', 'configs', 'setting', 'configure'])
    @sentiment_check
    async def sentiment_settings(self, ctx: commands.Context):
        """
        Configure the sentiment check
        """

        if ctx.invoked_subcommand is None:
            return await self.sentiment_settings_show(ctx)
    
    @sentiment_settings.command('show', aliases=['list'])
    @sentiment_check
    async def sentiment_settings_show(self, ctx: commands.Context):
        """
        Shows the sentiment settings set.
        """

        config = await self.bot.pool.fetchrow("SELECT * FROM sentiment WHERE guild_id = $1", ctx.guild.id)

        if not config:
            return await ctx.send(f"You have not setup sentiment check for this server yet. Invoke the `{ctx.prefix}sentiment setup` command to do so.")

        embed = discord.Embed(color=self.bot.color, title="Sentiment Settings:")

        embed.add_field(
            name="Modlog Channel:",
            value=f"<#{config['modlog_channel']}>",
        )

        embed.add_field(
            name="Enabled:",
            value=f"{config['is_enabled']}",
        )

        s = "- "

        for user_id in config['users_ignored']:
            s += f'<@{user_id}>\n- '

        s = s[:-2].strip()

        s = s or "None."

        embed.add_field(
            name="Users Ignored:",
            value=s,
            inline=False,
        )

        s = "- "

        for role_id in config['roles_ignored']:
            s += f'<@&{user_id}>\n- '

        s = s[:-2].strip()

        s = s or "None."

        embed.add_field(
            name="Roles Ignored:",
            value=s,
            inline=False,
        )

        s = "- "

        for channel_id in config['channels_ignored']:
            s += f'<#{channel_id}>\n- '

        s = s[:-2].strip()

        s = s or "None."

        embed.add_field(
            name="Channels Ignored:",
            value=s,
            inline=False,
        )

        await ctx.reply(embed=embed)

    # Discord doesnt allow sub-subgroups / sub-sub-subcommands in slash commands, so this is our workaround I guess.

    @sentiment_settings.command('enable', slash_command=False)
    @sentiment_check
    async def sentiment_settings_enable(self, ctx: commands.Context):
        """
        Enables sentiment in this server
        """

        config = await self.bot.pool.fetchrow("SELECT * FROM sentiment WHERE guild_id = $1", ctx.guild.id)

        if not config:
            return await ctx.send(f"You have not setup sentiment check for this server yet. Invoke the `{ctx.prefix}sentiment setup` command to do so.")

        if config['is_enabled'] is True:
            return await ctx.send("Sentiment is already enabled in this server.")

        await self.bot.pool.execute("""
        UPDATE sentiment SET is_enabled = 't' WHERE guild_id = $1
        """, ctx.guild.id)

        await ctx.send("Sentiment has been enabled.")

    @sentiment_settings.command('disable', slash_command=False)
    @sentiment_check
    async def sentiment_settings_disable(self, ctx: commands.Context):
        """
        Enables sentiment in this server
        """

        config = await self.bot.pool.fetchrow("SELECT * FROM sentiment WHERE guild_id = $1", ctx.guild.id)

        if not config:
            return await ctx.send(f"You have not setup sentiment check for this server yet. Invoke the `{ctx.prefix}sentiment setup` command to do so.")

        if config['is_enabled'] is False:
            return await ctx.send("Sentiment is already disabled in this server.")

        await self.bot.pool.execute("""
        UPDATE sentiment SET is_enabled = 'f' WHERE guild_id = $1
        """, ctx.guild.id)

        await ctx.send("Sentiment has been disabled.")

    @sentiment_settings.command('modlog', aliases=['modlogchannel', 'modlog-channel', 'modlog_channel'])
    @sentiment_check
    async def update_modlog_channel(self, ctx: commands.Context, channel: discord.TextChannel):
        """
        Updates the modlog channel for this server.
        """

        config = await self.bot.pool.fetchrow("SELECT * FROM sentiment WHERE guild_id = $1", ctx.guild.id)

        if not config:
            return await ctx.send(f"You have not setup sentiment check for this server yet. Invoke the `{ctx.prefix}sentiment setup` command to do so.")

        await self.bot.pool.execute("""
        UPDATE sentiment SET modlog_channel = $1 WHERE guild_id = $2
        """, channel.id, ctx.guild.id)

        await ctx.send(f"Modlog channel has been updated to {channel.mention}.")

    @sentiment_settings.group('user', slash_command=False, invoke_without_command=True)
    async def sentiment_settings_user(self, ctx: commands.Context):
        """
        Configures User Ignore for sentiment.
        """

        if ctx.invoked_subcommand is None:
            return await ctx.send_help(ctx.command)

    @sentiment_settings_user.command('add', aliases=['+'], slash_command=False)
    async def sentiment_settings_user_add(self, ctx: commands.Context, users: commands.Greedy[discord.Member]):
        """
        Adds a user to the user ignore list.
        """

        config = await self.bot.pool.fetchrow("SELECT * FROM sentiment WHERE guild_id = $1", ctx.guild.id)

        if not config:
            return await ctx.send(f"You have not setup sentiment check for this server yet. Invoke the `{ctx.prefix}sentiment setup` command to do so.")

        if not users:
            return await ctx.send("Please provide a user.")

        if len(users) > 1:
            user = users[0]

            if user.id in config['users_ignored']:
                return await ctx.send("That user is already ignored.")

            l = users
        else:
            l = [x.id for x in users if x.id not in config['users_ignored']]

            if not l:
                return await ctx.send("All the users are already ignored.")

        l = config['users_ignored'] + l

        await self.bot.pool.execute("UPDATE sentiment SET users_ignored = $2 WHERE guild_id = $1", ctx.guild.id, l)

        await ctx.send(f"Added {len(l)} user{'s' if len(l) > 1 else ''} to the user ignore list.")

    @sentiment_settings_user.command('remove', aliases=['-', 'rm'], slash_command=False)
    async def sentiment_settings_user_remove(self, ctx: commands.Context, users: commands.Greedy[discord.Member]):
        """
        Removes a user to the user ignore list.
        """

        config = await self.bot.pool.fetchrow("SELECT * FROM sentiment WHERE guild_id = $1", ctx.guild.id)

        if not config:
            return await ctx.send(f"You have not setup sentiment check for this server yet. Invoke the `{ctx.prefix}sentiment setup` command to do so.")

        if not users:
            return await ctx.send("Please provide a user.")

        if len(users) > 1:
            user = users[0]

            if user.id not in config['users_ignored']:
                return await ctx.send("That user is not ignored.")

            l = users
        else:
            l = [x.id for x in users if x.id in config['users_ignored']]

            if not l:
                return await ctx.send("All the users are not ignored.")

        for i in l:
            config['users_ignored'].remove(i)

        await self.bot.pool.execute("UPDATE sentiment SET users_ignored = $2 WHERE guild_id = $1", ctx.guild.id, config['users_ignored'])

        await ctx.send(f"Removed {len(l)} user{'s' if len(l) > 1 else ''} to the user ignore list.")

    @sentiment_settings.group('role', slash_command=False, invoke_without_command=True)
    async def sentiment_settings_role(self, ctx: commands.Context):
        """
        Configures Role Ignore for sentiment.
        """

        if ctx.invoked_subcommand is None:
            return await ctx.send_help(ctx.command)

    @sentiment_settings_role.command('add', aliases=['+'], slash_command=False)
    async def sentiment_settings_role_add(self, ctx: commands.Context, roles: commands.Greedy[discord.Role]):
        """
        Adds a role to the role ignore list.
        """

        config = await self.bot.pool.fetchrow("SELECT * FROM sentiment WHERE guild_id = $1", ctx.guild.id)

        if not config:
            return await ctx.send(f"You have not setup sentiment check for this server yet. Invoke the `{ctx.prefix}sentiment setup` command to do so.")

        if not roles:
            return await ctx.send("Please provide a role.")

        if len(roles) > 1:
            role = roles[0]

            if role.id in config['roles_ignored']:
                return await ctx.send("That role is already ignored.")

            l = roles
        else:
            l = [x.id for x in roles if x.id not in config['roles_ignored']]

            if not l:
                return await ctx.send("All the roles are already ignored.")

        l = config['roles_ignored'] + l

        await self.bot.pool.execute("UPDATE sentiment SET roles_ignored = $2 WHERE guild_id = $1", ctx.guild.id, l)

        await ctx.send(f"Added {len(l)} role{'s' if len(l) > 1 else ''} to the role ignore list.")

    @sentiment_settings_role.command('remove', aliases=['-', 'rm'], slash_command=False)
    async def sentiment_settings_role_remove(self, ctx: commands.Context, roles: commands.Greedy[discord.Role]):
        """
        Removes a role to the role ignore list.
        """

        config = await self.bot.pool.fetchrow("SELECT * FROM sentiment WHERE guild_id = $1", ctx.guild.id)

        if not config:
            return await ctx.send(f"You have not setup sentiment check for this server yet. Invoke the `{ctx.prefix}sentiment setup` command to do so.")

        if not roles:
            return await ctx.send("Please provide a role.")

        if len(roles) > 1:
            role = roles[0]

            if role.id not in config['roles_ignored']:
                return await ctx.send("That role is not ignored.")

            l = roles
        else:
            l = [x.id for x in roles if x.id in config['roles_ignored']]

            if not l:
                return await ctx.send("All the roles are not ignored.")

        for i in l:
            config['roles_ignored'].remove(i)

        await self.bot.pool.execute("UPDATE sentiment SET roles_ignored = $2 WHERE guild_id = $1", ctx.guild.id, config['roles_ignored'])

        await ctx.send(f"Removed {len(l)} role{'s' if len(l) > 1 else ''} to the role ignore list.")

    @sentiment_settings.group('channel', slash_command=False, invoke_without_command=True)
    async def sentiment_settings_channel(self, ctx: commands.Context):
        """
        Configures Channel Ignore for sentiment.
        """

        if ctx.invoked_subcommand is None:
            return await ctx.send_help(ctx.command)

    @sentiment_settings_channel.command('add', aliases=['+'], slash_command=False)
    async def sentiment_settings_channel_add(self, ctx: commands.Context, channels: commands.Greedy[discord.TextChannel]):
        """
        Adds a channel to the channel ignore list.
        """

        config = await self.bot.pool.fetchrow("SELECT * FROM sentiment WHERE guild_id = $1", ctx.guild.id)

        if not config:
            return await ctx.send(f"You have not setup sentiment check for this server yet. Invoke the `{ctx.prefix}sentiment setup` command to do so.")

        if not channels:
            return await ctx.send("Please provide a channel.")

        if len(channels) > 1:
            channel = channels[0]

            if channel.id in config['channels_ignored']:
                return await ctx.send("That channel is already ignored.")

            l = channels
        else:
            l = [x.id for x in channels if x.id not in config['channels_ignored']]

            if not l:
                return await ctx.send("All the channels are already ignored.")

        l = config['channels_ignored'] + l

        await self.bot.pool.execute("UPDATE sentiment SET channels_ignored = $2 WHERE guild_id = $1", ctx.guild.id, l)

        await ctx.send(f"Added {len(l)} channel{'s' if len(l) > 1 else ''} to the channel ignore list.")

    @sentiment_settings_channel.command('remove', aliases=['-', 'rm'], slash_command=False)
    async def sentiment_settings_channel_remove(self, ctx: commands.Context, channels: commands.Greedy[discord.TextChannel]):
        """
        Removes a channel to the channel ignore list.
        """

        config = await self.bot.pool.fetchrow("SELECT * FROM sentiment WHERE guild_id = $1", ctx.guild.id)

        if not config:
            return await ctx.send(f"You have not setup sentiment check for this server yet. Invoke the `{ctx.prefix}sentiment setup` command to do so.")

        if not channels:
            return await ctx.send("Please provide a channel.")

        if len(channels) > 1:
            channel = channels[0]

            if channel.id not in config['channels_ignored']:
                return await ctx.send("That channel is not ignored.")

            l = channels
        else:
            l = [x.id for x in channels if x.id in config['channels_ignored']]

            if not l:
                return await ctx.send("All the channels are not ignored.")

        for i in l:
            config['channels_ignored'].remove(i)

        await self.bot.pool.execute("UPDATE sentiment SET channels_ignored = $2 WHERE guild_id = $1", ctx.guild.id, config['channels_ignored'])

        await ctx.send(f"Removed {len(l)} channel{'s' if len(l) > 1 else ''} to the channel ignore list.")

    #@sentiment_settings.command('user-add', message_command=False, hidden=True)
    @sentiment_check
    async def sentiment_settings_user_add_slash(self, ctx: commands.Context, user: discord.Member = commands.Option(description='The user to add to the user ignore list.')):
        """
        Adds a user to the sentiment ignore list.
        """

        return await self.sentiment_settings_user_add(ctx, [user,])

    #@sentiment_settings.command('user-remove', message_command=False, hidden=True)
    @sentiment_check
    async def sentiment_settings_user_remove_slash(self, ctx: commands.Context, user: discord.Member = commands.Option(description='The user to remove from the user ignore list.')):
        """
        Removes a user to the sentiment ignore list.
        """

        return await self.sentiment_settings_user_remove(ctx, [user,])

    #@sentiment_settings.command('role-add', message_command=False, hidden=True)
    @sentiment_check
    async def sentiment_settings_role_add_slash(self, ctx: commands.Context, role: discord.Role = commands.Option(description='The role to add to the role ignore list.')):
        """
        Adds a role to the sentiment ignore list.
        """

        return await self.sentiment_settings_role_add(ctx, [role,])

    #@sentiment_settings.command('role-remove', message_command=False, hidden=True)
    @sentiment_check
    async def sentiment_settings_role_remove_slash(self, ctx: commands.Context, role: discord.Role = commands.Option(description='The role to remove from the role ignore list.')):
        """
        Removes a role to the sentiment ignore list.
        """

        return await self.sentiment_settings_role_remove(ctx, [role,])

    #@sentiment_settings.command('channel-add', message_command=False, hidden=True)
    @sentiment_check
    async def sentiment_settings_channel_add_slash(self, ctx: commands.Context, channel: discord.TextChannel = commands.Option(description='The channel to add to the channel ignore list.')):
        """
        Adds a channel to the sentiment ignore list.
        """

        return await self.sentiment_settings_channel_add(ctx, [channel,])

    #@sentiment_settings.command('channel-remove', message_command=False, hidden=True)
    @sentiment_check
    async def sentiment_settings_channel_remove_slash(self, ctx: commands.Context, channel: discord.TextChannel = commands.Option(description='The channel to remove from the channel ignore list.')):
        """
        Removes a channel to the sentiment ignore list.
        """

        return await self.sentiment_settings_channel_remove(ctx, [channel,])

    @commands.Cog.listener()
    async def on_message(self, msg: discord.Message):
        # Checks stuff:
        sentiment_config = self.bot.config.get('sentiment')

        s3 = self.bot.config.get('authentication').get('aws').get('s3')
        s3_bucket = s3.get('bucket')
        s3_prefix = s3.get('prefix')

        bucket = s3_bucket.split('/')[0]

        if sentiment_config['ignore-bots']:
            if msg.author.bot:
                return

        if not sentiment_config['content'] and not sentiment_config['audio'] and not sentiment_config['image']:
            return

        config = await self.bot.pool.fetchrow("SELECT * FROM sentiment WHERE guild_id = $1", msg.guild.id)

        if not config:
            return

        if not config['is_enabled']:
            return

        if msg.author.id in config['users_ignored']:
            return

        for role in msg.author.roles:
            if role.id in config['roles_ignored']:
                return

        if msg.channel.id in config['channels_ignored']:
            return

        async with self.lock:

            method = None

            if sentiment_config['content'] and msg.content:
                response = await self.run_sentiment(msg.content)

                percent = response['attributeScores']['TOXICITY']['summaryScore']['value'] * 100

                is_negative = percent > 75

                if is_negative:
                    method = "content"
            
            for attachment in msg.attachments:
                if attachment.content_type.startswith('audio/') and not method and sentiment_config['audio']:
                    # Upload to S3:
                    async with self.bot.session.get(attachment.url) as resp:
                        file_bytes = await resp.read()

                    with open(f'./src/speech-to-text/{job_name}.mp3', 'wb') as f:
                        f.write(file_bytes)

                    job_name = 'pixel-' + ''.join(random.choices(string.ascii_uppercase + string.digits, k=random.choice(range(5, 50))))

                    file_uri = '/'.join(s3_bucket.split('/')[2 if s3_bucket.endswith('/') else 1:]) + f'/sentiment/audio/' + job_name + '.mp3'

                    self.s3.upload_file(
                        f'./src/speech-to-text/{job_name}.mp3',
                        bucket,
                        file_uri
                    )

                    file_uri = f's3://{s3_bucket}/{file_uri}'

                    self.transcribe.start_transcription_job(
                        TranscriptionJobName=job_name,
                        IdentifyLanguage=True,
                        MediaFormat='mp3',
                        Media={'MediaFileUri': file_uri},
                    )

                    max_tries = 60

                    # We are gonna raise an exception here, try/except it so it can exit the while loop appropriately.
                    try:
                        while max_tries > 0:
                            max_tries -= 1
                            job = self.transcribe.get_transcription_job(TranscriptionJobName=job_name)
                            job_status = job['TranscriptionJob']['TranscriptionJobStatus']

                            if job_status == 'FAILED':
                                raise Exception()
                            elif job_status == 'COMPLETED':
                                async with self.bot.session.get(job['TranscriptionJob']['Transcript']['TranscriptFileUri']) as resp:
                                    data = json.loads(await resp.read())

                                raise Exception(data['results']['transcripts'][0]['transcript'])

                            await asyncio.sleep(.3)
                    except Exception as e:
                        if not str(e):
                            continue

                        text = str(e)

                        response = await self.run_sentiment(text)

                        percent = response['attributeScores']['TOXICITY']['summaryScore']['value'] * 100

                        is_negative = percent > 75

                        if is_negative:
                            method = "audio"
                elif attachment.content_type.startswith('image/') and not method and sentiment_config['image']:
                    async with self.bot.session.get(attachment.url) as resp:
                        img_bytes = await resp.read()

                    lines = []

                    response = self.rekognition.detect_document_text(
                        Document={
                            'Bytes': img_bytes,
                        }
                    )

                    ids = []

                    for text in response['TextDetections']:

                        if 'ParentId' in text:
                            if text['ParentId'] in ids:
                                continue
                            else:
                                ids.append(text['ParentId'])
                        else:
                            ids.append(text['Id'])

                        lines.append(text['DetectedText'])

                    text = ' '.join(lines)

                    response = await self.run_sentiment(text)

                    percent = response['attributeScores']['TOXICITY']['summaryScore']['value'] * 100

                    is_negative = percent > 75

                    if is_negative:
                        method = "image"

            if not method:
                await asyncio.sleep(2)
                return

            modlog_channel = msg.guild.get_channel(config['modlog_channel'])

            if not modlog_channel:
                return

            latest_case = await self.bot.pool.fetchrow("SELECT * FROM user_warnings WHERE guild_id = $1 ORDER BY case_id DESC LIMIT 1", msg.guild.id)

            if not latest_case:
                case_id = 1
            else:
                case_id = latest_case['case_id'] + 1

            raw_msg = await self.bot.http.get_message(msg.channel.id, msg.id)

            data_cdn_count = {}
            cdn_urls = []

            for attach in msg.attachments:
                if attach.content_type.startswith('image/'):
                    data_cdn_count['image'] = data_cdn_count.get('image', 0) + 1

                    ext = 'png'
                    name = f'image{data_cdn_count["image"]}'
                    full_name = f'{name}.{ext}'
                elif attach.content_type.startswith('audio/'):
                    data_cdn_count['audio'] = data_cdn_count.get('audio', 0) + 1

                    ext = 'mp3'
                    name = f'audio{data_cdn_count["audio"]}'
                    full_name = f'{name}.{ext}'
                elif attach.content_type.startswith('video/'):
                    data_cdn_count['video'] = data_cdn_count.get('video', 0) + 1

                    ext = 'mp4'
                    name = f'video{data_cdn_count["video"]}'
                    full_name = f'{name}.{ext}'
                else:
                    continue

                async with self.bot.session.get(attach.url) as resp:
                    file_bytes = await resp.read()

                with open(f'./src/warnings/{full_name}', 'wb') as f:
                    f.write(file_bytes)

                file_uri = f'{s3_prefix + ("/" if not s3_prefix.endswith("/") else "")}warnings/{msg.guild.id}/{case_id}/{full_name}'

                self.s3.upload_file(
                    f'./src/warnings/{full_name}',
                    bucket,
                    file_uri
                )

                cdn_urls.append(f'https://{s3_bucket}/{file_uri}')

            file_uri = f'{s3_prefix + ("/" if not s3_prefix.endswith("/") else "")}warnings/{msg.guild.id}/{case_id}/raw.json'

            await msg.channel.send(file_uri)

            with open(f'./src/warnings/raw.json', 'w') as f:
                f.write(json.dumps(raw_msg))

            self.s3.upload_file(
                f'./src/warnings/raw.json',
                bucket,
                file_uri
            )

            raw_url = f'https://{s3_bucket}/{file_uri}'

            warnings = len(
                await self.bot.pool.fetch("""
                    SELECT * FROM user_warnings WHERE user_id = $1 AND guild_id = $2
                """, msg.author.id, msg.guild.id)
            )

            modlog_embed = discord.Embed(color=self.bot.color)

            modlog_embed.set_author(name=f'Case #{case_id}', icon_url=msg.author.display_avatar)

            modlog_embed.add_field(name='User:', value=f'{msg.author.mention} - {msg.author} ({msg.author.id})')

            modlog_embed.add_field(name='Channel:', value=f'{msg.channel.mention} - {msg.channel} ({msg.channel.id})')

            if msg.content:
                modlog_embed.add_field(name='Message Content:', value=msg.content, inline=False)

            if method == 'audio':
                modlog_embed.add_field(name='Reason of case:', value=f'The user was warned for sending an audio that contains toxic content that was triggered by this bot in language `{job["TranscriptionJob"]["LanguageCode"]} - {response["languages"][0].upper()}`.', inline=False)
            elif method == 'image':
                modlog_embed.add_field(name='Reason of case:', value=f'The user was warned for sending an image that contains toxic content that was triggered by this bot in language `{response["languages"][0].upper()}`.', inline=False)
            elif method == 'content':
                modlog_embed.add_field(name='Reason of case:', value=f'The user was warned for sending a message that contains toxic content that was triggered by this bot in language `{response["languages"][0].upper()}`.', inline=False)

            modlog_embed.add_field(name='Assets sent in Message:', value='\n'.join([f'- [{x[len(x)-1].title()}]({x})' for x in cdn_urls]) or 'None.', inline=False)

            modlog_embed.add_field(name='Raw Message:', value=f'[Click here to view the raw message]({raw_url})')

            modlog_embed.add_field(name='Sentiment:', value=f'{response["attributeScores"]["TOXICITY"]["summaryScore"]["value"] * 100:.2f}%')

            modlog_embed.add_field(name='Number of Warnings Total Now:', value=warnings+1)

            modlog_embed.add_field(name='Action Taken:', value='Deleted the message & warned the user.')

            modlog_msg = await modlog_channel.send(embed=modlog_embed)

            user_embed = discord.Embed(color=self.bot.color)

            user_embed.set_author(name=f'Case #{case_id}', icon_url=msg.author.display_avatar)

            user_embed.description = f"You have been warned in **{msg.guild.name}** on channel {msg.channel.mention} for sending a message that contains toxic content that was triggered by this bot in language `{response['languages'][0].upper()}`."
            
            try:
                await msg.author.send(embed=user_embed)
            except:
                await msg.channel.send(content=msg.author.mention, embed=user_embed)

            warning = await self.bot.pool.fetchrow("""
            INSERT INTO user_warnings (case_id, guild_id, channel_id, user_id, case_type, case_time, case_method, case_sources, cdn_urls, modlog_message_url)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            RETURNING *
            """, case_id, msg.guild.id, msg.channel.id, msg.author.id, 'sentiment', datetime.datetime.utcnow(), method, json.dumps(raw_msg), cdn_urls, modlog_msg.jump_url)

            await msg.delete()

def setup(bot):
    if bot.config.get('sentiment').get('enable') is False:
        raise Exception("Sentiment check is disabled.")

    bot.add_cog(Sentiment(bot))