from rest_framework import viewsets, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.exceptions import PermissionDenied

from django.conf import settings
from django.conf import settings
from django.db.models import Count, Avg, Case, When, Value, CharField, Sum
from django.utils.timezone import now
from django.shortcuts import get_object_or_404
from django.contrib.auth import get_user_model
from django.db.models.functions import Cast, TruncMonth


import os
import json
from datetime import timedelta
from datetime import datetime, timedelta
from collections import Counter
from openai import OpenAI


from .models import (
    PracticeSession,
    PracticeSequence,
    ChunkSentimentAnalysis,
    SessionChunk,
)
from .serializers import (
    PracticeSessionSerializer,
    PracticeSessionSlidesSerializer,
    PracticeSequenceSerializer,
    ChunkSentimentAnalysisSerializer,
    SessionChunkSerializer,
)

User = get_user_model()


class PracticeSequenceViewSet(viewsets.ModelViewSet):
    """
    ViewSet for handling practice session sequences.
    Regular users can manage their own sequences; admin users can manage all.
    """

    serializer_class = PracticeSequenceSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user

        if getattr(self, "swagger_fake_view", False) or user.is_anonymous:
            return PracticeSequence.objects.none()

        if hasattr(user, "userprofile") and user.userprofile.is_admin():
            return PracticeSequence.objects.all().order_by("-sequence_name")

        return PracticeSequence.objects.filter(user=user).order_by("-sequence_name")

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

        if getattr(self, "swagger_fake_view", False) or user.is_anonymous:
            return (
                PracticeSession.objects.none()
            )  # Return empty queryset for schema generation or anonymous users

        if hasattr(user, "userprofile") and user.userprofile.is_admin():
            return PracticeSession.objects.all().order_by("-date")

        return PracticeSession.objects.filter(user=user).order_by("-date")

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    @action(detail=True, methods=["get"])
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
      - Recent sessions (with duration)
      - Total new sessions (per day) and the percentage difference from yesterday
      - Session category breakdown with percentage difference from yesterday
      - User growth per day
      - Number of active and inactive users
      - parameter to filter with(start_date, end_date, section)
      - section in the parameter can be (total_session,no_of_session,user_growth)

    For regular users:
      - Latest session aggregated data (pauses, tone, emotional_impact, audience_engagement)
      - Average aggregated data across all their sessions
      - Latest session score
      - Performance analytics data over time (list of dictionaries with date, volume, articulation, confidence)
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        data = {}
        today = now().date()
        yesterday = today - timedelta(days=1)
        # Get filter parameters from query params
        dashboard_section = request.query_params.get("section")
        start_date_str = request.query_params.get("start_date")
        end_date_str = request.query_params.get("end_date")

        if hasattr(user, "userprofile") and user.userprofile.is_admin():
            sessions = PracticeSession.objects.all()
            filtered_sessions = sessions

            # Filtering all session base on start date and time and  dashboard section
            if start_date_str and end_date_str:
                filtered_sessions = sessions.filter(
                    date__date__range=[start_date_str, end_date_str]
                )

                # parsed the start date and end date
                parsed_start = datetime.strptime(start_date_str, "%Y-%m-%d").date()
                parsed_end = datetime.strptime(end_date_str, "%Y-%m-%d").date()

                # Step 2: Calculate the previous interval
                interval_length = (parsed_start - parsed_end).days + 1
                prev_start_date = parsed_start - timedelta(days=interval_length)
                prev_end_date = parsed_end - timedelta(days=interval_length)
                previous_sessions = PracticeSession.objects.filter(
                    date__date__range=(prev_start_date, prev_end_date)
                )
            # if not query parameter is provided use yesterday
            previous_sessions = PracticeSession.objects.filter(
                date__date__range=(yesterday, yesterday)
            )
            total_sessions_qs = (
                filtered_sessions
                if dashboard_section == "total_session"
                else PracticeSession.objects.filter(date__date__range=(today, today))
            )
            no_of_sessions_qs = (
                filtered_sessions
                if dashboard_section == "no_of_session"
                else PracticeSession.objects.filter(date__date__range=(today, today))
            )
            user_growth_qs = (
                filtered_sessions if dashboard_section == "user_growth" else sessions
            )

            current_breakdown = total_sessions_qs.values("session_type").annotate(
                count=Count("id")
            )
            previous_breakdown = previous_sessions.values("session_type").annotate(
                count=Count("id")
            )

            # Convert previous breakdown to dict for fast lookup
            previous_counts = {
                entry["session_type"]: entry["count"] for entry in previous_breakdown
            }
            # Final list with percentage differences
            breakdown_with_difference = []
            for entry in current_breakdown:
                session_type = entry["session_type"]
                current_count = entry["count"]
                previous_count = previous_counts.get(session_type, 0)
                percentage_diff = self.calculate_percentage_difference(
                    current_count, previous_count
                )
                breakdown_with_difference.append(
                    {
                        "session_type": session_type,
                        "current_count": current_count,
                        "previous_count": previous_count,
                        "percentage_difference": percentage_diff,
                    }
                )

            total_sessions = total_sessions_qs.count()

            breakdown = breakdown_with_difference
            # breakdown = filtered_sessions.values("session_type").annotate(
            #     count=Count("id")
            # )
            last_30_days = now() - timedelta(days=30)
            sessions_over_time = (
                no_of_sessions_qs.filter(
                    date__date__range=(start_date_str, end_date_str)
                )
                .extra(select={"day": "date(date)"})
                .values("day", "session_type")
                .annotate(count=Count("id"))
                .order_by("day")
            )
            # recent_sessions = sessions.order_by("-date")[:5].values(
            #     "id", "session_name", "session_type", "date", "duration"
            # )

            recent_sessions = (
                sessions.annotate(
                    session_type_display=Case(
                        When(session_type="pitch", then=Value("Pitch Practice")),
                        When(session_type="public", then=Value("Public Speaking")),
                        When(session_type="presentation", then=Value("Presentation")),
                        output_field=CharField(),
                    ),
                    formatted_duration=Cast("duration", output_field=CharField()),
                )
                .order_by("-date")[:5]
                .values(
                    "id",
                    "session_name",
                    "session_type_display",
                    "date",
                    "formatted_duration",
                )
            )

            # Total new sessions (per day) and the percentage difference from yesterday
            # today_new_sessions_count = PracticeSession.objects.filter(
            #     date__date=today
            # ).count()
            # yesterday_new_sessions_count = PracticeSession.objects.filter(
            #     date__date=yesterday
            # ).count()
            # new_sessions_percentage_difference = self.calculate_percentage_difference(
            #     today_new_sessions_count, yesterday_new_sessions_count
            # )

            # Session category breakdown with percentage difference from yesterday
            # session_types = ["public", "pitch", "presentation"]
            # session_breakdown_with_diff = []
            # for session_type in session_types:
            #     today_count = PracticeSession.objects.filter(
            #         date__date=today, session_type=session_type
            #     ).count()
            #     yesterday_count = PracticeSession.objects.filter(
            #         date__date=yesterday, session_type=session_type
            #     ).count()
            #     percentage_difference = self.calculate_percentage_difference(
            #         today_count, yesterday_count
            #     )
            #     session_breakdown_with_diff.append(
            #         {
            #             "session_type": session_type,
            #             "today_count": today_count,
            #             "percentage_difference": percentage_difference,
            #         }
            #     )

            # User growth per day
            today_new_users_count = User.objects.filter(date_joined__date=today).count()
            yesterday_new_users_count = User.objects.filter(
                date_joined__date=yesterday
            ).count()
            user_growth_percentage_difference = self.calculate_percentage_difference(
                today_new_users_count, yesterday_new_users_count
            )

            # Number of active and inactive users (assuming active means having created at least one session)
            active_users_count = (
                PracticeSession.objects.values("user").distinct().count()
            )
            total_users_count = User.objects.count()
            inactive_users_count = total_users_count - active_users_count

            data = {
                "total_sessions": total_sessions,
                "session_breakdown": list(breakdown),
                "sessions_over_time": list(sessions_over_time),
                "recent_sessions": list(recent_sessions),
                "credits": user.userprofile.available_credits,
                # "today_new_sessions_count": today_new_sessions_count,
                # "new_sessions_percentage_difference": new_sessions_percentage_difference,
                # "session_breakdown_with_difference": session_breakdown_with_diff,
                "today_new_users_count": today_new_users_count,
                "user_growth_percentage_difference": user_growth_percentage_difference,
                "active_users_count": active_users_count,
                "inactive_users_count": inactive_users_count,
            }
        else:
            latest_session = (
                PracticeSession.objects.filter(user=user).order_by("-date").first()
            )
            latest_aggregated_data = {}
            latest_session_score = None
            performance_analytics_over_time = []

            if latest_session:
                # Calculate the average volume for the latest session
                average_volume = ChunkSentimentAnalysis.objects.filter(
                    chunk__session=latest_session
                ).aggregate(avg_volume=Avg("volume"))["avg_volume"]
                # Calculate the average pace for the latest session
                average_pace = ChunkSentimentAnalysis.objects.filter(
                    chunk__session=latest_session
                ).aggregate(avg_pace=Avg("pace"))["avg_pace"]

                latest_aggregated_data = {
                    "impact": latest_session.impact,
                    "volume": (
                        average_volume if average_volume is not None else 0.0
                    ),  # Use average volume
                    "pace": (
                        average_pace if average_pace is not None else 0.0
                    ),  # Use average pace
                    "clarity": latest_session.clarity,
                    "engagement": latest_session.audience_engagement,
                    "credits": user.userprofile.available_credits,
                    # Add other relevant aggregated fields here
                }
                # Calculate the latest session score (average impact from chunks)
                chunk_impacts = ChunkSentimentAnalysis.objects.filter(
                    chunk__session=latest_session
                ).values_list("impact", flat=True)
                if chunk_impacts:
                    latest_session_score = sum(chunk_impacts) / len(chunk_impacts)

            # Prepare performance analytics data over time
            user_sessions = PracticeSession.objects.filter(user=user).order_by("date")
            for session in user_sessions:
                chunk_data = ChunkSentimentAnalysis.objects.filter(
                    chunk__session=session
                ).aggregate(
                    avg_volume=Avg("volume"),
                    avg_articulation=Avg("clarity"),
                    avg_confidence=Avg("confidence"),
                    avg_pace=Avg(
                        "pace"
                    ),  # Include pace here as well if needed in the historical data
                )
                if (
                    chunk_data["avg_volume"] is not None
                    and chunk_data["avg_articulation"] is not None
                    and chunk_data["avg_confidence"] is not None
                ):
                    performance_analytics_over_time.append(
                        {
                            "date": session.date.isoformat(),  # Use isoformat for easy handling in JavaScript
                            "volume": chunk_data["avg_volume"],
                            "articulation": chunk_data["avg_articulation"],
                            "confidence": chunk_data["avg_confidence"],
                            # might want to include pace in the historical data as well
                        }
                    )

            # Calculate averages of the aggregated fields across all user sessions
            aggregated_averages = PracticeSession.objects.filter(user=user).aggregate(
                avg_pauses=Avg("pauses"),
                avg_emotional_impact=Avg("emotional_expression"),
                avg_audience_engagement=Avg("audience_engagement"),
                # Add averages for other relevant aggregated fields
            )

            data = {
                "latest_session_data": latest_aggregated_data,
                "average_performance": aggregated_averages,
                "latest_session_score": latest_session_score,
                "performance_analytics": performance_analytics_over_time,
            }
        return Response(data, status=status.HTTP_200_OK)

    def calculate_percentage_difference(self, current_value, previous_value):
        if previous_value == 0:
            return 100.0 if current_value > 0 else 0.0
        return ((current_value - previous_value) / previous_value) * 100


