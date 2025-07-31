# import csv
# import io
# import logging
# import random
# import string
# from openpyxl import load_workbook
# from django.conf import settings
# from django.core.exceptions import ValidationError
# from django.db import transaction, IntegrityError
# from django.template.loader import render_to_string
# from django.utils import timezone
# from rest_framework import status, viewsets
# from rest_framework.decorators import action
# from rest_framework.permissions import IsAdminUser
# from rest_framework.response import Response
# from rest_framework.parsers import MultiPartParser, JSONParser
# from django.contrib.auth import get_user_model
# from django.contrib.auth.tokens import default_token_generator
# from django.utils.encoding import force_bytes
# from django.utils.http import urlsafe_base64_encode

# from users.utils.email import send_email_via_ses
# from .models import Enterprise, EnterpriseUser, EnterpriseQuestion
# from .serializers import (
#     EnterpriseSerializer,
#     EnterpriseUserSerializer,
#     BulkUserUploadSerializer,
#     EnterpriseQuestionSerializer
# )

# # Get the logger for this file
# logger = logging.getLogger(__name__)

# User = get_user_model()
# logger = logging.getLogger(__name__)

# class EnterpriseViewSet(viewsets.ModelViewSet):
#     """
#     ViewSet for managing enterprises.
#     """
#     queryset = Enterprise.objects.all()
#     serializer_class = EnterpriseSerializer
#     permission_classes = [IsAdminUser]
#     parser_classes = [MultiPartParser, JSONParser]


# class EnterpriseQuestionViewSet(viewsets.ModelViewSet):
#     """
#     ViewSet for managing enterprise questions.
#     """
#     serializer_class = EnterpriseQuestionSerializer
#     permission_classes = [IsAdminUser]
    
#     def get_queryset(self):
#         queryset = EnterpriseQuestion.objects.select_related('enterprise')
        
#         # Filter by enterprise if specified
#         enterprise_id = self.request.query_params.get('enterprise_id')
#         if enterprise_id:
#             queryset = queryset.filter(enterprise_id=enterprise_id)
            
#         # Filter by vertical if specified
#         vertical = self.request.query_params.get('vertical')
#         if vertical:
#             queryset = queryset.filter(vertical=vertical)
            
#         # Filter by active status if specified
#         is_active = self.request.query_params.get('is_active')
#         if is_active is not None:
#             is_active = is_active.lower() in ('true', '1', 't')
#             queryset = queryset.filter(is_active=is_active)
            
#         return queryset
    
#     def perform_create(self, serializer):
#         """Set the enterprise and validate vertical."""
#         enterprise = serializer.validated_data['enterprise']
#         vertical = serializer.validated_data['vertical']
        
#         # Validate that the vertical is allowed for this enterprise
#         available_verticals = [v[0] for v in enterprise.get_available_verticals()]
#         if vertical not in available_verticals:
#             raise ValidationError({
#                 'vertical': f"Vertical '{vertical}' is not available for this enterprise type"
#             })
            
#         serializer.save()

import csv
import io
import logging
import random
import string
import uuid

import boto3
import openai
from openpyxl import load_workbook
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction, IntegrityError
from django.template.loader import render_to_string
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, JSONParser
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from users.utils.email import send_email_via_ses
from .models import Enterprise, EnterpriseUser, EnterpriseQuestion
from .serializers import (
    EnterpriseSerializer,
    EnterpriseUserSerializer,
    BulkUserUploadSerializer,
    EnterpriseQuestionSerializer
)

# Configure logger
logger = logging.getLogger(__name__)

# Initialize OpenAI and S3 client
openai.api_key = settings.OPENAI_API_KEY
s3_client = boto3.client(
    's3',
    aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
    aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
    region_name=getattr(settings, 'AWS_REGION', None)
)

User = get_user_model()

class EnterpriseViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing enterprises.
    """
    queryset = Enterprise.objects.all()
    serializer_class = EnterpriseSerializer
    permission_classes = [IsAdminUser]
    parser_classes = [MultiPartParser, JSONParser]

import logging
import os
import uuid
import boto3
import openai
from django.conf import settings

logger = logging.getLogger(__name__)

# Configure OpenAI client
openai.api_key = settings.OPENAI_API_KEY

class EnterpriseQuestionViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing enterprise questions and pre-generating TTS audio.
    """
    serializer_class = EnterpriseQuestionSerializer
    permission_classes = [IsAdminUser]
    
    @action(detail=False, methods=['get'])
    def test_endpoint(self, request):
        print("=== TEST ENDPOINT HIT ===")
        return Response({
            'status': 'success',
            'message': 'Test endpoint is working!',
            'available_verticals': [
                {'value': 'media_training', 'label': 'Media Training'},
                {'value': 'coach', 'label': 'Coach'},
                {'value': 'gm', 'label': 'General Manager'}
            ]
        })

    def get_queryset(self):
        queryset = EnterpriseQuestion.objects.select_related('enterprise')
        enterprise_id = self.request.query_params.get('enterprise_id')
        if enterprise_id:
            queryset = queryset.filter(enterprise_id=enterprise_id)
        vertical = self.request.query_params.get('vertical')
        if vertical:
            queryset = queryset.filter(vertical=vertical)
        is_active = self.request.query_params.get('is_active')
        if is_active is not None:
            is_active = is_active.lower() in ('true', '1', 't')
            queryset = queryset.filter(is_active=is_active)
        return queryset

    def perform_create(self, serializer):
        """
        Save the question, generate TTS audio, upload to S3, and store the URL.
        The model's clean() method will handle validation.
        """
        print("=== PROCESSING ENTERPRISE QUESTION ===")
        
        try:
            # Save the question instance - this will trigger the model's clean() method
            question = serializer.save()
            print(f"[SUCCESS] Saved question ID: {question.id}")
        except Exception as e:
            print(f"[ERROR] Failed to save question: {str(e)}")
            traceback.print_exc()
            raise

        # Generate TTS audio
        try:
            print(f"[AUDIO] Generating TTS for question: {question.id}")
            print(f"[AUDIO] Question text: {question.question_text}")
            
            # Use OpenAI to generate speech
            audio_resp = openai.audio.speech.create(
                model="tts-1",
                voice=getattr(settings, 'TTS_VOICE', 'alloy'),
                input=question.question_text
            )
            
            # Get the audio content
            audio_bytes = audio_resp.content
            print(f"[AUDIO] Successfully generated TTS")
            
        except Exception as e:
            print(f"[ERROR] Failed to generate TTS: {str(e)}")
            traceback.print_exc()
            question.is_active = False
            question.save(update_fields=['is_active'])
            return

        # Upload to S3 under the 'enterprise-questions' folder
        try:
            print("[S3] Starting S3 upload...")
            
            # Initialize S3 client with credentials from settings
            s3_client = boto3.client(
                's3',
                aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
                aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
                region_name=settings.AWS_S3_REGION_NAME
            )
            
            # Generate unique file name
            file_name = f"{question.id}-{uuid.uuid4().hex}.mp3"
            key = f"enterprise-questions/{question.enterprise.id}/{file_name}"
            
            # Upload to S3 without ACL (bucket has ACLs disabled)
            s3_client.put_object(
                Bucket=settings.AWS_STORAGE_BUCKET_NAME,
                Key=key,
                Body=audio_bytes,
                ContentType='audio/mpeg'
            )
            
            # Construct the public URL
            region = settings.AWS_S3_REGION_NAME
            audio_url = f"https://{settings.AWS_STORAGE_BUCKET_NAME}.s3.{region}.amazonaws.com/{key}"
            print(f"[S3] File uploaded successfully: {audio_url}")

            # Update question with audio URL and save
            question.audio_url = audio_url
            question.save(update_fields=['audio_url'])
            question.save(update_fields=['audio_url'])
        except Exception as e:
            logger.error(f"Error uploading audio for question {question.id} to S3: {e}", exc_info=True)


class EnterpriseUserViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing enterprise users.
    """
    serializer_class = EnterpriseUserSerializer
    permission_classes = [IsAdminUser]
    
    def get_queryset(self):
        return EnterpriseUser.objects.select_related('user', 'enterprise')
    
    @action(detail=False, methods=['post'], url_path='bulk-upload')
    def bulk_upload(self, request):
        """
        Handle bulk user upload via CSV/Excel file.
        Returns detailed information about each user creation attempt.
        """
        # Validate the request data
        serializer = BulkUserUploadSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {
                    'success': False,
                    'message': 'Invalid request data',
                    'errors': serializer.errors,
                    'results': []
                },
                status=status.HTTP_400_BAD_REQUEST
            )
        
        enterprise = serializer.validated_data['enterprise_id']
        file = serializer.validated_data['file']
        send_invitation = serializer.validated_data['send_invitation']
        
        try:
            # Process the uploaded file
            try:
                if file.name.lower().endswith('.csv'):
                    users = self._process_csv(file, enterprise)
                elif file.name.lower().endswith(('.xls', '.xlsx')):
                    users = self._process_excel(file, enterprise)
                else:
                    logger.error("Unsupported file format for bulk upload")
                    return Response(
                        {
                            'success': False,
                            'message': 'Unsupported file format. Please upload a CSV or Excel file.',
                            'results': []
                        },
                        status=status.HTTP_400_BAD_REQUEST
                    )
            except Exception as e:
                logger.error(f"File processing error: {str(e)}", exc_info=True)
                return Response(
                    {
                        'success': False,
                        'message': f'Error processing file: {str(e)}',
                        'results': []
                    },
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            if not users:
                return Response(
                    {
                        'success': False,
                        'message': 'No valid user data found in the uploaded file',
                        'results': []
                    },
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Process each user
            results = []
            success_count = 0
            
            for index, user_data in enumerate(users, start=1):
                email = user_data.get('email', '').strip().lower()
                result = {
                    'row': index,
                    'email': email,
                    'success': False,
                    'message': '',
                    'user_id': None
                }
                
                try:
                    if not email:
                        raise ValueError('Email is required')
                        
                    user = self._create_enterprise_user(user_data, enterprise, send_invitation)
                    result.update({
                        'success': True,
                        'message': 'User created/updated successfully',
                        'user_id': user.id
                    })
                    success_count += 1
                    
                except Exception as e:
                    error_message = str(e)
                    logger.error(f"Error creating user {email}: {error_message}", exc_info=True)
                    result['message'] = f'Error: {error_message}'
                
                results.append(result)
            
            # Prepare the response
            response_data = {
                'success': success_count > 0,
                'message': (
                    f'Processed {len(users)} users. '
                    f'Success: {success_count}, Failed: {len(users) - success_count}'
                ),
                'total': len(users),
                'success_count': success_count,
                'failure_count': len(users) - success_count,
                'results': results
            }
            
            status_code = status.HTTP_201_CREATED if success_count > 0 else status.HTTP_400_BAD_REQUEST
            return Response(response_data, status=status_code)
            
        except Exception as e:
            logger.error(f"Unexpected error in bulk upload: {str(e)}", exc_info=True)
            return Response(
                {
                    'success': False,
                    'message': f'An unexpected error occurred: {str(e)}',
                    'results': []
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    def _process_csv(self, file, enterprise):
        """Process CSV file and return list of user data."""
        try:
            content = file.read().decode('utf-8')
            io_string = io.StringIO(content)
            reader = csv.DictReader(io_string)
            return list(reader)
        except Exception as e:
            raise Exception(f"Error processing CSV file: {str(e)}")
    
    def _process_excel(self, file, enterprise):
        """Process Excel file and return list of user data."""
        try:
            wb = load_workbook(filename=file, read_only=True)
            ws = wb.active
            
            # Get headers from first row
            headers = [cell.value for cell in next(ws.rows)]
            
            # Convert rows to list of dicts
            users = []
            for row in ws.iter_rows(min_row=2, values_only=True):  # Skip header
                if not any(row):  # Skip empty rows
                    continue
                user_data = {headers[i].lower(): row[i] for i in range(len(headers))}
                users.append(user_data)
                
            return users
        except Exception as e:
            raise Exception(f"Error processing Excel file: {str(e)}")
    
    def _create_enterprise_user(self, user_data, enterprise, send_invitation=True):
        """
        Create or update an enterprise user from user data.
        
        Args:
            user_data (dict): Dictionary containing user data
            enterprise (Enterprise): The enterprise to associate the user with
            send_invitation (bool): Whether to send an invitation email
            
        Returns:
            User: The created or updated user object
            
        Raises:
            ValueError: If required data is missing or invalid
            ValidationError: If data validation fails
            Exception: For other unexpected errors
        """
        try:
            # Validate required fields
            email = user_data.get('email', '').strip().lower()
            if not email:
                raise ValueError("Email address is required")
                
            if not isinstance(email, str) or '@' not in email:
                raise ValueError("A valid email address is required")
            
            # Get and validate user_type first
            user_type = str(user_data.get('user_type', 'general')).lower()
            
            # Map to valid user types if needed
            if 'rookie' in user_type:
                user_type = 'rookie'
                user_type_value = User.UserType.ROOKIE_ENTERPRISE
            else:
                # Default to general enterprise if invalid or 'general' specified
                user_type = 'general'
                user_type_value = User.UserType.GENERAL_ENTERPRISE
            
            # Validate name fields
            first_name = str(user_data.get('first_name', '')).strip()
            last_name = str(user_data.get('last_name', '')).strip()
            
            # Check if user already exists
            user = User.objects.filter(email=email).first()
            created = False
            
            with transaction.atomic():
                if not user:
                    # Create new user with random password and enterprise type
                    try:
                        password = self._generate_random_password()
                        user = User.objects.create_user(
                            email=email,
                            password=password,  # This will be hashed by create_user
                            first_name=first_name,
                            last_name=last_name,
                            user_type=user_type_value,  # Set the correct enterprise user type
                            sso_login_enabled=True,     # Enable SSO for enterprise users
                            is_active=True
                        )
                        # Don't set unusable password - we want them to use the generated one
                        created = True
                        logger.info(f"Created new user {email} with generated password")
                    except IntegrityError as e:
                        if 'unique constraint' in str(e).lower():
                            raise ValueError(f"A user with email {email} already exists")
                        raise
                    except Exception as e:
                        logger.error(f"Failed to create user {email}: {str(e)}", exc_info=True)
                        raise ValueError(f"Failed to create user: {str(e)}")
                else:
                    # Update existing user's name and type if provided
                    update_fields = []
                    if first_name and first_name != user.first_name:
                        user.first_name = first_name
                        update_fields.append('first_name')
                    if last_name and last_name != user.last_name:
                        user.last_name = last_name
                        update_fields.append('last_name')
                    
                    # Always ensure the user_type and sso_login_enabled are set correctly for enterprise users
                    if user.user_type != user_type_value or not user.sso_login_enabled:
                        if user.user_type != user_type_value:
                            user.user_type = user_type_value
                            update_fields.append('user_type')
                        if not user.sso_login_enabled:
                            user.sso_login_enabled = True
                            update_fields.append('sso_login_enabled')
                            logger.info(f"Enabled SSO for user {user.email}")
                        
                    if update_fields:
                        try:
                            user.save(update_fields=update_fields)
                            if 'user_type' in update_fields:
                                logger.info(f"Updated existing user {user.email} type to {user_type_value}")
                        except Exception as e:
                            raise ValueError(f"Failed to update user: {str(e)}")
                
                # Get and validate user_type
                user_type = str(user_data.get('user_type', 'general')).lower()
                
                # Update the user's type and SSO settings to match the enterprise type
                needs_save = False
                update_fields = []
                
                if user.user_type != user_type_value:
                    user.user_type = user_type_value
                    update_fields.append('user_type')
                    needs_save = True
                    
                if not user.sso_login_enabled:
                    user.sso_login_enabled = True
                    update_fields.append('sso_login_enabled')
                    needs_save = True
                    logger.info(f"Enabled SSO for user {user.email}")
                
                if needs_save:
                    user.save(update_fields=update_fields)
                    logger.info(f"Updated user {user.email} type to {user_type_value}")
                
                # Validate admin flag
                try:
                    is_admin = str(user_data.get('is_admin', '')).lower() == 'true'
                except (ValueError, AttributeError):
                    is_admin = False
                
                # Create or update enterprise user
                try:
                    enterprise_user, eu_created = EnterpriseUser.objects.update_or_create(
                        user=user,
                        enterprise=enterprise,
                        defaults={
                            'user_type': user_type,
                            'is_admin': is_admin,
                            'department': str(user_data.get('department', ''))[:100],  # Limit to 100 chars
                            'position': str(user_data.get('position', ''))[:100]      # Limit to 100 chars
                        }
                    )
                except IntegrityError as e:
                    raise ValueError(f"Failed to associate user with enterprise: {str(e)}")
                except Exception as e:
                    raise ValueError(f"Error updating enterprise user: {str(e)}")
                
                # Send invitation if requested
                if send_invitation and created:
                    try:
                        # Only send password in email for newly created users
                        self._send_invitation_email(user, enterprise, password if created else None)
                    except Exception as e:
                        # Log the error but don't fail the whole operation
                        logger.error(f"Failed to send invitation email to {email}: {str(e)}", exc_info=True)
                        raise ValueError(f"User created but failed to send invitation: {str(e)}")
                
                return user
                
        except Exception as e:
            # Log the full error for debugging
            logger.error(f"Error in _create_enterprise_user for {email}: {str(e)}", exc_info=True)
            # Re-raise with a user-friendly message
            if not isinstance(e, (ValueError, ValidationError)):
                raise ValueError(f"An error occurred while processing user {email}: {str(e)}")
            raise
    
    def _generate_random_password(self, length=12):
        """Generate a random password."""
        chars = string.ascii_letters + string.digits + '!@#$%^&*()'
        return ''.join(random.choice(chars) for _ in range(length))
        
    def _send_invitation_email(self, user, enterprise, password=None):
        """
        Send invitation email to the user using AWS SES.
        
        Args:
            user: The user to send the invitation to
            enterprise: The enterprise the user is being invited to
            password: The user's plaintext password (only for new users)
            
        Raises:
            Exception: If email sending fails
        """
        try:
            # Log AWS configuration for debugging
            logger.debug(f"Sending invitation email to {user.email}")
            logger.debug(f"AWS Configuration - Region: {getattr(settings, 'AWS_SES_REGION', 'Not set')}")
            logger.debug(f"From Email: {getattr(settings, 'DEFAULT_FROM_EMAIL', 'Not set')}")
            logger.debug(f"Frontend Domain: {getattr(settings, 'FRONTEND_DOMAIN', 'Not set')}")
            
            # Prepare email context
            login_url = f"{settings.FRONTEND_DOMAIN}/login"
            context = {
                'user': user,
                'enterprise': enterprise,
                'login_url': login_url,
                'has_password': password is not None,
                'password': password,
                'protocol': 'https' if getattr(settings, 'USE_HTTPS', False) else 'http'
            }
            
            # Log context for debugging
            logger.debug(f"Email context: {context}")
            
            # Render email content
            if password:
                subject = f"Your {enterprise.name} account has been created"
            else:
                subject = f"Welcome to {enterprise.name}"
            try:
                text_content = render_to_string('emails/enterprise_invitation.txt', context)
                logger.debug("Successfully rendered email template")
            except Exception as e:
                logger.error(f"Failed to render email template: {str(e)}")
                raise Exception(f"Failed to render email template: {str(e)}")
            
            # Log email details
            logger.debug(f"Sending email with subject: {subject}")
            logger.debug(f"To: {user.email}")
            logger.debug(f"From: {settings.DEFAULT_FROM_EMAIL}")
            
            # Send email using AWS SES
            try:
                response = send_email_via_ses(
                    subject=subject,
                    body=text_content,
                    to_emails=[user.email],
                    from_email=settings.DEFAULT_FROM_EMAIL
                )
                
                if not response:
                    error_msg = "send_email_via_ses returned None"
                    logger.error(error_msg)
                    raise Exception(error_msg)
                    
                logger.info(f"Invitation email sent to {user.email}")
                logger.debug(f"SES Response: {response}")
                
            except Exception as e:
                error_msg = f"Error in send_email_via_ses: {str(e)}"
                logger.error(error_msg, exc_info=True)
                raise Exception(error_msg) from e
            
        except Exception as e:
            error_msg = f"Failed to send invitation email to {user.email}: {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise Exception(error_msg)
