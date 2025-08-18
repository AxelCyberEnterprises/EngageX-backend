from rest_framework import authentication, exceptions
from django.utils import timezone
from .models import ExpiringToken


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
