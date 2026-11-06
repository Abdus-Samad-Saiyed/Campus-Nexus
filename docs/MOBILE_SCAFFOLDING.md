# Campus Nexus — Mobile Scaffolding & Architecture Guide

## 1. Overview
The Campus Nexus mobile application is built using **React Native with Expo** (TypeScript), interfacing with the Django backend via the versioned **REST API v1** (`/api/v1/`) and WebSocket chat gateway (`/ws/ride/<id>/`).

Mobile clients operate under identical server-side domain invariants as the web platform:
- One active ride limit enforced at database transaction boundary.
- Strict gender comfort gates (Boys-only, Girls-only, Mixed, Friends-only).
- ID verification required for account access.
- Redaction of private complaints and phone numbers from non-moderators/non-co-travelers.

---

## 2. Directory Structure (Expo React Native Client)

```
mobile/
├── App.tsx                        # App entry point, QueryClientProvider, AuthProvider
├── app.json                       # Expo configuration (bundle ID, permissions, splash)
├── package.json                   # React Native & Expo dependencies
├── tsconfig.json                  # TypeScript compiler options
├── src/
│   ├── api/
│   │   ├── client.ts              # Axios/Fetch client with Bearer auth & retry interceptor
│   │   ├── auth.ts                # Login, logout, session restoration
│   │   ├── catalogue.ts           # Service directory queries
│   │   ├── rides.ts               # Ride discovery, creation, join, leave, history
│   │   └── types.ts               # TypeScript interfaces matching API v1 schemas
│   ├── auth/
│   │   ├── AuthContext.tsx        # React context for auth state & secure storage token
│   │   └── SecureStorage.ts       # expo-secure-store wrapper for session tokens
│   ├── navigation/
│   │   ├── RootNavigator.tsx      # Auth flow vs App flow navigation
│   │   ├── AppTabs.tsx            # Bottom tabs: Services, Rides, My Rides, Profile
│   │   └── RideStackNavigator.tsx# Ride List -> Ride Detail -> Live Chat
│   ├── screens/
│   │   ├── auth/
│   │   │   ├── LoginScreen.tsx
│   │   │   └── ApplyAccountScreen.tsx
│   │   ├── nexus/
│   │   │   ├── ServiceCatalogueScreen.tsx
│   │   │   └── ServiceDetailScreen.tsx
│   │   ├── coordinate/
│   │   │   ├── RideListScreen.tsx
│   │   │   ├── RideDetailScreen.tsx
│   │   │   ├── CreateRideScreen.tsx
│   │   │   ├── MyRidesScreen.tsx
│   │   │   └── RideChatScreen.tsx
│   │   └── profile/
│   │       ├── ProfileScreen.tsx
│   │       └── FriendsScreen.tsx
│   ├── components/
│   │   ├── common/
│   │   │   ├── GlassCard.tsx       # Dark glassmorphism card matching web design tokens
│   │   │   ├── StatusBadge.tsx     # Standardized status pills (Open, Full, etc.)
│   │   │   ├── ErrorBanner.tsx     # User-facing failure alert with retry button
│   │   │   └── ClockworkHeader.tsx # Geometric gear emblem header
│   │   └── rides/
│   │       ├── RideCard.tsx
│   │       └── ParticipantRow.tsx
│   └── theme/
│       ├── colors.ts              # Hex tokens: #050B08 bg, #08B74F accent, #18181b cards
│       └── typography.ts          # Inter font configurations
```

---

## 3. Authentication & Secure Storage Pattern

Mobile clients must **never** store authentication tokens in unencrypted `AsyncStorage`.
Instead, use `expo-secure-store` which relies on iOS Keychain and Android Keystore.

```typescript
// src/auth/SecureStorage.ts
import * as SecureStore from 'expo-secure-store';

const TOKEN_KEY = 'campus_nexus_session_token';

export async function saveAuthToken(token: string): Promise<void> {
  await SecureStore.setItemAsync(TOKEN_KEY, token, {
    keychainAccessible: SecureStore.AFTER_FIRST_UNLOCK,
  });
}

export async function getAuthToken(): Promise<string | null> {
  return await SecureStore.getItemAsync(TOKEN_KEY);
}

export async function clearAuthToken(): Promise<void> {
  await SecureStore.deleteItemAsync(TOKEN_KEY);
}
```

### API Client Interceptor
```typescript
// src/api/client.ts
import axios from 'axios';
import { getAuthToken, clearAuthToken } from '../auth/SecureStorage';

export const apiClient = axios.create({
  baseURL: 'https://campusnexus.nitsri.ac.in/api/v1',
  timeout: 10000,
  headers: {
    'Content-Type': 'application/json',
    'Accept': 'application/json',
  },
});

apiClient.interceptors.request.use(async (config) => {
  const token = await getAuthToken();
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

apiClient.interceptors.response.use(
  (response) => response,
  async (error) => {
    if (error.response?.status === 401) {
      // Session revoked or expired
      await clearAuthToken();
      // Notify AuthContext to redirect to LoginScreen
    }
    return Promise.reject(error);
  }
);
```

---

## 4. API Endpoints Contract (v1)

### 4.1 Service Catalogue
- `GET /api/v1/catalogue/`
  - Response: `{ status: "success", data: { services: [ ... ] } }`
- `GET /api/v1/catalogue/<slug>/`
  - Returns service capability and development status.

### 4.2 Authentication
- `POST /api/v1/auth/login/`
  - Body: `{ enrollment_number: "2024BCSE001", password: "..." }`
  - Response: `{ status: "success", data: { token: "<session_key>", user: { ... } } }`
- `POST /api/v1/auth/logout/`
  - Header: `Authorization: Bearer <token>`
  - Response: `{ status: "success", message: "Successfully logged out." }`

### 4.3 Current User & Stats
- `GET /api/v1/me/`
  - Header: `Authorization: Bearer <token>`
  - Response:
    ```json
    {
      "status": "success",
      "data": {
        "user": {
          "id": 1,
          "enrollment_number": "2024BCSE001",
          "name": "Abdus Samad",
          "branch": "CSE",
          "gender": "M",
          "on_time_percentage": 100.0,
          "rides_completed": 4
        },
        "active_ride": { ... }
      }
    }
    ```

### 4.4 Ride Operations (COORDINATE Parity)
- `GET /api/v1/rides/?origin=...&destination=...&date=...`
  - Returns open rides visible to requesting user under gender/friend visibility rules.
- `GET /api/v1/rides/<id>/`
  - Returns ride details and participant rosters with sensitive data redacted.
- `POST /api/v1/rides/create/`
  - Body: `{ origin, destination, departure_time, total_spots, visibility, notes }`
  - Enforces one active ride, max 14-day horizon, future departure, and gender eligibility.
- `POST /api/v1/rides/<id>/join/`
  - Atomically reserves a spot. Returns 409 if already participant or active ride exists.
- `POST /api/v1/rides/<id>/leave/`
  - Atomically leaves or cancels ride.
- `GET /api/v1/my-rides/`
  - Returns active and historical completed rides.

---

## 5. Offline and Interrupted Request Policy
1. **Idempotent Join/Leave:** Network timeouts on join requests must re-check `GET /api/v1/me/` to verify if participant was enrolled before displaying error alerts.
2. **Offline Mode:** The mobile client caches the latest ride list and profile in local storage for read-only offline viewing, visibly badging the screen with "Offline — Showing cached records".
3. **Failure Honesty:** Mutating operations (Join, Leave, Create) are strictly rejected while offline with immediate clear feedback rather than queued optimistic updates that could violate capacity invariants.
