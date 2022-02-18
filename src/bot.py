import sys
import os
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

import re
import json
import discord
import asyncpg
import asyncio
import jishaku
import aiohttp
from discord.ext import commands

from cogs.utils import Config

#from openrobot.api_wrapper import AsyncClient

# --------------------------------------------------

config = Config()

bot = commands.Bot(
    command_prefix=config.get('bot').get('prefix'), 
    help_command=commands.MinimalHelpCommand(), 
    description=config.get('bot').get('description'), 
    intents=discord.Intents.all(), 
    #slash_commands=True
)

bot.config = config

#bot.openrobot = AsyncClient(config.get('authentication').get('openrobot'))

bot.color = discord.Colour(config.get('bot').get('main-color'))

bot.session = aiohttp.ClientSession(loop=bot.loop)

@bot.event
async def on_ready():
    print('\n'.join([
        "-"*10,
        "Logged in as:",
        f"Bot Username: {bot.user.name}",
        f"Bot ID: {bot.user.id}",
        "-"*10,
    ]))

@bot.event
async def on_message(msg: discord.Message):
    if re.match(rf'^<@!?{bot.user.id}>$', msg.content):
        return await msg.reply(f"Hi! My prefix is `{config.get('bot').get('prefix')}`")

    return await bot.process_commands(msg)

async def start():
    bot.pool = await asyncpg.create_pool(config.get('database').get('psql'))

    async with bot.pool.acquire() as conn:
        await conn.set_type_codec(
            'json',
            encoder=json.dumps,
            decoder=json.loads,
            schema='pg_catalog'
        )

bot.loop.create_task(start())

# --------------------------------------------------

if __name__ == '__main__':
    for ext in config.get('bot').get('extensions'):
        bot.load_extension(ext)

    bot.run(config.get('bot').get('token'))