class UploadSessionSlidesView(APIView):
    """
    Endpoint to upload slides to a specific practice session.
    """

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]  # To handle file uploads

    def put(self, request, pk=None):
        """
        Upload slides for a practice session.
        """
        practice_session = get_object_or_404(PracticeSession, pk=pk)

        # Ensure the user making the request is the owner of the session
        if practice_session.user != request.user:
            return Response(
                {
                    "message": "You do not have permission to upload slides for this session."
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = PracticeSessionSlidesSerializer(
            practice_session, data=request.data, partial=True
        )  # partial=True for updates

        if serializer.is_valid():
            serializer.save()
            return Response(
                {
                    "status": "success",
                    "message": "Slides uploaded successfully.",
                    "data": serializer.data,
                },
                status=status.HTTP_200_OK,
            )
        else:
            return Response(
                {
                    "status": "fail",
                    "message": "Slide upload failed.",
                    "errors": serializer.errors,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )


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
        ).values_list("general_feedback_summary", flat=True)

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
                messages=[{"role": "user", "content": prompt}],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "Feedback",
                        "schema": {
                            "type": "object",
                            "properties": {
                                "Strength": {"type": "string"},
                                "Area of Improvement": {"type": "string"},
                                "General Feedback Summary": {"type": "string"},
                            },
                        },
                    },
                },
            )

            refined_summary = completion.choices[0].message.content
            parsed_summary = json.loads(refined_summary)

        except Exception as e:
            print(f"Error generating summary: {e}")
            parsed_data = {
                "Strength": "N/A",
                "Area of Improvement": "N/A",
                "General Feedback Summary": combined_feedback,
            }
        return parsed_summary

    def get(self, request, session_id):
        session = get_object_or_404(PracticeSession, id=session_id, user=request.user)

        averages = ChunkSentimentAnalysis.objects.filter(
            chunk__session=session
        ).aggregate(
            avg_engagement=Avg("engagement"),
            avg_conviction=Avg("conviction"),
            avg_clarity=Avg("clarity"),
            avg_impact=Avg("impact"),
            avg_brevity=Avg("brevity"),
            avg_transformative_potential=Avg("transformative_potential"),
            avg_body_posture=Avg("body_posture"),
            avg_volume=Avg("volume"),
            avg_pitch=Avg("pitch_variability"),
            avg_pace=Avg("pace"),
        )

        full_summary = self.generate_full_summary(session_id)

        return Response(
            {"average_scores": averages, "full_summary": full_summary},
            status=status.HTTP_200_OK,
        )


