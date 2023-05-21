"""Slack frontend for Dreambot."""
import asyncio
import traceback

from typing import Any

from slack_bolt.app.async_app import AsyncApp
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_sdk.errors import SlackApiError

from dreambot.shared.worker import DreambotWorkerBase, DreambotWorkerEndType, CallbackSendWorkload


class FrontendSlack(DreambotWorkerBase):
    """Class implementing a Slack client worker."""

    def __init__(
        self,
        options: dict[str, Any],
        callback_send_workload: CallbackSendWorkload,
    ):
        """Initialise the class."""
        super().__init__(
            name="Slack",
            end=DreambotWorkerEndType.FRONTEND,
            options=options,
            callback_send_workload=callback_send_workload,
        )
        self.token = options["slack"]["token"]
        self.socket_mode_token = options["slack"]["socketModeToken"]
        self.slack: AsyncApp
        self.handler: AsyncSocketModeHandler
        self.should_reconnect = True
        self.user_id: str = ""
        self.user_name_cache: dict[str, str] = {}
        self.channel_name_cache: dict[str, str] = {}

    async def boot(self):
        """Boot this instance."""
        while self.should_reconnect:
            self.logger.info("Booting Slack connection... (reconnect: %s)", self.should_reconnect)
            try:
                self.slack = AsyncApp(token=self.token)
                self.handler = AsyncSocketModeHandler(self.slack, self.socket_mode_token)

                @self.slack.event("message")  # type: ignore
                async def on_message(body: dict[str, Any]):  # type: ignore
                    try:
                        await self.on_message(body)
                    except Exception as exc:
                        self.logger.error("Error handling Slack message: %s", exc)

                self.is_booted = True  # FIXME: This really should happen in response to some kind of "connected" event, but Slack doesn't seem to have one
                await self.handler.start_async()
            except SlackApiError as exc:
                self.logger.error("Slack API error: %s", exc)
            except Exception as exc:
                self.logger.error("Slack connection error: %s", exc)
            finally:
                self.logger.debug("Slack connection closed")

                if self.should_reconnect:
                    self.logger.info("Sleeping before reconnecting...")
                    await asyncio.sleep(5)

    async def shutdown(self):
        """Shutdown this instance."""
        self.should_reconnect = False
        await self.handler.close_async()

    async def callback_receive_workload(self, queue_name: str, message: dict[str, Any]) -> bool:
        """Process an incoming workload message.

        Args:
            queue_name (str): The queue name the message was received on.
            message (bytes): The workload message, a JSON string encoded as bytes.

        Returns:
            bool: True if the message should be ack'd in NATS, False otherwise.
        """
        # FIXME: Implement
        return True

    async def on_message(self, msg: dict[str, Any]):
        """Handle an incoming Slack websocket message.

        Args:
            msg (aiohttp.WSMessage): The message.
        """
        self.logger.info("Received Slack message: %s", msg)
        if self.user_id == "":
            self.user_id = (await self.slack.client.auth_test())["user_id"]  # type: ignore
            self.logger.debug("Discovered our user_id: %s", self.user_id)

        if msg["event"]["user"] == self.user_id:
            # Discard messages from self
            self.logger.debug("Ignoring message from self")
            return

        text = msg["event"]["text"]
        for trigger in self.options["triggers"]:
            self.logger.debug("Checking trigger: '%s'", trigger + " ")
            if text.startswith(trigger + " "):
                prompt = text[len(trigger) + 1 :]

                reply = {
                    "to": self.options["triggers"][trigger],
                    "reply-to": self.address,
                    "frontend": "slack",
                    "channel": msg["event"]["channel"],
                    "user": msg["event"]["user"],
                    "trigger": trigger,
                    "prompt": prompt,
                }

                if reply["user"] not in self.user_name_cache:
                    user_info = await self.slack.client.users_info(user=reply["user"])  # type: ignore
                    self.logger.error("********* FOUND USER INFO: %s", user_info)
                    self.user_name_cache[reply["user"]] = user_info["user"]["real_name"]
                reply["user_name"] = self.user_name_cache[reply["user"]]

                # FIXME: Get channel_name and server_name here

                self.logger.info("INPUT: %s %s", self.log_slug(reply), text)

                # Publish the trigger
                try:
                    await self.callback_send_workload(reply)
                    # FIXME: Add thumbs-up reaction
                except Exception:
                    traceback.print_exc()
                    # FIXME: Add thumbs-down reaction

    def log_slug(self, resp: dict[str, str]) -> str:
        """Return a string to identify a message in logs."""
        return f"{resp['server_name']}:#{resp['channel_name']} <{resp['user_name']}>"
