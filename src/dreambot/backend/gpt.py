import json
import traceback
import openai

from typing import Any, Callable, Coroutine
from argparse import REMAINDER, ArgumentError
from openai.error import (
    APIError,
    Timeout,
    ServiceUnavailableError,
    RateLimitError,
    AuthenticationError,
    InvalidRequestError,
)
from dreambot.backend.base import DreambotBackendBase
from dreambot.shared.worker import UsageException, ErrorCatchingArgumentParser


class DreambotBackendGPT(DreambotBackendBase):
    def __init__(
        self, options: dict[str, Any], callback_send_workload: Callable[[str, bytes], Coroutine[Any, Any, None]]
    ):
        super().__init__("GPT", options, callback_send_workload)
        self.api_key = options["gpt"]["api_key"]
        self.organization = options["gpt"]["organization"]
        self.model = options["gpt"]["model"]
        self.chat_cache: dict[str, Any] = {}

        self.logger.debug(
            "Set GPT options to: api_key={}, organization={}, model={}".format(
                self.api_key, self.organization, self.model
            )
        )

    async def boot(self):
        openai.api_key = self.api_key
        openai.organization = self.organization

    async def shutdown(self):
        return

    async def callback_receive_workload(self, queue_name: str, message: bytes) -> bool:
        self.logger.info("callback_receive_workload: {}".format(message.decode()))
        try:
            resp = json.loads(message.decode())
        except Exception as e:
            self.logger.error("Failed to parse messagelol: {}".format(e))
            return True

        try:
            argparser = self.arg_parser()
            args = argparser.parse_args(resp["prompt"].split(" "))
            args.prompt = " ".join(args.prompt)

            new_chat_message = {"role": "user", "content": args.prompt}

            # Ensure we have a valid cache line for this user
            cache_key = self.cache_name_for_prompt(resp)
            if cache_key not in self.chat_cache:
                self.logger.debug("Creating new cache entry for {}".format(cache_key))
                self.reset_cache(cache_key)

            # Determine if we're adding to the cache or starting a new conversation
            if not args.followup:
                self.logger.debug("Starting new conversation for '{}'".format(cache_key))
                self.reset_cache(cache_key)
            else:
                self.logger.debug("Adding to existing conversation for '{}'".format(cache_key))

            if args.list_models:
                # We have to hard code this because the OpenAI API endpoint lists dozens of models that can't be used for Chat Completions
                # see https://platform.openai.com/docs/models/model-endpoint-compatibility

                models = ["gpt-3.5-turbo", "gpt-3.5-turbo-0301"]
                reply = ", ".join(models)  # type: ignore
            else:
                # Now that our cache is in the right state, add this new prompt to it
                self.chat_cache[cache_key].append(new_chat_message)

                # Now we can ask OpenAI for a response to the contents of our message cache
                self.logger.debug("Sending request to OpenAI...")
                response = openai.ChatCompletion.create(model=args.model, messages=self.chat_cache[cache_key], temperature=args.temperature)  # type: ignore

                # Fetch the response, prepare it to be sent back to the user and added to their cache
                reply = response.choices[0].message.content  # type: ignore

            resp["reply-text"] = reply
            self.chat_cache[cache_key].append({"role": "assistant", "content": reply})
        except UsageException as e:
            # This isn't strictly an error, but it's the easiest way to reply with our --help text, which is in the UsageException
            resp["reply-text"] = str(e)
        except (APIError, Timeout, ServiceUnavailableError) as e:
            self.logger.error("GPT service access error: {}".format(e))
            resp["error"] = "GPT service unavailable, try again."
        except (RateLimitError, AuthenticationError) as e:
            self.logger.error("GPT service query error: {}".format(e))
            resp["error"] = "GPT service error, ask your bot admin to check logs."
        except InvalidRequestError as e:
            self.logger.error("GPT request error: {}".format(e))
            resp["error"] = "GPT request error, ask your bot admin to check logs."
        except (ValueError, ArgumentError) as e:
            self.logger.error("GPT argument error: {}".format(e))
            resp["error"] = "Something is wrong with your arguments, try !gpt --help"
        except Exception as e:
            self.logger.error("Unknown error: {}".format(e))
            resp["error"] = "Unknown error, ask your bot admin to check logs."

        try:
            self.logger.info("Sending response: {} with {}".format(resp, self.callback_send_workload))
            packet = json.dumps(resp)
            await self.callback_send_workload(resp["reply-to"], packet.encode())
            self.logger.debug("Response sent!")
        except Exception as e:
            self.logger.error("Failed to send response: {}".format(e))
            traceback.print_exc()

        return True

    def cache_name_for_prompt(self, data: dict[str, Any]):
        return "{}_{}_{}".format(data["reply-to"], data["channel"], data["user"])

    def reset_cache(self, key: str):
        self.chat_cache[key] = [
            {
                "role": "system",
                "content": "You are a helpful assistant. Make your answers as brief as possible.",
            }
        ]

    def arg_parser(self) -> ErrorCatchingArgumentParser:
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