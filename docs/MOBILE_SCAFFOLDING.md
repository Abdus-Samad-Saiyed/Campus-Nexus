# Campus Nexus — Progressive Web App (PWA) Scaffolding & Client Guide

## 1. Overview
This guide provides the technical reference and implementation details for the **Campus Nexus Universal Progressive Web App (PWA)**. The PWA architecture delivers an installable, standalone, offline-resilient mobile and desktop client directly from the Django backend without third-party app stores.

---

## 2. PWA File Structure & Core Assets

```
Campus-Project/
├── static/
│   ├── manifest.json              # Web App Manifest schema, icons, display modes
│   ├── sw.js                      # Root Service Worker with caching and offline handling
│   ├── img/
│   │   ├── favicon.svg            # Vector brand favicon
│   │   └── pwa/
│   │       ├── icon-192x192.png   # Standard Android / mobile launcher icon
│   │       ├── icon-512x512.png   # Splash screen & high-DPI desktop icon
│   │       ├── icon-maskable-192x192.png # Adaptive maskable icon
│   │       └── icon-maskable-512x512.png # High-DPI adaptive maskable icon
│   └── css/
│       └── main.css               # Dark glassmorphism styles, accent CSS variables
├── templates/
│   ├── base.html                  # PWA registration script, install buttons, iOS & universal modals
│   ├── offline.html               # Network failure fallback page
│   └── accounts/
│       ├── dashboard.html         # Post-login install hero prompt (#postLoginPwaPrompt)
│       └── profile.html           # Appearance & UI customization card
└── campus/
    ├── views.py                   # /manifest.json, /sw.js, and /offline/ route handlers
    └── tests/
        └── test_pwa_installability_and_manifest.py # Automated PWA test suite
```

---

## 3. Web App Manifest Specification (`/manifest.json`)

The manifest is served from `/manifest.json` with `Content-Type: application/manifest+json`:

```json
{
  "name": "Campus Nexus — University Digital Ecosystem",
  "short_name": "Campus Nexus",
  "description": "Centralized digital mobility, verified carpooling, and communication platform for university students.",
  "start_url": "/dashboard/?source=pwa",
  "scope": "/",
  "display": "standalone",
  "orientation": "portrait-primary",
  "background_color": "#050B08",
  "theme_color": "#08B74F",
  "icons": [
    {
      "src": "/static/img/pwa/icon-192x192.png",
      "sizes": "192x192",
      "type": "image/png",
      "purpose": "any"
    },
    {
      "src": "/static/img/pwa/icon-512x512.png",
      "sizes": "512x512",
      "type": "image/png",
      "purpose": "any"
    },
    {
      "src": "/static/img/pwa/icon-maskable-192x192.png",
      "sizes": "192x192",
      "type": "image/png",
      "purpose": "maskable"
    },
    {
      "src": "/static/img/pwa/icon-maskable-512x512.png",
      "sizes": "512x512",
      "type": "image/png",
      "purpose": "maskable"
    }
  ],
  "shortcuts": [
    {
      "name": "Browse Rides",
      "short_name": "Rides",
      "description": "Find and coordinate verified campus rides",
      "url": "/rides/?source=pwa_shortcut",
      "icons": [{ "src": "/static/img/pwa/icon-192x192.png", "sizes": "192x192" }]
    },
    {
      "name": "Offer a Ride",
      "short_name": "Offer",
      "description": "Create a shared ride for fellow students",
      "url": "/rides/create/?source=pwa_shortcut",
      "icons": [{ "src": "/static/img/pwa/icon-192x192.png", "sizes": "192x192" }]
    }
  ]
}
```

---

## 4. Service Worker Lifecycle (`/sw.js`)

Served from the root scope with the `Service-Worker-Allowed: /` HTTP header:

1. **Installation (`install`):** Pre-caches the offline fallback page (`/offline/`) and core brand assets in CacheStorage (`campus-nexus-v1`). Calls `self.skipWaiting()` for immediate activation.
2. **Activation (`activate`):** Iterates over existing caches, purging stale cache keys from previous deployments, then calls `clients.claim()`.
3. **Fetch Interception (`fetch`):**
   - **Navigation Requests:** Attempts network fetch. If the network is unreachable, returns the cached `/offline/` page.
   - **Static Assets:** Serves from cache when available, falling back to network.
4. **Push Notifications (`push`):** Displays native system notifications with sound, vibration, and actionable URLs.
5. **Notification Click (`notificationclick`):** Focuses an existing open window or navigates to the target destination.

---

## 5. Client Installation & Standalone Detection

### 5.1. Standalone Mode Detection
```javascript
function isRunningStandalone() {
    return window.matchMedia('(display-mode: standalone)').matches ||
           window.navigator.standalone === true ||
           document.referrer.includes('android-app://') ||
           (typeof window.isNexusRunningStandalone === 'function' && window.isNexusRunningStandalone());
}
```

### 5.2. Installation Entry Points
1. **Desktop Navbar Button (`#btn-pwa-install`):** Visible in browser mode on desktop screens. Reflects dynamic accent colors (`var(--green)`).
2. **Mobile Drawer Card (`#btn-pwa-install-mobile`):** Prominently placed inside the mobile navigation drawer.
3. **Dashboard Hero Card (`#postLoginPwaPrompt`):** Rendered on the student dashboard after sign-in. Dynamically customized for the detected operating system (Windows, Mac, Android, Linux).
4. **iOS Safari Instruction Modal (`#pwaIosInstallModal`):** 3-step visual instruction explaining *Share > Add to Home Screen* for Apple WebKit devices.
5. **Universal Fallback Modal (`#pwaGenericInstallModal`):** Instructions for desktop Chrome/Edge address bar icons and Android menus if `beforeinstallprompt` was already dismissed.

---

## 6. Biometric Passkey Handshake for Downloaded App

In downloaded standalone mode, students stay signed in for 2 weeks with one-touch biometrics:

```mermaid
sequenceDiagram
    autonumber
    actor Student
    participant Client as PWA Standalone App
    participant Server as Django Auth Backend
    participant DB as PostgreSQL 16

    Note over Student,Client: Student opens installed PWA on PC or Phone
    Client->>Server: GET /auth/passkey/authenticate/challenge/?enrollment_number=2024BCSE001
    Server->>Client: 200 OK { challenge: "...base64url...", rp_id: "..." }
    Client->>Student: Prompt Touch ID / Face ID / Windows Hello
    Student->>Client: Biometric Touch Confirmed
    Client->>Server: POST /auth/passkey/authenticate/verify/ (Signature + ClientDataJSON)
    Server->>DB: Verify cryptographic WebAuthn assertion signature
    Server->>Client: 200 OK (Set-Cookie: sessionid; Max-Age=1209600 [14 Days])
    Client->>Client: Redirect to /dashboard/
```

---

## 7. Automated Testing & Verification

The PWA subsystem is continuously verified via Django tests:

```bash
# Run PWA test suite
python manage.py test campus.tests.test_pwa_installability_and_manifest --settings=campus.settings.test
```

### Verified Checks:
- `/manifest.json` returns HTTP 200, `application/manifest+json`, and valid PWA schema.
- High-DPI icons (`192x192`, `512x512`, maskable) exist on disk.
- `/offline/` returns HTTP 200 with emergency campus guidelines.
- `/sw.js` serves with root `Service-Worker-Allowed: /` header.
- Standalone detection and post-login install prompt elements render cleanly on authenticated dashboards.
