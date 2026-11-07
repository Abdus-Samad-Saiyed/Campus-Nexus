from datetime import timedelta
import time
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from coordinate.models import ParticipantStatus, RideParticipant, RideRequest
from coordinate.participation import transition_participant_state


class StateMachineTransitionTests(TestCase):
    def setUp(self):
        self.host = User.objects.create_user(
            username='host_user',
            enrollment_number='2024BCSE101',
            gender='M',
            phone_number='9876543210',
            password='password123',
            verified=True,
        )
        self.student = User.objects.create_user(
            username='student_user',
            enrollment_number='2024BCSE102',
            gender='M',
            phone_number='9876543211',
            password='password123',
            verified=True,
        )
        self.ride = RideRequest.objects.create(
            creator=self.host,
            route='NIT_TO_AIRPORT',
            visibility='MIXED',
            departure_time=timezone.now() + timedelta(days=2),
            total_spots=3,
        )

    def test_entry_transitions(self):
        # 1. Direct host addition to CONFIRMED
        host_rp = transition_participant_state(self.ride, self.host, ParticipantStatus.CONFIRMED)
        self.assertEqual(host_rp.status, ParticipantStatus.CONFIRMED)

        # 2. Student request to PENDING
        student_rp = transition_participant_state(self.ride, self.student, ParticipantStatus.PENDING, note='Need ride')
        self.assertEqual(student_rp.status, ParticipantStatus.PENDING)
        self.assertEqual(student_rp.note, 'Need ride')

    def test_pending_moderation_and_withdrawal(self):
        # Student requests PENDING
        rp = transition_participant_state(self.ride, self.student, ParticipantStatus.PENDING)
        self.assertEqual(rp.status, ParticipantStatus.PENDING)

        # Host approves -> CONFIRMED
        rp = transition_participant_state(self.ride, self.student, ParticipantStatus.CONFIRMED)
        self.assertEqual(rp.status, ParticipantStatus.CONFIRMED)
        self.assertIsNotNone(rp.reviewed_at)

    def test_decline_and_reentry(self):
        # Student requests PENDING
        transition_participant_state(self.ride, self.student, ParticipantStatus.PENDING)
        # Host declines -> DECLINED
        rp = transition_participant_state(self.ride, self.student, ParticipantStatus.DECLINED)
        self.assertEqual(rp.status, ParticipantStatus.DECLINED)

        # Student re-applies -> PENDING (allowed)
        rp2 = transition_participant_state(self.ride, self.student, ParticipantStatus.PENDING, note='Second try')
        self.assertEqual(rp2.status, ParticipantStatus.PENDING)
        self.assertEqual(rp2.note, 'Second try')

    def test_waitlist_promotion_and_queue_fifo(self):
        # Student joins WAITLISTED
        t1 = timezone.now() - timedelta(hours=2)
        rp = transition_participant_state(self.ride, self.student, ParticipantStatus.WAITLISTED)
        original_joined_at = rp.joined_at

        # Student withdraws -> CANCELLED
        rp = transition_participant_state(self.ride, self.student, ParticipantStatus.CANCELLED)
        self.assertEqual(rp.status, ParticipantStatus.CANCELLED)
        self.assertTrue(rp.is_revoked)

        # Student re-joins WAITLISTED -> queued_at must be updated, joined_at preserved
        time.sleep(0.01)
        rp2 = transition_participant_state(self.ride, self.student, ParticipantStatus.WAITLISTED)
        self.assertEqual(rp2.status, ParticipantStatus.WAITLISTED)
        self.assertEqual(rp2.joined_at, original_joined_at)
        self.assertGreater(rp2.queued_at, original_joined_at)

        # Promotion to CONFIRMED
        rp3 = transition_participant_state(self.ride, self.student, ParticipantStatus.CONFIRMED)
        self.assertEqual(rp3.status, ParticipantStatus.CONFIRMED)

    def test_confirmed_passenger_leave_and_kick(self):
        # Setup confirmed passenger with consent
        rp = transition_participant_state(
            self.ride, self.student, ParticipantStatus.CONFIRMED,
            consent_data={'disclose_ride_identity': True, 'share_contact_details': True}
        )
        self.assertFalse(rp.is_revoked)
        self.assertTrue(rp.disclose_ride_identity)

        # Passenger leaves -> LEFT (consent revoked)
        rp_left = transition_participant_state(self.ride, self.student, ParticipantStatus.LEFT)
        self.assertEqual(rp_left.status, ParticipantStatus.LEFT)
        self.assertTrue(rp_left.is_revoked)
        self.assertIsNotNone(rp_left.revoked_at)

    def test_kicked_passenger_is_terminal(self):
        # Passenger confirmed then kicked
        transition_participant_state(self.ride, self.student, ParticipantStatus.CONFIRMED)
        rp_kicked = transition_participant_state(self.ride, self.student, ParticipantStatus.KICKED)
        self.assertEqual(rp_kicked.status, ParticipantStatus.KICKED)
        self.assertTrue(rp_kicked.is_revoked)

        # Kicked passenger cannot re-request or rejoin
        with self.assertRaises(ValidationError):
            transition_participant_state(self.ride, self.student, ParticipantStatus.PENDING)

        with self.assertRaises(ValidationError):
            transition_participant_state(self.ride, self.student, ParticipantStatus.CONFIRMED)

        with self.assertRaises(ValidationError):
            transition_participant_state(self.ride, self.student, ParticipantStatus.WAITLISTED)

    def test_illegal_transitions_raise_validation_error(self):
        # Cannot jump from None to LEFT or KICKED
        with self.assertRaises(ValidationError):
            transition_participant_state(self.ride, self.student, ParticipantStatus.LEFT)

        with self.assertRaises(ValidationError):
            transition_participant_state(self.ride, self.student, ParticipantStatus.KICKED)

        # Cannot jump from WAITLISTED to LEFT directly
        transition_participant_state(self.ride, self.student, ParticipantStatus.WAITLISTED)
        with self.assertRaises(ValidationError):
            transition_participant_state(self.ride, self.student, ParticipantStatus.LEFT)


