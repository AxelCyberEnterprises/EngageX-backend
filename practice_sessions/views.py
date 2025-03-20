from rest_framework import viewsets, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.parsers import MultiPartParser, FormParser

from django.db.models import Count, Avg
from django.utils.timezone import now
from django.shortcuts import get_object_or_404

import os
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

    def get_most_common_tones(self, session_id):
        tones = ChunkSentimentAnalysis.objects.filter(
            chunk__session__id=session_id
        ).values_list('tone', flat=True)

        valid_tones = [
            "Authoritative", "Persuasive", "Conversational", "Inspirational",
            "Empathetic", "Enthusiastic", "Serious", "Humorous", "Reflective", "Urgent"
        ]

        filtered_tones = [
            t.strip() for tone in tones if tone
            for t in tone.strip().split(',') if t.strip() in valid_tones
        ]

        if not filtered_tones:
            return "N/A"

        top_tones = Counter(filtered_tones).most_common(2)
        return ', '.join([tone[0] for tone in top_tones])

    def generate_full_summary(self, session_id):
        """Creates a cohesive summary for Strengths, Improvements, and Feedback."""
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

        strengths = ChunkSentimentAnalysis.objects.filter(
            chunk__session__id=session_id
        ).values_list('strengths', flat=True)

        areas_of_improvements = ChunkSentimentAnalysis.objects.filter(
            chunk__session__id=session_id
        ).values_list('areas_of_improvements', flat=True)

        general_feedback_summary = ChunkSentimentAnalysis.objects.filter(
            chunk__session__id=session_id
        ).values_list('general_feedback_summary', flat=True)

        combined_strengths = " ".join([s for s in strengths if s])
        combined_improvements = " ".join([a for a in areas_of_improvements if a])
        combined_feedback = " ".join([g for g in general_feedback_summary if g])

        full_summary = f"""
        Strengths: {combined_strengths}

        Areas for Improvement: {combined_improvements}

        General Feedback: {combined_feedback}
        """

        prompt = f"""
        Summarize the following presentation evaluation data into a clear and concise summary, highlighting key strengths, actionable improvements, and overall feedback. Ensure the summary is structured, engaging, and helpful.

        {full_summary}

        The summary should be clear, informative, and highlight both positive and constructive points.
        """

        try:
            completion = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{
                    "role": "user", "content": prompt
                }]
            )

            refined_summary = completion.choices[0].message.content

        except Exception as e:
            print(f"Error generating GPT summary: {e}")
            refined_summary = full_summary

        return refined_summary

    
    def get(self, request, session_id):
        session = get_object_or_404(PracticeSession, id=session_id, user=request.user)

        averages = ChunkSentimentAnalysis.objects.filter(chunk__session=session).aggregate(
            avg_impact=Avg('impact'),
            avg_volume=Avg('volume'),
            avg_pitch=Avg('pitch_variability'),
            avg_pace=Avg('pace'),
            avg_clarity=Avg('clarity'),
        )

        averages['tone'] = self.get_most_common_tones(session_id)

        full_summary = self.generate_full_summary(session_id)

        return Response({
            'average_scores': averages,
            'full_summary': full_summary
        }, status=status.HTTP_200_OK)
