    function updateParticipantPresence(userId, status) {
        const badge = document.getElementById(`presence-badge-${userId}`);
        if (!badge) return;
        const text = badge.querySelector('.presence-text');
        if (status === 'online') {
            badge.className = 'presence-badge badge rounded-pill bg-success-subtle text-success border border-success-subtle small py-0 px-2';
            if (text) text.textContent = 'Online';
            badge.title = 'Online in ride';
        } else {
            badge.className = 'presence-badge badge rounded-pill bg-secondary-subtle text-secondary border border-secondary small py-0 px-2';
            if (text) text.textContent = 'Offline';
            badge.title = 'Offline';
        }
    }
// static/js/chat.js
// WebSocket real-time chat for COORDINATE rides with fallback handling

document.addEventListener('DOMContentLoaded', function () {
    const chatContainer = document.getElementById('ride-chat-box');
    if (!chatContainer) return;

    const rideId = chatContainer.dataset.rideId;
    const currentUsername = chatContainer.dataset.currentUser;
    const messagesContainer = document.getElementById('chat-messages');
    const messageForm = document.getElementById('chat-form');
    const messageInput = document.getElementById('chat-input');
    const statusIndicator = document.getElementById('chat-status');

    if (!rideId) return;

    function scrollToBottom() {
        if (messagesContainer) {
            messagesContainer.scrollTop = messagesContainer.scrollHeight;
        }
    }
    scrollToBottom();

    const protocol = window.location.protocol === 'https:' ? 'wss://' : 'ws://';
    const wsUrl = `${protocol}${window.location.host}/ws/ride/${rideId}/`;

    let socket = null;
    let reconnectAttempts = 0;
    const maxReconnectAttempts = 5;
    let isTerminated = false;

    function connectWebSocket() {
        if (isTerminated) return;
        socket = new WebSocket(wsUrl);

        socket.onopen = function () {
            reconnectAttempts = 0;
            if (statusIndicator) {
                statusIndicator.textContent = 'Live';
                statusIndicator.className = 'chat-status chat-status-connected';
            }
            // Mark all past incoming messages as read
            document.querySelectorAll('.message-other[data-message-id]').forEach(el => {
                const mid = el.getAttribute('data-message-id');
                if (mid) {
                    socket.send(JSON.stringify({ type: 'mark_read', message_id: parseInt(mid, 10) }));
                }
            });
        };

        socket.onmessage = function (e) {
            try {
                const data = JSON.parse(e.data);
                if (data.type === 'message') {
                    appendMessage(data.author, data.content, data.timestamp, data.is_self, data.message_id);
                    if (!data.is_self && data.message_id && socket && socket.readyState === WebSocket.OPEN) {
                        socket.send(JSON.stringify({ type: 'mark_read', message_id: data.message_id }));
                    }
                } else if (data.type === 'presence_update') {
                    updateParticipantPresence(data.user_id, data.status);
                } else if (data.type === 'presence_initial' && Array.isArray(data.online_user_ids)) {
                    data.online_user_ids.forEach(uid => updateParticipantPresence(uid, 'online'));
                } else if (data.type === 'read_receipt') {
                    const receiptEl = document.getElementById(`read-receipt-${data.message_id}`);
                    if (receiptEl) {
                        const countEl = document.getElementById(`read-count-${data.message_id}`);
                        if (countEl && data.read_count) {
                            countEl.textContent = data.read_count;
                        }
                    }
                } else if (data.type === 'system_notice') {
                    appendSystemNotice(data.content);
                    if (['cancelled', 'completed', 'terminated', 'user_kicked'].includes(data.action)) {
                        if (data.action !== 'user_kicked' || data.user === currentUsername) {
                            isTerminated = true;
                            disableChatUI('Ride session ended. WebSocket connection closed.');
                        }
                    }
                } else if (data.type === 'session_terminated') {
                    isTerminated = true;
                    disableChatUI(data.reason || 'Session terminated by security policy.');
                } else if (data.type === 'error') {
                    console.warn('Chat error from server:', data.error);
                }
            } catch (err) {
                console.error('Error parsing chat packet:', err);
            }
        };

        socket.onclose = function (e) {
            if (e.code === 4001 || e.code === 4003) {
                isTerminated = true;
                disableChatUI('Chat session ended.');
            }
            if (statusIndicator) {
                statusIndicator.textContent = isTerminated ? 'Closed' : 'Disconnected';
                statusIndicator.className = 'chat-status chat-status-disconnected';
            }
            if (!isTerminated && reconnectAttempts < maxReconnectAttempts) {
                reconnectAttempts++;
                setTimeout(connectWebSocket, 2000 * reconnectAttempts);
            }
        };

        socket.onerror = function () {
            if (statusIndicator) {
                statusIndicator.textContent = 'Error';
                statusIndicator.className = 'chat-status chat-status-error';
            }
        };
    }

    function disableChatUI(noticeText) {
        if (messageInput) {
            messageInput.disabled = true;
            messageInput.placeholder = noticeText || 'Chat is closed.';
        }
        const submitBtn = messageForm ? messageForm.querySelector('button[type="submit"]') : null;
        if (submitBtn) {
            submitBtn.disabled = true;
        }
    }

    function appendSystemNotice(content) {
        if (!messagesContainer || !content) return;
        const noticeDiv = document.createElement('div');
        noticeDiv.className = 'text-center my-2';
        const badge = document.createElement('span');
        badge.className = 'badge bg-dark border border-secondary text-white-50 px-3 py-2 small';
        const icon = document.createElement('i');
        icon.className = 'bi bi-info-circle me-1 text-success';
        badge.appendChild(icon);
        const textNode = document.createTextNode(' ' + content);
        badge.appendChild(textNode);
        noticeDiv.appendChild(badge);
        messagesContainer.appendChild(noticeDiv);
        scrollToBottom();
    }

    function appendMessage(author, content, timestamp, isSelf, messageId) {
        if (!messagesContainer) return;

        // Check if there was an empty notice and remove it
        const emptyNotice = document.getElementById('empty-chat-notice');
        if (emptyNotice) {
            emptyNotice.remove();
        }

        const msgDiv = document.createElement('div');
        msgDiv.className = `message-item ${isSelf ? 'message-self' : 'message-other'}`;
        if (messageId) {
            msgDiv.setAttribute('data-message-id', messageId);
        }

        const authorDiv = document.createElement('div');
        authorDiv.className = 'message-author';
        authorDiv.textContent = author;

        const bubbleDiv = document.createElement('div');
        bubbleDiv.className = 'message-bubble';
        bubbleDiv.textContent = content;

        const timeDiv = document.createElement('div');
        timeDiv.className = 'message-time d-flex align-items-center justify-content-end gap-1';
        const spanTime = document.createElement('span');
        spanTime.textContent = timestamp;
        timeDiv.appendChild(spanTime);

        if (isSelf && messageId) {
            const receiptSpan = document.createElement('span');
            receiptSpan.id = `read-receipt-${messageId}`;
            receiptSpan.className = 'read-receipt text-success ms-1';
            receiptSpan.title = 'Read receipts always active in ride groups';
            receiptSpan.innerHTML = `<i class="bi bi-check2-all"></i> <span class="small read-count-num" id="read-count-${messageId}"></span>`;
            timeDiv.appendChild(receiptSpan);
        }

        msgDiv.appendChild(authorDiv);
        msgDiv.appendChild(bubbleDiv);
        msgDiv.appendChild(timeDiv);

        messagesContainer.appendChild(msgDiv);
        scrollToBottom();
    }

    if (messageForm && messageInput) {
        messageForm.addEventListener('submit', function (e) {
            e.preventDefault();
            const text = messageInput.value.trim();
            if (!text) return;

            if (socket && socket.readyState === WebSocket.OPEN) {
                socket.send(JSON.stringify({ content: text }));
                messageInput.value = '';
            } else {
                // Fallback to standard HTTP POST form submission
                messageForm.submit();
            }
        });
    }

    connectWebSocket();
});
