import asyncio
import openai
from openai.error import APIError, Timeout, ServiceUnavailableError, RateLimitError, AuthenticationError, InvalidRequestError

from dreambot.backend.base import DreambotBackendBase

class DreambotBackendGPT(DreambotBackendBase):
    api_key = None
    organization = None
    model = None
    backend_name = "GPT"
    chat_cache = {}

    def __init__(self, nats_options, gpt_options):
        super().__init__(nats_options)
        self.api_key = gpt_options["api_key"]
        self.organization = gpt_options["organization"]
        self.model = gpt_options["model"]
        self.logger.debug("Set GPT options to: api_key={}, organization={}, model={}".format(self.api_key, self.organization, self.model))

    def cache_name_for_prompt(self, data):
        return "{}_{}_{}".format(data["reply-to"], data["channel"], data["user"])

    def reset_cache(self, key):
        self.chat_cache[key] = [{"role": "system", "content": "You are a helpful assistant. Make your answers as brief as possible."}]

    async def boot(self):
        def gpt_callback(data):
            try:
                prompt = data["prompt"]
                message = {"role": "user", "content": prompt}

                # Ensure we have a valid cache line for this user
                cache_key = self.cache_name_for_prompt(data)
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
                response = openai.ChatCompletion.create(model = self.model, messages = self.chat_cache[cache_key])

                # Fetch the response, prepare it to be sent back to the user and added to their cache
                reply = response.choices[0].message.content
                data["reply-text"] = reply
                self.chat_cache[cache_key].append({"role": "assistant", "content": reply})
            except (APIError, Timeout, ServiceUnavailableError) as e:
                self.logger.error("GPT service access error: {}".format(e))
                data["error"] = "GPT service unavailable, try again."
            except (RateLimitError, AuthenticationError) as e:
                self.logger.error("GPT service query error: {}".format(e))
                data["error"] = "GPT service error, ask your bot admin to check logs."
            except InvalidRequestError as e:
                self.logger.error("GPT request error: {}".format(e))
                data["error"] = "GPT request error, ask your bot admin to check logs."
            except Exception as e:
                self.logger.error("Unknown error: {}".format(e))
                data["error"] = "Unknown error, ask your bot admin to check logs."
            return data

        # Set the details OpenAI need, and call the base class boot with our callback
        openai.api_key = self.api_key
        openai.organization = self.organization
        await super().boot(gpt_callback)
