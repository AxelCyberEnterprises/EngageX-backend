from django.shortcuts import render
from django.core.exceptions import ValidationError
from djoser.views import UserViewSet, TokenCreateView
from django.core.cache import cache
from django.core.mail import EmailMessage
from django.conf import settings
from django.contrib.auth import get_user_model, authenticate

from rest_framework.authtoken.models import Token
from rest_framework import viewsets, status, permissions
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.views import APIView
from rest_framework.response import Response

from .serializers import (
    UserSerializer,  UserProfileSerializer,
    CustomTokenCreateSerializer, UpdateProfileSerializer, ChangePasswordSerializer,
)
from .models import (UserProfile, CustomUser)

import random, tempfile, os

from google.oauth2 import id_token
from google.auth.transport import requests

# Authentication

class UserCreateViewSet(viewsets.ModelViewSet):
    """
    Handles user creation with email verification.
    """
    serializer_class = UserSerializer
    queryset = CustomUser.objects.all()

    def get_permissions(self):
        if self.action == 'create':
            return [AllowAny()]
        return [IsAuthenticated()]

    def create(self, request, *args, **kwargs):
        try:
            serializer = self.get_serializer(data=request.data)
            serializer.is_valid(raise_exception=True)

            # Save the user but set them inactive until they verify
            user = serializer.save(is_active=False)
            user.verification_code = str(random.randint(100000, 999999))
            print(user.verification_code)  # Debug: print the verification code
            user.save()

            # Send the verification email
            # email = EmailMessage(
            #     subject='Verify your account',
            #     body=f'Your verification code is {user.verification_code}',
            #     from_email=settings.DEFAULT_FROM_EMAIL,
            #     to=[user.email],
            # )
            # email.extra_headers = {'X-PM-Message-Stream': 'outbound'}
            # email.send(fail_silently=False)

            response_data = {
                "status": "success",
                "message": "Verification code sent to your email.",
                "data": {
                    "user": {
                        "email": user.email,
                        "username": user.first_name,
                        "otp": user.verification_code,
                        "first_name": user.first_name,
                        "last_name": user.last_name
                    }
                }
            }
            return Response(response_data, status=status.HTTP_201_CREATED)

        except ValidationError as e:
            # Handle validation errors (e.g., user already exists)
            response_data = {
                "status": "fail",
                "message": "User with this email already exists.",
                "data": {}
            }
            return Response(response_data, status=status.HTTP_400_BAD_REQUEST)

        except Exception as e:
            import traceback
            print(f"Error sending email: {e}")
            
            response_data = {
                "status": "fail",
                "message": "Error sending verification email.",
                "data": {}
            }
            return Response(response_data, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        

class VerifyEmailView(APIView):
    """
    Verifies user email by checking the provided verification code.
    """
    permission_classes = [AllowAny]  # Allow access without authentication

    def post(self, request, *args, **kwargs):
        email = request.data.get('email')
        verification_code = request.data.get('verification_code')

        print(f"Verification attempt with email: {email} and code: {verification_code}")  # Log input

        # Validate input data
        if not email or not verification_code:
            response_data = {
                "status": "fail",
                "message": "Both email and verification code are required."
            }
            return Response(response_data, status=status.HTTP_400_BAD_REQUEST)

        try:
            # Fetch the user based on email and verification code
            user = CustomUser.objects.get(email=email, verification_code=verification_code)

            # Check if the user is already verified
            if user.is_active:
                response_data = {
                    "status": "fail",
                    "message": "This email address has already been verified. Please log in."
                }
                return Response(response_data, status=status.HTTP_400_BAD_REQUEST)
            
            # Proceed to verify the user's email
            user.is_verified = True
            user.is_active = True
            user.verification_code = ''
            user.save()

            response_data = {
                "status": "success",
                "message": "Email verified successfully! You can now log in."
            }
            return Response(response_data, status=status.HTTP_200_OK)

        except CustomUser.DoesNotExist:
            print("User not found with provided email or verification code.")  # Log error
            response_data = {
                "status": "fail",
                "message": "No account found with the provided email address and verification code. Please check your input or request a new verification code."
            }
            return Response(response_data, status=status.HTTP_400_BAD_REQUEST)

        except Exception as e:
            print("An unexpected error occurred:", str(e))  # Log unexpected errors
            response_data = {
                "status": "fail",
                "message": "An unexpected error occurred while verifying your email. Please try again later."
            }
            return Response(response_data, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        

class CustomTokenCreateView(TokenCreateView):
    serializer_class = CustomTokenCreateSerializer

    def post(self, request, *args, **kwargs):
        # Log the incoming request for debugging
        print("Login attempt with data:", request.data)

        # Check for required fields in the request
        email = request.data.get('email')
        password = request.data.get('password')

        if not email or not password:
            response_data = {
                "status": "fail",
                "message": "Email and password are required.",
                "data": {
                    "email": "This field is required." if not email else None,
                    "password": "This field is required." if not password else None,
                }
            }
            return Response(response_data, status=status.HTTP_400_BAD_REQUEST)

        serializer = self.get_serializer(data=request.data)

        try:
            # Attempt to retrieve user by email 
            user = CustomUser.objects.get(email=email)

            # Validate and create the token using the custom serializer
            serializer.is_valid(raise_exception=True)
            token = serializer.validated_data['auth_token']
            response_data = {
                "status": "success",
                "message": "Login successful.",
                "data": {
                    "token": token,
                    "email": email,
                    "first_name": user.first_name,
                    "last_name": user.last_name,
                }
            }
            return Response(response_data, status=status.HTTP_200_OK)

        except ValidationError as e:
            # Handle validation errors and construct a clear response
            print("Login failed. Errors:", e.detail)
            error_messages = e.detail
            response_data = {
                "status": "fail",
                "message": error_messages.get('message', "Unable to log in with provided credentials."),
                "data": {
                    "error": error_messages.get('email', ["Invalid credentials."])[0]  # Get specific email error
                }
            }
            return Response(response_data, status=status.HTTP_400_BAD_REQUEST)

        except Exception as e:
            print("An unexpected error occurred:", str(e))
            return Response({
                "status": "fail",
                "message": "An unexpected error occurred.",
                "data": {
                    "error": "Internal server error. Please try again later."
                }
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        

class PasswordResetRequestView(APIView):
    """
    View to handle password reset requests by sending an OTP to the user's email.
    """

    permission_classes = [AllowAny]

    def post(self, request):
        # Extract email from request
        email = request.data.get("email")
        
        # Validate email field
        if not email:
            return Response({"status": "error", "message": "Email is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            # Attempt to retrieve user by email
            user = CustomUser.objects.get(email=email)
            
            # Generate a 6-digit OTP and store it in cache for 5 minutes
            otp = random.randint(100000, 999999)
            cache.set(f"password_reset_otp_{user.id}", otp, timeout=300)
            
            # Prepare and send email with OTP
            email_message = EmailMessage(
                subject='Password Reset Verification Code',
                body=f'Your OTP code is {otp}',
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=[user.email],
            )
            email_message.extra_headers = {'X-PM-Message-Stream': 'outbound'}
            email_message.send(fail_silently=False)

            # Success response
            return Response({"status": "success", "message": "OTP sent to email"}, status=status.HTTP_200_OK)
        
        except CustomUser.DoesNotExist:
            # Error response if user is not found
            return Response({"status": "error", "message": "User with this email does not exist"}, status=status.HTTP_404_NOT_FOUND)


class PasswordResetConfirmView(APIView):
    """
    View to handle password reset confirmation by validating OTP and setting a new password.
    """

    permission_classes = [AllowAny]

    def post(self, request):
        # Extract fields from request
        email = request.data.get("email")
        otp = request.data.get("otp")
        new_password = request.data.get("new_password")

        # Validate required fields
        if not email or not otp or not new_password:
            missing_fields = [field for field in ["email", "otp", "new_password"] if not request.data.get(field)]
            return Response(
                {"status": "error", "message": f"Missing required fields: {', '.join(missing_fields)}"},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            # Retrieve user by email
            user = CustomUser.objects.get(email=email)
            
            # Retrieve cached OTP
            cached_otp = cache.get(f"password_reset_otp_{user.id}")

            # Check if OTP is expired or invalid
            if cached_otp is None:
                return Response({"status": "error", "message": "OTP expired or invalid"}, status=status.HTTP_400_BAD_REQUEST)

            # Check if OTP is correct
            if str(cached_otp) != str(otp):
                return Response({"status": "error", "message": "OTP is incorrect"}, status=status.HTTP_400_BAD_REQUEST)

            # Set new password
            user.set_password(new_password)
            user.save()
            
            # Clear OTP from cache after successful reset
            cache.delete(f"password_reset_otp_{user.id}")

            # Success response
            return Response({"status": "success", "message": "Password reset successfully"}, status=status.HTTP_200_OK)
        
        except CustomUser.DoesNotExist:
            # Error response if user is not found
            return Response({"status": "error", "message": "User with this email does not exist"}, status=status.HTTP_404_NOT_FOUND)
        

class CustomUserViewSet(UserViewSet):
    def set_password(self, request, *args, **kwargs):
        user = request.user

        # Ensure required fields are present
        password = request.data.get("password")
        re_password = request.data.get("re_password")

        if not password or not re_password:
            return Response({
                "status": "fail",
                "message": "Both password and re_password fields are required."
            }, status=status.HTTP_400_BAD_REQUEST)

        if password != re_password:
            return Response({
                "status": "fail",
                "message": "Passwords do not match."
            }, status=status.HTTP_400_BAD_REQUEST)

        # Update the password
        user.set_password(password)
        user.save()

        return Response({
            "status": "success",
            "message": "Password has been set successfully."
        }, status=status.HTTP_200_OK)



class UserProfileViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = UserProfileSerializer

    def get_queryset(self):
        # Access the user's profile to get the organization
        user_profile = self.request.user.userprofile
        return UserProfile.objects.filter(organization=user_profile.organization)


class UpdateProfileView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def put(self, request, *args, **kwargs):
        user = request.user
        profile = user.userprofile  # Assuming a OneToOne relationship

        # Update user fields
        user_serializer = UpdateProfileSerializer(user, data=request.data, partial=True)
        profile_serializer = UserProfileSerializer(profile, data=request.data, partial=True)

        if user_serializer.is_valid() and profile_serializer.is_valid():
            user_serializer.save()
            profile_serializer.save()
            return Response({
                "status": "success",
                "message": "Profile updated successfully.",
                "data": {
                    "user": user_serializer.data,
                    "profile": profile_serializer.data
                }
            }, status=status.HTTP_200_OK)

        return Response({
            "status": "fail",
            "message": "Profile update failed.",
            "errors": {
                "user": user_serializer.errors,
                "profile": profile_serializer.errors
            }
        }, status=status.HTTP_400_BAD_REQUEST)
    

class ChangePasswordView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, *args, **kwargs):
        serializer = ChangePasswordSerializer(data=request.data, context={'request': request})

        if serializer.is_valid():
            serializer.save()
            return Response({
                "status": "success",
                "message": "Password updated successfully."
            }, status=status.HTTP_200_OK)

        return Response({
            "status": "fail",
            "message": "Password update failed.",
            "errors": serializer.errors
        }, status=status.HTTP_400_BAD_REQUEST)
    

class GoogleLoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        token = request.data.get("token")
        print(f"Received Google login request. Token: {token[:20]}... (truncated)")

        if not token:
            return Response({"message": "Token is required."}, status=status.HTTP_400_BAD_REQUEST)

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
                return Response({"message": "Server misconfiguration."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

            # Validate audience dynamically
            expected_client_ids = {google_client_id, azp}
            if token_audience not in expected_client_ids:
                print(f"Token has wrong audience {token_audience}, expected one of {expected_client_ids}")
                return Response({"message": "Invalid token audience."}, status=status.HTTP_400_BAD_REQUEST)

            email = id_info.get("email")
            first_name = id_info.get("given_name", "")
            last_name = id_info.get("family_name", "")
            gender = id_info.get("gender", "P")  # Default to "Prefer not to say"
            language = id_info.get("locale", "en")  # Default to English

            if not email:
                print("Invalid token data: missing email.")
                return Response({"message": "Invalid token data."}, status=status.HTTP_400_BAD_REQUEST)

            # Create user if they don't exist
            User = get_user_model()
            user, created = User.objects.get_or_create(
                email=email,
                defaults={
                    'first_name': first_name,
                    'last_name': last_name,
                    'username': email,
                }
            )

            # If the user was created, save them
            if created:
                user.save()

            # Update user information with the data provided by Google
            user.first_name = first_name or user.first_name
            user.last_name = last_name or user.last_name
            user.username = user.username or email
            user.save()

            # Create or get the existing auth token for the user
            token, created = Token.objects.get_or_create(user=user)

            # Save profile picture (after user is created)
            user_profile, created_profile = UserProfile.objects.get_or_create(user=user)

            # if 'picture' in id_info:
            #     profile_picture_url = id_info['picture']
            #     saved_picture_url = save_google_profile_picture(user, profile_picture_url)
            #     if saved_picture_url:
            #         user_profile.profile_picture = saved_picture_url


            # Save user profile
            user_profile.gender = gender or user_profile.gender
            user_profile.language_preference = language or user_profile.language_preference
            user_profile.save()

            print("Token generated:", token.key)

            return Response({
                "status": "success",
                "message": "Google login successful.",
                "data": {
                    "token": token.key,
                    "email": user.email,
                    "first_name": user.first_name,
                    "last_name": user.last_name,
                    # "profile_picture": user_profile.profile_picture,
                    "gender": user_profile.gender,
                    "language_preference": user_profile.language_preference
                }
            }, status=status.HTTP_200_OK)

        except ValueError as e:
            print(f"Invalid Google token: {str(e)}")
            return Response({"message": "Invalid Google token."}, status=status.HTTP_400_BAD_REQUEST)