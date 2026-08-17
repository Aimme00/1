from __future__ import annotations

import os
import json
import unittest
from pathlib import Path


class OfflinePreviewContractTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.base_dir = Path(__file__).resolve().parents[1]
        cls.html = (cls.base_dir / "web" / "index.html").read_text(encoding="utf-8")
        cls.preview = (cls.base_dir / "web" / "preview-bootstrap.js").read_text(
            encoding="utf-8"
        )

    def test_page_uses_file_compatible_relative_assets(self) -> None:
        for asset in ("styles.css", "preview-bootstrap.js", "app.js", "downloads.js"):
            self.assertIn(f'./{asset}', self.html)
            self.assertTrue((self.base_dir / "web" / asset).is_file())

    def test_mock_only_activates_for_direct_file_opening(self) -> None:
        self.assertIn("window.location.protocol !== 'file:'", self.preview)
        self.assertIn("window.fetch = async", self.preview)
        self.assertIn("window.EventSource = PreviewEventSource", self.preview)

    def test_chart_is_optional_and_checkbox_choice_is_honoured(self) -> None:
        self.assertIn("body.generate_chart === true", self.preview)
        self.assertIn("chartRequested ||", self.preview)

    def test_macos_launcher_exists_and_is_executable(self) -> None:
        launcher = self.base_dir / "打开AskData离线演示.command"
        self.assertTrue(launcher.is_file())
        self.assertTrue(os.access(launcher, os.X_OK))
        self.assertIn('open "$PREVIEW_FILE"', launcher.read_text(encoding="utf-8"))

    def test_cloudflare_pages_function_contract_is_present(self) -> None:
        worker = self.base_dir / "functions" / "api" / "[[path]].js"
        routes = json.loads((self.base_dir / "web" / "_routes.json").read_text(encoding="utf-8"))
        config = json.loads((self.base_dir / "wrangler.jsonc").read_text(encoding="utf-8"))
        self.assertTrue(worker.is_file())
        self.assertEqual(routes["include"], ["/api/*"])
        self.assertEqual(config["pages_build_output_dir"], "./web")
        self.assertEqual(config["d1_databases"][0]["binding"], "DB")
        self.assertEqual(config["vars"]["ASKDATA_GUEST_QUERY_LIMIT"], "2")

    def test_cloudflare_worker_keeps_chart_generation_optional(self) -> None:
        worker = (self.base_dir / "functions" / "api" / "[[path]].js").read_text(
            encoding="utf-8"
        )
        self.assertIn("CHART_INTENT.test(query)", worker)
        self.assertIn("generateChart === true", worker)
        self.assertIn("if (!(requested || CHART_INTENT.test(query))", worker)

    def test_public_demo_uses_guest_session_without_legacy_quota_ui(self) -> None:
        worker = (self.base_dir / "functions" / "api" / "[[path]].js").read_text(
            encoding="utf-8"
        )
        javascript = (self.base_dir / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn("env.ASKDATA_GUEST_QUERY_LIMIT || 2", worker)
        self.assertIn("const usageBucket = 'lifetime'", worker)
        self.assertIn("request.headers.get('CF-Connecting-IP')", worker)
        self.assertIn("crypto.subtle.sign('HMAC'", worker)
        self.assertIn('/api/auth/guest', javascript)
        self.assertNotIn("showQuota(created.quota)", javascript)
        self.assertNotIn('id="quotaHint"', self.html)


if __name__ == "__main__":
    unittest.main()
