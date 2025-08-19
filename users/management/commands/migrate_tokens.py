from django.core.management.base import BaseCommand
from django.utils import timezone
from rest_framework.authtoken.models import Token
from users.models import ExpiringToken
from datetime import timedelta

class Command(BaseCommand):
    help = "Migrate existing DRF tokens into ExpiringToken"

    def handle(self, *args, **kwargs):
        count = 0
        for token in Token.objects.all():
            # If user already has an ExpiringToken, skip
            if ExpiringToken.objects.filter(user=token.user).exists():
                continue

            ExpiringToken.objects.create(
                key=token.key,
                user=token.user,
                created=token.created,
                expires_at=timezone.now() + timedelta(days=3)  # default 3-day expiry
            )
            count += 1

        self.stdout.write(self.style.SUCCESS(f"Migrated {count} tokens."))
