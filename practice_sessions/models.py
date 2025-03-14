from django.conf import settings
from django.db import models
from django.core.validators import MaxValueValidator, MinValueValidator
from django.utils import timezone
from datetime import timedelta

class PracticeSession(models.Model):
    SESSION_TYPE_CHOICES = [
        ('pitch', 'Pitch Practice'),
        ('public', 'Public Speaking'),
        ('presentation', 'Presentation'),
    ]
    # The user who created this session.
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="practice_sessions"
    )
    session_name = models.CharField(max_length=100)
    session_type = models.CharField(max_length=20, choices=SESSION_TYPE_CHOICES)
    date = models.DateTimeField(auto_now_add=True)
    duration = models.DurationField(help_text="Duration of the session")
    note = models.TextField(blank=True, null=True, help_text="Optional note (for users)")
    pauses = models.IntegerField(default=0)
    emotional_expression = models.TextField(blank=True, null=True)
    tone = models.TextField(blank=True, null=True)
    pronunciation = models.TextField(blank=True, null=True)
    content_organization = models.TextField(blank=True, null=True)
    emotional_impact = models.TextField(blank=True, null=True)
    audience_engagement = models.TextField(blank=True, null=True)
    transformative_potential = models.TextField(blank=True, null=True)
    visual_communication = models.TextField(blank=True, null=True)
    total_time_saved = models.IntegerField(default=0)
    slide_specific_timing = models.JSONField(default=dict, null=True, blank=True)

    # Add this field for slides upload
    slides = models.FileField(upload_to='session_slides/%Y/%m/%d/', blank=True, null=True, help_text="Optional slides for the session")

    def __str__(self):
        return f"{self.session_name} by {self.user.email}"

class SessionDetail(models.Model):
    session = models.OneToOneField(
        PracticeSession, on_delete=models.CASCADE, related_name="details"
    )
    # Audience Reactions (scale 0-100)
    engagement = models.PositiveIntegerField(default=0, validators=[MinValueValidator(0), MaxValueValidator(100)], help_text="Engagement score")
    emotional_connection = models.PositiveIntegerField(default=0, validators=[MinValueValidator(0), MaxValueValidator(100)], help_text="Emotional connection score")
    energy = models.PositiveIntegerField(default=0, validators=[MinValueValidator(0), MaxValueValidator(100)], help_text="Energy score")
    
    # Vocal Variety Analysis (scale 0-100)
    pitch_variation = models.PositiveIntegerField(default=0, validators=[MinValueValidator(0), MaxValueValidator(100)])
    volume_control = models.PositiveIntegerField(default=0, validators=[MinValueValidator(0), MaxValueValidator(100)])
    speech_rate = models.PositiveIntegerField(default=0, validators=[MinValueValidator(0), MaxValueValidator(100)])
    articulation = models.PositiveIntegerField(default=0, validators=[MinValueValidator(0), MaxValueValidator(100)])
    
    # Content Delivery Evaluation (scale 0-100)
    structure = models.PositiveIntegerField(default=0, validators=[MinValueValidator(0), MaxValueValidator(100)])
    impact = models.PositiveIntegerField(default=0, validators=[MinValueValidator(0), MaxValueValidator(100)])
    content_engagement = models.PositiveIntegerField(default=0, validators=[MinValueValidator(0), MaxValueValidator(100)])
    
    # Highlights and Areas for Improvement (JSON lists)
    strengths = models.JSONField(blank=True, null=True, help_text="List of strengths")
    areas_for_improvement = models.JSONField(blank=True, null=True, help_text="List of areas for improvement")
    
    def __str__(self):
        return f"Details for {self.session.session_name}"
