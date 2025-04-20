from rest_framework import serializers
from rest_framework.exceptions import  ValidationError
from django.db import transaction

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
        fields = "__all__"
        read_only_fields = [
            "id",
            "user",
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
        user  =  validated_data.get('user')

        with transaction.atomic():
            # Lock the profile row for this user to prevent race conditions
            profile = user.user_profile.__class__.objects.select_for_update().get(user=user)
            print(profile.available_credits)

            if profile.available_credits > 0:
                profile.available_credits -= 1
                profile.save()
                return PracticeSession.objects.create(**validated_data)
            else:
                raise ValidationError({"credit": "Insufficient credit"})

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
            "audience_emotion",
            "conviction",
            "clarity",
            "impact",
            "brevity",
            "transformative_potential",
            "trigger_response",
            "filler_words",
            "grammar",
            "volume",
            "general_feedback_summary",
            "pitch_variability",
            "posture",
            "pace",
            "motion",
            "gestures",
            "volume",
            "chunk_transcript",
            "pitch_variability",
            "general_feedback_summary",
            "chunk_transcript",
        ]
        read_only_fields = ["id"]


class SessionReportSerializer(serializers.Serializer):
    duration = serializers.CharField(allow_null=True, allow_blank=True)
