from rest_framework import viewsets, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.exceptions import PermissionDenied


from django.db.models.functions import Ceil
from django.conf import settings
from django.db.models import (
    Count,
    Avg,
    Case,
    When,
    Value,
    CharField,
    Sum,
    IntegerField,
    Q,
    ExpressionWrapper,
    FloatField,
)
from django.utils.timezone import now
from django.shortcuts import get_object_or_404
from django.contrib.auth import get_user_model
from django.db.models.functions import Cast, TruncMonth, TruncDay

import os
import json
from datetime import timedelta
from datetime import datetime, timedelta
from collections import Counter
from openai import OpenAI
from drf_yasg.utils import swagger_auto_schema
from collections import defaultdict
import traceback

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
    SessionReportSerializer,
)

User = get_user_model()


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

        if hasattr(user, "user_profile") and user.userprofile.is_admin():
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
        if hasattr(user, "user_profile") and user.user_profile.is_admin():
            sessions = PracticeSession.objects.all()
            filtered_sessions = sessions

            # Filtering all session base on start date and time and  dashboard section
            if start_date_str and end_date_str:
                parsed_start = datetime.strptime(start_date_str, "%Y-%m-%d").date()
                parsed_end = datetime.strptime(end_date_str, "%Y-%m-%d").date()

                # filtered_sessions = sessions.filter(
                #     date__date__range=[parsed_start, parsed_end]
                # )

                # Calculate previous range
                interval_length = (parsed_end - parsed_start).days + 1
                print(interval_length)
                prev_start_date = parsed_start - timedelta(days=interval_length)
                prev_end_date = parsed_end - timedelta(days=interval_length)
                print(prev_start_date, prev_end_date)

                # Previous session base on the prev date
                previous_sessions = sessions.filter(
                    date__date__range=(prev_start_date, prev_end_date)
                )
                print(previous_sessions)
            else:
                # use today as current and yesterday as previous (default)
                filtered_sessions = sessions.filter(date__date=today)
                previous_sessions = sessions.filter(date__date=yesterday)

            previous_total_sessions_count = previous_sessions.count()

            total_sessions_qs = (
                sessions.filter(date__date__range=[parsed_start, parsed_end])
                if dashboard_section == "total_session"
                else PracticeSession.objects.filter(date__date__range=(today, today))
            )

            no_of_sessions_qs = (
                sessions.filter(date__date__range=[parsed_start, parsed_end])
                if dashboard_section == "no_of_session"
                else PracticeSession.objects.filter(date__date__range=(today, today))
            )
            user_growth_qs = (
                sessions.filter(date__date__range=[parsed_start, parsed_end])
                if dashboard_section == "user_growth"
                else sessions
            )

            total_sessions_count = total_sessions_qs.count()

            #  Total session percentage difference
            total_session_diff = self.calculate_percentage_difference(
                total_sessions_count, previous_total_sessions_count
            )

            current_breakdown = total_sessions_qs.values("session_type").annotate(
                session_type_display=Case(
                    When(session_type="pitch", then=Value("Pitch Practice")),
                    When(session_type="public", then=Value("Public Speaking")),
                    When(session_type="presentation", then=Value("Presentation")),
                    output_field=CharField(),
                ),
                count=Count("id"),
            )
            print(current_breakdown)
            previous_breakdown = previous_sessions.values("session_type").annotate(
                count=Count("id")
            )
            print(previous_breakdown)

            # Convert previous breakdown to dict for fast lookup
            previous_counts = {
                entry["session_type"]: entry["count"] for entry in previous_breakdown
            }
            print(previous_counts)
            # Final list with percentage differences
            breakdown_with_difference = [
                {
                    "total_new_session": total_sessions_count,
                    "previous_total_sessions": previous_total_sessions_count,
                    "percentage_difference": total_session_diff,
                }
            ]
            for entry in current_breakdown:
                session_type = entry["session_type_display"]
                current_count = entry["count"]
                previous_count = previous_counts.get(entry["session_type"], 0)
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
                (no_of_sessions_qs)
                .extra(select={"day": "date(date)"})
                .values("day")
                .annotate(
                    session_type=Case(
                        When(session_type="pitch", then=Value("Pitch Practice")),
                        When(session_type="public", then=Value("Public Speaking")),
                        When(session_type="presentation", then=Value("Presentation")),
                        output_field=CharField(),
                    ),
                    count=Count("id"),
                )
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
                # "total_sessions": total_sessions,
                "session_breakdown": list(breakdown),
                "sessions_over_time": list(sessions_over_time),
                "recent_sessions": list(recent_sessions),
                "credits": user.user_profile.available_credits,
                # "today_new_sessions_count": today_new_sessions_count,
                # "new_sessions_percentage_difference": new_sessions_percentage_difference,
                # "session_breakdown_with_difference": session_breakdown_with_diff,
                "today_new_users_count": today_new_users_count,
                "user_growth_percentage_difference": user_growth_percentage_difference,
                "active_users_count": active_users_count,
                "inactive_users_count": inactive_users_count,
            }
        else:
            latest_session_score = None
            available_credit = None
            performance_analytics_over_time = []
            goals_and_achievment = []

            latest_session = (
                PracticeSession.objects.filter(user=user).order_by("-date").first()
            )

            latest_session_chunk = ChunkSentimentAnalysis.objects.filter(
                chunk__session=latest_session
            )

            available_credit = user.user_profile.available_credits if user else 0.0

            if latest_session:
                goals_and_achievment.append(
                    {
                        "vocal_variety": latest_session.vocal_variety,
                        "captured_impact": latest_session.overall_captured_impact,
                        "emotional_impact": latest_session.emotional_impact,
                        "body_language": latest_session.body_language,
                        "transformative_communication": latest_session.transformative_communication,
                        "audience_engagement": latest_session.audience_engagement,
                        "structure_and_clarity": latest_session.structure_and_clarity,
                        "language_and_word_choice": latest_session.language_and_word_choice,
                    }
                )
                # Calculate the average volume for the latest session
                # average_volume = ChunkSentimentAnalysis.objects.filter(
                #     chunk__session=latest_session
                # ).aggregate(avg_volume=Avg("volume"))["avg_volume"]
                # Calculate the average pace for the latest session
                # average_pace = ChunkSentimentAnalysis.objects.filter(
                #     chunk__session=latest_session
                # ).aggregate(avg_pace=Avg("pace"))["avg_pace"]

                # latest_aggregated_data = {
                #     "impact": latest_session.impact,
                #     "volume": (
                #         average_volume if average_volume is not None else 0.0
                #     ),  # Use average volume
                #     "pace": (
                #         average_pace if average_pace is not None else 0.0
                #     ),  # Use average pace
                # "clarity": latest_session.clarity,
                # "engagement": latest_session.audience_engagement,
                # "credits": user.userprofile.available_credits,
                # Add other relevant aggregated fields here
                # }
                # Calculate the latest session score (average impact from chunks)
                chunk_impacts = latest_session_chunk.values_list("impact", flat=True)

                latest_session_score = (
                    sum(chunk_impacts) / len(chunk_impacts) if chunk_impacts else 0
                )

                # Prepare performance analytics data over time
                for chunk in latest_session_chunk:
                    print(chunk)
                    performance_analytics_over_time.append(
                        {
                            "chunk_number": chunk.chunk_number,
                            "start_time": chunk.chunk.start_time,
                            "end_time": chunk.chunk.end_time,
                            "impact": chunk.impact,
                            "trigger_reponse": chunk.trigger_response,
                            "conviction": chunk.conviction,
                        }
                    )

            # user_sessions = PracticeSession.objects.filter(user=user).order_by("date")
            # for session in user_sessions:
            #     chunk_data = ChunkSentimentAnalysis.objects.filter(
            #         chunk__session=session
            #     ).aggregate(
            #         avg_volume=Avg("volume"),
            #         avg_articulation=Avg("clarity"),
            #         # avg_confidence=Avg("confidence"),
            #         avg_pace=Avg(
            #             "pace"
            #         ),  # Include pace here as well if needed in the historical data
            #     )
            #     if (
            #         chunk_data["avg_volume"] is not None
            #         and chunk_data["avg_articulation"] is not None
            #         # and chunk_data["avg_confidence"] is not None
            #     ):
            #         performance_analytics_over_time.append(
            #             {
            #                 "date": session.date.isoformat(),  # Use isoformat for easy handling in JavaScript
            #                 "volume": chunk_data["avg_volume"],
            #                 "articulation": chunk_data["avg_articulation"],
            #                 # "confidence": chunk_data["avg_confidence"],
            #                 # might want to include pace in the historical data as well
            #             }
            #         )

            # Calculate averages of the aggregated fields across all user sessions
            # aggregated_averages = (
            #     (
            #         PracticeSession.objects.filter(
            #             user=user,
            #             audience_engagement__isnull=False,
            #             # emotional_expression__isnull=False,
            #         ).exclude(Q(audience_engagement=""))
            #     )
            #     .annotate(
            #         audience_engagement_int=Cast("audience_engagement", IntegerField()),
            #         # emotional_expression_int=Cast(
            #         #     "emotional_expression", IntegerField()
            #         # ),
            #     )
            #     .aggregate(
            #         avg_pauses=Avg("pauses"),
            #         avg_audience_engagement=Avg("audience_engagement_int"),
            #         # avg_emotional_expression=Avg("emotional_expression_int"),
            #         # Add averages for other relevant aggregated fields
            #     )
            # )

            # print(aggregated_averages)

            data = {
                # "latest_session_data": latest_aggregated_data,
                # "average_performance": aggregated_averages,
                "latest_session_score": latest_session_score,
                "available_credit": available_credit,
                "performance_analytics": performance_analytics_over_time,
                "goals_and_achievment": goals_and_achievment,
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
            avg_conviction=Avg("conviction"),
            structure_and_clarity=Avg("clarity"),
            overall_captured_impact=Avg("impact"),
            avg_brevity=Avg("brevity"),
            avg_emotional_impact=Avg("trigger_response"),
            avg_transformative_potential=Avg("transformative_potential"),
            avg_filler_words=Avg("filler_words"),
            avg_grammar=Avg("grammar"),
            avg_posture=Avg("posture"),
            avg_motion=Avg("motion"),
            # num_of_true/total_number_of_gestures
            avg_gestures=Avg("gestures"),
            avg_volume=Avg("volume"),
            avg_pitch=Avg("pitch_variability"),
            avg_pace=Avg("pace"),
            avg_pauses=Avg("pauses"),
            body_language=ExpressionWrapper(
                ((Avg("posture") or 0) + (Avg("motion") or 0) + (Avg("gestures") or 0))
                / 3,
                output_field=FloatField(),
            ),
            vocal_variety=ExpressionWrapper(
                (
                    (Avg("volume") or 0)
                    + (Avg("pitch_variability") or 0)
                    + (Avg("pace") or 0)
                    + (Avg("pauses") or 0)
                )
                / 4,
                output_field=FloatField(),
            ),
            language_and_word_choice=ExpressionWrapper(
                (
                    (Avg("brevity") or 0)
                    + (Avg("filler_words") or 0)
                    + (Avg("grammar") or 0)
                )
                / 3,
                output_field=FloatField(),
            ),
            audience_engagement=ExpressionWrapper(
                (
                    (Avg("impact") or 0)
                    + (Avg("trigger_response") or 0)
                    + (Avg("conviction") or 0)
                )
                / 3,
                output_field=FloatField(),
            ),
        )

        averages["avg_gestures"] = min((averages.get("avg_gestures") or 0) * 100, 100)

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


