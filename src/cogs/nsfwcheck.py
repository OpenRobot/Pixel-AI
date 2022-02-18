from io import BytesIO
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

from jishaku.functools import executor_function

from collections import Counter

from cogs.utils import is_guild_owner, ImageConverter, NSFWCheckMostWarnings, NSFWCheckWarnings

def nsfw_warnings_check():
    def predicate(ctx):
        if ctx.command == ctx.bot.get_command('nsfw-check warnings') and (member := ctx.kwargs.get('member')):
            if member.id == ctx.author.id:
                return True
            else:
                return False

    return commands.check(predicate)

nsfw_check = commands.check_any(commands.is_owner(), commands.has_permissions(manage_guild=True, manage_messages=True), commands.guild_only(), is_guild_owner(), nsfw_warnings_check())

class NSFWCheck(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

        if self.bot.config.get('authentication').get('aws') and all(bool(x) for x in self.bot.config.get('authentication').get('aws').values()):
            self.rekognition = boto3.client(
                "rekognition",
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
            self.s3 = boto3.client("s3")

        self.s3_config = self.bot.config.get('authentication').get('aws').get('s3')

        self.lock = asyncio.Lock()

    @executor_function
    def _run_nsfwcheck(self, img: BytesIO | bytes) -> dict:
        filename = f"{''.join(random.choices(string.ascii_letters + string.digits, k=10))}.jpg"
        with open(f'./src/nsfw-check/{filename}.jpg', 'wb') as f:
            f.write(getattr(img, 'read', lambda: img)())

        self.s3.upload_file(f'./src/nsfw-check/{filename}.jpg', self.s3_config.get('bucket'), self.s3_config.get('prefix')+filename)

        if isinstance(img, BytesIO):
            response = self.rekognition.detect_moderation_labels(Image={'S3Object': {'Bucket': self.s3_config.get('bucket'), 'Name': self.s3_config.get('prefix')+filename}})
        else:
            response = self.rekognition.detect_moderation_labels(Image={'S3Object': {'Bucket': self.s3_config.get('bucket'), 'Name': self.s3_config.get('prefix')+filename}})

        return response

    async def run_nsfwcheck(self, img: BytesIO | bytes | str) -> dict:
        if isinstance(img, str):
            async with self.bot.session.get(img) as resp:
                img = BytesIO(await resp.read())

        x = await self._run_nsfwcheck(img)

        return x

    @commands.group('nsfw-check', aliases=['nsfw_check', 'nsfwcheck', 'nsfw', 'nc'], invoke_without_command=True)
    async def nsfw(self, ctx: commands.Context, image = commands.Option(None, description='The message ID/URL or a specific text.')):
        """
        Configures the NSFW check of messages
        """

        if ctx.invoked_subcommand is None:
            img = await ImageConverter().convert(ctx, image)

            if not img:
                return await ctx.send_help(ctx.command)

            try:
                response = await self.run_nsfwcheck(image)
            except:
                return await ctx.send('Something wen\'t wrong. Please try again later.')

            if not response:
                return await ctx.send("Could not find any NSFW check data")

            is_nsfw = bool(response['ModerationLabels'])

            newline = '\n'

            if is_nsfw:
                embed = discord.Embed(title="NSFW Check:", description=f"That image is NSFW with the following labels: \n- {f'{newline}- '.join([f'`{x}`' for x in response['ModerationLabels']])}", color=discord.Colour.red())
            else:
                embed = discord.Embed(title="NSFW Check:", description=f"That message is not NSFW.", color=discord.Colour.green())

            await ctx.reply(embed=embed)

    @nsfw.command('setup')
    @nsfw_check
    async def nsfw_setup(self, ctx: commands.Context):
        """
        Setup the NSFW check
        """

        config = await self.bot.pool.fetchrow("SELECT * FROM nsfwcheck WHERE guild_id = $1", ctx.guild.id)

        if config:
            await ctx.reply("You have already configured the NSFW check! Do you want to reconfigure? Say `yes` or `no`.")

            choice = await self.bot.wait_for('message', check=lambda m: m.author == ctx.author and m.channel == ctx.channel and m.content.lower() in ['y', 'yes', 'ye', 'no', 'n'])

            if choice.content.lower() in ['y', 'yes', 'ye']:
                await ctx.reply("Reconfiguring...")
            else:
                return await ctx.reply("Cancelled.")

        await ctx.reply("Welcome to the setup for the NSFW check!\n\nWe will ask you a question.\n\nTo abort the setup, say `cancel` or `stop`.")

        check = lambda m: m.author == ctx.author and m.channel == ctx.channel

        await ctx.reply("Please mention the channel you want to log NSFW checks.")

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
            await self.bot.pool.execute("DELETE FROM nsfwcheck WHERE guild_id = $1", ctx.guild.id)

        await self.bot.pool.execute("INSERT INTO nsfwcheck (guild_id, modlog_channel) VALUES ($1, $2)", ctx.guild.id, modlog.id)

        await ctx.send("Setup complete!")

    @nsfw.command('warnings', aliases=['warns'])
    @nsfw_check
    async def nsfw_warnings(self, ctx: commands.Context, *, member: discord.Member = commands.Option(None, description='The member to check warnings for.')):
        """
        Shows the NSFW Check warnings leaderboard or a specific member's NSFW Check warnings.
        """
        
        # if member is None, do leaderboard for most NSFW Check warnings.
        # If not, show the warnings for that member.

        if member:
            warnings = await self.bot.pool.fetch("""
            SELECT * FROM user_warnings WHERE guild_id = $1 AND case_type = 'nsfwcheck' AND user_id = $2
            """, ctx.guild.id, member.id)

            if not warnings:
                return await ctx.send(f"No warnings found for {'you' if member == ctx.author else member.mention}!", allowed_mentions=discord.AllowedMentions.none())

            return await NSFWCheckWarnings(warnings, per_page=1).start(ctx, timeout=180)
        else:
            warnings = await self.bot.pool.fetch("""
            SELECT * FROM user_warnings WHERE guild_id = $1 AND case_type = 'nsfwcheck'
            """, ctx.guild.id)

            if not warnings:
                return await ctx.send("No NSFW Check warnings found in this server!")

            # Doing the count logic:
            c = Counter([x['user_id'] for x in warnings]) # Have a Counter to generate the amount of the user's nsfw check warnings

            data_count = dict(c) # Convert the Counter to a dict with a format of {user_id: amount}

            l = [{'user_id': k, 'warns': v} for k, v in data_count.items()] # Make the dict into a list of dicts with the user_id and amount of warnings

            l.sort(key=lambda x: x['warns'], reverse=True) # Sort the amount of warnings

            # Starting the pagination
            await NSFWCheckMostWarnings(l, per_page=10).start(ctx, timeout=180)

    @nsfw.command('my-warnings', aliases=['mywarnings', 'my_warnings', 'my-warning', 'mywarning', 'my_warning', 'my-warns', 'mywarns', 'my_warns'])
    async def my_warnings(self, ctx: commands.Context):
        """
        Shows your NSFW Check warnings in this server.
        """
        
        return await self.nsfw_warnings(ctx, member=ctx.author)

    @nsfw.group('settings', invoke_without_command=True, aliases=['config', 'configs', 'setting', 'configure'])
    @nsfw_check
    async def nsfw_settings(self, ctx: commands.Context):
        """
        Configure the NSFW check
        """

        if ctx.invoked_subcommand is None:
            return await self.nsfw_settings_show(ctx)
    
    @nsfw_settings.command('show', aliases=['list'])
    @nsfw_check
    async def nsfw_settings_show(self, ctx: commands.Context):
        """
        Shows the NSFW check settings set.
        """

        config = await self.bot.pool.fetchrow("SELECT * FROM nsfwcheck WHERE guild_id = $1", ctx.guild.id)

        if not config:
            return await ctx.send(f"You have not setup nsfwcheck check for this server yet. Invoke the `{ctx.prefix}nsfw-check setup` command to do so.")

        embed = discord.Embed(color=self.bot.color, title="NSFW Check Settings:")

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

    @nsfw_settings.command('enable', slash_command=False)
    @nsfw_check
    async def nsfw_settings_enable(self, ctx: commands.Context):
        """
        Enables NSFW check in this server
        """

        config = await self.bot.pool.fetchrow("SELECT * FROM nsfwcheck WHERE guild_id = $1", ctx.guild.id)

        if not config:
            return await ctx.send(f"You have not setup NSFW check for this server yet. Invoke the `{ctx.prefix}nsfw-check setup` command to do so.")

        if config['is_enabled'] is True:
            return await ctx.send("NSFW check is already enabled in this server.")

        await self.bot.pool.execute("""
        UPDATE nsfwcheck SET is_enabled = 't' WHERE guild_id = $1
        """, ctx.guild.id)

        await ctx.send("NSFW check has been enabled.")

    @nsfw_settings.command('disable', slash_command=False)
    @nsfw_check
    async def nsfw_settings_disable(self, ctx: commands.Context):
        """
        Enables NSFW check in this server
        """

        config = await self.bot.pool.fetchrow("SELECT * FROM nsfwcheck WHERE guild_id = $1", ctx.guild.id)

        if not config:
            return await ctx.send(f"You have not setup NSFW check for this server yet. Invoke the `{ctx.prefix}nsfw-check setup` command to do so.")

        if config['is_enabled'] is False:
            return await ctx.send("NSFW Check is already disabled in this server.")

        await self.bot.pool.execute("""
        UPDATE nsfwcheck SET is_enabled = 'f' WHERE guild_id = $1
        """, ctx.guild.id)

        await ctx.send("NSFW Check has been disabled.")

    @nsfw_settings.command('modlog', aliases=['modlogchannel', 'modlog-channel', 'modlog_channel'])
    @nsfw_check
    async def update_modlog_channel(self, ctx: commands.Context, channel: discord.TextChannel):
        """
        Updates the modlog channel for this server.
        """

        config = await self.bot.pool.fetchrow("SELECT * FROM nsfwcheck WHERE guild_id = $1", ctx.guild.id)

        if not config:
            return await ctx.send(f"You have not setup NSFW check check for this server yet. Invoke the `{ctx.prefix}nsfw-check setup` command to do so.")

        await self.bot.pool.execute("""
        UPDATE nsfwcheck SET modlog_channel = $1 WHERE guild_id = $2
        """, channel.id, ctx.guild.id)

        await ctx.send(f"Modlog channel has been updated to {channel.mention}.")

    @nsfw_settings.group('user', slash_command=False, invoke_without_command=True)
    @nsfw_check
    async def nsfw_settings_user(self, ctx: commands.Context):
        """
        Configures User Ignore for NSFW check.
        """

        if ctx.invoked_subcommand is None:
            return await ctx.send_help(ctx.command)

    @nsfw_settings_user.command('add', aliases=['+'], slash_command=False)
    @nsfw_check
    async def nsfw_settings_user_add(self, ctx: commands.Context, users: commands.Greedy[discord.Member]):
        """
        Adds a user to the user ignore list.
        """

        config = await self.bot.pool.fetchrow("SELECT * FROM nsfwcheck WHERE guild_id = $1", ctx.guild.id)

        if not config:
            return await ctx.send(f"You have not setup NSFW check for this server yet. Invoke the `{ctx.prefix}nsfw-check setup` command to do so.")

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

        await self.bot.pool.execute("UPDATE nsfwcheck SET users_ignored = $2 WHERE guild_id = $1", ctx.guild.id, l)

        await ctx.send(f"Added {len(l)} user{'s' if len(l) > 1 else ''} to the user ignore list.")

    @nsfw_settings_user.command('remove', aliases=['-', 'rm'], slash_command=False)
    @nsfw_check
    async def nsfw_settings_user_remove(self, ctx: commands.Context, users: commands.Greedy[discord.Member]):
        """
        Removes a user to the user ignore list.
        """

        config = await self.bot.pool.fetchrow("SELECT * FROM nsfwcheck WHERE guild_id = $1", ctx.guild.id)

        if not config:
            return await ctx.send(f"You have not setup NSFW check for this server yet. Invoke the `{ctx.prefix}nsfw-check setup` command to do so.")

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

        await self.bot.pool.execute("UPDATE nsfwcheck SET users_ignored = $2 WHERE guild_id = $1", ctx.guild.id, config['users_ignored'])

        await ctx.send(f"Removed {len(l)} user{'s' if len(l) > 1 else ''} to the user ignore list.")

    @nsfw_settings.group('role', slash_command=False, invoke_without_command=True)
    @nsfw_check
    async def nsfw_settings_role(self, ctx: commands.Context):
        """
        Configures Role Ignore for NSFW check.
        """

        if ctx.invoked_subcommand is None:
            return await ctx.send_help(ctx.command)

    @nsfw_settings_role.command('add', aliases=['+'], slash_command=False)
    @nsfw_check
    async def nsfw_settings_role_add(self, ctx: commands.Context, roles: commands.Greedy[discord.Role]):
        """
        Adds a role to the role ignore list.
        """

        config = await self.bot.pool.fetchrow("SELECT * FROM nsfwcheck WHERE guild_id = $1", ctx.guild.id)

        if not config:
            return await ctx.send(f"You have not setup NSFW check for this server yet. Invoke the `{ctx.prefix}nsfw-check setup` command to do so.")

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

        await self.bot.pool.execute("UPDATE nsfwcheck SET roles_ignored = $2 WHERE guild_id = $1", ctx.guild.id, l)

        await ctx.send(f"Added {len(l)} role{'s' if len(l) > 1 else ''} to the role ignore list.")

    @nsfw_settings_role.command('remove', aliases=['-', 'rm'], slash_command=False)
    @nsfw_check
    async def nsfw_settings_role_remove(self, ctx: commands.Context, roles: commands.Greedy[discord.Role]):
        """
        Removes a role to the role ignore list.
        """

        config = await self.bot.pool.fetchrow("SELECT * FROM nsfwcheck WHERE guild_id = $1", ctx.guild.id)

        if not config:
            return await ctx.send(f"You have not setup NSFW check for this server yet. Invoke the `{ctx.prefix}nsfw-check setup` command to do so.")

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

        await self.bot.pool.execute("UPDATE nsfwcheck SET roles_ignored = $2 WHERE guild_id = $1", ctx.guild.id, config['roles_ignored'])

        await ctx.send(f"Removed {len(l)} role{'s' if len(l) > 1 else ''} to the role ignore list.")

    @nsfw_settings.group('channel', slash_command=False, invoke_without_command=True)
    @nsfw_check
    async def nsfw_settings_channel(self, ctx: commands.Context):
        """
        Configures Channel Ignore for NSFW check.
        """

        if ctx.invoked_subcommand is None:
            return await ctx.send_help(ctx.command)

    @nsfw_settings_channel.command('add', aliases=['+'], slash_command=False)
    @nsfw_check
    async def nsfw_settings_channel_add(self, ctx: commands.Context, channels: commands.Greedy[discord.TextChannel]):
        """
        Adds a channel to the channel ignore list.
        """

        config = await self.bot.pool.fetchrow("SELECT * FROM nsfwcheck WHERE guild_id = $1", ctx.guild.id)

        if not config:
            return await ctx.send(f"You have not setup NSFW check for this server yet. Invoke the `{ctx.prefix}nsfw-check setup` command to do so.")

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

        await self.bot.pool.execute("UPDATE nsfwcheck SET channels_ignored = $2 WHERE guild_id = $1", ctx.guild.id, l)

        await ctx.send(f"Added {len(l)} channel{'s' if len(l) > 1 else ''} to the channel ignore list.")

    @nsfw_settings_channel.command('remove', aliases=['-', 'rm'], slash_command=False)
    @nsfw_check
    async def nsfw_settings_channel_remove(self, ctx: commands.Context, channels: commands.Greedy[discord.TextChannel]):
        """
        Removes a channel to the channel ignore list.
        """

        config = await self.bot.pool.fetchrow("SELECT * FROM nsfwcheck WHERE guild_id = $1", ctx.guild.id)

        if not config:
            return await ctx.send(f"You have not setup NSFW check for this server yet. Invoke the `{ctx.prefix}nsfw-check setup` command to do so.")

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

        await self.bot.pool.execute("UPDATE nsfwcheck SET channels_ignored = $2 WHERE guild_id = $1", ctx.guild.id, config['channels_ignored'])

        await ctx.send(f"Removed {len(l)} channel{'s' if len(l) > 1 else ''} to the channel ignore list.")

    #@sentiment_settings.command('user-add', message_command=False, hidden=True)
    @nsfw_check
    async def sentiment_settings_user_add_slash(self, ctx: commands.Context, user: discord.Member = commands.Option(description='The user to add to the user ignore list.')):
        """
        Adds a user to the sentiment ignore list.
        """

        return await self.sentiment_settings_user_add(ctx, [user,])

    #@sentiment_settings.command('user-remove', message_command=False, hidden=True)
    @nsfw_check
    async def sentiment_settings_user_remove_slash(self, ctx: commands.Context, user: discord.Member = commands.Option(description='The user to remove from the user ignore list.')):
        """
        Removes a user to the sentiment ignore list.
        """

        return await self.sentiment_settings_user_remove(ctx, [user,])

    #@sentiment_settings.command('role-add', message_command=False, hidden=True)
    @nsfw_check
    async def sentiment_settings_role_add_slash(self, ctx: commands.Context, role: discord.Role = commands.Option(description='The role to add to the role ignore list.')):
        """
        Adds a role to the sentiment ignore list.
        """

        return await self.sentiment_settings_role_add(ctx, [role,])

    #@sentiment_settings.command('role-remove', message_command=False, hidden=True)
    @nsfw_check
    async def sentiment_settings_role_remove_slash(self, ctx: commands.Context, role: discord.Role = commands.Option(description='The role to remove from the role ignore list.')):
        """
        Removes a role to the sentiment ignore list.
        """

        return await self.sentiment_settings_role_remove(ctx, [role,])

    #@sentiment_settings.command('channel-add', message_command=False, hidden=True)
    @nsfw_check
    async def sentiment_settings_channel_add_slash(self, ctx: commands.Context, channel: discord.TextChannel = commands.Option(description='The channel to add to the channel ignore list.')):
        """
        Adds a channel to the sentiment ignore list.
        """

        return await self.sentiment_settings_channel_add(ctx, [channel,])

    #@sentiment_settings.command('channel-remove', message_command=False, hidden=True)
    @nsfw_check
    async def sentiment_settings_channel_remove_slash(self, ctx: commands.Context, channel: discord.TextChannel = commands.Option(description='The channel to remove from the channel ignore list.')):
        """
        Removes a channel to the sentiment ignore list.
        """

        return await self.sentiment_settings_channel_remove(ctx, [channel,])

    @commands.Cog.listener()
    async def on_message(self, msg: discord.Message):
        # Checks stuff:
        nsfw_config = self.bot.config.get('nsfw-check')

        s3 = self.bot.config.get('authentication').get('aws').get('s3')
        s3_bucket = s3.get('bucket')
        s3_prefix = s3.get('prefix')

        bucket = s3_bucket.split('/')[0]

        if nsfw_config['ignore-bots']:
            if msg.author.bot:
                return

        if nsfw_config['ignore-nsfw-channels']:
            if msg.channel.is_nsfw():
                return

        if not nsfw_config['video'] and not nsfw_config['image']:
            return

        config = await self.bot.pool.fetchrow("SELECT * FROM nsfwcheck WHERE guild_id = $1", msg.guild.id)

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
            
            for attachment in msg.attachments:
                if attachment.content_type.startswith('video/') and not method and nsfw_check['video']:
                    # Upload to S3:
                    async with self.bot.session.get(attachment.url) as resp:
                        file_bytes = await resp.read()

                    with open(f'./src/nsfw-check/{job_name}.mp3', 'wb') as f:
                        f.write(file_bytes)

                    job_name = 'pixel-' + ''.join(random.choices(string.ascii_uppercase + string.digits, k=random.choice(range(5, 50))))

                    file_uri = f'{s3_prefix + ("/" if not s3_prefix.endswith("/") else "")}nsfw-check/audio/{job_name}.mp3'

                    self.s3.upload_file(
                        f'./src/nsfw-check/{job_name}.mp3',
                        bucket,
                        file_uri
                    )

                    response = self.rekognition.start_content_moderation(
                        Video={
                            'S3Object': {
                                'Bucket': bucket,
                                'Name': file_uri,
                            }
                        }
                    )

                    job_id = response['JobId']

                    content_moderation = self.rekognition.get_content_moderation(
                        JobId=job_id, SortBy='TIMESTAMP'
                    )

                    while content_moderation['JobStatus'] == 'IN_PROGRESS':
                        await asyncio.sleep(1)
                        content_moderation = self.rekognition.get_content_moderation(
                            JobId=job_id, SortBy='TIMESTAMP'
                        )

                    is_nsfw = bool(content_moderation['ModerationLabels'])

                    if is_nsfw:
                        method = "video"
                elif attachment.content_type.startswith('image/') and not method and nsfw_config['image']:
                    response = await self.run_nsfwcheck(attachment.url)

                    is_nsfw = bool(response['ModerationLabels'])

                    if is_nsfw:
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

            modlog_embed.add_field(name='Reason of case:', value=f'The user was warned for sending a {method} that contains NSFW content that was triggered by this bot.', inline=False)

            modlog_embed.add_field(name='Assets sent in Message:', value='\n'.join([f'- [{x[len(x)-1].title()}]({x})' for x in cdn_urls]) or 'None.', inline=False)

            modlog_embed.add_field(name='Raw Message:', value=f'[Click here to view the raw message]({raw_url})')

            if method == 'video':
                modlog_embed.add_field(name='NSFW Check Labels:', value='- ' + '\n- '.join(['`' + x['ModerationLabel']['Name'] + ((' - ' + x['ModerationLabel']['ParentName']) if x['ModerationLabel']['ParentName'] else '') + '`' for x in response['ModerationLabels']]), inline=False)
            else:
                modlog_embed.add_field(name='NSFW Check Labels:', value='- ' + '\n- '.join(['`' + x['Name'] + ((' - ' + x['ParentName']) if x['ParentName'] else '') + '`' for x in response['ModerationLabels']]), inline=False)

            modlog_embed.add_field(name='Number of Warnings Total Now:', value=warnings+1)

            modlog_embed.add_field(name='Action Taken:', value='Deleted the message & warned the user.')

            modlog_msg = await modlog_channel.send(embed=modlog_embed)

            user_embed = discord.Embed(color=self.bot.color)

            user_embed.set_author(name=f'Case #{case_id}', icon_url=msg.author.display_avatar)

            user_embed.description = f"You have been warned in **{msg.guild.name}** on channel {msg.channel.mention} for sending a {method} that contains NSFW content that was triggered by this bot."
            
            try:
                await msg.author.send(embed=user_embed)
            except:
                await msg.channel.send(content=msg.author.mention, embed=user_embed)

            warning = await self.bot.pool.fetchrow("""
            INSERT INTO user_warnings (case_id, guild_id, channel_id, user_id, case_type, case_time, case_method, case_sources, cdn_urls, modlog_message_url)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            RETURNING *
            """, case_id, msg.guild.id, msg.channel.id, msg.author.id, 'nsfwcheck', datetime.datetime.utcnow(), method, json.dumps(raw_msg), cdn_urls, modlog_msg.jump_url)

            await msg.delete()

def setup(bot):
    if bot.config.get('nsfw-check').get('enable') is False:
        raise Exception("NSFW check is disabled.")

    bot.add_cog(NSFWCheck(bot))
