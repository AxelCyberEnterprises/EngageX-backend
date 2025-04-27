# models.py

from django.conf import settings
from django.db import models
from django.core.validators import MaxValueValidator, MinValueValidator, FileExtensionValidator
from django.utils import timezone
from datetime import timedelta
import uuid  # For generating unique sequence IDs

# *** The line below is where the ImportError is currently happening.
# *** We will attempt to work around this specific import issue below.
# from django.core.files.storage import get_storage_class, default_storage

# --- Manually load the custom storage backend class from settings ---
# This code tries to replicate what get_storage_class does internally,
# focusing on the backend path specified in your settings.py.

# Import default_storage here as it's still needed as a fallback
try:
    from django.core.files.storage import default_storage
except ImportError:
    # Fallback if default_storage also has import issues, though less likely
    print("Warning: Could not import default_storage. File field behavior may be unpredictable.")
    default_storage = None  # Assign None or raise an error if default_storage is essential

_storage_backend_path = settings.STORAGES.get("SlidesStorage", {}).get("BACKEND")
SlidesStorageInstance = default_storage  # Start with default_storage as the fallback

if _storage_backend_path:
    try:
        # Split the path into module and class name (e.g., "users.storages_backends", "SlidesStorage")
        _module_path, _class_name = _storage_backend_path.rsplit('.', 1)
        # Import the module dynamically
        _storage_module = __import__(_module_path, fromlist=[_class_name])
        # Get the class from the module
        SlidesStorageClass = getattr(_storage_module, _class_name)
        # Instantiate the class
        SlidesStorageInstance = SlidesStorageClass()
        # print(f"Successfully loaded custom SlidesStorage backend via manual import: {_storage_backend_path}")

    except (ImportError, KeyError, AttributeError) as e:
        print(f"Could not manually load custom SlidesStorage backend specified in settings: {e}. "
              f"Attempted path: {_storage_backend_path}. Falling back to default storage.")
        # Keep SlidesStorageInstance as default_storage if manual load fails
    except Exception as e:
        # Catch any other unexpected errors during manual loading
        print(f"An unexpected error occurred while loading SlidesStorage backend: {e}. "
              f"Attempted path: {_storage_backend_path}. Falling back to default storage.")
        # Keep SlidesStorageInstance as default_storage


else:
    print("Settings.STORAGES['SlidesStorage']['BACKEND'] is not defined. Using default storage.")
    # Keep SlidesStorageInstance as default_storage if the path is not set


