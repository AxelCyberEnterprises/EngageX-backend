from django.db import models
from django.core.exceptions import ValidationError
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from .managers import CustomUserManager
from django.conf import settings


from django.utils import timezone
from datetime import date

from django.core.validators import FileExtensionValidator
from django.utils.translation import gettext_lazy as _



# Create your models here.


class CustomUser(AbstractBaseUser, PermissionsMixin):
    email = models.EmailField(unique=True)
    username = models.CharField(max_length=30, null=True, blank=True)
    first_name = models.CharField(max_length=30, blank=True, null=True)  # Optional
    last_name = models.CharField(max_length=30, blank=True, null=True)   # Optional
    is_active = models.BooleanField(default=False)  # Set default to False
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(auto_now_add=True)
    is_verified = models.BooleanField(default=False)  # To track if the user has verified their email
    verification_code = models.CharField(max_length=6, blank=True, null=True)  # To store the 6-digit code

    objects = CustomUserManager()

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []  # No required fields by default

    def __str__(self):
        return self.email
    

class UserProfile(models.Model):
    GENDER_CHOICES = [
        ('M', 'Male'),
        ('F', 'Female'),
        ('P', 'Prefer not to say'),
    ]
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    date_of_birth = models.DateField(null=True, blank=True)

    def clean(self):
        if self.date_of_birth and self.date_of_birth > timezone.now().date():
            raise ValidationError("Date of birth cannot be in the future.")

    @property
    def age(self):
        if self.date_of_birth:
            today = date.today()
            return today.year - self.date_of_birth.year - ((today.month, today.day) < (self.date_of_birth.month, self.date_of_birth.day))
        return None

    gender = models.CharField(
        max_length=1,
        choices=GENDER_CHOICES,
        default='P'
    )

    profile_picture = models.ImageField(
        upload_to='profile_pictures/',
        null=True,
        blank=True,
        validators=[FileExtensionValidator(allowed_extensions=['jpg', 'jpeg', 'png'])],
        help_text="Upload a JPG, JPEG, or PNG image."
    )

    # New two-role configuration
    ADMIN = 'admin'
    USER = 'user'

    ROLE_CHOICES = [
        (ADMIN, 'Admin'),
        (USER, 'User'),
    ]

    role = models.CharField(
        max_length=20,
        choices=ROLE_CHOICES,
        default=USER
    )

    def is_admin(self):
        return self.role == self.ADMIN

    def is_user(self):
        return self.role == self.USER

    phone_number = models.CharField(max_length=15, null=True, blank=True, help_text="User's phone number")

    def __str__(self):
        return f"{self.user.email} - {self.role}"
    

class UserAssignment(models.Model):
    admin = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='assigned_users',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='assigned_to'
    )
    assigned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('admin', 'user')

    def __str__(self):
        return f"{self.admin.email} -> {self.user.email}"
