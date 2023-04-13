import asyncio
import json
import traceback
import openai

from openai.error import APIError, Timeout, ServiceUnavailableError, RateLimitError, AuthenticationError, InvalidRequestError
from dreambot.backend.base import DreambotBackendBase

class DreambotBackendGPT(DreambotBackendBase):
    backend_name = "GPT"

    api_key = None
    organization = None
    model = None
    chat_cache = {}

    def __init__(self, options, callback_send_message):
        super().__init__(options, callback_send_message)
        self.api_key = options["gpt"]["api_key"]
        self.organization = options["gpt"]["organization"]
        self.model = options["gpt"]["model"]
        self.logger.debug("Set GPT options to: api_key={}, organization={}, model={}".format(self.api_key, self.organization, self.model))

    async def boot(self):
        openai.api_key = self.api_key
        openai.organization = self.organization

    async def callback_receive_message(self, _, data):
        self.logger.info("callback_receive_message: {}".format(data.decode()))
        try:
            resp = json.loads(data.decode())
        except Exception as e:
            self.logger.error("Failed to parse message: {}".format(e))
            return True

        try:
            prompt = resp["prompt"]
            message = {"role": "user", "content": prompt}

            # Ensure we have a valid cache line for this user
            cache_key = self.cache_name_for_prompt(resp)
            if cache_key not in self.chat_cache:
                self.logger.debug("Creating new cache entry for {}".format(cache_key))
                self.reset_cache(cache_key)

            # Determine if we're adding to the cache or starting a new conversation
            if not prompt.startswith("!followup"):
                self.logger.debug("Starting new conversation for '{}'".format(cache_key))
                self.reset_cache(cache_key)
            else:
                self.logger.debug("Adding to existing conversation for '{}'".format(cache_key))

            # Now that our cache is in the right state, add this new prompt to it
            self.chat_cache[cache_key].append(message)

            # Now we can ask OpenAI for a response to the contents of our message cache
            self.logger.debug("Sending request to OpenAI...")
            response = openai.ChatCompletion.create(model = self.model, messages = self.chat_cache[cache_key])

            # Fetch the response, prepare it to be sent back to the user and added to their cache
            reply = response.choices[0].message.content

            resp["reply-text"] = reply
            self.chat_cache[cache_key].append({"role": "assistant", "content": reply})
        except (APIError, Timeout, ServiceUnavailableError) as e:
            self.logger.error("GPT service access error: {}".format(e))
            resp["error"] = "GPT service unavailable, try again."
        except (RateLimitError, AuthenticationError) as e:
            self.logger.error("GPT service query error: {}".format(e))
            resp["error"] = "GPT service error, ask your bot admin to check logs."
        except InvalidRequestError as e:
            self.logger.error("GPT request error: {}".format(e))
            resp["error"] = "GPT request error, ask your bot admin to check logs."
        except Exception as e:
            self.logger.error("Unknown error: {}".format(e))
            resp["error"] = "Unknown error, ask your bot admin to check logs."

        try:
            self.logger.info("Sending response: {} with {}".format(resp, self.callback_send_message))
            packet = json.dumps(resp)
            await self.callback_send_message(resp["reply-to"], packet.encode())
            self.logger.debug("Response sent!")
        except Exception as e:
            self.logger.error("Failed to send response: {}".format(e))
            traceback.print_exc()

        return True

    def cache_name_for_prompt(self, data):
        return "{}_{}_{}".format(data["reply-to"], data["channel"], data["user"])

    def reset_cache(self, key):
        self.chat_cache[key] = [{"role": "system", "content": "You are a helpful assistant. Make your answers as brief as possible."}]