from rest_framework import serializers

from datetime import timedelta
from .models import (
    PracticeSession,
    PracticeSequence,
    ChunkSentimentAnalysis,
    SessionChunk,
)


class PracticeSequenceSerializer(serializers.ModelSerializer):
    user_email = serializers.EmailField(source="user.email", read_only=True)

    class Meta:
        model = PracticeSequence
        fields = ["sequence_id", "sequence_name", "description", "user_email"]
        read_only_fields = ["sequence_id", "user_email"]


class PracticeSessionSerializer(serializers.ModelSerializer):
    user_email = serializers.EmailField(source="user.email", read_only=True)
    full_name = serializers.SerializerMethodField()
    session_type_display = serializers.SerializerMethodField()
    latest_score = serializers.SerializerMethodField()
    sequence = serializers.PrimaryKeyRelatedField(
        queryset=PracticeSequence.objects.all(), allow_null=True, required=False
    )

    class Meta:
        model = PracticeSession
        fields = [
            "id",
            "session_name",
            "session_type",
            "goals",
            "session_type_display",
            "latest_score",
            "date",
            "duration",
            "note",
            "user_email",
            "full_name",
            "pauses",
            "tone",
            "impact",
            "audience_engagement",
            "sequence",
            "allow_ai_questions",
            "virtual_environment",
            # Add other aggregated fields here if you have them in your PracticeSession model
        ]
        read_only_fields = [
            "id",
            "date",
            "user_email",
            "full_name",
            "latest_score",
            "session_type_display",
            "pauses",
            "tone",
            "emotional_impact",
            "audience_engagement",
        ]  # These are populated by the backend

    def get_full_name(self, obj):
        if obj.user:
            return f"{obj.user.first_name} {obj.user.last_name}".strip()
        return "None"

    def get_session_type_display(self, obj):
        return obj.get_session_type_display()

    def get_latest_score(self, obj):
        return obj.impact

    def create(self, validated_data):
        # We are no longer creating SessionDetail here
        return PracticeSession.objects.create(**validated_data)

    def update(self, instance, validated_data):
        # Allow updates to basic fields like session_name and note
        instance.session_name = validated_data.get(
            "session_name", instance.session_name
        )
        instance.session_type = validated_data.get(
            "session_type", instance.session_type
        )
        instance.goals = validated_data.get("goals", instance.goals)
        instance.note = validated_data.get("note", instance.note)
        instance.sequence = validated_data.get("sequence", instance.sequence)
        instance.allow_ai_questions = validated_data.get(
            "allow_ai_questions", instance.allow_ai_questions
        )
        instance.virtual_environment = validated_data.get(
            "virtual_environment", instance.virtual_environment
        )
        instance.duration = validated_data.get("duration", instance.duration)
        instance.save()
        return instance


class PracticeSessionSlidesSerializer(serializers.ModelSerializer):
    slides = serializers.FileField(
        required=False
    )  # Make slides field optional in serializer

    class Meta:
        model = PracticeSession
        fields = ["slides"]  # Only include the slides field


class SessionChunkSerializer(serializers.ModelSerializer):
    class Meta:
        model = SessionChunk
        fields = ["id", "session", "video_file"]
        read_only_fields = ["id"]


class ChunkSentimentAnalysisSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChunkSentimentAnalysis
        fields = [
            "id",
            "chunk",
            "engagement",
            "audience_emotion",
            "conviction",
            "clarity",
            "impact",
            "brevity",
            "transformative_potential",
            "body_posture",
            "volume",
            "pitch_variability",
            "pace",
            "chunk_transcript",
            "general_feedback_summary",
        ]
        read_only_fields = ["id"]


class SessionReportSerializer(serializers.Serializer):
    duration = serializers.CharField(allow_null=True, allow_blank=True)
