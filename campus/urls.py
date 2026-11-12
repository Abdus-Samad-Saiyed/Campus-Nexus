"""Root URL configuration for COORDINATE."""

import os
from django.contrib import admin
from django.contrib.auth import views as auth_views

from django.conf import settings
from django.urls import include, path, re_path
from accounts.demo import enter_guest_demo, exit_guest_demo, is_guest_demo
from django.views.generic.base import RedirectView

from accounts.forms import EnrollmentAuthenticationForm
from accounts.views import (
    custom_login_view,
    landing,
    passkey_auth_challenge,
    passkey_auth_verify,
    passkey_reg_challenge,
    passkey_reg_verify,
    passkey_delete,
    verify_security_otp,
    verify_security_step_up,
    passkey_step_up_verify,
    service_catalogue,
    two_factor_verify,
    acknowledge_announcement,
    forget_app_remembered_user,
)
from campus.operations import (
    admin_announcements_view,
    admin_create_announcement,
    admin_toggle_announcement,
    admin_delete_announcement,
    admin_delete_account,
    admin_resolve_physical_verification,
    admin_update_phone,
    export_applications_csv,
    export_rides_csv,
    export_users_csv,
    health_check,
    manage_superusers,
    operational_dashboard,
)
from campus.services import service_gateway_view
from campus.views import (
    about_view,
    contact_view,
    contributors_view,
    feedback_view,
    nominate_contributor,
    remove_contributor,
    offline_view,
)
from campus.learn_more import learn_more_view
from campus.agentic import robots_txt, llms_txt, ard_json

# Enforce platform 2FA and single-session on Django admin
admin.site.login = custom_login_view


from django.contrib.auth.views import PasswordChangeView
from django.contrib.sessions.models import Session
from django.utils import timezone
from django.http import Http404, HttpResponseForbidden, FileResponse
from django.contrib.auth.decorators import login_required
from accounts.models import log_audit_event, User
from accounts.two_factor import hash_otp
from coordinate.policies import can_view_profile_field

class CustomPasswordChangeView(PasswordChangeView):
    template_name = 'registration/password_change_form.html'
    success_url = '/password_change/done/'

    def form_valid(self, form):
        response = super().form_valid(form)
        user = self.request.user
        current_key = self.request.session.session_key
        for s in Session.objects.filter(expire_date__gt=timezone.now()):
            try:
                data = s.get_decoded()
                if str(data.get('_auth_user_id')) == str(user.id) and s.session_key != current_key:
                    s.delete()
            except Exception:
                pass
        if current_key:
            from accounts.models import UserPlatformSession
            from accounts.utils import enforce_platform_session_login
            UserPlatformSession.objects.filter(user=user).delete()
            enforce_platform_session_login(self.request, user)

        from accounts.utils import broadcast_user_session_revocation
        from django.db import transaction
        transaction.on_commit(lambda: broadcast_user_session_revocation(user.id, "Password changed."))

        log_audit_event(
            event_type='SECURITY_SETTINGS_UPDATED',
            actor_enrollment=user.enrollment_number,
            description='Password changed via web password_change view; concurrent sessions revoked',
        )
        return response

