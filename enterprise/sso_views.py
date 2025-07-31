from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.contrib.auth import get_user_model, login
from django.conf import settings
from django.utils import timezone
from rest_framework.authtoken.models import Token
import secrets

User = get_user_model()

class SSOLoginRequestView(APIView):
    """
    Request an SSO login link via email for enterprise users.
    """
    permission_classes = []  # Allow unauthenticated access
    
    def post(self, request, *args, **kwargs):
        email = request.data.get('email', '').strip().lower()
        print(f"\n=== SSO Login Request ===")
        print(f"Email: {email}")
        
        if not email:
            print("Error: Email is required")
            return Response(
                {'error': 'Email is required'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            print("Looking up user...")
            user = User.objects.get(email=email, is_active=True)
            print(f"Found user: {user.email} (ID: {user.id})")
            
            # Check if this is an enterprise user with SSO enabled
            is_enterprise = user.is_enterprise_user()
            sso_enabled = user.sso_login_enabled
            print(f"Enterprise user: {is_enterprise}")
            print(f"SSO enabled: {sso_enabled}")
            
            if not is_enterprise or not sso_enabled:
                print("User is not an enterprise user or SSO is not enabled")
                # Return a generic success message for security
                return Response(
                    {'message': 'If your email is registered, you will receive a login link'},
                    status=status.HTTP_200_OK
                )
            
            # Send the SSO login email
            print("Sending SSO login email...")
            email_sent = user.send_sso_login_email()
            
            if email_sent:
                print("SSO login email sent successfully")
                return Response(
                    {'message': 'Login link sent to your email'},
                    status=status.HTTP_200_OK
                )
            else:
                print("Failed to send SSO login email")
                return Response(
                    {'error': 'Failed to send login email'},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )
            
        except User.DoesNotExist:
            # Return a generic success message for security
            return Response(
                {'message': 'If your email is registered, you will receive a login link'},
                status=status.HTTP_200_OK
            )
        except Exception as e:
            return Response(
                {'error': 'An error occurred while processing your request'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class SSOLoginVerifyView(APIView):
    """
    Verify the SSO login code and return the token if valid.
    """
    permission_classes = []  # Allow unauthenticated access
    
    def post(self, request, *args, **kwargs):
        email = request.data.get('email', '').strip().lower()
        code = request.data.get('code', '').strip()
        
        if not email or not code:
            return Response(
                {'error': 'Email and code are required'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            user = User.objects.get(email=email, is_active=True)
            
            # Verify the code
            if not user.verify_sso_code(code):
                return Response(
                    {'error': 'Invalid or expired login code'}, 
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Mark the user as logged in
            user.last_login = timezone.now()
            user.last_login_method = 'email_link'
            if not user.has_logged_in:
                user.has_logged_in = True
            user.save(update_fields=['last_login', 'last_login_method', 'has_logged_in'])
            
            # Get or create a token for the user
            token, created = Token.objects.get_or_create(user=user)
            
            # Clear the used code
            user.sso_login_code = None
            user.sso_code_expires = None
            user.save(update_fields=['sso_login_code', 'sso_code_expires'])
            
            # Log the user in (optional, if you want to maintain session)
            if request:
                login(request, user)
            
            return Response({
                'status': 'success',
                'message': 'Login successful.',
                'data': {
                    'token': token.key,
                    'user_id': user.id,
                    'email': user.email,
                    'first_name': user.first_name,
                    'last_name': user.last_name,
                    'is_admin': user.is_superuser,
                    'first_login': not user.has_logged_in
                }
            })
            
        except User.DoesNotExist:
            return Response(
                {'error': 'Invalid email or code'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        except Exception as e:
            return Response(
                {'error': 'An error occurred while processing your request'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