# --- End manual loading of custom storage backend ---


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
    goals = models.JSONField(default=list, blank=True, null=True)
    date = models.DateTimeField(auto_now_add=True)
    duration = models.DurationField(
        help_text="Duration of the session", null=True, blank=True
    )
    note = models.TextField(
        blank=True, null=True, help_text="Optional note (for users)"
    )

    # --- Replace original slides_URL with slides_file (FileField) ---
    slides_file = models.FileField(
        # Pass the *instance* of the storage class obtained above
        storage=SlidesStorageInstance,  # <-- Use the obtained storage instance here (either custom or default)
        upload_to='slides/',  # <-- Specify the subdirectory within that storage
        blank=True,
        null=True,
        # Add validators if you want to restrict file types (e.g., PDF, PPT, images)
        validators=[FileExtensionValidator(
            allowed_extensions=['pdf', 'ppt', 'pptx', 'odp', 'key', 'jpg', 'jpeg', 'png', 'gif'])],
        help_text="Upload presentation slides (e.g., PDF, PPT, image files).",
    )
    # --- End Change ---

    slide_specific_timing = models.JSONField(default=dict, null=True, blank=True)
    allow_ai_questions = models.BooleanField(
        default=False, help_text="Allow AI to ask random questions during the session"
    )
    VIRTUAL_ENVIRONMENT_CHOICES = [
        ("conference_room", "Conference Room"),
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
    sequence = models.ForeignKey(
        PracticeSequence,  # Use the class directly if defined above
        on_delete=models.SET_NULL,
        related_name="sessions",
        null=True,
        blank=True,
        help_text="Optional sequence this session belongs to",
    )

    # New fields and renamed fields for sentiment analysis response (aggregated for the session)
    volume = models.IntegerField(default=0, help_text="Average volume of the session")
    pitch_variability = models.IntegerField(
        default=0, help_text="Average pitch variability of the session"
    )
    pace = models.IntegerField(default=0, help_text="Average pace of the session")
    pauses = models.IntegerField(
        default=0, help_text="Average pause score for the session (expected over 100)"  # Updated help text for clarity
    )
    conviction = models.IntegerField(
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Overall conviction score for the session",
    )
    clarity = models.IntegerField(
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Overall clarity score for the session",
    )
    impact = models.IntegerField(
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Overall impact score for the session",
    )
    brevity = models.IntegerField(
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Overall brevity score for the session",
    )
    trigger_response = models.IntegerField(
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Overall trigger response score for the session",
    )
    filler_words = models.IntegerField(
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Overall filler words score for the session",
    )
    grammar = models.IntegerField(
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Overall grammar score for the session",
    )
    posture = models.IntegerField(
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Overall posture score for the session",
    )
    motion = models.IntegerField(
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Overall motion score for the session",
    )
    gestures = models.BooleanField(
        default=False, help_text="Presence of positive gestures in the session"
    )
    gestures_score_for_body_language = models.IntegerField(
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Score for gestures in body language analysis",
    )
    transformative_potential = models.IntegerField(
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Overall transformative potential score for the session",
    )
    general_feedback_summary = models.TextField(
        blank=True, null=True, help_text="General feedback summary for the session"
    )
    strength = models.TextField(
        blank=True, null=True, help_text="Key strengths identified in the session"
    )
    area_of_improvement = models.TextField(
        blank=True, null=True, help_text="Areas for improvement in the session"
    )

    # New calculated fields (Let's keep them as FloatField for now as they represent averages)
    audience_engagement = models.FloatField(
        default=0, help_text="Average of impact, trigger response, and conviction"
    )
    overall_captured_impact = models.FloatField(
        default=0, help_text="Overall captured impact (same as impact)"
    )
    vocal_variety = models.FloatField(
        default=0, help_text="Average of volume, pitch, pace, and pauses (expected over 100)"  # Updated help text
    )
    emotional_impact = models.FloatField(
        default=0, help_text="Emotional impact (same as trigger response)"
    )
    body_language = models.FloatField(
        default=0, help_text="Score derived from posture, motion, and gestures"
    )
    transformative_communication = models.FloatField(
        default=0,
        help_text="Transformative communication (same as transformative potential)",
    )
    structure_and_clarity = models.FloatField(
        default=0, help_text="Overall score for structure and clarity"
    )
    language_and_word_choice = models.FloatField(
        default=0, help_text="Average of brevity, filler words, and grammar"
    )
    slide_efficiency = models.FloatField(
        default=0, help_text=""
    )
    text_economy = models.FloatField(
        default=0, help_text=""
    )
    visual_communication = models.FloatField(
        default=0, help_text=""
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
    # New fields for chunk processing
    chunk_number = models.IntegerField(
        null=True,
        blank=True,
        help_text="Order of the chunk in the session",
    )
    transcript = models.TextField(
        blank=True,
        null=True,
        help_text="Transcript of this chunk",
    )
    audio_path = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text="Path to the audio file for this chunk",
    )
    created_at = models.DateTimeField(
        default=timezone.now,
        help_text="When this chunk was created",
    )
    updated_at = models.DateTimeField(
        default=timezone.now,
        help_text="When this chunk was last updated",
    )

    class Meta:
        ordering = ['chunk_number', 'created_at']

    def __str__(self):
        return (
            f"Chunk {self.chunk_number or self.start_time}-{self.end_time} for {self.session.session_name}"
        )


class ChunkSentimentAnalysis(models.Model):
    chunk = models.OneToOneField(
        SessionChunk, on_delete=models.CASCADE, related_name="sentiment_analysis"
    )

    chunk_number = models.IntegerField(
        default=0, help_text="Order of the chunk in the session"
    )

    # Scores from analysis models (renamed for clarity)
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
    general_feedback_summary = models.TextField(blank=True, null=True)

    # Metrics from audio analysis
    volume = models.FloatField(null=True, blank=True, help_text="Volume")
    pitch_variability = models.FloatField(
        null=True, blank=True, help_text="Pitch variability"
    )
    pace = models.FloatField(null=True, blank=True, help_text="Pace")
    chunk_transcript = models.TextField(blank=True, null=True, help_text="Transcript")

    # New fields for sentiment analysis response
    trigger_response = models.IntegerField(  # Renamed from original field in your other model def
        default=0, help_text="Number of trigger responses detected"
        # Help text suggests count, but logs show score? Clarify this.
    )
    filler_words = models.IntegerField(  # Renamed
        default=0, help_text="Number of filler words used"
        # Help text suggests count, but logs show score? Clarify this.
    )
    grammar = models.IntegerField(  # Renamed
        default=0, help_text="Grammar score or number of errors"  # Help text ambiguous
    )
    posture = models.IntegerField(  # Renamed
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Posture score",
    )
    motion = models.IntegerField(  # Renamed
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Motion score",
    )
    gestures = models.BooleanField(  # Renamed
        default=False, help_text="Presence of positive gestures"
    )
    pauses = models.IntegerField(default=0,
                                 help_text="Pause score for this chunk (expected over 100)")  # Updated help text for clarity

    def __str__(self):
        return f"Sentiment Analysis for Chunk {self.chunk.start_time}-{self.chunk.end_time} of {self.chunk.session.session_name}"

# Remember to run makemigrations and migrate after changing the model.
