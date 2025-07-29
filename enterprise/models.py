from django.db import models
from django.conf import settings
from django.core.validators import FileExtensionValidator
from django.utils.translation import gettext_lazy as _

class Enterprise(models.Model):
    """
    Model representing an enterprise organization.
    """
    name = models.CharField(max_length=255, unique=True)
    domain = models.CharField(max_length=255, unique=True, help_text="Primary domain of the enterprise email")
    logo = models.ImageField(
        upload_to='enterprise/logos/',
        null=True,
        blank=True,
        validators=[FileExtensionValidator(allowed_extensions=['jpg', 'jpeg', 'png', 'svg'])]
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    # Enterprise settings
    require_domain_match = models.BooleanField(
        default=True,
        help_text="Require user emails to match the enterprise domain"
    )
    
    class Meta:
        ordering = ['name']
        verbose_name_plural = "Enterprises"
    
    def __str__(self):
        return self.name


class EnterpriseUser(models.Model):
    """
    Model representing the relationship between a user and an enterprise.
    """
    class UserType(models.TextChoices):
        ROOKIE = 'rookie', _('Rookie Enterprise Dashboard')
        GENERAL = 'general', _('General Enterprise Dashboard')
    
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='enterprise_profile'
    )
    enterprise = models.ForeignKey(
        Enterprise,
        on_delete=models.CASCADE,
        related_name='users'
    )
    user_type = models.CharField(
        max_length=10,
        choices=UserType.choices,
        default=UserType.GENERAL
    )
    is_admin = models.BooleanField(
        default=False,
        help_text="Designates whether the user can manage enterprise settings and users"
    )
    department = models.CharField(max_length=100, blank=True, null=True)
    position = models.CharField(max_length=100, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        unique_together = ('user', 'enterprise')
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.user.email} - {self.enterprise.name}"
