"""Tests for student directory grouping by enrollment year and branch."""

from django.test import TestCase
from django.urls import reverse
from accounts.models import User


class StudentDirectoryGroupingTests(TestCase):

    def setUp(self):
        # Current user: 2024 BCSE
        self.me = User.objects.create_user(
            username='2024BCSE001',
            enrollment_number='2024BCSE001',
            branch='CSE',
            degree='Bachelors',
            joining_year=2024,
            gender='M',
            password='Password123',
            verified=True,
        )

        # Peer 1: Same year, same branch
        self.peer_2024_cse = User.objects.create_user(
            username='2024BCSE002',
            enrollment_number='2024BCSE002',
            branch='CSE',
            degree='Bachelors',
            joining_year=2024,
            gender='F',
            password='Password123',
            verified=True,
        )

        # Peer 2: Same year, different branch (ECE)
        self.peer_2024_ece = User.objects.create_user(
            username='2024BECE003',
            enrollment_number='2024BECE003',
            branch='ECE',
            degree='Bachelors',
            joining_year=2024,
            gender='M',
            password='Password123',
            verified=True,
        )

        # Peer 3: Older year 2023, CSE
        self.peer_2023_cse = User.objects.create_user(
            username='2023BCSE004',
            enrollment_number='2023BCSE004',
            branch='CSE',
            degree='Bachelors',
            joining_year=2023,
            gender='M',
            password='Password123',
            verified=True,
        )

        # Peer 4: Older year 2023, ECE
        self.peer_2023_ece = User.objects.create_user(
            username='2023BECE005',
            enrollment_number='2023BECE005',
            branch='ECE',
            degree='Bachelors',
            joining_year=2023,
            gender='F',
            password='Password123',
            verified=True,
        )

        # Peer 5: Newer year 2025, CSE
        self.peer_2025_cse = User.objects.create_user(
            username='2025BCSE006',
            enrollment_number='2025BCSE006',
            branch='CSE',
            degree='Bachelors',
            joining_year=2025,
            gender='M',
            password='Password123',
            verified=True,
        )

    def test_student_directory_grouping_order(self):
        """
        Verify:
        1. First group is current user's year and branch (2024, CSE).
        2. Next are other branches in current user's year lexicographically (2024, ECE).
        3. Next are other years lexicographically (2023 then 2025), with branches lexicographically.
        """
        self.client.force_login(self.me)
        response = self.client.get(reverse('user_search'))
        self.assertEqual(response.status_code, 200)

        year_groups = response.context['year_groups']
        self.assertEqual(len(year_groups), 3)

        # Year 1: 2024 (My year first)
        yg_2024 = year_groups[0]
        self.assertEqual(yg_2024['year'], 2024)
        self.assertTrue(yg_2024['is_my_year'])
        self.assertEqual(len(yg_2024['branches']), 2)
        # First branch in 2024: CSE (my branch)
        self.assertEqual(yg_2024['branches'][0]['branch'], 'CSE')
        self.assertTrue(yg_2024['branches'][0]['is_my_group'])
        self.assertEqual(yg_2024['branches'][0]['students'][0]['student'], self.peer_2024_cse)
        # Second branch in 2024: ECE (lexicographical)
        self.assertEqual(yg_2024['branches'][1]['branch'], 'ECE')
        self.assertFalse(yg_2024['branches'][1]['is_my_group'])
        self.assertEqual(yg_2024['branches'][1]['students'][0]['student'], self.peer_2024_ece)

        # Year 2: 2023 (lexicographical among other years)
        yg_2023 = year_groups[1]
        self.assertEqual(yg_2023['year'], 2023)
        self.assertFalse(yg_2023['is_my_year'])
        self.assertEqual(len(yg_2023['branches']), 2)
        # Branches in 2023: CSE, then ECE (lexicographical)
        self.assertEqual(yg_2023['branches'][0]['branch'], 'CSE')
        self.assertEqual(yg_2023['branches'][1]['branch'], 'ECE')

        # Year 3: 2025
        yg_2025 = year_groups[2]
        self.assertEqual(yg_2025['year'], 2025)
        self.assertEqual(len(yg_2025['branches']), 1)
        self.assertEqual(yg_2025['branches'][0]['branch'], 'CSE')
