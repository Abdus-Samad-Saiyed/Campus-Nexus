// Campus Nexus - Service Worker for Desktop and Mobile Notifications

self.addEventListener('install', function(event) {
    self.skipWaiting();
});

self.addEventListener('activate', function(event) {
    event.waitUntil(self.clients.claim());
});

self.addEventListener('push', function(event) {
    if (!event.data) return;
    try {
        const payload = event.data.json();
        const title = payload.title || 'Campus Nexus';
        const options = {
            body: payload.body || payload.content || '',
            icon: payload.icon || '/static/img/nexus-icon.png',
            badge: '/static/img/nexus-icon.png',
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
            // Prefer the focused tab, or the first available window client
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
