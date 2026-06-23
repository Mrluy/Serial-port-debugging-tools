import unittest

from main import append_crc16_modbus_if_missing, calculate_crc16_modbus, parse_hex_payload, parse_port


class HexPayloadTests(unittest.TestCase):
    def test_hex_payload_accepts_common_separators(self) -> None:
        self.assertEqual(parse_hex_payload("0x4E, 0x57;00-13"), b"\x4E\x57\x00\x13")


class CrcTests(unittest.TestCase):
    def test_crc16_modbus_known_value(self) -> None:
        payload = b"\x01\x03\x00\x00\x00\x0A"
        self.assertEqual(calculate_crc16_modbus(payload), 0xCDC5)

    def test_crc16_modbus_is_appended_little_endian(self) -> None:
        payload, appended = append_crc16_modbus_if_missing(b"\x01\x03\x00\x00\x00\x0A")
        self.assertTrue(appended)
        self.assertEqual(payload, b"\x01\x03\x00\x00\x00\x0A\xC5\xCD")

    def test_crc16_modbus_is_not_appended_twice(self) -> None:
        original = b"\x01\x03\x00\x00\x00\x0A\xC5\xCD"
        payload, appended = append_crc16_modbus_if_missing(original)
        self.assertFalse(appended)
        self.assertEqual(payload, original)


class NetworkPortTests(unittest.TestCase):
    def test_port_accepts_valid_range(self) -> None:
        self.assertEqual(parse_port("0", "本地端口"), 0)
        self.assertEqual(parse_port("65535", "目标端口"), 65535)

    def test_port_rejects_out_of_range(self) -> None:
        with self.assertRaises(ValueError):
            parse_port("65536", "目标端口")


if __name__ == "__main__":
    unittest.main()
