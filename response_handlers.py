import time
from collections.abc import Coroutine, Callable
from typing import Any

import discord
from discord.ext import commands

from .types import MessageState
from .views import DeleteMessageButton

__all__ = (
    "ResponseHandler",
    "embed_response_handler",
    "webhook_response_handler",
)

ResponseHandler = Callable[[commands.Context, list[MessageState]], Coroutine[Any, Any, None]]


def strip_with_dots(string: str, *, max_length: int) -> str:
    if len(string) <= max_length:
        return string
    return string[:max_length - 3] + "..."


async def embed_response_handler(ctx: commands.Context, message_states: list[MessageState]) -> None:
    # TODO: Allow sniping older versions of a message
    latest_state: MessageState = message_states[-1]

    content = (
        f"Sniped message {latest_state.changed_through.name.lower()} by {latest_state.message.author.mention} "
        f"from <t:{int(time.mktime(latest_state.changed_at.timetuple()))}:R> "
    )

    embeds = [
        discord.Embed(
            title="Message content",
            description=strip_with_dots(latest_state.message.content, max_length=2000),
            colour=latest_state.message.author.colour,
        )
    ]
    if latest_state.message.reference and (reply := latest_state.message.reference.cached_message):
        content += f"in reply to {reply.author.mention} "
        embeds.append(reply_embed(reply))
    for embed in latest_state.message.embeds:
        if "video" in embed.to_dict():
            continue
        embeds.append(embed)

    button = DeleteMessageButton(sniped_user_id=latest_state.message.author.id, sniper_user_id=ctx.author.id)
    response = await ctx.reply(
        content=content,
        embeds=embeds[:10],
        files=[await attachment.to_file() for attachment in latest_state.message.attachments],
        view=button
    )

    await button.wait()
    if button.should_delete:
        await response.delete()


async def webhook_response_handler(ctx: commands.Context, message_states: list[MessageState]) -> None:
    # TODO: Allow sniping older versions of a message
    latest_state: MessageState = message_states[-1]

    accepted_webhook_name = "breadcord_bread_assassin_snipe_hook"  # Legacy support for the old webhook name
    try:
        snipe_webhook: discord.Webhook | None = discord.utils.find(
            lambda webhook: webhook.name == accepted_webhook_name,
            await ctx.channel.webhooks()
        )
        # We seemingly can't get the token after a while, so we just make a new webhook
        if not snipe_webhook or not snipe_webhook.token:
            if snipe_webhook is not None:
                await snipe_webhook.delete(reason="Could not get webhook token")
            snipe_webhook = await ctx.channel.create_webhook(
                name=accepted_webhook_name,
                reason="Webhook needed to spoof message author for sniping."
            )
    except discord.HTTPException as error:  # includes Forbidden
        # Fallback to an embed
        await embed_response_handler(ctx, message_states=message_states)
        raise error

    embeds = []
    for embed in latest_state.message.embeds:
        if "video" in embed.to_dict():
            continue
        embeds.append(embed)
    if latest_state.message.reference and (reply := latest_state.message.reference.cached_message):
        embeds.insert(0, reply_embed(reply))
    files = [await file.to_file() for file in latest_state.message.attachments + latest_state.message.stickers]

    button = DeleteMessageButton(sniped_user_id=latest_state.message.author.id, sniper_user_id=ctx.author.id)
    response = await snipe_webhook.send(
        username=f"{latest_state.message.author.display_name}",
        avatar_url=latest_state.message.author.avatar.url,

        content=latest_state.message.content,
        embeds=embeds[:10],
        files=files[:10],
        allowed_mentions=discord.AllowedMentions.none(),
        view=button,
        wait=True,
    )
    if ctx.interaction:
        await ctx.reply("Sniped message.", ephemeral=True)

    await button.wait()
    if button.should_delete:
        await response.delete()


def reply_embed(message: discord.Message) -> discord.Embed:
    embed = discord.Embed(
        title=f"Replying to message by {message.author.global_name}",
        description=strip_with_dots(message.content, max_length=4096),
        timestamp=message.created_at,
        color=message.author.color,
    )
    embed.set_author(name=message.author.display_name, icon_url=message.author.avatar.url)
    embed.set_footer(text=f"Replied with ping" if message.mentions else "Replied without ping")
    return embed
