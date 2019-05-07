import asyncio
import inspect
import time
import typing
from collections import deque

T = typing.TypeVar('T')
R = typing.TypeVar('R')


class IteratorWrapper(typing.AsyncIterator):
    __slots__ = (
        "loop", "closed", "executor", "__close_event",
        "__queue", "__queue_maxsize", "__gen_task", "__gen_func"
    )

    def __init__(self, gen_func: typing.Generator[T, None, R],
                 loop=None, max_size=0, executor=None):

        self.loop = loop or asyncio.get_event_loop()
        self.closed = False
        self.executor = executor

        self.__close_event = asyncio.Event(loop=self.loop)
        self.__queue = deque()
        self.__queue_maxsize = max_size
        self.__gen_task = None      # type: asyncio.Task
        self.__gen_func = gen_func  # type: typing.Callable

    @staticmethod
    def __throw(_):
        pass

    def __in_thread(self):
        try:
            gen = iter(self.__gen_func())

            throw = self.__throw
            if inspect.isgenerator(gen):
                throw = gen.throw

            while not self.closed:
                item = next(gen)

                while len(self.__queue) > self.__queue_maxsize:
                    time.sleep(0)

                    if self.closed:
                        throw(asyncio.CancelledError())
                        return

                self.__queue.append((item, False))
        except StopIteration as e:
            if self.closed:
                return
            self.__queue.append((e, None))
        except Exception as e:
            if self.closed:
                return
            self.__queue.append((e, True))
        finally:
            self.loop.call_soon_threadsafe(self.__close_event.set)

    async def close(self):
        self.closed = True
        self.__queue.clear()

        if not self.__gen_task.done():
            self.__gen_task.cancel()

        await self.__close_event.wait()
        del self.__queue

    def __aiter__(self):
        if self.__gen_task is not None:
            return self

        self.__gen_task = self.loop.run_in_executor(
            self.executor, self.__in_thread
        )
        return self

    async def __anext__(self) -> typing.Awaitable[T]:
        while not len(self.__queue):
            await asyncio.sleep(0, loop=self.loop)

        item, is_exc = self.__queue.popleft()

        if is_exc is None:
            await self.close()
            raise StopAsyncIteration(*item.args) from item
        elif is_exc:
            await self.close()
            raise item from item

        return item

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.closed:
            return

        await self.close()
