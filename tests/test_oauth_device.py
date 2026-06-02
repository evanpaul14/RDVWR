"""Unit tests for the _OAuthDevice class and related helpers in reddit_client.py."""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import time
import uuid
import pytest
from unittest.mock import patch, MagicMock

import reddit_client as app_module
from reddit_client import _OAuthDevice, _TOKEN_ROTATE_SECS, _CFFI_PROFILES, _ANDROID_APP_VERSIONS


class TestOAuthDeviceInit:
    def test_token_is_none(self):
        d = _OAuthDevice()
        assert d.token is None

    def test_expires_at_zero(self):
        d = _OAuthDevice()
        assert d.expires_at == 0.0

    def test_device_id_is_valid_uuid(self):
        d = _OAuthDevice()
        uuid.UUID(d.device_id)  # raises ValueError if invalid

    def test_impersonate_is_known_profile(self):
        d = _OAuthDevice()
        assert d.impersonate in _CFFI_PROFILES

    def test_qos_in_range(self):
        d = _OAuthDevice()
        assert 1.0 <= d.qos <= 100.0

    def test_user_agent_contains_reddit(self):
        d = _OAuthDevice()
        assert "Reddit/" in d.user_agent

    def test_user_agent_contains_android(self):
        d = _OAuthDevice()
        assert "Android" in d.user_agent

    def test_extra_is_empty_dict(self):
        d = _OAuthDevice()
        assert d.extra == {}


class TestNeedsRefresh:
    def test_true_when_no_token(self):
        d = _OAuthDevice()
        assert d.needs_refresh() is True

    def test_true_when_token_expired(self):
        d = _OAuthDevice()
        d.token = "tok"
        d.expires_at = time.time() - 1
        d.acquired_at = time.time()
        assert d.needs_refresh() is True

    def test_true_when_rotation_time_exceeded(self):
        d = _OAuthDevice()
        d.token = "tok"
        d.expires_at = time.time() + 3600
        d.acquired_at = time.time() - _TOKEN_ROTATE_SECS - 1
        assert d.needs_refresh() is True

    def test_false_when_fresh(self):
        d = _OAuthDevice()
        d.token = "tok"
        d.expires_at = time.time() + 3600
        d.acquired_at = time.time()
        assert d.needs_refresh() is False


class TestApiHeaders:
    def _fresh_device(self):
        d = _OAuthDevice()
        d.token = "mytoken"
        return d

    def test_authorization_header_present(self):
        d = self._fresh_device()
        h = d.api_headers()
        assert h["Authorization"] == "Bearer mytoken"

    def test_no_auth_header_when_no_token(self):
        d = _OAuthDevice()
        h = d.api_headers()
        assert h["Authorization"] == ""

    def test_device_id_consistent_across_calls(self):
        d = self._fresh_device()
        h1 = d.api_headers()
        h2 = d.api_headers()
        assert h1["X-Reddit-Device-Id"] == h2["X-Reddit-Device-Id"]

    def test_vendor_id_matches_device_id(self):
        d = self._fresh_device()
        h = d.api_headers()
        assert h["client-vendor-id"] == h["X-Reddit-Device-Id"]

    def test_extra_headers_included(self):
        d = self._fresh_device()
        d.extra = {"x-reddit-loid": "loid123"}
        h = d.api_headers()
        assert h["x-reddit-loid"] == "loid123"

    def test_codecs_header_always_present(self):
        d = self._fresh_device()
        h = d.api_headers()
        assert "x-reddit-media-codecs" in h

    def test_returns_dict(self):
        d = self._fresh_device()
        assert isinstance(d.api_headers(), dict)


class TestDriftQos:
    def test_stays_in_bounds(self):
        d = _OAuthDevice()
        for _ in range(200):
            d.drift_qos()
        assert 1.0 <= d.qos <= 100.0


class TestResetIdentity:
    def test_device_id_changes(self):
        d = _OAuthDevice()
        old_id = d.device_id
        d.reset_identity()
        assert d.device_id != old_id

    def test_new_device_id_valid_uuid(self):
        d = _OAuthDevice()
        d.reset_identity()
        uuid.UUID(d.device_id)

    def test_impersonate_stays_valid(self):
        d = _OAuthDevice()
        d.reset_identity()
        assert d.impersonate in _CFFI_PROFILES

    def test_user_agent_updated(self):
        d = _OAuthDevice()
        old_ua = d.user_agent
        # reset_identity may pick the same version by chance, so check format
        d.reset_identity()
        assert "Reddit/" in d.user_agent
        assert "Android" in d.user_agent

    def test_qos_reset_in_range(self):
        d = _OAuthDevice()
        d.qos = 1.0  # force a low value
        d.reset_identity()
        assert 1.0 <= d.qos <= 100.0