# class SessionReportView(APIView):
#     permission_classes = [IsAuthenticated]

#     def generate_full_summary(self, session_id):
#         """Creates a cohesive summary for Strengths, Improvements, and Feedback using OpenAI."""
#         client = OpenAI(api_key=settings.OPENAI_API_KEY)

#         # Retrieve all general feedback summaries for the session's chunks
#         general_feedback_summaries = ChunkSentimentAnalysis.objects.filter(
#             chunk__session__id=session_id
#         ).values_list("general_feedback_summary", flat=True)

#         combined_feedback = " ".join([g for g in general_feedback_summaries if g])

#         # If there's no feedback, return default values
#         if not combined_feedback.strip():
#             print("No feedback available from chunks to generate summary.")
#             return {
#                 "Strength": "N/A - No feedback available.",
#                 "Area of Improvement": "N/A - No feedback available.",
#                 "General Feedback Summary": "No feedback was generated for the chunks in this session.",
#             }

#         prompt = f"""
#         Using the following presentation evaluation data, provide a structured JSON response containing three key elements:

#         1. **Strength**: Identify the speaker’s most notable strengths based on their delivery, clarity, and engagement.
#         2. **Area of Improvement**: Provide actionable and specific recommendations for improving the speaker’s performance.
#         3. **General Feedback Summary**: Summarize the presentation’s overall effectiveness, balancing positive feedback with constructive advice.

