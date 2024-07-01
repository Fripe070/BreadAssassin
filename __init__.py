import contextlib
from collections import defaultdict
from datetime import datetime, timedelta
from enum import Enum

import discord
from discord.ext import tasks, commands

import breadcord
from breadcord.module import ModuleCog
from .response_handlers import embed_response_handler, webhook_response_handler, ACCEPTED_WEBHOOK_NAME
from .types import MessageState, ChangeType
from .views import DeleteMessageButton

MessageID = int


class ResponseType(Enum):
    EMBED = "embed"
    WEBHOOK = "webhook"


class BreadAssassin(ModuleCog):
    def __init__(self, module_id: str):
        super().__init__(module_id)
        self.message_cache: defaultdict[MessageID, list[MessageState]] = defaultdict(list)
        self.prune_message_cache.start()

        @self.settings.snipe_response_type.observe  # type: ignore
        def on_snipe_response_type_changed(_, new: str) -> None:
            # noinspection PyProtectedMember
            if new not in ResponseType._value2member_map_:
                raise ValueError(f"Invalid snipe response type: {new}")
        on_snipe_response_type_changed(None, self.settings.snipe_response_type.value)  # type: ignore

    @property
    def snipe_response_type(self) -> ResponseType:
        return ResponseType(self.settings.snipe_response_type.value)

    def is_state_expired(self, state: MessageState, *, lenience: timedelta = timedelta()) -> bool:
        return (
            state.changed_at
            + timedelta(seconds=self.settings.max_age.value)  # type: ignore
            + lenience
        ) < datetime.now()

    @tasks.loop(seconds=3)
    async def prune_message_cache(self):
        for message_id, message_states in self.message_cache.copy().items():
            latest_state = message_states[-1]
            if self.is_state_expired(latest_state):
                self.message_cache.pop(message_id)
                self.logger.debug(f"Message {message_id} removed from cache")

    def get_tracked_states_in_channel(self, channel: discord.abc.Messageable) -> list[list[MessageState]]:
        channel_states = [
            message_states
            for message_states in self.message_cache.values()
            if (latest := message_states[-1]).message.channel == channel and not self.is_state_expired(latest)
        ]
        channel_states.sort(key=lambda message_states: message_states[-1].changed_at)
        return channel_states

    async def can_snipe_message(self, message: discord.Message) -> bool:
        if self.settings.allow_self_snipe.value:
            if message.author == self.bot.user:
                return False
            # Will technically mess up if we snipe a webhook right after the response type is changed... but oh well
            if message.webhook_id is not None and self.snipe_response_type == ResponseType.WEBHOOK:
                webhook = await self.bot.fetch_webhook(message.webhook_id)
                if webhook.name == ACCEPTED_WEBHOOK_NAME:
                    return False
        return True

    @ModuleCog.listener()
    async def on_message_delete(self, message: discord.Message):
        if not self.settings.allow_deletion_sniping.value:
            return
        if not await self.can_snipe_message(message):
            return
        self.message_cache[message.id].append(
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
        if not await self.can_snipe_message(old_message):
            return
        self.message_cache[old_message.id].append(
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
    async def snipe(self, ctx: commands.Context, index: int = 1):
        if not self.settings.allow_edit_sniping.value and not self.settings.allow_deletion_sniping.value:
            await ctx.reply("Sniping is disabled.")
            return
        if not ctx.guild:
            await ctx.reply("Sniping is only available in guilds.")
            return

        message_states = self.get_tracked_states_in_channel(ctx.channel)
        if not message_states:
            await ctx.reply("No messages to snipe.")
            return
        sniped_message_sates = message_states[-max(1, min(index, len(message_states)))]
        delete_button, response = await self.use_handler(ctx, sniped_message_sates)
        with contextlib.suppress(KeyError):  # Race condition if it gets automatically pruned
            self.message_cache.pop(sniped_message_sates[-1].message.id)

        await delete_button.wait()
        if delete_button.should_delete:
            await response.delete()

    async def use_handler(
        self,
        ctx: commands.Context,
        message_states: list[MessageState]
    ) -> tuple[DeleteMessageButton, discord.Message]:
        if self.snipe_response_type == ResponseType.EMBED:
            return await embed_response_handler(ctx, message_states)
        if self.snipe_response_type == ResponseType.WEBHOOK:
            try:
                return await webhook_response_handler(ctx, message_states)
            except Exception as error:
                self.logger.exception(
                    "Failed to use webhook response handler, falling back to embed",
                    exc_info=error,
                )
                return await embed_response_handler(ctx, message_states)
        raise ValueError(f"Invalid snipe response type: {self.snipe_response_type}")


async def setup(bot: breadcord.Bot):
    await bot.add_cog(BreadAssassin("bread_assassin"))
