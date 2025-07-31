from django.db import models
from django.conf import settings
from django.core.validators import FileExtensionValidator
from django.utils.translation import gettext_lazy as _
from django.core.exceptions import ValidationError

class Enterprise(models.Model):
    """
    Model representing an enterprise organization.
    """
    class EnterpriseType(models.TextChoices):
        SPORT = 'sport', _('Sport Enterprise')
        GENERAL = 'general', _('General Enterprise')
    
    class Vertical(models.TextChoices):
        MEDIA_TRAINING = 'media_training', _('Media Training')
        COACH = 'coach', _('Coach')
        GM = 'gm', _('General Manager')
    
    name = models.CharField(max_length=255, unique=True)
    domain = models.CharField(max_length=255, unique=True, help_text="Primary domain of the enterprise email")
    enterprise_type = models.CharField(
        max_length=10,
        choices=EnterpriseType.choices,
        default=EnterpriseType.SPORT,
        help_text="Type of enterprise"
    )
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
    
    def get_available_verticals(self):
        """Return the list of available verticals based on enterprise type"""
        if self.enterprise_type == self.EnterpriseType.GENERAL:
            return [self.Vertical.COACH]
        return [
            self.Vertical.MEDIA_TRAINING,
            self.Vertical.COACH,
            self.Vertical.GM
        ]
    
    def clean(self):
        """Validate that the enterprise has valid verticals"""
        super().clean()
        if not self.get_available_verticals():
            raise ValidationError("Invalid enterprise type")
    
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
    # department = models.CharField(max_length=100, blank=True, null=True)
    # position = models.CharField(max_length=100, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        unique_together = ('user', 'enterprise')
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.user.email} - {self.enterprise.name}"


class EnterpriseQuestion(models.Model):
    """
    Model representing questions for enterprise verticals.
    Each question belongs to a specific enterprise and vertical.
    The same question text can exist across different enterprises and verticals,
    but must be unique within each enterprise/vertical combination.
    """
    enterprise = models.ForeignKey(
        Enterprise,
        on_delete=models.CASCADE,
        related_name='questions',
        help_text="Enterprise this question belongs to"
    )
    vertical = models.CharField(
        max_length=20,
        choices=Enterprise.Vertical.choices,
        help_text="Vertical this question is associated with"
    )
    question_text = models.TextField(help_text="The actual question text")
    audio_url = models.URLField(
        max_length=500,
        blank=True,
        null=True,
        help_text="URL to the generated audio file in S3"
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Whether this question is active and can be used"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['enterprise', 'vertical', 'created_at']
        verbose_name = "Enterprise Question"
        verbose_name_plural = "Enterprise Questions"
        constraints = [
            models.UniqueConstraint(
                fields=['enterprise', 'vertical', 'question_text'],
                name='unique_question_per_enterprise_vertical',
                # This ensures that the same question text can exist in different enterprises or verticals,
                # but not within the same enterprise/vertical combination
            )
        ]

    def clean(self):
        """
        Validate that:
        1. The vertical is allowed for the enterprise
        2. The question text is unique within the same enterprise/vertical
        """
        super().clean()
        
        if self.enterprise and self.vertical:
            available_verticals = self.enterprise.get_available_verticals()
            
            # Check if vertical is allowed for this enterprise type
            if self.vertical not in available_verticals:
                raise ValidationError({
                    'vertical': f"Vertical '{self.vertical}' is not available for this enterprise type"
                })
            
            # Check for duplicate question in the same enterprise/vertical
            if self._state.adding or self.enterprise_id or self.vertical or self.question_text:
                qs = EnterpriseQuestion.objects.filter(
                    enterprise=self.enterprise,
                    vertical=self.vertical,
                    question_text__iexact=self.question_text.strip()
                )
                
                if self.pk:
                    qs = qs.exclude(pk=self.pk)
                    
                if qs.exists():
                    raise ValidationError({
                        'question_text': 'This question already exists for this enterprise and vertical.'
                    })

    def save(self, *args, **kwargs):
        # Clean and validate before saving
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.enterprise.name} - {self.get_vertical_display()}: {self.question_text[:50]}..."
