from __future__ import annotations

import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.auth import (
    AuthConfig,
    AuthService,
    AuthenticationError,
    AuthenticationRateLimitError,
    InvalidBootstrapUserError,
)


class AuthenticationServiceTestCase(unittest.TestCase):
    def make_service(self, temp_dir: str, **overrides) -> AuthService:
        values = {
            "db_path": Path(temp_dir) / "auth.db",
            "pbkdf2_iterations": 1_000,
            "bootstrap_email": "analyst@example.com",
            "bootstrap_password": "safe-password",
            "bootstrap_display_name": "Data Analyst",
        }
        values.update(overrides)
        return AuthService(AuthConfig(**values))

    def test_password_is_hashed_and_session_can_be_revoked(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = self.make_service(temp_dir)
            user, token = service.login(
                email="ANALYST@example.com",
                password="safe-password",
                source="test-client",
            )
            self.assertTrue(user.is_admin)
            self.assertNotIn("safe-password", token)
            self.assertEqual(service.get_user_for_token(token), user)
            with sqlite3.connect(Path(temp_dir) / "auth.db") as conn:
                stored_hash, stored_salt = conn.execute(
                    "SELECT password_hash, password_salt FROM auth_users WHERE id = ?",
                    (user.id,),
                ).fetchone()
            self.assertNotEqual(stored_hash, b"safe-password")
            self.assertGreaterEqual(len(stored_salt), 16)
            service.logout(token)
            self.assertIsNone(service.get_user_for_token(token))

    def test_failed_logins_are_rate_limited_per_source_and_email(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = self.make_service(temp_dir, max_failed_attempts=2)
            for _ in range(2):
                with self.assertRaises(AuthenticationError):
                    service.login(
                        email="analyst@example.com",
                        password="wrong-password",
                        source="same-client",
                    )
            with self.assertRaises(AuthenticationRateLimitError):
                service.login(
                    email="analyst@example.com",
                    password="safe-password",
                    source="same-client",
                )
            user, _ = service.login(
                email="analyst@example.com",
                password="safe-password",
                source="different-client",
            )
            self.assertEqual(user.email, "analyst@example.com")

    def test_bootstrap_password_rotation_preserves_user_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            first = self.make_service(temp_dir)
            original, _ = first.login(
                email="analyst@example.com",
                password="safe-password",
            )
            rotated = self.make_service(
                temp_dir,
                bootstrap_password="new-safe-password",
                bootstrap_display_name="Lead Analyst",
            )
            with self.assertRaises(AuthenticationError):
                rotated.login(
                    email="analyst@example.com",
                    password="safe-password",
                    source="old-password",
                )
            updated, _ = rotated.login(
                email="analyst@example.com",
                password="new-safe-password",
                source="new-password",
            )
            self.assertEqual(updated.id, original.id)
            self.assertEqual(updated.display_name, "Lead Analyst")

    def test_expired_session_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = self.make_service(temp_dir)
            _, token = service.login(
                email="analyst@example.com",
                password="safe-password",
            )
            with sqlite3.connect(Path(temp_dir) / "auth.db") as conn:
                conn.execute(
                    "UPDATE auth_sessions SET expires_at = ?",
                    (int(time.time()) - 1,),
                )
            self.assertIsNone(service.get_user_for_token(token))

    def test_production_environment_has_no_default_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            "os.environ",
            {"ASKDATA_ENV": "production"},
            clear=True,
        ):
            config = AuthConfig.from_environment(temp_dir)
        self.assertEqual(config.bootstrap_email, "")
        self.assertEqual(config.bootstrap_password, "")
        self.assertTrue(config.cookie_secure)

    def test_production_first_start_fails_closed_without_admin(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(InvalidBootstrapUserError):
                AuthService(
                    AuthConfig(
                        db_path=Path(temp_dir) / "auth.db",
                        environment="production",
                        pbkdf2_iterations=1_000,
                        cookie_secure=True,
                    )
                )

    def test_production_rejects_demo_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(InvalidBootstrapUserError):
                AuthService(
                    AuthConfig(
                        db_path=Path(temp_dir) / "auth.db",
                        environment="production",
                        pbkdf2_iterations=1_000,
                        cookie_secure=True,
                        bootstrap_email="demo@askdata.local",
                        bootstrap_password="askdata-demo",
                    )
                )

    def test_production_rejects_insecure_cookie(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(InvalidBootstrapUserError):
                AuthService(
                    AuthConfig(
                        db_path=Path(temp_dir) / "auth.db",
                        environment="production",
                        pbkdf2_iterations=1_000,
                        cookie_secure=False,
                        bootstrap_email="admin@example.com",
                        bootstrap_password="strong-password",
                    )
                )


class AuthenticatedFrontendContractTestCase(unittest.TestCase):
    def test_public_mvp_uses_guest_session_identity_instead_of_user_id(self) -> None:
        project_dir = Path(__file__).resolve().parents[1]
        javascript = (project_dir / "web" / "app.js").read_text(encoding="utf-8")
        html = (project_dir / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn("/api/auth/guest", javascript)
        self.assertIn("/api/auth/me", javascript)
        self.assertNotIn("user_id", javascript)
        self.assertNotIn('id="loginForm"', html)
        self.assertNotIn('id="logoutButton"', html)


if __name__ == "__main__":
    unittest.main()
