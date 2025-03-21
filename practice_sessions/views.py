from rest_framework import viewsets, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.parsers import MultiPartParser, FormParser

from django.conf import settings
from django.db.models import Count, Avg
from django.utils.timezone import now
from django.shortcuts import get_object_or_404

import os
import json
from datetime import timedelta
from collections import Counter
from openai import OpenAI


from .models import (PracticeSession, PracticeSequence, ChunkSentimentAnalysis)
from .serializers import (PracticeSessionSerializer, PracticeSessionSlidesSerializer, PracticeSequenceSerializer)


class PracticeSequenceViewSet(viewsets.ModelViewSet):
    """
    ViewSet for handling practice session sequences.
    Regular users can manage their own sequences; admin users can manage all.
    """
    serializer_class = PracticeSequenceSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user

        if getattr(self, 'swagger_fake_view', False) or user.is_anonymous:
            return PracticeSequence.objects.none()

        if hasattr(user, 'userprofile') and user.userprofile.is_admin():
            return PracticeSequence.objects.all().order_by('-sequence_name')

        return PracticeSequence.objects.filter(user=user).order_by('-sequence_name')

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


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
        Retrieve the full session report for the given session.
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
      - Latest session aggregated data (pauses, tone, emotional_impact, audience_engagement)
      - Average aggregated data across all their sessions
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
            latest_aggregated_data = {}
            if latest_session:
                latest_aggregated_data = {
                    "impact": latest_session.impact,
                    "volume": latest_session.volume,
                    "pace": latest_session.pace,
                    "clarity": latest_session.clarity,
                    "engagement": latest_session.audience_engagement,
                    # Add other relevant aggregated fields here
                }

            # Calculate averages of the aggregated fields across all user sessions
            aggregated_averages = PracticeSession.objects.filter(user=user).aggregate(
                avg_pauses=Avg('pauses'),
                avg_emotional_impact=Avg('emotional_impact'),
                avg_audience_engagement=Avg('audience_engagement'),
                # Add averages for other relevant aggregated fields
            )

            data = {
                "latest_session_data": latest_aggregated_data,
                "average_performance": aggregated_averages,
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


class ChunkSentimentAnalysisView(APIView):
    """
    Calculates Averages of Scores and generate summary
    """

    permission_classes = [IsAuthenticated]


    def generate_full_summary(self, session_id):
        """Creates a cohesive summary for Strengths, Improvements, and Feedback."""
        client = OpenAI(api_key=settings.OPENAI_API_KEY)

        general_feedback_summary = ChunkSentimentAnalysis.objects.filter(
            chunk__session__id=session_id
        ).values_list('general_feedback_summary', flat=True)

        combined_feedback = " ".join([g for g in general_feedback_summary if g])

        # get strenghts and areas of improvements
        # grade content_organisation (0-100), from transcript

        prompt = f"""
        Using the following presentation evaluation data, provide a structured JSON response containing three key elements:
        
        1. **Strength**: Identify the speaker’s most notable strengths based on their delivery, clarity, and engagement.
        2. **Area of Improvement**: Provide actionable and specific recommendations for improving the speaker’s performance.
        3. **General Feedback Summary**: Summarize the presentation’s overall effectiveness, balancing positive feedback with constructive advice.

        Data to analyze:
        {combined_feedback}
        """

        try:
            completion = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{
                    "role": "user", "content": prompt
                }],
                response_format = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "Feedback",
                        "schema": {
                            "type": "object",
                            "properties": {
                                "Strength": {"type": "string"},
                                "Area of Improvement": {"type": "string"},
                                "General Feedback Summary": {"type": "string"}
                            }
                        }
                    }
                }
            )

            refined_summary = completion.choices[0].message.content
            parsed_summary = json.loads(refined_summary)

        except Exception as e:
            print(f"Error generating summary: {e}")
            parsed_data = {
                "Strength": "N/A",
                "Area of Improvement": "N/A",
                "General Feedback Summary": combined_feedback
            }
        return parsed_summary

    
    def get(self, request, session_id):
        session = get_object_or_404(PracticeSession, id=session_id, user=request.user)

        averages = ChunkSentimentAnalysis.objects.filter(chunk__session=session).aggregate(
            avg_engagement=Avg('engagement'),
            avg_conviction=Avg('conviction'),
            avg_clarity=Avg('clarity'),
            avg_impact=Avg('impact'),
            avg_brevity=Avg('brevity'),
            avg_transformative_potential=Avg('transformative_potential'),
            avg_body_posture=Avg('body_posture'),
            avg_volume=Avg('volume'),
            avg_pitch=Avg('pitch_variability'),
            avg_pace=Avg('pace'),
        )

        full_summary = self.generate_full_summary(session_id)

        return Response({
            'average_scores': averages,
            'full_summary': full_summary
        }, status=status.HTTP_200_OK)