#         Data to analyze:
#         {combined_feedback}
#         """

#         try:
#             print("Calling OpenAI for summary generation...")
#             completion = client.chat.completions.create(
#                 model="gpt-4o-mini", # Using gpt-4o-mini as per your previous view
#                 messages=[{"role": "user", "content": prompt}],
#                 response_format={
#                     "type": "json_schema",
#                     "json_schema": {
#                         "name": "Feedback",
#                         "schema": {
#                             "type": "object",
#                             "properties": {
#                                 "Strength": {"type": "string"},
#                                 "Area of Improvement": {"type": "string"},
#                                 "General Feedback Summary": {"type": "string"},
#                             },
#                             "required": ["Strength", "Area of Improvement", "General Feedback Summary"], # Ensure these fields are in the response
#                         },
#                     },
#                 },
#                  temperature=0.7, # Adjust temperature as needed
#                  max_tokens=500 # Limit tokens to control response length
#             )

#             refined_summary = completion.choices[0].message.content
#             print(f"OpenAI raw response: {refined_summary}")
#             parsed_summary = json.loads(refined_summary)
#             print(f"Parsed summary: {parsed_summary}")
#             return parsed_summary

#         except json.JSONDecodeError as e:
#              print(f"Error decoding JSON from OpenAI response: {e}")
#              print(f"Faulty JSON content: {refined_summary}")
#              # Fallback in case of JSON decoding error
#              return {
#                 "Strength": "N/A - Error generating detailed summary.",
#                 "Area of Improvement": "N/A - Error generating detailed summary.",
#                 "General Feedback Summary": f"Error processing AI summary. Raw feedback: {combined_feedback}",
#              }
#         except Exception as e:
#             print(f"Error generating summary with OpenAI: {e}")
#             # Fallback in case of any other OpenAI error
#             return {
#                 "Strength": "N/A - Error generating detailed summary.",
#                 "Area of Improvement": "N/A - Error generating detailed summary.",
#                 "General Feedback Summary": f"Error processing AI summary. Raw feedback: {combined_feedback}",
#             }


#     def get(self, request, session_id):
#         # Keep the GET method as is, it just returns the session details
#         try:
#             session = PracticeSession.objects.get(id=session_id, user=request.user)
#             session_serializer = PracticeSessionSerializer(session)
#             return Response(session_serializer.data, status=status.HTTP_200_OK)
#         except PracticeSession.DoesNotExist:
#             return Response(
#                 {"error": "Session not found"}, status=status.HTTP_404_NOT_FOUND
#             )

#     @swagger_auto_schema(
#         operation_description="update the session duration, calculate report, and generate summary",
#         request_body=SessionReportSerializer,
#         responses={},
#     )
#     def post(self, request, session_id):
#         print(f"Starting report generation and summary for session ID: {session_id}")
#         duration_seconds = request.data.get("duration")

#         try:
#             session = get_object_or_404(PracticeSession, id=session_id, user=request.user)
#             print(f"Session found: {session.session_name}")

#             # --- Update Duration ---
#             if duration_seconds is not None:
#                 try:
#                     duration_seconds_int = int(duration_seconds)
#                     session.duration = timedelta(seconds=duration_seconds_int)
#                     session.save(update_fields=['duration'])
#                     print(f"Session duration updated to: {session.duration}")
#                 except ValueError:
#                     print(f"Invalid duration value received: {duration_seconds}")
#                 except Exception as e:
#                     print(f"Error saving duration: {e}")

#             # --- Aggregate Chunk Sentiment Analysis Data ---
#             print("Aggregating chunk sentiment analysis data...")
#             # Get chunks with sentiment analysis data
#             chunks_with_sentiment = session.chunks.filter(
#                 sentiment_analysis__isnull=False
#             )
#             print(f"Number of chunks with sentiment analysis found: {chunks_with_sentiment.count()}")

#             # If no chunks with sentiment analysis, return a basic report
#             if not chunks_with_sentiment.exists():
#                  print("No chunks with sentiment analysis found. Returning basic report.")
#                  # You might want to populate session with default N/A or 0 values here
#                  # session.volume = 0 # etc.
#                  # session.strength = "No analysis data available." # etc.
#                  # session.save() # Save defaults if needed

#                  return Response({
#                      "session_id": session.id,
#                      "session_name": session.session_name,
#                      "duration": str(session.duration) if session.duration else None,
#                      "aggregated_scores": {}, # Empty or default values
#                      "derived_scores": {}, # Empty or default values
#                      "full_summary": {
#                          "Strength": "No analysis data available for summary.",
#                          "Area of Improvement": "No analysis data available for summary.",
#                          "General Feedback Summary": "No analysis data was generated for this session's chunks.",
#                      },
#                      "gestures_percentage": 0.0
#                      # No graph_data if you removed it from the response
#                  }, status=status.HTTP_200_OK)


