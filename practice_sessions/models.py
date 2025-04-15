# from django.conf import settings
# from django.db import models
# from django.core.validators import MaxValueValidator, MinValueValidator
# from django.utils import timezone
# from datetime import timedelta
# import uuid  # For generating unique sequence IDs


# class PracticeSequence(models.Model):
#     """Represents a sequence of practice sessions for improvement."""

#     sequence_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
#     sequence_name = models.CharField(max_length=100)
#     description = models.TextField(blank=True, null=True)
#     user = models.ForeignKey(
#         settings.AUTH_USER_MODEL,
#         on_delete=models.CASCADE,
#         related_name="practice_sequences",
#     )

#     def __str__(self):
#         return f"{self.sequence_name} by {self.user.email}"


# class PracticeSession(models.Model):
#     SESSION_TYPE_CHOICES = [
#         ("pitch", "Pitch Practice"),
#         ("public", "Public Speaking"),
#         ("presentation", "Presentation"),
#     ]
#     # The user who created this session.
#     user = models.ForeignKey(
#         settings.AUTH_USER_MODEL,
#         on_delete=models.CASCADE,
#         related_name="practice_sessions",
#     )
#     session_name = models.CharField(max_length=100)
#     session_type = models.CharField(max_length=20, choices=SESSION_TYPE_CHOICES)
#     goals = models.JSONField(default=list, blank=True, null=True)
#     date = models.DateTimeField(auto_now_add=True)
#     duration = models.DurationField(
#         help_text="Duration of the session", null=True, blank=True
#     )
#     note = models.TextField(
#         blank=True, null=True, help_text="Optional note (for users)"
#     )
#     pauses = models.IntegerField(default=0)  # Aggregated
#     emotional_expression = models.TextField(blank=True, null=True)  # Aggregated
#     tone = models.TextField(blank=True, null=True)  # Aggregated
#     impact = models.TextField(blank=True, null=True)  # Aggregated
#     audience_engagement = models.TextField(blank=True, null=True)  # Aggregated
#     transformative_potential = models.TextField(blank=True, null=True)  # Aggregated
#     visual_communication = models.TextField(blank=True, null=True)  # Aggregated
#     total_time_saved = models.IntegerField(default=0)  # Aggregated
#     slide_specific_timing = models.JSONField(default=dict, null=True, blank=True)
#     slides = models.FileField(
#         upload_to="session_slides/%Y/%m/%d/",
#         blank=True,
#         null=True,
#         help_text="Optional slides for the session",
#     )
#     sequence = models.ForeignKey(
#         PracticeSequence,
#         on_delete=models.SET_NULL,  # If a sequence is deleted, keep the sessions but disassociate
#         related_name="sessions",
#         null=True,
#         blank=True,
#         help_text="Optional sequence this session belongs to",
#     )
#     allow_ai_questions = models.BooleanField(
#         default=False, help_text="Allow AI to ask random questions during the session"
#     )
#     VIRTUAL_ENVIRONMENT_CHOICES = [
#         ("conference_room", "Conference Room"),
#         ("board_room_1", "Board Room 1"),
#         ("board_room_2", "Board Room 2"),
#     ]
#     virtual_environment = models.CharField(
#         max_length=50,
#         choices=VIRTUAL_ENVIRONMENT_CHOICES,
#         blank=True,
#         null=True,
#         help_text="Select a virtual environment.",
#     )

#     allow_ai_questions = models.BooleanField(
#         default=False, help_text="Allow AI to ask random questions during the session"
#     )

#     # New fields for sentiment analysis response (aggregated for the session)
#     trigger_response = models.IntegerField(
#         default=0, help_text="Total trigger responses detected in the session"
#     )
#     filler_words = models.IntegerField(
#         default=0, help_text="Total filler words used in the session"
#     )
#     grammar = models.IntegerField(
#         default=0, help_text="Overall grammar score or number of errors in the session"
#     )
#     posture = models.IntegerField(
#         default=0,
#         validators=[MinValueValidator(0), MaxValueValidator(100)],
#         help_text="Overall posture score for the session",
#     )
#     motion = models.IntegerField(
#         default=0,
#         validators=[MinValueValidator(0), MaxValueValidator(100)],
#         help_text="Overall motion score for the session",
#     )
#     gestures = models.BooleanField(
#         default=False, help_text="Presence of positive gestures in the session"
#     )

#     def __str__(self):
#         return f"{self.session_name} by {self.user.email}"


# class SessionChunk(models.Model):
#     session = models.ForeignKey(
#         PracticeSession, on_delete=models.CASCADE, related_name="chunks"
#     )
#     start_time = models.FloatField(
#         blank=True,
#         null=True,
#         help_text="Start time of the chunk in the session (in seconds)",
#     )
#     end_time = models.FloatField(
#         blank=True,
#         null=True,
#         help_text="End time of the chunk in the session (in seconds)",
#     )
#     video_file = models.CharField(
#         max_length=255,
#         blank=True,
#         null=True,
#         help_text="Video file for this chunk",
#     )
#     # Add more fields if you need to store specific information about the chunk itself

#     def __str__(self):
#         return (
#             f"Chunk {self.start_time}-{self.end_time} for {self.session.session_name}"
#         )


