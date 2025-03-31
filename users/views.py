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
    UserSerializer,  UserProfileSerializer,
    CustomTokenCreateSerializer, UpdateProfileSerializer, ChangePasswordSerializer, UserAssignmentSerializer
)
from .models import (UserProfile, CustomUser, UserAssignment)
from .permissions import IsAdmin

import random, tempfile, os
import secrets

from google.oauth2 import id_token
from google.auth.transport import requests
import boto3

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
            if CustomUser.objects.filter(email=request.data.get('email')).exists():
                raise ValidationError("User with this email already exists.")

            serializer = self.get_serializer(data=request.data)
            serializer.is_valid(raise_exception=True)

            with transaction.atomic():
                # Save user but keep them inactive
                user = serializer.save(is_active=False)
                user.verification_code = str(secrets.randbelow(9000) + 1000)  # Secure 4-digit OTP
                user.save()

                # Send verification email using AWS SES
                subject = "Verify your account"
                message = f"Your verification code is {user.verification_code}"
                from_email = settings.DEFAULT_FROM_EMAIL
                recipient_list = [user.email]

                try:
                    send_mail(
                        subject,
                        message,
                        from_email,
                        recipient_list,
                        fail_silently=False,
                    )
                except Exception as e:
                    print(f"Error sending email: {e}")
                    raise IntegrityError("Email sending failed.")  # Trigger rollback if email fails

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
                }
            }
            return Response(response_data, status=status.HTTP_201_CREATED)

        except ValidationError as e:
            return Response({"status": "fail", "message": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        except IntegrityError:
            return Response(
                {"status": "fail", "message": "Error sending verification email. Please try again."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        except Exception as e:
            print(f"Unexpected error: {e}")
            return Response(
                {"status": "fail", "message": "Something went wrong."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


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
    """
    Logs user in and provides authentication Token that would be used for other endpoints requiring authentication
    """
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
                    "is_admin": user.is_superuser
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
            # email_message = EmailMessage(
            #     subject='Password Reset Verification Code',
            #     body=f'Your OTP code is {otp}',
            #     from_email=settings.DEFAULT_FROM_EMAIL,
            #     to=[user.email],
            # )
            # email_message.extra_headers = {'X-PM-Message-Stream': 'outbound'}
            # email_message.send(fail_silently=False)

            # Success response
            return Response({"status": "success", "message": "OTP sent to email", "otp": otp}, status=status.HTTP_200_OK)
        
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

        # Log the incoming request data
        print(f"Password reset request received. Email: {email}, OTP: {otp}, New Password: {new_password}")

        # Validate required fields
        if not email or not otp or not new_password:
            missing_fields = [field for field in ["email", "otp", "new_password"] if not request.data.get(field)]
            print(f"Missing required fields: {', '.join(missing_fields)}")
            return Response(
                {"status": "error", "message": f"Missing required fields: {', '.join(missing_fields)}"},
                status=status.HTTP_400_BAD_REQUEST
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
                return Response({"status": "error", "message": "OTP expired or invalid"}, status=status.HTTP_400_BAD_REQUEST)

            # Check if OTP is correct
            if str(cached_otp) != str(otp):
                print("OTP is incorrect")
                return Response({"status": "error", "message": "OTP is incorrect"}, status=status.HTTP_400_BAD_REQUEST)

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
                if user.id == data.get('_auth_user_id'):
                    session.delete()
            print("User sessions invalidated")

            # Log the password hash for verification
            print(f"Password hash: {user.password}")

            # Success response
            return Response({"status": "success", "message": "Password reset successfully"}, status=status.HTTP_200_OK)
        
        except CustomUser.DoesNotExist:
            print("User with this email does not exist")
            return Response({"status": "error", "message": "User with this email does not exist"}, status=status.HTTP_404_NOT_FOUND)
        
        except Exception as e:
            # Log unexpected errors
            print("An unexpected error occurred during password reset:", str(e))
            return Response({
                "status": "error",
                "message": "An unexpected error occurred. Please try again later."
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class CustomUserViewSet(UserViewSet):
    """
    Sets a password for a user without one
    """
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
    """
    Returns the user profile based on the roles.
    """
    serializer_class = UserProfileSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user

        if getattr(self, 'swagger_fake_view', False) or user.is_anonymous:
            return UserProfile.objects.none() # Return empty queryset for schema generation or anonymous users

        if hasattr(user, 'userprofile'): # Check if userprofile exists before accessing it
            if user.userprofile.is_admin():
                # Admins can see everything
                return UserProfile.objects.all()

            elif user.userprofile.is_presenter():
                # Presenters can only see their own profile
                return UserProfile.objects.filter(user=user)

            elif user.userprofile.is_coach():
                # Coaches can see assigned users
                assigned_users = UserAssignment.objects.filter(coach=user).values_list('presenter', flat=True)
                return UserProfile.objects.filter(user_id__in=assigned_users)

        # Default: Return an empty queryset for non-admin, non-presenter, non-coach and users without userprofile
        return UserProfile.objects.none()


class UpdateProfileView(APIView):
    """
    Allows users to update their profiles (UserProfile), including profile picture, fully or partially.
    """
    permission_classes = [permissions.IsAuthenticated]

    def put(self, request, *args, **kwargs):
        user = request.user
        profile = user.userprofile  # Assuming a OneToOne relationship

        # Use UpdateProfileSerializer for UserProfile
        profile_serializer = UpdateProfileSerializer(profile, data=request.data, partial=True)

        if profile_serializer.is_valid(): # Only validate profile_serializer
            profile_serializer.save() # Only save profile_serializer
            return Response({
                "status": "success",
                "message": "Profile updated successfully.",
                "data": {
                    "profile": profile_serializer.data # Return only profile data as we are only updating profile now in this endpoint.
                }
            }, status=status.HTTP_200_OK)

        return Response({
            "status": "fail",
            "message": "Profile update failed.",
            "errors": {
                "profile": profile_serializer.errors # Only return profile serializer errors
            }
        }, status=status.HTTP_400_BAD_REQUEST)
    

class ChangePasswordView(APIView):
    """
    Change password of the user making the request
    """
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
    """
    Google login using OAUTH2
    """
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
        

class UserAssignmentViewSet(viewsets.ModelViewSet):
    queryset = UserAssignment.objects.select_related('admin', 'user').all()
    serializer_class = UserAssignmentSerializer  
    permission_classes = [IsAuthenticated]

    @action(detail=False, methods=['post'])
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

        admin_email = request.data.get('admin_email')
        user_email = request.data.get('user_email')

        if not admin_email or not user_email:
            print("Missing admin_email or user_email in request.")
            return Response({'error': 'Both admin_email and user_email are required.'}, status=status.HTTP_400_BAD_REQUEST)

        # Validate the admin
        admin = get_object_or_404(CustomUser, email=admin_email)
        print(f"Admin found: {admin.email} (ID: {admin.id})")
        admin_profile = getattr(admin, 'userprofile', None)
        if not admin_profile or admin_profile.role != 'admin':
            print(f"Invalid admin role for email: {admin.email}")
            return Response({'error': 'Invalid admin email or user is not an admin.'}, status=status.HTTP_400_BAD_REQUEST)

        # Validate the user
        user = get_object_or_404(CustomUser, email=user_email)
        print(f"User found: {user.email} (ID: {user.id})")
        user_profile = getattr(user, 'userprofile', None)
        if not user_profile or user_profile.role != 'user':
            print(f"Invalid user role for email: {user.email}")
            return Response({'error': 'Invalid user email or user is not a normal user.'}, status=status.HTTP_400_BAD_REQUEST)

        # Prevent self-assignment
        if admin == user:
            print("Attempted to assign a user to themselves.")
            return Response({'error': 'A user cannot be assigned to themselves.'}, status=status.HTTP_400_BAD_REQUEST)

        # Create assignment
        assignment, created = UserAssignment.objects.get_or_create(admin=admin, user=user)
        if created:
            print(f"New assignment created: {admin.email} -> {user.email}")
            return Response(
                {'message': 'User assigned successfully.', 'assignment': UserAssignmentSerializer(assignment).data},
                status=status.HTTP_201_CREATED
            )
        print(f"User {user.email} is already assigned to Admin {admin.email}.")
        return Response({'message': 'User already assigned.'}, status=status.HTTP_200_OK)

    @action(detail=False, methods=['get'])
    def admin_users(self, request):
        """
        Retrieve all admins and their assigned users.
        Returns a dictionary where keys are admin emails and values are lists of user emails.
        """
        print("Fetching all admin-user assignments.")
        assignments = UserAssignment.objects.select_related('admin', 'user').all()
        data = {}
        for assignment in assignments:
            admin_email = assignment.admin.email
            user_email = assignment.user.email
            data.setdefault(admin_email, []).append(user_email)
        print(f"Returning {len(assignments)} assignments.")
        return Response(data, status=status.HTTP_200_OK)

    @action(detail=False, methods=['get'])
    def user_admin(self, request):
        """
        Retrieve the admin for a specific user.
        Expects a query parameter: ?user_email=user@example.com
        """
        user_email = request.query_params.get('user_email')
        if not user_email:
            print("Missing user_email in request.")
            return Response({'error': 'User email is required.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            assignment = UserAssignment.objects.select_related('admin').get(user__email=user_email)
            print(f"Admin found for user {user_email}: {assignment.admin.email}")
            return Response({'admin_email': assignment.admin.email}, status=status.HTTP_200_OK)
        except UserAssignment.DoesNotExist:
            print(f"No admin assigned for user: {user_email}")
            return Response({'error': 'No admin assigned for this user.'}, status=status.HTTP_404_NOT_FOUND)
