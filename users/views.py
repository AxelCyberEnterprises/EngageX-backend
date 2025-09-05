from django.shortcuts import render, get_object_or_404
from django.core.exceptions import ValidationError
from djoser.views import UserViewSet, TokenCreateView
from django.core.cache import cache
from django.core.mail import EmailMessage, send_mail
from django.conf import settings
from django.contrib.auth import get_user_model, authenticate
from django.utils import timezone
from django.db import transaction, IntegrityError

from rest_framework.authtoken.models import Token
from rest_framework import viewsets, status, permissions
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser, FormParser

from .serializers import (
    UserSerializer,
    UserProfileSerializer,
    CustomTokenCreateSerializer,
    UpdateProfileSerializer,
    ChangePasswordSerializer,
    UserAssignmentSerializer,
    VerifyEmailSerializer,
    PasswordResetRequestSerializer,
    PasswordResetConfirmSerializer,
    ContactUsSerializer,
    OTPSerializer,
    ResendOTPSerializer,
)
from .models import UserProfile, CustomUser, UserAssignment
from .permissions import IsAdmin
from drf_yasg.utils import swagger_auto_schema

import random, tempfile, os
import secrets

from google.oauth2 import id_token
from google.auth.transport import requests
import boto3
from .utils.email import send_email_via_ses

User = get_user_model()


# Authentication


