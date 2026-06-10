import unittest

from main import DEFAULT_SEND_TEXT, parse_hex_payload, parse_port


class HexPayloadTests(unittest.TestCase):
    def test_default_payload_is_binary_frame(self) -> None:
        payload = parse_hex_payload(DEFAULT_SEND_TEXT)

        self.assertEqual(len(payload), 21)
        self.assertEqual(payload[:2], b"\x4E\x57")

    def test_hex_payload_accepts_common_separators(self) -> None:
        self.assertEqual(parse_hex_payload("0x4E, 0x57;00-13"), b"\x4E\x57\x00\x13")


class NetworkPortTests(unittest.TestCase):
    def test_port_accepts_valid_range(self) -> None:
        self.assertEqual(parse_port("0", "本地端口"), 0)
        self.assertEqual(parse_port("65535", "目标端口"), 65535)

    def test_port_rejects_out_of_range(self) -> None:
        with self.assertRaises(ValueError):
            parse_port("65536", "目标端口")


if __name__ == "__main__":
    unittest.main()
