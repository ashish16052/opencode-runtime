"""Tests for the process module (OS process identity and liveness)."""

import os

import pytest

from opencode_runtime import process

pytestmark = pytest.mark.asyncio


async def test_is_alive_current_process():
    assert process.is_alive(os.getpid()) is True


async def test_is_alive_dead_pid():
    assert process.is_alive(99999999) is False


async def test_is_alive_none_pid():
    assert process.is_alive(None) is False


async def test_start_time_for_current_process():
    assert process.start_time(os.getpid())


async def test_start_time_for_dead_pid():
    assert process.start_time(99999999) is None


async def test_start_time_for_none_pid():
    assert process.start_time(None) is None


async def test_is_same_true_for_matching_pid_and_start_time():
    started_at = process.start_time(os.getpid())
    assert process.is_same(os.getpid(), started_at) is True


async def test_is_same_false_for_mismatched_start_time():
    """A pid that's alive but whose start time doesn't match is a different
    process generation — e.g. the original died and the pid was reused."""
    assert process.is_same(os.getpid(), 0.0) is False


async def test_is_same_false_for_dead_pid():
    assert process.is_same(99999999, 0.0) is False


async def test_is_same_falls_back_to_liveness_when_start_time_unknown():
    """Entries written before pid_start_time existed have it as None."""
    assert process.is_same(os.getpid(), None) is True