class UserCreateViewSet(viewsets.ModelViewSet):
    """
    Handles user creation with email verification.
    By default, all actions require authentication except for user creation.
    """
    serializer_class = UserSerializer
    queryset = CustomUser.objects.all()
    permission_classes = [IsAuthenticated]  # Default permission for all actions

    def get_permissions(self):
        """
        Override to allow unauthenticated access to the create action only.
        All other actions require authentication.
        """
        if self.action == "create":
            return [AllowAny()]
        return super().get_permissions()

    def create(self, request, *args, **kwargs):
        try:
            if CustomUser.objects.filter(email=request.data.get("email")).exists():
                raise ValidationError("User with this email already exists.")

            serializer = self.get_serializer(data=request.data)
            serializer.is_valid(raise_exception=True)

            with transaction.atomic():
                # Save user but keep them inactive
                user = serializer.save(is_active=False)
                user.verification_code = str(secrets.randbelow(900000) + 100000)
                user.save()

                # Send verification email via AWS SES
                subject = "Welcome to EngageX: Verify your account"
                message = f"Your verification code is {user.verification_code}"
                recipient_list = [user.email]

                email_response = send_email_via_ses(
                    subject=subject, body=message, to_emails=recipient_list
                )

                if not email_response:
                    raise IntegrityError("Email sending failed.")

            response_data = {
                "status": "success",
                "message": "Verification code sent to your email.",
                "data": {
                    "user": {
                        "id": user.id,
                        "email": user.email,
                        "username": user.first_name,
                        "first_name": user.first_name,
                        "last_name": user.last_name,
                    }
                },
            }
            return Response(response_data, status=status.HTTP_201_CREATED)

        except ValidationError as e:
            return Response(
                {"status": "fail", "message": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        except IntegrityError:
            return Response(
                {
                    "status": "fail",
                    "message": "Error sending verification email. Please try again.",
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        except Exception as e:
            print(f"Unexpected error: {e}")
            return Response(
                {"status": "fail", "message": "Something went wrong."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class VerifyEmailView(APIView):
    """
    Verifies user email by checking the provided verification code.
    """

    permission_classes = [AllowAny]  # Allow access without authentication

    @swagger_auto_schema(
        operation_description="Register a Vendor user ",
        request_body=VerifyEmailSerializer,
        responses={
            200: "OTP sent successfully",
            400: "OTP not sent successfully",
        },
    )
    def post(self, request, *args, **kwargs):
        email = request.data.get("email")
        verification_code = request.data.get("verification_code")

        print(
            f"Verification attempt with email: {email} and code: {verification_code}"
        )  # Log input

        # Validate input data
        if not email or not verification_code:
            response_data = {
                "status": "fail",
                "message": "Both email and verification code are required.",
            }
            return Response(response_data, status=status.HTTP_400_BAD_REQUEST)

        try:
            # Fetch the user based on email and verification code
            user = CustomUser.objects.get(
                email=email, verification_code=verification_code
            )

            # Check if the user is already verified
            if user.is_active:
                response_data = {
                    "status": "fail",
                    "message": "This email address has already been verified. Please log in.",
                }
                return Response(response_data, status=status.HTTP_400_BAD_REQUEST)

            # Check if this is the first time the user is being verified
            first_time_verification = not user.has_logged_in
            
            # Proceed to verify the user's email
            user.is_verified = True
            user.is_active = True
            user.verification_code = ""
            user.has_logged_in = True
            user.save(update_fields=["is_verified", "is_active", "verification_code", "has_logged_in"])

            # Create token with expiration using the create_token method
            from .models import ExpiringToken
            # Use remember_me from request data, default to False if not provided
            remember_me = request.data.get('remember_me', False)
            token = ExpiringToken.create_token(user, remember_me=remember_me)
            data = {
                "token": token.key,
                "user_id": user.id,
                "email": email,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "is_admin": user.is_superuser,
                "first_time_verification": first_time_verification,
            }
            response_data = {
                "data": data,
                "status": "success",
                "message": "Email verified successfully! You can now log in.",
            }
            return Response(response_data, status=status.HTTP_200_OK)

        except CustomUser.DoesNotExist:
            print(
                "User not found with provided email or verification code."
            )  # Log error
            response_data = {
                "status": "fail",
                "message": "No account found with the provided email address and verification code. Please check your input or request a new verification code.",
            }
            return Response(response_data, status=status.HTTP_400_BAD_REQUEST)

        except Exception as e:
            print("An unexpected error occurred:", str(e))  # Log unexpected errors
            response_data = {
                "status": "fail",
                "message": "An unexpected error occurred while verifying your email. Please try again later.",
            }
            return Response(response_data, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class VerifyOTPView(APIView):
    """
    Verify the OTP and return an auth token if valid.
    Rate limited to 5 attempts per 15 minutes per IP address.
    """
    permission_classes = [AllowAny]
    serializer_class = OTPSerializer
    throttle_scope = 'verify_otp'

    @swagger_auto_schema(
        operation_description="Verify OTP for 2FA",
        request_body=OTPSerializer,
        responses={
            200: "OTP verified successfully. Returns auth token.",
            400: "Invalid OTP or OTP expired.",
            404: "User not found.",
            429: "Too many attempts. Please try again later.",
        },
    )
    def post(self, request, *args, **kwargs):
        # Get client IP for rate limiting
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0]
        else:
            ip = request.META.get('REMOTE_ADDR')
            
        # Log the attempt for rate limiting
        cache_key = f'otp_verify_attempts_{ip}'
        attempts = cache.get(cache_key, 0)
        
        if attempts >= 5:  # 5 attempts allowed
            return Response(
                {"status": "error", "message": "Too many attempts. Please try again later."},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
            
        # Increment attempt counter (expires in 15 minutes)
        cache.set(cache_key, attempts + 1, 900)  # 15 minutes = 900 seconds

        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)

        email = serializer.validated_data["email"]
        otp = serializer.validated_data["otp_code"]

        try:
            user = User.objects.get(email=email, is_active=True)
        except User.DoesNotExist:
            return Response(
                {"status": "error", "message": "User not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Verify OTP
        if not user.verify_otp(otp):
            return Response(
                {"status": "error", "message": "Invalid or expired OTP."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Clear the OTP after successful verification
        user.clear_otp()
        
        # Reset rate limiting on successful verification
        cache.delete(cache_key)

        # Generate a new auth token with expiration (3 days by default)
        # This will automatically handle any existing tokens for the user
        from .models import ExpiringToken
        token = ExpiringToken.create_token(user=user, remember_me=False)

        # Prepare user data for response
        user_data = {
            "is_admin": user.is_staff or user.is_superuser,  # Assuming admin users are staff or superusers
            "token": token.key,
            "email": user.email,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "user_id": user.id
        }

        return Response(
            {
                "status": "success",
                "message": "OTP verified successfully.",
                "data": user_data,
            },
            status=status.HTTP_200_OK,
        )


class CustomTokenCreateView(TokenCreateView):
    """
    Handles user login with 2FA (Two-Factor Authentication) flow.
    After successful password verification, sends an OTP to the user's email.
    The user must verify the OTP to complete the login.
    """

    serializer_class = CustomTokenCreateSerializer
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        operation_description="Login with email and password to initiate 2FA flow",
        request_body=CustomTokenCreateSerializer,
        responses={
            200: "OTP sent successfully",
            400: "Invalid input data",
            401: "Invalid credentials or inactive account",
            404: "User not found",
        },
    )
    def post(self, request, *args, **kwargs):
        # Log the incoming request for debugging
        print("Login attempt with data:", request.data)

        # Check for required fields in the request
        email = request.data.get("email")
        password = request.data.get("password")

        if not email or not password:
            response_data = {
                "status": "fail",
                "message": "Email and password are required.",
                "data": {
                    "email": "This field is required." if not email else None,
                    "password": "This field is required." if not password else None,
                },
            }
            return Response(response_data, status=status.HTTP_400_BAD_REQUEST)

        try:
            # Attempt to retrieve user by email
            user = CustomUser.objects.get(email=email)
            
            # Check if the provided password is correct
            if not user.check_password(password):
                return Response(
                    {"status": "fail", "message": "Incorrect email or password"},
                    status=status.HTTP_401_UNAUTHORIZED,
                )

            # Check if the user account is active
            if not user.is_active:
                return Response(
                    {"status": "fail", "message": "Account not verified or inactive"},
                    status=status.HTTP_401_UNAUTHORIZED,
                )

            # Generate and send OTP to user's email
            if not user.send_otp_email():
                return Response(
                    {"status": "error", "message": "Failed to send OTP. Please try again later."},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

            # Check if this is the first login
            first_login = not user.has_logged_in
            
            response_data = {
                "status": "success",
                "message": "OTP has been sent to your email. Please verify to complete login.",
                "data": {
                    "email": email,
                    "first_login": first_login,
                    "otp_required": True,
                },
            }
            return Response(response_data, status=status.HTTP_200_OK)

        except CustomUser.DoesNotExist:
            return Response(
                {"status": "fail", "message": "No account found with this email"},
                status=status.HTTP_404_NOT_FOUND,
            )


class ResendOTPView(APIView):
    """
    Resend OTP to the user's email.
    Rate limited to 3 resend attempts per 15 minutes per email.
    """
    permission_classes = [AllowAny]
    serializer_class = ResendOTPSerializer
    throttle_scope = 'resend_otp'

    @swagger_auto_schema(
        operation_description="Resend OTP for 2FA",
        request_body=ResendOTPSerializer,
        responses={
            200: "OTP has been resent successfully.",
            400: "Invalid email or user not found.",
            429: "Too many resend attempts. Please try again later.",
        },
    )
    def post(self, request, *args, **kwargs):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)

        email = serializer.validated_data["email"]
        
        # Check rate limiting for this email
        resend_cache_key = f'otp_resend_attempts_{email}'
        resend_attempts = cache.get(resend_cache_key, 0)
        
        if resend_attempts >= 3:  # 3 resend attempts allowed
            return Response(
                {"status": "error", "message": "Too many resend attempts. Please try again later."},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
            
        # Increment resend counter (expires in 15 minutes)
        cache.set(resend_cache_key, resend_attempts + 1, 900)  # 15 minutes = 900 seconds

        try:
            user = User.objects.get(email=email, is_active=True)
        except User.DoesNotExist:
            return Response(
                {"status": "error", "message": "User not found or inactive."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Generate and send new OTP
        otp = user.generate_otp()
        
        # Send OTP via email
        subject = "Your New Verification Code"
        message = f"Your new verification code is: {otp}\n\nThis code will expire in 10 minutes."
        try:
            send_email_via_ses(subject, message, [user.email])
        except Exception as e:
            logger.error(f"Failed to send OTP email to {user.email}: {str(e)}")
            return Response(
                {
                    "status": "error",
                    "message": "Failed to send OTP. Please try again later.",
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                "status": "success",
                "message": "A new OTP has been sent to your email address.",
            },
            status=status.HTTP_200_OK,
        )


class PasswordResetRequestView(APIView):
    """
    View to handle password reset requests by sending an OTP to the user's email.
    """

    permission_classes = [AllowAny]

    @swagger_auto_schema(
        operation_description="View to handle password reset requests by sending an OTP to the user's email",
        request_body=PasswordResetRequestSerializer,
        responses={},
    )
    def post(self, request):
        email = request.data.get("email")
        if not email:
            return Response(
                {"status": "error", "message": "Email is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            user = CustomUser.objects.get(email=email)

            otp = random.randint(100000, 999999)
            cache.set(f"password_reset_otp_{user.id}", otp, timeout=300)

            subject = "Password Reset Verification Code"
            body = f"Your OTP code is {otp}"

            try:
                send_email_via_ses(subject, body, [user.email])
            except Exception as e:
                print(f"Error sending OTP email via SES: {e}")
                return Response(
                    {"status": "error", "message": "Failed to send OTP email"},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

            return Response(
                {"status": "success", "message": "OTP sent to email", "otp": otp},
                status=status.HTTP_200_OK,
            )

        except CustomUser.DoesNotExist:
            return Response(
                {"status": "error", "message": "User with this email does not exist"},
                status=status.HTTP_404_NOT_FOUND,
            )


class PasswordResetConfirmView(APIView):
    """
    View to handle password reset confirmation by validating OTP and setting a new password.
    """

    permission_classes = [AllowAny]

    @swagger_auto_schema(
        operation_description=" View to handle password reset confirmation by validating OTP and setting a new password.",
        request_body=PasswordResetConfirmSerializer,
        responses={},
    )
    def post(self, request):
        # Extract fields from request
        email = request.data.get("email")
        otp = request.data.get("otp")
        new_password = request.data.get("new_password")
        confirm_new_passsword = request.data.get("confirm_new_password")

        # Log the incoming request data
        print(
            f"Password reset request received. Email: {email}, OTP: {otp}, New Password: {new_password}"
        )

        # Validate required fields
        if not email or not otp or not new_password:
            missing_fields = [
                field
                for field in ["email", "otp", "new_password"]
                if not request.data.get(field)
            ]
            print(f"Missing required fields: {', '.join(missing_fields)}")
            return Response(
                {
                    "status": "error",
                    "message": f"Missing required fields: {', '.join(missing_fields)}",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        if new_password != confirm_new_passsword:
            return Response(
                {
                    "status": "error",
                    "message": f"Incorrect Password",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            # Retrieve user by email
            user = CustomUser.objects.get(email=email)
            print(f"User found: {user.email}")

            # Retrieve cached OTP
            cached_otp = cache.get(f"password_reset_otp_{user.id}")
            print(f"Cached OTP: {cached_otp}")

            # Check if OTP is expired or invalid
            if cached_otp is None:
                print("OTP expired or invalid")
                return Response(
                    {"status": "error", "message": "OTP expired or invalid"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Check if OTP is correct
            if str(cached_otp) != str(otp):
                print("OTP is incorrect")
                return Response(
                    {"status": "error", "message": "OTP is incorrect"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Log user existence before setting the password
            print(f"User exists: {user.email}")

            # Log the password being set (length only for security reasons)
            print(f"Setting new password of length: {len(new_password)}")

            # Set new password
            user.set_password(new_password)
            user.save()
            print("Password reset successfully")

            # Clear OTP from cache after successful reset
            cache.delete(f"password_reset_otp_{user.id}")

            # Invalidate user sessions after password reset
            from django.contrib.sessions.models import Session

            sessions = Session.objects.filter(expire_date__gte=timezone.now())
            for session in sessions:
                data = session.get_decoded()
                if user.id == data.get("_auth_user_id"):
                    session.delete()
            print("User sessions invalidated")

            # Log the password hash for verification
            print(f"Password hash: {user.password}")

            # Success response
            return Response(
                {"status": "success", "message": "Password reset successfully"},
                status=status.HTTP_200_OK,
            )

        except CustomUser.DoesNotExist:
            print("User with this email does not exist")
            return Response(
                {"status": "error", "message": "User with this email does not exist"},
                status=status.HTTP_404_NOT_FOUND,
            )

        except Exception as e:
            # Log unexpected errors
            print("An unexpected error occurred during password reset:", str(e))
            return Response(
                {
                    "status": "error",
                    "message": "An unexpected error occurred. Please try again later.",
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class CustomUserViewSet(UserViewSet):
    """
    Sets a password for a user without one
    """

    def set_password(self, request, *args, **kwargs):
        user = request.user

        # Ensure required fields are present
        password = request.data.get("password")
        confirm_password = request.data.get("confirm_password")

        if not password or not confirm_password:
            return Response(
                {
                    "status": "fail",
                    "message": "Both password and confirm_password fields are required.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if password != confirm_password:
            return Response(
                {"status": "fail", "message": "Passwords do not match."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Update the password
        user.set_password(password)
        user.save()

        return Response(
            {"status": "success", "message": "Password has been set successfully."},
            status=status.HTTP_200_OK,
        )


class UserProfileViewSet(viewsets.ModelViewSet):
    """
    Returns the user profile based on the roles.
    """

    serializer_class = UserProfileSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user

        if getattr(self, "swagger_fake_view", False) or user.is_anonymous:
            return (
                UserProfile.objects.none()
            )  # Return empty queryset for schema generation or anonymous users

        if hasattr(
                user, "user_profile"
        ):  # Check if userprofile exists before accessing it
            if user.user_profile.is_admin():
                # Admins can see everything
                return UserProfile.objects.select_related(
                    'user',
                    'user__enterprise_profile',
                    'user__enterprise_profile__enterprise'
                ).all()

            elif user.user_profile.is_user():
                # Regular users can only see their own profile
                return UserProfile.objects.select_related(
                    'user',
                    'user__enterprise_profile',
                    'user__enterprise_profile__enterprise'
                ).filter(user=user)

        # Default: Return an empty queryset for non-admin, non-presenter, non-coach and users without userprofile
        return UserProfile.objects.none()

    # def list(self, request, *args, **kwargs):
    #     queryset = self.get_queryset()
    #     serializer = self.get_serializer(queryset, many=True)
    #
    #     return Response(serializer.data)


class ChangePasswordView(APIView):
    """
    Change password of the user making the request
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, *args, **kwargs):
        serializer = ChangePasswordSerializer(
            data=request.data, context={"request": request}
        )

        if serializer.is_valid():
            serializer.save()
            return Response(
                {"status": "success", "message": "Password updated successfully."},
                status=status.HTTP_200_OK,
            )

        return Response(
            {
                "status": "fail",
                "message": "Password update failed.",
                "errors": serializer.errors,
            },
            status=status.HTTP_400_BAD_REQUEST,
        )


class GoogleLoginView(APIView):
    """
    Google login using OAUTH2
    """

    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        token = request.data.get("token")
        print(f"Received Google login request. Token: {token[:20]}... (truncated)")

        if not token:
            return Response(
                {"message": "Token is required."}, status=status.HTTP_400_BAD_REQUEST
            )

        try:
            # Verify the token with Google
            id_info = id_token.verify_oauth2_token(token, requests.Request())
            print(f"Token verified. ID Info: {id_info}")

            token_audience = id_info.get("aud")
            azp = id_info.get("azp")

            # Get expected Client ID from env
            google_client_id = os.getenv("GOOGLE_CLIENT_ID")
            if not google_client_id:
                print("GOOGLE_CLIENT_ID is not set in environment variables.")
                return Response(
                    {"message": "Server misconfiguration."},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

            # Validate audience dynamically
            expected_client_ids = {google_client_id, azp}
            if token_audience not in expected_client_ids:
                print(
                    f"Token has wrong audience {token_audience}, expected one of {expected_client_ids}"
                )
                return Response(
                    {"message": "Invalid token audience."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            email = id_info.get("email")
            first_name = id_info.get("given_name", "")
            last_name = id_info.get("family_name", "")
            gender = id_info.get("gender", "P")  # Default to "Prefer not to say"
            language = id_info.get("locale", "en")  # Default to English

            if not email:
                print("Invalid token data: missing email.")
                return Response(
                    {"message": "Invalid token data."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Create user if they don't exist

            user, created = User.objects.get_or_create(
                email=email,
                defaults={
                    "first_name": first_name,
                    "last_name": last_name,
                    "username": email,
                },
            )

            # If the user was created, save them
            if created:
                user.save()

            # Update user information with the data provided by Google
            user.first_name = first_name or user.first_name
            user.last_name = last_name or user.last_name
            user.username = user.username or email
            user.save()

            # Create or get the existing auth token for the user with expiration
            from .models import ExpiringToken
            token, created = ExpiringToken.objects.get_or_create(user=user)
            if created:
                # Set expiration time (3 days by default)
                token.expires_at = timezone.now() + timezone.timedelta(days=3)
                token.save()

            # Save profile picture (after user is created)
            user_profile, created_profile = UserProfile.objects.get_or_create(user=user)

            # if 'picture' in id_info:
            #     profile_picture_url = id_info['picture']
            #     saved_picture_url = save_google_profile_picture(user, profile_picture_url)
            #     if saved_picture_url:
            #         user_profile.profile_picture = saved_picture_url

            # Save user profile
            user_profile.gender = gender or user_profile.gender
            user_profile.language_preference = (
                    language or user_profile.language_preference
            )
            user_profile.save()

            # First login logic for social login
            first_login = not user.has_logged_in
            if first_login:
                user.has_logged_in = True
                user.save(update_fields=["has_logged_in"])

            print("Token generated:", token.key)

            # Prepare user data for response with consistent format
            user_data = {
                "is_admin": user.is_staff or user.is_superuser,
                "token": token.key,
                "email": user.email,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "user_id": user.id,
                # Additional Google-specific data
                "gender": user_profile.gender,
                "language_preference": user_profile.language_preference,
                "first_login": first_login,
            }

            return Response(
                {
                    "status": "success",
                    "message": "Google login successful.",
                    "data": user_data,
                },
                status=status.HTTP_200_OK,
            )

        except ValueError as e:
            print(f"Invalid Google token: {str(e)}")
            return Response(
                {"message": "Invalid Google token."}, status=status.HTTP_400_BAD_REQUEST
            )


class UserAssignmentViewSet(viewsets.ModelViewSet):
    queryset = UserAssignment.objects.select_related("admin", "user").all()
    serializer_class = UserAssignmentSerializer
    permission_classes = [IsAuthenticated]

    @action(detail=False, methods=["post"])
    def assign_user(self, request):
        """
        Assign a user to an admin.
        Expected request JSON:
        {
            "admin_email": "admin@example.com",
            "user_email": "user@example.com"
        }
        """
        print(f"Incoming request data: {request.data}")

        admin_email = request.data.get("admin_email")
        user_email = request.data.get("user_email")

        if not admin_email or not user_email:
            print("Missing admin_email or user_email in request.")
            return Response(
                {"error": "Both admin_email and user_email are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Validate the admin
        admin = get_object_or_404(CustomUser, email=admin_email)
        print(f"Admin found: {admin.email} (ID: {admin.id})")
        admin_profile = getattr(admin, "userprofile", None)
        if not admin_profile or admin_profile.role != "admin":
            print(f"Invalid admin role for email: {admin.email}")
            return Response(
                {"error": "Invalid admin email or user is not an admin."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Validate the user
        user = get_object_or_404(CustomUser, email=user_email)
        print(f"User found: {user.email} (ID: {user.id})")
        user_profile = getattr(user, "userprofile", None)
        if not user_profile or user_profile.role != "user":
            print(f"Invalid user role for email: {user.email}")
            return Response(
                {"error": "Invalid user email or user is not a normal user."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Prevent self-assignment
        if admin == user:
            print("Attempted to assign a user to themselves.")
            return Response(
                {"error": "A user cannot be assigned to themselves."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Create assignment
        assignment, created = UserAssignment.objects.get_or_create(
            admin=admin, user=user
        )
        if created:
            print(f"New assignment created: {admin.email} -> {user.email}")
            return Response(
                {
                    "message": "User assigned successfully.",
                    "assignment": UserAssignmentSerializer(assignment).data,
                },
                status=status.HTTP_201_CREATED,
            )
        print(f"User {user.email} is already assigned to Admin {admin.email}.")
        return Response(
            {"message": "User already assigned."}, status=status.HTTP_200_OK
        )

    @action(detail=False, methods=["get"])
    def admin_users(self, request):
        """
        Retrieve all admins and their assigned users.
        Returns a dictionary where keys are admin emails and values are lists of user emails.
        """
        print("Fetching all admin-user assignments.")
        assignments = UserAssignment.objects.select_related("admin", "user").all()
        data = {}
        for assignment in assignments:
            admin_email = assignment.admin.email
            user_email = assignment.user.email
            data.setdefault(admin_email, []).append(user_email)
        print(f"Returning {len(assignments)} assignments.")
        return Response(data, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"])
    def user_admin(self, request):
        """
        Retrieve the admin for a specific user.
        Expects a query parameter: ?user_email=user@example.com
        """
        user_email = request.query_params.get("user_email")
        if not user_email:
            print("Missing user_email in request.")
            return Response(
                {"error": "User email is required."}, status=status.HTTP_400_BAD_REQUEST
            )
        try:
            assignment = UserAssignment.objects.select_related("admin").get(
                user__email=user_email
            )
            print(f"Admin found for user {user_email}: {assignment.admin.email}")
            return Response(
                {"admin_email": assignment.admin.email}, status=status.HTTP_200_OK
            )
        except UserAssignment.DoesNotExist:
            print(f"No admin assigned for user: {user_email}")
            return Response(
                {"error": "No admin assigned for this user."},
                status=status.HTTP_404_NOT_FOUND,
            )


class ContactUsView(APIView):
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        operation_description="View for Contact us",
        request_body=ContactUsSerializer,
        responses={},
    )
    def post(self, request):
        serializer = ContactUsSerializer(data=request.data)
        if serializer.is_valid():
            data = serializer.validated_data

            # Build email content
            subject = "New Contact Us Submission"
            message = f"""
New Contact Us Submission

First Name: {data['first_name']}
Last Name: {data['last_name']}
Email: {data['email']}

Message:
{data['message']}

Agreed to Privacy Policy: {"Yes" if data['agreed_to_policy'] else "No"}

-------------------------------
This message was sent via the Contact Us form on your website.
            """

            email_response = send_email_via_ses(
                subject=subject, body=message, to_emails=[settings.DEFAULT_FROM_EMAIL]
            )

            if not email_response:
                raise IntegrityError("Email sending failed.")

            return Response(
                {"message": "Your message has been sent successfully."},
                status=status.HTTP_200_OK,
            )

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
