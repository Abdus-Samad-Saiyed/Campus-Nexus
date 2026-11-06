"""Context processors for accounts application."""

from django.conf import settings

from accounts.demo import is_guest_demo


def early_launch_context(request):
    """Expose early_launch_mode, pending application state, incoming friend requests count, admin pending applications count, and session idle timeout across all templates."""
    pending_app = None
    friend_requests_count = 0
    pending_apps_count = 0
    user = getattr(request, 'user', None)
    if user and user.is_authenticated:
        if not getattr(user, 'verified', False):
            try:
                from accounts.models import AccountApplication
                pending_app = AccountApplication.objects.filter(
                    enrollment_number=user.enrollment_number,
                    approved=False,
                ).first()
            except Exception:
                pass
        try:
            from accounts.models import FriendRequest
            friend_requests_count = FriendRequest.objects.filter(receiver=user).count()
        except Exception:
            pass

        if getattr(user, 'is_staff', False) or getattr(user, 'is_superuser', False):
            try:
                from accounts.models import AccountApplication
                pending_apps_count = AccountApplication.objects.filter(approved=False).count()
            except Exception:
                pass

    return {
        'is_guest_demo': is_guest_demo(request),
        'early_launch_mode': getattr(settings, 'EARLY_LAUNCH_MODE', False),
        'pending_permanent_application': pending_app,
        'incoming_friend_requests_count': friend_requests_count,
        'pending_applications_count': pending_apps_count,
        'session_idle_timeout': getattr(settings, 'SESSION_IDLE_TIMEOUT', 1800),
    }
