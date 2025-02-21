from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import UserProfile, CustomUser

# Create a UserProfile when a new User is created
@receiver(post_save, sender=CustomUser)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        role = 'admin' if instance.is_superuser else 'user'
        UserProfile.objects.create(user=instance, role=role)


# Save the UserProfile when a User is saved (if the UserProfile exists)
@receiver(post_save, sender=CustomUser)
def save_user_profile(sender, instance, **kwargs):
    if hasattr(instance, 'userprofile'):
        instance.userprofile.save()