class SessionChunkViewSet(viewsets.ModelViewSet):
    """
    ViewSet for handling individual session chunks.
    """

    serializer_class = SessionChunkSerializer
    permission_classes = [IsAuthenticated]  # You might want to adjust permissions

    def get_queryset(self):
        user = self.request.user
        if getattr(self, "swagger_fake_view", False) or user.is_anonymous:
            return SessionChunk.objects.none()
        # Consider filtering by user's sessions if needed
        return SessionChunk.objects.all()

    def perform_create(self, serializer):
        # Ensure the session belongs to the user making the request (optional security)
        session = serializer.validated_data["session"]
        if session.user != self.request.user:
            raise PermissionDenied("Session does not belong to this user.")
        serializer.save()


class ChunkSentimentAnalysisViewSet(viewsets.ModelViewSet):
    """
    ViewSet for handling sentiment analysis results for each chunk.
    """

    serializer_class = ChunkSentimentAnalysisSerializer
    permission_classes = [IsAuthenticated]  # You might want to adjust permissions

    def get_queryset(self):
        user = self.request.user
        if getattr(self, "swagger_fake_view", False) or user.is_anonymous:
            return ChunkSentimentAnalysis.objects.none()
        # Consider filtering by user's sessions if needed
        return ChunkSentimentAnalysis.objects.all()

    def perform_create(self, serializer):
        # Optionally add checks here, e.g., ensure the chunk belongs to a user's session
        serializer.save()


