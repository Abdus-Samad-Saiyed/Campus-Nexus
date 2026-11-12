"""Stage 1 tests: Secrets, configuration, and deployment safety.

Validates:
- campus/settings/secrets_local.py isolation and local-only use.
- campus/settings/secrets_local.example.py contains placeholders only.
- Production settings fail fast on missing SECRET_KEY, DATABASE_URL, REDIS_URL, ALLOWED_HOSTS.
- Production settings refuse SQLite and refuse importing secrets_local.py.
- Git and Docker ignore rules exclude secret files while preserving .env.example and secrets_local.example.py.
- Email sender identity is preserved (noreply.campus.nexus@gmail.com).
"""

import importlib
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class SecretsLocalIsolationTest(unittest.TestCase):
    """Verify secrets_local.py is local-only and example file has no real credentials."""

    def test_example_secrets_file_exists_and_contains_only_placeholders(self):
        example_path = PROJECT_ROOT / 'campus' / 'settings' / 'secrets_local.example.py'
        self.assertTrue(example_path.exists(), "secrets_local.example.py must be tracked in the repository")
        content = example_path.read_text(encoding='utf-8')
        # Must not contain live credentials
        env_pwd = os.environ.get('EMAIL_HOST_PASSWORD')
        if env_pwd:
            self.assertNotIn(env_pwd, content)

        self.assertIn('your-google-app-password-here', content)

    def test_git_ignores_secrets_local_and_env(self):
        """git check-ignore must confirm sensitive files are excluded."""
        sensitive_files = [
            'campus/settings/secrets_local.py',
            '.env',
            '.env.local',
            '.env.production',
            'CAMPUS_MAIL_CREDENTIALS.md',
            'DEBUG_CREDENTIALS.md',
            'db.sqlite3',
        ]
        for rel_path in sensitive_files:
            res = subprocess.run(
                ['git', 'check-ignore', rel_path],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True
            )
            self.assertEqual(
                res.returncode, 0,
                f"File {rel_path} must be ignored by .gitignore, but git check-ignore returned {res.returncode}"
            )

    def test_git_does_not_ignore_examples(self):
        """Example files must remain tracked and not ignored."""
        tracked_examples = [
            'campus/settings/secrets_local.example.py',
            '.env.example',
            '.env.docker.example',
        ]
        for rel_path in tracked_examples:
            res = subprocess.run(
                ['git', 'check-ignore', rel_path],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True
            )
            self.assertNotEqual(
                res.returncode, 0,
                f"Example file {rel_path} should be tracked, but git check-ignore ignored it"
            )

    def test_dockerignore_excludes_secrets(self):
        """dockerignore file must explicitly exclude secrets_local.py and .env files."""
        dockerignore_path = PROJECT_ROOT / '.dockerignore'
        self.assertTrue(dockerignore_path.exists(), ".dockerignore must exist")
        content = dockerignore_path.read_text(encoding='utf-8')
        self.assertIn('campus/settings/secrets_local.py', content)
        self.assertIn('.env', content)
        self.assertIn('*.sqlite3', content)