#             aggregation_results = chunks_with_sentiment.aggregate(
#                 # Aggregate individual metrics
#                 avg_volume=Avg("sentiment_analysis__volume"),
#                 avg_pitch_variability=Avg("sentiment_analysis__pitch_variability"),
#                 avg_pace=Avg("sentiment_analysis__pace"),
#                 total_pauses=Sum("sentiment_analysis__pauses", output_field=IntegerField()), # Use Sum for total pauses
#                 avg_conviction=Avg("sentiment_analysis__conviction"),
#                 avg_clarity=Avg("sentiment_analysis__clarity"),
#                 avg_impact=Avg("sentiment_analysis__impact"),
#                 avg_brevity=Avg("sentiment_analysis__brevity"),
#                 avg_trigger_response=Avg("sentiment_analysis__trigger_response"),
#                 avg_filler_words=Avg("sentiment_analysis__filler_words"),
#                 avg_grammar=Avg("sentiment_analysis__grammar"),
#                 avg_posture=Avg("sentiment_analysis__posture"),
#                 avg_motion=Avg("sentiment_analysis__motion"),
#                 # To sum boolean gestures, explicitly cast to IntegerField before summing
#                 total_true_gestures=Sum(Cast('sentiment_analysis__gestures', output_field=IntegerField())),
#                 # Count the number of chunks considered for aggregation
#                 total_chunks_for_aggregation=Count('sentiment_analysis__conviction'), # Use Count on a non-nullable field
#                 avg_transformative_potential=Avg("sentiment_analysis__transformative_potential"),
#             )

#             print(f"Raw aggregation results: {aggregation_results}")

#             # --- Calculate Derived Fields and Prepare Data for Saving/Response ---
#             # Use .get with a default value (0 or 0.0) and check for None explicitly
#             def get_agg_value(key, default):
#                 value = aggregation_results.get(key, default)
#                 return value if value is not None else default

#             volume = get_agg_value("avg_volume", 0.0)
#             pitch_variability = get_agg_value("avg_pitch_variability", 0.0)
#             pace = get_agg_value("avg_pace", 0.0)
#             pauses = get_agg_value("total_pauses", 0) # This is a sum (integer)
#             conviction = get_agg_value("avg_conviction", 0.0)
#             clarity = get_agg_value("avg_clarity", 0.0)
#             impact = get_agg_value("avg_impact", 0.0)
#             brevity = get_agg_value("avg_brevity", 0.0)
#             trigger_response = get_agg_value("avg_trigger_response", 0.0)
#             filler_words = get_agg_value("avg_filler_words", 0.0)
#             grammar = get_agg_value("avg_grammar", 0.0)
#             posture = get_agg_value("avg_posture", 0.0)
#             motion = get_agg_value("avg_motion", 0.0)

#             # Calculate gestures proportion manually after fetching sum and count
#             total_true_gestures = get_agg_value("total_true_gestures", 0)
#             total_chunks_for_aggregation = get_agg_value("total_chunks_for_aggregation", 0)
#             gestures_proportion = (total_true_gestures / total_chunks_for_aggregation) if total_chunks_for_aggregation > 0 else 0.0


#             transformative_potential = get_agg_value("avg_transformative_potential", 0.0)


#             # Calculate derived fields as per PracticeSession model help text and common interpretations
#             # Use helper function to avoid division by zero
#             def safe_division(numerator, denominator):
#                 return (numerator / denominator) if denominator > 0 else 0.0

#             audience_engagement = safe_division((impact + trigger_response + conviction), 3.0) # Use 3.0 for float division
#             overall_captured_impact = impact # Same as impact
#             # Assuming pauses contributes to vocal variety calculation - might need adjustment based on how pauses is scored
#             # If pauses is a count, averaging it with scores (0-100) might skew results.
#             # Consider how pauses should factor into vocal variety (e.g., rate score related to pauses)
#             # For now, keeping the sum in the average as per previous logic, but note this potential area.
#             vocal_variety = safe_division((volume + pitch_variability + pace + pauses), 4.0) # Use 4.0 for float division
#             emotional_impact = trigger_response # Same as trigger response
#             # Body language score calculation - Example: simple average of posture, motion, and gestures (represented as 0 or 100)
#             gestures_score_for_body_language = gestures_proportion * 100
#             body_language = safe_division((posture + motion + gestures_score_for_body_language), 3.0) # Use 3.0 for float division
#             transformative_communication = transformative_potential # Same as transformative potential
#             structure_and_clarity = clarity # Same as clarity
#             language_and_word_choice = safe_division((brevity + filler_words + grammar), 3.0) # Use 3.0 for float division


#             # --- Generate Full Summary using OpenAI ---
#             print("Generating full summary...")
#             full_summary_data = self.generate_full_summary(session_id)
#             strength_summary = full_summary_data.get("Strength", "N/A")
#             improvement_summary = full_summary_data.get("Area of Improvement", "N/A")
#             general_feedback = full_summary_data.get("General Feedback Summary", "N/A")

#             # --- Save Calculated Data and Summary to PracticeSession ---
#             print("Saving aggregated and summary data to PracticeSession...")
#             session.volume = round(volume if volume is not None else 0) # Ensure not saving None
#             session.pitch_variability = round(pitch_variability if pitch_variability is not None else 0)
#             session.pace = round(pace if pace is not None else 0)
#             session.pauses = round(pauses if pauses is not None else 0) # Total pauses (sum)
#             session.conviction = round(conviction if conviction is not None else 0)
#             session.clarity = round(clarity if clarity is not None else 0)
#             session.impact = round(impact if impact is not None else 0)
#             session.brevity = round(brevity if brevity is not None else 0)
#             session.trigger_response = round(trigger_response if trigger_response is not None else 0)
#             session.filler_words = round(filler_words if filler_words is not None else 0)
#             session.grammar = round(grammar if grammar is not None else 0)
#             session.posture = round(posture if posture is not None else 0)
#             session.motion = round(motion if motion is not None else 0)
#             session.transformative_potential = round(transformative_potential if transformative_potential is not None else 0)