class SessionReportView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, session_id):
        try:
            session = PracticeSession.objects.get(id=session_id, user=request.user)
        except PracticeSession.DoesNotExist:
            return Response(
                {"error": "Session not found"}, status=status.HTTP_404_NOT_FOUND
            )

        chunks = session.chunks.select_related("sentiment_analysis").all()

        data = []
        graph_data = []
        all_brevity = []
        all_transformative_potential = []
        all_impact = []
        all_clarity = []
        all_volume = []
        all_pitch = []
        all_pace = []
        all_pauses = []

        for index, chunk in enumerate(chunks, start=1):
            if hasattr(chunk, "sentiment_analysis"):
                analysis = chunk.sentiment_analysis
                # graph dict
                graph_data.append(
                    {
                        "chuck_no": f"Chunk {index}",
                        "start_time": chunk.start_time,
                        "end_time": chunk.end_time,
                        "brevity": analysis.brevity,
                        "conviction": analysis.conviction,
                        "impact": analysis.impact,
                    }
                )
                # Collecting for avg
                all_brevity.append(analysis.brevity)
                all_transformative_potential.append(analysis.transformative_potential)
                all_impact.append(analysis.impact)
                all_clarity.append(analysis.clarity)
                all_volume.append(analysis.volume)
                all_pitch.append(analysis.pitch_variability)
                all_pace.append(analysis.pace)
                all_pauses.append(session.pauses)

        # Compute averages
        def safe_avg(lst):
            return round(sum(lst) / len(lst), 2) if lst else None

        averages = {
            "brevity": safe_avg(all_brevity),
            "all_transformative_potential": safe_avg(all_transformative_potential),
            "impact": safe_avg(all_impact),
            "clarity": safe_avg(all_clarity),
            "volume": safe_avg(all_volume),
            "pitch": safe_avg(all_pitch),
            "pace": safe_avg(all_pace),
            "pauses": safe_avg(all_pauses),
        }

        return Response({"graph_data": graph_data, "averages": averages})


class PerformanceAnalyticsView(APIView):
    def get(self, request):
        data = (
            ChunkSentimentAnalysis.objects.select_related("chunk__session")
            .filter(chunk__session__user=request.user)
            .annotate(month=TruncMonth("chunk__session__date"))
            .values("month")
            .annotate(
                total_brevity=Sum("brevity"),
                total_impact=Sum("impact"),
                total_conviction=Sum("conviction"),
            )
            .order_by("month")
        )
        result = [
            {
                "month": item["month"].strftime("%Y-%m"),
                "brevity": item["total_brevity"] or 0,
                "impact": item["total_impact"] or 0,
                "conviction": item["total_conviction"] or 0,
            }
            for item in data
        ]
        return Response(result)
