"""
Tests for telegram_notify.py — ensure notifications are formatted correctly
and fail gracefully when not configured.
"""
import pytest
from unittest.mock import patch, MagicMock

from telegram_notify import (
    send_telegram,
    notify_bill_parsed,
    notify_gmail_poll,
)


class TestSendTelegram:
    """Test the base send_telegram function."""

    def test_noop_when_not_configured(self, monkeypatch):
        """Should silently do nothing if env vars are missing."""
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
        # Should not raise
        send_telegram("test message")

    @patch("telegram_notify.httpx.post")
    def test_sends_when_configured(self, mock_post, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
        mock_post.return_value = MagicMock(status_code=200)

        send_telegram("test message")

        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        assert "fake-token" in call_kwargs[0][0]  # URL contains token
        assert call_kwargs[1]["json"]["chat_id"] == "12345"
        assert call_kwargs[1]["json"]["text"] == "test message"

    @patch("telegram_notify.httpx.post")
    def test_handles_api_error_gracefully(self, mock_post, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
        mock_post.side_effect = Exception("Network error")

        # Should not raise
        send_telegram("test message")


class TestNotifyBillParsed:
    """Test formatted bill notification."""

    @patch("telegram_notify.send_telegram")
    def test_formats_bill_notification(self, mock_send):
        result = {
            "vendor_category": "electricity",
            "total_amount": 45.67,
            "currency": "EUR",
            "invoice_date": "2026-03-15",
        }
        notify_bill_parsed(result, "2026-03_electricity_45.67EUR.pdf")

        mock_send.assert_called_once()
        msg = mock_send.call_args[0][0]
        assert "Alexela" in msg
        assert "45.67" in msg
        assert "2026-03-15" in msg

    @patch("telegram_notify.send_telegram")
    def test_handles_missing_fields(self, mock_send):
        result = {
            "vendor_category": "unknown",
            "total_amount": None,
            "currency": None,
            "invoice_date": None,
        }
        notify_bill_parsed(result, "unknown.pdf")
        mock_send.assert_called_once()


class TestNotifyGmailPoll:
    """Test Gmail poll summary notification."""

    @patch("telegram_notify.send_telegram")
    def test_no_notification_when_zero_new(self, mock_send):
        notify_gmail_poll(new_count=0, total_count=10)
        mock_send.assert_not_called()

    @patch("telegram_notify.send_telegram")
    def test_sends_when_new_bills(self, mock_send):
        notify_gmail_poll(new_count=3, total_count=10)
        mock_send.assert_called_once()
        msg = mock_send.call_args[0][0]
        assert "3" in msg
