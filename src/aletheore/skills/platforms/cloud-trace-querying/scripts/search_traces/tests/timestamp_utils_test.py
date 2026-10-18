import unittest
from google.protobuf.timestamp_pb2 import Timestamp
import timestamp_utils


class TestTimestampUtils(unittest.TestCase):

  def test_parse_timestamp_valid(self):
    ts = timestamp_utils.parse_timestamp("2026-06-25T13:00:00Z")
    self.assertIsNotNone(ts)
    self.assertIsInstance(ts, Timestamp)
    self.assertEqual(ts.seconds, 1782392400)

  def test_parse_timestamp_invalid(self):
    with self.assertRaises(ValueError):
      timestamp_utils.parse_timestamp("invalid-date")

  def test_parse_iso8601(self):
    dt = timestamp_utils.parse_iso8601("2026-06-25T13:00:00Z")
    self.assertEqual(dt.year, 2026)
    self.assertEqual(dt.month, 6)
    self.assertEqual(dt.day, 25)
    self.assertEqual(dt.hour, 13)

  def test_to_datetime_datetime(self):
    from datetime import datetime, timezone

    dt = datetime(2026, 6, 25, 13, 0, 0)
    res = timestamp_utils.to_datetime(dt)
    self.assertEqual(res.tzinfo, timezone.utc)

    dt_tz = datetime(2026, 6, 25, 13, 0, 0, tzinfo=timezone.utc)
    res_tz = timestamp_utils.to_datetime(dt_tz)
    self.assertEqual(res_tz, dt_tz)

  def test_to_datetime_str(self):
    from datetime import datetime, timezone

    res = timestamp_utils.to_datetime("2026-06-25T13:00:00Z")
    self.assertEqual(res.year, 2026)
    self.assertEqual(res.tzinfo, timezone.utc)

  def test_to_datetime_dict(self):
    from datetime import datetime, timezone

    res = timestamp_utils.to_datetime(
        {"seconds": 1782392400, "nanos": 500000000}
    )
    self.assertEqual(res.year, 2026)
    self.assertEqual(res.second, 0)
    self.assertEqual(res.microsecond, 500000)

  def test_to_datetime_invalid(self):
    with self.assertRaises(ValueError):
      timestamp_utils.to_datetime(123456)


if __name__ == "__main__":
  unittest.main()