class ProductionSettingsFailSafeTest(unittest.TestCase):
    """Production settings must fail fast when required environment variables are missing."""

    def setUp(self):
        # Save original sys.modules to restore after test
        self.orig_modules = sys.modules.copy()

    def tearDown(self):
        # Remove any imported prod settings from sys.modules
        for mod in list(sys.modules.keys()):
            if mod.startswith('campus.settings.prod'):
                del sys.modules[mod]

    def _import_prod(self, env_overrides):
        with patch.dict(os.environ, env_overrides, clear=True):
            if 'campus.settings.prod' in sys.modules:
                del sys.modules['campus.settings.prod']
            orig_secret_mod = sys.modules.pop('campus.settings.secrets_local', None)
            try:
                return importlib.import_module('campus.settings.prod')
            finally:
                if orig_secret_mod is not None:
                    sys.modules['campus.settings.secrets_local'] = orig_secret_mod

    def test_prod_fails_on_missing_secret_key(self):
        env_vars = {
            'DJANGO_SECRET_KEY': '',
            'DATABASE_URL': 'postgres://user:pass@localhost:5432/campus_db',
            'REDIS_URL': 'redis://localhost:6379/0',
            'DJANGO_ALLOWED_HOSTS': 'campus.nitsri.ac.in',
            'EMAIL_HOST_PASSWORD': 'dummy_password_for_test',
        }
        with self.assertRaises(Exception) as ctx:
            self._import_prod(env_vars)
        self.assertTrue(
            any(k in str(ctx.exception) for k in ['SECRET_KEY', 'insecure default', 'ImproperlyConfigured', 'ValueError'])
        )

    def test_prod_fails_on_missing_database_url(self):
        env_vars = {
            'DJANGO_SECRET_KEY': 'production-safe-high-entropy-secret-key-at-least-50-characters-long',
            'REDIS_URL': 'redis://localhost:6379/0',
            'DJANGO_ALLOWED_HOSTS': 'campus.nitsri.ac.in',
            'EMAIL_HOST_PASSWORD': 'dummy_password_for_test',
        }
        with self.assertRaises(Exception) as ctx:
            self._import_prod(env_vars)
        self.assertIn('DATABASE_URL', str(ctx.exception))

    def test_prod_fails_on_sqlite_database_url(self):
        env_vars = {
            'DJANGO_SECRET_KEY': 'production-safe-high-entropy-secret-key-at-least-50-characters-long',
            'DATABASE_URL': 'sqlite:///db.sqlite3',
            'REDIS_URL': 'redis://localhost:6379/0',
            'DJANGO_ALLOWED_HOSTS': 'campus.nitsri.ac.in',
            'EMAIL_HOST_PASSWORD': 'dummy_password_for_test',
        }
        with self.assertRaises(Exception) as ctx:
            self._import_prod(env_vars)
        self.assertIn('SQLite is not allowed in production', str(ctx.exception))

    def test_prod_succeeds_on_sqlite_when_allowed(self):
        env_vars = {
            'DJANGO_SECRET_KEY': 'production-safe-high-entropy-secret-key-at-least-50-characters-long',
            'DATABASE_URL': 'sqlite:///db.sqlite3',
            'DJANGO_ALLOW_SQLITE_PROD': 'True',
            'REDIS_URL': 'redis://localhost:6379/0',
            'DJANGO_ALLOWED_HOSTS': 'campus.nitsri.ac.in',
            'EMAIL_HOST_PASSWORD': 'dummy_password_for_test',
            'VAPID_PUBLIC_KEY': 'mock_prod_vapid_public_key_1234567890',
            'VAPID_PRIVATE_KEY': 'mock_prod_vapid_private_key_1234567890',
        }
        mod = self._import_prod(env_vars)
        self.assertEqual(mod.DATABASES['default']['ENGINE'], 'django.db.backends.sqlite3')
        self.assertEqual(mod.DATABASES['default']['OPTIONS']['timeout'], 20)

    def test_prod_fails_on_missing_redis_url(self):
        env_vars = {
            'DJANGO_SECRET_KEY': 'production-safe-high-entropy-secret-key-at-least-50-characters-long',
            'DATABASE_URL': 'postgres://user:pass@localhost:5432/campus_db',
            'DJANGO_ALLOWED_HOSTS': 'campus.nitsri.ac.in',
            'EMAIL_HOST_PASSWORD': 'dummy_password_for_test',
        }
        with self.assertRaises(Exception) as ctx:
            self._import_prod(env_vars)
        self.assertIn('REDIS_URL', str(ctx.exception))

    def test_prod_fails_on_insecure_allowed_hosts(self):
        env_vars = {
            'DJANGO_SECRET_KEY': 'production-safe-high-entropy-secret-key-at-least-50-characters-long',
            'DATABASE_URL': 'postgres://user:pass@localhost:5432/campus_db',
            'REDIS_URL': 'redis://localhost:6379/0',
            'DJANGO_ALLOWED_HOSTS': 'localhost,127.0.0.1',
            'EMAIL_HOST_PASSWORD': 'dummy_password_for_test',
        }
        with self.assertRaises(Exception) as ctx:
            self._import_prod(env_vars)
        self.assertIn('ALLOWED_HOSTS', str(ctx.exception))

    def test_prod_refuses_importing_secrets_local(self):
        """Production must fail if secrets_local is loaded in sys.modules."""
        env_vars = {
            'DJANGO_SECRET_KEY': 'production-safe-high-entropy-secret-key-at-least-50-characters-long',
            'DATABASE_URL': 'postgres://user:pass@localhost:5432/campus_db',
            'REDIS_URL': 'redis://localhost:6379/0',
            'DJANGO_ALLOWED_HOSTS': 'campus.nitsri.ac.in',
            'EMAIL_HOST_PASSWORD': 'dummy_password_for_test',
        }
        with patch.dict(sys.modules, {'campus.settings.secrets_local': object()}):
            with patch.dict(os.environ, env_vars, clear=True):
                if 'campus.settings.prod' in sys.modules:
                    del sys.modules['campus.settings.prod']
                with self.assertRaises(Exception) as ctx:
                    importlib.import_module('campus.settings.prod')
                self.assertIn('secrets_local', str(ctx.exception))

    def test_prod_fails_on_missing_vapid_keys(self):
        env_vars = {
            'DJANGO_SECRET_KEY': 'production-safe-high-entropy-secret-key-at-least-50-characters-long',
            'DATABASE_URL': 'postgres://user:pass@localhost:5432/campus_db',
            'REDIS_URL': 'redis://localhost:6379/0',
            'DJANGO_ALLOWED_HOSTS': 'campus.nitsri.ac.in,api.campus.nitsri.ac.in',
            'EMAIL_HOST_PASSWORD': 'dummy_password_for_test',
        }
        with self.assertRaises(Exception) as ctx:
            self._import_prod(env_vars)
        self.assertIn('VAPID', str(ctx.exception))

    def test_prod_succeeds_with_complete_secure_configuration(self):
        env_vars = {
            'DJANGO_SECRET_KEY': 'production-safe-high-entropy-secret-key-at-least-50-characters-long',
            'DATABASE_URL': 'postgres://user:pass@localhost:5432/campus_db',
            'REDIS_URL': 'redis://localhost:6379/0',
            'DJANGO_ALLOWED_HOSTS': 'campus.nitsri.ac.in,api.campus.nitsri.ac.in',
            'EMAIL_HOST_PASSWORD': 'dummy_password_for_test',
            'VAPID_PUBLIC_KEY': 'mock_prod_vapid_public_key_1234567890',
            'VAPID_PRIVATE_KEY': 'mock_prod_vapid_private_key_1234567890',
        }
        mod = self._import_prod(env_vars)
        self.assertFalse(mod.DEBUG)
        self.assertEqual(mod.EMAIL_HOST_USER, 'noreply.campus.nexus@gmail.com')
        self.assertEqual(mod.DEFAULT_FROM_EMAIL, 'Campus Nexus <noreply.campus.nexus@gmail.com>')
        self.assertEqual(mod.CHANNEL_LAYERS['default']['BACKEND'], 'channels_redis.core.RedisChannelLayer')
        self.assertEqual(mod.CACHES['default']['BACKEND'], 'django.core.cache.backends.redis.RedisCache')


class EmailSenderIdentityPreservationTest(TestCase):
    """Verify email sender account and identity remain exactly noreply.campus.nexus@gmail.com."""

    def test_sender_identity_is_preserved(self):
        self.assertEqual(
            getattr(settings, 'EMAIL_HOST_USER', ''),
            'noreply.campus.nexus@gmail.com',
            "Email user must remain noreply.campus.nexus@gmail.com"
        )
        self.assertIn(
            'noreply.campus.nexus@gmail.com',
            getattr(settings, 'DEFAULT_FROM_EMAIL', ''),
            "Default from email must remain noreply.campus.nexus@gmail.com"
        )
