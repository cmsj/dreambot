"""Backend for Replit."""
import asyncio
import traceback

from typing import Any
from argparse import REMAINDER, ArgumentError
from transformers import AutoModelForCausalLM, AutoTokenizer, set_seed  # type: ignore

import torch

from dreambot.shared.custom_argparse import UsageException, ErrorCatchingArgumentParser
from dreambot.shared.worker import DreambotWorkerBase, DreambotWorkerEndType, CallbackSendWorkload


class DreambotBackendReplit(DreambotWorkerBase):
    """Dreambot backend for Replit."""

    def __init__(self, options: dict[str, Any], callback_send_workload: CallbackSendWorkload):
        """Initialise the Replit backend."""
        super().__init__(
            name="replit",
            end=DreambotWorkerEndType.BACKEND,
            options=options,
            callback_send_workload=callback_send_workload,
        )
        self.hf_token = options["hugging_face_token"]
        self.tokenizer = None
        self.model = None

    async def boot(self):
        """Boot our model on the GPU."""
        self.logger.info("Loading tokenizer...")
        self.tokenizer = AutoTokenizer.from_pretrained("replit/replit-code-v1-3b", use_auth_token=self.hf_token, trust_remote_code=True)  # type: ignore
        self.tokenizer.truncation_side = "left"  # type: ignore

        await asyncio.sleep(0)  # Since our boot takes a long time, give asyncio a chance to run its tasks

        self.logger.info("Loading model...")
        self.model = AutoModelForCausalLM.from_pretrained("replit/replit-code-v1-3b", use_auth_token=self.hf_token, trust_remote_code=True).to("cuda", dtype=torch.bfloat16)  # type: ignore pylint: disable=no-member

        self.model.eval()  # type: ignore
        self.logger.info("Booted")
        self.is_booted = True

    async def shutdown(self):
        """Shutdown our model."""
        return

    async def callback_receive_workload(self, queue_name: str, message: dict[str, Any]) -> bool:
        """Receive work from NATS.

        Args:
            queue_name (str): The name of the queue we received the message from
            message (bytes): The full message we received

        Returns:
            bool: True if we either processed successfully, or failed to process in a way that suggests the message should be discarded. Otherwise False.
        """
        self.logger.info("callback_receive_workload: %s", message)

        try:
            argparser = self.arg_parser()
            args = argparser.parse_args(message["prompt"].split(" "))
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
