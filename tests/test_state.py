import os
import sys
import time
import pytest
from multiprocessing import Process

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from utils.state import StateRegister


def test_state_register_basic_operations(tmp_path) -> None:
    filepath = str(tmp_path / "test_state.json")
    register = StateRegister(filepath=filepath)

    assert not register.is_active("worker-1")
    register.activate("worker-1")
    assert register.is_active("worker-1")

    # Try acquire should fail when active
    assert not register.try_acquire("worker-1")

    register.deactivate("worker-1")
    assert not register.is_active("worker-1")

    # Try acquire should succeed when inactive
    assert register.try_acquire("worker-1")
    assert register.is_active("worker-1")

    register.clear("worker-1")
    assert not register.is_active("worker-1")


def test_state_register_snapshot_and_release(tmp_path) -> None:
    filepath = str(tmp_path / "test_state.json")
    register = StateRegister(filepath=filepath)

    register.activate("worker-a")
    register.activate("worker-b")

    snap = register.snapshot()
    assert snap.get("worker-a") is True
    assert snap.get("worker-b") is True

    register.release("worker-a")
    assert not register.is_active("worker-a")


def _worker_process_task(register: StateRegister, worker_id: str) -> None:
    # Loop and write to test concurrent stress
    for i in range(50):
        key = f"key-{worker_id}-{i}"
        register.activate(key)
        assert register.is_active(key)
        register.deactivate(key)


def test_state_register_multiprocess_safety(tmp_path) -> None:
    filepath = str(tmp_path / "test_state_multiprocess.json")
    register = StateRegister(filepath=filepath)

    processes = []
    for idx in range(4):
        p = Process(target=_worker_process_task, args=(register, f"worker-{idx}"))
        processes.append(p)
        p.start()

    for p in processes:
        p.join()
        assert p.exitcode == 0
