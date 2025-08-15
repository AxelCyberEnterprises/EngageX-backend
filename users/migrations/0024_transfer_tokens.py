from django.db import migrations

def transfer_tokens(apps, schema_editor):
    # Get the old and new token models
    OldToken = apps.get_model('authtoken', 'Token')
    ExpiringToken = apps.get_model('users', 'ExpiringToken')
    
    # Transfer all existing tokens
    for old_token in OldToken.objects.all():
        # Create a new ExpiringToken with the same key and user
        from django.utils import timezone
        from datetime import timedelta
        
        ExpiringToken.objects.create(
            key=old_token.key,
            user=old_token.user,
            created=old_token.created,
            # Set expiration to 3 days from now for transferred tokens
            expires_at=timezone.now() + timedelta(days=3)
        )

def reverse_transfer(apps, schema_editor):
    # In case we need to reverse, delete all ExpiringTokens
    ExpiringToken = apps.get_model('users', 'ExpiringToken')
    ExpiringToken.objects.all().delete()

class Migration(migrations.Migration):
    dependencies = [
        ('users', '0023_expiringtoken'),
        ('authtoken', '0003_tokenproxy'),  # Ensure authtoken is available
    ]

    operations = [
        migrations.RunPython(transfer_tokens, reverse_transfer),
    ]
