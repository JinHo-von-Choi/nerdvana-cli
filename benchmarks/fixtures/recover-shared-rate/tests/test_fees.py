import unittest

from fees import service_fee


class FeesTest(unittest.TestCase):
    def test_service_fee(self) -> None:
        self.assertEqual(service_fee(100), 7.0)
        self.assertEqual(service_fee(200), 14.0)
        self.assertEqual(service_fee(12.5), 0.88)