@login_required
def serve_protected_media(request, path):
    if is_guest_demo(request):
        return HttpResponseForbidden("Access to student media is strictly prohibited in Guest Demo Mode.")
    safe_path = os.path.normpath(path).lstrip('/\\')
    if safe_path.startswith('..') or os.path.isabs(safe_path):
        raise Http404("Invalid media path.")

    media_root = os.path.realpath(settings.MEDIA_ROOT)
    full_path = os.path.realpath(os.path.join(media_root, safe_path))
    if not full_path.startswith(media_root + os.sep) and full_path != media_root:
        raise Http404("Invalid media path.")

    if not os.path.isfile(full_path):
        raise Http404("Media file not found.")

    if is_guest_demo(request):
        return HttpResponseForbidden("Access to student media is strictly prohibited in Guest Demo Mode.")

    normalized_rel = safe_path.replace('\\', '/')
    if normalized_rel.startswith('profiles/'):
        owner = User.objects.filter(profile_photo=normalized_rel).first()
        if not owner:
            raise Http404("Media file not found.")
        # Self or staff can always view own profile photo
        if request.user == owner or request.user.is_staff or request.user.is_superuser:
            return FileResponse(open(full_path, 'rb'))
        # In Early Launch Mode (temporary database), permit profile photo viewing unless hidden by owner
        if getattr(settings, 'EARLY_LAUNCH_MODE', False) and not getattr(owner, 'hide_profile_photo', False):
            return FileResponse(open(full_path, 'rb'))
        # Standard privacy policy check
        if can_view_profile_field(request.user, owner, 'profile_photo'):
            return FileResponse(open(full_path, 'rb'))
        return HttpResponseForbidden("Access to this profile media is restricted.")

    # Sensitive non-profile media (e.g. ID verification cards) requires verified student or staff
    if not (request.user.is_staff or getattr(request.user, 'verified', False) or getattr(settings, 'EARLY_LAUNCH_MODE', False)):
        return HttpResponseForbidden("Verified student authentication required.")

    return FileResponse(open(full_path, 'rb'))


def favicon_view(request):
    """Directly serve favicon.ico with image/x-icon content type and HTTP 200."""
    favicon_path = os.path.join(settings.BASE_DIR, 'static', 'favicon.ico')
    if os.path.exists(favicon_path):
        return FileResponse(open(favicon_path, 'rb'), content_type='image/x-icon')
    return RedirectView.as_view(url=settings.STATIC_URL + 'favicon.ico', permanent=False)(request)


def service_worker_view(request):
    """Serve root /sw.js with Service-Worker-Allowed: / for full origin scope."""
    sw_path = os.path.join(settings.BASE_DIR, 'static', 'sw.js')
    if os.path.exists(sw_path):
        response = FileResponse(open(sw_path, 'rb'), content_type='application/javascript')
        response['Service-Worker-Allowed'] = '/'
        response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        return response
    raise Http404("Service worker not found")


def manifest_view(request):
    """Directly serve manifest.json with application/manifest+json content type and HTTP 200."""
    manifest_path = os.path.join(settings.BASE_DIR, 'static', 'manifest.json')
    if os.path.exists(manifest_path):
        response = FileResponse(open(manifest_path, 'rb'), content_type='application/manifest+json')
        response['Cache-Control'] = 'no-cache, must-revalidate'
        return response
    raise Http404("Manifest not found")


