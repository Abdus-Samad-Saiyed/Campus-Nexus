from datetime import date
from django.test import TestCase
from psycopg.types.range import Range

from accounts.models import User
from accommodate.models import (
    AccommodationGroup,
    AccommodationJoinApplication,
    AccommodationMembership,
    AdmissionMode,
    ApplicationStatus,
    DepartureReason,
    MembershipStatus,
    RecruitmentStatus,
)
from accommodate.services.admission_service import process_auto_queue
from accommodate.services.application_service import submit_application
from accommodate.services.group_service import (
    create_group,
    set_admission_mode,
    update_member_limit,
)
from accommodate.services.membership_service import leave_group, remove_member


class AutoQueueEngineTests(TestCase):
    def setUp(self):
        # Create users
        self.owner = User.objects.create_user(
            username="2023BCSE001",
            email="owner@nitk.ac.in",
            enrollment_number="2023BCSE001",
            first_name="Alice",
            last_name="Owner",
        )
        self.user_b = User.objects.create_user(
            username="2023BCSE002",
            email="userb@nitk.ac.in",
            enrollment_number="2023BCSE002",
            first_name="Bob",
            last_name="Applicant",
        )
        self.user_c = User.objects.create_user(
            username="2023BCSE003",
            email="userc@nitk.ac.in",
            enrollment_number="2023BCSE003",
            first_name="Charlie",
            last_name="Candidate",
        )
        self.user_d = User.objects.create_user(
            username="2023BCSE004",
            email="userd@nitk.ac.in",
            enrollment_number="2023BCSE004",
            first_name="David",
            last_name="Waitlist",
        )
        self.user_e = User.objects.create_user(
            username="2023BCSE005",
            email="usere@nitk.ac.in",
            enrollment_number="2023BCSE005",
            first_name="Eve",
            last_name="Alternate",
        )

        self.stay_period = Range(date(2027, 6, 1), date(2027, 7, 31), bounds='[]')

    def test_instant_admission_when_auto_group_has_capacity(self):
        """Applying to an AUTO group with capacity grants immediate active membership."""
        group = create_group(
            creator=self.owner,
            title="Auto Admission Bangalore",
            destination_city="Bengaluru",
            location_within_city="Koramangala",
            creator_planned_stay=self.stay_period,
            member_limit=3,
            admission_mode=AdmissionMode.AUTO,
        )

        # Bob applies
        app = submit_application(
            group_id=group.id,
            applicant=self.user_b,
            requested_stay=self.stay_period,
            message="Looking forward to staying together!",
        )

        app.refresh_from_db()
        self.assertEqual(app.status, ApplicationStatus.APPROVED)
        self.assertEqual(app.resolution_code, 'AUTO_ADMITTED')
        self.assertIsNotNone(app.membership)
        self.assertEqual(app.membership.status, MembershipStatus.ACTIVE)
        self.assertEqual(group.active_members_count, 2)

    def test_waitlist_when_auto_group_is_full(self):
        """Applying to an AUTO group at full capacity waitlists the applicant."""
        group = create_group(
            creator=self.owner,
            title="Full Auto Group",
            destination_city="Bengaluru",
            location_within_city="Whitefield",
            creator_planned_stay=self.stay_period,
            member_limit=2,  # Limit is 2; owner is already member 1
            admission_mode=AdmissionMode.AUTO,
        )

        # Bob takes member slot 2
        app_b = submit_application(
            group_id=group.id,
            applicant=self.user_b,
            requested_stay=self.stay_period,
        )
        self.assertEqual(app_b.status, ApplicationStatus.APPROVED)
        self.assertEqual(group.active_members_count, 2)

        # Charlie applies; group is full -> Charlie waitlisted
        app_c = submit_application(
            group_id=group.id,
            applicant=self.user_c,
            requested_stay=self.stay_period,
        )
        self.assertEqual(app_c.status, ApplicationStatus.WAITLISTED)
        self.assertIsNone(app_c.membership)
        self.assertEqual(group.active_members_count, 2)

    def test_fifo_promotion_on_limit_increase(self):
        """Increasing member_limit in an AUTO group automatically promotes waitlisted applicants in FIFO order."""
        group = create_group(
            creator=self.owner,
            title="Growing Auto Group",
            destination_city="Hyderabad",
            location_within_city="Gachibowli",
            creator_planned_stay=self.stay_period,
            member_limit=1,  # Owner fills the only slot
            admission_mode=AdmissionMode.AUTO,
        )

        app_b = submit_application(group.id, self.user_b, self.stay_period)
        app_c = submit_application(group.id, self.user_c, self.stay_period)
        app_d = submit_application(group.id, self.user_d, self.stay_period)

        self.assertEqual(app_b.status, ApplicationStatus.WAITLISTED)
        self.assertEqual(app_c.status, ApplicationStatus.WAITLISTED)
        self.assertEqual(app_d.status, ApplicationStatus.WAITLISTED)

        # Owner increases limit from 1 to 3
        update_member_limit(group.id, self.owner, 3)

        app_b.refresh_from_db()
        app_c.refresh_from_db()
        app_d.refresh_from_db()

        # Oldest two (Bob and Charlie) should be admitted, David remains waitlisted
        self.assertEqual(app_b.status, ApplicationStatus.APPROVED)
        self.assertEqual(app_b.resolution_code, 'AUTO_ADMITTED')
        self.assertEqual(app_c.status, ApplicationStatus.APPROVED)
        self.assertEqual(app_c.resolution_code, 'AUTO_ADMITTED')
        self.assertEqual(app_d.status, ApplicationStatus.WAITLISTED)
        self.assertEqual(group.active_members_count, 3)

    def test_fifo_promotion_on_member_departure(self):
        """When an active member leaves, the oldest waitlisted applicant is automatically admitted."""
        group = create_group(
            creator=self.owner,
            title="Auto Group Departure",
            destination_city="Pune",
            location_within_city="Hinjawadi",
            creator_planned_stay=self.stay_period,
            member_limit=2,
            admission_mode=AdmissionMode.AUTO,
        )

        # Bob admitted
        app_b = submit_application(group.id, self.user_b, self.stay_period)
        # Charlie waitlisted
        app_c = submit_application(group.id, self.user_c, self.stay_period)
        # David waitlisted
        app_d = submit_application(group.id, self.user_d, self.stay_period)

        self.assertEqual(group.active_members_count, 2)
        self.assertEqual(app_c.status, ApplicationStatus.WAITLISTED)

        # Bob leaves
        bob_membership = app_b.membership
        leave_group(bob_membership.id, self.user_b)

        # Charlie should be promoted automatically!
        app_c.refresh_from_db()
        app_d.refresh_from_db()

        self.assertEqual(app_c.status, ApplicationStatus.APPROVED)
        self.assertEqual(app_c.resolution_code, 'AUTO_ADMITTED')
        self.assertIsNotNone(app_c.membership)
        self.assertEqual(app_c.membership.status, MembershipStatus.ACTIVE)

        # David remains waitlisted
        self.assertEqual(app_d.status, ApplicationStatus.WAITLISTED)
        self.assertEqual(group.active_members_count, 2)

    def test_fifo_promotion_on_member_removal(self):
        """When an owner removes a member, the next waitlisted applicant is promoted."""
        group = create_group(
            creator=self.owner,
            title="Auto Group Removal",
            destination_city="Pune",
            location_within_city="Viman Nagar",
            creator_planned_stay=self.stay_period,
            member_limit=2,
            admission_mode=AdmissionMode.AUTO,
        )

        app_b = submit_application(group.id, self.user_b, self.stay_period)
        app_c = submit_application(group.id, self.user_c, self.stay_period)

        self.assertEqual(group.active_members_count, 2)
        self.assertEqual(app_c.status, ApplicationStatus.WAITLISTED)

        # Owner removes Bob for misbehavior
        remove_member(
            membership_id=app_b.membership.id,
            actor=self.owner,
            reason_code=DepartureReason.DISRUPTIVE_CONDUCT,
            reason_text="Repeated disturbances.",
        )

        app_c.refresh_from_db()
        self.assertEqual(app_c.status, ApplicationStatus.APPROVED)
        self.assertEqual(app_c.resolution_code, 'AUTO_ADMITTED')
        self.assertEqual(group.active_members_count, 2)

    def test_skip_conflicting_waitlisted_candidate(self):
        """A waitlisted candidate who took another overlapping stay is skipped and auto-rescinded."""
        group_a = create_group(
            creator=self.owner,
            title="Group A - Auto",
            destination_city="Bengaluru",
            location_within_city="Indiranagar",
            creator_planned_stay=self.stay_period,
            member_limit=1,
            admission_mode=AdmissionMode.AUTO,
        )

        # Bob is waitlisted in Group A
        app_a = submit_application(group_a.id, self.user_b, self.stay_period)
        self.assertEqual(app_a.status, ApplicationStatus.WAITLISTED)

        # Charlie is also waitlisted behind Bob in Group A
        app_a_c = submit_application(group_a.id, self.user_c, self.stay_period)
        self.assertEqual(app_a_c.status, ApplicationStatus.WAITLISTED)

        # Meanwhile, Bob creates or joins Group B for the same stay period
        group_b = create_group(
            creator=self.user_b,
            title="Group B - Bob's Own Group",
            destination_city="Bengaluru",
            location_within_city="HSR Layout",
            creator_planned_stay=self.stay_period,
            member_limit=2,
        )
        self.assertTrue(
            AccommodationMembership.objects.filter(
                user=self.user_b, status=MembershipStatus.ACTIVE
            ).exists()
        )

        # Now Group A opens up a slot (owner increases limit)
        update_member_limit(group_a.id, self.owner, 2)

        app_a.refresh_from_db()
        app_a_c.refresh_from_db()

        # Bob's application was auto-rescinded on Group B formation
        self.assertEqual(app_a.status, ApplicationStatus.AUTO_RESCINDED)
        self.assertIn(app_a.resolution_code, ['OVERLAPPING_MEMBERSHIP_ACCEPTED', 'OVERLAPPING_MEMBERSHIP_CONFLICT'])

        # Charlie (next in FIFO) should be admitted!
        self.assertEqual(app_a_c.status, ApplicationStatus.APPROVED)
        self.assertEqual(app_a_c.resolution_code, 'AUTO_ADMITTED')
        self.assertEqual(app_a_c.membership.user, self.user_c)

    def test_switch_from_manual_to_auto(self):
        """Switching from MANUAL to AUTO converts PENDING to WAITLISTED and processes available capacity."""
        group = create_group(
            creator=self.owner,
            title="Manual to Auto Transition",
            destination_city="Delhi NCR",
            location_within_city="Gurugram",
            creator_planned_stay=self.stay_period,
            member_limit=2,
            admission_mode=AdmissionMode.MANUAL,
        )

        app_b = submit_application(group.id, self.user_b, self.stay_period)
        app_c = submit_application(group.id, self.user_c, self.stay_period)

        self.assertEqual(app_b.status, ApplicationStatus.PENDING)
        self.assertEqual(app_c.status, ApplicationStatus.PENDING)

        # Switch to AUTO
        set_admission_mode(group.id, self.owner, AdmissionMode.AUTO)

        app_b.refresh_from_db()
        app_c.refresh_from_db()

        # Group has limit=2, owner takes 1 slot, Bob takes slot 2, Charlie remains waitlisted
        self.assertEqual(app_b.status, ApplicationStatus.APPROVED)
        self.assertEqual(app_c.status, ApplicationStatus.WAITLISTED)
        self.assertEqual(group.active_members_count, 2)

    def test_switch_from_auto_to_manual(self):
        """Switching from AUTO to MANUAL converts WAITLISTED back to PENDING with incremented objection round."""
        group = create_group(
            creator=self.owner,
            title="Auto to Manual Transition",
            destination_city="Srinagar",
            location_within_city="Rajbagh",
            creator_planned_stay=self.stay_period,
            member_limit=1,
            admission_mode=AdmissionMode.AUTO,
        )

        app_b = submit_application(group.id, self.user_b, self.stay_period)
        self.assertEqual(app_b.status, ApplicationStatus.WAITLISTED)
        self.assertEqual(app_b.objection_round, 1)

        # Switch to MANUAL
        set_admission_mode(group.id, self.owner, AdmissionMode.MANUAL)

        app_b.refresh_from_db()
        self.assertEqual(app_b.status, ApplicationStatus.PENDING)
        self.assertEqual(app_b.objection_round, 2)
