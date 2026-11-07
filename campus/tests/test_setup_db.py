"""Unit tests for the setup_db script and user provisioning CLI."""

import io
from unittest.mock import patch
from django.test import TestCase
from django.contrib.auth import get_user_model
from scripts.setup_db import main, get_unapplied_migrations

User = get_user_model()


class SetupDBCliTest(TestCase):
    """Test non-interactive flags and functions of setup_db."""

    def test_get_unapplied_migrations(self):
        """Unapplied migrations check should return a list (empty during clean test run)."""
        unapplied = get_unapplied_migrations()
        self.assertIsInstance(unapplied, list)

    def test_cli_add_user_student(self):
        """CLI flag --add-user creates a student properly."""
        enrollment = "2024BCSE777"
        argv = [
            "--add-user",
            "--enrollment", enrollment,
            "--first-name", "TestStudent",
            "--last-name", "CLI",
            "--password", "SecretStudentPass1!",
        ]
        with patch('sys.stdout', new_callable=io.StringIO):
            main(argv=argv)

        user = User.objects.get(enrollment_number=enrollment)
        self.assertEqual(user.first_name, "TestStudent")
        self.assertEqual(user.degree, "Bachelors")
        self.assertEqual(user.branch, "CSE")
        self.assertEqual(user.roll_number, "777")
        self.assertTrue(user.check_password("SecretStudentPass1!"))
        self.assertFalse(user.is_superuser)
        self.assertFalse(user.is_staff)
        self.assertTrue(user.verified)

    def test_cli_add_user_admin(self):
        """CLI flag --add-user --admin creates a superuser properly."""
        enrollment = "2024BCSE776"
        argv = [
            "--add-user",
            "--enrollment", enrollment,
            "--first-name", "AdminUser",
            "--last-name", "Boss",
            "--admin",
            "--password", "SuperAdminPass123!",
        ]
        with patch('sys.stdout', new_callable=io.StringIO):
            main(argv=argv)

        user = User.objects.get(enrollment_number=enrollment)
        self.assertEqual(user.first_name, "AdminUser")
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.is_staff)
        self.assertTrue(user.check_password("SuperAdminPass123!"))
