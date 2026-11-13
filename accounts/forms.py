"""Forms for the accounts app."""

from django import forms
from django.contrib.auth.forms import AuthenticationForm

from .models import ENROLLMENT_VALIDATOR, AccountApplication, User


class EnrollmentAuthenticationForm(AuthenticationForm):
    """
    Authentication form that normalizes enrollment number to uppercase
    and trims whitespace.
    """

    username = forms.CharField(
        label='Enrollment Number',
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'e.g. 2024BCSE001',
            'autofocus': True,
            'autocapitalize': 'characters',
            'autocomplete': 'username',
        }),
    )
    password = forms.CharField(
        label='Password',
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': 'Enter your password',
            'autocomplete': 'current-password',
        }),
    )

    def clean_username(self):
        username = self.cleaned_data.get('username', '')
        return username.strip().upper()


class AccountApplicationForm(forms.ModelForm):
    """Collect student account details or an existing student's verification request."""

    email = forms.EmailField(
        label='Institute / Personal Email',
        widget=forms.EmailInput(attrs={
            'class': 'form-control',
            'placeholder': 'e.g. 2024bcse065@nitsri.ac.in',
            'autocomplete': 'email',
        }),
    )
    password = forms.CharField(
        label='Password',
        required=False,
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': 'Choose a password',
        }),
    )
    confirm_password = forms.CharField(
        label='Confirm Password',
        required=False,
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': 'Repeat your password',
        }),
    )

    class Meta:
        model = AccountApplication
        fields = ['enrollment_number', 'full_name', 'email', 'phone_number', 'gender']
        widgets = {
            'enrollment_number': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'e.g. 2024BCSE001',
            }),
            'full_name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Your full name',
            }),
            'phone_number': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': '+91 XXXXX XXXXX',
            }),
            'gender': forms.Select(attrs={'class': 'form-select'}),
        }

    def __init__(self, *args, user=None, **kwargs):
        from accounts.demo import is_guest_demo
        if user and is_guest_demo(user):
            user = None
        self.user = user
        super().__init__(*args, **kwargs)
        if self.user and self.user.is_authenticated:
            self.fields['password'].widget.attrs['placeholder'] = 'Leave blank to retain current password'
            self.fields['confirm_password'].widget.attrs['placeholder'] = 'Leave blank to retain current password'
            self.fields['enrollment_number'].widget.attrs['readonly'] = 'readonly'
            self.fields['enrollment_number'].initial = self.user.enrollment_number

    def clean_enrollment_number(self):
        from django.conf import settings
        from accounts.demo import is_guest_demo
        if self.user and self.user.is_authenticated and not is_guest_demo(self.user):
            enrollment = self.user.enrollment_number.strip().upper()
        else:
            enrollment = self.cleaned_data.get('enrollment_number', '').strip().upper()

        if enrollment == 'GUEST_DEMO':
            raise forms.ValidationError(
                'Guest Demo cannot request student verification. Enter your own student enrollment number to create an account.'
            )

        if not (self.user and self.user.is_authenticated) and User.objects.filter(
            enrollment_number=enrollment,
            is_deleted=False,
        ).exists():
            raise forms.ValidationError(
                'An account may already exist for these details. Try signing in or resetting your password.'
            )

        if not ENROLLMENT_VALIDATOR.regex.match(enrollment):
            raise forms.ValidationError(
                'Invalid enrollment number format. Expected format: 2024BCSE001'
            )

        if not getattr(settings, 'DEBUG', False) and enrollment.startswith('20XX'):
            raise forms.ValidationError(
                'Synthetic debug enrollments (20XX...) are disabled in production.'
            )

        from django.db import connections
        from accounts.utils import is_test_environment
        is_distinct_perm = (
            not is_test_environment()
            and 'permanent' in connections
            and connections['permanent'].settings_dict.get('NAME') != connections['default'].settings_dict.get('NAME')
        )
        perm_db = 'permanent' if is_distinct_perm else 'default'

        if not (self.user and self.user.is_authenticated) and perm_db != 'default' and User.objects.using(perm_db).filter(
            enrollment_number=enrollment,
            is_deleted=False,
        ).exists():
            raise forms.ValidationError(
                'An account may already exist for these details. Try signing in or resetting your password.'
            )

        # Only block if an already verified, active non-deleted user exists
        if User.objects.filter(enrollment_number=enrollment, verified=True, is_deleted=False).exists():
            raise forms.ValidationError(
                'An account with this enrollment number is already registered and verified.'
            )
        if perm_db != 'default' and User.objects.using(perm_db).filter(enrollment_number=enrollment, verified=True, is_deleted=False).exists():
            raise forms.ValidationError(
                'An account with this enrollment number is already registered and verified.'
            )

        from accounts.utils import release_deleted_user_identifiers
        release_deleted_user_identifiers(enrollment=enrollment)

        # Block if an application is already pending
        if AccountApplication.objects.filter(enrollment_number=enrollment, approved=False).exists():
            raise forms.ValidationError(
                'An application for this enrollment number is already pending verification.'
            )
        if perm_db != 'default' and AccountApplication.objects.using(perm_db).filter(enrollment_number=enrollment, approved=False).exists():
            raise forms.ValidationError(
                'An application for this enrollment number is already pending verification.'
            )

        return enrollment

    def clean_full_name(self):
        name = self.cleaned_data.get('full_name', '').strip()
        if not name:
            raise forms.ValidationError('Full name is required.')
        if '<' in name or '>' in name:
            raise forms.ValidationError('Name contains invalid characters.')
        from accounts.two_factor import clean_person_name
        return clean_person_name(name)

    def clean_phone_number(self):
        from accounts.two_factor import normalize_phone_number, validate_phone_number
        phone = self.cleaned_data.get('phone_number', '').strip()
        normalized = normalize_phone_number(phone)
        validate_phone_number(normalized)
        return normalized

    def clean_email(self):
        email = self.cleaned_data.get('email', '').strip().lower()
        if not email:
            raise forms.ValidationError('Email address is required.')

        from django.db import connections
        from accounts.utils import is_test_environment
        is_distinct_perm = (
            not is_test_environment()
            and 'permanent' in connections
            and connections['permanent'].settings_dict.get('NAME') != connections['default'].settings_dict.get('NAME')
        )
        perm_db = 'permanent' if is_distinct_perm else 'default'

        if not (self.user and self.user.is_authenticated):
            email_users = User.objects.filter(email=email, is_deleted=False)
            if email_users.exists() or (perm_db != 'default' and User.objects.using(perm_db).filter(email=email, is_deleted=False).exists()):
                raise forms.ValidationError(
                    'This email may already be linked to an account. Try signing in or resetting your password.'
                )

        if User.objects.filter(email=email, verified=True, is_deleted=False).exists():
            raise forms.ValidationError('An account with this email address already exists.')
        if perm_db != 'default' and User.objects.using(perm_db).filter(email=email, verified=True, is_deleted=False).exists():
            raise forms.ValidationError('An account with this email address already exists.')

        from accounts.utils import release_deleted_user_identifiers
        release_deleted_user_identifiers(email=email)

        if AccountApplication.objects.filter(email=email, approved=False).exists():
            raise forms.ValidationError('An application with this email address is already pending verification.')
        if perm_db != 'default' and AccountApplication.objects.using(perm_db).filter(email=email, approved=False).exists():
            raise forms.ValidationError('An application with this email address is already pending verification.')

        return email

    def clean(self):
        cleaned = super().clean()
        pw = cleaned.get('password')
        cpw = cleaned.get('confirm_password')

        if not self.user or not self.user.is_authenticated:
            if not pw:
                self.add_error('password', 'Password is required.')
            if not cpw:
                self.add_error('confirm_password', 'Password confirmation is required.')

        if pw:
            if len(pw) < 8:
                self.add_error('password', 'Password must contain at least 8 characters.')
            else:
                from django.contrib.auth.password_validation import validate_password
                try:
                    validate_password(pw)
                except forms.ValidationError as e:
                    self.add_error('password', e)

            if not cpw:
                self.add_error('confirm_password', 'Password confirmation is required.')
            elif pw != cpw:
                self.add_error('confirm_password', 'Passwords do not match.')

        return cleaned