urlpatterns = [
    # Django admin & Operational Dashboard
    path('admin/', admin.site.urls),
    path('operations/', operational_dashboard, name='operational_dashboard'),
    path('operations/superusers/', manage_superusers, name='manage_superusers'),
    path('operations/announcements/', admin_announcements_view, name='admin_announcements'),
    path('operations/announcements/create/', admin_create_announcement, name='admin_create_announcement'),
    path('operations/announcements/<int:announcement_id>/toggle/', admin_toggle_announcement, name='admin_toggle_announcement'),
    path('operations/announcements/<int:announcement_id>/delete/', admin_delete_announcement, name='admin_delete_announcement'),
    path('announcements/<int:announcement_id>/acknowledge/', acknowledge_announcement, name='acknowledge_announcement'),
    path('operations/users/<int:user_id>/update-phone/', admin_update_phone, name='admin_update_phone'),
    path('operations/users/<int:user_id>/delete/', admin_delete_account, name='admin_delete_account'),
    path('operations/physical-verification/<int:request_id>/resolve/', admin_resolve_physical_verification, name='admin_resolve_physical_verification'),
    path('operations/export/rides/', export_rides_csv, name='export_rides_csv'),
    path('operations/export/users/', export_users_csv, name='export_users_csv'),
    path('operations/export/applications/', export_applications_csv, name='export_applications_csv'),

    # Autonomous Agentic & Discovery Endpoints
    path('robots.txt', robots_txt, name='robots_txt'),
    path('llms.txt', llms_txt, name='llms_txt'),
    path('.well-known/ard.json', ard_json, name='ard_json'),

    # System Health Check, PWA Manifest & Root Favicon
    path('favicon.ico', favicon_view, name='favicon'),
    path('manifest.json', manifest_view, name='pwa_manifest'),
    path('sw.js', service_worker_view, name='service_worker'),
    path('offline/', offline_view, name='pwa_offline'),
    path('health/', health_check, name='health_check'),
    path('healthz/', health_check, name='healthz'),

    # Campus Nexus Guest Demo
    path('demo/', enter_guest_demo, name='enter_guest_demo'),
    path('demo/exit/', exit_guest_demo, name='exit_guest_demo'),
    path('guest-demo/', enter_guest_demo, name='guest_demo_alias'),

    # Campus Nexus Platform Shell, Info & Community
    path('', landing, name='landing'),
    path('about/', about_view, name='about'),
    path('contact/', contact_view, name='contact'),
    path('contributors/', contributors_view, name='contributors'),
    path('contributors/nominate/', nominate_contributor, name='nominate_contributor'),
    path('contributors/<int:contributor_id>/remove/', remove_contributor, name='remove_contributor'),
    path('feedback/', feedback_view, name='feedback'),
    path('report-bug/', feedback_view, name='report_bug'),
    # Learn More interactive technical guides
    path('learn-more/', learn_more_view, name='learn_more_default'),
    path('learn-more/<slug:topic>/', learn_more_view, name='learn_more'),
    path('services/', service_catalogue, name='service_catalogue'),
    path('services/<slug:slug>/', service_gateway_view, name='service_gateway'),

    # Versioned REST API (v1)
    path('api/v1/', include('campus.api.urls')),

    # 2-Factor Authentication & Biometric / Windows Hello Passkey Engine
    path('login/', custom_login_view, name='login'),
    path('login/2fa/', two_factor_verify, name='two_factor_verify'),
    path('auth/passkey/authenticate/challenge/', passkey_auth_challenge, name='passkey_auth_challenge'),
    path('auth/passkey/authenticate/verify/', passkey_auth_verify, name='passkey_auth_verify'),
    path('auth/passkey/register/challenge/', passkey_reg_challenge, name='passkey_reg_challenge'),
    path('auth/passkey/register/verify/', passkey_reg_verify, name='passkey_reg_verify'),
    path('auth/passkey/<int:credential_id>/delete/', passkey_delete, name='passkey_delete'),
    path('auth/app-forget/', forget_app_remembered_user, name='forget_app_remembered_user'),
    path(
        'logout/',
        auth_views.LogoutView.as_view(),
        name='logout',
    ),
    path(
        'password_change/',
        CustomPasswordChangeView.as_view(),
        name='password_change',
    ),
    path('auth/passkey/step-up/', verify_security_step_up, name='passkey_step_up'),
    path('auth/passkey/step-up-verify/', passkey_step_up_verify, name='passkey_step_up_verify'),
    path('otp/verify-security/', verify_security_otp, name='verify_security_otp'),
    path(
        'password_change/done/',
        auth_views.PasswordChangeDoneView.as_view(
            template_name='registration/password_change_done.html',
        ),
        name='password_change_done',
    ),

    # Accounts app (dashboard, profile, apply, friends, analytics…)
    path('', include('accounts.urls')),

    # Coordinate app (rides) & clean alias
    path('rides/', RedirectView.as_view(url='/coordinate/', permanent=False), name='rides_redirect'),
    path('coordinate/', include('coordinate.urls')),
]





urlpatterns += [
    re_path(r'^media/(?P<path>.*)$', serve_protected_media, name='serve_protected_media'),
]

