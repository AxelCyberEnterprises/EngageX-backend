from rest_framework import serializers
from .models import PracticeSession, SessionDetail

class SessionDetailSerializer(serializers.ModelSerializer):
    class Meta:
        model = SessionDetail
        fields = [
            'engagement', 'emotional_connection', 'energy',
            'pitch_variation', 'volume_control', 'speech_rate', 'articulation',
            'structure', 'impact', 'content_engagement', 'strengths', 'areas_for_improvement'
        ]

class PracticeSessionSerializer(serializers.ModelSerializer):
    details = SessionDetailSerializer(required=False)
    user_email = serializers.EmailField(source='user.email', read_only=True)
    
    class Meta:
        model = PracticeSession
        fields = ['id', 'session_name', 'session_type', 'date', 'duration', 'note', 'user_email', 'details']
    
    def create(self, validated_data):
        details_data = validated_data.pop('details', None)
        session = PracticeSession.objects.create(**validated_data)
        if details_data:
            SessionDetail.objects.create(session=session, **details_data)
        return session


class PracticeSessionSlidesSerializer(serializers.ModelSerializer):
    slides = serializers.FileField(required=False) # Make slides field optional in serializer

    class Meta:
        model = PracticeSession
        fields = ['slides'] # Only include the slides field