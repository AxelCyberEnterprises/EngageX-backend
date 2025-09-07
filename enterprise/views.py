import csv
import io
import logging
import random
import string
from django.template.loader import render_to_string
import uuid
import openai
from openpyxl import load_workbook
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction, IntegrityError, models
from django.utils import timezone
from django.db.models import Q, F, Case, When, Value, IntegerField, Count, Avg, Max, Sum
import os
import boto3
import traceback
import random
from datetime import datetime, timedelta
from django.db.models.functions import Concat

from rest_framework import viewsets, status, filters
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser, JSONParser
from django.conf import settings
from django_filters.rest_framework import DjangoFilterBackend

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
    TrainingGoalOptionsSerializer,
    UserProgressSerializer
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
    
    Search and Filtering:
    - Search: Use ?search=query to search in name, and enterprise_type
    - Filtering: Use ?is_active=true/false, ?enterprise_type=type
    - Ordering: Use ?ordering=field (prefix with - for descending)
    """
    queryset = Enterprise.objects.all()
    serializer_class = EnterpriseSerializer
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, JSONParser]
    filter_backends = [
        DjangoFilterBackend,
        filters.SearchFilter,
        filters.OrderingFilter
    ]
    filterset_fields = {
        'is_active': ['exact'],
        'enterprise_type': ['exact'],
        'created_at': ['gte', 'lte', 'exact', 'gt', 'lt'],
    }
    search_fields = ['name', 'enterprise_type']
    ordering_fields = ['name', 'created_at', 'updated_at']
    ordering = ['name']
    
    def get_queryset(self):
        """
        Return a queryset of enterprises based on user permissions:
        - Superusers and staff see all enterprises
        - Enterprise admins see their enterprise
        - Regular users see enterprises they belong to
        """
        queryset = super().get_queryset()
        user = self.request.user
        
        # Superusers and staff can see all enterprises
        if user.is_superuser or user.is_staff:
            return queryset
            
        try:
            # For enterprise admins and regular users, only show their enterprise
            if hasattr(user, 'enterprise_profile'):
                return queryset.filter(id=user.enterprise_profile.enterprise_id)
                
        except EnterpriseUser.DoesNotExist:
            pass
            
        # Default: no access if no enterprise profile exists
        return queryset.none()  # Default ordering
    
    def get_permissions(self):
        """
        Instantiates and returns the list of permissions that this view requires.
        - Superusers and staff users can perform all actions
        - Enterprise admins can perform all actions
        - Regular users can only perform safe actions (GET, HEAD, OPTIONS)
        """
        # Check if user is a superuser or staff
        if self.request.user.is_superuser or self.request.user.is_staff:
            return [IsAuthenticated()]
            
        # Check if user is an enterprise admin through their EnterpriseUser profile
        try:
            if hasattr(self.request.user, 'enterprise_profile') and self.request.user.enterprise_profile.is_admin:
                return [IsAuthenticated()]
        except EnterpriseUser.DoesNotExist:
            pass
            
        # For regular users, only allow safe methods
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            self.permission_classes = [IsAdminUser]
        else:
            self.permission_classes = [IsAuthenticated]
        return super().get_permissions()
        
    def update(self, request, *args, **kwargs):
        """
        Handle PATCH requests for updating enterprise data.
        Supports updating all fields including is_active, logo, favicon, etc.
        """
        instance = self.get_object()
        data = request.data.copy()
        
        # Handle file uploads if present
        if 'logo' in request.FILES:
            instance.logo = request.FILES['logo']
        if 'favicon' in request.FILES:
            instance.favicon = request.FILES['favicon']
        
        # Save the instance to handle file uploads
        if request.FILES:
            instance.save()
        
        # Remove file fields from data as they're already handled
        for field in ['logo', 'favicon']:
            data.pop(field, None)
        
        # Use the serializer for all fields including is_active
        serializer = self.get_serializer(instance, data=data, partial=True)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        
        # Get the updated instance with all fields
        updated_instance = self.get_queryset().get(pk=instance.pk)
        return Response(EnterpriseSerializer(updated_instance, context={'request': request}).data)
        
    @action(detail=False, methods=['get'])
    def dashboard_analytics(self, request, enterprise_id=None):
        """
        Get analytics data for the admin dashboard.
        
        When enterprise_id is provided in query params, returns data for that specific enterprise.
        When no enterprise_id is provided (admin only), returns data for the entire application.
        
        Query Parameters:
            time_range: str - Time range for the data (day, week, month, year, all)
            start_date: str - Start date in YYYY-MM-DD format (overrides time_range if provided)
            end_date: str - End date in YYYY-MM-DD format (overrides time_range if provided)
            enterprise_id: int - Optional enterprise ID to filter by
        
        Returns:
            Response: JSON containing:
            - sessions: Session counts over time by vertical
            - user_growth: User growth metrics
            - user_status: Active/inactive user counts
            - scope: 'enterprise' or 'application' indicating the data scope
        """
        from django.db.models.functions import TruncDate, TruncWeek, TruncMonth, TruncYear
        from django.db.models import Count, Q, F, Case, When, IntegerField
        from django.utils.timezone import now, make_aware
        from datetime import timedelta
        import datetime as dt
        from rest_framework import status
        
        # Get enterprise ID from method parameter or query params
        enterprise_id = enterprise_id or request.query_params.get('enterprise_id')
        if enterprise_id:
            try:
                from .models import Enterprise
                enterprise = Enterprise.objects.get(pk=enterprise_id)
                user_filter = Q(user__enterprise_profile__enterprise=enterprise)
                sessions_filter = Q(user__enterprise_profile__enterprise=enterprise)
                scope = 'enterprise'
            except Enterprise.DoesNotExist:
                return Response(
                    {"detail": "Enterprise not found"},
                    status=status.HTTP_404_NOT_FOUND
                )
        else:
            # Check admin access for application-wide analytics
            if not (request.user.is_staff or request.user.is_superuser):
                return Response(
                    {"detail": "Administrator privileges required for application-wide analytics."},
                    status=status.HTTP_403_FORBIDDEN
                )
            user_filter = Q()
            sessions_filter = Q()
            scope = 'application'
        
        # Get time range parameters
        time_range = request.query_params.get('time_range', 'month').lower()
        start_date_str = request.query_params.get('start_date')
        end_date_str = request.query_params.get('end_date')
        
        # Set date range
        end_date = now().date()
        if end_date_str:
            end_date = make_aware(dt.datetime.strptime(end_date_str, '%Y-%m-%d')).date()
        
        if start_date_str:
            start_date = make_aware(dt.datetime.strptime(start_date_str, '%Y-%m-%d')).date()
        else:
            # Set default start date based on time_range
            if time_range == 'day':
                start_date = end_date
            elif time_range == 'week':
                start_date = end_date - timedelta(days=7)
            elif time_range == 'month':
                start_date = end_date - timedelta(days=30)
            elif time_range == 'year':
                start_date = end_date - timedelta(days=365)
            else:  # all time
                start_date = None
        
        # Get practice sessions based on scope
        from practice_sessions.models import PracticeSession, EnterpriseSpecialtySession
        
        sessions_qs = PracticeSession.objects.filter(
            user__enterprise_profile__isnull=False,  # Only users with enterprise profiles
            date__isnull=False
        )
        
        if enterprise_id:
            sessions_qs = sessions_qs.filter(user_filter)
        
        if start_date:
            sessions_qs = sessions_qs.filter(date__date__gte=start_date)
        if end_date:
            sessions_qs = sessions_qs.filter(date__date__lte=end_date)
        
        # Get session counts by vertical over time
        sessions_by_vertical = sessions_qs.annotate(
            vertical=Case(
                When(session_type='enterprise', then=F('enterprise_settings__rookie_type')),
                default='session_type',
                output_field=models.CharField()
            )
        ).values('vertical').annotate(
            count=Count('id')
        ).order_by('-count')
        
        # Get sessions over time (grouped by time period)
        trunc_map = {
            'day': TruncDate('date'),
            'week': TruncWeek('date'),
            'month': TruncMonth('date'),
            'year': TruncYear('date')
        }
        
        trunc_func = trunc_map.get(time_range, TruncMonth('date'))
        
        sessions_over_time = sessions_qs.annotate(
            period=trunc_func,
            vertical=Case(
                When(session_type='enterprise', then=F('enterprise_settings__rookie_type')),
                default='session_type',
                output_field=models.CharField()
            )
        ).values('period', 'vertical').annotate(
            count=Count('id')
        ).order_by('period')
        
        # Format sessions data for the response
        verticals = set(item['vertical'] for item in sessions_by_vertical if item['vertical'])
        sessions_data = {
            'by_vertical': list(sessions_by_vertical),
            'over_time': list(sessions_over_time),
            'verticals': list(verticals)
        }
        
        # Get user growth data based on scope
        users_qs = User.objects.filter(enterprise_profile__isnull=False)  # Only enterprise users
        if enterprise_id:
            users_qs = users_qs.filter(user_filter)
        
        if start_date:
            users_created = users_qs.filter(date_joined__date__gte=start_date)
        else:
            users_created = users_qs
        
        user_growth = users_created.annotate(
            period=TruncDate('date_joined')
        ).values('period').annotate(
            count=Count('id')
        ).order_by('period')
        
        # Get active/inactive users
        # A user is considered active if their is_active flag is True
        active_users = users_qs.filter(is_active=True).count()
        total_users = users_qs.count()
        inactive_users = total_users - active_users
        
        user_status = {
            'total': total_users,
            'active': active_users,
            'inactive': inactive_users
        }
        
        response_data = {
            'sessions': sessions_data,
            'user_growth': list(user_growth),
            'user_status': user_status,
            'scope': scope,
            'time_range': {
                'start_date': start_date.isoformat() if start_date else None,
                'end_date': end_date.isoformat()
            }
        }
        
        # Add enterprise info if scope is specific to an enterprise
        if enterprise_id:
            response_data['enterprise'] = {
                'id': enterprise.id,
                'name': enterprise.name
            }
            
        return Response(response_data)
    
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
            credit = Credit.objects.filter(enterprise=enterprise).first()
            credits_left = float(credit.balance) if credit else 0
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
                # Calculate completion based on completed_sessions vs target_sessions
                total_completed = sum(goal.completed_sessions for goal in goals)
                total_target = sum(goal.target_sessions for goal in goals)
                if total_target > 0:
                    goals_completion_percent = int((total_completed / total_target) * 100)
                else:
                    goals_completion_percent = 0
            else:
                goals_completion_percent = 0
        except (ImportError, AttributeError):
            goals_completion_percent = 0
        
        return Response({
            'total_members': total_members,
            'credits_left': credits_left,
            'goals_completion_percent': goals_completion_percent,
            'coaching_sessions_booked': enterprise.coaching_sessions_booked
        })
        
    @action(detail=True, methods=['post'], url_path='book-coaching-session')
    def book_coaching_session(self, request, pk=None):
        """
        Increment the coaching session counter for the enterprise.
        This endpoint is called when a user books a coaching session through the one-on-one coaching link.
        """
        enterprise = self.get_object()
        
        # Check if coaching is enabled for this enterprise
        if not enterprise.one_on_one_coaching_link:
            return Response(
                {'error': 'One-on-one coaching is not enabled for this enterprise'},
                status=status.HTTP_400_BAD_REQUEST
            )
            
        # Increment the counter atomically to handle concurrent requests
        Enterprise.objects.filter(pk=enterprise.pk).update(
            coaching_sessions_booked=models.F('coaching_sessions_booked') + 1
        )
        
        # Refresh the enterprise object to get the updated counter
        enterprise.refresh_from_db()
        
        return Response({
            'message': 'Coaching session booked successfully',
            'coaching_sessions_booked': enterprise.coaching_sessions_booked
        }, status=status.HTTP_200_OK)
        
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
        # Initialize variables to avoid reference errors in exception handling
        enterprise = None
        amount = None
        
        try:
            from payments.models import Credit, CreditTransaction
            
            # Get request data
            amount = request.data.get('amount')
            reason = request.data.get('reason', 'Admin credit addition')
            
            # Input validation
            if not amount or not isinstance(amount, (int, float)) or amount <= 0:
                return Response(
                    {'error': 'A positive amount is required'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Get enterprise object
            enterprise = self.get_object()
            
            with transaction.atomic():
                # Get or create credit record
                credit, created = Credit.objects.get_or_create(
                    enterprise=enterprise,
                    defaults={'total_credits': 0, 'credits_used': 0}
                )
                
                # Update credit balance
                credit.total_credits += amount
                credit.save()
                
                # Create transaction record
                transaction_data = {
                    'enterprise': enterprise,
                    'transaction_type': 'add',
                    'amount': amount,
                    'description': reason,
                    'reference_id': f"credit_add_{enterprise.id}_{timezone.now().timestamp()}"
                }
                
                # Add user if authenticated
                if request.user.is_authenticated:
                    transaction_data['user'] = request.user
                
                # Create transaction
                credit_transaction = CreditTransaction.objects.create(**transaction_data)
                
                # Prepare success response
                response_data = {
                    'message': f'Successfully added {amount} credits to enterprise {enterprise.name}',
                    'enterprise_id': enterprise.id,
                    'enterprise_name': enterprise.name,
                    'amount_added': amount,
                    'new_balance': float(credit.balance),  # Convert Decimal to float for JSON serialization
                    'transaction_id': credit_transaction.id,
                    'timestamp': timezone.now().isoformat()
                }
                
                return Response(response_data, status=status.HTTP_200_OK)
                
        except Exception as e:
            error_msg = str(e)
            enterprise_id = enterprise.id if enterprise and hasattr(enterprise, 'id') else 'unknown'
            logger.error(f"Error adding {amount or 'unknown'} credits to enterprise {enterprise_id}: {error_msg}")
            return Response(
                {'error': f'Failed to add credits: {error_msg}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
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
        Get currently enabled and available verticals for this enterprise.
        Returns:
        {
            'enterprise_id': str,
            'accessible_verticals': List[str],  # Currently enabled verticals
            'available_verticals': [           # All available verticals for this enterprise
                {'value': str, 'label': str},
                ...
            ]
        }
        """
        enterprise = self.get_object()
        available_verticals = enterprise.get_available_verticals()
        
        # Convert available_verticals to list of dicts with value and label
        available_verticals_list = []
        for item in available_verticals:
            if isinstance(item, (list, tuple)) and len(item) == 2:
                # Handle (value, label) tuples
                code, label = item
                available_verticals_list.append({'value': str(code), 'label': str(label)})
            else:
                # Handle string values - use the string as both value and label
                code = str(item)
                # Get the display label from Vertical choices if possible
                label = dict(enterprise.Vertical.choices).get(code, code.replace('_', ' ').title())
                available_verticals_list.append({'value': code, 'label': label})
        
        return Response({
            'enterprise_id': str(enterprise.id),
            'accessible_verticals': enterprise.accessible_verticals or [],
            'available_verticals': available_verticals_list
        })
        
    @enterprise_verticals.mapping.put
    def update_enterprise_verticals(self, request, pk=None):
        """
        Update accessible verticals for this enterprise.
        
        Expected payload (either format is accepted):
        {
            "vertical_ids": ["media_training", "coach"]
            // or
            "accessible_verticals": ["media_training", "coach"]
        }
        
        Returns:
        {
            'message': str,
            'enterprise_id': str,
            'accessible_verticals': List[str],  # Updated list of enabled verticals
            'available_verticals': [           # All available verticals for this enterprise
                {'value': str, 'label': str},
                ...
            ]
        }
        """
        enterprise = self.get_object()
        # Accept both vertical_ids and accessible_verticals for backward compatibility
        vertical_ids = request.data.get('vertical_ids', request.data.get('accessible_verticals', []))
        
        if not isinstance(vertical_ids, list):
            return Response(
                {'error': 'vertical_ids must be a list'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Get available verticals for this enterprise type
        available_vertical_codes = enterprise.get_available_verticals()
        
        # Validate all provided verticals are allowed
        invalid_verticals = [v for v in vertical_ids if v not in available_vertical_codes]
        if invalid_verticals:
            return Response(
                {'error': f'Invalid verticals: {", ".join(invalid_verticals)}'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Update enterprise verticals using the model method for proper validation
        try:
            enterprise.set_accessible_verticals(vertical_ids)
            enterprise.save()
        except ValueError as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Format available verticals for response using the Vertical choices from the model
        available_verticals_list = [
            {'value': code, 'label': label}
            for code, label in Enterprise.Vertical.choices
            if code in available_vertical_codes
        ]
        
        return Response({
            'message': 'Accessible verticals updated successfully',
            'enterprise_id': str(enterprise.id),
            'accessible_verticals': enterprise.accessible_verticals or [],
            'available_verticals': available_verticals_list
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
                user__last_login__gte=start_date
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
        Email the progress report to the specified recipients with an optional PDF attachment.
        
        Payload: {
            "recipients": ["email1@example.com", "email2@example.com"],
            "pdf_file": <binary PDF data>  # Optional PDF file to attach
        }
        """
        from django.utils import timezone
        from datetime import datetime, timedelta
        from io import BytesIO
        
        enterprise = self.get_object()
        recipients = request.data.get('recipients', [])
        pdf_file = request.FILES.get('pdf_file')
        
        # Log the raw request data for debugging
        logger.debug(f"Request data: {request.data}")
        logger.debug(f"Request FILES: {request.FILES}")
        
        # Handle case where recipients is a JSON string or contains square brackets
        if isinstance(recipients, str):
            try:
                import json
                import re
                
                # Clean up the string by removing any extra whitespace and newlines
                recipients = recipients.strip()
                
                # If it's a JSON array, parse it
                if recipients.startswith('[') and recipients.endswith(']'):
                    recipients = json.loads(recipients)
                else:
                    # Clean up the string and split by commas
                    recipients = re.split(r'[,\n\r]+', recipients)
                    # Clean up each email
                    recipients = [email.strip(" []'\"") for email in recipients if email.strip()]
                
                logger.debug(f"Parsed recipients: {recipients}")
            except Exception as e:
                logger.error(f"Error parsing recipients: {str(e)}")
                return Response(
                    {'error': 'Invalid recipients format. Please provide a valid JSON array or comma-separated list of email addresses'},
                    status=status.HTTP_400_BAD_REQUEST
                )
        
        logger.debug(f"Final recipients: {recipients}")
        logger.debug(f"Recipients type: {type(recipients)}")
        
        # Ensure recipients is a list and not empty
        if not isinstance(recipients, list) or not recipients:
            return Response(
                {'error': 'recipients must be a non-empty array of email addresses'},
                status=status.HTTP_400_BAD_REQUEST
            )
            
        # Clean up email addresses
        recipients = [email.strip() for email in recipients if email and isinstance(email, str)]
        
        if not recipients:
            return Response(
                {'error': 'No valid email addresses provided after cleaning'},
                status=status.HTTP_400_BAD_REQUEST
            )
            
        # Clean and validate email addresses
        from django.core.validators import validate_email
        from django.core.exceptions import ValidationError
        
        valid_emails = []
        invalid_emails = []
        for email in recipients:
            try:
                validate_email(email)
                valid_emails.append(email)
            except ValidationError as e:
                logger.warning(f"Invalid email address: {email} - {str(e)}")
                invalid_emails.append(email)
                continue
        
        logger.debug(f"Valid emails: {valid_emails}")
        logger.debug(f"Invalid emails: {invalid_emails}")
                
        if not valid_emails:
            return Response(
                {'error': 'No valid email addresses provided'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            # Calculate date range for the report (last 30 days by default)
            end_date = timezone.now().date()
            start_date = end_date - timedelta(days=30)
            
            # Get the progress data using the progress_data endpoint
            from enterprise.serializers import UserProgressSerializer
            from enterprise.models import EnterpriseUser
            
            # Get all users for this enterprise
            users_queryset = EnterpriseUser.objects.filter(enterprise=enterprise)\
                .select_related('user')\
                .order_by('user__last_name')
            
            # Calculate statistics
            total_users = users_queryset.count()
            active_users = users_queryset.filter(
                user__last_login__date__gte=start_date
            ).count()
            
            # Get completion data
            total_completion = 0
            for user in users_queryset:
                user_progress = UserProgressSerializer(user).data
                total_completion += user_progress.get('overall_goal_completion', 0)
            
            avg_completion = (total_completion / total_users) if total_users > 0 else 0
            
            # Create email subject
            subject = f"EngageX Progress Report - {datetime.now().strftime('%Y-%m')}"
            
            # Create HTML email content
            html_content = f"""
            <html>
                <body>
                    <h2>EngageX Progress Report</h2>
                    <p><strong>Enterprise:</strong> {enterprise.name}</p>
                    <p><strong>Report Period:</strong> {start_date} to {end_date}</p>
                    
                    <h3>User Statistics</h3>
                    <p>Total Users: {total_users}</p>
                    <p>Active Users (30 days): {active_users} ({(active_users/total_users*100 if total_users > 0 else 0):.1f}%)</p>
                    
                    <h3>Progress</h3>
                    <p>Avg. Completion: {avg_completion:.1f}%</p>
                    
                    <p><em>This is an automated report. Contact support for assistance.</em></p>
                </body>
            </html>
            """
            
            # Create a plain text version as fallback
            text_content = f"""
            EngageX Progress Report
            ======================
            
            Enterprise: {enterprise.name}
            Report Period: {start_date} to {end_date}
            
            USER STATISTICS
            --------------
            Total Users: {total_users}
            Active Users (30 days): {active_users} ({(active_users/total_users*100 if total_users > 0 else 0):.1f}%)
            
            PROGRESS
            --------
            Avg. Completion: {avg_completion:.1f}%
            
            This is an automated report. Contact support for assistance.
            """
            
            # Log the content for debugging
            print(f"Sending email with content:\n{text_content}")
            
            # Prepare attachments if PDF is provided
            attachments = []
            if pdf_file:
                attachments.append({
                    'filename': f'progress_report_{enterprise.name.replace(" ", "_")}_{datetime.now().strftime("%Y%m%d")}.pdf',
                    'content': pdf_file
                })
            
            # Send the email using our SES utility
            try:
                from users.utils.email import send_email_via_ses
                
                email_response = send_email_via_ses(
                    subject=subject,
                    body=text_content.strip(),
                    to_emails=recipients,
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    html_body=html_content,
                    attachments=attachments
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
    queryset = EnterpriseQuestion.objects.select_related('enterprise').all()
    serializer_class = EnterpriseQuestionSerializer

    def get_permissions(self):
        """
        Instantiates and returns the list of permissions that this view requires.
        """
        if self.action in ['list', 'retrieve', 'test_endpoint', 'get_queryset']:
            permission_classes = [IsAuthenticated]
        elif self.action in ['create', 'update', 'partial_update', 'destroy']:
            permission_classes = [IsAdminUser]
        else:
            # Default to a safe permission for any other action
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
            
            questions = EnterpriseQuestion.objects.filter(enterprise=enterprise)
            
            available_verticals = enterprise.get_available_verticals()
            
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

    def get_queryset(self):
        queryset = EnterpriseQuestion.objects.select_related('enterprise')
        enterprise_id = self.request.query_params.get('enterprise_id')
        if enterprise_id:
            queryset = queryset.filter(enterprise_id=enterprise_id)
            
        vertical = self.request.query_params.get('vertical')
        if vertical:
            queryset = queryset.filter(vertical=vertical)
            
            # For media training, handle gender filtering
            if vertical.lower() == 'media_training':
                gender = self.request.query_params.get('gender')
                if gender:
                    queryset = queryset.filter(gender=gender)
                # If no gender specified, include all questions (for backward compatibility)
                    
        sport_type = self.request.query_params.get('sport_type')
        if sport_type:
            queryset = queryset.filter(sport_type=sport_type)
            
        is_active = self.request.query_params.get('is_active')
        if is_active is not None:
            is_active = is_active.lower() in ('true', '1', 't')
            queryset = queryset.filter(is_active=is_active)
            
        return queryset

    def _generate_and_upload_tts(self, question):
        """
        Helper method to generate TTS audio, upload to S3, and save the URL.
        """
        try:
            print(f"[AUDIO] Generating TTS for question: {question.id}")
            print(f"[AUDIO] Question text: {question.question_text}")
            
            request_user = getattr(self.request, 'user', None)
            rookie_type = None
            sport_type = None
            
            enterprise_settings = self.request.data.get('enterprise_settings', {})
            
            if hasattr(request_user, 'enterprise_profile'):
                rookie_type = getattr(request_user.enterprise_profile, 'rookie_type', None)
                sport_type = getattr(request_user.enterprise_profile, 'sport_type', None)
            
            if not rookie_type:
                if enterprise_settings and isinstance(enterprise_settings, dict):
                    rookie_type = enterprise_settings.get('rookie_type')
                    sport_type = enterprise_settings.get('sport_type')
            
            if not rookie_type:
                rookie_type = self.request.data.get('vertical')
            if not sport_type:
                sport_type = self.request.data.get('sport_type') 

            if question.vertical.lower() == 'coaching':
                voice = 'sage'  # Female voice
            elif question.vertical.lower() == 'media_training':        # For media training, use gender to determine voice
             
                # Ensure gender is set (should be handled by model's clean method)
                if not question.gender or question.gender == 'N':
                    # If we still don't have a valid gender, log a warning and use random
                    print(f"[WARNING] No valid gender set for media training question {question.id}, using random")
                    question.gender = random.choice(['M', 'F'])
                    # Save and refresh the question to ensure we have the latest data
                    question.save(update_fields=['gender'])
                    question.refresh_from_db()
                
                # Now use the set gender
                if question.gender == 'M':
                    voice = 'onyx'  # Male voice
                elif question.gender == 'F':
                    voice = 'sage'  # Female voice
                else:
                    # This should not happen due to above check, but just in case
                    voice = 'echo'
                    print(f"[WARNING] Unexpected gender value: {question.gender}")
                    
                print(f"[TTS] Using voice: {voice} (media training, gender: {question.gender})")
                print(f"[DEBUG] Question ID {question.id} - Final gender before TTS: {question.gender}")
            else:
                # Use existing logic for non-media training questions
                voice = get_voice_for_question(rookie_type, sport_type)
                print(f"[TTS] Using voice: {voice} (rookie_type: {rookie_type}, sport_type: {sport_type})")
            
            audio_resp = openai.audio.speech.create(
                model="tts-1",
                voice=voice,
                input=question.question_text
            )
            
            audio_bytes = audio_resp.content
            print(f"[AUDIO] Successfully generated TTS")
            
            s3_client = boto3.client(
                's3',
                aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
                aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
                region_name=settings.AWS_S3_REGION_NAME
            )
            
            file_name = f"{question.id}-{uuid.uuid4().hex}.mp3"
            key = f"enterprise-questions/{question.enterprise.id}/{file_name}"
            
            s3_client.put_object(
                Bucket=settings.AWS_STORAGE_BUCKET_NAME,
                Key=key,
                Body=audio_bytes,
                ContentType='audio/mpeg'
            )
            
            region = settings.AWS_S3_REGION_NAME
            audio_url = f"https://{settings.AWS_STORAGE_BUCKET_NAME}.s3.{region}.amazonaws.com/{key}"
            
            # Delete old audio file if it exists
            if question.audio_url:
                old_key = question.audio_url.split('.com/')[1]
                try:
                    s3_client.delete_object(Bucket=settings.AWS_STORAGE_BUCKET_NAME, Key=old_key)
                    print(f"Deleted old audio file: {old_key}")
                except Exception as e:
                    print(f"Failed to delete old audio file {old_key}: {e}")
            
            question.audio_url = audio_url
            question.save(update_fields=['audio_url'])
            
            return True, None
            
        except Exception as e:
            logger.error(f"Error generating or uploading audio for question {question.id}: {e}", exc_info=True)
            question.is_active = False
            question.save(update_fields=['is_active'])
            return False, str(e)

    def perform_create(self, serializer):
        """
        Save the question and generate TTS audio.
        """
        print("=== PROCESSING ENTERPRISE QUESTION (CREATE) ===")
        
        # Save the question first
        question = serializer.save()
        
        # Ensure the question is saved and refreshed from the database
        # This ensures any model-level defaults or clean() methods have been applied
        question.refresh_from_db()
        
        # Generate and upload TTS
        success, error = self._generate_and_upload_tts(question)
        
        # If TTS generation fails, update the question to be inactive
        if not success:
            question.is_active = False
            question.save(update_fields=['is_active'])
            raise ValidationError({'detail': f'Failed to generate audio: {error}'})

    def perform_update(self, serializer):
        """
        Update the question and re-generate TTS audio if the question text or gender changes.
        """
        print("=== PROCESSING ENTERPRISE QUESTION (UPDATE) ===")
        
        # Get the original question before the update
        original_question = self.get_object()
        original_question_text = original_question.question_text
        original_gender = getattr(original_question, 'gender', None)
        is_media_training = original_question.vertical.lower() == 'media_training'
        
        # For media training, ensure we preserve the original gender if not explicitly changed
        if is_media_training and 'gender' not in serializer.validated_data and original_gender:
            serializer.validated_data['gender'] = original_gender
        
        # Save the updated instance
        question = serializer.save()
        
        # Check if we need to regenerate TTS
        should_regenerate = False
        
        # Check if question text changed
        if 'question_text' in serializer.validated_data and \
           serializer.validated_data['question_text'] != original_question_text:
            should_regenerate = True
            
        # For media training, check if gender changed
        if is_media_training and 'gender' in serializer.validated_data and \
           serializer.validated_data['gender'] != original_gender:
            should_regenerate = True
        
        # Regenerate TTS if needed
        if should_regenerate:
            success, error = self._generate_and_upload_tts(question)
            if not success:
                raise ValidationError({'detail': f'Failed to re-generate audio: {error}'})

        # Manually re-serialize the updated instance to ensure the response is correct
        # The default ModelViewSet.update() method would have done this automatically
        # but since perform_update is overridden, it needs to be done here.
        return Response(self.get_serializer(question).data)


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
    - Admins can view and manage all enterprise users
    - Regular users can only view and manage their own enterprise user object
    """
    serializer_class = EnterpriseUserSerializer
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, JSONParser]
    
    def create(self, request, *args, **kwargs):
        """
        Create a single enterprise user.
        
        Required fields:
        - email: User's email address
        - enterprise: ID of the enterprise
        
        Optional fields:
        - first_name: User's first name
        - last_name: User's last name
        - user_type: 'general' (default) or 'rookie'
        - role: User's role in the enterprise
        - team: User's team in the enterprise
        - send_invitation: Whether to send invitation email (default: true)
        """
        try:
            # Get enterprise
            enterprise_id = request.data.get('enterprise')
            if not enterprise_id:
                return Response(
                    {'error': 'Enterprise ID is required'},
                    status=status.HTTP_400_BAD_REQUEST
                )
                
            try:
                enterprise = Enterprise.objects.get(pk=enterprise_id)
            except Enterprise.DoesNotExist:
                return Response(
                    {'error': 'Enterprise not found'},
                    status=status.HTTP_404_NOT_FOUND
                )
            
            # Check permissions - only enterprise admins can create users
            if not request.user.is_staff and not request.user.is_superuser:
                # Check if user is an admin of this enterprise
                try:
                    enterprise_user = EnterpriseUser.objects.get(
                        user=request.user,
                        enterprise=enterprise,
                        is_admin=True
                    )
                except EnterpriseUser.DoesNotExist:
                    return Response(
                        {'error': 'You do not have permission to create users for this enterprise'},
                        status=status.HTTP_403_FORBIDDEN
                    )
            
            # Create user data dict from request
            user_data = {
                'email': request.data.get('email'),
                'first_name': request.data.get('first_name', ''),
                'last_name': request.data.get('last_name', ''),
                'user_type': request.data.get('user_type', 'general'),
                'role': request.data.get('role'),
                'team': request.data.get('team')
            }
            
            # Check if send_invitation is provided, default to True if not
            send_invitation = request.data.get('send_invitation', True)
            # Convert string 'true'/'false' to boolean if needed
            if isinstance(send_invitation, str):
                send_invitation = send_invitation.lower() == 'true'
            
            try:
                # Create the user
                user = self._create_enterprise_user(user_data, enterprise, send_invitation)
                
                # Get the created enterprise user
                enterprise_user = EnterpriseUser.objects.get(user=user, enterprise=enterprise)
                
                # Return the created user
                serializer = self.get_serializer(enterprise_user)
                headers = self.get_success_headers(serializer.data)
                return Response(
                    serializer.data,
                    status=status.HTTP_201_CREATED,
                    headers=headers
                )
                
            except ValueError as e:
                return Response(
                    {'error': str(e)},
                    status=status.HTTP_400_BAD_REQUEST
                )
            except Exception as e:
                logger.error(f"Error creating enterprise user: {str(e)}", exc_info=True)
                return Response(
                    {'error': 'An error occurred while creating the user'},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )
                
        except Exception as e:
            logger.error(f"Unexpected error in create enterprise user: {str(e)}", exc_info=True)
            return Response(
                {'error': 'An unexpected error occurred'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    def get_queryset(self):
        queryset = EnterpriseUser.objects.select_related('user', 'enterprise')
        
        # For non-admin users, only return their own enterprise user object
        if not (self.request.user.is_staff or self.request.user.is_superuser):
            return queryset.filter(user=self.request.user)
        
        # For admin users, apply the existing filters
        params = self.request.query_params
        
        # Filter by enterprise_id if provided
        enterprise_id = params.get('enterprise_id')
        if enterprise_id:
            queryset = queryset.filter(enterprise_id=enterprise_id)
            
        # Filter by is_admin if provided
        is_admin = params.get('is_admin')
        if is_admin is not None:
            is_admin = is_admin.lower() in ('true', '1', 't')
            queryset = queryset.filter(is_admin=is_admin)
            
        # Search functionality
        search_query = params.get('search')
        if search_query:
            # Create a Q object to combine multiple search conditions with OR
            search_filter = Q()
            
            # Search in user fields (first_name, last_name, email)
            user_fields = ['first_name', 'last_name', 'email']
            for field in user_fields:
                search_filter |= Q(**{f'user__{field}__icontains': search_query})
                
            # Also search in the combined first_name + last_name
            search_filter |= (Q(user__first_name__icontains=search_query) | 
                            Q(user__last_name__icontains=search_query))
            
            # Search in enterprise user specific fields
            search_filter |= (Q(department__icontains=search_query) |
                            Q(job_title__icontains=search_query) |
                            Q(phone_number__icontains=search_query))
            
            queryset = queryset.filter(search_filter)
        
        # Ordering
        order_by = params.get('order_by', 'user__last_name')
        if order_by.lstrip('-') in ['first_name', 'last_name', 'email', 'date_joined']:
            order_by = f'user__{order_by.lstrip("-")}'
            if order_by.startswith('-'):
                order_by = f'-user__{order_by[1:]}'
            queryset = queryset.order_by(order_by)
        
        return queryset.distinct()
        
    def update(self, request, *args, **kwargs):
        """
        Handle PATCH requests for updating enterprise user branding.
        Supports updating logo, favicon, primary_color, and secondary_color.
        """
        instance = self.get_object()
        
        # Only allow updating branding fields if the user has permission
        if not request.user.is_superuser and not request.user.is_staff:
            # Check if the requesting user is an admin of the same enterprise
            try:
                requesting_eu = EnterpriseUser.objects.get(
                    user=request.user,
                    enterprise=instance.enterprise,
                    is_admin=True
                )
            except EnterpriseUser.DoesNotExist:
                return Response(
                    {'error': 'You do not have permission to update this user'},
                    status=status.HTTP_403_FORBIDDEN
                )
        
        # Handle file uploads
        data = request.data.copy()
        
        # Process logo if included in the request
        if 'logo' in request.FILES:
            instance.logo = request.FILES['logo']
            
        # Process favicon if included in the request
        if 'favicon' in request.FILES:
            instance.favicon = request.FILES['favicon']
        
        # Save the instance to handle file uploads before serialization
        if request.FILES:
            instance.save()
        
        # Remove file fields from data as they're already handled
        data.pop('logo', None)
        data.pop('favicon', None)
        
        # Use the serializer for the rest of the fields
        serializer = self.get_serializer(instance, data=data, partial=True)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        
        return Response(serializer.data)
        
    @action(detail=False, methods=['get'], url_path='progress-data')
    def progress_data(self, request):
        """
        Get progress data for multiple users in the enterprise with advanced filtering and search.
        
        Query Parameters:
        - enterprise_id: Required, filters users by enterprise
        - search: Optional, search term to filter users by name or email (case-insensitive)
        - goal_status: Optional, filter by goal status ('completed', 'in_progress', 'not_started')
        - last_activity_after: Optional, filter users active after this date (YYYY-MM-DD)
        - last_activity_before: Optional, filter users active before this date (YYYY-MM-DD)
        - sort_by: Optional, field to sort by (e.g., 'name', 'email', 'last_login', 'sessions_completed', 'goal_completion')
        - sort_order: Optional, sort order ('asc' or 'desc', default: 'asc')
        - page: Optional, page number for pagination (default: 1)
        - page_size: Optional, number of items per page (default: 20, max: 100)
        - enterprise_user_ids: Comma-separated list of EnterpriseUser IDs to include (overrides other filters)
        """
        
        enterprise_id = request.query_params.get('enterprise_id')
        if not enterprise_id:
            return Response(
                {'error': 'enterprise_id is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
            
        try:
            # Get the enterprise
            enterprise = Enterprise.objects.get(id=enterprise_id)
            print(f"Processing progress data for enterprise: {enterprise.name} (ID: {enterprise.id})")
            
            # Get all active goals for this enterprise
            active_goals = TrainingGoal.objects.filter(enterprise=enterprise, is_active=True)
            
            # Get base queryset for users in this enterprise with related data
            users_queryset = EnterpriseUser.objects.filter(enterprise=enterprise)\
                .select_related('user')\
                .annotate(
                    last_activity=Max('user__last_login'),
                    full_name=Concat('user__first_name', Value(' '), 'user__last_name')
                )\
                .order_by()  # Clear any default ordering
            
            # Apply search filter if provided
            search_query = request.query_params.get('search')
            if search_query:
                users_queryset = users_queryset.filter(
                    Q(user__first_name__icontains=search_query) |
                    Q(user__last_name__icontains=search_query) |
                    Q(user__email__icontains=search_query) |
                    Q(full_name__icontains=search_query)
                )
            
            # Apply date range filters for last activity
            try:
                last_activity_after = request.query_params.get('last_activity_after')
                if last_activity_after:
                    last_activity_after = datetime.strptime(last_activity_after, '%Y-%m-%d').date()
                    users_queryset = users_queryset.filter(user__last_login__date__gte=last_activity_after)
                
                last_activity_before = request.query_params.get('last_activity_before')
                if last_activity_before:
                    last_activity_before = datetime.strptime(last_activity_before, '%Y-%m-%d').date()
                    users_queryset = users_queryset.filter(user__last_login__date__lte=last_activity_before)
            except ValueError:
                return Response(
                    {'error': 'Invalid date format. Use YYYY-MM-DD'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Log all enterprise users for debugging
            all_enterprise_users = list(users_queryset.values_list('id', 'user__email'))
            logger.info(f"All enterprise users in enterprise {enterprise_id}: {all_enterprise_users}")
            
            # Apply sorting
            sort_by = request.query_params.get('sort_by', 'user__last_name')
            sort_order = request.query_params.get('sort_order', 'asc')
            
            # Map sort fields to actual model fields
            sort_mapping = {
                'name': 'full_name',
                'email': 'user__email',
                'last_login': 'last_activity',
                'sessions_completed': 'sessions_completed',
                'goal_completion': 'overall_goal_completion'
            }
            
            sort_field = sort_mapping.get(sort_by, 'user__last_name')
            
            # Handle descending order
            if sort_order.lower() == 'desc':
                sort_field = f'-{sort_field}'
                
            # Apply sorting to the queryset
            users_queryset = users_queryset.order_by(sort_field)
            
            # Filter by enterprise_user_ids if provided (overrides other filters)
            enterprise_user_ids_param = request.query_params.get('enterprise_user_ids')
            if enterprise_user_ids_param:
                try:
                    enterprise_user_ids = [int(euid.strip()) for euid in enterprise_user_ids_param.split(',')]
                    logger.info(f"Filtering for enterprise user IDs: {enterprise_user_ids}")
                    users_queryset = users_queryset.filter(id__in=enterprise_user_ids)
                    logger.info(f"Found {users_queryset.count()} users after filtering")
                except (ValueError, AttributeError) as e:
                    return Response(
                        {'error': f'Invalid enterprise_user_ids parameter: {str(e)}'},
                        status=status.HTTP_400_BAD_REQUEST
                    )
            
            # Apply pagination
            try:
                page = int(request.query_params.get('page', 1))
                page_size = min(int(request.query_params.get('page_size', 20)), 100)  # Max 100 per page
            except (ValueError, TypeError):
                return Response(
                    {'error': 'Invalid page or page_size parameter'},
                    status=status.HTTP_400_BAD_REQUEST
                )
                
            paginator = Paginator(users_queryset, page_size)
            try:
                users_page = paginator.page(page)
            except EmptyPage:
                users_page = paginator.page(paginator.num_pages)
            
            # Serialize paginated user progress data
            serializer = UserProgressSerializer(users_page, many=True)
            
            # Calculate overall statistics for the entire result set (not just current page)
            all_goals = []
            total_sessions = 0
            total_completion = 0
            
            for user_data in users_queryset:
                user_progress = UserProgressSerializer(user_data).data
                total_sessions += user_progress['sessions_completed']
                total_completion += user_progress['overall_goal_completion']
            
            # Calculate goal summaries
            goal_summary = {}
            for goal in all_goals:
                if goal['goal_id'] not in goal_summary:
                    goal_summary[goal['goal_id']] = {
                        'goal_id': goal['goal_id'],
                        'name': goal['name'],
                        'target': goal['target'],
                        'total_completed': 0,
                        'user_count': 0
                    }
                goal_summary[goal['goal_id']]['total_completed'] += goal['completed']
                goal_summary[goal['goal_id']]['user_count'] += 1
            
            # Format goal summaries
            goals_summary = []
            for goal_id, data in goal_summary.items():
                avg_completed = round(data['total_completed'] / data['user_count'], 1) if data['user_count'] > 0 else 0
                goals_summary.append({
                    'goal_id': data['goal_id'],
                    'name': data['name'],
                    'target': data['target'],
                    'average_completed': avg_completed,
                    'progress': round((data['total_completed'] / (data['target'] * data['user_count'])) * 100, 1) if data['user_count'] > 0 else 0
                })
            

            
            # Prepare response data with pagination info
            response_data = {
                'enterprise_id': enterprise.id,
                'enterprise_name': enterprise.name,
                'pagination': {
                    'total_users': paginator.count,
                    'total_pages': paginator.num_pages,
                    'current_page': page,
                    'page_size': page_size,
                    'has_next': users_page.has_next(),
                    'has_previous': users_page.has_previous(),
                    'next_page_number': users_page.next_page_number() if users_page.has_next() else None,
                    'previous_page_number': users_page.previous_page_number() if users_page.has_previous() else None
                },
                'filters': {
                    'search_term': search_query,
                    'date_range': {
                        'start': last_activity_after.strftime('%Y-%m-%d') if 'last_activity_after' in locals() and last_activity_after else None,
                        'end': last_activity_before.strftime('%Y-%m-%d') if 'last_activity_before' in locals() and last_activity_before else None
                    },
                    'sorting': {
                        'sort_by': sort_by,
                        'sort_order': sort_order
                    }
                },
                'total_users': users_queryset.count(),
                'total_sessions': total_sessions,
                'average_goal_completion': round(total_completion / users_queryset.count(), 1) if users_queryset.count() > 0 else 0,
                'goals_summary': goals_summary,
                'users': serializer.data
            }
            
            return Response(response_data)
            
        except Enterprise.DoesNotExist:
            return Response(
                {'error': 'Enterprise not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            logger.error(f"Error in progress_data: {str(e)}")
            return Response(
                {'error': 'An error occurred while processing your request'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
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
                            'is_admin': is_admin,
                            'role': user_data.get('role'),
                            'team': user_data.get('team')
                        }
                    )
                except IntegrityError as e:
                    raise ValueError(f"Failed to associate user with enterprise: {str(e)}")
                except Exception as e:
                    raise ValueError(f"Error updating enterprise user: {str(e)}")
                
                # Send invitation if requested
                if send_invitation:
                    print("Sending invitation email to", user.email)
                    try:
                        # Only send password in email for newly created users
                        self._send_invitation_email(user, enterprise, password if created else None)
                        print("Successfully sent invitation email to", user.email)
                    except Exception as e:
                        # Log the error but don't fail the whole operation
                        print(f"Failed to send invitation email to {user.email}: {str(e)}", exc_info=True)
                        raise ValueError(f"User {'created' if created else 'updated'} but failed to send invitation: {str(e)}")
                
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
        print(f"[EMAIL] Starting to send invitation email to {user.email}")
        
        try:            
            # Prepare email context
            login_url = f"{settings.FRONTEND_DOMAIN}/auth/login"
            context = {
                'user': user,
                'enterprise': enterprise,
                'login_url': login_url,
                'has_password': password is not None,
                'password': password,
                'protocol': 'https' if getattr(settings, 'USE_HTTPS', False) else 'http'
            }
            
            # Log context for debugging (without password)
            safe_context = context.copy()
            if 'password' in safe_context and safe_context['password']:
                safe_context['password'] = '********'  # Mask password in logs
            print(f"[EMAIL] Context: {safe_context}")
            
            # Render email content
            if password:
                subject = f"Your {enterprise.name} account has been created"
            else:
                subject = f"Welcome to {enterprise.name}"
                
            print(f"[EMAIL] Subject: {subject}")
            
            try:
                print("[EMAIL] Rendering email template...")
                text_content = render_to_string('emails/enterprise_invitation.txt', context)
                print("[EMAIL] Successfully rendered email template")
                
                # Log first 100 chars of email content for debugging
                logger.debug(f"[EMAIL] Email content preview: {text_content[:100]}...")
                
            except Exception as e:
                logger.error(f"[EMAIL] Failed to render email template: {str(e)}", exc_info=True)
                raise Exception(f"Failed to render email template: {str(e)}")
            
            # Log email details
            print(f"[EMAIL] Preparing to send email to: {user.email}")
            print(f"[EMAIL] From email address: {settings.DEFAULT_FROM_EMAIL}")
            
            # Check if we're in development mode
            if getattr(settings, 'DEBUG', False):
                logger.warning("[EMAIL] DEBUG mode is enabled. Email will be printed to console instead of being sent.")
                print("\n" + "="*80)
                print(f"DEBUG EMAIL: Would send to {user.email}")
                print(f"Subject: {subject}")
                print("\nContent:")
                print(text_content)
                print("="*80 + "\n")
                return True
                
            # Send email using AWS SES
            try:
                print("[EMAIL] Sending email via AWS SES...")
                
                # Log AWS SES configuration
                logger.debug(f"[EMAIL] AWS SES Configuration:")
                logger.debug(f"- AWS_ACCESS_KEY_ID: {'Set' if hasattr(settings, 'AWS_ACCESS_KEY_ID') else 'Not set'}")
                logger.debug(f"- AWS_SECRET_ACCESS_KEY: {'Set' if hasattr(settings, 'AWS_SECRET_ACCESS_KEY') else 'Not set'}")
                logger.debug(f"- AWS_DEFAULT_REGION: {getattr(settings, 'AWS_DEFAULT_REGION', 'Not set')}")
                
                response = send_email_via_ses(
                    subject=subject,
                    body=text_content,
                    to_emails=[user.email],
                    from_email=settings.DEFAULT_FROM_EMAIL
                )
                
                if not response:
                    error_msg = "[EMAIL] send_email_via_ses returned None - check AWS SES configuration and credentials"
                    logger.error(error_msg)
                    raise Exception(error_msg)
                
                # Log successful sending
                print(f"[EMAIL] Successfully sent invitation email to {user.email}")
                logger.debug(f"[EMAIL] SES Response: {response}")
                
                return True
                
            except Exception as e:
                error_msg = f"[EMAIL] Error in send_email_via_ses: {str(e)}"
                logger.error(error_msg, exc_info=True)
                
                # Check for common AWS SES issues
                if 'InvalidParameterValue' in str(e):
                    logger.error("[EMAIL] Possible issue with sender email verification in AWS SES")
                elif 'MessageRejected' in str(e):
                    logger.error("[EMAIL] Message rejected by SES - check if the sending domain is verified")
                elif 'Credentials' in str(e):
                    logger.error("[EMAIL] AWS credentials issue - check AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY")
                elif 'Region' in str(e):
                    logger.error("[EMAIL] AWS region issue - check AWS_DEFAULT_REGION setting")
                    
                raise Exception(error_msg) from e
            
        except Exception as e:
            error_msg = f"[EMAIL] Failed to send invitation email to {user.email}: {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise Exception(error_msg)
