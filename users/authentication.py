from datetime import timedelta
from django.utils import timezone
from django.db import models
from rest_framework.authtoken.models import Token
from rest_framework import authentication, exceptions


class ExpiringToken(Token):
    """Token model with expiration support."""
    expires_at = models.DateTimeField(null=True, blank=True)

    @property
    def is_expired(self):
        """Check if token is expired."""
        if not self.expires_at:
            return False
        return timezone.now() > self.expires_at

    @classmethod
    def create_token(cls, user, remember_me=False):
        """Create a new token with optional expiration."""
        # Delete existing tokens for this user
        cls.objects.filter(user=user).delete()
        
        expires_at = None
        if remember_me:
            expires_at = timezone.now() + timedelta(days=30)
        
        return cls.objects.create(
            user=user,
            expires_at=expires_at
        )


class ExpiringTokenAuthentication(authentication.TokenAuthentication):
    model = ExpiringToken

    def authenticate_credentials(self, key):
        try:
            token = self.model.objects.select_related('user').get(key=key)
        except self.model.DoesNotExist:
            raise exceptions.AuthenticationFailed('Invalid token')

        if not token.user.is_active:
            raise exceptions.AuthenticationFailed('User inactive or deleted')

        if token.is_expired:
            token.delete()
            raise exceptions.AuthenticationFailed('Token has expired')

        return (token.user, token)
