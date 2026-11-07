// Campus Nexus - Progressive Web App Service Worker
// Provides Network-First navigation with offline fallback, asset caching, and Web Push notifications

const CACHE_NAME = 'campus-nexus-v1';
const PRECACHE_URLS = [
    '/offline/',
    '/static/img/pwa/icon-192x192.png',
    '/static/img/pwa/icon-512x512.png',
    '/manifest.json'
];

self.addEventListener('install', function(event) {
    event.waitUntil(
        caches.open(CACHE_NAME).then(function(cache) {
            return cache.addAll(PRECACHE_URLS).catch(function(err) {
                console.warn('[SW] Pre-cache warning:', err);
            });
        }).then(function() {
            return self.skipWaiting();
        })
    );
});

self.addEventListener('activate', function(event) {
    event.waitUntil(
        caches.keys().then(function(cacheNames) {
            return Promise.all(
                cacheNames.map(function(cacheName) {
                    if (cacheName !== CACHE_NAME) {
                        return caches.delete(cacheName);
                    }
                })
            );
        }).then(function() {
            return self.clients.claim();
        })
    );
});

self.addEventListener('fetch', function(event) {
    const request = event.request;
    if (request.method !== 'GET') {
        return;
    }

    const url = new URL(request.url);

    // Bypass WebSockets and cross-origin analytics/CDN calls from strict caching
    if (url.protocol.startsWith('ws')) {
        return;
    }

    // 1. Navigation requests (HTML page loads): Network-First with Offline Fallback
    if (request.mode === 'navigate') {
        event.respondWith(
            fetch(request).catch(function() {
                return caches.match('/offline/').then(function(offlineResponse) {
                    return offlineResponse || new Response(
                        '<h1>Campus Nexus is Offline</h1><p>Please check your internet connection.</p>',
                        { headers: { 'Content-Type': 'text/html; charset=utf-8' } }
                    );
                });
            })
        );
        return;
    }

    // 2. Static application assets and manifest: Cache-First with Network Fallback & dynamic caching
    if (url.origin === self.location.origin && (url.pathname.startsWith('/static/') || url.pathname === '/manifest.json')) {
        event.respondWith(
            caches.match(request).then(function(cachedResponse) {
                if (cachedResponse) {
                    return cachedResponse;
                }
                return fetch(request).then(function(networkResponse) {
                    if (!networkResponse || networkResponse.status !== 200 || networkResponse.type !== 'basic') {
                        return networkResponse;
                    }
                    const responseClone = networkResponse.clone();
                    caches.open(CACHE_NAME).then(function(cache) {
                        cache.put(request, responseClone);
                    });
                    return networkResponse;
                }).catch(function() {
                    return cachedResponse;
                });
            })
        );
        return;
    }

    // 3. All other local GET requests: Network-First
    if (url.origin === self.location.origin) {
        event.respondWith(
            fetch(request).catch(function() {
                return caches.match(request);
            })
        );
    }
});

self.addEventListener('push', function(event) {
    if (!event.data) return;
    try {
        const payload = event.data.json();
        const title = payload.title || 'Campus Nexus';
        const options = {
            body: payload.body || payload.content || '',
            icon: payload.icon || '/static/img/pwa/icon-192x192.png',
            badge: '/static/img/pwa/icon-192x192.png',
            vibrate: [200, 100, 200],
            tag: payload.tag || 'nexus-notification',
            renotify: true,
            data: {
                url: payload.url || '/dashboard/',
            },
        };
        event.waitUntil(self.registration.showNotification(title, options));
    } catch (e) {
        console.error('Service Worker push error:', e);
    }
});

self.addEventListener('notificationclick', function(event) {
    event.notification.close();
    const rawUrl = (event.notification.data && event.notification.data.url) ? event.notification.data.url : '/dashboard/';
    const targetUrl = new URL(rawUrl, self.location.origin).href;

    event.waitUntil(
        clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function(clientList) {
            // 1. Is there an open window already on this exact target URL?
            for (let i = 0; i < clientList.length; i++) {
                const client = clientList[i];
                if (client.url === targetUrl && 'focus' in client) {
                    return client.focus();
                }
            }

            // 2. If any window from our origin is open, bring it to focus and navigate it straight to the target URL!
            let targetClient = null;
            for (let i = 0; i < clientList.length; i++) {
                const client = clientList[i];
                if (client.url.startsWith(self.location.origin)) {
                    if (client.focused) {
                        targetClient = client;
                        break;
                    }
                    if (!targetClient) {
                        targetClient = client;
                    }
                }
            }

            if (targetClient) {
                if ('focus' in targetClient) {
                    targetClient.focus();
                }
                if ('navigate' in targetClient) {
                    return targetClient.navigate(targetUrl).then(function(navigatedClient) {
                        return (navigatedClient || targetClient).focus();
                    }).catch(function() {
                        targetClient.postMessage({ action: 'navigate', url: targetUrl });
                        return targetClient.focus();
                    });
                } else {
                    targetClient.postMessage({ action: 'navigate', url: targetUrl });
                    return targetClient.focus();
                }
            }

            // 3. Only open a new window if NO window is open
            if (clients.openWindow) {
                return clients.openWindow(targetUrl);
            }
        })
    );
});
