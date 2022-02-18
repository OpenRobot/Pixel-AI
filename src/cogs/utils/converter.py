import re
import discord
from discord.ext import commands
from discord.ext.commands import Converter, Context

class ImageConverter(Converter):
    def __init__(self, **kwargs):
        self.options = kwargs

    async def convert(self, ctx: Context, argument: str):
        if isinstance(argument, str):
            for strip_remove in self.options.get("strip_remove", []):
                argument = argument.replace(strip_remove, "")
                argument = argument.replace(" " + strip_remove, "")
                argument = argument.replace(strip_remove + " ", "")

        if argument:
            x = re.findall(
                r"http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*(),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+",
                argument,
            )
            if x:
                return x[0]
        elif ctx.message.reference:
            x = re.findall(
                r"http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*(),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+",
                ctx.message.reference.resolved.content,
            )
            if x:
                return x[0]

        try:
            return (await commands.MemberConverter().convert(ctx, argument)).avatar.url
        except:
            try:
                return (
                    await commands.UserConverter().convert(ctx, argument)
                ).avatar.url
            except:
                pass

        if ctx.message.attachments:
            return ctx.message.attachments[0].url
        if ctx.message.reference:
            if ctx.message.reference.resolved.attachments:
                return ctx.message.reference.resolved.attachments[0].url

            elif ctx.message.reference.resolved.embeds:
                for embed in ctx.message.reference.resolved.embeds:
                    if embed.image is not discord.Embed.Empty:
                        return embed.image.url
                    elif embed.thumbnail is not discord.Embed.Empty:
                        return embed.thumbnail.url

        if ctx.message.content:
            if emoji := re.findall(
                r"<(?P<animated>a?):(?P<name>[a-zA-Z0-9_]{2,32}):(?P<id>[0-9]{18,22})>",
                ctx.message.content,
            ):
                emoji_id = emoji[0][2]
                return f"https://cdn.discordapp.com/emojis/{emoji_id}.png"
        elif ctx.message.reference:
            if ctx.message.reference.resolved.content:
                if emoji := re.findall(
                    r"<(?P<animated>a?):(?P<name>[a-zA-Z0-9_]{2,32}):(?P<id>[0-9]{18,22})>",
                    ctx.message.reference.resolved.content,
                ):
                    emoji_id = emoji[0][2]
                    return f"https://cdn.discordapp.com/emojis/{emoji_id}.png"

            return ctx.message.reference.resolved.author.avatar.url

        return None