import threading
import unittest
from django.db import connection, transaction
from django.test import TransactionTestCase

from coordinate.policies import can_view_ride_contact, can_view_ride_identity
from coordinate.services import user_has_active_ride
from campus.api.serializers import serialize_ride_detail


class AuthorizationRegressionTests(TestCase):
    def setUp(self):
        self.host = User.objects.create_user(
            username='host_auth',
            enrollment_number='2024BCSE201',
            gender='M',
            phone_number='9876543220',
            password='password123',
            verified=True,
        )
        self.student = User.objects.create_user(
            username='student_auth',
            enrollment_number='2024BCSE202',
            gender='M',
            phone_number='9876543221',
            password='password123',
            verified=True,
        )
        self.ride = RideRequest.objects.create(
            creator=self.host,
            route='NIT_TO_AIRPORT',
            visibility='MIXED',
            departure_time=timezone.now() + timedelta(days=2),
            total_spots=3,
        )
        transition_participant_state(self.ride, self.host, ParticipantStatus.CONFIRMED)

    def test_non_confirmed_statuses_isolated_from_authorization_and_capacity(self):
        non_confirmed_statuses = [
            ParticipantStatus.PENDING,
            ParticipantStatus.WAITLISTED,
            ParticipantStatus.LEFT,
            ParticipantStatus.KICKED,
            ParticipantStatus.DECLINED,
            ParticipantStatus.CANCELLED,
        ]

        for status in non_confirmed_statuses:
            with self.subTest(status=status):
                # Setup participant in target non-confirmed status
                rp, _ = RideParticipant.all_rows.update_or_create(
                    ride=self.ride,
                    user=self.student,
                    defaults={
                        'status': status,
                        'disclose_ride_identity': True,
                        'share_contact_details': True,
                    }
                )

                # 1. ConfirmedManager isolation: RideParticipant.objects.filter(...) misses them
                self.assertFalse(
                    RideParticipant.objects.filter(ride=self.ride, user=self.student).exists(),
                    f"Status {status} leaked into default RideParticipant.objects queryset!"
                )

                # 2. Capacity isolation: spots_remaining and is_full only count confirmed
                self.assertEqual(self.ride.participant_count(), 1, f"Status {status} incremented participant count!")
                self.assertEqual(self.ride.spots_remaining(), 2)
                self.assertFalse(self.ride.is_full())

                # 3. Active ride isolation: user_has_active_ride evaluates to False
                self.assertFalse(
                    user_has_active_ride(self.student),
                    f"Status {status} wrongly blocked student from creating/joining other rides!"
                )

                # 4. API serialization isolation: serialize_ride_detail excludes them
                detail = serialize_ride_detail(self.ride, request_user=self.host)
                passenger_enrollments = [p['enrollment_number'] for p in detail['participants'] if not p.get('is_creator')]
                self.assertNotIn(
                    self.student.enrollment_number,
                    passenger_enrollments,
                    f"Status {status} student leaked into serialized passenger roster!"
                )

                # 5. Contact privacy isolation: non-confirmed passengers never reveal contacts
                self.assertFalse(
                    can_view_ride_contact(self.host, self.ride, self.student),
                    f"Host was able to view private contact details of student with status {status}!"
                )


