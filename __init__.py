import contextlib
from collections import defaultdict
from datetime import datetime, timedelta

import discord
from discord.ext import tasks, commands

import breadcord
from breadcord.module import ModuleCog
from .response_handlers import embed_response_handler, webhook_response_handler
from .types import MessageState, ChangeType


class BreadAssassin(ModuleCog):
    def __init__(self, module_id: str):
        super().__init__(module_id)
        self.message_cache: defaultdict[discord.Object, list[MessageState]] = defaultdict(list)
        self.prune_message_cache.start()

        @self.settings.snipe_response_type.observe
        def on_snipe_response_type_changed(_, new: str) -> None:
            if new not in ("embed", "webhook"):
                raise ValueError(f"Invalid snipe response type: {new}")
        on_snipe_response_type_changed(None, self.settings.snipe_response_type.value)

    def is_state_expired(self, state: MessageState, *, lenience: timedelta = timedelta()) -> bool:
        return state.changed_at + timedelta(seconds=self.settings.max_age.value) + lenience < datetime.now()

    @tasks.loop(seconds=3)
    async def prune_message_cache(self):
        for message_id, message_states in tuple(self.message_cache.items()):
            latest_state = message_states[-1]
            if self.is_state_expired(latest_state):
                self.message_cache.pop(message_id)
                self.logger.debug(f"Message {message_id.id} removed from cache")

    def get_tracked_states_in_channel(self, channel: discord.TextChannel) -> list[list[MessageState]]:
        channel_states = [
            message_states
            for message_states in self.message_cache.values()
            if (latest := message_states[-1]).message.channel == channel and not self.is_state_expired(latest)
        ]
        channel_states.sort(key=lambda message_states: message_states[-1].changed_at)
        return channel_states

    @ModuleCog.listener()
    async def on_message_delete(self, message: discord.Message):
        if not self.settings.allow_deletion_sniping.value:
            return
        if self.settings.allow_self_snipe.value and message.author == self.bot.user:
            return
        self.message_cache[discord.Object(message.id)].append(
            MessageState(
                message=message,
                changed_through=ChangeType.DELETE,
                changed_at=datetime.now()
            )
        )
        self.logger.debug(f"Message {message.id} deleted and tracked")

    @ModuleCog.listener()
    async def on_message_edit(self, old_message: discord.Message, _):
        if not self.settings.allow_edit_sniping.value:
            return
        if self.settings.allow_self_snipe.value and old_message.author == self.bot.user:
            return
        self.message_cache[discord.Object(old_message.id)].append(
            MessageState(
                message=old_message,
                changed_through=ChangeType.EDIT,
                changed_at=datetime.now()
            )
        )
        self.logger.debug(f"Message {old_message.id} edited and tracked")

    @commands.hybrid_command(
        aliases=["s"],
        description='"Snipe" a message that was recently edited or deleted'
    )
    async def snipe(self, ctx: commands.Context):
        if not self.settings.allow_edit_sniping.value and not self.settings.allow_deletion_sniping.value:
            await ctx.reply("Sniping is disabled.")
            return

        message_states = self.get_tracked_states_in_channel(ctx.channel)
        if not message_states:
            await ctx.reply("No messages to snipe.")
            return

        sniped_message_sates = message_states[-1]

        match self.settings.snipe_response_type.value:
            case "embed":
                response_handler = embed_response_handler
            case "webhook":
                response_handler = webhook_response_handler
            case _:
                raise ValueError(f"Invalid snipe response type: {self.settings.snipe_response_type.value}")

        await response_handler(ctx, message_states=sniped_message_sates)
        with contextlib.suppress(KeyError):
            self.message_cache.pop(discord.Object(sniped_message_sates[-1].message.id))


async def setup(bot: breadcord.Bot):
    await bot.add_cog(BreadAssassin("bread_assassin"))
