from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAdminUser
from django.contrib.auth import get_user_model
from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils import timezone
from datetime import timedelta
import secrets
import string

from .models import Enterprise, EnterpriseUser
from .serializers import EnterpriseUserSerializer

User = get_user_model()

class ManualUserUploadView(APIView):
    """
    API endpoint for manually adding enterprise users through the admin dashboard.
    """
    permission_classes = [IsAdminUser]
    
    def post(self, request, *args, **kwargs):
        data = request.data.copy()
        
        # Validate required fields
        required_fields = ['email', 'enterprise', 'user_type']
        missing_fields = [field for field in required_fields if field not in data]
        if missing_fields:
            return Response(
                {'error': f'Missing required fields: {", ".join(missing_fields)}'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        email = data['email'].strip().lower()
        enterprise_id = data['enterprise']
        user_type = data['user_type'].lower()
        
        # Validate user type
        valid_user_types = dict(EnterpriseUser.UserType.choices).keys()
        if user_type not in valid_user_types:
            return Response(
                {'error': f'Invalid user type. Must be one of: {", ".join(valid_user_types)}'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            # Get or create the user
            user, created = User.objects.get_or_create(
                email=email,
                defaults={
                    'first_name': data.get('first_name', ''),
                    'last_name': data.get('last_name', ''),
                    'is_active': True,
                    'user_type': user_type.upper(),
                    'sso_login_enabled': True  # Enable SSO for enterprise users
                }
            )
            
            # If user already exists, update their details
            if not created:
                user.first_name = data.get('first_name', user.first_name)
                user.last_name = data.get('last_name', user.last_name)
                user.user_type = user_type.upper()
                user.sso_login_enabled = True
                user.save()
            
            # Get the enterprise
            try:
                enterprise = Enterprise.objects.get(id=enterprise_id)
            except Enterprise.DoesNotExist:
                return Response(
                    {'error': 'Enterprise not found'},
                    status=status.HTTP_404_NOT_FOUND
                )
            
            # Create or update enterprise user
            enterprise_user, eu_created = EnterpriseUser.objects.update_or_create(
                user=user,
                enterprise=enterprise,
                defaults={
                    'user_type': user_type,
                    'is_admin': data.get('is_admin', 'false').lower() == 'true',
                    'department': data.get('department', ''),
                    'position': data.get('position', '')
                }
            )
            
            # Send invitation email
            self._send_invitation_email(user, enterprise, created)
            
            # Prepare response
            serializer = EnterpriseUserSerializer(enterprise_user)
            return Response(
                {
                    'message': 'User added successfully',
                    'user_created': created,
                    'enterprise_user': serializer.data
                },
                status=status.HTTP_201_CREATED if created else status.HTTP_200_OK
            )
            
        except Exception as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    def _send_invitation_email(self, user, enterprise, is_new_user=False):
        """Send an invitation email to the user."""
        try:
            # Generate a one-time password reset token
            from django.contrib.auth.tokens import default_token_generator
            from django.utils.encoding import force_bytes
            from django.utils.http import urlsafe_base64_encode
            
            token = default_token_generator.make_token(user)
            uid = urlsafe_base64_encode(force_bytes(user.pk))
            
            context = {
                'user': user,
                'enterprise': enterprise,
                'is_new_user': is_new_user,
                'login_url': f"{settings.FRONTEND_DOMAIN}/auth/set-password/{uid}/{token}/",
                'sso_login_url': f"{settings.FRONTEND_DOMAIN}/auth/sso-login/?email={user.email}",
                'expiry_days': 7,
                'site_name': settings.SITE_NAME,
            }
            
            subject = f"Invitation to join {enterprise.name}" if is_new_user else f"New account created for {enterprise.name}"
            
            # Render both HTML and plain text versions
            message = render_to_string('emails/enterprise_invitation.txt', context)
            html_message = render_to_string('emails/enterprise_invitation.html', context)
            
            # Send the email
            send_mail(
                subject=subject,
                message=message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[user.email],
                html_message=html_message,
                fail_silently=False,
            )
            
            return True
            
        except Exception as e:
            # Log the error but don't fail the request
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Failed to send invitation email to {user.email}: {str(e)}")
            return False
