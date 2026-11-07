import unittest
from tunnel import build_tun_config


class TunnelConfigTests(unittest.TestCase):
    def test_vless_tun_config(self):
        link = (
            "vless://11111111-1111-4111-8111-111111111111@example.com:443"
            "?security=tls&type=tcp&sni=example.com&fp=chrome#test"
        )
        config = build_tun_config(link)
        self.assertEqual(config["inbounds"][0]["type"], "tun")
        self.assertTrue(config["inbounds"][0]["auto_route"])
        self.assertTrue(config["inbounds"][0]["strict_route"])
        self.assertEqual(config["route"]["final"], "proxy")
        self.assertEqual(config["outbounds"][0]["type"], "vless")

    def test_rejects_invalid_profile(self):
        with self.assertRaises(ValueError):
            build_tun_config("not-a-proxy-profile")


if __name__ == "__main__":
    unittest.main()
