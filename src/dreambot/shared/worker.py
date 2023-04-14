class DreambotWorkerBase:
    def queue_name(self) -> str:
        raise NotImplementedError

    async def boot(self) -> None:
        raise NotImplementedError

    async def shutdown(self) -> None:
        raise NotImplementedError

    async def callback_receive_message(self, queue_name: str, message: bytes) -> bool:
        raise NotImplementedError
