from django.http import HttpResponse, JsonResponse
import json

def robots_txt(request):
    lines = [
        "User-agent: *",
        "Allow: /",
        "Disallow: /operations/",
        "Disallow: /admin/",
        "Disallow: /api/v1/auth/",
        "",
        "Sitemap: https://dev.campus-nexus.online/sitemap.xml",
        "Agentmap: https://dev.campus-nexus.online/.well-known/ard.json",
    ]
    return HttpResponse("\n".join(lines), content_type="text/plain; charset=utf-8")

def llms_txt(request):
    text = """# Campus Nexus ? Centralized Student Digital Services & Mobility
> Decentralized, real-time campus mobility, ride sharing, and digital identity network for universities.

## System Overview
- **Platform**: Campus Nexus
- **Core Domain**: https://dev.campus-nexus.online
- **Purpose**: Student ride pooling, identity verification, emergency reporting, peer collaboration.
- **Protocol Support**: HTTP/1.1, HTTP/2, WebSockets (ASGI / Daphne), WebAuthn / Passkeys, WebMCP.

## Key Agent Endpoints
- **Public Ride Coordination**: https://dev.campus-nexus.online/coordinate/ (Browse and search shared campus rides)
- **Ride Creation**: https://dev.campus-nexus.online/coordinate/create/ (Publish student travel routes)
- **Student Authentication**: https://dev.campus-nexus.online/login/ (Session and Passkey entry)
- **Account Verification**: https://dev.campus-nexus.online/apply/ (Campus enrollment verification)
- **Versioned REST API**: https://dev.campus-nexus.online/api/v1/ (Bearer token authenticated endpoints)
- **Agent Discovery (ARD)**: https://dev.campus-nexus.online/.well-known/ard.json (Tool and capability catalog)

## Machine-Readable API Capabilities
1. `GET /api/v1/me/` - Authenticated student profile
2. `GET /api/v1/rides/` - Structured real-time ride search with filters
3. `POST /api/v1/rides/` - Create a student travel listing
4. `WS /ws/notifications/` - Real-time push notifications over WebSocket
5. `WS /ws/ride/<id>/` - Live channel coordinate chat
"""
    return HttpResponse(text.strip(), content_type="text/plain; charset=utf-8")

def ard_json(request):
    manifest = {
        "$schema": "https://raw.githubusercontent.com/ards-project/ard-spec/main/spec/schemas/ard-entry.schema.json",
        "specVersion": "1.0",
        "host": {
            "displayName": "Campus Nexus",
            "identifier": "did:web:dev.campus-nexus.online"
        },
        "entries": [
            {
                "identifier": "urn:air:campus-nexus.online:coordinate:rides",
                "displayName": "Campus Nexus Ride Coordination Service",
                "type": "application/mcp-server-card+json",
                "url": "https://dev.campus-nexus.online/coordinate/",
                "description": "Real-time university ride pooling, seat reservations, route matching, and chat.",
                "capabilities": ["SearchRides", "CreateRide", "RequestJoin", "CoordinateChat"],
                "representativeQueries": [
                    "find shared rides leaving campus to the airport",
                    "offer an empty seat for my daily commute to university",
                    "search student carpools to Srinagar city center"
                ]
            },
            {
                "identifier": "urn:air:campus-nexus.online:api:v1",
                "displayName": "Campus Nexus REST API v1",
                "type": "application/json",
                "url": "https://dev.campus-nexus.online/api/v1/",
                "description": "Versioned RESTful services for student profiles, token auth, and real-time mobility.",
                "capabilities": ["StudentAuth", "TokenExchange", "RideAPI", "PushNotifications"],
                "representativeQueries": [
                    "authenticate mobile client with bearer token",
                    "fetch current student account status and active rides"
                ]
            }
        ]
    }
    return JsonResponse(manifest, json_dumps_params={"indent": 2})
