from rest_framework import serializers
from django.contrib.auth import get_user_model
from .models import Enterprise, EnterpriseUser, EnterpriseQuestion
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
            'id', 'name', 'domain', 'enterprise_type', 'logo', 'is_active',
            'require_domain_match', 'available_verticals', 'created_at', 'updated_at'
        ]
        read_only_fields = ('id', 'created_at', 'updated_at', 'available_verticals')
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

    class Meta:
        model = EnterpriseQuestion
        fields = [
            'id', 'enterprise', 'enterprise_name', 'vertical', 'vertical_display',
            'question_text', 'audio_url', 'is_active', 'created_at', 'updated_at'
        ]
        read_only_fields = ('id', 'created_at', 'updated_at')
        extra_kwargs = {
            'enterprise': {'required': True},
            'vertical': {'required': True},
            'question_text': {'required': True}
        }

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
