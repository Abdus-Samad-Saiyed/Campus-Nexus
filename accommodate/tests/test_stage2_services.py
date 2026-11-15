from datetime import date, timedelta
from decimal import Decimal
from django.test import TestCase
from django.utils import timezone
from psycopg.types.range import Range

from accounts.models import User
from accommodate.exceptions import (
    ApplicationStateError,
    CapacityRequiredError,
    GroupStateError,
    OverlapConflictError,
    PermissionDeniedError,
)
from accommodate.models import (
    AccommodationGroup,
    AccommodationJoinApplication,
    AccommodationMembership,
    AccommodationMembershipDateChange,
    AccommodationStayLocation,
    AdmissionMode,
    ApplicationStatus,
    CostBasis,
    DepartureReason,
    DepartureType,
    GroupStatus,
    InvitationStatus,
    MembershipStatus,
    ModerationStatus,
    OwnershipEventType,
    RecruitmentStatus,
)
from accommodate.services import (
    approve_manual,
    claim_ownership,
    create_group,
    leave_group,
    record_objection,
    reject_application,
    remove_member,
    set_admission_mode,
    submit_application,
    submit_complaint,
    submit_review,
    toggle_recruitment,
    transfer_ownership,
    update_group_details,
    update_member_limit,
    update_planned_stay,
    withdraw_application,
    withdraw_objection,
)


class GroupServiceTests(TestCase):
    def setUp(self):
        self.creator = User.objects.create_user(
            username='creator',
            enrollment_number='2024BCSE201',
            gender='M',
            phone_number='9876543201',
            password='password123',
            verified=True,
        )
        self.user2 = User.objects.create_user(
            username='user2',
            enrollment_number='2024BCSE202',
            gender='F',
            phone_number='9876543202',
            password='password123',
            verified=True,
        )

    def test_create_group_atomic_with_creator_membership(self):
        """Creating a group admits the creator as active member and owner atomically."""
        stay_range = Range(date(2027, 6, 1), date(2027, 7, 31), bounds='[)')
        group = create_group(
            creator=self.creator,
            title="Amazon Manyata Summer Interns",
            destination_city="Bengaluru",
            location_within_city="Nagavara",
            creator_planned_stay=stay_range,
            member_limit=4,
            admission_mode=AdmissionMode.MANUAL,
            estimated_cost_amount=Decimal('12000.00'),
            estimated_cost_basis=CostBasis.PER_PERSON_MONTH,
        )
        self.assertEqual(group.initiated_by, self.creator)
        self.assertEqual(group.current_owner, self.creator)
        self.assertEqual(group.active_members_count, 1)

        mem = group.memberships.first()
        self.assertEqual(mem.user, self.creator)
        self.assertEqual(mem.status, MembershipStatus.ACTIVE)
        self.assertEqual(mem.planned_stay, stay_range)

    def test_create_group_rejected_if_creator_has_overlapping_active_stay(self):
        """Creator cannot form a group with dates overlapping another active membership."""
        # Pre-existing group & stay
        create_group(
            creator=self.creator,
            title="Existing Group",
            destination_city="Bengaluru",
            location_within_city="HSR",
            creator_planned_stay=Range(date(2027, 6, 1), date(2027, 7, 15), bounds='[)'),
        )
        # Attempt second group with overlapping period
        with self.assertRaises(OverlapConflictError):
            create_group(
                creator=self.creator,
                title="Conflicting Group",
                destination_city="Hyderabad",
                location_within_city="Gachibowli",
                creator_planned_stay=Range(date(2027, 7, 1), date(2027, 8, 1), bounds='[)'),
            )

    def test_create_group_auto_rescinds_creators_other_conflicting_applications(self):
        """Creating a group auto-rescinds creator's pending applications in other groups that overlap."""
        # Other user creates a group
        other_group = create_group(
            creator=self.user2,
            title="Other Group",
            destination_city="Pune",
            location_within_city="Hinjawadi",
            creator_planned_stay=Range(date(2027, 6, 1), date(2027, 8, 1), bounds='[)'),
        )
        # Creator applies to other group
        app = submit_application(
            group_id=other_group.id,
            applicant=self.creator,
            requested_stay=Range(date(2027, 6, 15), date(2027, 7, 15), bounds='[)'),
        )
        self.assertEqual(app.status, ApplicationStatus.PENDING)

        # Creator now creates their own group overlapping that requested stay
        create_group(
            creator=self.creator,
            title="My Own Group",
            destination_city="Bengaluru",
            location_within_city="Manyata",
            creator_planned_stay=Range(date(2027, 6, 1), date(2027, 7, 31), bounds='[)'),
        )

        app.refresh_from_db()
        self.assertEqual(app.status, ApplicationStatus.AUTO_RESCINDED)
        self.assertEqual(app.resolution_code, 'OVERLAPPING_MEMBERSHIP_ACCEPTED')

    def test_update_member_limit_does_not_eject_members(self):
        """Lowering member limit below current occupant count blocks future admissions without ejecting anyone."""
        group = create_group(
            creator=self.creator,
            title="Group",
            destination_city="Bengaluru",
            location_within_city="HSR",
            creator_planned_stay=Range(date(2027, 6, 1), date(2027, 7, 1), bounds='[)'),
            member_limit=5,
        )
        # Admit user 2
        app = submit_application(
            group_id=group.id,
            applicant=self.user2,
            requested_stay=Range(date(2027, 6, 1), date(2027, 7, 1), bounds='[)'),
        )
        approve_manual(app.id, self.creator)
        self.assertEqual(group.active_members_count, 2)

        # Owner lowers limit to 1 (below current active count of 2)
        update_member_limit(group.id, self.creator, new_limit=1)
        group.refresh_from_db()
        self.assertEqual(group.member_limit, 1)
        self.assertEqual(group.active_members_count, 2)
        self.assertFalse(group.has_available_capacity)

    def test_toggle_recruitment_closing_expires_pending_applications(self):
        """Closing recruitment bulk-expires pending requests."""
        group = create_group(
            creator=self.creator,
            title="Recruitment Group",
            destination_city="Hyderabad",
            location_within_city="Gachibowli",
            creator_planned_stay=Range(date(2027, 6, 1), date(2027, 7, 1), bounds='[)'),
        )
        app = submit_application(
            group_id=group.id,
            applicant=self.user2,
            requested_stay=Range(date(2027, 6, 1), date(2027, 7, 1), bounds='[)'),
        )
        self.assertEqual(app.status, ApplicationStatus.PENDING)

        toggle_recruitment(group.id, self.creator, RecruitmentStatus.CLOSED)
        group.refresh_from_db()
        self.assertEqual(group.recruitment_status, RecruitmentStatus.CLOSED)

        app.refresh_from_db()
        self.assertEqual(app.status, ApplicationStatus.EXPIRED)
        self.assertEqual(app.resolution_code, 'GROUP_CLOSED')


class AdmissionServiceTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username='owner',
            enrollment_number='2024BCSE301',
            gender='M',
            phone_number='9876543301',
            password='password123',
            verified=True,
        )
        self.applicant = User.objects.create_user(
            username='applicant',
            enrollment_number='2024BCSE302',
            gender='F',
            phone_number='9876543302',
            password='password123',
            verified=True,
        )
        self.other_owner = User.objects.create_user(
            username='other_owner',
            enrollment_number='2024BCSE303',
            gender='M',
            phone_number='9876543303',
            password='password123',
            verified=True,
        )

        self.group = create_group(
            creator=self.owner,
            title="Amazon Manyata Group",
            destination_city="Bengaluru",
            location_within_city="Nagavara",
            creator_planned_stay=Range(date(2027, 6, 1), date(2027, 7, 31), bounds='[)'),
            member_limit=2,
            admission_mode=AdmissionMode.MANUAL,
        )
        self.other_group = create_group(
            creator=self.other_owner,
            title="Google Outer Ring Rd Group",
            destination_city="Bengaluru",
            location_within_city="Bellandur",
            creator_planned_stay=Range(date(2027, 6, 1), date(2027, 7, 31), bounds='[)'),
            member_limit=3,
        )

    def test_manual_approval_success_and_cascade_rescission(self):
        """Approving an applicant admits them and auto-rescinds their other overlapping applications."""
        # Applicant applies to Group A [June 5 - July 25)
        app_a = submit_application(
            group_id=self.group.id,
            applicant=self.applicant,
            requested_stay=Range(date(2027, 6, 5), date(2027, 7, 25), bounds='[)'),
        )
        # Applicant also applies to Group B with overlapping dates [June 10 - July 20)
        app_b = submit_application(
            group_id=self.other_group.id,
            applicant=self.applicant,
            requested_stay=Range(date(2027, 6, 10), date(2027, 7, 20), bounds='[)'),
        )

        # Owner A approves applicant
        mem = approve_manual(app_a.id, self.owner)
        self.assertIsNotNone(mem.id)
        self.assertEqual(mem.group, self.group)
        self.assertEqual(mem.user, self.applicant)
        self.assertEqual(mem.status, MembershipStatus.ACTIVE)

        app_a.refresh_from_db()
        self.assertEqual(app_a.status, ApplicationStatus.APPROVED)
        self.assertEqual(app_a.membership, mem)

        # App B must be auto-rescinded
        app_b.refresh_from_db()
        self.assertEqual(app_b.status, ApplicationStatus.AUTO_RESCINDED)
        self.assertEqual(app_b.resolution_code, 'OVERLAPPING_MEMBERSHIP_ACCEPTED')

    def test_approval_blocked_when_group_is_full(self):
        """Owner approval is blocked with CapacityRequiredError when group reaches member_limit."""
        # Group limit is 2. Owner is already 1 active member.
        # Add applicant 1
        app1 = submit_application(
            group_id=self.group.id,
            applicant=self.applicant,
            requested_stay=Range(date(2027, 6, 1), date(2027, 7, 1), bounds='[)'),
        )
        approve_manual(app1.id, self.owner)
        self.assertEqual(self.group.active_members_count, 2)

        # User 3 applies to full group
        user3 = User.objects.create_user(
            username='user3',
            enrollment_number='2024BCSE304',
            gender='M',
            phone_number='9876543304',
            password='password123',
            verified=True,
        )
        app2 = submit_application(
            group_id=self.group.id,
            applicant=user3,
            requested_stay=Range(date(2027, 6, 1), date(2027, 7, 1), bounds='[)'),
        )
        self.assertEqual(app2.status, ApplicationStatus.PENDING)

        # Owner tries to approve while full -> CapacityRequiredError
        with self.assertRaises(CapacityRequiredError):
            approve_manual(app2.id, self.owner)

        # Owner raises limit to 3 and retries -> Success!
        update_member_limit(self.group.id, self.owner, new_limit=3)
        mem2 = approve_manual(app2.id, self.owner)
        self.assertIsNotNone(mem2.id)
        self.assertEqual(self.group.active_members_count, 3)

    def test_advisory_objection_recorded_on_approval(self):
        """Owner can approve despite member objections; resolution_code indicates approved_with_objections."""
        # Admit applicant into group
        app1 = submit_application(
            group_id=self.group.id,
            applicant=self.applicant,
            requested_stay=Range(date(2027, 6, 1), date(2027, 7, 1), bounds='[)'),
        )
        # Update limit so group has space
        update_member_limit(self.group.id, self.owner, new_limit=4)
        approve_manual(app1.id, self.owner)

        # User 3 applies
        user3 = User.objects.create_user(
            username='user3',
            enrollment_number='2024BCSE304',
            gender='M',
            phone_number='9876543304',
            password='password123',
            verified=True,
        )
        app2 = submit_application(
            group_id=self.group.id,
            applicant=user3,
            requested_stay=Range(date(2027, 6, 1), date(2027, 7, 1), bounds='[)'),
        )

        # Applicant 1 (now active member) objects to User 3
        record_objection(app2.id, self.applicant, reason="Different shift timings")

        # Owner approves anyway
        approve_manual(app2.id, self.owner)
        app2.refresh_from_db()
        self.assertEqual(app2.status, ApplicationStatus.APPROVED)
        self.assertEqual(app2.resolution_code, 'APPROVED_WITH_OBJECTIONS')


class MembershipAndDepartureTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username='owner',
            enrollment_number='2024BCSE401',
            gender='M',
            phone_number='9876543401',
            password='password123',
            verified=True,
        )
        self.member = User.objects.create_user(
            username='member',
            enrollment_number='2024BCSE402',
            gender='F',
            phone_number='9876543402',
            password='password123',
            verified=True,
        )
        self.group = create_group(
            creator=self.owner,
            title="Hyderabad Gachibowli Group",
            destination_city="Hyderabad",
            location_within_city="Gachibowli",
            creator_planned_stay=Range(date(2027, 6, 1), date(2027, 7, 31), bounds='[)'),
            member_limit=3,
        )
        app = submit_application(
            group_id=self.group.id,
            applicant=self.member,
            requested_stay=Range(date(2027, 6, 1), date(2027, 7, 31), bounds='[)'),
        )
        self.membership = approve_manual(app.id, self.owner)

    def test_update_planned_stay_success(self):
        """Member can update stay dates if non-conflicting."""
        new_stay = Range(date(2027, 6, 10), date(2027, 8, 10), bounds='[)')
        change_log = update_planned_stay(self.membership.id, self.member, new_stay)
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.planned_stay, new_stay)
        self.assertEqual(change_log.new_period, new_stay)

    def test_voluntary_leave_and_reputation_invitations(self):
        """Voluntary leave ends membership, releases capacity, and issues review invitations."""
        self.assertEqual(self.group.active_members_count, 2)
        leave_group(self.membership.id, self.member)

        self.membership.refresh_from_db()
        self.assertEqual(self.membership.status, MembershipStatus.LEFT)
        self.assertEqual(self.membership.departure_type, DepartureType.VOLUNTARY)
        self.assertIsNotNone(self.membership.left_at)

        self.assertEqual(self.group.active_members_count, 1)

        # Review invitation should have been issued to the owner (who shared overlapping platform interval)
        from accommodate.models import AccommodationReviewInvitation
        invite = AccommodationReviewInvitation.objects.filter(
            subject_membership=self.membership,
            reviewer_user=self.owner
        ).first()
        self.assertIsNotNone(invite)
        self.assertEqual(invite.status, InvitationStatus.OPEN)

    def test_owner_removal_with_reason(self):
        """Owner removes member with mandatory reason category."""
        remove_member(
            self.membership.id,
            self.owner,
            reason_code=DepartureReason.BREACH_OF_GROUP_AGREEMENT,
            reason_text="Failed to pay hostel room advance deposit"
        )
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.status, MembershipStatus.REMOVED)
        self.assertEqual(self.membership.departure_reason_code, DepartureReason.BREACH_OF_GROUP_AGREEMENT)
        self.assertEqual(self.membership.removed_by, self.owner)


class OwnershipAndLifecycleTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username='owner',
            enrollment_number='2024BCSE501',
            gender='M',
            phone_number='9876543501',
            password='password123',
            verified=True,
        )
        self.member = User.objects.create_user(
            username='member',
            enrollment_number='2024BCSE502',
            gender='F',
            phone_number='9876543502',
            password='password123',
            verified=True,
        )
        self.group = create_group(
            creator=self.owner,
            title="Pune Hinjawadi Group",
            destination_city="Pune",
            location_within_city="Hinjawadi",
            creator_planned_stay=Range(date(2027, 6, 1), date(2027, 7, 31), bounds='[)'),
        )
        app = submit_application(
            group_id=self.group.id,
            applicant=self.member,
            requested_stay=Range(date(2027, 6, 1), date(2027, 7, 31), bounds='[)'),
        )
        self.member_mem = approve_manual(app.id, self.owner)

    def test_transfer_ownership(self):
        """Owner transfers ownership to active occupant."""
        transfer_ownership(self.group.id, self.owner, self.member)
        self.group.refresh_from_db()
        self.assertEqual(self.group.current_owner, self.member)

    def test_final_member_exit_ends_group(self):
        """When the last active member departs, group transitions to ENDED and recruitment closes."""
        owner_mem = self.group.memberships.filter(user=self.owner).first()

        # Member leaves first
        leave_group(self.member_mem.id, self.member)
        self.group.refresh_from_db()
        self.assertEqual(self.group.group_status, GroupStatus.ACTIVE)

        # Owner is last member and leaves
        leave_group(owner_mem.id, self.owner)
        self.group.refresh_from_db()
        self.assertEqual(self.group.group_status, GroupStatus.ENDED)
        self.assertEqual(self.group.recruitment_status, RecruitmentStatus.CLOSED)
        self.assertIsNone(self.group.current_owner)
