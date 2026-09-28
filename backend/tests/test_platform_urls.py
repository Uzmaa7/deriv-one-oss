import os
import unittest

from platform_urls import platform_internal_base_url


class PlatformUrlsTests(unittest.TestCase):
    def test_prefers_platform_internal_base_url(self) -> None:
        with _env(
            PLATFORM_INTERNAL_BASE_URL="http://backend:8000/",
            DERIV_ONE_BASE_URL="http://legacy:9000",
            APP_BASE_URL="http://public:8080",
        ):
            self.assertEqual(platform_internal_base_url(), "http://backend:8000")

    def test_legacy_deriv_one_alias(self) -> None:
        with _env(
            DERIV_ONE_BASE_URL="http://legacy:9000/",
            APP_BASE_URL="http://public:8080",
        ):
            self.assertEqual(platform_internal_base_url(), "http://legacy:9000")

    def test_falls_back_to_app_base_url(self) -> None:
        with _env(APP_BASE_URL="http://public:8080/"):
            self.assertEqual(platform_internal_base_url(), "http://public:8080")


def _env(**values: str):
    class _Ctx:
        def __enter__(self):
            self._saved = {
                key: os.environ.get(key)
                for key in (
                    "PLATFORM_INTERNAL_BASE_URL",
                    "DERIV_ONE_BASE_URL",
                    "APP_BASE_URL",
                )
            }
            for key in self._saved:
                os.environ.pop(key, None)
            for key, value in values.items():
                os.environ[key] = value
            return self

        def __exit__(self, *args):
            for key, value in self._saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    return _Ctx()


if __name__ == "__main__":
    unittest.main()
