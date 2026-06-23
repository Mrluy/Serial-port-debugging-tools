import unittest

from main import (
    APP_VERSION,
    append_crc16_modbus_if_missing,
    app_config_path,
    calculate_crc16_modbus,
    config_bool,
    parse_hex_payload,
    parse_port,
)


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


class VersionTests(unittest.TestCase):
    def test_app_version_uses_semantic_version_format(self) -> None:
        parts = APP_VERSION.split(".")
        self.assertEqual(len(parts), 3)
        self.assertTrue(all(part.isdigit() for part in parts))


class ConfigTests(unittest.TestCase):
    def test_app_config_path_uses_appdata_directory(self) -> None:
        self.assertEqual(
            str(app_config_path(r"C:\Users\Tester\AppData\Roaming")),
            r"C:\Users\Tester\AppData\Roaming\Serial-port-debugging-tools\config.json",
        )

    def test_config_bool_accepts_common_string_values(self) -> None:
        self.assertTrue(config_bool("true"))
        self.assertTrue(config_bool("1"))
        self.assertFalse(config_bool("false", True))
        self.assertFalse(config_bool("0", True))


if __name__ == "__main__":
    unittest.main()
