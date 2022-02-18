import asyncio

import discord
from discord import ui
from discord.ext import menus

class ViewMenuPages(ui.View, menus.MenuPages):
    def __init__(self, source, *, delete_message_after=False, timeout=60):
        super().__init__(timeout=timeout)
        self._source = source
        self.current_page = 0
        self.ctx = None
        self.message = None
        self.delete_message_after = delete_message_after

        self.update_buttons()

    async def start(self, ctx, *, channel=None, wait=False):
        # We wont be using wait/channel, you can implement them yourself. This is to match the MenuPages signature.
        await self._source._prepare_once()
        self.ctx = ctx
        self.message = await self.send_initial_message(ctx, ctx.channel)

    async def _get_kwargs_from_page(self, page):
        """This method calls ListPageSource.format_page class"""
        value = await super()._get_kwargs_from_page(page)
        if 'view' not in value:
            value.update({'view': self})
        return value

    async def interaction_check(self, item, interaction):
        """Only allow the author that invoke the command to be able to use the interaction"""

        # If the user who sent the interaction is the same as the person who invoked the command OR the interaction user is the bot owner OR the interaction user is a Member and has manage_messages permission.
        if not (interaction.user == self.ctx.author or \
                await self.ctx.bot.is_owner(interaction.user) or \
                    (interaction.user.guild_permissions.manage_messages if isinstance(interaction.user, discord.Member) else True)):
            await interaction.response.send_message("This is not your interaction!", ephemeral=True)

            return False

        return True

    def update_buttons(self):
        """This method is called when the page is updated. This will update the buttons (disabled or not)"""
        
        if self.current_page == 0 and self._source.get_max_pages() != 1:
            self.first_page.disabled = True
            self.before_page.disabled = True
        elif self.current_page + 1 == (self._source.get_max_pages()) and self._source.get_max_pages() != 1:
            self.next_page.disabled = True
            self.last_page.disabled = True
        elif self._source.get_max_pages() == 1:
            self.first_page.disabled = True
            self.before_page.disabled = True
            self.next_page.disabled = True
            self.last_page.disabled = True
        else:
            self.first_page.disabled = False
            self.before_page.disabled = False
            self.next_page.disabled = False
            self.last_page.disabled = False

        self.skip_to_page.label = f'{self.current_page + 1}/{self._source.get_max_pages()}'

    @ui.button(emoji='<:doubleleft:943007192037068811', style=discord.ButtonStyle.blurple)
    async def first_page(self, button, interaction):
        self.update_buttons()

        await self.show_page(0)

    @ui.button(emoji='<:arrowleft:943007180389498891>', style=discord.ButtonStyle.blurple, label='Previous')
    async def before_page(self, button, interaction):
        self.update_buttons()

        await self.show_checked_page(self.current_page - 1)

    @ui.button(label='0/0')
    async def skip_to_page(self, button, interaction):
        self.update_buttons()

        await interaction.response.defer()

        await interaction.followup.send("Enter the page number you want to skip to?", ephemeral=True)

        def check(m):
            return m.author == interaction.user and m.channel == interaction.channel and m.content.isdigit() and 0 < int(m.content) >= self._source.get_max_pages()

        try:
            m = await self.ctx.bot.wait_for('message', check=check, timeout=60)
        except asyncio.TimeoutError:
            return await interaction.followup.send(f"You took too long to respond. Cancelling.", ephemeral=True)

        page_num = int(m.content)

        try:
            await m.delete()
        except:
            pass

        await self.show_page(page_num)

    @ui.button(emoji='<:arrowright:943007165734604820>', style=discord.ButtonStyle.blurple, label='Next')
    async def next_page(self, button, interaction):
        self.update_buttons()

        await self.show_checked_page(self.current_page + 1)

    @ui.button(emoji='<:doubleright:943007149917892658>', style=discord.ButtonStyle.blurple)
    async def last_page(self, button, interaction):
        self.update_buttons()

        await self.show_page(self._source.get_max_pages() - 1)

    @ui.button(emoji='<:StopButton:845592836116054017>', style=discord.ButtonStyle.red, label='Stop')
    async def stop_page(self, button, interaction):
        self.update_buttons()

        self.stop()
        if self.delete_message_after:
            await self.message.delete(delay=0)

MenuPages = ViewMenuPages

class ListPageSource(menus.ListPageSource):
    async def start(self, ctx, *, delete_message_after=False, timeout=60, **kwargs):
        return await ViewMenuPages(self, delete_message_after=delete_message_after, timeout=timeout).start(ctx, **kwargs)

