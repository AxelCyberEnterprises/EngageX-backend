from django.db import models
from django.core.exceptions import ValidationError
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin, BaseUserManager
from rest_framework.authtoken.models import Token
from .managers import CustomUserManager
from django.conf import settings

from django.utils import timezone
from datetime import date, timedelta

from django.core.validators import FileExtensionValidator
from django.utils.translation import gettext_lazy as _

from .storages_backends import (
    ProfilePicStorage,
    SlidesStorage,
    UserVideosStorage,
    StaticVideosStorage,
)


class ExpiringToken(Token):
    """
    Extends the default Token model to add an expiration time.
    """
    expires_at = models.DateTimeField(null=True, blank=True)

    @property
    def is_expired(self):
        """Check if token is expired."""
        if not self.expires_at:
            return False
        return timezone.now() > self.expires_at

    @classmethod
    def create_token(cls, user, remember_me=False):
        """Create a new token with optional expiration.
        
        Args:
            user: The user this token belongs to
            remember_me: If True, token expires in 30 days. If False, expires in 3 days.
        """
        # Delete existing tokens for this user
        cls.objects.filter(user=user).delete()
        
        # Default to 3 days expiration
        expires_at = timezone.now() + timedelta(days=3)
        if remember_me:
            # If remember_me is True, extend to 30 days
            expires_at = timezone.now() + timedelta(days=30)
        
        return cls.objects.create(
            user=user,
            expires_at=expires_at
        )
    
    def clear_otp(self):
        """
        Clear the OTP code and related fields after successful verification.
        """
        self.otp_code = None
        self.otp_created_at = None
        self.otp_verified = True
        self.save(update_fields=['otp_code', 'otp_created_at', 'otp_verified'])


from django.dispatch import receiver
from django.db.models.signals import post_save


def validate_hex_color(value):
    import re
    if not re.match(r'^#([A-Fa-f0-9]{6})$', value):
        raise ValidationError("Color must be in the format #RRGGBB")


# Create your models here.


