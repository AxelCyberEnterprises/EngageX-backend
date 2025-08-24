from datetime import timedelta
from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.db.models import Sum
from .models import Enterprise, EnterpriseUser, EnterpriseQuestion, TrainingGoal
from users.serializers import UserSerializer
from practice_sessions.models import PracticeSession
from payments.models import CreditTransaction

User = get_user_model()

class EnterpriseSerializer(serializers.ModelSerializer):
    """
    Serializer for the Enterprise model.
    """
    available_verticals = serializers.SerializerMethodField()
    sport_type_display = serializers.SerializerMethodField()
    
    class Meta:
        model = Enterprise
        fields = [
            'id', 'name', 'domain', 'enterprise_type', 'sport_type', 'sport_type_display',
            'logo', 'favicon', 'primary_color', 'secondary_color', 'is_active', 
            'require_domain_match', 'one_on_one_coaching_link', 'accessible_verticals', 
            'available_verticals', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'sport_type_display']
        extra_kwargs = {
            'logo': {'required': False, 'allow_null': True},
            'favicon': {'required': False, 'allow_null': True},
            'primary_color': {'required': False},
            'secondary_color': {'required': False},
            'sport_type': {'required': False, 'allow_null': True, 'write_only': False}
        }
    
    def get_available_verticals(self, obj):
        """Return the list of available verticals for the enterprise."""
        return [{
            'value': vertical,
            'label': dict(Enterprise.Vertical.choices)[vertical]
        } for vertical in obj.get_available_verticals()]
        
    def get_sport_type_display(self, obj):
        """Return the display value for sport_type."""
        if not obj.sport_type:
            return None
        return dict(Enterprise._meta.get_field('sport_type').choices).get(obj.sport_type)

    def validate_domain(self, value):
        """Ensure domain is in the correct format."""
        if not value.startswith('@'):
            value = f"@{value}"
        return value.lower()


class UserProgressSerializer(serializers.Serializer):
    """
    Serializer for user progress data in reports.
    Tracks user progress against enterprise training goals.
    """
    user_id = serializers.IntegerField(source='id')
    first_name = serializers.CharField(source='user.first_name')
    last_name = serializers.CharField(source='user.last_name')
    email = serializers.EmailField(source='user.email')
    role = serializers.SerializerMethodField()
    assigned_goals = serializers.SerializerMethodField()
    sessions_completed = serializers.SerializerMethodField()
    overall_goal_completion = serializers.SerializerMethodField()
    last_session_date = serializers.SerializerMethodField()

    def get_role(self, obj):
        return 'Admin' if obj.is_admin else 'User'

    def get_assigned_goals(self, obj):
        """Return list of enterprise goals with user's progress"""
        from .models import TrainingGoal
        import logging
        logger = logging.getLogger(__name__)
        
        enterprise = obj.enterprise
        goals = []
        
        logger.info(f"[DEBUG] Getting goals for user {obj.user_id} in enterprise {enterprise.id}")
        
        # Get all active goals for the enterprise
        active_goals = TrainingGoal.objects.filter(enterprise=enterprise, is_active=True)
        logger.info(f"[DEBUG] Found {active_goals.count()} active goals for enterprise {enterprise.id}")
        
        for goal in active_goals:
            # Calculate user's completed sessions for this goal in the last 30 days
            query = PracticeSession.objects.filter(
                user=obj.user,
                session_type=goal.room,
                created_at__gte=timezone.now() - timedelta(days=30)
            )
            completed_sessions = query.count()
            
            # Log the query details
            logger.info(f"[DEBUG] Goal {goal.id} ({goal.room}): "
                      f"target={goal.target_sessions}, "
                      f"completed_sessions={completed_sessions}")
            
            # Calculate progress percentage
            target = goal.target_sessions
            progress = min(100, int((completed_sessions / target) * 100)) if target > 0 else 0
            
            goal_data = {
                'goal_id': goal.id,
                'room': goal.room,
                'name': goal.get_room_display(),
                'target': target,
                'completed': completed_sessions,
                'progress': progress,
                'due_date': goal.due_date.isoformat() if goal.due_date else None,
                'is_active': goal.is_active
            }
            
            logger.info(f"[DEBUG] Goal data: {goal_data}")
            goals.append(goal_data)
        
        logger.info(f"[DEBUG] Total goals found: {len(goals)}")
        return goals

    def get_sessions_completed(self, obj):
        """Total sessions completed in last 30 days across all goals"""
        return PracticeSession.objects.filter(
            user=obj.user,
            created_at__gte=timezone.now() - timedelta(days=30)
        ).count()
        
    def get_last_session_date(self, obj):
        """Get the date of the user's most recent session"""
        last_session = PracticeSession.objects.filter(
            user=obj.user
        ).order_by('-created_at').first()
        
        return last_session.created_at.isoformat() if last_session else None

    def get_overall_goal_completion(self, obj):
        """
        Calculate weighted average completion percentage across all goals
        Weights by the target number of sessions for each goal
        """
        goals = self.get_assigned_goals(obj)
        if not goals:
            return 0
            
        total_weighted_progress = 0
        total_weight = 0
        
        for goal in goals:
            weight = goal['target'] or 1  # Avoid division by zero
            total_weighted_progress += goal['progress'] * weight
            total_weight += weight
            
        return round(total_weighted_progress / total_weight, 1) if total_weight > 0 else 0


class EnterpriseUserSerializer(serializers.ModelSerializer):
    """Serializer for the EnterpriseUser model."""
    user = UserSerializer(read_only=True)
    user_id = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(),
        write_only=True,
        source='user',
        required=False
    )
    enterprise = EnterpriseSerializer(read_only=True)
    enterprise_name = serializers.CharField(
        source='enterprise.name',
        read_only=True
    )
    progress = serializers.SerializerMethodField()
    
    # Branding fields (effective values that inherit from enterprise if not set)
    logo = serializers.SerializerMethodField()
    favicon = serializers.SerializerMethodField()
    primary_color = serializers.SerializerMethodField()
    secondary_color = serializers.SerializerMethodField()

    credits_used = serializers.SerializerMethodField()
    
    def get_logo(self, obj):
        return self.context['request'].build_absolute_uri(obj.effective_logo.url) if obj.effective_logo else None
        
    def get_favicon(self, obj):
        return self.context['request'].build_absolute_uri(obj.effective_favicon.url) if obj.effective_favicon else None
        
    def get_primary_color(self, obj):
        return obj.effective_primary_color
        
    def get_secondary_color(self, obj):
        return obj.effective_secondary_color

    def get_credits_used(self, obj):
        total_credits = CreditTransaction.objects.filter(
            user=obj.user,
            transaction_type=CreditTransaction.TRANSACTION_TYPES[1][0]
        ).aggregate(total=Sum('amount'))['total']

        return total_credits if total_credits is not None else 0

    class Meta:
        model = EnterpriseUser
        fields = [
            'id', 'user', 'user_id', 'enterprise', 'enterprise_name', 'user_type',
            'is_admin', 'created_at', 'updated_at', 'progress',
            'logo', 'favicon', 'primary_color', 'secondary_color',
            'role', 'team', 'credits_used'
        ]
        read_only_fields = ['created_at', 'updated_at']
        extra_kwargs = {
            'enterprise': {'required': True},
            'role': {'required': False, 'allow_blank': True},
            'team': {'required': False, 'allow_blank': True}
        }
        
    def get_progress(self, obj):
        """Get user's progress data"""
        return UserProgressSerializer(obj).data

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
