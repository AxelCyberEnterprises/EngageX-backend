from django.contrib.auth.models import BaseUserManager


class CustomUserManager(BaseUserManager):
    def create_user(self, email, password=None, username=None, **extra_fields):
        if not email:
            raise ValueError("The Email field must be set")
        email = self.normalize_email(email)
        user = self.model(email=email, username=username, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, username=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)

        if extra_fields.get('is_staff') is not True:
            raise ValueError('Superuser must have is_staff=True.')
        if extra_fields.get('is_superuser') is not True:
            raise ValueError('Superuser must have is_superuser=True.')

        # Create the user
        user = self.create_user(email, password, username, **extra_fields)
        user.is_active = True  # Ensure superuser is active
        user.save(using=self._db)  # Save changes explicitly

        # Update the related UserProfile role to 'admin'
        if hasattr(user, 'userprofile'):
            user.userprofile.role = 'admin'
            user.userprofile.save()

        return user
