"""OpenAI GPT backend for Dreambot."""
import traceback

from typing import Any
from argparse import REMAINDER, ArgumentError

import openai
from openai.error import (
    APIError,
    Timeout,
    ServiceUnavailableError,
    RateLimitError,
    AuthenticationError,
    InvalidRequestError,
)
from dreambot.shared.custom_argparse import UsageException, ErrorCatchingArgumentParser
from dreambot.shared.worker import DreambotWorkerBase, DreambotWorkerEndType, CallbackSendWorkload


class DreambotBackendGPT(DreambotWorkerBase):
    """OpenAI GPT backend for Dreambot."""

    def __init__(self, options: dict[str, Any], callback_send_workload: CallbackSendWorkload):
        """Initialise the class."""
        super().__init__(
            name="gpt",
            end=DreambotWorkerEndType.BACKEND,
            options=options,
            callback_send_workload=callback_send_workload,
        )
        self.api_key = options["gpt"]["api_key"]
        self.organization = options["gpt"]["organization"]
        self.model = options["gpt"]["model"]
        self.conversation_cache: dict[str, Any] = {}

    async def boot(self):
        """Boot the backend."""
        openai.api_key = self.api_key
        openai.organization = self.organization
        self.is_booted = True

    async def shutdown(self):
        """Shutdown the backend."""
        return

    async def callback_receive_workload(self, queue_name: str, message: dict[str, Any]) -> bool:
        """Process a workload message.

        Args:
            queue_name (str): The name of the queue the message was received on.
            message (bytes): The message that was received, as a dictionary.

        Returns:
            bool: _description_
        """
        self.logger.info("callback_receive_workload: %s", message)

        try:
            argparser = self.arg_parser()
            args = argparser.parse_args(message["prompt"].split(" "))
            args.prompt = " ".join(args.prompt)

            # Ensure we have a valid conversation cache for this user
            cache_key = self.ensure_cache_for_user(message)

            # Determine if we're adding to the cache or starting a new conversation
            if not args.followup:
                self.reset_cache(cache_key)

            if args.list_models:
                # We have to hard code this because the OpenAI API endpoint lists dozens of models that can't be used for Chat Completions
                # see https://platform.openai.com/docs/models/model-endpoint-compatibility

                models = ["gpt-4", "gpt-3.5-turbo", "gpt-3.5-turbo-0301"]
                message["reply-text"] = ", ".join(models)  # type: ignore
            else:
                # Now that our cache is in the right state, add this new prompt to it
                self.conversation_cache[cache_key].append({"role": "user", "content": args.prompt})

                # Now we can ask OpenAI for a response to the contents of our message cache
                self.logger.debug("Sending request to OpenAI...")
                response = openai.ChatCompletion.create(model=args.model, messages=self.conversation_cache[cache_key], temperature=args.temperature)  # type: ignore

                # Fetch the response, prepare it to be sent back to the user and added to their cache
                message["reply-text"] = response.choices[0].message.content  # type: ignore

            # Add the response to the user's cache
            self.conversation_cache[cache_key].append({"role": "assistant", "content": message["reply-text"]})
        except UsageException as exc:
            # This isn't strictly an error, but it's the easiest way to reply with our --help text, which is in the UsageException
            message["reply-text"] = str(exc)
        except (APIError, Timeout, ServiceUnavailableError) as exc:
            message["error"] = f"GPT service unavailable, try again: {exc}"
        except (RateLimitError, AuthenticationError) as exc:
            message["error"] = f"GPT service query error: {exc}"
        except InvalidRequestError as exc:
            message["error"] = f"GPT request error: {exc}"
        except (ValueError, ArgumentError) as exc:
            message["error"] = f"Something is wrong with your arguments, try {message['trigger']} --help ({exc})"
        except Exception as exc:
            message["error"] = f"Unknown error: {exc}"
            traceback.print_exc()

        await self.send_message(message)
        return True

    def ensure_cache_for_user(self, data: dict[str, Any]) -> str:
        """Ensure we have a cache entry for this user.

        Args:
            data (dict[str, Any]): A dictionary containing a NATS message.

        Returns:
            str: The cache key for this user.
        """
        cache_key = self.cache_name_for_prompt(data)
        if cache_key not in self.conversation_cache:
            self.logger.debug("Creating new cache entry for %s", cache_key)
            self.reset_cache(cache_key)
        return cache_key

    def cache_name_for_prompt(self, data: dict[str, Any]) -> str:
        """Determine the cache key for a given NATS message.

        Args:
            data (dict[str, Any]): A dictionary containing a NATS message.

        Returns:
            str: The cache key for this user.
        """
        return f"{data['reply-to']}_{data['channel']}_{data['user']}"

    def reset_cache(self, key: str):
        """Reset the cache for a given user.

        This is where our initial 'system' prompt is set, which guides GPT to behave the way we want.
        """
        self.conversation_cache[key] = [
            {
                "role": "system",
                "content": "You are a helpful assistant. Make your answers as brief as possible.",
            }
        ]

    def arg_parser(self) -> ErrorCatchingArgumentParser:
        """Parse arguments that may be contained in a workload message.

        Returns:
            ErrorCatchingArgumentParser: An argparse parser that can be used to parse arguments.
        """
        parser = super().arg_parser()
        parser.add_argument("-m", "--model", help="GPT model to use", default=self.model)
        parser.add_argument("-l", "--list-models", help="List available models", action="store_true")
        parser.add_argument("-f", "--followup", help="Enable followup prompts", action="store_true")
        parser.add_argument(
            "-t",
            "--temperature",
            help="Sampling temperature of the model, 0.0-2.0. Higher values make the output more random",
            default=1.0,
        )
        parser.add_argument("prompt", nargs=REMAINDER)
        return parser
