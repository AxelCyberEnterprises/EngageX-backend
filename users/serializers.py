from django.contrib.auth import authenticate
from rest_framework import serializers
from .models import (UserProfile, CustomUser, UserAssignment)

from djoser.serializers import TokenCreateSerializer
from rest_framework.authtoken.models import Token
from rest_framework.exceptions import ValidationError


class CustomTokenCreateSerializer(TokenCreateSerializer):
    def validate(self, attrs):
        email = attrs.get('email')
        password = attrs.get('password')
        print(f"Attempting to authenticate user with email: {email}")

        # Authenticate using the email as the username
        user = authenticate(username=email, password=password)

        # Check if the authentication failed
        if user is None:
            print("Authentication failed.")
            raise ValidationError({
                "message": "Invalid credentials.",
                "email": ["No user found with this email address or password is incorrect."]
            })

        # Check if the user is inactive
        if not user.is_active:
            raise ValidationError({
                "message": "Account inactive.",
                "email": ["Your account has not been verified. Please check your email for the verification link."]
            })

        print("Authentication succeeded. User ID:", user.id)

        # Create or get the existing auth token for the user
        token, created = Token.objects.get_or_create(user=user)

        # Ensure the token has a user associated
        if not user or token.user is None:
            print("Token creation failed. User is not associated with the token.")
            raise ValidationError({
                "message": "Token generation failed.",
                "detail": ["Token could not be created. Please try again."]
            })

        print("Token generated:", token.key)
        return {'auth_token': token.key}


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomUser
        fields = ['id', 'email', 'first_name', 'last_name', 'password']
        # fields = '__all__'
        extra_kwargs = {
            'password': {'write_only': True, 'required': True},  # Password is required
            'email': {'required': True},  # Email is required
            'first_name': {'required': False},  # First name is optional
            'last_name': {'required': False},  # Last name is optional
        }

    def create(self, validated_data):
        # Automatically set the username as the first name
        validated_data['username'] = validated_data.get('first_name')
        user = CustomUser(**validated_data)
        user.set_password(validated_data['password'])  # Hash the password
        user.save()
        return user


class UpdateProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomUser
        fields = ['first_name', 'last_name', 'email']


class UserProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserProfile
        fields = ['date_of_birth', 'gender', 'profile_picture']


class ChangePasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True)
    confirm_new_password = serializers.CharField(write_only=True)

    def validate_old_password(self, value):
        user = self.context['request'].user
        if not user.check_password(value):
            raise serializers.ValidationError("Old password is incorrect.")
        return value

    def validate(self, attrs):
        new_password = attrs.get('new_password')
        confirm_new_password = attrs.get('confirm_new_password')

        # Check if the new password matches the confirmation
        if new_password != confirm_new_password:
            raise serializers.ValidationError({
                "confirm_new_password": "New passwords do not match."
            })

        # Perform additional validation on the new password if needed
        if len(new_password) < 8:
            raise serializers.ValidationError({
                "new_password": "New password must be at least 8 characters long."
            })

        return attrs

    def save(self, **kwargs):
        user = self.context['request'].user
        user.set_password(self.validated_data['new_password'])
        user.save()


class UserAssignmentSerializer(serializers.ModelSerializer):
    coach_email = serializers.EmailField(source='coach.email', read_only=True)
    presenter_email = serializers.EmailField(source='presenter.email', read_only=True)

    class Meta:
        model = UserAssignment
        fields = ['id', 'coach_email', 'presenter_email', 'assigned_at']
