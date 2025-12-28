import random
from datetime import timedelta
from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.utils import timezone
from .models import Enterprise
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
    user_count = serializers.SerializerMethodField()
    goals = serializers.SerializerMethodField()
    
    class Meta:
        model = Enterprise
        fields = [
            'id', 'name', 'enterprise_type', 'sport_type', 'sport_type_display',
            'logo', 'favicon', 'primary_color', 'secondary_color', 'is_active', 
            'one_on_one_coaching_link', 'accessible_verticals', 
            'available_verticals', 'user_count', 'goals', 'created_at', 'updated_at'
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
        verticals = obj.get_available_verticals()
        return [{
            'value': code,
            'label': dict(Enterprise.Vertical.choices).get(code, code.replace('_', ' ').title())
        } for code in verticals]
        
    def get_sport_type_display(self, obj):
        """Return the display value for sport_type."""
        if not obj.sport_type:
            return None
        return dict(Enterprise._meta.get_field('sport_type').choices).get(obj.sport_type)
        
    def get_user_count(self, obj):
        """Return the number of users in the enterprise."""
        return obj.users.count()
        
    def get_goals(self, obj):
        """Return list of active training goals for the enterprise"""
        goals = obj.training_goals.filter(is_active=True)
        return TrainingGoalSerializer(goals, many=True).data


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
        
        # Mapping from TrainingGoal room to PracticeSession session_type
        room_to_session_type = {
            "pitch": "pitch",
            "presentation": "presentation",
            "public_speaking": "public",
            "media_training": "enterprise",
            "coach": "enterprise",
            "general_manager": "enterprise",
            "coaching": "enterprise"
        }
        
        # Get all active goals for the enterprise
        active_goals = TrainingGoal.objects.filter(enterprise=enterprise, is_active=True)
        
        # Date threshold (30 days ago)
        date_threshold = (timezone.now() - timedelta(days=30)).date()
        
        for goal in active_goals:
            mapped_type = room_to_session_type.get(str(goal.room))
            
            if not mapped_type:
                logger.warning(f"Unknown room type {goal.room} for goal {goal.id}")
                completed_sessions = 0
            else:
                # Calculate user's completed sessions for this goal in the last 30 days
                # explicit date comparison for DateField
                query = PracticeSession.objects.filter(
                    user=obj.user,
                    session_type=mapped_type,
                    created_at__gte=date_threshold
                )
                
                # Refined filtering for Enterprise subtype
                if mapped_type == "enterprise":
                    if goal.room == "coaching":
                        query = query.filter(enterprise_settings__enterprise_type="coaching")
                    elif goal.room == "media_training":
                        query = query.filter(enterprise_settings__rookie_type="media_training")
                    elif goal.room == "coach":
                        query = query.filter(enterprise_settings__rookie_type="coach")
                    elif goal.room == "general_manager":
                        query = query.filter(enterprise_settings__rookie_type="gm")
                
                completed_sessions = query.count()
                if completed_sessions > 0:
                    logger.info(f"User {obj.user.id} - Goal {goal.room} (Mapped: {mapped_type}): {completed_sessions} sessions")
            
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
            
            goals.append(goal_data)
        
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
    
    credits_used = serializers.SerializerMethodField()
    
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
    gender_display = serializers.CharField(
        source='get_gender_display',
        read_only=True
    )

    class Meta:
        model = EnterpriseQuestion
        fields = [
            'id', 'enterprise', 'enterprise_name', 'vertical', 'vertical_display',
            'sport_type', 'sport_type_display', 'gender', 'gender_display', 
            'question_text', 'audio_url', 'is_active', 'created_at', 'updated_at'
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
        Validate the question data including vertical for media training.
        The gender assignment for media training is now handled in the model's clean method.
        """
        enterprise = data.get('enterprise') or getattr(self.instance, 'enterprise', None)
        vertical = data.get('vertical') or getattr(self.instance, 'vertical', None)
        
        if enterprise and vertical:
            # Ensure accessible_verticals is properly initialized
            if not hasattr(enterprise, 'accessible_verticals') or not enterprise.accessible_verticals:
                enterprise.accessible_verticals = enterprise.get_available_verticals()
                if not getattr(self, '_enterprise_updated', False):  # Prevent multiple saves
                    enterprise.save(update_fields=['accessible_verticals'])
                    self._enterprise_updated = True
            
            # Convert both to lowercase for case-insensitive comparison
            vertical_lower = vertical.lower()
            accessible_verticals_lower = [str(v).lower() for v in enterprise.accessible_verticals]
            
            # Check if vertical is in the enterprise's accessible_verticals (case-insensitive)
            if vertical_lower not in accessible_verticals_lower:
                # Get the display names of available verticals for the error message
                vertical_choices = dict(enterprise.Vertical.choices)
                available_verticals = []
                
                for v in enterprise.accessible_verticals:
                    if isinstance(v, (list, tuple)) and len(v) > 0:
                        v = v[0]  # Get the code from (code, name) tuple
                    if hasattr(v, 'value'):
                        v = v.value  # Handle enum values if needed
                    if v in vertical_choices:
                        available_verticals.append(vertical_choices[v])
                
                raise serializers.ValidationError({
                    'vertical': f"Vertical '{vertical}' is not available for this enterprise type. "
                              f"Available verticals: {', '.join(available_verticals) if available_verticals else 'None'}"
                })
            
            # Update the vertical with the correct case from the enterprise's accessible_verticals
            if vertical not in enterprise.accessible_verticals:
                # Find the correct case from the accessible_verticals
                for v in enterprise.accessible_verticals:
                    if isinstance(v, (list, tuple)) and len(v) > 0:
                        v = v[0]  # Get the code from (code, name) tuple
                    if str(v).lower() == vertical_lower:
                        data['vertical'] = v
                        break
        
        return data


class TrainingGoalSerializer(serializers.ModelSerializer):
    """
    Serializer for the TrainingGoal model.
    Handles validation and serialization of training goals.
    """
    completed_sessions = serializers.SerializerMethodField()
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
        read_only_fields = ('id', 'created_at', 'updated_at', 'progress_percent', 'is_completed', 'enterprise')

    def get_completed_sessions(self, obj):
        """
        Calculate total sessions completed by all users in the enterprise
        for this goal's specific room type.
        """
        # Mapping from TrainingGoal room to PracticeSession session_type
        room_to_session_type = {
            "pitch": "pitch",
            "presentation": "presentation",
            "public_speaking": "public",
            "media_training": "enterprise",
            "coach": "enterprise",
            "general_manager": "enterprise",
            "coaching": "enterprise"
        }
        
        mapped_type = room_to_session_type.get(obj.room)
        if not mapped_type:
            return 0
            
        # Base query: All sessions by users in this enterprise matching the mapped type
        # We use user__enterprise_profile__enterprise to filter by the goal's enterprise
        # Use ID filtering to avoid object instance mismatch issues
        sessions_query = PracticeSession.objects.filter(
            user__enterprise_profile__enterprise__id=obj.enterprise_id,
            session_type=mapped_type
        )
        
        # Refined filtering for Enterprise subtype
        if mapped_type == "enterprise":
            if obj.room == "coaching":
                sessions_query = sessions_query.filter(enterprise_settings__enterprise_type="coaching")
            elif obj.room == "media_training":
                sessions_query = sessions_query.filter(enterprise_settings__rookie_type="media_training")
            elif obj.room == "coach":
                sessions_query = sessions_query.filter(enterprise_settings__rookie_type="coach")
            elif obj.room == "general_manager":
                sessions_query = sessions_query.filter(enterprise_settings__rookie_type="gm")
                
        return sessions_query.count()
    
    def get_progress_percent(self, obj):
        """
        Calculate progress percentage based on:
        (Total Sessions Done) / (Target Per User * Total Users)
        """
        total_completed = self.get_completed_sessions(obj)
        user_count = obj.enterprise.users.count()
        
        if user_count == 0 or obj.target_sessions == 0:
            return 0
            
        total_target = obj.target_sessions * user_count
        
        # Calculate percentage
        return min(100, int((total_completed / total_target) * 100))
    
    def get_is_completed(self, obj):
        """
        For enterprise-wide view, 'is_completed' is ambiguous.
        We returned True if the overall progress is 100%.
        """
        return self.get_progress_percent(obj) >= 100
    
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
