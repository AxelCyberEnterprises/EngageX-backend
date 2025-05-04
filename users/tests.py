from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from django.contrib.auth import get_user_model
from django.core.cache import cache
from decimal import Decimal
import random

User = get_user_model()


class BaseUserTests(APITestCase):
    def setUp(self):
        # Clear cache in case OTPs persist from previous tests
        cache.clear()

        # Create a verified user for login and password tests.
        self.verified_user = User.objects.create_user(
            email="verified@example.com", password="testpass123",
            first_name="Verified", last_name="User"
        )
        self.verified_user.is_active = True
        self.verified_user.is_verified = True
        self.verified_user.save()

        # Create a user profile is assumed to be created via signals.
        # Create an admin user for testing set_password if needed.
        self.admin_user = User.objects.create_user(
            email="admin@example.com", password="adminpass123",
            first_name="Admin", last_name="User"
        )
        # Set admin role on profile:
        self.admin_user.userprofile.role = "admin"
        self.admin_user.userprofile.save()

    def test_registration_success(self):
        """
        Test that a new user can register successfully and receives a verification code.
        """
        url = reverse("user-list")  # Assuming UserCreateViewSet is registered as "user"
        payload = {
            "email": "newuser@example.com",
            "password": "newpass123",
            "first_name": "New",
            "last_name": "User"
        }
        response = self.client.post(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data.get("status"), "success")
        # Verify that the user is created but inactive
        user = User.objects.get(email="newuser@example.com")
        self.assertFalse(user.is_active)
        self.assertTrue(user.verification_code)

    def test_registration_duplicate(self):
        """
        Test that registering a duplicate user returns an error.
        """
        url = reverse("user-list")
        payload = {
            "email": "dup@example.com",
            "password": "dup123",
            "first_name": "Dup",
            "last_name": "User"
        }
        # First registration
        self.client.post(url, payload, format="json")
        # Second registration attempt
        response = self.client.post(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data.get("status"), "fail")

    def test_verify_email_success(self):
        """
        Test successful email verification.
        """
        # Register a user first
        url = reverse("user-list")
        payload = {
            "email": "verifyme@example.com",
            "password": "verify123",
            "first_name": "Verify",
            "last_name": "Me"
        }
        reg_response = self.client.post(url, payload, format="json")
        self.assertEqual(reg_response.status_code, status.HTTP_201_CREATED)

        user = User.objects.get(email="verifyme@example.com")
        code = user.verification_code

        verify_url = reverse("verify-email")
        verify_payload = {
            "email": "verifyme@example.com",
            "verification_code": code
        }
        response = self.client.post(verify_url, verify_payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data.get("status"), "success")
        user.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertTrue(user.is_verified)
        self.assertEqual(user.verification_code, "")

    def test_verify_email_missing_fields(self):
        """
        Test that missing email or verification code returns an error.
        """
        url = reverse("verify-email")
        payload = {"email": "verifyme@example.com"}  # missing code
        response = self.client.post(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_verify_email_wrong_code(self):
        """
        Test that an incorrect verification code returns an error.
        """
        url = reverse("user-list")
        payload = {
            "email": "wrongcode@example.com",
            "password": "pass1234",
            "first_name": "Wrong",
            "last_name": "Code"
        }
        self.client.post(url, payload, format="json")
        verify_url = reverse("verify-email")
        verify_payload = {
            "email": "wrongcode@example.com",
            "verification_code": "000000"  # an incorrect code
        }
        response = self.client.post(verify_url, verify_payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_login_success(self):
        """
        Test that a verified user can log in and receive a token.
        """
        login_url = reverse("login")  # Ensure this matches your URL name for CustomTokenCreateView
        payload = {
            "email": "verified@example.com",
            "password": "testpass123"
        }
        response = self.client.post(login_url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data.get("status"), "success")
        self.assertIn("token", response.data.get("data", {}))

    def test_login_missing_fields(self):
        """
        Test that login returns an error if email or password is missing.
        """
        login_url = reverse("login")
        payload = {"email": "verified@example.com"}  # Missing password
        response = self.client.post(login_url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_password_reset_request_success(self):
        """
        Test that a password reset request is successful when the user exists.
        """
        url = reverse("password-reset-request")
        payload = {"email": "verified@example.com"}
        response = self.client.post(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data.get("status"), "success")

    def test_password_reset_confirm_success(self):
        """
        Test that a valid OTP and new password reset the user's password.
        """
        # Create a user to reset password
        url = reverse("user-list")
        payload = {
            "email": "resetme@example.com",
            "password": "oldpass",
            "first_name": "Reset",
            "last_name": "Me"
        }
        self.client.post(url, payload, format="json")
        user = User.objects.get(email="resetme@example.com")

        # Request password reset (which sets an OTP in cache)
        reset_request_url = reverse("password-reset-request")
        self.client.post(reset_request_url, {"email": "resetme@example.com"}, format="json")
        otp = cache.get(f"password_reset_otp_{user.id}")
        self.assertIsNotNone(otp)

        reset_confirm_url = reverse("password-reset-confirm")
        confirm_payload = {
            "email": "resetme@example.com",
            "otp": str(otp),
            "new_password": "newpass123"
        }
        response = self.client.post(reset_confirm_url, confirm_payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data.get("status"), "success")

        # Verify login with new password
        login_url = reverse("login")
        login_payload = {"email": "resetme@example.com", "password": "newpass123"}
        login_response = self.client.post(login_url, login_payload, format="json")
        self.assertEqual(login_response.status_code, status.HTTP_200_OK)

    def test_set_password(self):
        """
        Test that an authenticated user can set their password.
        """
        # Force authenticate as verified_user
        self.client.force_authenticate(user=self.verified_user)
        url = reverse("customuser-set-password")  # Ensure your URL name is correct.
        payload = {
            "password": "newsecurepass",
            "re_password": "newsecurepass"
        }
        response = self.client.post(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data.get("status"), "success")

    def test_update_profile(self):
        """
        Test that an authenticated user can update their profile.
        """
        self.client.force_authenticate(user=self.verified_user)
        url = reverse("profile-update")  # Ensure this URL name matches your configuration.
        payload = {
            "first_name": "UpdatedFirst",
            "last_name": "UpdatedLast",
            "country": "USA"
        }
        response = self.client.put(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data.get("status"), "success")
        # Verify the changes
        self.verified_user.refresh_from_db()
        self.assertEqual(self.verified_user.first_name, "UpdatedFirst")
        self.assertEqual(self.verified_user.last_name, "UpdatedLast")
        self.assertEqual(self.verified_user.userprofile.country, "USA")

    def test_change_password(self):
        """
        Test that an authenticated user can change their password.
        """
        self.client.force_authenticate(user=self.verified_user)
        url = reverse("password-change")  # Ensure this URL name matches your configuration.
        payload = {
            "old_password": "testpass123",
            "new_password": "changedpass123",
            "confirm_new_password": "changedpass123"
        }
        response = self.client.post(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data.get("status"), "success")
        # Verify login with the new password
        login_url = reverse("login")
        login_payload = {"email": "verified@example.com", "password": "changedpass123"}
        login_response = self.client.post(login_url, login_payload, format="json")
        self.assertEqual(login_response.status_code, status.HTTP_200_OK)

    def test_non_authenticated_access(self):
        """
        Test that endpoints requiring authentication reject unauthenticated requests.
        """
        # For user profile endpoint
        url = reverse("userprofile-list")
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
