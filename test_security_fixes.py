#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_security_fixes.py — Tests for security fixes and critical functionality
===========================================================================

Tests cover:
- Thread-safe singleton patterns
- Bounded cache eviction
- JSON parsing error handling
- API key validation and masking
- Environment validation
"""

import pytest
import threading
import time
from collections import OrderedDict


class TestBoundedCache:
    """Test the bounded cache with LRU eviction."""

    def test_cache_bounded_size(self):
        """Test that cache respects maxsize limit."""
        from crew_tools import BoundedCache

        cache = BoundedCache(maxsize=3)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.set("c", 3)
        cache.set("d", 4)  # Should evict 'a'

        assert cache.get("a") is None
        assert cache.get("b") == 2
        assert cache.get("c") == 3
        assert cache.get("d") == 4

    def test_cache_lru_eviction(self):
        """Test that least recently used items are evicted."""
        from crew_tools import BoundedCache

        cache = BoundedCache(maxsize=3)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.set("c", 3)

        # Access 'a' to make it recently used
        cache.get("a")

        # Add new item - should evict 'b' (least recently used)
        cache.set("d", 4)

        assert cache.get("a") == 1
        assert cache.get("b") is None
        assert cache.get("c") == 3
        assert cache.get("d") == 4

    def test_cache_thread_safety(self):
        """Test that cache is thread-safe."""
        from crew_tools import BoundedCache

        cache = BoundedCache(maxsize=100)
        errors = []

        def writer(n):
            try:
                for i in range(100):
                    cache.set(f"{n}_{i}", i)
            except Exception as e:
                errors.append(e)

        def reader(n):
            try:
                for i in range(100):
                    cache.get(f"{n}_{i}")
            except Exception as e:
                errors.append(e)

        threads = []
        for i in range(5):
            threads.append(threading.Thread(target=writer, args=(i,)))
            threads.append(threading.Thread(target=reader, args=(i,)))

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0


class TestJSONParsing:
    """Test safe JSON parsing utility."""

    def test_parse_valid_json(self):
        """Test parsing valid JSON."""
        from trading_crew import safe_parse_json

        result = safe_parse_json('{"status": "ok", "value": 123}')
        assert result == {"status": "ok", "value": 123}

    def test_parse_json_with_surrounding_text(self):
        """Test extracting JSON from text."""
        from trading_crew import safe_parse_json

        text = 'Some text before {"status": "ok"} and after'
        result = safe_parse_json(text)
        assert result == {"status": "ok"}

    def test_parse_invalid_json_returns_fallback(self):
        """Test that invalid JSON returns fallback value."""
        from trading_crew import safe_parse_json

        result = safe_parse_json("not json at all", fallback={"error": "parse_failed"})
        assert result == {"error": "parse_failed"}

    def test_parse_empty_string(self):
        """Test parsing empty string."""
        from trading_crew import safe_parse_json

        result = safe_parse_json("", fallback=None)
        assert result is None


class TestAPIKeyValidation:
    """Test API key validation and masking."""

    def test_mask_api_key(self):
        """Test API key masking."""
        from agents import mask_api_key

        key = "sk-1234567890abcdefghij"
        masked = mask_api_key(key)
        assert masked == "sk-1...ghij"
        assert len(masked) < len(key)

    def test_mask_short_key(self):
        """Test masking short keys."""
        from agents import mask_api_key

        assert mask_api_key("short") == "***"
        assert mask_api_key("") == "***"

    def test_validate_valid_key(self):
        """Test validation of valid API key."""
        from agents import validate_api_key

        assert validate_api_key("sk-1234567890abcdefghijklmnop") is True

    def test_validate_invalid_key_wrong_prefix(self):
        """Test validation rejects wrong prefix."""
        from agents import validate_api_key

        assert validate_api_key("pk-1234567890abcdefghijklmnop") is False

    def test_validate_invalid_key_too_short(self):
        """Test validation rejects too short key."""
        from agents import validate_api_key

        assert validate_api_key("sk-short") is False

    def test_validate_empty_key(self):
        """Test validation rejects empty key."""
        from agents import validate_api_key

        assert validate_api_key("") is False
        assert validate_api_key(None) is False


class TestThreadSafeSingletons:
    """Test thread-safe singleton pattern."""

    def test_singleton_returns_same_instance(self):
        """Test that singleton returns same instance."""
        from trade_tracker import get_trade_tracker

        instance1 = get_trade_tracker()
        instance2 = get_trade_tracker()
        assert instance1 is instance2

    def test_singleton_thread_safety(self):
        """Test that singleton is thread-safe."""
        from trade_tracker import get_trade_tracker, _tracker_instance

        # Reset instance for testing
        import trade_tracker
        trade_tracker._tracker_instance = None

        instances = []

        def get_instance():
            instances.append(get_trade_tracker())

        threads = [threading.Thread(target=get_instance) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All instances should be the same object
        assert len(set(id(i) for i in instances)) == 1


class TestEnvironmentValidation:
    """Test environment variable validation."""

    def test_validate_environment_missing_openai_key(self, monkeypatch):
        """Test validation catches missing OPENAI_API_KEY."""
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        from importlib import reload
        import main
        reload(main)

        errors, warnings = main.validate_environment()
        assert any("OPENAI_API_KEY" in error for error in errors)

    def test_validate_environment_deprecated_ssl_flag(self, monkeypatch):
        """Test validation catches deprecated ALLOW_INSECURE_SSL."""
        monkeypatch.setenv("ALLOW_INSECURE_SSL", "true")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test1234567890abcdefghijklmnop")

        from importlib import reload
        import main
        reload(main)

        errors, warnings = main.validate_environment()
        assert any("ALLOW_INSECURE_SSL" in error for error in errors)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
