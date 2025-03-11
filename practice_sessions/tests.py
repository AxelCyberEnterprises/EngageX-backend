from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from django.contrib.auth import get_user_model
from practice_sessions.models import PracticeSession, SessionDetail
from datetime import timedelta
from django.utils.timezone import now

User = get_user_model()

class PracticeSessionTests(APITestCase):
    def setUp(self):
        # Create an admin user
        self.admin_user = User.objects.create_user(email='admin@example.com', password='adminpass')
        self.admin_user.userprofile.role = 'admin'
        self.admin_user.userprofile.save()

        # Create a regular user
        self.regular_user = User.objects.create_user(email='user@example.com', password='userpass')
        self.regular_user.userprofile.role = 'user'
        self.regular_user.userprofile.save()

        # Create a session for the admin user
        self.admin_session = PracticeSession.objects.create(
            user=self.admin_user,
            session_name="Admin Session",
            session_type="pitch",
            duration=timedelta(minutes=30)
        )
        SessionDetail.objects.create(
            session=self.admin_session,
            engagement=80,
            emotional_connection=75,
            energy=70,
            pitch_variation=60,
            volume_control=65,
            speech_rate=55,
            articulation=70,
            structure=80,
            impact=75,
            content_engagement=70,
            strengths=["Clear articulation"],
            areas_for_improvement=["More energy"]
        )

        # Create a session for the regular user
        self.regular_session = PracticeSession.objects.create(
            user=self.regular_user,
            session_name="User Session",
            session_type="presentation",
            duration=timedelta(minutes=45)
        )
        SessionDetail.objects.create(
            session=self.regular_session,
            engagement=70,
            emotional_connection=60,
            energy=65,
            pitch_variation=50,
            volume_control=55,
            speech_rate=60,
            articulation=65,
            structure=70,
            impact=75,
            content_engagement=80,
            strengths=["Good structure"],
            areas_for_improvement=["Improve articulation"]
        )

    def test_admin_get_all_sessions(self):
        self.client.force_authenticate(user=self.admin_user)
        url = reverse('practice-session-list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # If pagination is enabled, sessions may be in response.data['results']
        results = response.data.get('results', response.data)
        self.assertEqual(len(results), 2)

    def test_regular_user_get_own_sessions(self):
        self.client.force_authenticate(user=self.regular_user)
        url = reverse('practice-session-list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data.get('results', response.data)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['session_name'], "User Session")

    def test_report_endpoint_admin(self):
        self.client.force_authenticate(user=self.admin_user)
        url = reverse('practice-session-report', kwargs={'pk': self.regular_session.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('details', response.data)
        self.assertEqual(response.data['session_name'], "User Session")

    def test_report_endpoint_regular_user(self):
        self.client.force_authenticate(user=self.regular_user)
        url = reverse('practice-session-report', kwargs={'pk': self.regular_session.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('details', response.data)

    def test_dashboard_endpoint_admin(self):
        self.client.force_authenticate(user=self.admin_user)
        url = reverse('session-dashboard')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data
        self.assertIn("total_sessions", data)
        self.assertIn("session_breakdown", data)
        self.assertIn("sessions_over_time", data)
        self.assertIn("recent_sessions", data)

    def test_dashboard_endpoint_regular_user(self):
        self.client.force_authenticate(user=self.regular_user)
        url = reverse('session-dashboard')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data
        self.assertIn("latest_session_score", data)
        self.assertIn("performance_analytics", data)

    def test_non_authenticated_access(self):
        url = reverse('practice-session-list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
