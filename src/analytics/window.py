from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Iterator
from typing import Generic, TypeVar

T = TypeVar("T")


class SlidingWindowBuffer(Generic[T]):
    """Fixed-size rolling buffer for near-term analytics events.

    Uses a deque with a bounded max length so older records fall out
    automatically when the buffer exceeds capacity.
    """

    MAX_ENTRIES: int = 100

    def __init__(self, maxlen: int = MAX_ENTRIES) -> None:
        self._buffer: deque[T] = deque(maxlen=maxlen)

    def append(self, item: T) -> None:
        self._buffer.append(item)

    def extend(self, items: Iterable[T]) -> None:
        self._buffer.extend(items)

    def clear(self) -> None:
        self._buffer.clear()

    def __len__(self) -> int:
        return len(self._buffer)

    def __iter__(self) -> Iterator[T]:
        return iter(self._buffer)

    def to_list(self) -> list[T]:
        return list(self._buffer)
