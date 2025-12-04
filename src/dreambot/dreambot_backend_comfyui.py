"""Dreambot ComfyUI backend launcher."""

from dreambot.backend.comfyui import DreambotBackendComfyUI
from dreambot.shared.cli import DreambotCLI


class DreambotBackendComfyUICLI(DreambotCLI):
    """Dreambot ComfyUI backend launcher."""

    example_json = """Example JSON config:
{
  "comfyui": {
      "host": "localhost",
      "port": "8188",
      "default_workflow": "txt2img",
      "workflows": {
        "txt2img": {
                "workflow": {
                  "3": {
                    "class_type": "KSampler",
                    "inputs": {
                      "seed": -1,
                      "steps": 20,
                      "cfg": 8.0,
                      "sampler_name": "euler",
                      "scheduler": "normal",
                      "denoise": 1.0,
                      "model": ["4", 0],
                      "positive": ["6", 0],
                      "negative": ["7", 0],
                      "latent_image": ["5", 0]
                    }
                  },
                  "4": {
                    "class_type": "CheckpointLoaderSimple",
                    "inputs": {
                      "ckpt_name": "sd_xl_base_1.0.safetensors"
                    }
                  },
                  "5": {
                    "class_type": "EmptyLatentImage",
                    "inputs": {
                      "width": 512,
                      "height": 512,
                      "batch_size": 1
                    }
                  },
                  "6": {
                    "class_type": "CLIPTextEncode",
                    "inputs": {
                      "text": "beautiful scenery",
                      "clip": ["4", 1]
                    }
                  },
                  "7": {
                    "class_type": "CLIPTextEncode",
                    "inputs": {
                      "text": "text, watermark",
                      "clip": ["4", 1]
                    }
                  },
                  "8": {
                    "class_type": "VAEDecode",
                    "inputs": {
                      "samples": ["3", 0],
                      "vae": ["4", 2]
                    }
                  },
                  "9": {
                    "class_type": "SaveImage",
                    "inputs": {
                      "filename_prefix": "ComfyUI",
                      "images": ["8", 0]
                    }
                  }
                }
        }
      }
  },
  "nats_uri": [ "nats://nats-1:4222", "nats://nats-2:4222" ]
}"""

    def __init__(self):
        """Initialise the instance."""
        super().__init__("BackendComfyUI")

    def boot(self):
        """Boot the instance."""
        super().boot()

        try:
            worker = DreambotBackendComfyUI(self.options, self.callback_send_workload)
            self.workers.append(worker)
        except Exception as exc:
            self.logger.error("Exception during boot: %s", exc)

        self.run()


def main():
    """Start the program."""
    cli = DreambotBackendComfyUICLI()
    cli.boot()


if __name__ == "__main__":
    main()