#             # Save derived fields (FloatFields in PracticeSession)
#             session.audience_engagement = round(audience_engagement if audience_engagement is not None else 0.0, 2)
#             session.overall_captured_impact = round(overall_captured_impact if overall_captured_impact is not None else 0.0, 2)
#             session.vocal_variety = round(vocal_variety if vocal_variety is not None else 0.0, 2)
#             session.emotional_impact = round(emotional_impact if emotional_impact is not None else 0.0, 2)
#             session.body_language = round(body_language if body_language is not None else 0.0, 2)
#             session.transformative_communication = round(transformative_communication if transformative_communication is not None else 0.0, 2)
#             session.structure_and_clarity = round(structure_and_clarity if structure_and_clarity is not None else 0.0, 2)
#             session.language_and_word_choice = round(language_and_word_choice if language_and_word_choice is not None else 0.0, 2)

#             # Save boolean gestures field (True if any positive gestures were recorded)
#             session.gestures = total_true_gestures > 0 # True if sum > 0

#             # Save the text summaries
#             session.strength = strength_summary
#             session.area_of_improvement = improvement_summary
#             session.general_feedback_summary = general_feedback


#             session.save()
#             print(f"PracticeSession {session_id} updated with report data and summary.")

#             # --- Prepare Response ---
#             # You can include the calculated aggregated data and summary in the response
#             report_response_data = {
#                 "session_id": session.id,
#                 "session_name": session.session_name,
#                 "duration": str(session.duration) if session.duration else None,
#                 "aggregated_scores": {
#                     "volume": session.volume,
#                     "pitch_variability": session.pitch_variability,
#                     "pace": session.pace,
#                     "pauses": session.pauses, # Total pauses
#                     "conviction": session.conviction,
#                     "clarity": session.clarity,
#                     "impact": session.impact,
#                     "brevity": session.brevity,
#                     "trigger_response": session.trigger_response,
#                     "filler_words": session.filler_words,
#                     "grammar": session.grammar,
#                     "posture": session.posture,
#                     "motion": session.motion,
#                     "transformative_potential": session.transformative_potential,
#                     # Include gestures as boolean and percentage in response
#                     "gestures_present": session.gestures, # Boolean from session model
#                     "gestures_percentage": round(gestures_proportion * 100, 2) # Percentage calculated
#                 },
#                  "derived_scores": {
#                     "audience_engagement": session.audience_engagement,
#                     "overall_captured_impact": session.overall_captured_impact,
#                     "vocal_variety": session.vocal_variety,
#                     "emotional_impact": session.emotional_impact,
#                     "body_language": session.body_language,
#                     "transformative_communication": session.transformative_communication,
#                     "structure_and_clarity": session.structure_and_clarity,
#                     "language_and_word_choice": session.language_and_word_choice,
#                  },
#                 "full_summary": {
#                     "Strength": session.strength,
#                     "Area of Improvement": session.area_of_improvement,
#                     "General Feedback Summary": session.general_feedback_summary,
#                 },
#                  # Include graph_data if you still need it in the response, you would need to fetch it separately here
#                  # "graph_data": ... (Perhaps fetch chunks_with_sentiment and serialize minimal data)
#             }


#             print(f"Report generation and summary complete for session ID: {session_id}")
#             return Response(report_response_data, status=status.HTTP_200_OK)

#         except PracticeSession.DoesNotExist:
#             print(f"PracticeSession with ID {session_id} not found.")
#             return Response(
#                 {"error": "PracticeSession not found"}, status=status.HTTP_404_NOT_FOUND
#             )
#         except Exception as e:
#             print(f"An unexpected error occurred during report generation: {e}")
#             traceback.print_exc() # Print traceback for detailed error logging
#             return Response(
#                 {"error": "An error occurred during report generation.", "details": str(e)}, # Include error details in response
#                 status=status.HTTP_500_INTERNAL_SERVER_ERROR,
#             )



