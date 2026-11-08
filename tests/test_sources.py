import base64
import unittest

from multi_tester import _extract_proxy_links


class SourceExtractionTests(unittest.TestCase):
    def test_extracts_plain_links(self):
        payload = "vless://id@example.org:443?security=tls#node"
        self.assertEqual(_extract_proxy_links(payload), [payload])

    def test_decodes_base64_subscription(self):
        payload = "vless://id@example.org:443?security=tls#node\\n"
        encoded = base64.b64encode(payload.encode("utf-8")).decode("ascii")
        self.assertEqual(_extract_proxy_links(encoded), [payload.strip()])


if __name__ == "__main__":
    unittest.main()
