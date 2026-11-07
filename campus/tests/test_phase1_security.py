"""Phase 1 regression tests — Security hardening & debug account guards.

Tests cover:
  SEC-07: Debug accounts command must not print raw passwords
  SEC-08: create_debug_accounts refuses to run when DEBUG=False (standard and --reset)
  CHK-01: Application passes core Django system checks
"""

from io import StringIO
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings


class DebugAccountsProductionGuardTest(TestCase):
    """SEC-08: create_debug_accounts must refuse to run when DEBUG=False."""

    @override_settings(DEBUG=False)
    def test_refuses_when_debug_false(self):
        with self.assertRaises(CommandError) as ctx:
            call_command('create_debug_accounts')
        self.assertIn('DEBUG=True', str(ctx.exception))

    @override_settings(DEBUG=True)
    def test_runs_when_debug_true(self):
        """Command executes successfully when DEBUG=True."""
        out = StringIO()
        call_command('create_debug_accounts', stdout=out)


class DebugAccountsResetGuardTest(TestCase):
    """SEC-08: --reset flag also refuses under production."""

    @override_settings(DEBUG=False)
    def test_reset_refuses_when_debug_false(self):
        with self.assertRaises(CommandError):
            call_command('create_debug_accounts', '--reset')


class DebugAccountsNoPasswordLeakTest(TestCase):
    """SEC-07: The command must not print the raw password to stdout."""

    @override_settings(DEBUG=True)
    def test_no_plaintext_password_in_output(self):
        out = StringIO()
        call_command('create_debug_accounts', stdout=out)
        output = out.getvalue()
        self.assertNotIn('Debug@123', output)


class ApplicationStartsWithTestSettingsTest(TestCase):
    """Verify the application starts with safe test settings."""

    def test_django_check_passes(self):
        """Django system checks must pass (called via management command)."""
        call_command('check', stdout=StringIO())
