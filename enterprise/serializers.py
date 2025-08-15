from rest_framework import serializers
from django.contrib.auth import get_user_model
from .models import Enterprise, EnterpriseUser, EnterpriseQuestion, TrainingGoal
from users.serializers import UserSerializer

User = get_user_model()

class EnterpriseSerializer(serializers.ModelSerializer):
    """
    Serializer for the Enterprise model.
    """
    available_verticals = serializers.SerializerMethodField()
    
    class Meta:
        model = Enterprise
        fields = [
            'id', 'name', 'domain', 'enterprise_type', 'logo', 
            'is_active', 'require_domain_match', 'one_on_one_coaching_link',
            'accessible_verticals', 'available_verticals', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
        extra_kwargs = {
            'logo': {'required': False, 'allow_null': True}
        }
    
    def get_available_verticals(self, obj):
        """Return the list of available verticals for the enterprise."""
        return [{
            'value': vertical,
            'label': dict(Enterprise.Vertical.choices)[vertical]
        } for vertical in obj.get_available_verticals()]

    def validate_domain(self, value):
        """Ensure domain is in the correct format."""
        if not value.startswith('@'):
            value = f"@{value}"
        return value.lower()


class EnterpriseUserSerializer(serializers.ModelSerializer):
    """
    Serializer for the EnterpriseUser model.
    """
    user = UserSerializer(read_only=True)
    user_id = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(),
        write_only=True,
        source='user',
        required=False
    )
    enterprise_name = serializers.CharField(
        source='enterprise.name',
        read_only=True
    )

    class Meta:
        model = EnterpriseUser
        fields = [
            'id', 'user', 'user_id', 'enterprise', 'enterprise_name',
            'user_type', 'is_admin',
            'created_at', 'updated_at'
        ]
        read_only_fields = ('id', 'created_at', 'updated_at')
        extra_kwargs = {
            'enterprise': {'required': True},
        }

    def validate(self, data):
        """
        Validate that the user's email matches the enterprise domain if required.
        """
        enterprise = data.get('enterprise')
        user = data.get('user')
        
        if enterprise and user and enterprise.require_domain_match:
            user_domain = f"@{user.email.split('@')[-1]}"
            if user_domain != enterprise.domain:
                raise serializers.ValidationError(
                    f"User email domain must match the enterprise domain {enterprise.domain}"
                )
        
        return data


class BulkUserUploadSerializer(serializers.Serializer):
    """
    Serializer for bulk user upload via CSV/Excel.
    """
    enterprise_id = serializers.PrimaryKeyRelatedField(
        queryset=Enterprise.objects.all(),
        required=True
    )
    file = serializers.FileField(required=True)
    send_invitation = serializers.BooleanField(default=True)
    
    def validate_file(self, value):
        """Validate the uploaded file."""
        valid_extensions = ['.csv', '.xlsx', '.xls']
        import os
        ext = os.path.splitext(value.name)[1].lower()
        if ext not in valid_extensions:
            raise serializers.ValidationError(
                'Unsupported file extension. Supported formats: .csv, .xlsx, .xls'
            )
        return value


class EnterpriseQuestionSerializer(serializers.ModelSerializer):
    """
    Serializer for the EnterpriseQuestion model.
    """
    enterprise_name = serializers.CharField(
        source='enterprise.name',
        read_only=True
    )
    vertical_display = serializers.CharField(
        source='get_vertical_display',
        read_only=True
    )
    sport_type_display = serializers.SerializerMethodField()

    class Meta:
        model = EnterpriseQuestion
        fields = [
            'id', 'enterprise', 'enterprise_name', 'vertical', 'vertical_display',
            'sport_type', 'sport_type_display', 'question_text', 'audio_url', 
            'is_active', 'created_at', 'updated_at'
        ]
        read_only_fields = ('id', 'created_at', 'updated_at')
        extra_kwargs = {
            'enterprise': {'required': True},
            'vertical': {'required': True},
            'question_text': {'required': True},
            'sport_type': {'required': False}
        }
    
    def get_sport_type_display(self, obj):
        """Get the display value for sport_type"""
        return dict(EnterpriseQuestion._meta.get_field('sport_type').choices).get(obj.sport_type) if obj.sport_type else None

    def validate(self, data):
        """
        Validate that the vertical is allowed for the enterprise.
        """
        enterprise = data.get('enterprise')
        vertical = data.get('vertical')
        
        if enterprise and vertical:
            available_verticals = enterprise.get_available_verticals()
            if vertical not in available_verticals:
                raise serializers.ValidationError({
                    'vertical': f"Vertical '{vertical}' is not available for this enterprise type"
                })
        
        return data


class TrainingGoalSerializer(serializers.ModelSerializer):
    """
    Serializer for the TrainingGoal model.
    Handles validation and serialization of training goals.
    """
    progress_percent = serializers.SerializerMethodField()
    is_completed = serializers.SerializerMethodField()
    room_display = serializers.CharField(source='get_room_display', read_only=True)
    
    class Meta:
        model = TrainingGoal
        fields = [
            'id', 'enterprise', 'room', 'room_display', 'target_sessions', 
            'completed_sessions', 'progress_percent', 'is_completed',
            'due_date', 'is_active', 'created_at', 'updated_at'
        ]
        read_only_fields = ('id', 'created_at', 'updated_at', 'progress_percent', 'is_completed')
    
    def get_progress_percent(self, obj):
        """Calculate and return the progress percentage."""
        return obj.progress_percent
    
    def get_is_completed(self, obj):
        """Return whether the goal is completed."""
        return obj.is_completed
    
    def validate(self, data):
        """
        Validate the training goal data.
        - Ensure room type is allowed for the enterprise type
        - Ensure only one active goal per room per enterprise
        """
        enterprise = data.get('enterprise') or self.instance.enterprise if self.instance else None
        room = data.get('room') or (self.instance.room if self.instance else None)
        is_active = data.get('is_active', True if not self.instance else self.instance.is_active)
        
        if enterprise and room and is_active:
            # Check for existing active goal for this room in the enterprise
            existing = TrainingGoal.objects.filter(
                enterprise=enterprise,
                room=room,
                is_active=True
            )
            
            if self.instance:
                existing = existing.exclude(pk=self.instance.pk)
            
            if existing.exists():
                raise serializers.ValidationError({
                    'room': f"There is already an active goal for {dict(TrainingGoal.RoomType.choices).get(room, room)}"
                })
            
            # Validate room type against enterprise type
            if (enterprise.enterprise_type == Enterprise.EnterpriseType.GENERAL and 
                room not in [
                    TrainingGoal.RoomType.COACHING.value, 
                    TrainingGoal.RoomType.PRESENTATION.value,
                    TrainingGoal.RoomType.PITCH.value, 
                    TrainingGoal.RoomType.PUBLIC_SPEAKING.value,
                    TrainingGoal.RoomType.MEDIA_TRAINING.value
                ]):
                raise serializers.ValidationError({
                    'room': f"Room type '{dict(TrainingGoal.RoomType.choices).get(room, room)}' is not allowed for general enterprises"
                })
        
        return data


class TrainingGoalOptionsSerializer(serializers.Serializer):
    """
    Serializer for training goal options.
    Used to list available room types for creating/updating goals.
    """
    id = serializers.CharField()
    name = serializers.CharField()
    enterprise_types = serializers.ListField(child=serializers.CharField())
