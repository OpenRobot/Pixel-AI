import discord
import datetime
import contextlib
from discord.ext import commands

from cogs.utils import Config

config = Config()

class HelpEmbed(discord.Embed): # Our embed with some preset attributes to avoid setting it multiple times
    def __init__(self, bot, **kwargs):
        super().__init__(**kwargs)
        self.timestamp = discord.utils.utcnow()
        text = "Use help [command] or help [category] for more information | <> is required | [] is optional"
        self.set_footer(text=text)
        self.color = bot.color

class MyHelp(commands.MinimalHelpCommand):
    def __init__(self, *args, **kwargs):
        if "command_attrs" in kwargs and isinstance(kwargs["command_attrs"], dict):
            kwargs["command_attrs"].update({
                "help": "The help command for the bot",
                "aliases": ['commands', '?', 'h']
            })
        else:
            kwargs["command_attrs"] = {
                "help": "The help command for the bot",
                "aliases": ['commands', '?', 'h']
            }

        super().__init__(*args, **kwargs) # create our class with some aliases
    
    async def send(self, **kwargs):
        """a short cut to sending to get_destination"""
        await self.get_destination().send(**kwargs)

    async def send_bot_help(self, mapping):
        """triggers when a `<prefix>help` is called"""
        ctx = self.context
        embed = HelpEmbed(ctx.bot, title=f"{ctx.me.display_name} Help")
        embed.set_thumbnail(url=ctx.me.display_avatar.url)
        usable = 0 

        for cog, commands in mapping.items(): #iterating through our mapping of cog: commands
            if filtered_commands := await self.filter_commands(commands): 
                # if no commands are usable in this category, we don't want to display it
                amount_commands = len(filtered_commands)
                usable += amount_commands
                if cog: # getting attributes dependent on if a cog exists or not
                    name = cog.qualified_name
                    description = cog.description or "No description"
                else:
                    name = "No Category"
                    description = "Commands with no category"

                embed.add_field(name=f"{name} Category [{amount_commands}]", value=description)

        embed.description = f"{len(ctx.bot.commands)} commands | {usable} usable" 

        await self.send(embed=embed)

    async def send_command_help(self, command):
        """triggers when a `<prefix>help <command>` is called"""
        signature = self.get_command_signature(command) # get_command_signature gets the signature of a command in <required> [optional]
        embed = HelpEmbed(self.context.bot, title=signature, description=command.help or "No help found...")

        if cog := command.cog:
            embed.add_field(name="Category", value=cog.qualified_name)

        can_run = "No"
        # command.can_run to test if the cog is usable
        with contextlib.suppress(commands.CommandError):
            if await command.can_run(self.context):
                can_run = "Yes"
            
        embed.add_field(name="Usable", value=can_run)

        if command._buckets and (cooldown := command._buckets._cooldown): # use of internals to get the cooldown of the command
            embed.add_field(
                name="Cooldown",
                value=f"{cooldown.rate} per {cooldown.per:.0f} seconds",
            )

        embed.add_field(name="Aliases", value=", ".join([f'`{x}`' for x in command.aliases]) or "None.")

        if isinstance(command, commands.Group) and command.commands:
            if subcommands := await self.filter_commands(command.commands):
                embed.add_field(name="Subcommands", value="\n".join([f'`{self.get_command_signature(x)}`' for x in subcommands]), inline=False)

        await self.send(embed=embed)

    async def send_help_embed(self, title, description, commands): # a helper function to add commands to an embed
        embed = HelpEmbed(self.context.bot, title=title)

        if description:
            embed.description = description

        if filtered_commands := await self.filter_commands(commands):
            for command in filtered_commands:
                embed.add_field(name=self.get_command_signature(command), value=command.help or "\u200b")
           
        await self.send(embed=embed)

    async def send_group_help(self, group):
        """triggers when a `<prefix>help <group>` is called"""
        #title = self.get_command_signature(group)
        #await self.send_help_embed(title, group.help, group.commands)

        return await self.send_command_help(group)

    async def send_cog_help(self, cog):
        """triggers when a `<prefix>help <cog>` is called"""
        title = cog.qualified_name or "No"
        await self.send_help_embed(f'{title} Category', cog.description, cog.get_commands())

class Help(commands.Cog):
    def __init__(self, bot):
        self._original_help_command = bot.help_command
        bot.help_command = MyHelp()
        bot.help_command.cog = self

    def cog_unload(self):
        self.bot.help_command = self._original_help_command

def setup(bot):
    bot.add_cog(Help(bot))