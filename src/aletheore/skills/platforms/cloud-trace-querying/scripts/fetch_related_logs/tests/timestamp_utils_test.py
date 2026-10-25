import datetime
import unittest

import timestamp_utils


class TestTimestampUtils(unittest.TestCase):

  def test_parse_iso8601_with_z(self):
    res = timestamp_utils.parse_iso8601("2026-06-25T13:00:00Z")
    expected = datetime.datetime(
        2026, 6, 25, 13, 0, 0, tzinfo=datetime.timezone.utc
    )
    self.assertEqual(res, expected)

  def test_parse_iso8601_with_offset(self):
    res = timestamp_utils.parse_iso8601("2026-06-25T13:00:00+02:00")
    tz = datetime.timezone(datetime.timedelta(hours=2))
    expected = datetime.datetime(2026, 6, 25, 13, 0, 0, tzinfo=tz)
    self.assertEqual(res, expected)

  def test_validate_iso8601_valid(self):
    res = timestamp_utils.validate_iso8601("2026-06-25T13:00:00Z")
    self.assertEqual(res, "2026-06-25T13:00:00Z")

  def test_validate_iso8601_invalid(self):
    with self.assertRaises(ValueError):
      timestamp_utils.validate_iso8601("invalid-time")

  def test_validate_iso8601_empty(self):
    self.assertIsNone(timestamp_utils.validate_iso8601(None))
    self.assertIsNone(timestamp_utils.validate_iso8601(""))


if __name__ == "__main__":
  unittest.main()
