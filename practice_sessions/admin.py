from django.contrib import admin
from .models import (
    PracticeSession,
    ChunkSentimentAnalysis,
    SessionChunk,
    PracticeSequence,
SlidePreview,
)


@admin.register(PracticeSequence)
class PracticeSequenceAdmin(admin.ModelAdmin):
    list_display = ("sequence_name", "sequence_id", "user", "description")
    search_fields = ("sequence_name", "description", "user__email")
    list_filter = ("user",)


@admin.register(PracticeSession)
class PracticeSessionAdmin(admin.ModelAdmin):
    list_display = (
        "session_name",
        "session_type",
        "date",
        "duration",
        "user",
        "pauses",
        "audience_engagement",
        "sequence",  # Added sequence to the list display
        "allow_ai_questions",  # Added allow_ai_questions to the list display
        # Add other aggregated fields here as needed (e.g., 'pronunciation', 'content_organization')
    )
    search_fields = ("session_name", "user__email")
    list_filter = (
        "session_type",
        "date",
        # Removed 'tone' from list_filter
        "sequence",
    )  # Added 'sequence' as a filter
    # You might also want to add 'allow_ai_questions' to list_filter


@admin.register(ChunkSentimentAnalysis)
class ChunkSentimentAnalysisAdmin(admin.ModelAdmin):
    list_display = (
        "chunk",
        # 'engagement',
        "impact",
    )
    search_fields = ("chunk__session__session_name",)
    list_filter = ("impact",)


@admin.register(SessionChunk)
class SessionChunkAdmin(admin.ModelAdmin):
    # list_display = ("session", "start_time", "end_time")
    list_display = ("session",)
    list_filter = ("session",)
    search_fields = ("session__session_name",)

admin.site.register(SlidePreview)