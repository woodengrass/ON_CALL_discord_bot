from collections.abc import Awaitable, Callable

import discord

BackCallback = Callable[[discord.Interaction], Awaitable[None]]
