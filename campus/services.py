"""Campus Nexus Service Registry and Catalogue.

Defines the modular service offerings provided under the Campus Nexus umbrella platform.
Services can be active (e.g. COORDINATE) or in development (e.g. PRINT HUB).
"""

from typing import Any, Dict, List, Optional
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render

SERVICE_CATALOGUE: List[Dict[str, Any]] = [
    {
        "id": "coordinate",
        "slug": "coordinate",
        "name": "COORDINATE",
        "tagline": "Campus Ride Sharing & Commute",
        "description": "Coordinate cab shares to Srinagar Airport, Railway Station, or across town with verified NIT peers.",
        "icon": "bi-car-front-fill",
        "route_name": "ride_list",
        "url": "/coordinate/rides/",
        "is_active": True,
        "status_badge": "Live",
        "badge_class": "badge-open",
        "requires_auth": True,
        "features": [
            "Real-time ride discovery & filtering",
            "Gender-specific & friends-only comfort gates",
            "WebSocket live coordination chat",
            "Peer punctuality & attendance tracking",
        ],
    },
    {
        "id": "accommodate",
        "slug": "accommodate",
        "name": "ACCOMMODATE",
        "tagline": "Internship & Outstation Stay Coordination",
        "description": "Form peer flatmate & PG co-stay groups for outstation internships, conferences, and student travel.",
        "icon": "bi-buildings-fill",
        "route_name": "accommodate:group_list",
        "url": "/accommodate/groups/",
        "is_active": True,
        "status_badge": "Live",
        "badge_class": "badge-open",
        "requires_auth": True,
        "features": [
            "City and locality-based group discovery",
            "Verified Google Maps stay locations & top student hubs",
            "Auto FIFO waitlist queue or manual peer reviews",
            "Declared stay period non-overlap guarantees",
            "Transparent peer exit reviews & stay feedback",
        ],
    },
]


def get_all_services() -> List[Dict[str, Any]]:
    """Return all services in the catalogue."""
    return SERVICE_CATALOGUE


def get_active_services() -> List[Dict[str, Any]]:
    """Return only active/live services."""
    return [svc for svc in SERVICE_CATALOGUE if svc["is_active"]]


def get_service_by_slug(slug: str) -> Optional[Dict[str, Any]]:
    """Lookup a service entry by its slug or ID."""
    for svc in SERVICE_CATALOGUE:
        if svc["slug"] == slug or svc["id"] == slug:
            return svc
    return None


def service_gateway_view(request: HttpRequest, slug: str) -> HttpResponse:
    """Server-authoritative gateway for service access.
    
    Active services are redirected to their root view.
    Inactive/in-development services return HTTP 403 Forbidden with clear diagnostic information,
    preventing direct bypass of catalogue availability state.
    """
    service = get_service_by_slug(slug)
    if not service:
        raise Http404(f"Campus Nexus service '{slug}' not found.")

    if service["is_active"]:
        return redirect(service["url"])

    # Service is in development - enforce server-side 403
    return render(
        request,
        "nexus/service_unavailable.html",
        {
            "service": service,
            "catalogue": SERVICE_CATALOGUE,
        },
        status=403,
    )
