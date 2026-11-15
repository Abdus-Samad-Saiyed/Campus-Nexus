from datetime import date
from django.test import Client, TestCase
from django.urls import reverse
from psycopg.types.range import Range

from accounts.models import User
from accommodate.models import (
    AccommodationGroup,
    AccommodationJoinApplication,
    AccommodationMembership,
    AdmissionMode,
    ApplicationStatus,
    MembershipStatus,
    RecruitmentStatus,
)
from accommodate.services.group_service import create_group


class AccommodateViewsIntegrationTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.owner = User.objects.create_user(
            username="2023BCSE001",
            email="owner@nitk.ac.in",
            enrollment_number="2023BCSE001",
            first_name="Alice",
            last_name="Owner",
        )
        self.applicant = User.objects.create_user(
            username="2023BCSE002",
            email="applicant@nitk.ac.in",
            enrollment_number="2023BCSE002",
            first_name="Bob",
            last_name="Applicant",
        )
        self.other_user = User.objects.create_user(
            username="2023BCSE003",
            email="other@nitk.ac.in",
            enrollment_number="2023BCSE003",
            first_name="Charlie",
            last_name="Other",
        )

        self.stay_period = Range(date(2027, 6, 1), date(2027, 7, 31), bounds='[]')
        self.group = create_group(
            creator=self.owner,
            title="Bangalore Tech Interns 2027",
            destination_city="Bengaluru",
            location_within_city="Koramangala",
            creator_planned_stay=self.stay_period,
            member_limit=3,
            admission_mode=AdmissionMode.MANUAL,
        )

    def test_group_list_view_authenticated(self):
        self.client.force_login(self.applicant)
        response = self.client.get(reverse('accommodate:group_list'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'accommodate/group_list.html')
        self.assertIn('groups', response.context)
        self.assertEqual(len(response.context['groups']), 1)

    def test_group_search_partial_htmx(self):
        self.client.force_login(self.applicant)
        response = self.client.get(
            reverse('accommodate:group_search_partial'),
            {'city': 'Bengaluru'},
            HTTP_HX_REQUEST='true'
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'accommodate/partials/group_results.html')

    def test_group_create_view_get(self):
        self.client.force_login(self.applicant)
        response = self.client.get(reverse('accommodate:group_create'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'accommodate/group_create.html')
        self.assertIn('popular_stays', response.context)

    def test_group_create_view_post_success(self):
        self.client.force_login(self.applicant)
        post_data = {
            'title': 'Hyderabad Summer PG',
            'destination_city': 'Hyderabad',
            'location_within_city': 'Gachibowli',
            'lodging_name': 'Zolo Amber',
            'start_date': '2027-08-01',
            'end_date': '2027-09-30',
            'member_limit': '4',
            'admission_mode': 'AUTO',
            'estimated_cost_amount': '11000',
            'estimated_cost_basis': 'PER_PERSON_MONTH',
            'description': 'Near Microsoft campus.',
        }
        response = self.client.post(reverse('accommodate:group_create'), post_data)
        self.assertEqual(response.status_code, 302)

        new_group = AccommodationGroup.objects.filter(title='Hyderabad Summer PG').first()
        self.assertIsNotNone(new_group)
        self.assertEqual(new_group.initiated_by, self.applicant)
        self.assertEqual(new_group.current_owner, self.applicant)
        self.assertEqual(new_group.admission_mode, AdmissionMode.AUTO)

    def test_group_detail_view(self):
        self.client.force_login(self.applicant)
        response = self.client.get(reverse('accommodate:group_detail', args=[self.group.id]))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'accommodate/group_detail.html')
        self.assertEqual(response.context['group'], self.group)
        self.assertFalse(response.context['viewer_is_owner'])

    def test_apply_view_post_and_workflow(self):
        self.client.force_login(self.applicant)
        post_data = {
            'start_date': '2027-06-01',
            'end_date': '2027-07-31',
            'message': 'Hi Alice! Would love to stay together.',
        }
        response = self.client.post(reverse('accommodate:apply', args=[self.group.id]), post_data)
        self.assertEqual(response.status_code, 302)

        app = AccommodationJoinApplication.objects.filter(
            group=self.group, applicant=self.applicant
        ).first()
        self.assertIsNotNone(app)
        self.assertEqual(app.status, ApplicationStatus.PENDING)

        # Owner views manage console
        self.client.force_login(self.owner)
        manage_resp = self.client.get(reverse('accommodate:group_manage', args=[self.group.id]))
        self.assertEqual(manage_resp.status_code, 200)
        self.assertTemplateUsed(manage_resp, 'accommodate/group_manage.html')
        self.assertEqual(len(manage_resp.context['inbox']['pending_applications']), 1)

        # Owner approves application
        approve_resp = self.client.post(reverse('accommodate:approve_application', args=[app.id]))
        self.assertEqual(approve_resp.status_code, 302)

        app.refresh_from_db()
        self.assertEqual(app.status, ApplicationStatus.APPROVED)
        self.assertIsNotNone(app.membership)
        self.assertEqual(app.membership.status, MembershipStatus.ACTIVE)

    def test_manage_permission_denied_for_non_owner(self):
        self.client.force_login(self.other_user)
        response = self.client.get(reverse('accommodate:group_manage', args=[self.group.id]))
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('accommodate:group_detail', args=[self.group.id]))

    def test_my_groups_view(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse('accommodate:my_groups'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'accommodate/my_groups.html')
        self.assertEqual(len(response.context['active_memberships']), 1)

    def test_my_reviews_view(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse('accommodate:my_reviews'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'accommodate/my_reviews.html')
