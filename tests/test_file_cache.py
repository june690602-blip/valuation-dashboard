from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from src.data.cache import file_cache


class FileCacheTests(unittest.TestCase):
    def test_fresh_cache_avoids_duplicate_source_calls(self):
        calls = 0

        @file_cache("fresh-test", ttl_hours=1)
        def load_value():
            nonlocal calls
            calls += 1
            return {"value": 7}

        with tempfile.TemporaryDirectory() as tmp, patch(
            "src.data.cache.CACHE_DIR", Path(tmp)
        ):
            self.assertEqual(load_value(), {"value": 7})
            self.assertEqual(load_value(), {"value": 7})

        self.assertEqual(calls, 1)

    def test_stale_cache_is_used_when_source_fails(self):
        should_fail = False

        @file_cache("stale-test", ttl_hours=1)
        def load_value():
            if should_fail:
                raise ConnectionError("source unavailable")
            return {"value": 11}

        with tempfile.TemporaryDirectory() as tmp, patch(
            "src.data.cache.CACHE_DIR", Path(tmp)
        ):
            self.assertEqual(load_value(), {"value": 11})
            cache_file = next(Path(tmp).glob("stale-test_*.json"))
            old = time.time() - 2 * 3600
            os.utime(cache_file, (old, old))
            should_fail = True

            self.assertEqual(load_value(), {"value": 11})


if __name__ == "__main__":
    unittest.main()
