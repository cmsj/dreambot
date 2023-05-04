import json

from typing import Any, Callable, Coroutine
from argparse import REMAINDER, ArgumentError
from transformers import AutoModelForCausalLM, AutoTokenizer, set_seed  # type: ignore
import torch

from dreambot.backend.base import DreambotBackendBase
from dreambot.shared.worker import UsageException, ErrorCatchingArgumentParser


class DreambotBackendReplit(DreambotBackendBase):
    def __init__(
        self, options: dict[str, Any], callback_send_workload: Callable[[str, bytes], Coroutine[Any, Any, None]]
    ):
        super().__init__("Replit", options, callback_send_workload)
        self.hf_token = options["hugging_face_token"]

    async def boot(self):
        self.logger.info("Booting model...")
        self.tokenizer = AutoTokenizer.from_pretrained("replit/replit-code-v1-3b", use_auth_token=self.hf_token, trust_remote_code=True)  # type: ignore
        self.tokenizer.truncation_side = "left"  # type: ignore

        self.model = AutoModelForCausalLM.from_pretrained("replit/replit-code-v1-3b", use_auth_token=self.hf_token, trust_remote_code=True)  # type: ignore
        self.model.to(device="cuda", dtype=torch.bfloat16)  # type: ignore

        self.model.eval()  # type: ignore
        self.logger.info("Booted")

    async def shutdown(self):
        return

    async def callback_receive_workload(self, queue_name: str, message: bytes) -> bool:
        self.logger.info("callback_receive_workload: {}".format(message.decode()))
        try:
            resp = json.loads(message.decode())
        except Exception as e:
            self.logger.error("Failed to parse message: {}".format(e))
            return True

        try:
            argparser = self.arg_parser()
            args = argparser.parse_args(resp["prompt"].split(" "))
            args.prompt = " ".join(args.prompt)

            # Tokenize our prompt and do inference
            self.logger.debug("Sending request to Replit...")
            tokens = self.tokenizer.encode(args.prompt, return_tensors="pt", max_length=1024, truncation=True)  # type: ignore
            tokens.to("cuda")  # type: ignore

            set_seed(args.seed)

            generation = self.model.generate(  # type: ignore
                tokens,
                max_new_tokens=48,  # FIXME: Make this an arg. Min 8, Max 128, steps 1, default 48
                temperature=args.temperature,
                pad_token_id=self.tokenizer.pad_token_id,  # type: ignore
                eos_token_id=self.tokenizer.eos_token_id,  # type: ignore
                top_p=0.95,  # FIXME: Make this an arg, see https://huggingface.co/spaces/replit/replit-code-v1-3b-demo/blob/main/app.py
                top_k=4,  # FIXME: Ditto
                use_cache=True,  # FIXME: Ditto
                repetition_penalty=1.0,  # FIXME: Ditto
            )

            # Decoding our result
            response = self.tokenizer.decode(  # type: ignore
                generation[0], skip_special_tokens=True, clean_up_tokenization_spaces=False
            )

            # Fetch the response, prepare it to be sent back to the user and added to their cache
            resp["reply-text"] = response

        except UsageException as e:
            # This isn't strictly an error, but it's the easiest way to reply with our --help text, which is in the UsageException
            resp["reply-text"] = str(e)
        except (ValueError, ArgumentError) as e:
            resp["error"] = "Something is wrong with your arguments, try {}} --help ({})".format(self.queue_name, e)
        except Exception as e:
            resp["error"] = "Unknown error: {}".format(e)

        await self.send_message(resp)
        return True

    async def send_message(self, resp: dict[str, Any]):
        try:
            self.logger.info("Sending response: {} with {}".format(resp, self.callback_send_workload))
            packet = json.dumps(resp)
            await self.callback_send_workload(resp["reply-to"], packet.encode())
        except Exception as e:
            self.logger.error("Failed to send response: {}".format(e))

    def arg_parser(self) -> ErrorCatchingArgumentParser:
        parser = super().arg_parser()
        parser.add_argument("-s", "--seed", help="Random seed for the model", default=42, type=int)
        parser.add_argument(
            "-t",
            "--temperature",
            help="Sampling temperature of the model, 0.0-2.0. Higher values make the output more random",
            default=0.2,
        )
        parser.add_argument("prompt", nargs=REMAINDER)
        return parser
