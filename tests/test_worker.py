# pylint: skip-file
import pytest
import dreambot.shared.worker


class TestWorker(dreambot.shared.worker.DreambotWorkerBase):
    def queue_name(self):
        return "test_queue"

    async def boot(self):
        self.is_booted = True

    async def shutdown(self):
        pass

    async def callback_receive_workload(self, queue_name: str, message: bytes) -> bool:
        return False


def test_clean_filename():
    worker = dreambot.shared.worker.DreambotWorkerBase()
    assert worker.clean_filename("test", output_dir="/tmp/") == "test.png"
    assert worker.clean_filename("test&", output_dir="/tmp/") == "test.png"
    assert worker.clean_filename("test&", replace="&", output_dir="/tmp/") == "test_.png"


@pytest.mark.asyncio
async def test_unimplemented():
    worker = dreambot.shared.worker.DreambotWorkerBase()

    with pytest.raises(NotImplementedError):
        worker.queue_name()

    with pytest.raises(NotImplementedError):
        await worker.boot()

    with pytest.raises(NotImplementedError):
        await worker.shutdown()

    with pytest.raises(NotImplementedError):
        await worker.callback_receive_workload("some_queue", b"some_message")


def test_arg_parser():
    worker = TestWorker()
    parser = worker.arg_parser()

    assert isinstance(parser, dreambot.shared.worker.ErrorCatchingArgumentParser)

    with pytest.raises(dreambot.shared.worker.UsageException):
        args = parser.parse_args(["-h"])

    parser.add_argument("-t", "--test", help="test help")
    args = parser.parse_args(["-t", "testvalue"])
    assert args.test == "testvalue"

    with pytest.raises(ValueError):
        args = parser.parse_args(["--unknown"])
