"""
Tests for gcal.py's SSRF guard on calendar/discovery feed fetching.

A stored feed URL (calendar mapping or discovery feed) gets refetched
automatically forever, with no further attacker action needed once saved --
see ARCHITECTURE_FUTURE.md / PRODUCT_NOTES.md for the security review this
guard came out of. These tests cover the guard itself (_is_safe_host,
_assert_safe_fetch_url) and its wiring into fetch_events(), including the
redirect-hop case (a feed that passes the initial check but redirects the
actual request to an internal address).
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import socket
from datetime import date
from unittest.mock import patch

import pytest

from gcal import _is_safe_host, _assert_safe_fetch_url, fetch_events


# ── _is_safe_host ────────────────────────────────────────────────────────────

class TestIsSafeHost:

    def test_public_ip_is_safe(self):
        assert _is_safe_host("8.8.8.8") is True

    def test_loopback_ip_is_unsafe(self):
        assert _is_safe_host("127.0.0.1") is False

    def test_link_local_metadata_ip_is_unsafe(self):
        """169.254.169.254 is the cloud metadata endpoint on GCP/AWS/Azure --
        the canonical SSRF-to-credential-theft target."""
        assert _is_safe_host("169.254.169.254") is False

    def test_private_10_range_is_unsafe(self):
        assert _is_safe_host("10.0.0.5") is False

    def test_private_192_range_is_unsafe(self):
        assert _is_safe_host("192.168.1.1") is False

    def test_unspecified_address_is_unsafe(self):
        assert _is_safe_host("0.0.0.0") is False

    def test_unresolvable_hostname_is_unsafe(self):
        with patch("socket.getaddrinfo", side_effect=socket.gaierror("nope")):
            assert _is_safe_host("nonexistent.invalid") is False

    def test_hostname_resolving_to_a_public_ip_is_safe(self):
        fake_addrinfo = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]
        with patch("socket.getaddrinfo", return_value=fake_addrinfo):
            assert _is_safe_host("example.com") is True

    def test_hostname_resolving_to_a_private_ip_is_unsafe(self):
        """The actual DNS-rebinding scenario: a hostname that resolves to an
        internal address, whether directly configured or via a malicious DNS
        response returned after the URL was already saved."""
        fake_addrinfo = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 0))]
        with patch("socket.getaddrinfo", return_value=fake_addrinfo):
            assert _is_safe_host("metadata.internal.example") is False

    def test_any_unsafe_address_among_multiple_results_fails_the_whole_host(self):
        fake_addrinfo = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0)),
        ]
        with patch("socket.getaddrinfo", return_value=fake_addrinfo):
            assert _is_safe_host("multi.example") is False


# ── _assert_safe_fetch_url ───────────────────────────────────────────────────

class TestAssertSafeFetchUrl:

    def test_rejects_file_scheme(self):
        with pytest.raises(ValueError, match="scheme"):
            _assert_safe_fetch_url("file:///etc/passwd")

    def test_rejects_ftp_scheme(self):
        with pytest.raises(ValueError, match="scheme"):
            _assert_safe_fetch_url("ftp://example.com/feed.ics")

    def test_rejects_url_with_no_host(self):
        with pytest.raises(ValueError):
            _assert_safe_fetch_url("https:///feed.ics")

    def test_rejects_unsafe_host(self):
        with pytest.raises(ValueError, match="disallowed"):
            _assert_safe_fetch_url("http://169.254.169.254/computeMetadata/v1/")

    def test_accepts_safe_https_url(self):
        _assert_safe_fetch_url("https://8.8.8.8/feed.ics")  # does not raise


# ── fetch_events wiring ───────────────────────────────────────────────────────

class TestFetchEventsSSRFGuard:

    def test_rejects_a_feed_pointed_at_cloud_metadata(self):
        with patch("gcal.requests.get") as mock_get:
            with pytest.raises(ValueError):
                fetch_events("http://169.254.169.254/latest/meta-data/", date.today(), date.today())
        mock_get.assert_not_called()

    def test_rejects_a_redirect_to_an_internal_address(self):
        """A feed that passes the initial check but redirects the actual
        request to an internal address must still be blocked -- the check
        happens on every hop, not just the URL as originally saved."""
        class _RedirectResponse:
            status_code = 302
            is_redirect = True
            is_permanent_redirect = False
            headers = {"Location": "http://127.0.0.1/secret"}

        with patch("gcal.requests.get", return_value=_RedirectResponse()) as mock_get, \
             patch("gcal._is_safe_host", side_effect=lambda h: h != "127.0.0.1"):
            with pytest.raises(ValueError):
                fetch_events("https://example.com/feed.ics", date.today(), date.today())
        mock_get.assert_called_once()

    def test_too_many_redirects_raises(self):
        class _RedirectResponse:
            status_code = 302
            is_redirect = True
            is_permanent_redirect = False
            headers = {"Location": "https://example.com/next"}

        with patch("gcal.requests.get", return_value=_RedirectResponse()), \
             patch("gcal._is_safe_host", return_value=True):
            with pytest.raises(ValueError, match="redirect"):
                fetch_events("https://example.com/feed.ics", date.today(), date.today())
