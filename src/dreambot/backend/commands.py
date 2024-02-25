"""Backend for useful commands."""

import random
import traceback

from typing import Any
from argparse import REMAINDER, ArgumentError

from dreambot.shared.custom_argparse import UsageException, ErrorCatchingArgumentParser
from dreambot.shared.worker import DreambotWorkerBase, DreambotWorkerEndType, CallbackSendWorkload


class DreambotBackendCommands(DreambotWorkerBase):
    """Dreambot backend for various useful commands."""

    def __init__(self, options: dict[str, Any], callback_send_workload: CallbackSendWorkload):
        """Initialise the Commands backend."""
        super().__init__(
            name="commands",
            end=DreambotWorkerEndType.BACKEND,
            options=options,
            callback_send_workload=callback_send_workload,
        )

    async def boot(self):
        """Boot ourselves."""
        self.logger.info("Booted")
        self.is_booted = True

    async def shutdown(self):
        """Shutdown ourselves."""
        return

    async def callback_receive_workload(self, queue_name: str, message: dict[str, Any]) -> bool:
        """Receive work from NATS.

        Args:
            queue_name (str): The name of the queue we received the message from
            message (bytes): The full message we received, as a dictionary

        Returns:
            bool: True if we either processed successfully, or failed to process in a way that suggests the message should be discarded. Otherwise False.
        """
        self.logger.info("callback_receive_workload: %s", message)

        try:
            argparser = self.arg_parser(message["trigger"])
            args = argparser.parse_args(message["prompt"].split(" "))
            args.prompt = " ".join(args.prompt)

            # Determine which command was triggered
            match message["trigger"]:
                case "!chance":
                    response = f"{random.randint(1, 100)}% chance {args.prompt}"
                case _:
                    response = "Unknown command"

            # Fetch the response, prepare it to be sent back to the user and added to their cache
            message["reply-text"] = response
        except UsageException as exc:
            # This isn't strictly an error, but it's the easiest way to reply with our --help text, which is in the UsageException
            message["reply-text"] = str(exc)
        except (ValueError, ArgumentError) as exc:
            message["error"] = f"Something is wrong with your arguments, try {message['trigger']} --help ({exc})"
        except Exception as exc:
            message["error"] = f"Unknown error: {exc}"
            traceback.print_exc()

        await self.send_message(message)
        return True

    def arg_parser(self, trigger: str = "") -> ErrorCatchingArgumentParser:
        """Create an argument parser for the command.

        Args:
            trigger (str, optional): The command that was triggered. Defaults to "".

        Returns:
            ErrorCatchingArgumentParser: An argument parser object that can with parse_args()
        """
        parser = super().arg_parser()

        match trigger:
            case "!chance":
                pass
            case _:
                pass

        parser.add_argument("prompt", nargs=REMAINDER)
        return parser
