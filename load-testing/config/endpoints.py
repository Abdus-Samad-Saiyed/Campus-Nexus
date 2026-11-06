"""
Campus Nexus — Verified Endpoint Registry
Actual application endpoints derived from Django URL configuration.
"""

# Anonymous / Public Endpoints
LANDING = "/"
ABOUT = "/about/"
CONTACT = "/contact/"
CONTRIBUTORS = "/contributors/"
SERVICES = "/services/"
FEEDBACK = "/feedback/"
LOGIN = "/login/"
APPLY = "/apply/"
COORDINATE_PUBLIC = "/coordinate/"
HEALTHZ = "/healthz/"
DEMO_EXPLORE = "/demo/"

# Authenticated Student Endpoints
DASHBOARD = "/dashboard/"
PROFILE_SELF = "/profile/{enrollment}/"
SEARCH_STUDENTS = "/search/?q={query}"
RIDE_DIRECTORY = "/coordinate/"
RIDE_DETAIL = "/coordinate/{ride_id}/"
MY_RIDES = "/coordinate/my-rides/"
MESSAGES_INBOX = "/messages/"
DIRECT_CHAT = "/messages/{enrollment}/"

# Transactional Endpoints (POST Actions)
CREATE_RIDE = "/coordinate/create/"
JOIN_RIDE = "/coordinate/{ride_id}/join/"
LEAVE_RIDE = "/coordinate/{ride_id}/leave/"
JOIN_WAITLIST = "/coordinate/{ride_id}/waitlist/join/"
LEAVE_WAITLIST = "/coordinate/{ride_id}/waitlist/leave/"
SEND_RIDE_MESSAGE = "/coordinate/{ride_id}/message/"
FRIEND_REQUEST = "/friends/{enrollment}/request/"

# Administrative Endpoints
OPERATIONS_DASHBOARD = "/operations/"
APPLICATIONS_REVIEW = "/applications/"
ANALYTICS_VIEW = "/analytics/"
EXPORT_RIDES_CSV = "/operations/export/rides/"
EXPORT_USERS_CSV = "/operations/export/users/"
