from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Contributor, Friendship, User

from .models import (
    RideJoinRequest,
    RideMessage,
    RideParticipant,
    RideRequest,
    RideReview,
    RideWaitlist,
)


class RideWorkflowTests(TestCase):

    def setUp(self):

        self.creator = User.objects.create_user(
            username='Creator',
            enrollment_number='2024BCSE001',
            gender='M',
            phone_number='9999999999',
            password='password123',
            verified=True,
        )

        self.other = User.objects.create_user(
            username='Other',
            enrollment_number='2024BCSE002',
            gender='M',
            phone_number='9999999998',
            password='password123',
            verified=True,
        )

        self.girl = User.objects.create_user(
            username='Girl',
            enrollment_number='2024BCSE003',
            gender='F',
            phone_number='9999999997',
            password='password123',
            verified=True,
        )

    def future_time(self, days=1):

        return timezone.now() + timedelta(days=days)

    def create_ride(self, total_spots=2, visibility='MIXED', route='NIT_TO_AIRPORT'):

        ride = RideRequest.objects.create(
            creator=self.creator,
            route=route,
            visibility=visibility,
            departure_time=self.future_time(),
            total_spots=total_spots
        )

        RideParticipant.objects.create(
            ride=ride,
            user=self.creator
        )

        ride.sync_status()

        return ride

    def test_create_ride_adds_creator_and_capacity_one_is_full(self):

        self.client.force_login(self.creator)

        response = self.client.post(
            reverse('create_ride'),
            {
                'route': 'NIT_TO_AIRPORT',
                'visibility': 'MIXED',
                'departure_time': self.future_time().strftime(
                    '%Y-%m-%dT%H:%M'
                ),
                'total_spots': '1',
            }
        )

        self.assertRedirects(response, reverse('ride_list'))

        ride = RideRequest.objects.get()

        self.assertEqual(ride.status, 'FULL')
        self.assertEqual(ride.participant_count(), 1)
        self.assertTrue(
            RideParticipant.objects.filter(
                ride=ride,
                user=self.creator
            ).exists()
        )

    def test_full_ride_reopens_when_participant_leaves(self):

        ride = self.create_ride(total_spots=2)

        RideParticipant.objects.create(
            ride=ride,
            user=self.other
        )

        ride.sync_status()
        ride.refresh_from_db()

        self.assertEqual(ride.status, 'FULL')

        self.client.force_login(self.other)

        self.client.post(
            reverse('leave_ride', args=[ride.id])
        )

        ride.refresh_from_db()

        self.assertEqual(ride.status, 'OPEN')
        self.assertEqual(ride.participant_count(), 1)

    def test_join_rejects_full_completed_restricted_and_get_requests(self):

        full_ride = self.create_ride(total_spots=1)

        self.client.force_login(self.other)

        self.assertEqual(
            self.client.get(
                reverse('join_ride', args=[full_ride.id])
            ).status_code,
            405
        )

        self.client.post(
            reverse('join_ride', args=[full_ride.id])
        )

        self.assertFalse(
            RideParticipant.objects.filter(
                ride=full_ride,
                user=self.other
            ).exists()
        )

        completed = self.create_ride(total_spots=3)
        completed.status = 'COMPLETED'
        completed.save(update_fields=['status'])

        self.client.post(
            reverse('join_ride', args=[completed.id])
        )

        self.assertFalse(
            RideParticipant.objects.filter(
                ride=completed,
                user=self.other
            ).exists()
        )

        girls_only = self.create_ride(
            total_spots=3,
            visibility='GIRLS'
        )

        self.client.post(
            reverse('join_ride', args=[girls_only.id])
        )

        self.assertFalse(
            RideParticipant.objects.filter(
                ride=girls_only,
                user=self.other
            ).exists()
        )

    def test_duplicate_and_multiple_active_ride_join_are_prevented(self):

        ride = self.create_ride(total_spots=3)

        self.client.force_login(self.other)

        # Duplicate join requests create only 1 pending request
        self.client.post(reverse('join_ride', args=[ride.id]))
        self.client.post(reverse('join_ride', args=[ride.id]))

        self.assertEqual(
            RideJoinRequest.objects.filter(
                ride=ride,
                user=self.other,
                status='PENDING'
            ).count(),
            1
        )

        # Host approves the join request
        join_req = RideJoinRequest.objects.get(ride=ride, user=self.other)
        self.client.force_login(self.creator)
        self.client.post(reverse('approve_join_request', args=[ride.id, join_req.id]))

        self.assertEqual(
            RideParticipant.objects.filter(
                ride=ride,
                user=self.other
            ).count(),
            1
        )

        second_ride = RideRequest.objects.create(
            creator=self.girl,
            route='AIRPORT_TO_NIT',
            visibility='MIXED',
            departure_time=self.future_time(),
            total_spots=3
        )

        RideParticipant.objects.create(
            ride=second_ride,
            user=self.girl
        )

        # Student with active ride cannot request second ride
        self.client.force_login(self.other)
        self.client.post(reverse('join_ride', args=[second_ride.id]))

        self.assertFalse(
            RideParticipant.objects.filter(
                ride=second_ride,
                user=self.other
            ).exists()
        )
        self.assertFalse(
            RideJoinRequest.objects.filter(
                ride=second_ride,
                user=self.other
            ).exists()
        )

    def test_empty_ride_deleted_after_last_participant_leaves(self):

        ride = self.create_ride(total_spots=1)

        self.client.force_login(self.creator)
        self.client.post(reverse('leave_ride', args=[ride.id]))

        ride.refresh_from_db()
        self.assertEqual(ride.status, 'CANCELLED')

    def test_creator_can_kick_participant_and_cannot_kick_self(self):

        ride = self.create_ride(total_spots=2)

        other_participant = RideParticipant.objects.create(
            ride=ride,
            user=self.other
        )

        creator_participant = RideParticipant.objects.get(
            ride=ride,
            user=self.creator
        )

        self.client.force_login(self.creator)
        self.client.post(
            reverse(
                'kick_participant',
                args=[ride.id, other_participant.id]
            )
        )

        self.assertFalse(
            RideParticipant.objects.filter(
                id=other_participant.id
            ).exists()
        )

        self.client.post(
            reverse(
                'kick_participant',
                args=[ride.id, creator_participant.id]
            )
        )

        self.assertTrue(
            RideParticipant.objects.filter(
                id=creator_participant.id
            ).exists()
        )

    def test_completed_ride_stays_completed_on_save(self):

        ride = self.create_ride(total_spots=2)
        ride.status = 'COMPLETED'
        ride.save()
        ride.save()
        ride.refresh_from_db()

        self.assertEqual(ride.status, 'COMPLETED')

    def test_ride_detail_private_and_chat_participant_only(self):

        ride = self.create_ride(total_spots=2)
        outsider = User.objects.create_user(
            username='Outsider',
            enrollment_number='2024BCSE004',
            gender='M',
            phone_number='9999999996',
            password='password123'
        )

        self.client.force_login(outsider)

        self.assertEqual(
            self.client.get(
                reverse('ride_detail', args=[ride.id])
            ).status_code,
            404
        )

        self.assertEqual(
            self.client.post(
                reverse('send_message', args=[ride.id]),
                {'content': 'hello'}
            ).status_code,
            403
        )

        self.client.force_login(self.creator)
        self.client.post(
            reverse('send_message', args=[ride.id]),
            {'content': 'Meet at gate 1'}
        )

        self.assertEqual(RideMessage.objects.count(), 1)
        self.assertContains(
            self.client.get(
                reverse('ride_detail', args=[ride.id])
            ),
            'Meet at gate 1'
        )

    def test_review_requires_completed_participated_ride(self):

        ride = self.create_ride(total_spots=2)

        RideParticipant.objects.create(
            ride=ride,
            user=self.other
        )

        self.client.force_login(self.creator)

        self.assertEqual(
            self.client.get(
                reverse(
                    'review_user',
                    args=[ride.id, self.other.id]
                )
            ).status_code,
            403
        )

        ride.status = 'COMPLETED'
        ride.save(update_fields=['status'])

        response = self.client.post(
            reverse('review_user', args=[ride.id, self.other.id]),
            {
                'rating': '5',
                'complaint': 'Helpful'
            }
        )

        self.assertRedirects(
            response,
            reverse('ride_detail', args=[ride.id])
        )
        self.assertEqual(RideReview.objects.count(), 1)

        self.client.post(
            reverse('review_user', args=[ride.id, self.other.id]),
            {
                'rating': '4',
                'complaint': ''
            }
        )

        self.assertEqual(RideReview.objects.count(), 1)

    def test_friends_only_ride_allows_friend(self):

        Friendship.objects.create(
            user1=self.creator,
            user2=self.other
        )

        ride = self.create_ride(
            total_spots=2,
            visibility='FRIENDS'
        )

        self.client.force_login(self.other)
        self.client.post(reverse('join_ride', args=[ride.id]))

        # Host approves the join request
        join_req = RideJoinRequest.objects.get(ride=ride, user=self.other)
        self.assertEqual(join_req.status, 'PENDING')
        self.client.force_login(self.creator)
        self.client.post(reverse('approve_join_request', args=[ride.id, join_req.id]))

        self.assertTrue(
            RideParticipant.objects.filter(
                ride=ride,
                user=self.other
            ).exists()
        )

    def test_ride_join_request_approval_and_decline_workflow(self):
        ride = self.create_ride(total_spots=3)

        # Student requests to join
        self.client.force_login(self.other)
        resp = self.client.post(reverse('join_ride', args=[ride.id]))
        self.assertRedirects(resp, reverse('ride_list'))

        join_req = RideJoinRequest.objects.get(ride=ride, user=self.other)
        self.assertEqual(join_req.status, 'PENDING')
        self.assertFalse(RideParticipant.objects.filter(ride=ride, user=self.other).exists())

        # Non-creator cannot decline or approve
        outsider = User.objects.create_user(
            username='OutsiderJoin',
            enrollment_number='2024BCSE099',
            gender='M',
            phone_number='9999999988',
            password='password123',
            verified=True,
        )
        self.client.force_login(outsider)
        self.assertIn(
            self.client.post(reverse('approve_join_request', args=[ride.id, join_req.id])).status_code,
            [403, 404]
        )

        # Host declines the request
        self.client.force_login(self.creator)
        self.client.post(reverse('decline_join_request', args=[ride.id, join_req.id]))
        join_req.refresh_from_db()
        self.assertEqual(join_req.status, 'DECLINED')
        self.assertFalse(RideParticipant.objects.filter(ride=ride, user=self.other).exists())

        # Student cancels a new request
        self.client.force_login(self.other)
        self.client.post(reverse('join_ride', args=[ride.id]))
        self.assertTrue(RideJoinRequest.objects.filter(ride=ride, user=self.other, status='PENDING').exists())
        self.client.post(reverse('cancel_join_request', args=[ride.id]))
        self.assertFalse(RideJoinRequest.objects.filter(ride=ride, user=self.other, status='PENDING').exists())

        # Host approves a final request
        self.client.post(reverse('join_ride', args=[ride.id]))
        final_req = RideJoinRequest.objects.get(ride=ride, user=self.other, status='PENDING')
        self.client.force_login(self.creator)
        self.client.post(reverse('approve_join_request', args=[ride.id, final_req.id]))
        final_req.refresh_from_db()
        self.assertEqual(final_req.status, 'APPROVED')
        self.assertTrue(RideParticipant.objects.filter(ride=ride, user=self.other).exists())

    def test_immediate_grievance_review_for_kicked_and_left_passengers(self):
        ride = self.create_ride(total_spots=3)
        RideParticipant.objects.create(ride=ride, user=self.other)

        # Host kicks participant while ride is OPEN
        self.client.force_login(self.creator)
        other_part = RideParticipant.objects.get(ride=ride, user=self.other)
        self.client.post(reverse('kick_participant', args=[ride.id, other_part.id]))
        ride.refresh_from_db()
        self.assertIn(self.other, ride.kicked_users.all())

        # Host can review immediately with grievance without waiting for ride COMPLETED
        self.assertEqual(ride.status, 'OPEN')
        rev_resp = self.client.post(
            reverse('review_user', args=[ride.id, self.other.id]),
            {
                'rating': '2',
                'punctuality_rating': '1',
                'communication_rating': '2',
                'reliability_rating': '1',
                'grievance': 'LATE',
                'complaint': 'Arrived 25 minutes late without heads-up.',
            }
        )
        self.assertRedirects(rev_resp, reverse('ride_detail', args=[ride.id]))
        self.assertTrue(RideReview.objects.filter(ride=ride, reviewed_user=self.other, grievance='LATE').exists())

        # Second student leaves voluntarily
        voluntary_user = User.objects.create_user(
            username='VoluntaryLeaver',
            enrollment_number='2024BCSE088',
            gender='M',
            phone_number='9999999987',
            password='password123'
        )
        RideParticipant.objects.create(ride=ride, user=voluntary_user)
        self.client.force_login(voluntary_user)
        self.client.post(reverse('leave_ride', args=[ride.id]))
        ride.refresh_from_db()
        self.assertIn(voluntary_user, ride.left_users.all())

        # Host reviews departed user immediately with NO_SHOW grievance
        self.client.force_login(self.creator)
        self.client.post(
            reverse('review_user', args=[ride.id, voluntary_user.id]),
            {
                'rating': '1',
                'punctuality_rating': '1',
                'communication_rating': '1',
                'reliability_rating': '1',
                'grievance': 'NO_SHOW',
                'complaint': 'Left ride 5 minutes before departure.',
            }
        )
        self.assertTrue(RideReview.objects.filter(ride=ride, reviewed_user=voluntary_user, grievance='NO_SHOW').exists())

    def test_ride_density_telemetry_and_ordering(self):
        self.client.force_login(self.creator)

        # Create multiple rides across different dates
        r1 = self.create_ride(total_spots=2, route='NIT_TO_AIRPORT')
        r2 = RideRequest.objects.create(
            creator=self.creator,
            route='LAL_CHOWK_TO_NIT',
            visibility='MIXED',
            departure_time=timezone.now() + timedelta(days=2),
            total_spots=4
        )
        RideParticipant.objects.create(ride=r2, user=self.creator)

        # Check ride list defaults to all upcoming rides (un-filtered, 14 days by default)
        resp = self.client.get(reverse('ride_list'))
        self.assertEqual(resp.status_code, 200)
        self.assertIn('density_data', resp.context)
        self.assertEqual(len(resp.context['density_data']), 14)
        self.assertEqual(resp.context['bar_min_width'], '38px')
        self.assertContains(resp, 'min-width: 38px')

        # Check configurable density telemetry views: 30 days and 3 months (90 days) with bar thinning
        resp_30 = self.client.get(reverse('ride_list') + '?density_view=30')
        self.assertEqual(resp_30.status_code, 200)
        self.assertEqual(len(resp_30.context['density_data']), 30)
        self.assertEqual(resp_30.context['bar_min_width'], '18px')
        self.assertContains(resp_30, 'min-width: 18px')

        resp_90 = self.client.get(reverse('ride_list') + '?density_view=90')
        self.assertEqual(resp_90.status_code, 200)
        self.assertEqual(len(resp_90.context['density_data']), 90)
        self.assertEqual(resp_90.context['bar_min_width'], '8px')
        self.assertContains(resp_90, 'min-width: 8px')

        # Check order_by parameters
        for order_param in ['time_asc', 'time_desc', 'spots_desc', 'created_desc']:
            order_resp = self.client.get(reverse('ride_list') + f'?order_by={order_param}')
            self.assertEqual(order_resp.status_code, 200)

        # Check queue_only filter
        q_resp = self.client.get(reverse('ride_list') + '?queue_only=1')
        self.assertEqual(q_resp.status_code, 200)

        # Check date filter application and verify reset button URL is clean (no bare ampersand 404)
        today_str = timezone.now().strftime('%Y-%m-%d')
        date_filtered_resp = self.client.get(reverse('ride_list') + f'?date_from={today_str}&date_to={today_str}')
        self.assertEqual(date_filtered_resp.status_code, 200)
        self.assertTrue(date_filtered_resp.context['has_date_filter'])
        self.assertContains(date_filtered_resp, 'View All Dates (Reset Date Filter)')
        self.assertNotContains(date_filtered_resp, '/coordinate/&order_by')
        self.assertContains(date_filtered_resp, '/coordinate/?order_by')

    def test_contributor_nomination_peer_workflow(self):
        # Ordinary non-admin user cannot nominate or add
        self.client.force_login(self.other)
        nom_resp = self.client.post(
            reverse('nominate_contributor'),
            {
                'name': 'Other Nominee',
                'enrollment_number': self.creator.enrollment_number,
                'role': 'Core Maintainer',
                'bio': 'Built routing algorithm.',
            }
        )
        self.assertRedirects(nom_resp, reverse('contributors'))
        self.assertFalse(Contributor.objects.filter(name='Other Nominee').exists())

        # Admin can add contributor
        self.creator.is_staff = True
        self.creator.save()
        self.client.force_login(self.creator)
        success_nom = self.client.post(
            reverse('nominate_contributor'),
            {
                'name': 'Test Other Contributor',
                'enrollment_number': self.other.enrollment_number,
                'role': 'Full-stack Contributor',
                'bio': 'Implemented real-time chat telemetry.',
                'github_url': 'https://github.com/other',
            }
        )
        self.assertRedirects(success_nom, reverse('contributors'))
        contrib = Contributor.objects.filter(user=self.other, nominated_by=self.creator).first()
        self.assertIsNotNone(contrib)

        # Admin can remove contributor
        remove_resp = self.client.post(reverse('remove_contributor', args=[contrib.id]))
        self.assertRedirects(remove_resp, reverse('contributors'))
        self.assertFalse(Contributor.objects.filter(id=contrib.id).exists())

    def test_unverified_student_cannot_create_ride(self):
        """Unverified student cannot create a ride."""
        unverified_user = User.objects.create_user(
            username='2024BCSE077',
            enrollment_number='2024BCSE077',
            gender='M',
            phone_number='9999999977',
            password='password123',
            verified=False,
            is_active=True,
        )
        self.client.force_login(unverified_user)
        dep_time = (timezone.now() + timedelta(days=1)).strftime('%Y-%m-%dT%H:%M')
        resp = self.client.post(reverse('create_ride'), {
            'route': 'NIT_TO_AIRPORT',
            'visibility': 'MIXED',
            'departure_time': dep_time,
            'total_spots': 3,
            'disclose_identity': 'on',
        })
        self.assertRedirects(resp, reverse('ride_list'))
        self.assertFalse(RideRequest.objects.filter(creator=unverified_user).exists())

    def test_reschedule_concluded_ride_fails(self):
        """Rescheduling a ride whose departure + 2 hours buffer has elapsed is blocked."""
        past_ride = RideRequest.objects.create(
            creator=self.creator,
            route='NIT_TO_AIRPORT',
            visibility='MIXED',
            departure_time=timezone.now() - timedelta(hours=3),
            total_spots=3,
            status='OPEN',
        )
        RideParticipant.objects.create(ride=past_ride, user=self.creator)
        self.client.force_login(self.creator)
        new_dep_time = (timezone.now() + timedelta(days=2)).strftime('%Y-%m-%dT%H:%M')
        resp = self.client.post(reverse('reschedule_ride', args=[past_ride.id]), {
            'departure_time': new_dep_time,
        })
        self.assertRedirects(resp, reverse('ride_detail', args=[past_ride.id]))
        past_ride.refresh_from_db()
        self.assertNotEqual(past_ride.departure_time.strftime('%Y-%m-%d'), (timezone.now() + timedelta(days=2)).strftime('%Y-%m-%d'))

    def test_create_ride_defaults_to_sharing_contact_and_no_optional_label(self):
        """Ride creation defaults share_contact to True and does not designate it as optional."""
        self.client.force_login(self.creator)
        get_resp = self.client.get(reverse('create_ride'))
        self.assertEqual(get_resp.status_code, 200)
        content = get_resp.content.decode('utf-8')
        self.assertNotIn('(optional, valid only during active ride)', content)
        self.assertIn('checked', content)

        dep_time = (timezone.now() + timedelta(days=1)).strftime('%Y-%m-%dT%H:%M')
        post_resp = self.client.post(reverse('create_ride'), {
            'route': 'NIT_TO_AIRPORT',
            'visibility': 'MIXED',
            'departure_time': dep_time,
            'total_spots': 3,
            'disclose_identity': 'on',
            'share_contact': 'on',
        })
        self.assertRedirects(post_resp, reverse('ride_list'))
        ride = RideRequest.objects.filter(creator=self.creator).latest('id')
        p_row = RideParticipant.all_rows.get(ride=ride, user=self.creator)
        self.assertTrue(p_row.share_contact_details)

    def test_join_ride_share_contact_policy(self):
        """Consenting to contact sharing is mandatory to create and join rides; in-ride toggle is removed."""
        ride = self.create_ride(total_spots=3)
        self.client.force_login(self.other)

        # 1. Attempting to join with share_contact='false' fails because contact consent is mandatory
        resp_unconsented = self.client.post(reverse('join_ride', args=[ride.id]), {
            'disclose_identity': 'true',
            'share_contact': 'false',
        })
        self.assertRedirects(resp_unconsented, reverse('ride_detail', args=[ride.id]))
        self.assertFalse(RideParticipant.all_rows.filter(ride=ride, user=self.other).exists())

        # 2. Joining with share_contact='true' succeeds and records mandatory consent
        resp = self.client.post(reverse('join_ride', args=[ride.id]), {
            'disclose_identity': 'true',
            'share_contact': 'true',
        })
        self.assertRedirects(resp, reverse('ride_list'))
        p_row = RideParticipant.all_rows.get(ride=ride, user=self.other)
        self.assertTrue(p_row.share_contact_details)

        # 3. Ride detail page has zero in-ride toggle options for phone sharing
        detail_resp = self.client.get(reverse('ride_detail', args=[ride.id]))
        self.assertEqual(detail_resp.status_code, 200)
        self.assertNotIn(b'Hide Phone', detail_resp.content)
        self.assertNotIn(b'Phone sharing:', detail_resp.content)

        # 4. Attempting to create ride without share_contact fails form validation
        dep_time = (timezone.now() + timedelta(days=1)).strftime('%Y-%m-%dT%H:%M')
        create_resp = self.client.post(reverse('create_ride'), {
            'route': 'NIT_TO_AIRPORT',
            'visibility': 'MIXED',
            'departure_time': dep_time,
            'total_spots': 3,
            'disclose_identity': 'on',
            'share_contact': '',  # explicitly unconsented
        })
        self.assertEqual(create_resp.status_code, 200)
        self.assertFormError(create_resp.context['form'], 'share_contact', 'You must consent to share your contact details with confirmed co-passengers to create a ride.')

    def test_ride_list_seamless_quick_select_date_transition(self):
        """Next/Prev and date strip in /coordinate provide seamless HTMX partial swaps."""
        self.client.force_login(self.creator)

        # 1. Standard GET renders full shell with container
        resp_full = self.client.get(reverse('ride_list'))
        self.assertEqual(resp_full.status_code, 200)
        self.assertTemplateUsed(resp_full, 'coordinate/ride_list.html')
        self.assertTemplateUsed(resp_full, 'coordinate/partials/ride_list_content.html')
        content_full = resp_full.content.decode('utf-8')
        self.assertIn('id="coordinate-rides-container"', content_full)
        self.assertIn('data-no-transition="true"', content_full)
        self.assertIn('hx-target="#coordinate-rides-container"', content_full)
        self.assertIn('hx-get="?date_from=', content_full)

        # 2. HTMX GET renders only partial content for seamless transition
        target_date = (timezone.now() + timedelta(days=1)).strftime('%Y-%m-%d')
        resp_htmx = self.client.get(
            f"{reverse('ride_list')}?date_from={target_date}&date_to={target_date}",
            HTTP_HX_REQUEST='true'
        )
        self.assertEqual(resp_htmx.status_code, 200)
        self.assertTemplateUsed(resp_htmx, 'coordinate/partials/ride_list_content.html')
        self.assertTemplateNotUsed(resp_htmx, 'coordinate/ride_list.html')
        content_htmx = resp_htmx.content.decode('utf-8')
        self.assertNotIn('<!DOCTYPE html>', content_htmx)
        self.assertIn(f'date_from={target_date}', content_htmx)
        self.assertIn('Quick Select Date', content_htmx)
        self.assertIn('Next <i class="bi bi-chevron-right"></i>', content_htmx)

    def test_alter_capacity_increase_promotes_waitlist(self):
        """Increasing ride capacity opens spots and automatically promotes waitlisted students."""
        self.client.force_login(self.creator)
        ride = self.create_ride(total_spots=2)
        # 1. Other user is confirmed participant (ride now full: 2/2)
        RideParticipant.objects.create(ride=ride, user=self.other)
        ride.sync_status()
        ride.refresh_from_db()
        self.assertEqual(ride.participant_count(), 2)
        self.assertEqual(ride.status, 'FULL')

        # 2. Third user joins waitlist
        user_c = User.objects.create_user(
            username='WaitlistC',
            enrollment_number='2024BCSE888',
            email='waitlist_cap@nitsri.ac.in',
            phone_number='9876543888',
            password='password123',
            gender='M',
            branch='CSE',
            is_active=True,
            verified=True,
        )
        self.client.force_login(user_c)
        resp_w = self.client.post(reverse('join_waitlist', args=[ride.id]))
        self.assertTrue(RideWaitlist.objects.filter(ride=ride, user=user_c).exists())

        # 3. Host alters capacity from 2 to 3
        self.client.force_login(self.creator)
        resp = self.client.post(reverse('alter_ride_capacity', args=[ride.id]), {'total_spots': 3})
        self.assertRedirects(resp, reverse('ride_detail', args=[ride.id]))

        ride.refresh_from_db()
        self.assertEqual(ride.total_spots, 3)
        # user_c was promoted to confirmed passenger!
        self.assertFalse(RideWaitlist.objects.filter(ride=ride, user=user_c).exists())
        self.assertTrue(RideParticipant.objects.filter(ride=ride, user=user_c, status='CONFIRMED').exists())
        self.assertEqual(ride.participant_count(), 3)
        self.assertEqual(ride.status, 'FULL')

    def test_alter_capacity_decrease_safe_no_kicks_needed(self):
        """Host can decrease capacity if new capacity is >= current confirmed member count."""
        self.client.force_login(self.creator)
        ride = self.create_ride(total_spots=5)
        # Other is confirmed participant (total 2 members: host + other)
        RideParticipant.objects.create(ride=ride, user=self.other)
        ride.sync_status()
        ride.refresh_from_db()
        self.assertEqual(ride.participant_count(), 2)

        # Host reduces capacity from 5 to 3 (which is >= 2 members)
        self.client.force_login(self.creator)
        resp = self.client.post(reverse('alter_ride_capacity', args=[ride.id]), {'total_spots': 3})
        self.assertRedirects(resp, reverse('ride_detail', args=[ride.id]))

        ride.refresh_from_db()
        self.assertEqual(ride.total_spots, 3)
        self.assertEqual(ride.participant_count(), 2)
        self.assertEqual(ride.status, 'OPEN')

    def test_alter_capacity_decrease_requires_selecting_kicks(self):
        """Decreasing capacity below current member count without selecting kicks is rejected."""
        self.client.force_login(self.creator)
        ride = self.create_ride(total_spots=3)
        # Other joins (now 2 members: host + other)
        RideParticipant.objects.create(ride=ride, user=self.other)
        ride.sync_status()
        ride.refresh_from_db()
        self.assertEqual(ride.participant_count(), 2)

        # Host tries to reduce capacity to 1 without specifying kick_users
        self.client.force_login(self.creator)
        resp = self.client.post(reverse('alter_ride_capacity', args=[ride.id]), {'total_spots': 1})
        self.assertRedirects(resp, reverse('ride_detail', args=[ride.id]))

        # Ride capacity remained unchanged because kicks were required
        ride.refresh_from_db()
        self.assertEqual(ride.total_spots, 3)
        self.assertEqual(ride.participant_count(), 2)

        # Decreasing capacity with passenger selected to kick succeeds
        resp2 = self.client.post(reverse('alter_ride_capacity', args=[ride.id]), {
            'total_spots': 1,
            'kick_users': [self.other.id],
        })
        self.assertRedirects(resp2, reverse('ride_detail', args=[ride.id]))

        ride.refresh_from_db()
        self.assertEqual(ride.total_spots, 1)
        self.assertEqual(ride.participant_count(), 1)
        self.assertEqual(ride.status, 'FULL')
        # other is now KICKED
        self.assertIn(self.other, ride.kicked_users.all())

    def test_non_host_cannot_alter_capacity(self):
        """Non-host member or stranger cannot alter ride capacity."""
        self.client.force_login(self.creator)
        ride = self.create_ride(total_spots=3)

        self.client.force_login(self.other)
        resp = self.client.post(reverse('alter_ride_capacity', args=[ride.id]), {'total_spots': 4})
        self.assertRedirects(resp, reverse('ride_detail', args=[ride.id]))

        ride.refresh_from_db()
        self.assertEqual(ride.total_spots, 3)

    def test_alter_capacity_range_and_completed_guards(self):
        """Capacity must be between 1 and 6, and cannot be altered on completed rides."""
        self.client.force_login(self.creator)
        ride = self.create_ride(total_spots=3)
        # Other is confirmed participant so confirmed count is 2 (host + other)
        RideParticipant.objects.create(ride=ride, user=self.other)
        ride.sync_status()
        ride.refresh_from_db()
        self.assertEqual(ride.participant_count(), 2)

        self.client.force_login(self.creator)
        # Range invalid (0 or 7)
        resp_zero = self.client.post(reverse('alter_ride_capacity', args=[ride.id]), {'total_spots': 0})
        self.assertRedirects(resp_zero, reverse('ride_detail', args=[ride.id]))
        ride.refresh_from_db()
        self.assertEqual(ride.total_spots, 3)

        resp_seven = self.client.post(reverse('alter_ride_capacity', args=[ride.id]), {'total_spots': 7})
        self.assertRedirects(resp_seven, reverse('ride_detail', args=[ride.id]))
        ride.refresh_from_db()
        self.assertEqual(ride.total_spots, 3)

        # Host cannot kick themselves when kicks are needed (reducing 2 members to 1 capacity)
        resp_self_kick = self.client.post(reverse('alter_ride_capacity', args=[ride.id]), {
            'total_spots': 1,
            'kick_users': [self.creator.id],
        })
        self.assertRedirects(resp_self_kick, reverse('ride_detail', args=[ride.id]))
        ride.refresh_from_db()
        self.assertEqual(ride.total_spots, 3)

    def test_ride_list_displays_host_name_and_enrollment_and_joined_riders(self):
        """Verify that students browsing rides see the host's full name & enrollment and joined riders' full names & enrollments."""
        self.creator.first_name = 'Farhan'
        self.creator.last_name = 'Qureshi'
        self.creator.save()

        self.other.first_name = 'Raju'
        self.other.last_name = 'Rastogi'
        self.other.save()

        ride = self.create_ride(total_spots=4)
        RideParticipant.objects.create(ride=ride, user=self.other)
        ride.sync_status()

        # Spectator browsing rides
        spectator = User.objects.create_user(
            username='Spectator',
            enrollment_number='2024BCSE999',
            first_name='Rancho',
            last_name='Chanchad',
            gender='M',
            phone_number='9999900000',
            password='password123',
            verified=True,
        )
        self.client.force_login(spectator)

        resp = self.client.get(reverse('ride_list'))
        self.assertEqual(resp.status_code, 200)

        # Host name and enrollment displayed
        self.assertContains(resp, 'Farhan Qureshi (2024BCSE001)')
        # Joined rider name and enrollment displayed
        self.assertContains(resp, 'Raju Rastogi (2024BCSE002)')

        # API serialization also contains host and participants
        from campus.api.serializers import serialize_ride
        serialized = serialize_ride(ride, request_user=spectator)
        self.assertEqual(serialized['creator']['enrollment_number'], '2024BCSE001')
        self.assertEqual(serialized['creator']['name'], 'Farhan Qureshi')

        participant_names = [p['name'] for p in serialized['participants']]
        participant_enrollments = [p['enrollment_number'] for p in serialized['participants']]
        self.assertIn('Raju Rastogi', participant_names)
        self.assertIn('2024BCSE002', participant_enrollments)
