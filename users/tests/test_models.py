from django.test import TestCase
from .models import UserProfile, CustomUser

class UserProfileModelTest(TestCase):
    def setUp(self):
        self.user = CustomUser.objects.create(email='test@example.com', username='testuser')
        self.user_profile = UserProfile.objects.create(user=self.user, role='user')

    def test_user_profile_role(self):
        self.assertEqual(self.user_profile.role, 'user')
        self.user_profile.role = 'coach'
        self.user_profile.save()
        self.assertEqual(self.user_profile.role, 'coach') 