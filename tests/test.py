"""
Smoke test — package is importable.
"""

import opencode_harness


def test_package_is_importable():
    assert opencode_harness is not None
