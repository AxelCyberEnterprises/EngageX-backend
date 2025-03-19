from django.contrib import admin
from .models import PracticeSession, ChunkSentimentAnalysis, SessionChunk

@admin.register(PracticeSession)
class PracticeSessionAdmin(admin.ModelAdmin):
    list_display = (
        'session_name',
        'session_type',
        'date',
        'duration',
        'user',
        'pauses',
        'tone',
        'emotional_impact',
        'audience_engagement',
        # Add other aggregated fields here as needed (e.g., 'pronunciation', 'content_organization')
    )
    search_fields = ('session_name', 'user__email')
    list_filter = ('session_type', 'date', 'tone') # Added 'tone' as a filter example

# @admin.register(SessionDetail)
# class SessionDetailAdmin(admin.ModelAdmin):
#     list_display = ('session', 'engagement', 'emotional_connection', 'energy', 'pitch_variation', 'articulation')

@admin.register(ChunkSentimentAnalysis)
class ChunkSentimentAnalysisAdmin(admin.ModelAdmin):
    list_display = (
        'chunk',
        'engagement',
        'confidence',
        'overall_score',
        'tone',
        'emotional_impact',
        # Add other relevant fields from ChunkSentimentAnalysis
    )
    search_fields = ('chunk__session__session_name',)
    list_filter = ('tone',)

@admin.register(SessionChunk)
class SessionChunkAdmin(admin.ModelAdmin):
    list_display = ('session', 'start_time', 'end_time')
    list_filter = ('session',)
    search_fields = ('session__session_name',)