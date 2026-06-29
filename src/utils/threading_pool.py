from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional
import logging
from multiprocessing import shared_memory
import struct


logger = logging.getLogger("Utils.SharedMemory")
# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MIN_WORKERS: int = 4
MAX_WORKERS: int = 16

# Supervisor evaluates queue depth every N seconds.
_SUPERVISOR_INTERVAL: float = 2.0

# Scale-up when queue depth exceeds this threshold per active worker.
_SCALE_UP_RATIO: float = 2.0

# Scale-down when queue depth drops below this threshold per active worker.
_SCALE_DOWN_RATIO: float = 0.5

# How long an idle worker waits for an item before looping (keeps threads
# responsive to the stop event without busy-spinning).
_WORKER_TIMEOUT: float = 1.0


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PoolSnapshot:
    """Immutable view of pool state at a point in time."""

    active_workers: int
    queue_depth: int
    tasks_completed: int
    tasks_failed: int


@dataclass
class _PoolState:
    """Mutable internal counters — always accessed under _lock."""

    worker_count: int = MIN_WORKERS
    tasks_completed: int = 0
    tasks_failed: int = 0


# ---------------------------------------------------------------------------
# Worker function
# ---------------------------------------------------------------------------


def _worker(
    work_queue: queue.Queue,
    stop_event: threading.Event,
    state: _PoolState,
    lock: threading.Lock,
) -> None:
    """Main loop executed by each worker thread.

    Dequeues callables and runs them.  Increments completion/failure counters
    under *lock* so the supervisor can read consistent metrics.
    """
    while not stop_event.is_set():
        try:
            task: Callable = work_queue.get(timeout=_WORKER_TIMEOUT)
        except queue.Empty:
            continue

        try:
            task()
            with lock:
                state.tasks_completed += 1
        except Exception:
            logger.exception("Worker caught unhandled exception in task")
            with lock:
                state.tasks_failed += 1
        finally:
            work_queue.task_done()


# ---------------------------------------------------------------------------
# Supervisor
# ---------------------------------------------------------------------------


def _supervisor(
    work_queue: queue.Queue,
    threads: list[threading.Thread],
    stop_event: threading.Event,
    state: _PoolState,
    lock: threading.Lock,
    thread_factory: Callable[[], threading.Thread],
) -> None:
    """Background supervisor that adjusts worker count based on queue depth.

    Scale-up rule: ``queue_depth / active_workers > SCALE_UP_RATIO``
    Scale-down rule: ``queue_depth / active_workers < SCALE_DOWN_RATIO``

    Worker count is clamped to [MIN_WORKERS, MAX_WORKERS].
    """
    while not stop_event.is_set():
        time.sleep(_SUPERVISOR_INTERVAL)

        with lock:
            depth = work_queue.qsize()
            current = state.worker_count

        if current == 0:
            ratio = float("inf")
        else:
            ratio = depth / current

        if ratio > _SCALE_UP_RATIO and current < MAX_WORKERS:
            # Add one worker per supervisor tick to avoid overshooting.
            new_thread = thread_factory()
            new_thread.start()
            with lock:
                threads.append(new_thread)
                state.worker_count += 1
            logger.info(
                "ThreadingPool: scaled UP to %d workers (queue depth %d)",
                state.worker_count,
                depth,
            )

        elif ratio < _SCALE_DOWN_RATIO and current > MIN_WORKERS:
            # Signal one idle worker to exit on its next empty-queue loop by
            # enqueuing a sentinel None value that workers check for.
            work_queue.put(None)  # handled below — see _worker_with_sentinel
            with lock:
                state.worker_count -= 1
            logger.info(
                "ThreadingPool: scaled DOWN to %d workers (queue depth %d)",
                state.worker_count,
                depth,
            )


# ---------------------------------------------------------------------------
# Pool
# ---------------------------------------------------------------------------