class CustomUser(AbstractBaseUser, PermissionsMixin):
    class UserType(models.TextChoices):
        STANDARD = 'standard', _('Standard User')
        ROOKIE_ENTERPRISE = 'rookie_enterprise', _('Rookie Enterprise Dashboard')
        GENERAL_ENTERPRISE = 'general_enterprise', _('General Enterprise Dashboard')
    
    email = models.EmailField(unique=True)
    username = models.CharField(max_length=30, null=True, blank=True)
    first_name = models.CharField(max_length=30, blank=True, null=True)
    last_name = models.CharField(max_length=30, blank=True, null=True)
    is_active = models.BooleanField(default=False)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(auto_now_add=True)
    is_verified = models.BooleanField(default=False)
    verification_code = models.CharField(max_length=6, blank=True, null=True)
    
    # Enterprise user type
    user_type = models.CharField(
        max_length=20,
        choices=UserType.choices,
        default=UserType.STANDARD,
        help_text=_('Designates the type of user.')
    )
    
    # Login tracking
    last_login_method = models.CharField(
        max_length=20,
        blank=True,
        null=True,
        choices=[
            ('password', 'Password'),
            ('email_link', 'Email Link'),
        ],
        help_text=_('Last login method used')
    )
    has_logged_in = models.BooleanField(
        default=False,
        help_text=_('Tracks if the user has logged in at least once.')
    )
    
    # SSO/Invitation fields
    sso_login_enabled = models.BooleanField(
        default=False,
        help_text=_('Whether the user can log in via email link (SSO)')
    )
    sso_login_code = models.CharField(
        max_length=64,
        blank=True,
        null=True,
        help_text=_('One-time code for email login')
    )
    sso_code_expires = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_('Expiration time for the SSO login code')
    )
    
    # 2FA OTP fields
    otp_code = models.CharField(
        max_length=6,
        blank=True,
        null=True,
        help_text=_('One-time password for 2FA')
    )
    otp_created_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_('When the OTP was created')
    )
    otp_verified = models.BooleanField(
        default=False,
        help_text=_('Whether the current OTP has been verified')
    )

    objects = CustomUserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []  # No required fields by default

    def __str__(self):
        return self.email
        
    def is_enterprise_user(self):
        """Check if the user is an enterprise user."""
        print(f"\n=== Checking if user is enterprise ===")
        print(f"User ID: {self.id}")
        print(f"Email: {self.email}")
        print(f"User type: {self.user_type}")
        print(f"User type class: {type(self.user_type).__name__}")
        
        # Get the actual string values for comparison
        user_type_str = str(self.user_type).lower()
        rookie_enterprise = str(self.UserType.ROOKIE_ENTERPRISE).lower()
        general_enterprise = str(self.UserType.GENERAL_ENTERPRISE).lower()
        
        print(f"Comparing user_type '{user_type_str}' with: {rookie_enterprise}, {general_enterprise}")
        
        # Check if the user_type matches either enterprise type (case-insensitive)
        is_enterprise = user_type_str in [rookie_enterprise, general_enterprise]
        print(f"Is enterprise user: {is_enterprise}")
        
        return is_enterprise
        
    def is_enterprise_admin(self):
        """Check if the user is an enterprise admin."""
        if not self.is_enterprise_user():
            return False
        return hasattr(self, 'enterprise_profile') and self.enterprise_profile.is_admin
    
    def get_enterprise(self):
        """Get the enterprise this user belongs to, if any."""
        if not self.is_enterprise_user():
            return None
        return getattr(self.enterprise_profile, 'enterprise', None)
        
    def generate_sso_code(self):
        """Generate a one-time code for email login."""
        import secrets
        from django.utils import timezone
        from datetime import timedelta
        
        self.sso_login_code = secrets.token_urlsafe(32)
        self.sso_code_expires = timezone.now() + timedelta(hours=24)  # Code valid for 24 hours
        self.save(update_fields=['sso_login_code', 'sso_code_expires'])
        return self.sso_login_code
        
    def verify_sso_code(self, code):
        """Verify if the provided SSO code is valid."""
        from django.utils import timezone
        
        if not self.sso_login_enabled or not self.sso_login_code or not self.sso_code_expires:
            return False
            
        if timezone.now() > self.sso_code_expires:
            return False
            
        return secrets.compare_digest(self.sso_login_code, code)
        
    def send_sso_login_email(self, request=None):
        """
        Public method to send an SSO login email to the user.
        
        Returns:
            bool: True if the email was sent successfully, False otherwise
        """
        print(f"\n=== SSO Login Email Requested ===")
        print(f"User: {self.email} (ID: {self.id})")
        print(f"SSO Enabled: {self.sso_login_enabled}")
        print(f"User Type: {self.user_type}")
        
        if not self.sso_login_enabled:
            print("SSO login is not enabled for this user")
            return False
            
        if not self.is_active:
            print("User account is not active")
            return False
            
        print("Proceeding to send SSO login email...")
        return self._send_sso_login_email(request)
        
    def clear_otp(self):
        """
        Clear the OTP code and related fields after successful verification.
        """
        self.otp_code = None
        self.otp_created_at = None
        self.otp_verified = True
        self.save(update_fields=['otp_code', 'otp_created_at', 'otp_verified'])
        
    def verify_sso_code(self, code):
        """
        Verify if the provided SSO login code is valid.
        
        Args:
            code (str): The 6-digit code to verify
            
        Returns:
            bool: True if the code is valid and not expired, False otherwise
        """
        if not code or not self.sso_login_code or not self.sso_code_expires:
            return False
            
        # Check if the code matches and is not expired
        is_valid = (
            code == self.sso_login_code and
            timezone.now() < self.sso_code_expires
        )
        
        print(f"\n=== Verifying SSO Code ===")
        print(f"Provided code: {code}")
        print(f"Stored code: {self.sso_login_code}")
        print(f"Code expires at: {self.sso_code_expires}")
        print(f"Current time: {timezone.now()}")
        print(f"Is valid: {is_valid}")
        
        # Clear the code after verification attempt
        if is_valid:
            self.sso_login_code = None
            self.sso_code_expires = None
            self.save(update_fields=['sso_login_code', 'sso_code_expires'])
            
        return is_valid
        
    def generate_otp(self):
        """
        Generate a new 6-digit OTP and save it with timestamp.
        
        Returns:
            str: The generated OTP code
        """
        import random
        from django.utils import timezone
        
        self.otp_code = f"{random.randint(0, 999999):06d}"  # 6-digit code with leading zeros
        self.otp_created_at = timezone.now()
        self.otp_verified = False
        self.save(update_fields=['otp_code', 'otp_created_at', 'otp_verified'])
        return self.otp_code
        
    def verify_otp(self, code):
        """
        Verify if the provided OTP code is valid.
        
        Args:
            code (str): The OTP code to verify
            
        Returns:
            bool: True if the OTP is valid and not expired, False otherwise
        """
        from django.utils import timezone
        from datetime import timedelta
        
        if not code or not self.otp_code or not self.otp_created_at:
            return False
            
        # OTP expires after 10 minutes
        expiry_time = self.otp_created_at + timedelta(minutes=10)
        is_valid = (
            code == self.otp_code and
            timezone.now() < expiry_time and
            not self.otp_verified
        )
        
        # If valid, mark as verified
        if is_valid:
            self.otp_verified = True
            self.save(update_fields=['otp_verified'])
            
        return is_valid
        
    def send_otp_email(self):
        """
        Send the OTP code to the user's email.
        
        Returns:
            bool: True if email was sent successfully, False otherwise
        """
        from .utils.email import send_email_via_ses
        from django.conf import settings
        
        # Generate a new OTP if one doesn't exist or is expired
        if not self.otp_code or not self.otp_created_at or \
           self.otp_created_at < (timezone.now() - timezone.timedelta(minutes=10)):
            self.generate_otp()
            
        subject = "Your Login Verification Code"
        message = f"""
        Your verification code is: {self.otp_code}
        
        This code will expire in 10 minutes.
        
        If you didn't request this code, please ignore this email.
        """
        
        html_message = f"""
        <h2>Your Verification Code</h2>
        <p>Your verification code is: <strong>{self.otp_code}</strong></p>
        <p>This code will expire in 10 minutes.</p>
        <p>If you didn't request this code, please ignore this email.</p>
        """
        
        try:
            send_email_via_ses(
                subject=subject,
                body=message,
                to_emails=[self.email],
                html_body=html_message
            )
            return True
        except Exception as e:
            print(f"Failed to send OTP email: {str(e)}")
            return False
            
    def _send_sso_login_email(self, request=None):
        """Send an email with a 6-digit login code to the user using the custom email utility."""
        from django.template.loader import render_to_string
        from django.conf import settings
        import random
        
        print(f"\n=== Preparing SSO Login Email ===")
        print(f"Recipient: {self.email}")
        
        try:
            # Generate a 6-digit code
            code = ''.join([str(random.randint(0, 9)) for _ in range(6)])
            
            # Save the code to the user model
            self.sso_login_code = code
            self.sso_code_expires = timezone.now() + timezone.timedelta(minutes=15)  # Code expires in 15 minutes
            self.save(update_fields=['sso_login_code', 'sso_code_expires'])
            
            context = {
                'user': self,
                'code': code,
                'expiry_minutes': 15,
                'site_name': getattr(settings, 'SITE_NAME', 'EngageX'),
            }
            
            print(f"SSO login code: {code}")
            
            subject = f'Your {getattr(settings, "SITE_NAME", "EngageX")} Login Code: {code}'
            print(f"Email subject: {subject}")
            
            text_content = render_to_string('emails/sso_login_code.txt', context)
            html_content = render_to_string('emails/sso_login_code.html', context)
            
            # Import here to avoid circular imports
            from users.utils.email import send_email_via_ses
            
            print("Sending email via SES...")
            result = send_email_via_ses(
                subject=subject,
                body=text_content,
                to_emails=[self.email],
                from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@engagexai.io'),
                html_body=html_content
            )
            
            if result:
                print("Email sent successfully!")
                return True
            else:
                print("Failed to send email")
                return False
                
        except Exception as e:
            print(f"Error sending email: {str(e)}")
            return False


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_user_profile(sender, instance, created, **kwargs):
    """Create a user profile when a new user is created."""
    if created:
        UserProfile.objects.create(user=instance)