class EarlyLaunchDeclarationForm(forms.Form):
    """Early launch self-declaration form requiring enrollment, phone, name, gender, password.

    Skips the physical admin verification queue and grants immediate active student access.
    """

    enrollment_number = forms.CharField(
        max_length=11,
        label='Enrollment Number',
        widget=forms.TextInput(attrs={
            'class': 'form-control font-monospace',
            'placeholder': 'e.g. 2024BCSE001',
            'autocomplete': 'username',
        }),
    )
    full_name = forms.CharField(
        max_length=150,
        label='Full Name',
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Your Full Name',
            'autocomplete': 'name',
        }),
    )
    gender = forms.ChoiceField(
        choices=User.GENDER_CHOICES,
        label='Gender',
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    phone_number = forms.CharField(
        max_length=15,
        label='Phone Number (WhatsApp)',
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': '+91 XXXXX XXXXX',
            'autocomplete': 'tel',
        }),
    )
    email = forms.EmailField(
        required=False,
        label='Institute / Personal Email (Optional)',
        widget=forms.EmailInput(attrs={
            'class': 'form-control',
            'placeholder': 'Defaults to enrollment@nitsri.ac.in',
            'autocomplete': 'email',
        }),
    )
    password = forms.CharField(
        label='Password',
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': 'Choose a secure password',
            'autocomplete': 'new-password',
        }),
    )
    confirm_password = forms.CharField(
        label='Confirm Password',
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': 'Confirm your password',
            'autocomplete': 'new-password',
        }),
    )

    def clean_enrollment_number(self):
        from django.conf import settings
        enrollment = self.cleaned_data.get('enrollment_number', '').strip().upper()

        if enrollment == 'GUEST_DEMO':
            raise forms.ValidationError(
                'GUEST_DEMO is reserved for preview demo sessions and cannot be registered as an account.'
            )

        if not ENROLLMENT_VALIDATOR.regex.match(enrollment):
            raise forms.ValidationError(
                'Invalid enrollment number format. Expected format: 2024BCSE001'
            )

        if not getattr(settings, 'DEBUG', False) and enrollment.startswith('20XX'):
            raise forms.ValidationError(
                'Synthetic debug enrollments (20XX...) are disabled in production.'
            )

        if User.objects.filter(enrollment_number=enrollment, is_deleted=False).exists():
            raise forms.ValidationError(
                'An account with this enrollment number is already registered. Please log in.'
            )

        from accounts.utils import release_deleted_user_identifiers
        release_deleted_user_identifiers(enrollment=enrollment)
        return enrollment

    def clean_full_name(self):
        name = self.cleaned_data.get('full_name', '').strip()
        if not name:
            raise forms.ValidationError('Full name is required.')
        from accounts.two_factor import clean_person_name
        return clean_person_name(name)

    def clean_phone_number(self):
        from accounts.two_factor import normalize_phone_number, validate_phone_number
        phone = self.cleaned_data.get('phone_number', '').strip()
        normalized = normalize_phone_number(phone)
        validate_phone_number(normalized)
        return normalized

    def clean_email(self):
        email = self.cleaned_data.get('email', '').strip().lower()
        if email and User.objects.filter(email=email, is_deleted=False).exists():
            raise forms.ValidationError('An account with this email address already exists.')
        if email:
            from accounts.utils import release_deleted_user_identifiers
            release_deleted_user_identifiers(email=email)
        return email

    def clean(self):
        cleaned = super().clean()
        pw = cleaned.get('password')
        cpw = cleaned.get('confirm_password')

        if pw:
            if len(pw) < 8:
                self.add_error('password', 'Password must contain at least 8 characters.')
            else:
                from django.contrib.auth.password_validation import validate_password
                try:
                    validate_password(pw)
                except forms.ValidationError as e:
                    self.add_error('password', e)

        if not cpw:
            self.add_error('confirm_password', 'This field is required.')
        elif pw and pw != cpw:
            self.add_error('confirm_password', 'Passwords do not match.')

        return cleaned

    def save(self):
        from django.contrib.auth.hashers import make_password
        enrollment = self.cleaned_data['enrollment_number']
        phone = self.cleaned_data['phone_number']
        full_name = self.cleaned_data.get('full_name', '').strip()
        gender = self.cleaned_data.get('gender', '')
        password = self.cleaned_data['password']
        email = self.cleaned_data.get('email') or f"{enrollment.lower()}@nitsri.ac.in"

        from accounts.utils import release_deleted_user_identifiers
        release_deleted_user_identifiers(enrollment=enrollment, email=email)

        parts = full_name.split() if full_name else []
        first_name = parts[0][:150] if parts else ''
        last_name = ' '.join(parts[1:])[:150] if len(parts) > 1 else ''

        user = User.objects.create(
            username=enrollment,
            enrollment_number=enrollment,
            first_name=first_name,
            last_name=last_name,
            phone_number=phone,
            email=email,
            gender=gender,
            password=make_password(password),
            verified=False,  # Early access self-declarations are untrusted
            is_active=True,
        )
        return user
