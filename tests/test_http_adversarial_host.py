"""HOST-ONLY tests of the raw-wire adversarial harness; no AttoLang guest runs."""
import unittest
from unittest.mock import patch

from http_adversarial import cases, deny, response_statuses, wire


class HarnessTests(unittest.TestCase):
    def test_every_case_is_unique_and_bounded(self):
        items = list(cases())
        names = [name for name, _ in items]
        self.assertEqual(len(names), len(set(names)))
        self.assertGreaterEqual(len(items), 12)
        for name, payload in items:
            with self.subTest(name=name):
                self.assertIsInstance(payload, bytes)
                self.assertLess(len(payload), 70000)
                self.assertTrue(payload)

    def test_wire_has_explicit_close_and_loopback(self):
        data = wire(b"Content-Length: 1\r\n", b"x")
        self.assertIn(b"Host: 127.0.0.1\r\n", data)
        self.assertIn(b"Connection: close\r\n", data)
        self.assertTrue(data.endswith(b"\r\nx"))

    def test_status_parser_finds_smuggled_success(self):
        data = (b"HTTP/1.1 400 Bad Request\r\n\r\n"
                b"HTTP/1.1 200 OK\r\n\r\n")
        self.assertEqual(response_statuses(data), [400, 200])
        with patch("http_adversarial.transmit", return_value=(data, True)):
            with self.assertRaisesRegex(AssertionError, "unexpected status"):
                deny(1234, "smuggling", b"bad")

    def test_capacity_rejection_cannot_pass(self):
        with patch("http_adversarial.transmit", return_value=(b"HTTP/1.1 503 Busy\r\n", True)) as mock:
            with self.assertRaisesRegex(AssertionError, "no parser evidence"):
                deny(1234, "overload", b"bad")
        self.assertEqual(mock.call_count, 5)

    def test_transport_close_without_reply_can_pass(self):
        with patch("http_adversarial.transmit", return_value=(b"", True)):
            self.assertEqual(deny(1234, "close", b"bad"), ["closed"])

    def test_hang_without_reply_fails(self):
        with patch("http_adversarial.transmit", return_value=(b"", False)):
            with self.assertRaisesRegex(AssertionError, "neither rejected nor closed"):
                deny(1234, "hung", b"bad")

    def test_http_500_not_a_parser_rejection(self):
        with patch("http_adversarial.transmit", return_value=(b"HTTP/1.1 500 Internal Server Error\r\n", True)):
            with self.assertRaisesRegex(AssertionError, "unexpected status"):
                deny(1234, "panic", b"bad")


if __name__ == "__main__":
    unittest.main()
