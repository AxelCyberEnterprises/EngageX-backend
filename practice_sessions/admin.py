from django.contrib import admin
from .models import PracticeSession, SessionDetail

@admin.register(PracticeSession)
class PracticeSessionAdmin(admin.ModelAdmin):
    list_display = ('session_name', 'session_type', 'date', 'duration', 'user')
    search_fields = ('session_name', 'user__email')
    list_filter = ('session_type', 'date')

@admin.register(SessionDetail)
class SessionDetailAdmin(admin.ModelAdmin):
    list_display = ('session', 'engagement', 'emotional_connection', 'energy', 'pitch_variation', 'articulation')
