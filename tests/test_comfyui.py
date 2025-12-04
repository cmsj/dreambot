# pylint: skip-file
import pytest
from dreambot.backend.comfyui import DreambotBackendComfyUI
from unittest.mock import MagicMock, AsyncMock, patch


@pytest.mark.asyncio
async def test_comfyui_boot():
    """Test that the ComfyUI backend can boot."""
    options = {
        "comfyui": {
            "host": "localhost",
            "port": "8188",
            "default_workflow": "txt2img",
            "workflows": {
                "txt2img": {
                    "workflow": {}
                }
            }
        }
    }
    backend = DreambotBackendComfyUI(options, MagicMock())
    await backend.boot()
    assert backend.is_booted is True


@pytest.mark.asyncio
async def test_comfyui_shutdown():
    """Test that the ComfyUI backend can shutdown."""
    options = {
        "comfyui": {
            "host": "localhost",
            "port": "8188",
            "default_workflow": "txt2img",
            "workflows": {
                "txt2img": {
                    "workflow": {}
                }
            }
        }
    }
    backend = DreambotBackendComfyUI(options, MagicMock())
    await backend.shutdown()
    # No exception means success


@pytest.mark.asyncio
async def test_comfyui_arg_parser():
    """Test the ComfyUI argument parser."""
    options = {
        "comfyui": {
            "host": "localhost",
            "port": "8188",
            "default_workflow": "txt2img",
            "workflows": {
                "txt2img": {
                    "workflow": {}
                }
            }
        }
    }
    backend = DreambotBackendComfyUI(options, MagicMock())
    parser = backend.arg_parser()

    # Test basic prompt parsing
    args = parser.parse_args(["test", "prompt"])
    assert args.prompt == ["test", "prompt"]
    assert args.imgurl is None
    assert args.workflow == "txt2img"

    # Test with workflow argument
    args = parser.parse_args(["-w", "custom", "test", "prompt"])
    assert args.workflow == "custom"
    assert args.prompt == ["test", "prompt"]

    # Test with image URL
    args = parser.parse_args(["-i", "http://example.com/image.png", "test"])
    assert args.imgurl == "http://example.com/image.png"
    assert args.prompt == ["test"]


@pytest.mark.asyncio
async def test_comfyui_list_workflows():
    """Test listing available workflows."""
    options = {
        "comfyui": {
            "host": "localhost",
            "port": "8188",
            "default_workflow": "txt2img",
            "workflows": {
                "txt2img": {
                    "workflow": {}
                },
                "img2img": {
                    "workflow": {}
                }
            }
        }
    }
    callback = AsyncMock()
    backend = DreambotBackendComfyUI(options, callback)

    message = {
        "prompt": "-l",
        "trigger": "!comfy",
        "to": "backend.comfyui",
        "reply-to": "frontend.test"
    }

    await backend.callback_receive_workload("test_queue", message)

    assert "reply-text" in message
    assert "txt2img" in message["reply-text"]
    assert "img2img" in message["reply-text"]


@pytest.mark.asyncio
async def test_comfyui_invalid_workflow():
    """Test error handling for invalid workflow name."""
    options = {
        "comfyui": {
            "host": "localhost",
            "port": "8188",
            "default_workflow": "txt2img",
            "workflows": {
                "txt2img": {
                    "workflow": {}
                }
            }
        }
    }
    callback = AsyncMock()
    backend = DreambotBackendComfyUI(options, callback)

    message = {
        "prompt": "-w invalid_workflow test prompt",
        "trigger": "!comfy",
        "to": "backend.comfyui",
        "reply-to": "frontend.test"
    }

    await backend.callback_receive_workload("test_queue", message)

    assert "error" in message
    assert "Unknown workflow" in message["error"]
    assert "invalid_workflow" in message["error"]
    assert "txt2img" in message["error"]
