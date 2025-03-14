from rest_framework import viewsets, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.parsers import MultiPartParser, FormParser

from django.db.models import Count, Avg
from django.utils.timezone import now
from django.shortcuts import get_object_or_404

from datetime import timedelta

from .models import (PracticeSession, SessionDetail)
from .serializers import (PracticeSessionSerializer, PracticeSessionSlidesSerializer)


class PracticeSessionViewSet(viewsets.ModelViewSet):
    """
    ViewSet for handling practice session history.
    Admin users see all sessions; regular users see only their own sessions.
    Includes a custom action 'report' to retrieve full session details.
    """
    serializer_class = PracticeSessionSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user

        if getattr(self, 'swagger_fake_view', False) or user.is_anonymous:
            return PracticeSession.objects.none() # Return empty queryset for schema generation or anonymous users

        if hasattr(user, 'userprofile') and user.userprofile.is_admin():
            return PracticeSession.objects.all().order_by('-date')

        return PracticeSession.objects.filter(user=user).order_by('-date')

    @action(detail=True, methods=['get'])
    def report(self, request, pk=None):
        """
        Retrieve the full session report (with details) for the given session.
        Admins can view any session; regular users can view only their own.
        """
        session = self.get_object()
        serializer = PracticeSessionSerializer(session)
        return Response(serializer.data)


class SessionDashboardView(APIView):
    """
    Dashboard endpoint that returns different aggregated data depending on user role.
    
    For admin users:
      - Total sessions
      - Breakdown of sessions by type (pitch, public speaking, presentation)
      - Sessions over time (for graphing purposes)
      - Recent sessions
      
    For regular users:
      - Latest session score (derived from session details)
      - Average performance analytics (e.g. pitch variation, volume control, articulation)
    """
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        user = request.user
        data = {}
        if hasattr(user, 'userprofile') and user.userprofile.is_admin():
            sessions = PracticeSession.objects.all()
            total_sessions = sessions.count()
            breakdown = sessions.values('session_type').annotate(count=Count('id'))
            last_30_days = now() - timedelta(days=30)
            sessions_over_time = (sessions.filter(date__gte=last_30_days)
                                  .extra(select={'day': "date(date)"})
                                  .values('day')
                                  .annotate(count=Count('id'))
                                  .order_by('day'))
            recent_sessions = sessions.order_by('-date')[:5].values('session_name', 'session_type', 'date')
            data = {
                "total_sessions": total_sessions,
                "session_breakdown": list(breakdown),
                "sessions_over_time": list(sessions_over_time),
                "recent_sessions": list(recent_sessions)
            }
        else:
            latest_session = PracticeSession.objects.filter(user=user).order_by('-date').first()
            if latest_session and hasattr(latest_session, 'details'):
                details = latest_session.details
                latest_score = {
                    "pitch_variation": details.pitch_variation,
                    "volume_control": details.volume_control,
                    "articulation": details.articulation,
                }
            else:
                latest_score = {}
            analytics = SessionDetail.objects.filter(session__user=user).aggregate(
                avg_pitch=Avg('pitch_variation'),
                avg_volume=Avg('volume_control'),
                avg_articulation=Avg('articulation')
            )
            data = {
                "latest_session_score": latest_score,
                "performance_analytics": analytics,
            }
        return Response(data, status=status.HTTP_200_OK)


class UploadSessionSlidesView(APIView):
    """
    Endpoint to upload slides to a specific practice session.
    """
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser] # To handle file uploads

    def put(self, request, pk=None):
        """
        Upload slides for a practice session.
        """
        practice_session = get_object_or_404(PracticeSession, pk=pk)

        # Ensure the user making the request is the owner of the session
        if practice_session.user != request.user:
            return Response({"message": "You do not have permission to upload slides for this session."}, status=status.HTTP_403_FORBIDDEN)

        serializer = PracticeSessionSlidesSerializer(practice_session, data=request.data, partial=True) # partial=True for updates

        if serializer.is_valid():
            serializer.save()
            return Response({
                "status": "success",
                "message": "Slides uploaded successfully.",
                "data": serializer.data
            }, status=status.HTTP_200_OK)
        else:
            return Response({
                "status": "fail",
                "message": "Slide upload failed.",
                "errors": serializer.errors
            }, status=status.HTTP_400_BAD_REQUEST)