class UserProfile(models.Model):
    GENDER_CHOICES = [
        ("M", "Male"),
        ("F", "Female"),
        ("P", "Prefer not to say"),
    ]
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="user_profile"
    )
    date_of_birth = models.DateField(null=True, blank=True)

    def clean(self):
        if self.date_of_birth and self.date_of_birth > timezone.now().date():
            raise ValidationError("Date of birth cannot be in the future.")

    @property
    def age(self):
        if self.date_of_birth:
            today = date.today()
            return (
                    today.year
                    - self.date_of_birth.year
                    - (
                            (today.month, today.day)
                            < (self.date_of_birth.month, self.date_of_birth.day)
                    )
            )
        return None

    gender = models.CharField(max_length=1, choices=GENDER_CHOICES, default="P")

    profile_picture = models.ImageField(
        storage=ProfilePicStorage(),
        upload_to="",
        null=True,
        blank=True,
        validators=[FileExtensionValidator(allowed_extensions=["jpg", "jpeg", "png"])],
        help_text="Upload a JPG, JPEG, or PNG image.",
    )

    # Role configuration: Only two roles.
    ADMIN = "admin"
    USER = "user"
    COACH = "coach"
    ROLE_CHOICES = [
        (ADMIN, "Admin"),
        (USER, "User"),
        (COACH, "Coach"),
    ]
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default=USER)

    # Onboarding fields updated to match UI
    PURPOSE_CHOICES = [
        ("pitch", "Pitch"),
        ("present", "Present"),
        ("speak_storytelling", "Speak/Storytelling"),
        ("interview", "Interview"),
    ]
    purpose = models.CharField(
        max_length=30, choices=PURPOSE_CHOICES, blank=True, null=True
    )

    def is_admin(self):
        return self.role == self.ADMIN

    def is_user(self):
        return self.role == self.USER

    def is_coach(self):
        return self.role == self.COACH

    # New signup field: user intent, now as a choice field.
    INTENT_CHOICES = [
        ("early", "Early Career Professional"),
        ("mid", "Mid-level Professionals"),
        ("sales", "Sales Professionals"),
        ("c_suite", "C-suites"),
        ("entrepreneur", "Entrepreneurs"),
        ("athlete", "Major League Sports Athlete"),
        ("executive", "Major League Sports Executive"),
    ]
    user_intent = models.CharField(
        max_length=50,
        choices=INTENT_CHOICES,
        blank=True,
        null=True,
        help_text="Select your career/intention level at sign up.",
    )

    # Dashboard field: available credits.
    available_credits = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=1.00,
        help_text="Available credits on the user dashboard.",
    )

    # Additional profile fields.
    quickbooks_customer_id = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        unique=True,
        help_text="QuickBooks Customer ID linked to this user."
    )
    
    country = models.CharField(
        max_length=100, blank=True, null=True, help_text="Country of the user."
    )
    timezone = models.CharField(
        max_length=50, blank=True, null=True, help_text="User's timezone."
    )
    company = models.CharField(
        max_length=100, blank=True, null=True, help_text="Company name."
    )

    phone_number = models.CharField(
        max_length=15, null=True, blank=True, help_text="User's phone number"
    )

    email_alert = models.BooleanField(default=False, null=True, blank=True)
    practice_reminder = models.BooleanField(default=False, null=True, blank=True)
    session_analysis = models.BooleanField(default=False, null=True, blank=True)

    INDUSTRY_CHOICES = [
        ("Media & Presentation", "Media & Presentation"),
        ("Technology", "Technology"),
        ("Healthcare", "Healthcare"),
        ("Finance", "Finance"),
        ("Major League Sports", "Major League Sports"),
        ("Others", "Others")
    ]
    industry = models.CharField(
        max_length=50,
        choices=INDUSTRY_CHOICES,
        blank=True,
        null=True,

    )

    # White-labeling fields
    logo = models.ImageField(
        storage=ProfilePicStorage(),
        upload_to="whitelabel/logo/",
        null=True,
        blank=True,
        validators=[FileExtensionValidator(allowed_extensions=["jpg", "jpeg", "png", "svg"])],
        help_text="Upload your company logo (JPG, PNG, or SVG)",
    )
    favicon = models.ImageField(
        storage=ProfilePicStorage(),
        upload_to="whitelabel/favicon/",
        null=True,
        blank=True,
        validators=[FileExtensionValidator(allowed_extensions=["ico", "png", "jpg"])],
        help_text="Upload your favicon (ICO, PNG, or JPG)",
    )
    primary_color = models.CharField(
        max_length=7,
        default="#262b3a",
        validators=[validate_hex_color],
        help_text="Primary brand color in hex format (e.g., #RRGGBB)",
    )
    secondary_color = models.CharField(
        max_length=7,
        default="#10161e",
        validators=[validate_hex_color],
        help_text="Secondary brand color in hex format (e.g., #RRGGBB)",
    )

    def __str__(self):
        return f"{self.user.email} - {self.role}"


class UserAssignment(models.Model):
    admin = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="assigned_users",
        blank=True,
        null=True,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="assigned_to"
    )
    assigned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("admin", "user")

    def __str__(self):
        return f"{self.admin.email} -> {self.user.email}"
