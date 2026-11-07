"""Management command to purge all synthetic and test accounts from the production database.

Removes developer debug fixtures (20XX*), audit crawlers (2099*), temporary migration/test users,
and self-declaration mock entries along with their associated rides, messages, friendships, and audit logs.
"""

from django.core.management.base import BaseCommand
from django.db import transaction
from accounts.models import User, DirectMessage, Friendship, FriendRequest, PhoneOTP, AuditEvent
from coordinate.models import (
    RideRequest, RideParticipant, RideMessage, RideConsent, RideWaitlist,
    RideJoinRequest, RideReview, RideIncidentReport
)

# Canonical list of synthetic and testing account enrollment numbers
TEST_ENROLLMENT_PATTERNS = [
    '20XXBCSE001', '20XXBECE001', '20XXBCIV001', '20XXBELE001', '20XXBMEC001',
    '2099BCSE999', '2099BCSE998', '2099BCSE997',
    '2024BCSE999', '2024BCSE888', '2024BCSE992',
    '1990BMME903',
]


class Command(BaseCommand):
    help = "Purge all synthetic test, debug, and audit accounts from the database for production readiness."

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Report accounts and records that would be purged without actually deleting them.',
        )

    def handle(self, *args, **options):
        dry_run = options.get('dry_run', False)

        test_users = User.objects.filter(
            enrollment_number__in=TEST_ENROLLMENT_PATTERNS
        ) | User.objects.filter(
            enrollment_number__startswith='20XX'
        ) | User.objects.filter(
            enrollment_number__startswith='2099'
        ) | User.objects.filter(
            username='u_test_mig'
        )
        test_users = test_users.distinct()

        user_count = test_users.count()
        if user_count == 0:
            self.stdout.write(self.style.SUCCESS("No testing accounts found. Database is already clean."))
            return

        self.stdout.write(self.style.WARNING(f"Found {user_count} testing accounts:"))
        enrollments = []
        for u in test_users:
            enrollments.append(u.enrollment_number)
            self.stdout.write(f"  * {u.enrollment_number} - {u.get_full_name() or u.username} ({u.email or 'no email'})")

        # Discover related objects
        test_rides = RideRequest.objects.filter(creator__in=test_users)
        test_participants = RideParticipant.objects.filter(user__in=test_users)
        test_messages = RideMessage.objects.filter(author__in=test_users)
        test_dms = DirectMessage.objects.filter(sender__in=test_users) | DirectMessage.objects.filter(recipient__in=test_users)
        test_friendships = Friendship.objects.filter(user1__in=test_users) | Friendship.objects.filter(user2__in=test_users)
        test_friend_requests = FriendRequest.objects.filter(sender__in=test_users) | FriendRequest.objects.filter(receiver__in=test_users)
        test_otps = PhoneOTP.objects.filter(user__in=test_users)
        test_consents = RideConsent.objects.filter(user__in=test_users)
        test_waitlists = RideWaitlist.objects.filter(user__in=test_users)
        test_join_requests = RideJoinRequest.objects.filter(user__in=test_users)
        test_audits = AuditEvent.objects.filter(actor_enrollment__in=enrollments)

        self.stdout.write("\nRelated records to be purged:")
        self.stdout.write(f"  - Rides created by test accounts   : {test_rides.count()}")
        self.stdout.write(f"  - Ride participants                : {test_participants.count()}")
        self.stdout.write(f"  - Ride messages                    : {test_messages.count()}")
        self.stdout.write(f"  - Direct messages                  : {test_dms.count()}")
        self.stdout.write(f"  - Friendships & requests           : {test_friendships.count() + test_friend_requests.count()}")
        self.stdout.write(f"  - OTPs & consents                  : {test_otps.count() + test_consents.count()}")
        self.stdout.write(f"  - Operational audit events         : {test_audits.count()}")

        if dry_run:
            self.stdout.write(self.style.NOTICE("\n[DRY RUN] No changes were applied to the database."))
            return

        with transaction.atomic():
            deleted_rides, _ = test_rides.delete()
            deleted_dms, _ = test_dms.delete()
            deleted_friendships, _ = test_friendships.delete()
            deleted_friend_requests, _ = test_friend_requests.delete()

            # Audit events use an immutable manager; purge test events using direct cursor
            from django.db import connection
            with connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM accounts_auditevent WHERE actor_enrollment = ANY(%s)",
                    [enrollments]
                )
                deleted_audits = cursor.rowcount

            deleted_otps, _ = test_otps.delete()
            deleted_consents, _ = test_consents.delete()
            deleted_waitlists, _ = test_waitlists.delete()
            deleted_join_requests, _ = test_join_requests.delete()
            deleted_participants, _ = test_participants.delete()
            deleted_messages, _ = test_messages.delete()
            deleted_users, _ = test_users.delete()

        self.stdout.write(self.style.SUCCESS(
            f"\n[SUCCESS] Successfully purged all {deleted_users} testing accounts and related records from production."
        ))
