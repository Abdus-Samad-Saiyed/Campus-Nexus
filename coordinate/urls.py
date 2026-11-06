"""URL patterns for the coordinate app."""

from django.urls import path

from .views import (
    alter_ride_capacity,
    approve_join_request,
    cancel_join_request,
    complete_ride,
    create_ride,
    decline_join_request,
    join_ride,
    join_waitlist,
    kick_participant,
    leave_ride,
    leave_waitlist,
    my_rides,
    report_incident,
    reschedule_ride,
    review_user,
    ride_detail,
    ride_list,
    send_message,
    terminate_ride,
    toggle_ride_chat_history,
)

urlpatterns = [
    path('',                                                     ride_list,             name='ride_list'),
    path('rides/',                                               ride_list,             name='ride_list_alias'),
    path('my-rides/',                                            my_rides,              name='my_rides'),
    path('create/',                                              create_ride,           name='create_ride'),
    path('<int:ride_id>/',                                       ride_detail,           name='ride_detail'),
    path('<int:ride_id>/join/',                                  join_ride,             name='join_ride'),
    path('<int:ride_id>/requests/<int:request_id>/approve/',     approve_join_request,  name='approve_join_request'),
    path('<int:ride_id>/requests/<int:request_id>/decline/',     decline_join_request,  name='decline_join_request'),
    path('<int:ride_id>/requests/cancel/',                       cancel_join_request,   name='cancel_join_request'),
    path('<int:ride_id>/leave/',                                 leave_ride,            name='leave_ride'),
    path('<int:ride_id>/reschedule/',                            reschedule_ride,       name='reschedule_ride'),
    path('<int:ride_id>/alter-capacity/',                        alter_ride_capacity,   name='alter_ride_capacity'),
    path('<int:ride_id>/waitlist/join/',                         join_waitlist,         name='join_waitlist'),
    path('<int:ride_id>/waitlist/leave/',                        leave_waitlist,        name='leave_waitlist'),
    path('<int:ride_id>/terminate/',                             terminate_ride,        name='terminate_ride'),
    path('<int:ride_id>/complete/',                              complete_ride,         name='complete_ride'),
    path('<int:ride_id>/kick/<int:participant_id>/',             kick_participant,      name='kick_participant'),
    path('<int:ride_id>/review/<int:user_id>/',                  review_user,           name='review_user'),
    path('<int:ride_id>/message/',                               send_message,          name='send_message'),
    path('<int:ride_id>/report-incident/',                       report_incident,       name='report_incident'),
    path('<int:ride_id>/toggle-chat-history/',                   toggle_ride_chat_history, name='toggle_ride_chat_history'),
]