# class ChunkSentimentAnalysis(models.Model):
#     chunk = models.OneToOneField(
#         SessionChunk, on_delete=models.CASCADE, related_name="sentiment_analysis"
#     )

#     # Scores from OpenAI's GPT model
#     engagement = models.PositiveIntegerField(
#         default=0,
#         validators=[MinValueValidator(0), MaxValueValidator(100)],
#         help_text="Engagement Heatmap",
#     )
#     audience_emotion = models.CharField(
#         max_length=50, blank=True, null=True, help_text="Audience Emotion"
#     )
#     conviction = models.PositiveIntegerField(
#         default=0,
#         validators=[MinValueValidator(0), MaxValueValidator(100)],
#         help_text="Convictions",
#     )
#     clarity = models.PositiveIntegerField(
#         default=0,
#         validators=[MinValueValidator(0), MaxValueValidator(100)],
#         help_text="Clarity",
#     )
#     impact = models.PositiveIntegerField(
#         default=0,
#         validators=[MinValueValidator(0), MaxValueValidator(100)],
#         help_text="impact",
#     )
#     brevity = models.PositiveIntegerField(
#         default=0,
#         validators=[MinValueValidator(0), MaxValueValidator(100)],
#         help_text="Brevity",
#     )
#     transformative_potential = models.PositiveIntegerField(
#         default=0,
#         validators=[MinValueValidator(0), MaxValueValidator(100)],
#         help_text="transformative potential",
#     )
#     body_posture = models.PositiveIntegerField(
#         default=0,
#         validators=[MinValueValidator(0), MaxValueValidator(100)],
#         help_text="Body posture",
#     )
#     general_feedback_summary = models.TextField(blank=True, null=True)

#     # Metrics from audio analysis
#     volume = models.FloatField(null=True, blank=True, help_text="Volume")
#     pitch_variability = models.FloatField(
#         null=True, blank=True, help_text="Pitch variability"
#     )
#     pace = models.FloatField(null=True, blank=True, help_text="Pace")
#     chunk_transcript = models.TextField(blank=True, null=True, help_text="Transcript")

#     # New fields for sentiment analysis response
#     trigger_response = models.IntegerField(
#         default=0, help_text="Number of trigger responses detected"
#     )
#     filler_words = models.IntegerField(
#         default=0, help_text="Number of filler words used"
#     )
#     grammar = models.IntegerField(
#         default=0, help_text="Grammar score or number of errors"
#     )
#     posture = models.IntegerField(
#         default=0,
#         validators=[MinValueValidator(0), MaxValueValidator(100)],
#         help_text="Posture score",
#     )
#     motion = models.IntegerField(
#         default=0,
#         validators=[MinValueValidator(0), MaxValueValidator(100)],
#         help_text="Motion score",
#     )
#     gestures = models.BooleanField(
#         default=False, help_text="Presence of positive gestures"
#     )

#     def __str__(self):
#         return f"Sentiment Analysis for Chunk {self.chunk.start_time}-{self.chunk.end_time} of {self.chunk.session.session_name}"





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
    user_id = models.ForeignKey(  # Renamed from 'user' to 'user_id'
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
    slides_URL = models.URLField(
        max_length=255, blank=True, null=True, help_text="Optional URL to slides"
    )
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
        PracticeSequence,
        on_delete=models.SET_NULL,  # If a sequence is deleted, keep the sessions but disassociate
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
    pauses = models.IntegerField(default=0, help_text="Total number of pauses in the session")
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
        default=0, help_text="Average of volume, pitch, pace, and pauses"
    )
    emotional_impact = models.FloatField(
        default=0, help_text="Emotional impact (same as trigger response)"
    )
    body_language = models.FloatField(
        default=0, help_text="Score derived from posture, motion, and gestures"
    )
    transformative_communication = models.FloatField(
        default=0, help_text="Transformative communication (same as transformative potential)"
    )
    structure_and_clarity = models.FloatField(
        default=0, help_text="Overall score for structure and clarity"
    )
    language_and_word_choice = models.FloatField(
        default=0, help_text="Average of brevity, filler words, and grammar"
    )

    def __str__(self):
        return f"{self.session_name} by {self.user_id.email}"


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

    def __str__(self):
        return (
            f"Chunk {self.start_time}-{self.end_time} for {self.session.session_name}"
        )


class ChunkSentimentAnalysis(models.Model):
    chunk = models.OneToOneField(
        SessionChunk, on_delete=models.CASCADE, related_name="sentiment_analysis"
    )

    chunk_number = models.IntegerField(
        default=0, help_text="Order of the chunk in the session"
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

    # New fields for sentiment analysis response
    trigger_response = models.IntegerField(
        default=0, help_text="Number of trigger responses detected"
    )
    filler_words = models.IntegerField(
        default=0, help_text="Number of filler words used"
    )
    grammar = models.IntegerField(
        default=0, help_text="Grammar score or number of errors"
    )
    posture = models.IntegerField(
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Posture score",
    )
    motion = models.IntegerField(
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Motion score",
    )
    gestures = models.BooleanField(
        default=False, help_text="Presence of positive gestures"
    )
    pauses = models.IntegerField(
        default=0, help_text="Number of pauses in this chunk"
    )

    def __str__(self):
        return f"Sentiment Analysis for Chunk {self.chunk.start_time}-{self.chunk.end_time} of {self.chunk.session.session_name}"