# PWA assets and offline behavior

Reviewed 2026-11-12. This page documents the browser-facing PWA files; it does not describe a native mobile client.

## Source files and routes

| File or route | Responsibility |
| --- | --- |
| `static/manifest.json`, `/manifest.json` | Install name, icons, start URL, scope, display mode, and shortcuts. |
| `static/sw.js`, `/sw.js` | Root-scoped service worker, fetch strategies, offline fallback, and push event handling. |
| `templates/base.html` | Registration, install UI, standalone detection, and push subscription UI. |
| `templates/offline.html` | Network-failure navigation fallback. |

The server sets `Service-Worker-Allowed: /` on `/sw.js` and uses no-cache headers for the worker so browsers can check for updates.

## Cache behavior

The worker uses a versioned cache. It precaches the offline page and selected icons, serves navigation requests from the network with an offline-page fallback, uses stale-while-revalidate for same-origin `/static/` assets, and treats the manifest as network-first. Other same-origin GET requests use network-first with a cached response fallback. Non-GET requests are not intercepted.

Do not describe the app as fully offline-capable. The service worker does not queue writes, synchronize rides, or provide guaranteed offline access to every screen. Validate authenticated page caching and cache invalidation changes carefully.

## Install behavior

The browser may expose a native install prompt; support and UI differ by browser. The application also provides install guidance when the native prompt is unavailable. Test manifest icon paths, start URL, root scope, update prompts, and standalone detection on each supported browser.

## Push and passkeys

Push requires HTTPS and server-side VAPID configuration; users must grant notification permission. Passkeys use the browser WebAuthn API and require relying-party settings to match the deployed origin. These are capabilities, not a promise that every browser supports them.

## Change checklist

When editing these assets, check cache versioning, service-worker update behavior, stale authenticated responses, offline fallback accessibility, Android installation, iOS home-screen behavior, and push permission/revocation. The documentation review did not run browser or device tests.
