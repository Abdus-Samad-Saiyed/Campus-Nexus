from datetime import date
from django.db import IntegrityError
from django.test import TestCase
from psycopg.types.range import Range

from accounts.models import User
from accommodate.models import (
    AccommodationStayLocation,
    AccommodationGroup,
    AccommodationMembership,
    AccommodationMembershipDateChange,
    AccommodationJoinApplication,
    AccommodationApplicationObjection,
    AccommodationReviewInvitation,
    AccommodationReview,
    AdmissionMode,
    RecruitmentStatus,
    GroupStatus,
    CostBasis,
    MembershipStatus,
    ApplicationStatus,
)


class StayLocationModelTests(TestCase):
    def test_seeded_stays_exist_and_queryable(self):
        """Verify that top stays were seeded in migration."""
        seeded_count = AccommodationStayLocation.objects.filter(is_seeded=True).count()
        self.assertGreaterEqual(seeded_count, 14)
        
        # Verify Bengaluru stay exists
        hsr = AccommodationStayLocation.objects.filter(google_place_id="ChIJbU123_HSR_ZOLO_01").first()
        self.assertIsNotNone(hsr)
        self.assertEqual(hsr.city, "Bengaluru")
        self.assertEqual(hsr.city_normalized, "bengaluru")
        self.assertEqual(hsr.location_within_city_normalized, "hsr layout sector 2")

    def test_stay_location_normalization_on_save(self):
        """Verify city and locality normalization."""
        stay = AccommodationStayLocation.objects.create(
            name="Zolo Horizon",
            formatted_address="Koramangala, Bengaluru, Karnataka",
            city="  Bengaluru  ",
            location_within_city=" Koramangala 5th Block ",
            google_place_id="ChIJ_CUSTOM_TEST_01"
        )
        self.assertEqual(stay.city_normalized, "bengaluru")
        self.assertEqual(stay.location_within_city_normalized, "koramangala 5th block")


class GroupModelTests(TestCase):
    def setUp(self):
        self.user1 = User.objects.create_user(
            username='user1',
            enrollment_number='2024BCSE101',
            gender='M',
            phone_number='9876543210',
            password='password123',
            verified=True,
        )

    def test_group_creation_and_normalization(self):
        """Verify group creation and automatic lowercase field normalization."""
        group = AccommodationGroup.objects.create(
            title="Amazon Manyata Summer Interns",
            destination_city=" Bengaluru ",
            location_within_city=" Nagavara / Manyata Tech Park ",
            lodging_name=" Zolo Stays ",
            initiated_by=self.user1,
            current_owner=self.user1,
            member_limit=4,
            admission_mode=AdmissionMode.MANUAL,
            recruitment_status=RecruitmentStatus.OPEN,
            group_status=GroupStatus.ACTIVE,
            estimated_cost_amount=12000.00,
            estimated_cost_basis=CostBasis.PER_PERSON_MONTH,
        )
        self.assertEqual(group.destination_city_normalized, "bengaluru")
        self.assertEqual(group.location_within_city_normalized, "nagavara / manyata tech park")
        self.assertEqual(group.lodging_name_normalized, "zolo stays")
        self.assertEqual(group.active_members_count, 0)
        self.assertTrue(group.has_available_capacity)

    def test_capacity_calculation(self):
        """Verify capacity calculations against member_limit."""
        group = AccommodationGroup.objects.create(
            title="Limited Group",
            destination_city="Hyderabad",
            location_within_city="Gachibowli",
            initiated_by=self.user1,
            current_owner=self.user1,
            member_limit=1,
        )
        self.assertTrue(group.has_available_capacity)

        # Add active member
        AccommodationMembership.objects.create(
            group=group,
            user=self.user1,
            planned_stay=Range(date(2027, 6, 1), date(2027, 7, 1), bounds='[)'),
            status=MembershipStatus.ACTIVE,
        )
        self.assertEqual(group.active_members_count, 1)
        self.assertFalse(group.has_available_capacity)


