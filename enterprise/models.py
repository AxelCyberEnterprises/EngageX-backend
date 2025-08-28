from django.db import models
from django.conf import settings
from django.core.validators import FileExtensionValidator
from django.utils.translation import gettext_lazy as _
from django.core.exceptions import ValidationError

def validate_hex_color(value):
    import re
    if not re.match(r'^#([A-Fa-f0-9]{6})$', value):
        raise ValidationError("Color must be in the format #RRGGBB")

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
        COACHING = 'coaching', _('Coaching')
        PITCH = 'pitch', _('Pitch')
        PRESENTATION = 'presentation', _('Presentation')
        PUBLIC_SPEAKING = 'public_speaking', _('Public Speaking')
        
        def __str__(self):
            return str(self.label)

    
    name = models.CharField(max_length=255, unique=True)
    enterprise_type = models.CharField(
        max_length=10,
        choices=EnterpriseType.choices,
        default=EnterpriseType.SPORT,
        help_text="Type of enterprise"
    )
    sport_type = models.CharField(
        max_length=10,
        choices=[
            ('nfl', 'NFL'),
            ('nba', 'NBA'),
            ('wnba', 'WNBA'),
            ('mlb', 'MLB')
        ],
        blank=True,
        null=True,
        help_text="Primary sport type for this enterprise (if applicable)"
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    # Enterprise settings
    one_on_one_coaching_link = models.URLField(
        max_length=500,
        default=True,
        blank=True,
        null=True,
        help_text="Link for 1-on-1 coaching booking"
    )
    coaching_sessions_booked = models.PositiveIntegerField(
        default=0,
        help_text="Number of coaching sessions booked through the one-on-one coaching link"
    )
    accessible_verticals = models.JSONField(
        default=list,
        help_text="List of vertical IDs from RoomEnum that are accessible"
    )
    
    # Branding fields
    logo = models.ImageField(
        upload_to='enterprise/logos/',
        null=True,
        blank=True,
        validators=[FileExtensionValidator(allowed_extensions=['jpg', 'jpeg', 'png', 'svg'])]
    )
    favicon = models.ImageField(
        upload_to='enterprise/favicon/',
        null=True,
        blank=True,
        validators=[FileExtensionValidator(allowed_extensions=['ico', 'png', 'jpg'])],
        help_text="Upload your favicon (ICO, PNG, or JPG)",
    )
    primary_color = models.CharField(
        max_length=7,
        default='#262b3a',
        validators=[validate_hex_color],
        help_text="Primary brand color in hex format (e.g., #RRGGBB)",
    )
    secondary_color = models.CharField(
        max_length=7,
        default='#10161e',
        validators=[validate_hex_color],
        help_text="Secondary brand color in hex format (e.g., #RRGGBB)",
    )
    
    class Meta:
        ordering = ['name']
        verbose_name_plural = "Enterprises"
    
    def get_default_verticals(self):
        """Return the default verticals based on enterprise type
        
        Returns:
            list: List of vertical tuples (value, label) for the enterprise type
            - Sport enterprises: media_training, coach, gm, pitch, presentation, public_speaking
            - General enterprises: coaching, pitch, presentation, public_speaking
        """
        if self.enterprise_type == self.EnterpriseType.GENERAL:
            return [
                self.Vertical.COACHING,
                self.Vertical.PITCH,
                self.Vertical.PRESENTATION,
                self.Vertical.PUBLIC_SPEAKING
            ]
        else:  # SPORT enterprise
            return [
                self.Vertical.MEDIA_TRAINING,
                self.Vertical.COACH,
                self.Vertical.GM,
                self.Vertical.PITCH,
                self.Vertical.PRESENTATION,
                self.Vertical.PUBLIC_SPEAKING
            ]
    
    def get_available_verticals(self):
        """
        Return the list of available verticals based on enterprise type.
        
        Returns:
            list: List of (value, label) tuples representing all verticals
                  available for this enterprise type
        """
        # Simply return all default verticals for the enterprise type
        # The frontend will handle which ones are currently enabled
        return self.get_default_verticals()
    
    def set_accessible_verticals(self, vertical_codes):
        """Set the accessible verticals for this enterprise"""
        if not isinstance(vertical_codes, list):
            raise ValueError("vertical_codes must be a list")
            
        # Validate that all provided vertical codes are valid
        valid_codes = [code for code, _ in self.Vertical.choices]
        for code in vertical_codes:
            if code not in valid_codes:
                raise ValueError(f"Invalid vertical code: {code}")
                
        # Only keep verticals that are valid for this enterprise type
        default_verticals = [v[0] for v in self.get_default_verticals()]
        self.accessible_verticals = [v for v in vertical_codes if v in default_verticals]
        return self.accessible_verticals
    
    def clean(self):
        """Validate and initialize enterprise verticals
        
        - Sets default verticals if none are configured
        - Ensures only valid verticals for the enterprise type are allowed
        - Validates that at least one vertical is enabled
        """
        super().clean()
        
        # Initialize with default verticals if none are set
        if not hasattr(self, 'accessible_verticals') or not self.accessible_verticals:
            self.accessible_verticals = [v[0] for v in self.get_default_verticals()]
        
        # Ensure all selected verticals are valid for this enterprise type
        valid_verticals = [v[0] for v in self.get_default_verticals()]
        self.accessible_verticals = [v for v in self.accessible_verticals if v in valid_verticals]
        
        # Ensure at least one vertical is selected
        if not self.accessible_verticals:
            raise ValidationError("At least one vertical must be enabled for the enterprise")
    
    @property
    def current_credits(self):
        """
        Returns the current available credits for this enterprise.
        Returns 0 if no credit record exists.
        """
        from payments.models import Credit
        try:
            return Credit.objects.get(enterprise=self).balance
        except Credit.DoesNotExist:
            return 0

    def __str__(self):
        return f"{self.name} (Credits: {self.current_credits})"


class EnterpriseUser(models.Model):
    """
    Model representing the relationship between a user and an enterprise.
    """
    class UserType(models.TextChoices):
        STANDARD = 'standard', _('Standard User')
        ROOKIE_ENTERPRISE = 'rookie_enterprise', _('Rookie Enterprise Dashboard')
        GENERAL_ENTERPRISE = 'general_enterprise', _('General Enterprise Dashboard')
        
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
        max_length=20,
        choices=UserType.choices,
        default=UserType.GENERAL_ENTERPRISE
    )
    is_admin = models.BooleanField(
        default=False,
        help_text="Designates whether the user can manage enterprise settings and users"
    )
    
    role = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        help_text="User's role within the enterprise"
    )
    
    team = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        help_text="User's team within the enterprise"
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    @property
    def effective_logo(self):
        """Return enterprise logo."""
        return self.enterprise.logo
        
    @property
    def effective_favicon(self):
        """Return enterprise favicon."""
        return self.enterprise.favicon
        
    @property
    def effective_primary_color(self):
        """Return enterprise primary color."""
        return self.enterprise.primary_color
        
    @property
    def effective_secondary_color(self):
        """Return enterprise secondary color."""
        return self.enterprise.secondary_color
    
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
    sport_type = models.CharField(
        max_length=10,
        choices=[
            ('nfl', 'NFL'),
            ('nba', 'NBA'),
            ('wnba', 'WNBA'),
            ('mlb', 'MLB')
        ],
        blank=True,
        null=True,
        help_text="Sport type this question is associated with (if applicable)"
    )
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
        return f"{self.question_text[:50]}..." if len(self.question_text) > 50 else self.question_text