class SessionReportView(APIView):
    permission_classes = [IsAuthenticated]

    def generate_full_summary(self, session_id):
        """Creates a cohesive summary for Strengths, Improvements, and Feedback using OpenAI."""
        client = OpenAI(api_key=settings.OPENAI_API_KEY)

        # Retrieve all general feedback summaries for the session's chunks
        general_feedback_summaries = ChunkSentimentAnalysis.objects.filter(
            chunk__session__id=session_id
        ).values_list("general_feedback_summary", flat=True)

        combined_feedback = " ".join([g for g in general_feedback_summaries if g])

        # If there's no feedback, return default values
        if not combined_feedback.strip():
            print("No feedback available from chunks to generate summary.")
            return {
                "Strength": "N/A - No feedback available.",
                "Area of Improvement": "N/A - No feedback available.",
                "General Feedback Summary": "No feedback was generated for the chunks in this session.",
            }

        prompt = f"""
        Using the presentation evaluation data provided, generate a structured JSON response with the following three components:
        Strengths: List the speaker’s top strengths based on their delivery, clarity, and audience engagement. Format the output as a Python string representing a list, with each of the 3 strengths as points separated by a full stop with the quotes outside the list brackets (e.g., "[Strength 1. Strength 2. Strength 3]").
        Areas for Improvement: Provide 3 specific, actionable suggestions to help the speaker enhance their performance. Format the output as a Python string representing a list, with each of the 3 area of improvement points separated by a full stop with the quotes outside the list brackets (e.g., "[Area of Improvement 1. Area of Improvement 2. Area of Improvement 3]").

        General Feedback Summary: Write a concise paragraph summarizing the overall effectiveness of the presentation, balancing both positive observations and constructive feedback.

        Data to analyze:
        {combined_feedback}
        """

        try:
            print("Calling OpenAI for summary generation...")
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
                                "General Feedback Summary":{"type": "string"},
                            },
                            "required": ["Strength", "Area of Improvement", "General Feedback Summary"],
                    }
                }
                },
                temperature=0.7, # Adjust temperature as needed
                max_tokens=500 # Limit tokens to control response length
            )

            refined_summary = completion.choices[0].message.content
            print(f"OpenAI raw response: {refined_summary}")
            parsed_summary = json.loads(refined_summary)
            print(f"Parsed summary: {parsed_summary}")
            return parsed_summary

        except json.JSONDecodeError as e:
             print(f"Error decoding JSON from OpenAI response: {e}")
             print(f"Faulty JSON content: {refined_summary}")
             # Fallback in case of JSON decoding error
             return {
                 "Strength": "N/A - Error generating detailed summary.",
                 "Area of Improvement": "N/A - Error generating detailed summary.",
                 "General Feedback Summary": f"Error processing AI summary. Raw feedback: {combined_feedback}",
              }
        except Exception as e:
            print(f"Error generating summary with OpenAI: {e}")
            # Fallback in case of any other OpenAI error
            return {
                "Strength": "N/A - Error generating detailed summary.",
                "Area of Improvement": "N/A - Error generating detailed summary.",
                "General Feedback Summary": f"Error processing AI summary. Raw feedback: {combined_feedback}",
             }


    def get(self, request, session_id):
        # Keep the GET method as is, it just returns the session details
        try:
            session = PracticeSession.objects.get(id=session_id, user=request.user)
            session_serializer = PracticeSessionSerializer(session)
            return Response(session_serializer.data, status=status.HTTP_200_OK)
        except PracticeSession.DoesNotExist:
            return Response(
                {"error": "Session not found"}, status=status.HTTP_404_NOT_FOUND
            )

    @swagger_auto_schema(
        operation_description="update the session duration, calculate report, and generate summary",
        request_body=SessionReportSerializer,
        responses={},
    )
    
    def post(self, request, session_id):
        print(f"Starting report generation and summary for session ID: {session_id}")
        duration_seconds = request.data.get("duration")

        try:
            session = get_object_or_404(PracticeSession, id=session_id, user=request.user)
            print(f"Session found: {session.session_name}")

            # --- Update Duration ---
            if duration_seconds is not None:
                try:
                    duration_seconds_int = int(duration_seconds)
                    session.duration = timedelta(seconds=duration_seconds_int)
                    session.save(update_fields=['duration'])
                    print(f"Session duration updated to: {session.duration}")
                except ValueError:
                    print(f"Invalid duration value received: {duration_seconds}")
                except Exception as e:
                    print(f"Error saving duration: {e}")

            # --- Aggregate Chunk Sentiment Analysis Data ---
            print("Aggregating chunk sentiment analysis data...")
            # Get chunks with sentiment analysis data
            chunks_with_sentiment = session.chunks.filter(
                sentiment_analysis__isnull=False
            )
            print(f"Number of chunks with sentiment analysis found: {chunks_with_sentiment.count()}")

            # If no chunks with sentiment analysis, return a basic report
            if not chunks_with_sentiment.exists():
                 print("No chunks with sentiment analysis found. Returning basic report.")
                 # You might want to populate session with default N/A or 0 values here
                 # session.volume = 0 # etc.
                 # session.strength = "No analysis data available." # etc.
                 # session.save() # Save defaults if needed

                 return Response({
                     "session_id": session.id,
                     "session_name": session.session_name,
                     "duration": str(session.duration) if session.duration else None,
                     "aggregated_scores": {}, # Empty or default values
                     "derived_scores": {}, # Empty or default values
                     "full_summary": {
                         "Strength": "No analysis data available for summary.",
                         "Area of Improvement": "N/A - No analysis data available for summary.",
                         "General Feedback Summary": "No analysis data was generated for this session's chunks.",
                     },
                     "gestures_percentage": 0.0
                     # No graph_data if you removed it from the response
                 }, status=status.HTTP_200_OK)


            aggregation_results = chunks_with_sentiment.aggregate(
                # Aggregate individual metrics
                avg_volume=Ceil(Avg("sentiment_analysis__volume")),
                avg_pitch_variability=Ceil(Avg("sentiment_analysis__pitch_variability")),
                avg_pace=Ceil(Avg("sentiment_analysis__pace")),
                avg_conviction=Ceil(Avg("sentiment_analysis__conviction")),
                avg_clarity=Ceil(Avg("sentiment_analysis__clarity")),
                avg_impact=Ceil(Avg("sentiment_analysis__impact")),
                avg_brevity=Ceil(Avg("sentiment_analysis__brevity")),
                avg_trigger_response=Ceil(Avg("sentiment_analysis__trigger_response")),
                avg_filler_words=Ceil(Avg("sentiment_analysis__filler_words")),
                avg_grammar=Ceil(Avg("sentiment_analysis__grammar")),
                avg_posture=Ceil(Avg("sentiment_analysis__posture")),
                avg_motion=Ceil(Avg("sentiment_analysis__motion")),
                # avg_volume=Avg("sentiment_analysis__volume"),
                # savg_pitch_variability=Avg("sentiment_analysis__pitch_variability"),
                # avg_pace=Avg("sentiment_analysis__pace"),
                avg_pauses=Ceil(Avg("sentiment_analysis__pauses")), # Use Avg for aggregated pauses
                # avg_conviction=Avg("sentiment_analysis__conviction"),
                # avg_clarity=Avg("sentiment_analysis__clarity"),
                # avg_impact=Avg("sentiment_analysis__impact"),
                # avg_brevity=Avg("sentiment_analysis__brevity"),
                # avg_trigger_response=Avg("sentiment_analysis__trigger_response"),
                # avg_filler_words=Avg("sentiment_analysis__filler_words"),
                # avg_grammar=Avg("sentiment_analysis__grammar"),
                # avg_posture=Avg("sentiment_analysis__posture"),
                # avg_motion=Avg("sentiment_analysis__motion"),
                # To sum boolean gestures, explicitly cast to IntegerField before summing
                total_true_gestures=Ceil(Sum(Cast('sentiment_analysis__gestures', output_field=IntegerField()))),
                # Count the number of chunks considered for aggregation
                total_chunks_for_aggregation=Count('sentiment_analysis__conviction'), # Use Count on a non-nullable field
                avg_transformative_potential=Ceil(Avg("sentiment_analysis__transformative_potential")),
            )

            print(f"Raw aggregation results: {aggregation_results}")

            # --- Calculate Derived Fields and Prepare Data for Saving/Response ---
            # Use .get with a default value (0 or 0.0) and check for None explicitly
            def get_agg_value(key, default):
                value = aggregation_results.get(key, default)
                return value if value is not None else default

            volume = get_agg_value("avg_volume", 0.0)
            pitch_variability = get_agg_value("avg_pitch_variability", 0.0)
            pace = get_agg_value("avg_pace", 0.0)
            pauses_average = get_agg_value("avg_pauses", 0.0) # <-- Get the average pauses (expected to be over 100)
            conviction = get_agg_value("avg_conviction", 0.0)
            clarity = get_agg_value("avg_clarity", 0.0)
            impact = get_agg_value("avg_impact", 0.0)
            brevity = get_agg_value("avg_brevity", 0.0)
            trigger_response = get_agg_value("avg_trigger_response", 0.0)
            filler_words = get_agg_value("avg_filler_words", 0.0)
            grammar = get_agg_value("avg_grammar", 0.0)
            posture = get_agg_value("avg_posture", 0.0)
            motion = get_agg_value("avg_motion", 0.0)

            # Calculate gestures proportion manually after fetching sum and count
            total_true_gestures = get_agg_value("total_true_gestures", 0)
            total_chunks_for_aggregation = get_agg_value("total_chunks_for_aggregation", 0)
            gestures_proportion = (total_true_gestures / total_chunks_for_aggregation) if total_chunks_for_aggregation > 0 else 0.0

            transformative_potential = get_agg_value("avg_transformative_potential", 0.0)

            # Calculate derived fields as per PracticeSession model help text and common interpretations
            # Use helper function to avoid division by zero
            def safe_division(numerator, denominator):
                return (numerator / denominator) if denominator > 0 else 0.0

            audience_engagement = safe_division((impact + trigger_response + conviction), 3.0) # Use 3.0 for float division
            overall_captured_impact = impact # Same as impact
            vocal_variety = safe_division((volume + pitch_variability + pace + pauses_average), 4.0) # <-- Use the average here

            emotional_impact = trigger_response # Same as trigger response
            # Body language score calculation - Example: simple average of posture, motion, and gestures (represented as 0 or 100)
            gestures_score_for_body_language = gestures_proportion * 100
            body_language = safe_division((posture + motion + gestures_score_for_body_language), 3.0) # Use 3.0 for float division
            transformative_communication = transformative_potential # Same as transformative potential
            structure_and_clarity = clarity # Same as clarity
            language_and_word_choice = safe_division((brevity + filler_words + grammar), 3.0) # Use 3.0 for float division


            # --- Generate Full Summary using OpenAI ---
            print("Generating full summary...")
            full_summary_data = self.generate_full_summary(session_id)
            strength_summary = full_summary_data.get("Strength", "N/A")
            improvement_summary = full_summary_data.get("Area of Improvement", "N/A")
            general_feedback = full_summary_data.get("General Feedback Summary", "N/A")

            # --- Save Calculated Data and Summary to PracticeSession ---
            print("Saving aggregated and summary data to PracticeSession...")
            session.volume = round(volume if volume is not None else 0) # Ensure not saving None
            session.pitch_variability = round(pitch_variability if pitch_variability is not None else 0)
            session.pace = round(pace if pace is not None else 0)
            session.pauses = round(pauses_average if pauses_average is not None else 0) # Save the AVERAGE here
            session.conviction = round(conviction if conviction is not None else 0)
            session.clarity = round(clarity if clarity is not None else 0)
            session.impact = round(impact if impact is not None else 0)
            session.brevity = round(brevity if brevity is not None else 0)
            session.trigger_response = round(trigger_response if trigger_response is not None else 0)
            session.filler_words = round(filler_words if filler_words is not None else 0)
            session.grammar = round(grammar if grammar is not None else 0)
            session.posture = round(posture if posture is not None else 0)
            session.motion = round(motion if motion is not None else 0)
            session.transformative_potential = round(transformative_potential if transformative_potential is not None else 0)

            # Save derived fields (FloatFields in PracticeSession)
            session.audience_engagement = round(audience_engagement if audience_engagement is not None else 0.0, 2)
            session.overall_captured_impact = round(overall_captured_impact if overall_captured_impact is not None else 0.0, 2)
            session.vocal_variety = round(vocal_variety if vocal_variety is not None else 0.0, 2)
            session.emotional_impact = round(emotional_impact if emotional_impact is not None else 0.0, 2)
            session.body_language = round(body_language if body_language is not None else 0.0, 2)
            session.transformative_communication = round(transformative_communication if transformative_communication is not None else 0.0, 2)
            session.structure_and_clarity = round(structure_and_clarity if structure_and_clarity is not None else 0.0, 2)
            session.language_and_word_choice = round(language_and_word_choice if language_and_word_choice is not None else 0.0, 2)

            # Save boolean gestures field (True if any positive gestures were recorded)
            session.gestures = total_true_gestures > 0 # True if sum > 0

            # Save the text summaries
            session.strength = strength_summary
            session.area_of_improvement = improvement_summary
            session.general_feedback_summary = general_feedback


            session.save()
            print(f"PracticeSession {session_id} updated with report data and summary.")

            # --- Prepare Response ---
            # You can include the calculated aggregated data and summary in the response
            report_response_data = {
                "session_id": session.id,
                "session_name": session.session_name,
                "duration": str(session.duration) if session.duration else None,
                "aggregated_scores": {
                    "volume": session.volume,
                    "pitch_variability": session.pitch_variability,
                    "pace": session.pace,
                    "pauses": session.pauses, # Return the AVERAGE here (stored in session.pauses)
                    "conviction": session.conviction,
                    "clarity": session.clarity,
                    "impact": session.impact,
                    "brevity": session.brevity,
                    "trigger_response": session.trigger_response,
                    "filler_words": session.filler_words,
                    "grammar": session.grammar,
                    "posture": session.posture,
                    "motion": session.motion,
                    "transformative_potential": session.transformative_potential,
                    "gestures_present": session.gestures, # Boolean from session model
                },
                 "derived_scores": {
                    "audience_engagement": session.audience_engagement,
                    "overall_captured_impact": session.overall_captured_impact,
                    "vocal_variety": session.vocal_variety,
                    "emotional_impact": session.emotional_impact,
                    "body_language": session.body_language,
                    "transformative_communication": session.transformative_communication,
                    "structure_and_clarity": session.structure_and_clarity,
                    "language_and_word_choice": session.language_and_word_choice,
                 },
                "full_summary": {
                    "Strength": session.strength,
                    "Area of Improvement": session.area_of_improvement,
                    "General Feedback Summary": session.general_feedback_summary,
                },
                 # Include graph_data if you still need it in the response, you would need to fetch it separately here
                 # "graph_data": ... (Perhaps fetch chunks_with_sentiment and serialize minimal data)
            }


            print(f"Report generation and summary complete for session ID: {session_id}")
            return Response(report_response_data, status=status.HTTP_200_OK)

        except PracticeSession.DoesNotExist:
            print(f"PracticeSession with ID {session_id} not found.")
            return Response(
                {"error": "PracticeSession not found"}, status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            print(f"An unexpected error occurred during report generation: {e}")
            traceback.print_exc() # Print traceback for detailed error logging
            return Response(
                {"error": "An error occurred during report generation.", "details": str(e)}, # Include error details in response
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

class PerformanceAnalyticsView(APIView):
    def get(self, request):
        user = request.user
        session = PracticeSession.objects.filter(user=user)

        chunk = ChunkSentimentAnalysis.objects.select_related("chunk__session").all()
        card_data = session.aggregate(
            speaking_time=Sum("duration"),
            total_session=Count("id"),
            impact=Avg("impact"),
            vocal_variety=Avg("vocal_variety"),
        )
        # Convert timedelta to HH:MM:SS
        if card_data["speaking_time"]:
            card_data["speaking_time"] = str(card_data["speaking_time"])
        recent_data = (
            session.annotate(
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
                "impact",
            )
        )
        graph_data = (
            ChunkSentimentAnalysis.objects.select_related("chunk__session")
            .all()
            .annotate(
                # month=TruncMonth("chunk__session__date"),
                day=TruncDay("chunk__session__date"),
            )
            .values("day")
            .annotate(
                clarity=Sum("chunk__session__clarity"),
                impact=Sum("chunk__session__impact"),
                audience_engagement=Sum("chunk__session__audience_engagement"),
            )
            .order_by("day")
        )

        result = (
            {
                "month": item["day"],
                "clarity": item["clarity"] or 0,
                "impact": item["impact"] or 0,
                "audience_engagement": item["audience_engagement"] or 0,
            }
            for item in graph_data
        )
        data = {
            "overview_card": dict(card_data),
            "recent_session": list(recent_data),
            "graph_data": result,
        }
        return Response(data)


class SequenceListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        sequences = PracticeSequence.objects.filter(user=request.user)
        sequence_serializer = PracticeSequenceSerializer(sequences, many=True)
        return Response({"sequences": sequence_serializer.data})


class SessionList(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        sessions = PracticeSession.objects.filter(user=request.user).order_by("-date")
        session_serializer = PracticeSessionSerializer(sessions, many=True)
        return Response({"sessions": session_serializer.data})


class CompareSessionsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, session1_id, session2_id):
        session1 = get_object_or_404(PracticeSession, id=session1_id, user=request.user)
        session2 = get_object_or_404(PracticeSession, id=session2_id, user=request.user)
        print(session1)
        print(session2)
        session1_serialized = PracticeSessionSerializer(session1).data
        session2_serialized = PracticeSessionSerializer(session2).data

        data = {
            "session1": session1_serialized,
            "session2": session2_serialized,
        }

        # def get_session_data(session):
        #     return {}

        # data = {
        #     "session1": get_session_data(session1),
        #     "session2": get_session_data(session2),
        # }

        return Response(data)
        # def get_avg_metrics(session_id):
        #     analyses = ChunkSentimentAnalysis.objects.filter(
        #         chunk__session__id=session_id,
        #     )
        #     count = analyses.count()
        #     print(analyses)
        #     print(count)
        #     if count == 0:
        #         return {}

        #     return {
        #         "brevity": sum(a.brevity for a in analyses) / count,
        #         "impact": sum(a.impact for a in analyses) / count,
        #         "conviction": sum(a.conviction for a in analyses) / count,
        #         "clarity": sum(a.clarity for a in analyses) / count,
        #         "transformative_potential": sum(
        #             a.transformative_potential for a in analyses
        #         )
        #         / count,
        #     }

        # session1_metrics = get_avg_metrics(session1_id)
        # session2_metrics = get_avg_metrics(session2_id)
        # return Response(
        #     {
        #         "session1": {
        #             "id": session1_id,
        #             "metrics": session1_metrics,
        #         },
        #         "session2": {
        #             "id": session2_id,
        #             "metrics": session2_metrics,
        #         },
        #     }
        # )


class GoalAchievementView(APIView):

    def get(self, request):
        user = request.user
        goals = defaultdict(int)

        fields = [
            "vocal_variety",
            "body_language",
            "structure_and_clarity",
            "overall_captured_impact",
            "transformative_communication",
            "language_and_word_choice",
            "emotional_impact",
            "audience_engagement",
        ]
        sessions = PracticeSession.objects.filter(user=user)

        for session in sessions:
            for field in fields:
                value = getattr(session, field, 0)
                if value >= 80:
                    goals[field] += 1
                else:
                    goals[field] += 0

        return Response(dict(goals))
