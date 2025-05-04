from rest_framework.test import APIRequestFactory, force_authenticate
from .permissions import IsCoach
from .models import CustomUser, UserProfile
from django.test import TestCase


class IsCoachPermissionTest(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.user = CustomUser.objects.create(email='coach@example.com', username='coachuser')
        self.user_profile = UserProfile.objects.create(user=self.user, role='coach')

    def test_is_coach_permission(self):
        request = self.factory.get('/some-url/')
        request.user = self.user
        permission = IsCoach()
        self.assertTrue(permission.has_permission(request, None))
