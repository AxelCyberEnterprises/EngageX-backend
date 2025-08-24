from django.db.models import CharField
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from django.db import transaction

from datetime import timedelta

from rest_framework.fields import DictField

from .models import (
    PracticeSession,
    PracticeSequence,
    ChunkSentimentAnalysis,
    SessionChunk,
    SlidePreview,
    EnterpriseSpecialtySession
)


class PracticeSequenceSerializer(serializers.ModelSerializer):
    user_email = serializers.EmailField(source="user.email", read_only=True)

    class Meta:
        model = PracticeSequence
        fields = ["sequence_id", "sequence_name", "description", "user_email"]
        read_only_fields = ["sequence_id", "user_email"]


class EnterpriseSpecialtySessionSerializer(serializers.ModelSerializer):
    class Meta:
        model = EnterpriseSpecialtySession
        fields = [
            'enterprise_type',
            'rookie_type',
            'sport_type',
            'speaker_notes'
        ]
        extra_kwargs = {
            'enterprise_type': {'required': False, 'default': 'rookie'},
            'rookie_type': {'required': False},
            'sport_type': {'required': False},
            'speaker_notes': {'required': False},
        }


class SlidePreviewSerializer(serializers.ModelSerializer):
    class Meta:
        model = SlidePreview
        fields = '__all__'
        read_only_fields = ["user", "is_linked", "created_at"]


class PracticeSessionSerializer(serializers.ModelSerializer):
    user_email = serializers.EmailField(source="user.email", read_only=True)
    full_name = serializers.SerializerMethodField()
    session_type_display = serializers.SerializerMethodField()
    latest_score = serializers.SerializerMethodField()
    slide_preview_id = serializers.CharField(allow_null=True, allow_blank=True, required=False)
    
    slide_preview = SlidePreviewSerializer(read_only=True)
    slide_preview_id = serializers.PrimaryKeyRelatedField(
        queryset=SlidePreview.objects.all(),
        source='slide_preview',
        write_only=True,
        required=False
    )

    # Enterprise Specialty fields
    enterprise_settings = EnterpriseSpecialtySessionSerializer(required=False, allow_null=True)

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
            "slide_preview_id",
            "slide_preview",
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
        user = validated_data.get('user')
        slide_preview_id = validated_data.pop('slide_preview_id', None)
        enterprise_settings_data = validated_data.pop('enterprise_settings', None)
        
        # Force allow_ai_questions to True for enterprise sessions
        if validated_data.get('session_type') == 'enterprise':
            validated_data['allow_ai_questions'] = True

        if slide_preview_id:
            try:
                slide_preview = SlidePreview.objects.get(id=slide_preview_id, user=user)
            except SlidePreview.DoesNotExist:
                raise ValidationError({"slide_preview_id": "Invalid or unauthorized slide preview."})
        else:
            slide_preview = None

        with transaction.atomic():
            # Import models here to avoid circular imports
            from payments.models import Credit, CreditTransaction
            
            # Check if user is part of an enterprise with available credits
            enterprise = user.get_enterprise()
            credit_used_from = 'user'  # Track where the credit was used from
            
            if enterprise:
                # Try to use enterprise credits first
                try:
                    credit = Credit.objects.select_for_update().get(enterprise=enterprise)
                    if credit.balance >= 1:  # Check if enterprise has enough credits
                        credit.credits_used += 1
                        credit.save()
                        credit_used_from = 'enterprise'
                        
                        # Record the transaction
                        CreditTransaction.objects.create(
                            enterprise=enterprise,
                            transaction_type='use',
                            amount=1,
                            description=f'Practice session by {user.email}',
                            user=user,
                            reference_id=f"session_credit_use_{enterprise.id}_{timezone.now().timestamp()}"
                        )
                except Credit.DoesNotExist:
                    pass  # No enterprise credit record exists, fall back to user credits
            
            # If no enterprise credits were used, try user credits
            if credit_used_from == 'user':
                profile = user.user_profile.__class__.objects.select_for_update().get(user=user)
                if profile.available_credits > 0:
                    profile.available_credits -= 1
                    profile.save()
                else:
                    raise ValidationError({"credit": "Insufficient credit"})

            # Create the session
            session = PracticeSession.objects.create(
                slide_preview=slide_preview,
                **validated_data
            )

            # Handle slide preview if exists
            if slide_preview:
                session.slides_file = slide_preview.slides_file
                slide_preview.is_linked = True
                slide_preview.save()
            
            # Handle enterprise settings if this is an enterprise session
            if session.session_type == 'enterprise' and enterprise_settings_data:
                EnterpriseSpecialtySession.objects.create(
                    session=session,
                    **enterprise_settings_data
                )
            
            return session

    def update(self, instance, validated_data):
        enterprise_settings_data = validated_data.pop('enterprise_settings', None)
        
        # Update basic fields
        instance.session_name = validated_data.get(
            "session_name", instance.session_name
        )
        
        # Check if session_type is being updated to enterprise
        new_session_type = validated_data.get("session_type", instance.session_type)
        if new_session_type == 'enterprise':
            # Force allow_ai_questions to True for enterprise sessions
            validated_data['allow_ai_questions'] = True
        
        instance.session_type = new_session_type
        instance.goals = validated_data.get("goals", instance.goals)
        instance.note = validated_data.get("note", instance.note)
        instance.sequence = validated_data.get("sequence", instance.sequence)
        
        # Only update allow_ai_questions if it's not an enterprise session
        if instance.session_type != 'enterprise':
            instance.allow_ai_questions = validated_data.get(
                "allow_ai_questions", instance.allow_ai_questions
            )
        else:
            instance.allow_ai_questions = True
            
        instance.virtual_environment = validated_data.get(
            "virtual_environment", instance.virtual_environment
        )
        instance.duration = validated_data.get("duration", instance.duration)
        
        # Handle enterprise settings
        if instance.session_type == 'enterprise':
            if enterprise_settings_data:
                enterprise_settings = instance.enterprise_settings
                if enterprise_settings:
                    # Update existing enterprise settings
                    for attr, value in enterprise_settings_data.items():
                        setattr(enterprise_settings, attr, value)
                    enterprise_settings.save()
                else:
                    # Create new enterprise settings if they don't exist
                    EnterpriseSpecialtySession.objects.create(
                        session=instance,
                        **enterprise_settings_data
                    )
        
        instance.save()
        return instance


class PracticeSessionSlidesSerializer(serializers.ModelSerializer):
    slides_file = serializers.FileField(
        required=False
    )

    class Meta:
        model = PracticeSession
        fields = ["slides_file"]


class SessionChunkSerializer(serializers.ModelSerializer):
    class Meta:
        model = SessionChunk
        fields = [
            "id",
            "session",
            "video_file",
            "chunk_number",
            "transcript",
            "audio_path",
            "start_time",
            "end_time",
            "created_at",
            "updated_at"
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class ChunkSentimentAnalysisSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChunkSentimentAnalysis
        fields = [
            "id",
            "chunk",
            "chunk_number",
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
            "pauses",
            "chunk_transcript",
        ]
        read_only_fields = ["id"]


class SessionReportSerializer(serializers.Serializer):
    duration = serializers.CharField(allow_null=True, allow_blank=True)
    slide_specific_timing = DictField(allow_empty=True)



