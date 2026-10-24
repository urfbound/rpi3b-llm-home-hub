from threading import Event

import pytest

from homehub.chat import ChatQueueFull, ChatRequestQueue


def test_chat_queue_runs_one_request_and_prioritizes_admins_with_fifo_ties():
    first_started = Event()
    release_first = Event()
    all_finished = Event()
    started = []

    def process(job):
        started.append(job['id'])
        if job['id'] == 'first-user':
            first_started.set()
            assert release_first.wait(2)
        if len(started) == 4:
            all_finished.set()

    queue = ChatRequestQueue(process)
    queue.enqueue({'id': 'first-user', 'is_admin': False}, max_waiting=3)
    assert first_started.wait(2)
    queue.enqueue({'id': 'user-1', 'is_admin': False}, max_waiting=3)
    queue.enqueue({'id': 'admin-1', 'is_admin': True}, max_waiting=3)
    queue.enqueue({'id': 'admin-2', 'is_admin': True}, max_waiting=3)

    assert queue.position('admin-1') == 1
    assert queue.position('admin-2') == 2
    assert queue.position('user-1') == 3
    release_first.set()

    assert all_finished.wait(2)
    assert queue.wait_idle(2)
    assert started == ['first-user', 'admin-1', 'admin-2', 'user-1']


def test_chat_queue_rejects_requests_when_configured_waiting_depth_is_full():
    started = Event()
    release = Event()

    def process(job):
        if job['id'] == 'running':
            started.set()
            assert release.wait(2)

    queue = ChatRequestQueue(process)
    queue.enqueue({'id': 'running', 'is_admin': False}, max_waiting=1)
    assert started.wait(2)
    queue.enqueue({'id': 'waiting', 'is_admin': False}, max_waiting=1)

    with pytest.raises(ChatQueueFull):
        queue.enqueue({'id': 'rejected', 'is_admin': True}, max_waiting=1)

    release.set()
    assert queue.wait_idle(2)


def test_failed_chat_request_is_reported_without_killing_queue_worker():
    started = []
    first_started = Event()

    def process(job):
        started.append(job['id'])
        if job['id'] == 'bad':
            first_started.set()
        if job['id'] == 'bad':
            raise RuntimeError('model failed')

    queue = ChatRequestQueue(process)
    queue.enqueue({'id': 'bad', 'is_admin': False}, max_waiting=1)
    assert first_started.wait(2)
    queue.enqueue({'id': 'good', 'is_admin': False}, max_waiting=1)

    assert queue.wait_idle(2)
    assert started == ['bad', 'good']
    assert queue.status('bad')['status'] == 'failed'
    assert queue.status('good')['status'] == 'complete'
