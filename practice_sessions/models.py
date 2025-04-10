from django.conf import settings
from django.db import models
from django.core.validators import MaxValueValidator, MinValueValidator
from django.utils import timezone
from datetime import timedelta
import uuid  # For generating unique sequence IDs


class PracticeSequence(models.Model):
    """Represents a sequence of practice sessions for improvement."""

    sequence_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sequence_name = models.CharField(max_length=100)
    description = models.TextField(blank=True, null=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="practice_sequences",
    )

    def __str__(self):
        return f"{self.sequence_name} by {self.user.email}"


class PracticeSession(models.Model):
    SESSION_TYPE_CHOICES = [
        ("pitch", "Pitch Practice"),
        ("public", "Public Speaking"),
        ("presentation", "Presentation"),
    ]
    # The user who created this session.
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="practice_sessions",
    )
    session_name = models.CharField(max_length=100)
    session_type = models.CharField(max_length=20, choices=SESSION_TYPE_CHOICES)
    goals = models.TextField(null=True, blank=True, default="Just practicing")
    date = models.DateTimeField(auto_now_add=True)
    duration = models.DurationField(
        help_text="Duration of the session", null=True, blank=True
    )
    note = models.TextField(
        blank=True, null=True, help_text="Optional note (for users)"
    )
    pauses = models.IntegerField(default=0)  # Aggregated
    emotional_expression = models.TextField(blank=True, null=True)  # Aggregated
    tone = models.TextField(blank=True, null=True)  # Aggregated
    impact = models.TextField(blank=True, null=True)  # Aggregated
    audience_engagement = models.TextField(blank=True, null=True)  # Aggregated
    transformative_potential = models.TextField(blank=True, null=True)  # Aggregated
    visual_communication = models.TextField(blank=True, null=True)  # Aggregated
    total_time_saved = models.IntegerField(default=0)  # Aggregated
    slide_specific_timing = models.JSONField(default=dict, null=True, blank=True)
    slides = models.FileField(
        upload_to="session_slides/%Y/%m/%d/",
        blank=True,
        null=True,
        help_text="Optional slides for the session",
    )
    sequence = models.ForeignKey(
        PracticeSequence,
        on_delete=models.SET_NULL,  # If a sequence is deleted, keep the sessions but disassociate
        related_name="sessions",
        null=True,
        blank=True,
        help_text="Optional sequence this session belongs to",
    )
    allow_ai_questions = models.BooleanField(
        default=False, help_text="Allow AI to ask random questions during the session"
    )
    VIRTUAL_ENVIRONMENT_CHOICES = [
        ("conference_room", "Conference Room"),
        ("seminar_room", "Seminar Room"),
        ("board_room_1", "Board Room 1"),
        ("board_room_2", "Board Room 2"),
    ]
    virtual_environment = models.CharField(
        max_length=50,
        choices=VIRTUAL_ENVIRONMENT_CHOICES,
        blank=True,
        null=True,
        help_text="Select a virtual environment.",
    )

    allow_ai_questions = models.BooleanField(
        default=False, help_text="Allow AI to ask random questions during the session"
    )

    def __str__(self):
        return f"{self.session_name} by {self.user.email}"


class SessionChunk(models.Model):
    session = models.ForeignKey(
        PracticeSession, on_delete=models.CASCADE, related_name="chunks"
    )
    start_time = models.FloatField(
        blank=True,
        null=True,
        help_text="Start time of the chunk in the session (in seconds)",
    )
    end_time = models.FloatField(
        blank=True,
        null=True,
        help_text="End time of the chunk in the session (in seconds)",
    )
    video_file = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text="Video file for this chunk",
    )
    # Add more fields if you need to store specific information about the chunk itself

    def __str__(self):
        return (
            f"Chunk {self.start_time}-{self.end_time} for {self.session.session_name}"
        )


class ChunkSentimentAnalysis(models.Model):
    chunk = models.OneToOneField(
        SessionChunk, on_delete=models.CASCADE, related_name="sentiment_analysis"
    )

    # Scores from OpenAI's GPT model
    engagement = models.PositiveIntegerField(
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Engagement Heatmap",
    )
    audience_emotion = models.CharField(
        max_length=50, blank=True, null=True, help_text="Audience Emotion"
    )
    conviction = models.PositiveIntegerField(
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Convictions",
    )
    clarity = models.PositiveIntegerField(
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Clarity",
    )
    impact = models.PositiveIntegerField(
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="impact",
    )
    brevity = models.PositiveIntegerField(
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Brevity",
    )
    transformative_potential = models.PositiveIntegerField(
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="transformative potential",
    )
    body_posture = models.PositiveIntegerField(
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Body posture",
    )
    general_feedback_summary = models.TextField(blank=True, null=True)

    # Metrics from audio analysis
    volume = models.FloatField(null=True, blank=True, help_text="Volume")
    pitch_variability = models.FloatField(
        null=True, blank=True, help_text="Pitch variability"
    )
    pace = models.FloatField(null=True, blank=True, help_text="Pace")
    chunk_transcript = models.TextField(blank=True, null=True, help_text="Transcript")

    def __str__(self):
        return f"Sentiment Analysis for Chunk {self.chunk.start_time}-{self.chunk.end_time} of {self.chunk.session.session_name}"