class MembershipConstraintTests(TestCase):
    def setUp(self):
        self.user1 = User.objects.create_user(
            username='user1',
            enrollment_number='2024BCSE101',
            gender='M',
            phone_number='9876543210',
            password='password123',
            verified=True,
        )
        self.user2 = User.objects.create_user(
            username='user2',
            enrollment_number='2024BCSE102',
            gender='F',
            phone_number='9876543211',
            password='password123',
            verified=True,
        )
        self.group1 = AccommodationGroup.objects.create(
            title="Group 1 Bengaluru",
            destination_city="Bengaluru",
            location_within_city="HSR",
            initiated_by=self.user1,
        )
        self.group2 = AccommodationGroup.objects.create(
            title="Group 2 Hyderabad",
            destination_city="Hyderabad",
            location_within_city="Gachibowli",
            initiated_by=self.user2,
        )

    def test_one_active_membership_per_group_user(self):
        """A user cannot hold two active memberships in the same group."""
        AccommodationMembership.objects.create(
            group=self.group1,
            user=self.user1,
            planned_stay=Range(date(2027, 6, 1), date(2027, 7, 1), bounds='[)'),
            status=MembershipStatus.ACTIVE,
        )
        with self.assertRaises(IntegrityError):
            AccommodationMembership.objects.create(
                group=self.group1,
                user=self.user1,
                planned_stay=Range(date(2027, 7, 1), date(2027, 8, 1), bounds='[)'),
                status=MembershipStatus.ACTIVE,
            )

    def test_cross_group_overlapping_stay_exclusion_constraint(self):
        """A user cannot hold overlapping active memberships in DIFFERENT groups (ExclusionConstraint)."""
        AccommodationMembership.objects.create(
            group=self.group1,
            user=self.user1,
            planned_stay=Range(date(2027, 6, 1), date(2027, 7, 15), bounds='[)'),
            status=MembershipStatus.ACTIVE,
        )
        # Attempt to create overlapping active membership in group 2 for same user
        with self.assertRaises(IntegrityError):
            AccommodationMembership.objects.create(
                group=self.group2,
                user=self.user1,
                planned_stay=Range(date(2027, 7, 1), date(2027, 8, 1), bounds='[)'),
                status=MembershipStatus.ACTIVE,
            )

    def test_adjacent_half_open_stays_are_allowed(self):
        """Consecutive adjacent stays [June 1, July 1) and [July 1, August 1) do NOT overlap."""
        mem1 = AccommodationMembership.objects.create(
            group=self.group1,
            user=self.user1,
            planned_stay=Range(date(2027, 6, 1), date(2027, 7, 1), bounds='[)'),
            status=MembershipStatus.ACTIVE,
        )
        mem2 = AccommodationMembership.objects.create(
            group=self.group2,
            user=self.user1,
            planned_stay=Range(date(2027, 7, 1), date(2027, 8, 1), bounds='[)'),
            status=MembershipStatus.ACTIVE,
        )
        self.assertIsNotNone(mem1.id)
        self.assertIsNotNone(mem2.id)

    def test_different_users_can_have_overlapping_stays(self):
        """Different users can have overlapping dates in the same or different groups."""
        mem1 = AccommodationMembership.objects.create(
            group=self.group1,
            user=self.user1,
            planned_stay=Range(date(2027, 6, 1), date(2027, 7, 1), bounds='[)'),
            status=MembershipStatus.ACTIVE,
        )
        mem2 = AccommodationMembership.objects.create(
            group=self.group1,
            user=self.user2,
            planned_stay=Range(date(2027, 6, 1), date(2027, 7, 1), bounds='[)'),
            status=MembershipStatus.ACTIVE,
        )
        self.assertIsNotNone(mem1.id)
        self.assertIsNotNone(mem2.id)

    def test_left_membership_does_not_block_new_active_stay(self):
        """A former membership (status='LEFT') does not prevent overlapping active memberships."""
        AccommodationMembership.objects.create(
            group=self.group1,
            user=self.user1,
            planned_stay=Range(date(2027, 6, 1), date(2027, 7, 1), bounds='[)'),
            status=MembershipStatus.LEFT,
        )
        # Should succeed because condition is status='ACTIVE'
        mem2 = AccommodationMembership.objects.create(
            group=self.group2,
            user=self.user1,
            planned_stay=Range(date(2027, 6, 1), date(2027, 7, 1), bounds='[)'),
            status=MembershipStatus.ACTIVE,
        )
        self.assertIsNotNone(mem2.id)


