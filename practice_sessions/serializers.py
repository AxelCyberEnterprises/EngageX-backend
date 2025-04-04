from rest_framework import serializers
from .models import PracticeSession, PracticeSequence


class PracticeSequenceSerializer(serializers.ModelSerializer):
    user_email = serializers.EmailField(source='user.email', read_only=True)

    class Meta:
        model = PracticeSequence
        fields = ['sequence_id', 'sequence_name', 'description', 'user_email']
        read_only_fields = ['sequence_id', 'user_email']


class PracticeSessionSerializer(serializers.ModelSerializer):
    user_email = serializers.EmailField(source='user.email', read_only=True)
    sequence = serializers.PrimaryKeyRelatedField(queryset=PracticeSequence.objects.all(), allow_null=True, required=False)

    class Meta:
        model = PracticeSession
        fields = [
            'id',
            'session_name',
            'session_type',
            'date',
            'duration',
            'note',
            'user_email',
            'pauses',
            'tone',
            'impact',
            'audience_engagement',
            'sequence',
            'allow_ai_questions',
            'virtual_environment'
            # Add other aggregated fields here if you have them in your PracticeSession model
        ]
        read_only_fields = ['id', 'date', 'duration', 'user_email', 'pauses', 'tone', 'emotional_impact', 'audience_engagement'] # These are populated by the backend

    def create(self, validated_data):
        # We are no longer creating SessionDetail here
        return PracticeSession.objects.create(**validated_data)

    def update(self, instance, validated_data):
        # Allow updates to basic fields like session_name and note
        instance.session_name = validated_data.get('session_name', instance.session_name)
        instance.session_type = validated_data.get('session_type', instance.session_type)
        instance.note = validated_data.get('note', instance.note)
        instance.sequence = validated_data.get('sequence', instance.sequence)
        instance.allow_ai_questions = validated_data.get('allow_ai_questions', instance.allow_ai_questions)
        instance.virtual_environment = validated_data.get('virtual_environment', instance.virtual_environment)
        instance.save()
        return instance


class PracticeSessionSlidesSerializer(serializers.ModelSerializer):
    slides = serializers.FileField(required=False) # Make slides field optional in serializer

    class Meta:
        model = PracticeSession
        fields = ['slides'] # Only include the slides field