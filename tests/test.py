"""
Smoke test — package is importable.
"""

import opencode_runtime


def test_package_is_importable():
    assert opencode_runtime is not None
