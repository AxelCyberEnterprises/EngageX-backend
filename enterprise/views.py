import csv
import io
import logging
import random
import string
import uuid
import openai
from openpyxl import load_workbook
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction, IntegrityError, models
from django.db.models import Count, Sum, F, Q
import os
import boto3
import traceback
from datetime import datetime, timedelta

from rest_framework import viewsets, status
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser, JSONParser
from django.conf import settings

# Import the utility function for voice selection
from .utils import get_voice_for_question
from django.contrib.auth import get_user_model
from users.utils.email import send_email_via_ses
from .models import Enterprise, EnterpriseUser, EnterpriseQuestion, TrainingGoal
from .serializers import (
    EnterpriseSerializer,
    EnterpriseUserSerializer,
    BulkUserUploadSerializer,
    EnterpriseQuestionSerializer,
    TrainingGoalSerializer,
    TrainingGoalOptionsSerializer
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
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, JSONParser]
    
    def get_permissions(self):
        """
        Instantiates and returns the list of permissions that this view requires.
        - Superusers and staff users can perform all actions
        - Regular users can only perform safe actions (GET, HEAD, OPTIONS)
        """
        # Allow all actions for superusers and staff users
        if self.request.user.is_superuser or self.request.user.is_staff or self.request.user.is_admin:
            return [IsAuthenticated()]
            
        # For regular users, only allow safe methods
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            self.permission_classes = [IsAdminUser]
        else:
            self.permission_classes = [IsAuthenticated]
        return super().get_permissions()
    
    @action(detail=True, methods=['get'], url_path='overview-stats')
    def overview_stats(self, request, pk=None):
        """
        Get overview statistics for an enterprise.
        Returns:
            Response: JSON containing statistics like total members, credits left, etc.
        """
        enterprise = self.get_object()
        
        # Get total members
        total_members = enterprise.users.count()
        
        # Get credits left (placeholder - implement your credit logic)
        # This assumes you have a Credit model with a 'balance' field
        try:
            from payments.models import Credit
            credits_left = Credit.objects.filter(
                enterprise=enterprise
            ).aggregate(total=Sum('balance'))['total'] or 0
        except ImportError:
            credits_left = 0
        
        # Check if coaching is activated
        one_on_one_coaching_activated = bool(enterprise.one_on_one_coaching_link)
        
        # Calculate goals completion percent (placeholder)
        # This assumes you have a TrainingGoal model with 'target' and 'completed' fields
        try:
            from .models import TrainingGoal
            goals = TrainingGoal.objects.filter(enterprise=enterprise)
            total_goals = goals.count()
            if total_goals > 0:
                completed_goals = goals.filter(completed=True).count()
                goals_completion_percent = int((completed_goals / total_goals) * 100)
            else:
                goals_completion_percent = 0
        except (ImportError, AttributeError):
            goals_completion_percent = 0
        
        return Response({
            'total_members': total_members,
            'credits_left': credits_left,
            'one_on_one_coaching_activated': one_on_one_coaching_activated,
            'goals_completion_percent': goals_completion_percent
        })
        
    @action(detail=True, methods=['get'], url_path='credits/summary')
    def credits_summary(self, request, pk=None):
        """
        Get credits summary for the enterprise.
        Returns total credits and usage statistics.
        """
        enterprise = self.get_object()
        
        try:
            from payments.models import Credit, CreditTransaction
            
            # Get current balance
            credits = Credit.objects.filter(enterprise=enterprise).first()
            if not credits:
                return Response({
                    'total_credits': 0,
                    'credits_used': 0,
                    'credits_remaining': 0,
                    'transactions': []
                })
            
            # Get recent transactions
            transactions = CreditTransaction.objects.filter(
                enterprise=enterprise
            ).order_by('-created_at')[:10]  # Last 10 transactions
            
            transaction_data = [{
                'id': t.id,
                'amount': t.amount,
                'transaction_type': t.get_transaction_type_display(),
                'description': t.description,
                'created_at': t.created_at
            } for t in transactions]
            
            return Response({
                'total_credits': credits.total_credits,
                'credits_used': credits.credits_used,
                'credits_remaining': credits.balance,
                'transactions': transaction_data
            })
            
        except ImportError:
            return Response(
                {'error': 'Credits module not found'},
                status=status.HTTP_501_NOT_IMPLEMENTED
            )
    
    @action(detail=True, methods=['post'], url_path='credits/add')
    def add_credits(self, request, pk=None):
        """
        Add credits to the enterprise.
        Expected payload:
        {
            "amount": 100,
            "reason": "Monthly subscription"
        }
        """
        enterprise = self.get_object()
        amount = request.data.get('amount')
        reason = request.data.get('reason', 'Admin credit addition')
        
        if not amount or not isinstance(amount, (int, float)) or amount <= 0:
            return Response(
                {'error': 'A positive amount is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
            
        try:
            from payments.models import Credit, CreditTransaction
            
            with transaction.atomic():
                # Get or create credit record
                credit, created = Credit.objects.get_or_create(
                    enterprise=enterprise,
                    defaults={'total_credits': 0, 'credits_used': 0}
                )
                
                # Update credit balance
                credit.total_credits += amount
                credit.save()
                
                # Record transaction
                CreditTransaction.objects.create(
                    enterprise=enterprise,
                    amount=amount,
                    transaction_type='add_to_enterprise',
                    description=reason,
                    created_by=request.user
                )
                
                return Response({
                    'message': f'Successfully added {amount} credits',
                    'new_balance': credit.balance
                })
                
        except ImportError:
            return Response(
                {'error': 'Credits module not found'},
                status=status.HTTP_501_NOT_IMPLEMENTED
            )
    
    @action(detail=True, methods=['get', 'put'], url_path='coaching-settings')
    def coaching_settings(self, request, pk=None):
        """
        GET: Get current coaching settings
        PUT: Update coaching settings
        Expected payload for PUT:
        {
            "one_on_one_coaching_link": "https://calendly.com/..."
        }
        """
        enterprise = self.get_object()
        
        if request.method == 'GET':
            return Response({
                'one_on_one_coaching_link': enterprise.one_on_one_coaching_link,
                'one_on_one_coaching_activated': bool(enterprise.one_on_one_coaching_link)
            })
            
        elif request.method == 'PUT':
            one_on_one_coaching_link = request.data.get('one_on_one_coaching_link')
            
            if not one_on_one_coaching_link:
                return Response(
                    {'error': 'one_on_one_coaching_link is required'},
                    status=status.HTTP_400_BAD_REQUEST
                )
                
            enterprise.one_on_one_coaching_link = one_on_one_coaching_link
            enterprise.save()
            
            return Response({
                'message': 'Coaching settings updated successfully',
                'one_on_one_coaching_link': enterprise.one_on_one_coaching_link,
                'one_on_one_coaching_activated': True
            })
    
    @action(detail=False, methods=['get'])
    def verticals(self, request):
        """
        List all available verticals across all enterprise types.
        Frontend can filter based on enterprise_type if needed.
        """
        from .models import Enterprise
        
        verticals = []
        for choice in Enterprise.Vertical.choices:
            verticals.append({
                'id': choice[0],
                'name': choice[1],
                'enterprise_types': [
                    'sport' if choice[0] in ['media_training', 'coach', 'gm'] else 'general'
                ]
            })
        
        return Response(verticals)
    
    @action(detail=True, methods=['get'], url_path='verticals')
    def enterprise_verticals(self, request, pk=None):
        """
        Get currently enabled verticals for this enterprise.
        Returns list of vertical IDs from Enterprise.accessible_verticals.
        """
        enterprise = self.get_object()
        
        try:
            from payments.models import Credit, CreditTransaction
            
            # Get current balance
            credits = Credit.objects.filter(enterprise=enterprise).first()
            if not credits:
                return Response({
                    'total_credits': 0,
                    'credits_used': 0,
                    'credits_remaining': 0,
                    'transactions': []
                })
            
            # Get recent transactions
            transactions = CreditTransaction.objects.filter(
                enterprise=enterprise
            ).order_by('-created_at')[:10]  # Get 10 most recent transactions
            
            transaction_data = [{
                'id': str(t.id),
                'amount': float(t.amount),
                'type': t.get_transaction_type_display(),
                'description': t.description,
                'date': t.created_at.isoformat(),
                'balance_after': float(t.balance_after)
            } for t in transactions]
            
            return Response({
                'total_credits': credits.total_credits,
                'credits_used': credits.credits_used,
                'credits_remaining': credits.balance,
                'transactions': transaction_data
            })
            
        except ImportError:
            return Response(
                {'error': 'Credits module not found'},
                status=status.HTTP_501_NOT_IMPLEMENTED
            )
    
    @action(detail=True, methods=['post'], url_path='credits/add')
    def add_credits(self, request, pk=None):
        """
        Add credits to the enterprise.
        Expected payload:
        {
            "amount": 100,
            "reason": "Monthly subscription"
        }
        """
        enterprise = self.get_object()
        amount = request.data.get('amount')
        reason = request.data.get('reason', 'Admin credit addition')
        
        if not amount or not isinstance(amount, (int, float)) or amount <= 0:
            return Response(
                {'error': 'A positive amount is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
            
        try:
            from payments.models import Credit, CreditTransaction
            
            with transaction.atomic():
                # Get or create credit record
                credit, created = Credit.objects.get_or_create(
                    enterprise=enterprise,
                    defaults={'total_credits': 0, 'credits_used': 0}
                )
                
                # Update credit balance
                credit.total_credits += amount
                credit.save()
                
                # Record transaction
                CreditTransaction.objects.create(
                    enterprise=enterprise,
                    amount=amount,
                    transaction_type='add_to_enterprise',
                    description=reason,
                    created_by=request.user
                )
                
                return Response({
                    'message': f'Successfully added {amount} credits',
                    'new_balance': credit.balance
                })
                
        except ImportError:
            return Response(
                {'error': 'Credits module not found'},
                status=status.HTTP_501_NOT_IMPLEMENTED
            )
        
    @action(detail=True, methods=['get', 'put'], url_path='coaching-settings')
    def coaching_settings(self, request, pk=None):
        """
        GET: Get current coaching settings
        PUT: Update coaching settings
        Expected payload for PUT:
        {
            "one_on_one_coaching_link": "https://calendly.com/..."
        }
        """
        enterprise = self.get_object()
        
        if request.method == 'GET':
            return Response({
                'one_on_one_coaching_link': enterprise.one_on_one_coaching_link,
                'one_on_one_coaching_activated': bool(enterprise.one_on_one_coaching_link)
            })
            
        elif request.method == 'PUT':
            one_on_one_coaching_link = request.data.get('one_on_one_coaching_link')
            
            if not one_on_one_coaching_link:
                return Response(
                    {'error': 'one_on_one_coaching_link is required'},
                    status=status.HTTP_400_BAD_REQUEST
                )
                
            enterprise.one_on_one_coaching_link = one_on_one_coaching_link
            enterprise.save()
            
            return Response({
                'message': 'Coaching settings updated successfully',
                'one_on_one_coaching_link': enterprise.one_on_one_coaching_link,
                'one_on_one_coaching_activated': True
            })
        
    @action(detail=False, methods=['get'])
    def verticals(self, request):
        """
        List all available verticals across all enterprise types.
        Frontend can filter based on enterprise_type if needed.
        """
        from .models import Enterprise
        
        verticals = []
        for choice in Enterprise.Vertical.choices:
            verticals.append({
                'id': choice[0],
                'name': choice[1],
                'enterprise_types': [
                    'sport' if choice[0] in ['media_training', 'coach', 'gm'] else 'general'
                ]
            })
        
        return Response(verticals)
        
    @action(detail=True, methods=['get'], url_path='verticals')
    def enterprise_verticals(self, request, pk=None):
        """
        Get currently enabled verticals for this enterprise.
        Returns list of vertical IDs from Enterprise.accessible_verticals.
        """
        enterprise = self.get_object()
        return Response({
            'enterprise_id': enterprise.id,
            'accessible_verticals': enterprise.accessible_verticals or []
        })
        
    @enterprise_verticals.mapping.put
    def update_enterprise_verticals(self, request, pk=None):
        """
        Update accessible verticals for this enterprise.
        Expected payload:
        {
            "vertical_ids": ["media_training", "coach"]
        }
        """
        enterprise = self.get_object()
        vertical_ids = request.data.get('vertical_ids', [])
        
        if not isinstance(vertical_ids, list):
            return Response(
                {'error': 'vertical_ids must be a list'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Validate verticals against available choices
        valid_verticals = [choice[0] for choice in Enterprise.Vertical.choices]
        invalid_verticals = [v for v in vertical_ids if v not in valid_verticals]
        
        if invalid_verticals:
            return Response(
                {'error': f'Invalid verticals: {", ".join(invalid_verticals)}'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Update enterprise verticals
        enterprise.accessible_verticals = vertical_ids
        enterprise.save()
        
        return Response({
            'message': 'Accessible verticals updated successfully',
            'enterprise_id': enterprise.id,
            'accessible_verticals': enterprise.accessible_verticals
        })

    @action(detail=True, methods=['put'])
    def update_verticals(self, request, pk=None):
        """
        Update the list of accessible verticals for an enterprise.
        Payload: {"vertical_ids": ["coach", "gm"]}
        """
        enterprise = self.get_object()
        vertical_ids = request.data.get('vertical_ids', [])
        
        if not isinstance(vertical_ids, list):
            return Response(
                {'error': 'vertical_ids must be an array'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Get available verticals for this enterprise type
        available_verticals = enterprise.get_available_verticals()
        
        # Validate all provided verticals are allowed
        invalid_verticals = [v for v in vertical_ids if v not in available_verticals]
        if invalid_verticals:
            return Response(
                {'error': f'Invalid verticals: {", ".join(invalid_verticals)}'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Update enterprise verticals
        enterprise.accessible_verticals = vertical_ids
        enterprise.save()
        
        return Response({
            'message': 'Accessible verticals updated successfully',
            'enterprise_id': enterprise.id,
            'accessible_verticals': enterprise.accessible_verticals
        })

    @action(detail=True, methods=['post'], url_path='progress-report/compute')
    def compute_progress_report(self, request, pk=None):
        """
        Compute progress report for an enterprise.
        Returns statistics on user activity, session completion, and goal progress.
        """
        from datetime import datetime, timedelta
        
        enterprise = self.get_object()
        
        try:
            # Get date range (default to last 30 days)
            end_date = datetime.now()
            start_date = end_date - timedelta(days=30)
            
            # Get total users
            total_users = enterprise.users.count()
            
            # Get active users (users with activity in the last 30 days)
            active_users = enterprise.users.filter(
                last_login__gte=start_date
            ).count()
            
            # Get session statistics (placeholder - implement based on your session tracking)
            total_sessions = 0
            completed_sessions = 0
            
            # Get goal progress
            goals = TrainingGoal.objects.filter(enterprise=enterprise, is_active=True)
            goal_progress = [
                {
                    'goal_id': goal.id,
                    'room': goal.room,
                    'room_display': goal.get_room_display(),
                    'target_sessions': goal.target_sessions,
                    'completed_sessions': goal.completed_sessions,
                    'progress_percent': goal.progress_percent,
                    'is_completed': goal.is_completed
                }
                for goal in goals
            ]
            
            # Calculate overall completion percentage
            overall_completion = 0
            if goals.exists():
                overall_completion = sum(g.progress_percent for g in goals) / goals.count()
            
            # Get vertical usage statistics
            vertical_usage = []
            for vertical in enterprise.accessible_verticals:
                vertical_questions = EnterpriseQuestion.objects.filter(
                    enterprise=enterprise,
                    vertical=vertical,
                    is_active=True
                )
                vertical_usage.append({
                    'vertical': vertical,
                    'vertical_display': dict(EnterpriseQuestion.Vertical.choices).get(vertical, vertical),
                    'question_count': vertical_questions.count(),
                    'usage_count': 0  # Placeholder - implement based on your tracking
                })
            
            return Response({
                'enterprise_id': enterprise.id,
                'enterprise_name': enterprise.name,
                'report_period': {
                    'start_date': start_date.isoformat(),
                    'end_date': end_date.isoformat()
                },
                'user_statistics': {
                    'total_users': total_users,
                    'active_users': active_users,
                    'active_percentage': (active_users / total_users * 100) if total_users > 0 else 0
                },
                'session_statistics': {
                    'total_sessions': total_sessions,
                    'completed_sessions': completed_sessions,
                    'completion_rate': (completed_sessions / total_sessions * 100) if total_sessions > 0 else 0
                },
                'goal_progress': goal_progress,
                'overall_completion_percentage': overall_completion,
                'vertical_usage': vertical_usage,
                'generated_at': datetime.now().isoformat()
            })
            
        except Exception as e:
            logger.error(f"Error generating progress report for enterprise {enterprise.id}: {str(e)}", exc_info=True)
            return Response(
                {'error': 'Failed to generate progress report'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=True, methods=['post'], url_path='progress-report/email')
    def email_progress_report(self, request, pk=None):
        """
        Email the progress report to the specified recipients.
        Payload: {"recipients": ["email1@example.com", "email2@example.com"]}
        """
        enterprise = self.get_object()
        recipients = request.data.get('recipients', [])
        
        if not recipients or not isinstance(recipients, list):
            return Response(
                {'error': 'recipients must be a non-empty array of email addresses'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            # Generate the report data
            report_response = self.compute_progress_report(request, pk)
            if report_response.status_code != status.HTTP_200_OK:
                return report_response
                
            report_data = report_response.data
            
            # Format the email content (simplified example)
            subject = f"Progress Report for {enterprise.name} - {datetime.now().strftime('%B %Y')}"
            
            # Create a simple text version of the report
            text_content = f"""
            Progress Report for {enterprise.name}
            Period: {report_data['report_period']['start_date']} to {report_data['report_period']['end_date']}
            
            User Statistics:
            - Total Users: {report_data['user_statistics']['total_users']}
            - Active Users: {report_data['user_statistics']['active_users']} ({report_data['user_statistics']['active_percentage']:.1f}%)
            
            Session Statistics:
            - Total Sessions: {report_data['session_statistics']['total_sessions']}
            - Completed Sessions: {report_data['session_statistics']['completed_sessions']}
            - Completion Rate: {report_data['session_statistics']['completion_rate']:.1f}%
            
            Overall Completion: {report_data['overall_completion_percentage']:.1f}%
            
            This is an automated report. Please contact support if you have any questions.
            """
            
            # Send the email using our SES utility
            try:
                from users.utils.email import send_email_via_ses
                
                email_response = send_email_via_ses(
                    subject=subject,
                    body=text_content.strip(),
                    to_emails=recipients,
                    from_email=settings.DEFAULT_FROM_EMAIL
                )
                
                if not email_response:
                    return Response(
                        {'error': 'Failed to send email'},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR
                    )
                
                return Response({
                    'success': True,
                    'message': f'Progress report sent to {len(recipients)} recipients',
                    'recipients': recipients
                })
                
            except Exception as e:
                logger.error(f"Error sending progress report email: {str(e)}", exc_info=True)
                return Response(
                    {'error': 'Failed to send email'},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )
                
        except Exception as e:
            logger.error(f"Error in email_progress_report: {str(e)}", exc_info=True)
            return Response(
                {'error': 'An error occurred while processing your request'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class EnterpriseQuestionViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing enterprise questions and pre-generating TTS audio.
    - GET operations are allowed for any authenticated user
    - Other operations (POST, PUT, PATCH, DELETE) require admin privileges
    """
    serializer_class = EnterpriseQuestionSerializer
    
    def get_permissions(self):
        """
        Instantiates and returns the list of permissions that this view requires.
        """
        if self.action in ['list', 'retrieve', 'test_endpoint']:
            permission_classes = [IsAuthenticated]
        else:
            permission_classes = [IsAdminUser]
        return [permission() for permission in permission_classes]
    
    @action(detail=False, methods=['get'])
    def question_sections(self, request):
        """
        Get available question sections (grouped by vertical) for an enterprise.
        Returns sections with question counts and active status.
        
        Query Parameters:
            enterprise_id (required): ID of the enterprise
        """
        enterprise_id = request.query_params.get('enterprise_id')
        if not enterprise_id:
            return Response(
                {'error': 'enterprise_id is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
            
        try:
            enterprise = Enterprise.objects.get(pk=enterprise_id)
            
            # Get all questions for the enterprise
            questions = EnterpriseQuestion.objects.filter(enterprise=enterprise)
            
            # Get available verticals for this enterprise
            available_verticals = enterprise.get_available_verticals()
            
            # Group questions by vertical
            verticals_data = []
            for vertical in available_verticals:
                vertical_questions = questions.filter(vertical=vertical)
                active_count = vertical_questions.filter(is_active=True).count()
                
                verticals_data.append({
                    'id': vertical,
                    'name': dict(EnterpriseQuestion.Vertical.choices)[vertical],
                    'total_questions': vertical_questions.count(),
                    'active_questions': active_count,
                    'has_questions': vertical_questions.exists()
                })
            
            return Response(verticals_data)
            
        except Enterprise.DoesNotExist:
            return Response(
                {'error': 'Enterprise not found'},
                status=status.HTTP_404_NOT_FOUND
            )
            
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
            # Get sport_type from request data if provided
            sport_type = None
            if 'sport_type' in self.request.data:
                sport_type = self.request.data['sport_type']
            elif 'enterprise_settings' in self.request.data and isinstance(self.request.data['enterprise_settings'], dict):
                sport_type = self.request.data['enterprise_settings'].get('sport_type')
            
            # Validate sport_type if provided
            if sport_type:
                valid_sport_types = dict(EnterpriseQuestion._meta.get_field('sport_type').choices).keys()
                if sport_type not in valid_sport_types:
                    raise ValidationError({
                        'sport_type': f"Invalid sport_type. Must be one of: {', '.join(valid_sport_types)}"
                    })
                print(f"[DEBUG] Saving with sport_type: {sport_type}")
            
            # Save the question with sport_type if provided
            question = serializer.save(sport_type=sport_type)
            print(f"[SUCCESS] Saved question ID: {question.id}")
        except Exception as e:
            print(f"[ERROR] Failed to save question: {str(e)}")
            traceback.print_exc()
            raise

        # Generate TTS audio
        try:
            print(f"[AUDIO] Generating TTS for question: {question.id}")
            print(f"[AUDIO] Question text: {question.question_text}")
            
            # Determine the appropriate voice based on rookie type and sport type
            # Get the enterprise user making the request (if available)
            request_user = getattr(self.request, 'user', None)
            rookie_type = None
            sport_type = None
            
            # Try to get the rookie type and sport type from the request data
            print(f"[DEBUG] Request data: {self.request.data}")
            
            # Check if enterprise_settings exists in request data
            enterprise_settings = self.request.data.get('enterprise_settings', {})
            print(f"[DEBUG] Enterprise settings from request: {enterprise_settings}")
            
            # Try to get from user's enterprise profile first
            if hasattr(request_user, 'enterprise_profile'):
                rookie_type = getattr(request_user.enterprise_profile, 'rookie_type', None)
                sport_type = getattr(request_user.enterprise_profile, 'sport_type', None)
                print(f"[DEBUG] Got from user profile - rookie_type: {rookie_type}, sport_type: {sport_type}")
            
            # If not found in user profile, try to get from request data
            if not rookie_type:
                # Try to get from enterprise_settings first
                if enterprise_settings and isinstance(enterprise_settings, dict):
                    if not rookie_type:
                        rookie_type = enterprise_settings.get('rookie_type')
                    if not sport_type:
                        sport_type = enterprise_settings.get('sport_type')
                    print(f"[DEBUG] Got from enterprise_settings - rookie_type: {rookie_type}, sport_type: {sport_type}")
                
                # Fall back to top-level request data
                if not rookie_type:
                    rookie_type = self.request.data.get('vertical')  # Using vertical as rookie_type
                if not sport_type:
                    sport_type = self.request.data.get('sport_type')
                print(f"[DEBUG] Got from top-level request - rookie_type: {rookie_type}, sport_type: {sport_type}")
            
            # Log final values before voice selection
            print(f"[DEBUG] Final values - rookie_type: {rookie_type}, sport_type: {sport_type}")
            
            # Get the appropriate voice
            voice = get_voice_for_question(rookie_type, sport_type)
            print(f"[TTS] Using voice: {voice} (rookie_type: {rookie_type}, sport_type: {sport_type})")
            
            # Use OpenAI to generate speech with the selected voice
            audio_resp = openai.audio.speech.create(
                model="tts-1",
                voice=voice,
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
            
            # Upload to S3 
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


class TrainingGoalViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing training goals for enterprises.
    """
    serializer_class = TrainingGoalSerializer
    permission_classes = [IsAdminUser]
    
    def get_queryset(self):
        """Return training goals for the specified enterprise"""
        enterprise_id = self.kwargs.get('enterprise_pk')
        return TrainingGoal.objects.filter(enterprise_id=enterprise_id)
    
    def perform_create(self, serializer):
        """Set the enterprise when creating a new goal"""
        enterprise_id = self.kwargs.get('enterprise_pk')
        enterprise = Enterprise.objects.get(pk=enterprise_id)
        serializer.save(enterprise=enterprise)
    
    @action(detail=False, methods=['get'], url_path='options')
    def goal_options(self, request, enterprise_pk=None):
        """
        Get available room options for training goals based on enterprise type.
        Sport enterprises get different options than general enterprises.
        """
        try:
            enterprise = Enterprise.objects.get(pk=enterprise_pk)
            
            # Define options for each enterprise type
            sport_options = [
                {'id': 'presentation', 'name': 'Presentation'},
                {'id': 'pitch', 'name': 'Pitch'},
                {'id': 'public_speaking', 'name': 'Public Speaking'},
                {'id': 'media_training', 'name': 'Media Training'},
                {'id': 'coach', 'name': 'Coach'},
                {'id': 'general_manager', 'name': 'General Manager'}
            ]
            
            general_options = [
                {'id': 'presentation', 'name': 'Presentation'},
                {'id': 'pitch', 'name': 'Pitch'},
                {'id': 'public_speaking', 'name': 'Public Speaking'},
                {'id': 'media_training', 'name': 'Media Training'},
                {'id': 'coaching', 'name': 'Coaching'}
            ]
            
            options = sport_options if enterprise.enterprise_type == Enterprise.EnterpriseType.SPORT else general_options
            
            # Add enterprise type information
            result = []
            for opt in options:
                result.append({
                    'id': opt['id'],
                    'name': opt['name'],
                    'enterprise_types': ['sport' if opt['id'] in [o['id'] for o in sport_options] else 'general']
                })
            
            serializer = TrainingGoalOptionsSerializer(result, many=True)
            return Response(serializer.data)
            
        except Enterprise.DoesNotExist:
            return Response(
                {'error': 'Enterprise not found'},
                status=status.HTTP_404_NOT_FOUND
            )


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
                    # Create a copy of user_data to avoid modifying the original
                    user_data_copy = user_data.copy()
                    
                    # Explicitly remove department and position fields if they exist
                    user_data_copy.pop('department', None)
                    user_data_copy.pop('position', None)
                    
                    enterprise_user, eu_created = EnterpriseUser.objects.update_or_create(
                        user=user,
                        enterprise=enterprise,
                        defaults={
                            'user_type': user_type,
                            'is_admin': is_admin
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