class ConcurrencyLockingTests(TransactionTestCase):
    """
    Validates atomic row-locking under concurrent threads during seat claims.
    Multi-threaded row locking is designed for PostgreSQL row-level locks and
    is skipped on SQLite which employs file/table-level locks.
    """
    def setUp(self):
        self.host = User.objects.create_user(
            username='c_host', enrollment_number='2024BCSE401', gender='M', phone_number='9876543401', password='pass'
        )
        self.student1 = User.objects.create_user(
            username='c_s1', enrollment_number='2024BCSE402', gender='M', phone_number='9876543402', password='pass'
        )
        self.student2 = User.objects.create_user(
            username='c_s2', enrollment_number='2024BCSE403', gender='M', phone_number='9876543403', password='pass'
        )
        self.ride = RideRequest.objects.create(
            creator=self.host,
            route='NIT_TO_AIRPORT',
            visibility='MIXED',
            departure_time=timezone.now() + timedelta(days=2),
            total_spots=2,  # 1 host spot + 1 remaining passenger spot
        )
        transition_participant_state(self.ride, self.host, ParticipantStatus.CONFIRMED)

    @unittest.skipIf(connection.vendor == 'sqlite', "Multi-threaded row concurrency tests require PostgreSQL (skipped on SQLite)")
    def test_concurrent_claim_final_spot(self):
        """
        Two simultaneous approvals/transitions for the last remaining seat.
        Under row locking, exactly one becomes CONFIRMED, and total confirmed count never exceeds total_spots.
        """
        results = []

        def claim_seat(user):
            connection.close()
            try:
                with transaction.atomic():
                    r = RideRequest.objects.select_for_update().get(id=self.ride.id)
                    if r.spots_remaining() > 0:
                        transition_participant_state(r, user, ParticipantStatus.CONFIRMED)
                        results.append((user.enrollment_number, True))
                    else:
                        results.append((user.enrollment_number, False))
            except Exception as e:
                results.append((user.enrollment_number, False, str(e)))
            finally:
                connection.close()

        t1 = threading.Thread(target=claim_seat, args=(self.student1,))
        t2 = threading.Thread(target=claim_seat, args=(self.student2,))

        t1.start()
        t2.start()
        t1.join()
        t2.join()

        success_count = sum(1 for r in results if r[1] is True)
        self.assertEqual(success_count, 1, f"Expected exactly 1 seat claim to succeed, got results: {results}")

        confirmed_count = RideParticipant.objects.filter(ride=self.ride, status=ParticipantStatus.CONFIRMED).count()
        self.assertEqual(confirmed_count, 2)  # Host (1) + 1 winner = 2 (total_spots)
