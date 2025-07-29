from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import UserProfile

# Save the UserProfile when a User is saved (if the UserProfile exists)
@receiver(post_save, sender=UserProfile.user.field.model)
def save_user_profile(sender, instance, **kwargs):
    if hasattr(instance, 'userprofile'):
        instance.userprofile.save()
