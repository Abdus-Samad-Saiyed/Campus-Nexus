"""Forms for the coordinate app."""

import datetime
from django import forms
from django.utils import timezone

from .models import RideMessage, RideRequest, RideReview


class RideRequestForm(forms.ModelForm):
    """Form for creating a new ride with validation for spots, departure time, and routes."""

    disclose_identity = forms.BooleanField(
        required=False,
        initial=True,
        label='Disclose student identity (name, enrollment, gender) to confirmed co-passengers (Mandatory)',
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input', 'id': 'id_disclose_identity', 'required': 'required'}),
    )
    share_contact = forms.BooleanField(
        required=False,
        initial=True,
        label='Share contact details (phone number and email) with confirmed co-passengers during active trip (Mandatory)',
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input', 'id': 'id_share_contact', 'required': 'required'}),
    )

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    class Meta:
        model = RideRequest
        fields = [
            'route',
            'custom_from_location',
            'custom_to_location',
            'visibility',
            'departure_time',
            'total_spots',
            'share_chat_history',
        ]
        widgets = {
            'route': forms.Select(attrs={'class': 'form-select', 'id': 'id_route'}),
            'custom_from_location': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Enter source (e.g. Lal Chowk, Dal Gate)',
                'id': 'id_custom_from_location',
            }),
            'custom_to_location': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Enter destination (e.g. Hazratbal, Foreshore Road)',
                'id': 'id_custom_to_location',
            }),
            'visibility': forms.Select(attrs={'class': 'form-select'}),
            'departure_time': forms.DateTimeInput(
                attrs={'class': 'form-control', 'type': 'datetime-local'},
                format='%Y-%m-%dT%H:%M',
            ),
            'total_spots': forms.NumberInput(attrs={
                'class': 'form-control',
                'min': 1,
                'max': 6,
                'placeholder': '1–6',
            }),
            'share_chat_history': forms.CheckboxInput(attrs={
                'class': 'form-check-input',
                'id': 'id_share_chat_history',
            }),
        }

    def clean_total_spots(self):
        spots = self.cleaned_data.get('total_spots')
        if spots is not None and (spots < 1 or spots > 6):
            raise forms.ValidationError('Total spots must be between 1 and 6.')
        return spots

    def clean_departure_time(self):
        dep_time = self.cleaned_data.get('departure_time')
        if dep_time:
            now = timezone.now()
            if dep_time <= now:
                raise forms.ValidationError('Departure time must be in the future.')
            if dep_time > now + datetime.timedelta(days=365):
                raise forms.ValidationError('Departure time cannot be scheduled more than 1 year in advance.')
        return dep_time

    def clean(self):
        cleaned = super().clean()
        if 'disclose_identity' in self.data:
            if not cleaned.get('disclose_identity'):
                self.add_error('disclose_identity', 'You must consent to disclose your identity to create a ride.')
        else:
            cleaned['disclose_identity'] = True

        if 'share_contact' in self.data:
            if not cleaned.get('share_contact'):
                self.add_error('share_contact', 'You must consent to share your contact details with confirmed co-passengers to create a ride.')
        else:
            cleaned['share_contact'] = True

        if cleaned.get('route') == 'CUSTOM':
            if not cleaned.get('custom_from_location'):
                self.add_error('custom_from_location', 'Required for custom routes.')
            if not cleaned.get('custom_to_location'):
                self.add_error('custom_to_location', 'Required for custom routes.')

        # Catalogue overload protection: limit active rides to 10 per account
        if self.user and not getattr(self.user, 'is_guest_demo', False):
            active_count = RideRequest.objects.filter(
                creator=self.user,
                status__in=['OPEN', 'FULL'],
            ).count()
            if active_count >= 10:
                self.add_error(None, 'Ride creation limit reached: you cannot have more than 10 active rides in the catalogue.')

        return cleaned


class RideMessageForm(forms.ModelForm):
    """Inline form for sending a chat message (fallback for non-WebSocket)."""

    class Meta:
        model = RideMessage
        fields = ['content']
        widgets = {
            'content': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Type a message…',
                'autocomplete': 'off',
            }),
        }


class RideReviewForm(forms.ModelForm):
    """Post-ride review form with multi-parameter ratings and grievance categorization."""

    rating = forms.ChoiceField(
        choices=[(1, '1'), (2, '2'), (3, '3'), (4, '4'), (5, '5')],
        widget=forms.RadioSelect(attrs={'class': 'btn-check'}),
        initial=5,
        label='Overall Experience',
    )
    punctuality_rating = forms.ChoiceField(
        choices=[(1, '1'), (2, '2'), (3, '3'), (4, '4'), (5, '5')],
        widget=forms.RadioSelect(attrs={'class': 'btn-check'}),
        initial=5,
        required=False,
        label='Punctuality & Timeliness',
    )
    communication_rating = forms.ChoiceField(
        choices=[(1, '1'), (2, '2'), (3, '3'), (4, '4'), (5, '5')],
        widget=forms.RadioSelect(attrs={'class': 'btn-check'}),
        initial=5,
        required=False,
        label='Communication & Responsiveness',
    )
    reliability_rating = forms.ChoiceField(
        choices=[(1, '1'), (2, '2'), (3, '3'), (4, '4'), (5, '5')],
        widget=forms.RadioSelect(attrs={'class': 'btn-check'}),
        initial=5,
        required=False,
        label='Reliability & Etiquette',
    )
    grievance = forms.ChoiceField(
        choices=RideReview.GRIEVANCE_CHOICES,
        widget=forms.Select(attrs={'class': 'form-select', 'id': 'id_grievance'}),
        initial='NONE',
        required=False,
        label='Grievance Category',
    )

    class Meta:
        model = RideReview
        fields = [
            'rating',
            'punctuality_rating',
            'communication_rating',
            'reliability_rating',
            'grievance',
            'custom_grievance',
            'complaint',
        ]
        widgets = {
            'grievance': forms.Select(attrs={'class': 'form-select', 'id': 'id_grievance'}),
            'custom_grievance': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Specify custom grievance details...',
                'id': 'id_custom_grievance',
            }),
            'complaint': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 3,
                'placeholder': 'Optional: additional details or feedback for this participant.',
            }),
        }

    def clean(self):
        cleaned = super().clean()
        rating_val = cleaned.get('rating')
        try:
            rating_int = int(rating_val) if rating_val is not None else 5
        except (ValueError, TypeError):
            rating_int = 5

        for field in ['punctuality_rating', 'communication_rating', 'reliability_rating']:
            val = cleaned.get(field)
            if not val:
                cleaned[field] = rating_int
            else:
                try:
                    cleaned[field] = int(val)
                except (ValueError, TypeError):
                    cleaned[field] = rating_int

        if not cleaned.get('grievance'):
            cleaned['grievance'] = 'NONE'

        if cleaned.get('grievance') == 'CUSTOM' and not cleaned.get('custom_grievance'):
            self.add_error('custom_grievance', 'Please provide details for the custom grievance.')

        return cleaned
