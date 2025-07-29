from rest_framework import serializers
from django.contrib.auth import get_user_model
from .models import Enterprise, EnterpriseUser
from users.serializers import UserSerializer

User = get_user_model()

class EnterpriseSerializer(serializers.ModelSerializer):
    """
    Serializer for the Enterprise model.
    """
    class Meta:
        model = Enterprise
        fields = [
            'id', 'name', 'domain', 'logo', 'is_active',
            'sso_enabled', 'sso_metadata_url', 'sso_entity_id',
            'require_domain_match', 'created_at', 'updated_at'
        ]
        read_only_fields = ('id', 'created_at', 'updated_at')
        extra_kwargs = {
            'logo': {'required': False, 'allow_null': True},
            'sso_metadata_url': {'required': False, 'allow_blank': True, 'allow_null': True},
            'sso_entity_id': {'required': False, 'allow_blank': True, 'allow_null': True},
        }

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
            'user_type', 'is_admin', 'department', 'position',
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