class TrainingGoal(models.Model):
    """
    Model representing training goals for an enterprise.
    Tracks progress towards session targets in different rooms/verticals.
    """
    class RoomType(models.TextChoices):
        PRESENTATION = 'presentation', _('Presentation')
        PITCH = 'pitch', _('Pitch')
        PUBLIC_SPEAKING = 'public_speaking', _('Public Speaking')
        MEDIA_TRAINING = 'media_training', _('Media Training')
        COACH = 'coach', _('Coach')
        GENERAL_MANAGER = 'general_manager', _('General Manager')
        COACHING = 'coaching', _('Coaching')
    
    enterprise = models.ForeignKey(
        Enterprise,
        on_delete=models.CASCADE,
        related_name='training_goals',
        help_text="Enterprise this goal belongs to"
    )
    room = models.CharField(
        max_length=20,
        choices=RoomType.choices,
        help_text="Type of room/vertical for this goal"
    )
    target_sessions = models.PositiveIntegerField(
        help_text="Target number of sessions to complete"
    )
    completed_sessions = models.PositiveIntegerField(
        default=0,
        help_text="Number of sessions completed so far"
    )
    due_date = models.DateField(
        null=True,
        blank=True,
        help_text="Optional due date for this goal"
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Whether this goal is currently active"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-is_active', 'due_date', 'room']
        unique_together = ['enterprise', 'room', 'is_active']
    
    def __str__(self):
        return f"{self.get_room_display()} - {self.completed_sessions}/{self.target_sessions} (Enterprise: {self.enterprise.name})"
    
    @property
    def progress_percent(self):
        """Calculate completion percentage"""
        if self.target_sessions == 0:
            return 0
        return min(100, int((self.completed_sessions / self.target_sessions) * 100))
    
    @property
    def is_completed(self):
        """Check if goal is completed"""
        return self.completed_sessions >= self.target_sessions
    
    def clean(self):
        """Validate that room type is allowed for the enterprise"""
        if self.enterprise.enterprise_type == Enterprise.EnterpriseType.GENERAL and \
           self.room not in [self.RoomType.COACHING, self.RoomType.PRESENTATION, 
                           self.RoomType.PITCH, self.RoomType.PUBLIC_SPEAKING, 
                           self.RoomType.MEDIA_TRAINING]:
            raise ValidationError(
                f"Invalid room type '{self.get_room_display()}' for general enterprise"
            )