class DynamicThreadingPool:
    """Automated worker-scaling thread pool.

    Starts with :data:`MIN_WORKERS` threads and adjusts dynamically between
    :data:`MIN_WORKERS` and :data:`MAX_WORKERS` depending on real-time queue
    depth.

    Usage::

        pool = DynamicThreadingPool()
        pool.start()

        pool.submit(my_callable)
        pool.submit(lambda: process(item))

        pool.stop()

    The pool is also usable as a context manager::

        with DynamicThreadingPool() as pool:
            pool.submit(my_callable)
    """

    def __init__(
        self,
        min_workers: int = MIN_WORKERS,
        max_workers: int = MAX_WORKERS,
        supervisor_interval: float = _SUPERVISOR_INTERVAL,
    ) -> None:
        if min_workers < 1:
            raise ValueError("min_workers must be >= 1")
        if max_workers < min_workers:
            raise ValueError("max_workers must be >= min_workers")

        self._min_workers = min_workers
        self._max_workers = max_workers
        self._supervisor_interval = supervisor_interval

        self._work_queue: queue.Queue = queue.Queue()
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._state = _PoolState(worker_count=min_workers)
        self._threads: list[threading.Thread] = []
        self._supervisor_thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _make_worker_thread(self) -> threading.Thread:
        """Create (but do not start) a daemon worker thread."""
        return threading.Thread(
            target=_worker_with_sentinel,
            args=(
                self._work_queue,
                self._stop_event,
                self._state,
                self._lock,
            ),
            daemon=True,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Spawn initial workers and the supervisor thread."""
        if self._supervisor_thread is not None:
            raise RuntimeError("Pool is already running")

        for _ in range(self._min_workers):
            t = self._make_worker_thread()
            t.start()
            self._threads.append(t)

        self._supervisor_thread = threading.Thread(
            target=_supervisor,
            args=(
                self._work_queue,
                self._threads,
                self._stop_event,
                self._state,
                self._lock,
                self._make_worker_thread,
            ),
            daemon=True,
            name="ThreadingPool-Supervisor",
        )
        self._supervisor_thread.start()
        logger.info(
            "ThreadingPool: started with %d workers (min=%d, max=%d)",
            self._min_workers,
            self._min_workers,
            self._max_workers,
        )

    def submit(self, task: Callable) -> None:
        """Enqueue *task* for execution by a worker thread.

        Raises ``RuntimeError`` if the pool has been stopped.
        """
        if self._stop_event.is_set():
            raise RuntimeError("Cannot submit tasks to a stopped pool")
        self._work_queue.put(task)

    def stop(self, wait: bool = True, timeout: Optional[float] = None) -> None:
        """Signal all workers and the supervisor to stop.

        Parameters
        ----------
        wait:
            If ``True`` (default), block until all threads have exited.
        timeout:
            Optional per-thread join timeout in seconds.
        """
        self._stop_event.set()

        if wait:
            if self._supervisor_thread is not None:
                self._supervisor_thread.join(timeout=timeout)
            for t in self._threads:
                t.join(timeout=timeout)

        logger.info("ThreadingPool: stopped")

    def snapshot(self) -> PoolSnapshot:
        """Return an immutable snapshot of current pool metrics."""
        with self._lock:
            return PoolSnapshot(
                active_workers=self._state.worker_count,
                queue_depth=self._work_queue.qsize(),
                tasks_completed=self._state.tasks_completed,
                tasks_failed=self._state.tasks_failed,
            )

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> "DynamicThreadingPool":
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        self.stop()
        return False


# ---------------------------------------------------------------------------
# Worker variant that supports scale-down sentinel
# ---------------------------------------------------------------------------


def _worker_with_sentinel(
    work_queue: queue.Queue,
    stop_event: threading.Event,
    state: _PoolState,
    lock: threading.Lock,
) -> None:
    """Worker loop that also handles the ``None`` scale-down sentinel.

    When the supervisor wants to remove a worker it enqueues ``None``.  The
    first worker to dequeue it exits cleanly, reducing the active count by one.
    """
    while not stop_event.is_set():
        try:
            task = work_queue.get(timeout=_WORKER_TIMEOUT)
        except queue.Empty:
            continue

        # Scale-down sentinel — exit gracefully.
        if task is None:
            work_queue.task_done()
            break

        try:
            task()
            with lock:
                state.tasks_completed += 1
        except Exception:
            logger.exception("Worker caught unhandled exception in task")
            with lock:
                state.tasks_failed += 1
        finally:
            work_queue.task_done()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

#: Shared pool instance; call ``threading_pool.start()`` to activate.
threading_pool = DynamicThreadingPool(
    min_workers=MIN_WORKERS,
    max_workers=MAX_WORKERS,
)

__all__ = [
    "MIN_WORKERS",
    "MAX_WORKERS",
    "PoolSnapshot",
    "DynamicThreadingPool",
    "threading_pool",
]

CONTROL_SIZE = 8

class SharedMemoryRingBuffer:
    def __init__(self, name: str, size: int = 1024, slot_size: int = 128, create: bool = False):
        """
        Initializes a lock-free circular ring buffer over shared memory primitives.
        
        :param name: Unique globally identifiable shared resource string identifier.
        :param size: Total maximum item slot capacities bound to the circular matrix.
        :param slot_size: Static byte footprint allocation size reserved per tracking frame slot.
        """
        self.size = size
        self.slot_size = slot_size
        self.total_bytes = CONTROL_SIZE + (self.size * self.slot_size)
        
        if create:
            self.shm = shared_memory.SharedMemory(name=name, create=True, size=self.total_bytes)
            # Initialize control indices (Write=0, Read=0) via struct buffers
            self.shm.buf[0:CONTROL_SIZE] = struct.pack("!II", 0, 0)
            logger.info(f"SharedMemory Ring Buffer created cleanly: {name} ({self.total_bytes} bytes allocation)")
        else:
            self.shm = shared_memory.SharedMemory(name=name)
            logger.debug(f"Connected to existing shared memory allocation region: {name}")

        self.buf = self.shm.buf

    def _get_pointers(self) -> tuple[int, int]:
        """Unpacks and extracts the current (write_ptr, read_ptr) execution boundaries."""
        return struct.unpack("!II", self.buf[0:CONTROL_SIZE])

    def _set_write_pointer(self, ptr: int) -> None:
        """Sets the write pointer coordinate inside the control header region."""
        self.buf[0:4] = struct.pack("!I", ptr)

    def _set_read_pointer(self, ptr: int) -> None:
        """Sets the read pointer coordinate inside the control header region."""
        self.buf[4:8] = struct.pack("!I", ptr)

    def enqueue(self, data: bytes) -> bool:
        """
        Pushes a raw byte frame into the ring buffer without serialization overhead.
        Returns True if successful, or False if the buffer space is full.
        """
        if len(data) > self.slot_size:
            raise ValueError(f"Payload footprint ({len(data)}b) exceeds configured slot size bound ({self.slot_size}b)")

        write_ptr, read_ptr = self._get_pointers()
        
        # Lock-free Ring-Buffer Wrap Around Bound Checking
        if (write_ptr + 1) % self.size == read_ptr % self.size:
            logger.warning("SharedMemory queue overflow: Ring buffer is full. Dropping frame telemetry write.")
            return False

        # Compute exact memory offset address
        slot_index = write_ptr % self.size
        offset = CONTROL_SIZE + (slot_index * self.slot_size)
        
        # Zero out the block slot and copy raw un-pickled data bytes directly
        self.buf[offset:offset + self.slot_size] = b'\x00' * self.slot_size
        self.buf[offset:offset + len(data)] = data

        # Atomically increment write pointer position to open visibility to consumer loops
        self._set_write_pointer(write_ptr + 1)
        return True

    def dequeue(self) -> tuple[bool, bytes]:
        """
        Pulls a raw byte frame out of the ring buffer.
        Returns a tuple of (Success Status, Raw Bytes Payload).
        """
        write_ptr, read_ptr = self._get_pointers()

        # Check if the queue is empty
        if read_ptr == write_ptr:
            return False, b""

        slot_index = read_ptr % self.size
        offset = CONTROL_SIZE + (slot_index * self.slot_size)

        # Harvest the raw segment allocation slice block
        raw_payload = bytes(self.buf[offset:offset + self.slot_size]).rstrip(b'\x00')

        # Increment read tracking pointer coordinates to free up slot availability bounds
        self._set_read_pointer(read_ptr + 1)
        return True, raw_payload

    def close(self) -> None:
        """Closes access handle pipelines pointing down shared mapping zones."""
        self.shm.close()

    def unlink(self) -> None:
        """Destroys and garbage collects memory map segments from OS kernels."""
        try:
            self.shm.unlink()
            logger.info("SharedMemory segments unlinked cleanly from system cores.")
        except FileNotFoundError:
            pass
🧪 Multi-Process Integrity Testing
src/utils/__tests__/test_shared_memory.py
Python
import multiprocessing
import time
import pytest
from src.utils.threading_pool import SharedMemoryRingBuffer

SHM_TEST_CHANNEL = "shm_telemetry_test_channel"

def child_producer_worker(shm_name, count, slot_size):
    """Isolated child process script executing raw fast binary writes."""
    buffer = SharedMemoryRingBuffer(name=shm_name, size=10, slot_size=slot_size, create=False)
    for i in range(count):
        payload = f"FRAME_DATA_METRIC_{i}".encode('utf-8')
        # Busy-wait loop if enqueue transiently returns full state bounds
        while not buffer.enqueue(payload):
            time.sleep(0.001)
    buffer.close()

def test_shared_memory_zero_copy_pipeline():
    """
    Asserts lock-free high-speed interprocess communications pass raw data frames
    safely across standard process borders without pickling errors.
    """
    slot_allocation_bytes = 64
    total_broadcast_frames = 5
    
    # 1. Initialize the master SharedMemory Ring Allocation segment
    master_buffer = SharedMemoryRingBuffer(
        name=SHM_TEST_CHANNEL, 
        size=10, 
        slot_size=slot_allocation_bytes, 
        create=True
    )

    # 2. Boot up an isolated background worker process
    process = multiprocessing.Process(
        target=child_producer_worker, 
        args=(SHM_TEST_CHANNEL, total_broadcast_frames, slot_allocation_bytes)
    )
    process.start()

    # 3. Harvest incoming messages from the shared memory block
    received_payloads = []
    timeout_cutoff = time.time() + 3.0

    while len(received_payloads) < total_broadcast_frames and time.time() < timeout_cutoff:
        success, frame_bytes = master_buffer.dequeue()
        if success:
            received_payloads.append(frame_bytes.decode('utf-8'))
        else:
            time.sleep(0.001)

    # 4. Cleanup and assert state structures
    process.join(timeout=1.0)
    master_buffer.close()
    master_buffer.unlink()

    assert len(received_payloads) == total_broadcast_frames
    assert received_payloads[0] == "FRAME_DATA_METRIC_0"
    assert received_payloads[-1] == f"FRAME_DATA_METRIC_{total_broadcast_frames - 1}"