"""Backend for Replit."""
import json

from typing import Any, Callable, Coroutine
from argparse import REMAINDER, ArgumentError
from transformers import AutoModelForCausalLM, AutoTokenizer, set_seed  # type: ignore

import torch

from dreambot.backend.base import DreambotBackendBase
from dreambot.shared.worker import UsageException, ErrorCatchingArgumentParser


class DreambotBackendReplit(DreambotBackendBase):
    """Dreambot backend for Replit."""

    def __init__(
        self, options: dict[str, Any], callback_send_workload: Callable[[str, bytes], Coroutine[Any, Any, None]]
    ):
        """Initialise the Replit backend."""
        super().__init__("Replit", options, callback_send_workload)
        self.hf_token = options["hugging_face_token"]
        self.tokenizer = None
        self.model = None

    async def boot(self):
        """Boot our model on the GPU."""
        self.logger.info("Booting model...")
        self.tokenizer = AutoTokenizer.from_pretrained("replit/replit-code-v1-3b", use_auth_token=self.hf_token, trust_remote_code=True)  # type: ignore
        self.tokenizer.truncation_side = "left"  # type: ignore

        self.model = AutoModelForCausalLM.from_pretrained("replit/replit-code-v1-3b", use_auth_token=self.hf_token, trust_remote_code=True).to("cuda", dtype=torch.bfloat16)  # type: ignore pylint: disable=no-member

        self.model.eval()  # type: ignore
        self.logger.info("Booted")

    async def shutdown(self):
        """Shutdown our model."""
        return

    async def callback_receive_workload(self, queue_name: str, message: bytes) -> bool:
        """Receive work from NATS.

        Args:
            queue_name (str): The name of the queue we received the message from
            message (bytes): The full message we received

        Returns:
            bool: True if we either processed successfully, or failed to process in a way that suggests the message should be discarded. Otherwise False.
        """
        self.logger.info("callback_receive_workload: %s", message.decode())
        try:
            resp = json.loads(message.decode())
        except Exception as exc:
            self.logger.error("Failed to parse message: %s", exc)
            return True

        try:
            argparser = self.arg_parser()
            args = argparser.parse_args(resp["prompt"].split(" "))
            args.prompt = " ".join(args.prompt)

            # Tokenize our prompt and do inference
            self.logger.debug("Sending request to Replit...")
            tokens = self.tokenizer.encode(args.prompt, return_tensors="pt", max_length=1024, truncation=True).to("cuda")  # type: ignore

            set_seed(args.seed)

            generation = self.model.generate(  # type: ignore
                tokens,
                max_new_tokens=args.max_new_tokens,
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

        except UsageException as exc:
            # This isn't strictly an error, but it's the easiest way to reply with our --help text, which is in the UsageException
            resp["reply-text"] = str(exc)
        except (ValueError, ArgumentError) as exc:
            resp["error"] = f"Something is wrong with your arguments, try {self.queue_name()} --help ({exc})"
        except Exception as exc:
            resp["error"] = f"Unknown error: {exc}"

        await self.send_message(resp)
        return True

    async def send_message(self, resp: dict[str, Any]):
        """Send a message to NATS.

        Args:
            resp (dict[str, Any]): A dictionary containing the message to send.
        """
        try:
            self.logger.info("Sending response: %s with %s", resp, self.callback_send_workload)
            packet = json.dumps(resp)
            await self.callback_send_workload(resp["reply-to"], packet.encode())
        except Exception as exc:
            self.logger.error("Failed to send response: %s", format(exc))

    def arg_parser(self) -> ErrorCatchingArgumentParser:
        """Create an argument parser for this backend.

        Returns:
            ErrorCatchingArgumentParser: An argument parser object that can with parse_args()
        """
        parser = super().arg_parser()
        parser.add_argument("-s", "--seed", help="Random seed for the model", default=42, type=int)
        parser.add_argument(
            "-t",
            "--temperature",
            help="Sampling temperature of the model, 0.0-2.0. Higher values make the output more random",
            default=0.2,
            type=float,
        )
        parser.add_argument(
            "-m",
            "--max-new-tokens",
            help="Maximum number of tokens to generate",
            default=48,
            type=int,
            choices=range(8, 129),
            metavar="[8-128]",
        )
        parser.add_argument("prompt", nargs=REMAINDER)
        return parser