class ApplicationAndObjectionTests(TestCase):
    def setUp(self):
        self.user1 = User.objects.create_user(
            username='user1',
            enrollment_number='2024BCSE101',
            gender='M',
            phone_number='9876543210',
            password='password123',
            verified=True,
        )
        self.user2 = User.objects.create_user(
            username='user2',
            enrollment_number='2024BCSE102',
            gender='F',
            phone_number='9876543211',
            password='password123',
            verified=True,
        )
        self.group = AccommodationGroup.objects.create(
            title="Manyata Group",
            destination_city="Bengaluru",
            location_within_city="Nagavara",
            initiated_by=self.user1,
        )

    def test_one_unresolved_application_constraint(self):
        """Applicant can only have one PENDING or WAITLISTED request per group."""
        AccommodationJoinApplication.objects.create(
            group=self.group,
            applicant=self.user2,
            requested_stay=Range(date(2027, 6, 1), date(2027, 7, 1), bounds='[)'),
            status=ApplicationStatus.PENDING,
        )
        with self.assertRaises(IntegrityError):
            AccommodationJoinApplication.objects.create(
                group=self.group,
                applicant=self.user2,
                requested_stay=Range(date(2027, 6, 1), date(2027, 7, 1), bounds='[)'),
                status=ApplicationStatus.PENDING,
            )

    def test_objection_round_unique_constraint(self):
        """A member can only lodge one objection per application per objection round."""
        mem = AccommodationMembership.objects.create(
            group=self.group,
            user=self.user1,
            planned_stay=Range(date(2027, 6, 1), date(2027, 7, 1), bounds='[)'),
            status=MembershipStatus.ACTIVE,
        )
        app = AccommodationJoinApplication.objects.create(
            group=self.group,
            applicant=self.user2,
            requested_stay=Range(date(2027, 6, 1), date(2027, 7, 1), bounds='[)'),
            status=ApplicationStatus.PENDING,
            objection_round=1,
        )
        AccommodationApplicationObjection.objects.create(
            application=app,
            objector_membership=mem,
            reason="Schedule discrepancy",
            objection_round=1,
        )
        # Duplicate in round 1 raises IntegrityError
        with self.assertRaises(IntegrityError):
            AccommodationApplicationObjection.objects.create(
                application=app,
                objector_membership=mem,
                reason="Another reason",
                objection_round=1,
            )


class ReputationModelTests(TestCase):
    def setUp(self):
        self.user1 = User.objects.create_user(
            username='user1',
            enrollment_number='2024BCSE101',
            gender='M',
            phone_number='9876543210',
            password='password123',
            verified=True,
        )
        self.user2 = User.objects.create_user(
            username='user2',
            enrollment_number='2024BCSE102',
            gender='F',
            phone_number='9876543211',
            password='password123',
            verified=True,
        )
        self.group = AccommodationGroup.objects.create(
            title="Reputation Test Group",
            destination_city="Bengaluru",
            location_within_city="HSR",
            initiated_by=self.user1,
        )
        self.subject_mem = AccommodationMembership.objects.create(
            group=self.group,
            user=self.user1,
            planned_stay=Range(date(2027, 6, 1), date(2027, 7, 1), bounds='[)'),
            status=MembershipStatus.LEFT,
        )
        self.reviewer_mem = AccommodationMembership.objects.create(
            group=self.group,
            user=self.user2,
            planned_stay=Range(date(2027, 6, 1), date(2027, 7, 1), bounds='[)'),
            status=MembershipStatus.ACTIVE,
        )

    def test_single_invitation_per_subject_reviewer(self):
        """Only one review invitation per subject departure per reviewer user."""
        from django.utils import timezone
        from datetime import timedelta
        deadline = timezone.now() + timedelta(days=30)
        AccommodationReviewInvitation.objects.create(
            subject_membership=self.subject_mem,
            reviewer_user=self.user2,
            qualifying_membership=self.reviewer_mem,
            deadline_at=deadline,
        )
        with self.assertRaises(IntegrityError):
            AccommodationReviewInvitation.objects.create(
                subject_membership=self.subject_mem,
                reviewer_user=self.user2,
                qualifying_membership=self.reviewer_mem,
                deadline_at=deadline,
            )

    def test_review_creation_and_linking(self):
        """A review links 1-to-1 with its invitation."""
        from django.utils import timezone
        from datetime import timedelta
        invite = AccommodationReviewInvitation.objects.create(
            subject_membership=self.subject_mem,
            reviewer_user=self.user2,
            qualifying_membership=self.reviewer_mem,
            deadline_at=timezone.now() + timedelta(days=30),
        )
        review = AccommodationReview.objects.create(
            invitation=invite,
            reliability_score=5,
            communication_score=4,
            cooperation_score=5,
            respect_score=5,
            private_context="Excellent roommate."
        )
        self.assertIsNotNone(review.id)
        self.assertEqual(invite.review, review)
