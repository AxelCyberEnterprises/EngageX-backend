from rest_framework import authentication, exceptions
from django.utils import timezone
from .models import ExpiringToken


class ExpiringTokenAuthentication(authentication.TokenAuthentication):
    model = ExpiringToken

    def authenticate_credentials(self, key):
        try:
            print(f"Looking up token: {key}")
            token = self.model.objects.select_related('user').get(key=key)
            print(f"Token found - User: {token.user.email if token.user else 'No user'}")
            print(f"Token created: {token.created}")
            print(f"Token expires at: {token.expires_at}")
            print(f"Current time: {timezone.now()}")
            print(f"Is token expired: {token.is_expired}")
            
            # Debug: Check all tokens for this user
            user_tokens = self.model.objects.filter(user=token.user)
            print(f"User has {user_tokens.count()} tokens total")
            
        except self.model.DoesNotExist:
            print("Token not found in database")
            raise exceptions.AuthenticationFailed('Invalid token')

        if not token.user.is_active:
            print("User account is not active")
            raise exceptions.AuthenticationFailed('User inactive or deleted')

        if token.is_expired:
            print("Token has expired")
            token.delete()
            raise exceptions.AuthenticationFailed('Token has expired')

        print("Token is valid")
        return (token.user, token)
