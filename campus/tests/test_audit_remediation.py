"""Tests verifying remediation for the Authenticated Browser Audit findings.

Covers:
- PWA My Rides shortcut URL resolution and fallback redirect
- Directory search partial AJAX rendering and full rendering
- Password recovery copy harmonization (Email recovery vs In-Person helpdesk)
- About page graduated student verification copy
- My Rides mobile card view and desktop table view
"""

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from coordinate.models import RideRequest

User = get_user_model()


class AuditRemediationTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username="2024BCSE001",
            enrollment_number="2024BCSE001",
            first_name="Farhan",
            last_name="Akhtar",
            phone_number="9876543201",
            password="StrongPassword123!",
            is_staff=False,
            verified=True,
            branch="CSE",
            degree="B.Tech",
            joining_year=2024,
            gender="M",
        )
        self.peer = User.objects.create_user(
            username="2024BCSE002",
            enrollment_number="2024BCSE002",
            first_name="Zoya",
            last_name="Akhtar",
            phone_number="9876543202",
            password="StrongPassword123!",
            is_staff=False,
            verified=True,
            branch="CSE",
            degree="B.Tech",
            joining_year=2024,
            gender="F",
        )

    def test_pwa_my_rides_shortcut_redirect(self):
        """Ensure /rides/my/ redirects to /coordinate/my-rides/ with HTTP 302."""
        self.client.force_login(self.user)
        response = self.client.get('/rides/my/?source=pwa_shortcut', follow=False)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith('/coordinate/my-rides/'))

        # Follow redirect should reach 200
        follow_response = self.client.get('/rides/my/?source=pwa_shortcut', follow=True)
        self.assertEqual(follow_response.status_code, 200)

    def test_directory_search_full_and_ajax_partial(self):
        """Ensure full GET returns base template, and AJAX GET returns partial fragment without base template."""
        self.client.force_login(self.user)

        # Full page load
        full_res = self.client.get(reverse('user_search') + '?q=Zoya')
        self.assertEqual(full_res.status_code, 200)
        self.assertContains(full_res, '<!DOCTYPE html>')
        self.assertContains(full_res, 'id="student-search-results"')
        self.assertContains(full_res, 'Zoya')

        # AJAX partial request
        ajax_res = self.client.get(
            reverse('user_search') + '?q=Zoya',
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(ajax_res.status_code, 200)
        ajax_content = ajax_res.content.decode('utf-8')
        # Does NOT include outer HTML boilerplate / base template
        self.assertNotIn('<!DOCTYPE html>', ajax_content)
        self.assertNotIn('<html', ajax_content)
        self.assertNotIn('<nav class="navbar', ajax_content)
        # Includes results and count marker
        self.assertIn('id="partial-student-count"', ajax_content)
        self.assertIn('Zoya', ajax_content)

    def test_forgot_password_copy_harmonization(self):
        """Ensure /forgot-password/ copy consistently refers to Email Recovery and Helpdesk."""
        response = self.client.get(reverse('forgot_password'))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')

        self.assertIn("Email Recovery Code", content)
        self.assertIn("In-Person Helpdesk Verification", content)
        self.assertNotIn("Reset your password via Phone OTP", content)
        self.assertNotIn("6-Digit Phone OTP", content)
        self.assertNotIn("Lost SIM / In-Person Desk", content)

    def test_about_page_verification_copy_harmonization(self):
        """Ensure /about/ reflects Graduated Student Verification rather than absolute unverified claims."""
        response = self.client.get(reverse('about'))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')

        self.assertIn("Graduated Student Verification", content)
        self.assertNotIn("No anonymous trolls or unverified outsiders", content)

    def test_my_rides_mobile_cards_and_desktop_table(self):
        """Ensure /coordinate/my-rides/ contains both mobile card container and desktop table container."""
        self.client.force_login(self.user)

        # Create a past ride and participant
        from django.utils import timezone
        import datetime
        from coordinate.models import RideParticipant, ParticipantStatus
        past_time = timezone.now() - datetime.timedelta(days=2)
        ride = RideRequest.objects.create(
            creator=self.user,
            route="AIRPORT",
            departure_time=past_time,
            total_spots=3,
            status="COMPLETED",
        )
        RideParticipant.objects.create(
            ride=ride,
            user=self.user,
            status=ParticipantStatus.CONFIRMED,
        )

        response = self.client.get(reverse('my_rides'))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')

        # Check mobile card view container exists
        self.assertIn('d-md-none d-flex flex-column gap-3', content)
        # Check desktop table container exists
        self.assertIn('d-none d-md-block', content)
        self.assertIn('table-responsive', content)
        self.assertIn('table table-dark', content)

    def test_announcement_no_duplicate_replica_and_action_highlight(self):
        """Ensure an unacknowledged announcement is proclaimed once and not duplicated in the feed, action button is highlighted, and no raw URL is printed."""
        from accounts.models import Announcement, AnnouncementReceipt
        ann = Announcement.objects.create(
            title="Bring the platform to life!",
            content="Campus Nexus is up and running... turn on notifications and set up a passkey.",
            priority="NOTICE",
            target_type="ALL",
            action_url="/settings/",
            action_label="Setup Notifications and Passkeys",
            is_active=True,
        )

        self.client.force_login(self.user)
        res = self.client.get(reverse('dashboard'))
        self.assertEqual(res.status_code, 200)
        content = res.content.decode('utf-8')

        # 1. Proclaimed banner is present with highlighted action button
        self.assertIn('id="firstLoginAnnouncementBanner"', content)
        self.assertIn('btn-info text-dark', content)
        self.assertIn('Setup Notifications and Passkeys', content)
        self.assertIn('Acknowledge', content)

        # 2. Lower feed does NOT duplicate the proclaimed announcement
        self.assertNotIn('Priority Campus Announcements', content)
        self.assertEqual(res.context['feed_announcements'], [])

        # 3. Acknowledge announcement
        ack_res = self.client.post(
            reverse('acknowledge_announcement', kwargs={'announcement_id': ann.id}),
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(ack_res.status_code, 200)

        # 4. Next visit: Proclamation banner is gone, feed shows announcement without raw URL and with Acknowledged badge
        res_after = self.client.get(reverse('dashboard'))
        self.assertEqual(res_after.status_code, 200)
        content_after = res_after.content.decode('utf-8')

        self.assertNotIn('id="firstLoginAnnouncementBanner"', content_after)
        self.assertIn('Priority Campus Announcements', content_after)
        self.assertIn('Acknowledged', content_after)
        # Verify no raw action_url text floating in feed
        self.assertNotIn('<span class="text-secondary small">/settings/</span>', content_after)

    def test_clockwork_watermark_default_off_markup_and_controls(self):
        """Ensure clockwork watermark defaults to off/hidden on initial markup and control selectors."""
        response = self.client.get(reverse('landing'))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')

        # Watermark container has display: none by default
        self.assertIn('id="clockwork-watermark"', content)
        self.assertIn('style="display: none;"', content)

        # Visual controls dropdown and modal display 'Off / Hidden (Default)'
        self.assertIn('Off / Hidden (Default)', content)

    def test_announcement_settings_and_internal_links_stay_in_same_tab(self):
        """Ensure /settings/ and relative internal URLs do not have target='_blank', while external URLs do."""
        from accounts.models import Announcement
        self.client.force_login(self.user)

        # 1. Internal /settings/ announcement
        internal_ann = Announcement.objects.create(
            title="Internal Route Test",
            content="Testing settings link target.",
            priority="NOTICE",
            target_type="ALL",
            action_url="/settings/",
            action_label="Setup Notifications and Passkeys",
            is_active=True,
        )

        res = self.client.get(reverse('dashboard'))
        self.assertEqual(res.status_code, 200)
        content = res.content.decode('utf-8')

        # Find the proclamation link tag
        self.assertIn('href="/settings/"', content)
        # Must NOT have target="_blank"
        self.assertNotIn('href="/settings/" target="_blank"', content)
        # Uses gear icon
        self.assertIn('bi-gear-fill', content)

        # 2. External announcement
        internal_ann.delete()
        external_ann = Announcement.objects.create(
            title="External Resource",
            content="Testing external link target.",
            priority="NOTICE",
            target_type="ALL",
            action_url="https://external-resource.example.com/survey",
            action_label="Open Survey",
            is_active=True,
        )

        res_ext = self.client.get(reverse('dashboard'))
        self.assertEqual(res_ext.status_code, 200)
        content_ext = res_ext.content.decode('utf-8')

        # External URL MUST have target="_blank" rel="noopener noreferrer"
        self.assertIn('href="https://external-resource.example.com/survey"', content_ext)
        self.assertIn('target="_blank" rel="noopener noreferrer"', content_ext)
        self.assertIn('bi-box-arrow-up-right', content_ext)

    def test_dashboard_announcement_compact_title_only_and_limit(self):
        """Ensure dashboard renders compact title rows, limits feed to 3, and links to /announcements/."""
        from accounts.models import Announcement, AnnouncementReceipt
        # Create 5 active announcements
        announcements = []
        for i in range(1, 6):
            ann = Announcement.objects.create(
                title=f"Broadcast Notice Number {i}",
                content=f"Detailed body paragraph explaining announcement #{i} in depth with extra details.",
                priority="INFO",
                target_type="ALL",
                is_active=True,
            )
            announcements.append(ann)

        # Mark all as acknowledged so none are held in proclamation hero
        for ann in announcements:
            AnnouncementReceipt.objects.create(user=self.user, announcement=ann, dismissed=True)

        self.client.force_login(self.user)
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')

        # Check that feed_announcements is limited to 3
        feed_items = response.context['feed_announcements']
        self.assertEqual(len(feed_items), 3)
        self.assertTrue(response.context['has_more_announcements'])
        self.assertEqual(response.context['total_active_announcements_count'], 5)

        # Dashboard contains styled announcement card with link to detail view
        self.assertIn('announcement-compact-row', content)
        self.assertIn(reverse('announcement_detail', kwargs={'announcement_id': feed_items[0].id}), content)
        self.assertIn(feed_items[0].title, content)

        # Dashboard includes link to all announcements directory
        self.assertIn(reverse('announcement_list'), content)
        self.assertIn('View all 5 campus announcements', content)

    def test_announcement_list_and_detail_views(self):
        """Ensure /announcements/ and /announcements/<id>/ render full details, metadata, and allow acknowledgment."""
        from accounts.models import Announcement, AnnouncementReceipt
        ann = Announcement.objects.create(
            title="Important Maintenance Window",
            content="Campus servers will undergo maintenance at midnight.\nPlease save your active rides.",
            priority="WARNING",
            target_type="ALL",
            action_url="/settings/",
            action_label="Configure Alerts",
            is_active=True,
        )

        self.client.force_login(self.user)

        # 1. Directory page: /announcements/
        list_res = self.client.get(reverse('announcement_list'))
        self.assertEqual(list_res.status_code, 200)
        list_content = list_res.content.decode('utf-8')
        self.assertIn("Campus Announcements", list_content)
        self.assertIn("Important Maintenance Window", list_content)
        self.assertIn(reverse('announcement_detail', kwargs={'announcement_id': ann.id}), list_content)

        # 2. Detail page: /announcements/<id>/
        detail_res = self.client.get(reverse('announcement_detail', kwargs={'announcement_id': ann.id}))
        self.assertEqual(detail_res.status_code, 200)
        detail_content = detail_res.content.decode('utf-8')
        self.assertIn("Important Maintenance Window", detail_content)
        self.assertIn("Campus servers will undergo maintenance at midnight.", detail_content)
        self.assertIn("Configure Alerts", detail_content)
        self.assertIn('href="/settings/"', detail_content)
        # Internal link must NOT have target="_blank"
        self.assertNotIn('href="/settings/" target="_blank"', detail_content)
        # Acknowledge button present because user has not acknowledged
        self.assertIn('Acknowledge Notice', detail_content)

        # 3. Acknowledge notice via detail page form action
        ack_res = self.client.post(
            reverse('acknowledge_announcement', kwargs={'announcement_id': ann.id}),
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(ack_res.status_code, 200)

        # 4. Detail page now reflects acknowledged status
        detail_res2 = self.client.get(reverse('announcement_detail', kwargs={'announcement_id': ann.id}))
        self.assertEqual(detail_res2.status_code, 200)
        detail_content2 = detail_res2.content.decode('utf-8')
        self.assertIn("Notice Acknowledged", detail_content2)
        self.assertNotIn('id="formAckDetail"', detail_content2)

    def test_admin_edit_announcement_workflow(self):
        """Ensure staff/admin can view and edit announcements, updating content and recording audit events."""
        from accounts.models import Announcement, AuditEvent
        staff_admin = User.objects.create_user(
            username="2020BCSE999",
            enrollment_number="2020BCSE999",
            first_name="Admin",
            last_name="Staff",
            phone_number="9876543999",
            password="AdminPassword123!",
            is_staff=True,
            is_superuser=True,
            verified=True,
        )

        ann = Announcement.objects.create(
            title="Initial Title Announcement",
            content="Initial notice content body.",
            priority="INFO",
            target_type="ALL",
            action_url="/settings/",
            action_label="Old Label",
            is_active=True,
            created_by=staff_admin,
        )

        # Non-staff user cannot access edit
        self.client.force_login(self.user)
        non_staff_res = self.client.get(reverse('admin_edit_announcement', kwargs={'announcement_id': ann.id}))
        # user_passes_test redirects to login or operations
        self.assertEqual(non_staff_res.status_code, 302)

        # Staff admin logs in and opens edit page
        self.client.force_login(staff_admin)
        get_res = self.client.get(reverse('admin_edit_announcement', kwargs={'announcement_id': ann.id}))
        self.assertEqual(get_res.status_code, 200)
        get_content = get_res.content.decode('utf-8')
        self.assertIn("Edit Platform", get_content)
        self.assertIn("Initial Title Announcement", get_content)
        self.assertIn("Initial notice content body.", get_content)

        # Staff admin submits edit
        post_data = {
            'title': "Updated Revised Notice Title",
            'priority': "CRITICAL",
            'content': "Updated high priority message body for all students.",
            'action_label': "Go To Security",
            'action_url': "/settings/",
            'target_type': "ALL",
            'is_active': "on",
        }
        post_res = self.client.post(
            reverse('admin_edit_announcement', kwargs={'announcement_id': ann.id}),
            data=post_data,
            follow=True,
        )
        self.assertEqual(post_res.status_code, 200)

        # Verify DB updated
        ann.refresh_from_db()
        self.assertEqual(ann.title, "Updated Revised Notice Title")
        self.assertEqual(ann.priority, "CRITICAL")
        self.assertEqual(ann.content, "Updated high priority message body for all students.")
        self.assertEqual(ann.action_label, "Go To Security")

        # Verify AuditEvent logged
        audit = AuditEvent.objects.filter(event_type="ANNOUNCEMENT_UPDATED", target_id=str(ann.id)).first()
        self.assertIsNotNone(audit)
        self.assertEqual(audit.actor_enrollment, staff_admin.enrollment_number)

    def test_dedicated_operational_audit_trail_pagination_and_access(self):
        """Dedicated audit trail page restricts access, paginates 25 per page, and supports filtering."""
        from accounts.models import AuditEvent

        # 1. Anonymous user redirected to login
        res_anon = self.client.get(reverse('operational_audit_trail'))
        self.assertEqual(res_anon.status_code, 302)
        self.assertIn('/login/', res_anon.url)

        # 2. Non-staff student denied access
        self.client.force_login(self.user)
        res_user = self.client.get(reverse('operational_audit_trail'))
        self.assertEqual(res_user.status_code, 302)

        # 3. Superuser granted access
        admin_user = User.objects.create_superuser(
            username="admin_audit",
            enrollment_number="ADMIN001",
            first_name="Admin",
            last_name="Super",
            password="AdminPassword123!",
            is_staff=True,
            is_superuser=True,
            verified=True,
        )
        self.client.force_login(admin_user)

        # Seed 30 Audit Events
        for i in range(30):
            AuditEvent.objects.create(
                event_type="LOGIN_SUCCESS" if i % 2 == 0 else "SECURITY_SETTINGS_UPDATED",
                actor_enrollment=f"ENROLL_{i:03d}",
                description=f"Test audit action sequence #{i}",
                target_id=str(i),
            )

        # Page 1: 25 items
        res_page1 = self.client.get(reverse('operational_audit_trail'))
        self.assertEqual(res_page1.status_code, 200)
        self.assertEqual(len(res_page1.context['audit_events']), 25)
        self.assertEqual(res_page1.context['page_obj'].paginator.num_pages, 2)

        # Page 2: remaining items (5 + any from other tests)
        res_page2 = self.client.get(reverse('operational_audit_trail') + '?page=2')
        self.assertEqual(res_page2.status_code, 200)
        self.assertGreaterEqual(len(res_page2.context['audit_events']), 5)

        # Filter by event_type
        res_filter = self.client.get(reverse('operational_audit_trail') + '?event_type=LOGIN_SUCCESS')
        self.assertEqual(res_filter.status_code, 200)
        for evt in res_filter.context['audit_events']:
            self.assertEqual(evt.event_type, "LOGIN_SUCCESS")

        # Search by actor
        res_search = self.client.get(reverse('operational_audit_trail') + '?q=ENROLL_015')
        self.assertEqual(res_search.status_code, 200)
        self.assertEqual(len(res_search.context['audit_events']), 1)
        self.assertEqual(res_search.context['audit_events'][0].actor_enrollment, "ENROLL_015")

        # Verify "View" button appears in audit trail rows
        self.assertContains(res_search, "View")
        self.assertContains(res_search, reverse('operational_audit_detail', args=[res_search.context['audit_events'][0].id]))

    def test_operational_audit_detail_view(self):
        """Dedicated audit record detail view displays Title, Timestamp, Actor, Event, and Full Details."""
        from accounts.models import AuditEvent

        evt = AuditEvent.objects.create(
            event_type="LOGIN_SUCCESS",
            actor_enrollment=self.user.enrollment_number,
            description="Student signed in successfully (2-week app session: False)",
            target_id="SESSION_99",
        )

        detail_url = reverse('operational_audit_detail', args=[evt.id])

        # 1. Anonymous user redirected to login
        res_anon = self.client.get(detail_url)
        self.assertEqual(res_anon.status_code, 302)
        self.assertIn('/login/', res_anon.url)

        # 2. Regular student user denied access
        self.client.force_login(self.user)
        res_user = self.client.get(detail_url)
        self.assertEqual(res_user.status_code, 302)

        # 3. Superuser access
        admin_user = User.objects.create_superuser(
            username="admin_audit_detail",
            enrollment_number="ADMIN_DET_01",
            first_name="Detail",
            last_name="Super",
            password="AdminPassword123!",
            is_staff=True,
            is_superuser=True,
            verified=True,
        )
        self.client.force_login(admin_user)

        res_admin = self.client.get(detail_url)
        self.assertEqual(res_admin.status_code, 200)
        self.assertTemplateUsed(res_admin, "nexus/operations_audit_detail.html")
        self.assertEqual(res_admin.context['audit_event'], evt)
        self.assertEqual(res_admin.context['actor_user'], self.user)

        # 4. Verify contents: Title, Timestamp, Actor, Event, and full Description details
        content = res_admin.content.decode('utf-8')
        self.assertIn("Login Success", content)
        self.assertIn(self.user.enrollment_number, content)
        self.assertIn("SESSION_99", content)
        self.assertIn("Student signed in successfully (2-week app session: False)", content)
        self.assertIn("Immutable Ledger Record", content)

        # 5. Non-existent event returns 404
        res_404 = self.client.get(reverse('operational_audit_detail', args=[9999999]))
        self.assertEqual(res_404.status_code, 404)