class SentimentWarnings(ListPageSource):
    async def format_page(self, menu, page):
        ctx = menu.ctx
        bot = ctx.bot

        # We only fetch here cause of intents purposes...
        try:
            user = ctx.guild.get_member(page['user_id']) or bot.get_user(page['user_id']) or await bot.fetch_user(page['user_id'])
        except:
            user = None

        try:
            channel = ctx.guild.get_channel(page['channel_id']) or bot.get_channel(page['channel_id'])
        except:
            channel = None

        embed = discord.Embed(title=f"Case ID: #{page['case_id']}", color=bot.color)

        if user:
            embed.set_author(name=f'Sentiment warnings for {user}:', icon_url=user.display_avatar.url)
        else:
            embed.set_author(name=f'Sentiment warnings for user ID {page["user_id"]}:')

        embed.description = f"""
**Channel:** {channel.mention if channel else 'Unknown'}
**Time of warning:** {discord.utils.format_dt(page['case_time'], style='F')}
**Cause of warning:** Sending a toxic {'message' if page['case_method'] == 'content' else page['case_method']}
**Case Message URL:** {page['modlog_message_url']}
        """

        embed.set_footer(text=f"Total warns: {len(self.entries)}")

        return embed

class SentimentMostWarnings(ListPageSource):
    def __init__(self, entries, *, per_page):
        super().__init__(entries, per_page=per_page)

    async def format_page(self, menu, page):
        ctx = menu.ctx
        bot = ctx.bot

        embed = discord.Embed(color=bot.color, title="Most Sentiment warnings in this server:")
        embed.description = "" # So we can do `embed.description += "..."` without erroring/needing to check if it is an EmptyEmbed.

        indexes = self.calculate_index(page)

        try:
            user = ctx.guild.get_member(entry['user_id']) or bot.get_user(entry['user_id']) or await bot.fetch_user(entry['user_id'])
        except:
            user = None

        for entry_index in range(len(page)):
            entry = page[entry_index]
            index = indexes[entry_index]

            if not user:
                indexes = indexes.insert(entry_index + 1, index) # To make sure the index does not skip count.
                continue

            embed.description += f"**{index})** {user.mention if user else 'Unknown'} ({entry['warns']} warnings)\n"

        return embed

    def calculate_index(self, page) -> list[int]:
        first_entry_index = self.entries.index(page[0])

        indexes = []

        for i in range(len(page)):
            indexes.append(i + first_entry_index + 1)

        return indexes

class NSFWCheckWarnings(ListPageSource):
    async def format_page(self, menu, page):
        ctx = menu.ctx
        bot = ctx.bot

        # We only fetch here cause of intents purposes...
        try:
            user = ctx.guild.get_member(page['user_id']) or bot.get_user(page['user_id']) or await bot.fetch_user(page['user_id'])
        except:
            user = None

        try:
            channel = ctx.guild.get_channel(page['channel_id']) or bot.get_channel(page['channel_id'])
        except:
            channel = None

        embed = discord.Embed(title=f"Case ID: #{page['case_id']}", color=bot.color)

        if user:
            embed.set_author(name=f'NSFW Check warnings for {user}:', icon_url=user.display_avatar.url)
        else:
            embed.set_author(name=f'NSFW Check warnings for user ID {page["user_id"]}:')

        embed.description = f"""
**Channel:** {channel.mention if channel else 'Unknown'}
**Time of warning:** {discord.utils.format_dt(page['case_time'], style='F')}
**Cause of warning:** Sending a {page['case_method']} that contains NSFW.
**Case Message URL:** {page['modlog_message_url']}
        """

        embed.set_footer(text=f"Total warns: {len(self.entries)}")

        return embed

class NSFWCheckMostWarnings(ListPageSource):
    def __init__(self, entries, *, per_page):
        super().__init__(entries, per_page=per_page)

    async def format_page(self, menu, page):
        ctx = menu.ctx
        bot = ctx.bot

        embed = discord.Embed(color=bot.color, title="Most NSFW Check warnings in this server:")
        embed.description = "" # So we can do `embed.description += "..."` without erroring/needing to check if it is an EmptyEmbed.

        indexes = self.calculate_index(page)

        try:
            user = ctx.guild.get_member(entry['user_id']) or bot.get_user(entry['user_id']) or await bot.fetch_user(entry['user_id'])
        except:
            user = None

        for entry_index in range(len(page)):
            entry = page[entry_index]
            index = indexes[entry_index]

            if not user:
                indexes = indexes.insert(entry_index + 1, index) # To make sure the index does not skip count.
                continue

            embed.description += f"**{index})** {user.mention if user else 'Unknown'} ({entry['warns']} warnings)\n"

        return embed

    def calculate_index(self, page) -> list[int]:
        first_entry_index = self.entries.index(page[0])

        indexes = []

        for i in range(len(page)):
            indexes.append(i + first_entry_index + 1)

        return indexes