import base64
import concurrent.futures
import openai
import os
import json
import traceback
import boto3
import requests
from django.core.files import File

from rest_framework import viewsets, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.exceptions import PermissionDenied

from django.core.files.uploadedfile import UploadedFile
from django.db.models.functions import Round
from django.conf import settings
from django.db.models import (Count, Avg, Case, When, Value, CharField, Sum, IntegerField, Q,
                              ExpressionWrapper, FloatField,
                              )
from django.utils.timezone import now
from django.shortcuts import get_object_or_404
from django.contrib.auth import get_user_model
from django.db.models.functions import Cast, TruncMonth, TruncDay
from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse

from requests import session
from datetime import timedelta
from datetime import datetime, timedelta
from collections import Counter
from openai import OpenAI
from drf_yasg.utils import swagger_auto_schema
from collections import defaultdict
from botocore.exceptions import NoCredentialsError, PartialCredentialsError, ClientError
from itertools import chain

from .models import (
    PracticeSession,
    PracticeSequence,
    ChunkSentimentAnalysis,
    SessionChunk,
    SlidePreview
)
from .serializers import (
    PracticeSessionSerializer,
    PracticeSessionSlidesSerializer,
    PracticeSequenceSerializer,
    ChunkSentimentAnalysisSerializer,
    SessionChunkSerializer,
    SessionReportSerializer,
    SlidePreviewSerializer
)

User = get_user_model()


# def generate_full_summary(self, session_id):
#     """Creates a cohesive summary for Strengths, Improvements, and Feedback."""
#     client = OpenAI(api_key=settings.OPENAI_API_KEY)

#     general_feedback_summary = ChunkSentimentAnalysis.objects.filter(
#         chunk__session__id=session_id
#     ).values_list("general_feedback_summary", flat=True)

#     combined_feedback = " ".join([g for g in general_feedback_summary if g])

#     # get strenghts and areas of improvements
#     # grade content_organisation (0-100), from transcript

#     prompt = f"""
#         Using the following presentation evaluation data, provide a structured JSON response containing three key elements:

#         1. **Strength**: Identify the speaker’s most notable strengths based on their delivery, clarity, and engagement.
#         2. **Area of Improvement**: Provide actionable and specific recommendations for improving the speaker’s performance.
#         3. **General Feedback Summary**: Summarize the presentation’s overall effectiveness, balancing positive feedback with constructive advice.

#         Data to analyze:
#         {combined_feedback}
#         """

#     try:
#         completion = client.chat.completions.create(
#             model="gpt-4o-mini",
#             messages=[{"role": "user", "content": prompt}],
#             response_format={
#                 "type": "json_schema",
#                 "json_schema": {
#                     "name": "Feedback",
#                     "schema": {
#                         "type": "object",
#                         "properties": {
#                             "Strength": {"type": "string"},
#                             "Area of Improvement": {"type": "string"},
#                             "General Feedback Summary": {"type": "string"},
#                         },
#                     },
#                 },
#             },
#         )

#         refined_summary = completion.choices[0].message.content
#         parsed_summary = json.loads(refined_summary)

#     except Exception as e:
#         print(f"Error generating summary: {e}")
#         parsed_data = {
#             "Strength": "N/A",
#             "Area of Improvement": "N/A",
#             "General Feedback Summary": combined_feedback,
#         }
#     return parsed_summary

@csrf_exempt
def get_openai_realtime_token(request):
    headers = {
        "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": "gpt-4o-realtime-preview",
        "modalities": ["text"],  # Only return text, not audio
        "instructions": """You are an advanced presentation evaluation system. Using the speaker's transcript.

Select one of these emotions that the audience is feeling most strongly ONLY choose from this list(thinking, empathy, excitement, laughter, surprise, interested).

Take into account what came before each entry but prioritize the most recent entry. Respond only with the emotion.""",
        "turn_detection": {
            "type": "server_vad",  # Use Server VAD
            "silence_duration_ms": 70  # 100ms silence threshold
        }
    }

    response = requests.post(
        "https://api.openai.com/v1/realtime/sessions",
        headers=headers,
        json=payload
    )

    return JsonResponse(response.json(), status=response.status_code)


def generate_slide_summary(pdf_path):
    client = OpenAI(api_key=settings.OPENAI_API_KEY)
    base64_pdf = None
    # STEP 2: Read and encode the PDF as Base64
    if isinstance(pdf_path, (str, bytes, os.PathLike)):
        with open(pdf_path, 'rb') as file:
            pdf_bytes = file.read()
            base64_pdf = base64.b64encode(pdf_bytes).decode('utf-8')
    elif isinstance(pdf_path, UploadedFile):
        pdf_bytes = pdf_path.read()
        base64_pdf = base64.b64encode(pdf_bytes).decode('utf-8')
        pdf_path.seek(0)

        # STEP 3: Construct your evaluation prompt
    prompt = """
        You are a presentation evaluator. Review the attached presentation and score it on:

        1. *Slide Efficiency*: Are too many slides used to deliver simple points?
        2. *Text Economy*: Is the presentation light on text per slide?
        3. *Visual Communication*: Is there a strong use of images, diagrams, or design elements?

        Give each a score from 1 (poor) to 100 (excellent).
        """

    # STEP 4: Make the completion call using the file and structured JSON schema
    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "file",
                        "file": {
                            "file_data": f"data:application/pdf;base64,{base64_pdf}",
                            "filename": "uploaded_document.pdf"
                        }
                    },
                    {
                        "type": "text",
                        "text": prompt
                    }
                ]
            }
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "PresentationEvaluation",
                "schema": {
                    "type": "object",
                    "properties": {
                        "SlideEfficiency": {"type": "number"},
                        "TextEconomy": {"type": "number"},
                        "VisualCommunication": {"type": "number"},
                    },
                    "required": [
                        "SlideEfficiency",
                        "TextEconomy",
                        "VisualCommunication",
                    ]
                }
            }
        }
    )

    # STEP 5: Unpack and print the response
    result = json.loads(response.choices[0].message.content)

    print("\n✅ Evaluation Results:")
    print(f"Slide Efficiency: {result['SlideEfficiency']}/100")
    print(f"Text Economy: {result['TextEconomy']}/100")
    print(f"Visual Communication: {result['VisualCommunication']}/100")

    return result


def format_timedelta_12h(td):
    # Get the total seconds from the timedelta
    total_seconds = int(td.total_seconds())

    # Calculate hours, minutes, and seconds
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    # If hours are less than 12, we keep it as 00:xx:xx format
    if hours >= 12:
        hours = hours % 12  # Convert to 12-hour clock
        if hours == 0:
            hours = 12  # If hours % 12 is 0, show as 12 (since 12:xx:xx is correct for noon/midnight)

    # If the hours are less than 12, we leave the format as is (00:xx:xx)
    return f"{hours:02}:{minutes:02}:{seconds:02}"


import tempfile
import subprocess
import platform
import time


def get_soffice_path():
    system = platform.system()

    if system == "Darwin":  # macOS
        return "/Applications/LibreOffice.app/Contents/MacOS/soffice"
    elif system == "Windows":
        # Common install location on Windows
        possible_paths = [
            r"C:\Program Files\LibreOffice\program\soffice.exe",
            r"C:\Program Files (x86)\LibreOffice\program\soffice.exe"
        ]
        for path in possible_paths:
            if os.path.exists(path):
                return path
        raise FileNotFoundError("LibreOffice not found in expected Windows locations.")
    else:  # Assume Linux/Docker
        return "soffice"  # Must be in PATH


def convert_pptx_to_pdf(pptx_file):
    soffice_path = get_soffice_path()

    with tempfile.NamedTemporaryFile(suffix='.pptx', delete=False) as temp_pptx:
        for chunk in pptx_file.chunks():
            temp_pptx.write(chunk)
        temp_pptx_path = temp_pptx.name

    output_dir = tempfile.gettempdir()

    result = subprocess.run(
        [
            soffice_path,
            "--headless",
            "--convert-to", "pdf",
            "--outdir", output_dir,
            temp_pptx_path
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    # Debug output
    print("LibreOffice stdout:\n", result.stdout)
    print("LibreOffice stderr:\n", result.stderr)

    # if result.returncode != 0:
    
    #     print(f"LibreOffice failed with exit code {result.returncode}")
    #     raise RuntimeError(f"LibreOffice failed with exit code {result.returncode}")

    # Wait to ensure file is written before returning
    time.sleep(0.5)

    pdf_path = os.path.join(output_dir, os.path.basename(temp_pptx_path).replace(".pptx", ".pdf"))
    if not os.path.exists(pdf_path):
        print(f"Expected output PDF not found at: {pdf_path}")
        raise FileNotFoundError(f"Expected output PDF not found at: {pdf_path}")

    return pdf_path


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

        if hasattr(user, "user_profile") and user.user_profile.is_admin():
            return PracticeSession.objects.all().order_by("-date")

        return PracticeSession.objects.filter(user=user).order_by("-date")

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


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
        today = now().date()
        yesterday = today - timedelta(days=1)

        start_date_str = request.query_params.get("start_date")
        end_date_str = request.query_params.get("end_date")

        if hasattr(user, "user_profile") and user.user_profile.is_admin():
            sessions = PracticeSession.objects.all()

            if start_date_str and end_date_str:
                parsed_start = datetime.strptime(start_date_str, "%Y-%m-%d").date()
                parsed_end = datetime.strptime(end_date_str, "%Y-%m-%d").date()

                interval_length = (parsed_end - parsed_start).days + 1
                prev_start_date = parsed_start - timedelta(days=interval_length)
                prev_end_date = parsed_end - timedelta(days=interval_length)

                filtered_sessions = sessions.filter(date__date__range=(parsed_start, parsed_end))
                previous_sessions = sessions.filter(date__date__range=(prev_start_date, prev_end_date))
            else:
                filtered_sessions = sessions.filter(date__date=today)
                previous_sessions = sessions.filter(date__date=yesterday)

            filtered_sessions_count = filtered_sessions.count()
            previous_sessions_count = previous_sessions.count()

            total_session_diff = self.calculate_percentage_difference(filtered_sessions_count, previous_sessions_count)

            # All session types we expect
            session_types = {
                "pitch": "Pitch Practice",
                "public": "Public Speaking",
                "presentation": "Presentation",
            }

            # Current breakdown
            current_breakdown = filtered_sessions.values("session_type").annotate(
                count=Count("id")
            )

            # Previous breakdown
            previous_breakdown = previous_sessions.values("session_type").annotate(
                count=Count("id")
            )

            # Convert previous breakdown to a dictionary
            previous_counts = {entry["session_type"]: entry["count"] for entry in previous_breakdown}

            # Build final breakdown
            breakdown_with_difference = [
                {
                    "total_new_session": filtered_sessions_count,
                    "previous_total_sessions": previous_sessions_count,
                    "percentage_difference": total_session_diff,
                }
            ]

            current_counts = {entry["session_type"]: entry["count"] for entry in current_breakdown}

            for key, label in session_types.items():
                current_count = current_counts.get(key, 0)
                previous_count = previous_counts.get(key, 0)
                percentage_diff = self.calculate_percentage_difference(current_count, previous_count)

                breakdown_with_difference.append({
                    "session_type": label,
                    "current_count": current_count,
                    "previous_count": previous_count,
                    "percentage_difference": percentage_diff,
                })

            # Sessions over time
            sessions_over_time = (
                filtered_sessions.extra(select={"day": "date(date)"})
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

            # Recent sessions
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
                    "id", "session_name", "session_type_display", "date", "formatted_duration",
                )
            )

            # User growth and activity
            today_new_users_count = User.objects.filter(date_joined__date=today).count()
            yesterday_new_users_count = User.objects.filter(date_joined__date=yesterday).count()
            user_growth_percentage_difference = self.calculate_percentage_difference(today_new_users_count,
                                                                                     yesterday_new_users_count)

            active_users_count = PracticeSession.objects.values("user").distinct().count()
            total_users_count = User.objects.count()
            inactive_users_count = total_users_count - active_users_count

            # Final Data
            data = {
                "session_breakdown": list(breakdown_with_difference),
                "sessions_over_time": list(sessions_over_time),
                "recent_sessions": list(recent_sessions),
                "today_new_users_count": today_new_users_count,
                "user_growth_percentage_difference": user_growth_percentage_difference,
                "active_users_count": active_users_count,
                "inactive_users_count": inactive_users_count,
            }
        else:
            latest_session = (
                PracticeSession.objects.filter(user=user).order_by("-date").first()
            )
            sessions = PracticeSession.objects.filter(user=user)

            latest_session_chunk = ChunkSentimentAnalysis.objects.filter(
                chunk__session=latest_session
            )
            print(latest_session_chunk)

            latest_session_dict = {}
            available_credit = user.user_profile.available_credits if user else 0.0
            performance_analytics_over_time = []
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
            session_type_map = {
                "presentation": "Presentation",
                "pitch": "Pitch Practice",
                "public": "Public Speaking"
            }

            if latest_session:
                latest_session_dict["session_type"] = session_type_map.get(latest_session.session_type, "")
                latest_session_dict["session_score"] = latest_session.impact
            else:
                latest_session_dict["session_type"] = ""
                latest_session_dict["session_score"] = ""

            print(latest_session_dict)

            # goals and achievment
            for session in sessions:
                for field in fields:
                    value = getattr(session, field, 0)
                    if value >= 80 and goals[field] < 10:
                        goals[field] += 1
                    else:
                        goals[field] += 0

            # performamce analytics
            print(latest_session_chunk)
            for chunk in latest_session_chunk:
                performance_analytics_over_time.append({
                    "chunk_number": chunk.chunk_number if chunk.chunk_number is not None else 0,
                    "start_time": chunk.chunk.start_time if chunk.chunk.start_time is not None else 0,
                    "end_time": chunk.chunk.end_time if chunk.chunk.end_time is not None else 0,
                    "impact": chunk.impact if chunk.impact is not None else 0,
                    "trigger_response": chunk.trigger_response if chunk.trigger_response is not None else 0,
                    "conviction": chunk.conviction if chunk.conviction is not None else 0,
                })

            data = {
                "latest_session_dict": latest_session_dict,
                "available_credit": available_credit,
                "performance_analytics": performance_analytics_over_time,
                "goals_and_achievement": dict(goals),
            }
        return Response(data, status=status.HTTP_200_OK)

    def calculate_percentage_difference(self, current_value, previous_value):
        if previous_value == 0:
            return 100.0 if current_value > 0 else 0.0
        return round(((current_value - previous_value) / previous_value) * 100, 2)


class UploadSessionSlidesView(APIView):
    """
    Endpoint to upload slides to a specific practice session, and retrieve the slide URL.
    """

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def get(self, request, pk=None):
        """
        Retrieve the URL of the slides for a specific practice session.
        Returns a pre-signed URL for S3 files if USE_S3 is True and files are not public.
        For local storage, returns the standard URL.
        """
        try:
            # Get the practice session object by its primary key
            practice_session = get_object_or_404(PracticeSession, pk=pk)
            print(practice_session.slide_preview)
            print(practice_session.slide_preview.slides_file)

            if practice_session.user != request.user:
                return Response(
                    {"message": "You do not have permission to access slides for this session."},
                    status=status.HTTP_403_FORBIDDEN,
                )

            # Check if a slides_file has been uploaded for this session
            if not practice_session.slides_file or not practice_session.slides_file.name:
                # Return a 404 or 200 with a clear message if no file is attached
                return Response(
                    {"message": "No slides available for this session."},
                    status=status.HTTP_404_NOT_FOUND  # Or status.HTTP_200_OK with {"slide_url": None}
                )

            slide_url = None
            # Determine the storage method configured and get the appropriate URL
            if settings.USE_S3:
                try:
                    s3_client = boto3.client(
                        "s3",
                        region_name=settings.AWS_S3_REGION_NAME,
                        # Consider more secure ways to handle credentials in production
                    )
                except Exception as e:
                    print(f"Error initializing S3 client: {e}")
                    return Response(
                        {"error": "Could not initialize S3 client."},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR
                    )

                try:
                    s3_key = practice_session.slides_file.name  # This is the value from the database field

                    print(f"Attempting to generate pre-signed URL for S3 key: {s3_key}")  # Log the key from .name

                    # Generate the pre-signed URL for 'get_object' operation
                    slide_url = s3_client.generate_presigned_url(
                        'get_object',
                        Params={'Bucket': settings.AWS_STORAGE_BUCKET_NAME, 'Key': s3_key},
                        ExpiresIn=3600  # URL expires in 1 hour (adjust the expiration time as needed)
                    )
                    print(f"Generated pre-signed S3 URL for key: {s3_key}")  # Log the key used to generate URL


                except (NoCredentialsError, PartialCredentialsError):
                    print("AWS credentials not found or incomplete. Cannot generate pre-signed URL.")
                    return Response(
                        {"error": "AWS credentials not configured correctly."},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR
                    )
                except ClientError as e:
                    print(f"S3 ClientError generating pre-signed URL: {e}")
                    if e.response['Error']['Code'] == '404' or e.response['Error']['Code'] == 'NoSuchKey':
                        print(
                            f"NoSuchKey error details from S3: Key attempted: {e.response['Error'].get('Key')}")  # Log the key S3 was asked for
                        return Response(
                            {"error": "Slide file not found in S3. The requested key does not exist."},
                            status=status.HTTP_404_NOT_FOUND
                        )
                    return Response(
                        {"error": f"S3 error generating slide URL: {e}"},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR
                    )
                except Exception as e:
                    print(f"Error generating pre-signed URL: {e}")
                    traceback.print_exc()
                    return Response(
                        {"error": "Could not generate slide URL due to unexpected error."},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR
                    )
            else:
                try:
                    slide_url = practice_session.slides_file.url
                    print(f"Using local storage URL: {slide_url}")
                except Exception as e:
                    print(f"Error getting local storage URL: {e}")
                    traceback.print_exc()
                    return Response(
                        {"error": "Could not retrieve local slide URL."},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR
                    )

            if slide_url:
                return Response(
                    {
                        "status": "success",
                        "message": "Slide URL retrieved successfully.",
                        "slide_url": slide_url,
                    },
                    status=status.HTTP_200_OK,
                )
            else:
                return Response(
                    {"message": "Could not retrieve slide URL."},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )

        except PracticeSession.DoesNotExist:
            return Response(
                {"error": "PracticeSession not found"}, status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            print(f"An unexpected error occurred while retrieving slide URL: {e}")
            traceback.print_exc()
            return Response(
                {"error": "An internal error occurred.", "details": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def put(self, request, pk=None):
        """
        Generate and save slide summary for a specific practice session.
        """
        try:
            practice_session = get_object_or_404(PracticeSession, pk=pk)
            print(practice_session.slide_preview)

            if practice_session.user != request.user:
                return Response(
                    {
                        "message": "You do not have permission to upload slides for this session."
                    },
                    status=status.HTTP_403_FORBIDDEN,
                )

            # Check if there is a slides file
            if not practice_session.slides_file:
                return Response(
                    {"message": "No slides file found for this session."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Get the path to the slides file
            slides_path = practice_session.slides_file
            if not slides_path.name.endswith('pdf'):
                return Response(
                    {"message": "Slides file is not a PDF."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            print('---processing pdf----')

            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(generate_slide_summary, slides_path)
                result = future.result()

            practice_session.slide_efficiency = result['SlideEfficiency']
            practice_session.text_economy = result['TextEconomy']
            practice_session.visual_communication = result['VisualCommunication']
            practice_session.save()

            # Serialize updated session
            from .serializers import PracticeSessionSerializer
            session_data = PracticeSessionSerializer(practice_session).data

            # *** CHECK THIS LOG AFTER A PUT REQUEST ***
            if practice_session.slides_file:
                print(
                    f"WS: After save in PUT, practice_session.slides_file.name is: {practice_session.slides_file.name}"
                )
            else:
                print("WS: After save in PUT, practice_session.slides_file is None.")
            # *** WHAT IS THE EXACT OUTPUT OF THIS LINE? ***

            return Response(
                {
                    "status": "success",
                    "message": "Slides uploaded and summary generated successfully.",
                    "data": session_data
                },
                status=status.HTTP_200_OK,
            )

        except PracticeSession.DoesNotExist:
            return Response(
                {"error": "PracticeSession not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        except Exception as e:
            print(f"An unexpected error occurred during slide upload for session {pk}: {e}")
            traceback.print_exc()
            return Response(
                {
                    "error": "An internal error occurred during slide upload.",
                    "details": str(e),
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
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

    def generate_full_summary(self, session_id, metrics_string):
        """Creates a cohesive summary for Strengths, Improvements, and Feedback using OpenAI."""
        client = OpenAI(api_key=settings.OPENAI_API_KEY)

        goals = PracticeSession.objects.filter(id=session_id).values_list("goals", flat=True).first()

        name = PracticeSession.objects.get(id=session_id).user.first_name
        
        role = PracticeSession.objects.get(id=session_id).user.user_profile.user_intent

        print(f"Firstname: {name}. role: {role}")

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

        if goals == '':
            goals = 'to have an impact'

        # My name is .
        prompt = f"""
            My name is {name},and my career level is {role}.
            You are my personal expert communication mentor/coach specializing in public speaking, storytelling, pitching, and presentations. Your role is to critique me referencing my speech transcript for my growth, and guide me to becoming a more impactful professional speaker for my career development.

            My goal with this presentation is {goals}. Using my provided presentation evaluation data and transcript, generate a structured JSON response with the following three components:

            1. Strengths: Identify my most impactful strengths. Focus on concrete content choices, tone, delivery techniques, and audience engagement strategies. Format the output as a Python string representing a list, also make sure not to use commas in your points as that may conflict with the list , with each strength as points separated by a comma with the quotes outside the list brackets (e.g., "[Strength 1, Strength 2]").

            2. Areas for Improvement: Provide clear, actionable, and specific feedback on where I can improve. Emphasize my delivery habits, missed emotional beats, and structural weaknesses. Format the output as a Python string representing a list, with each point separated by a comma and the quotes outside the list brackets (e.g., "[Area of Improvement 1, Area of Improvement 2, Area of Improvement 3, Area of Improvement 4]").

            3. General Feedback Summary: Craft a detailed, content-specific analysis of my presentation. Your response must be grounded in specific parts of my transcript. Include the following:
            - Evaluate the effectiveness of my opening: Was it attention-grabbing, relevant, or emotionally engaging? Did I clearly set the tone or premise for the rest of the talk?
            - Highlight specific trigger words or emotionally resonant phrases I used that effectively drove engagement, and explain how they influenced the audience.
            - List any filler words I overused (e.g., "um", "like", "you know").
            - Comment on how I used powerful or evocative language—did I evoke empathy, joy, urgency, or excitement? Did I show vulnerability or emotional relatability?
            - Analyze my tone of voice, Was it confident, warm, authoritative, enthusiastic, or inconsistent? Note any tone shifts and how they impacted audience engagement.
            - Reflect on whether my style or personal story helped make the talk more memorable.
            - Was I persuasive enough, Did I inspire action, challenge assumptions, or shift perspectives? Highlight specific techniques like storytelling, analogies, or rhetorical questions.
            - Evaluate the structure and flow of my talk. Were transitions smooth? Did I build toward a clear message or emotional climax?
            - Clearly state whether my talk was effective — and if so, effective at what specifically (e.g., persuading the audience, building trust, sparking interest).
            - Provide an overall evaluation of how well I demonstrated mastery in storytelling, public speaking, or pitching. Include tailored suggestions for improvement based on the context and audience.

            Tone: speak to me personally but professionaly like a mentor coach, critique me for my growth while referencing my transcript not my evaluation data. Don't use headers or "**" for titles, just correct me and reference my transcript. Use \n\n for line breaks between paragraphs and also start with an encouraging remark relevant to my presentation with my name.

            Evaluation data: {metrics_string}
            Transcript:
            {combined_feedback}
            """

        try:
            print("Calling OpenAI for summary generation...")
            completion = client.chat.completions.create(
                model="gpt-4o",
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
                            "required": ["Strength", "Area of Improvement", "General Feedback Summary"],
                        }
                    }
                },
                temperature=0.7,  # Adjust temperature as needed
                max_tokens=2400  # Limit tokens to control response length
            )
            print(f"prompt: {prompt}")

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
        try:
            user = request.user
            session = PracticeSession.objects.get(id=session_id, user=request.user)
            session_serializer = PracticeSessionSerializer(session)

            # Get related chunk sentiment analysis
            latest_session_chunk = ChunkSentimentAnalysis.objects.filter(
                chunk__session=session
            )

            performance_analytics_over_time = []
            company = user.user_profile.company
            print(company)

            for chunk in latest_session_chunk:
                performance_analytics_over_time.append({
                    "chunk_number": chunk.chunk_number if chunk.chunk_number is not None else 0,
                    "start_time": chunk.chunk.start_time if chunk.chunk.start_time is not None else 0,
                    "end_time": chunk.chunk.end_time if chunk.chunk.end_time is not None else 0,
                    "impact": chunk.impact if chunk.impact is not None else 0,
                    "trigger_response": chunk.trigger_response if chunk.trigger_response is not None else 0,
                    "conviction": chunk.conviction if chunk.conviction is not None else 0,
                })

            # Combine both sets of data in the response
            response_data = session_serializer.data
            response_data['company'] = company
            response_data["performance_analytics"] = performance_analytics_over_time

            return Response(response_data, status=status.HTTP_200_OK)

        except PracticeSession.DoesNotExist:
            return Response(
                {"error": "Session not found"},
                status=status.HTTP_404_NOT_FOUND
            )

    @swagger_auto_schema(
        operation_description="update the session duration, calculate report, and generate summary",
        request_body=SessionReportSerializer,
        responses={},
    )
    def post(self, request, session_id):
        print(f"Starting report generation and summary for session ID: {session_id}")
        duration_seconds = request.data.get("duration")
        slide_specific_seconds = request.data.get("slide_specific_timing")
        user = request.user
        company = user.user_profile.company
        print(company)
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

            if slide_specific_seconds is not None:
                slide_specific = {}
                for key, value in slide_specific_seconds.items():
                    try:
                        seconds = int(value)
                        td = timedelta(seconds=seconds)
                        formatted_time = format_timedelta_12h(td)
                        slide_specific[key] = formatted_time
                    except (ValueError, TypeError):
                        print(f"Invalid value for slide '{key}': {value}")
                        continue
                session.slide_specific_timing = slide_specific
                session.save()
                print(f"Session Slide updated to: {session.slide_specific_timing}")

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

                return Response({
                    "session_id": session.id,
                    "session_name": session.session_name,
                    "company":company,
                    "duration": str(session.duration) if session.duration else None,
                    "aggregated_scores": {},  # Empty or default values
                    "derived_scores": {},  # Empty or default values
                    "full_summary": {
                        "Strength": "No analysis data available for summary.",
                        "Area of Improvement": "N/A - No analysis data available for summary.",
                        "General Feedback Summary": "No analysis data was generated for this session's chunks.",
                    },
                    "gestures_percentage": 0.0
                    # No graph_data if you removed it from the response
                }, status=status.HTTP_200_OK)

            # performamce analytics
            latest_session_chunk = ChunkSentimentAnalysis.objects.filter(
                chunk__session=session
            )
            performance_analytics_over_time = []

            for chunk in latest_session_chunk:
                performance_analytics_over_time.append({
                    "chunk_number": chunk.chunk_number if chunk.chunk_number is not None else 0,
                    "start_time": chunk.chunk.start_time if chunk.chunk.start_time is not None else 0,
                    "end_time": chunk.chunk.end_time if chunk.chunk.end_time is not None else 0,
                    "impact": chunk.impact if chunk.impact is not None else 0,
                    "trigger_response": chunk.trigger_response if chunk.trigger_response is not None else 0,
                    "conviction": chunk.conviction if chunk.conviction is not None else 0,
                })
            print(performance_analytics_over_time)
            aggregation_results = chunks_with_sentiment.aggregate(
                # Aggregate individual metrics
                avg_volume=Round(Avg("sentiment_analysis__volume"), output_field=IntegerField()),
                avg_pitch_variability=Round(Avg("sentiment_analysis__pitch_variability"), output_field=IntegerField()),
                avg_pace=Round(Avg("sentiment_analysis__pace"), output_field=IntegerField()),
                avg_conviction=Round(Avg("sentiment_analysis__conviction"), output_field=IntegerField()),
                avg_clarity=Round(Avg("sentiment_analysis__clarity"), output_field=IntegerField()),
                avg_impact=Round(Avg("sentiment_analysis__impact"), output_field=IntegerField()),
                avg_brevity=Round(Avg("sentiment_analysis__brevity"), output_field=IntegerField()),
                avg_trigger_response=Round(Avg("sentiment_analysis__trigger_response"), output_field=IntegerField()),
                avg_filler_words=Round(Avg("sentiment_analysis__filler_words"), output_field=IntegerField()),
                avg_grammar=Round(Avg("sentiment_analysis__grammar"), output_field=IntegerField()),
                avg_posture=Round(Avg("sentiment_analysis__posture"), output_field=IntegerField()),
                avg_motion=Round(Avg("sentiment_analysis__motion"), output_field=IntegerField()),
                avg_pauses=Round(Avg("sentiment_analysis__pauses"), output_field=IntegerField()),
                total_true_gestures=Round(Sum(Cast('sentiment_analysis__gestures', output_field=IntegerField()))),
                # Count the number of chunks considered for aggregation
                total_chunks_for_aggregation=Count('sentiment_analysis__conviction'),
                # Use Count on a non-nullable field
                avg_transformative_potential=Round(Avg("sentiment_analysis__transformative_potential"),
                                                   output_field=IntegerField()),
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
            pauses_average = get_agg_value("avg_pauses", 0.0)  # <-- Get the average pauses (expected to be over 100)
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
            gestures_proportion = (
                    total_true_gestures / total_chunks_for_aggregation) if total_chunks_for_aggregation > 0 else 0.0

            transformative_potential = get_agg_value("avg_transformative_potential", 0.0)

            # Calculate derived fields as per PracticeSession model help text and common interpretations
            # Use helper function to avoid division by zero
            def safe_division(numerator, denominator):
                return (numerator / denominator) if denominator > 0 else 0.0

            audience_engagement = safe_division((impact + trigger_response + conviction),
                                                3.0)  # Use 3.0 for float division
            overall_captured_impact = impact  # Same as impact
            vocal_variety = safe_division((volume + pitch_variability + pace + pauses_average),
                                          4.0)  # <-- Use the average here

            emotional_impact = trigger_response  # Same as trigger response
            # Body language score calculation - Example: simple average of posture, motion, and gestures (represented as 0 or 100)
            gestures_score_for_body_language = gestures_proportion * 100
            body_language = safe_division((posture + motion + gestures_score_for_body_language),
                                          3.0)  # Use 3.0 for float division
            transformative_communication = transformative_potential  # Same as transformative potential
            structure_and_clarity = clarity  # Same as clarity
            language_and_word_choice = safe_division((brevity + filler_words + grammar),
                                                     3.0)  # Use 3.0 for float division

            # --- Save Calculated Data and Summary to PracticeSession ---
            print("Saving aggregated and summary data to PracticeSession...")
            session.volume = round(volume if volume is not None else 0)  # Ensure not saving None
            session.pitch_variability = round(pitch_variability if pitch_variability is not None else 0)
            session.pace = round(pace if pace is not None else 0)
            session.pauses = round(pauses_average if pauses_average is not None else 0)  # Save the AVERAGE here
            session.conviction = round(conviction if conviction is not None else 0)
            session.clarity = round(clarity if clarity is not None else 0)
            session.impact = round(impact if impact is not None else 0)
            session.brevity = round(brevity if brevity is not None else 0)
            session.trigger_response = round(trigger_response if trigger_response is not None else 0)
            session.filler_words = round(filler_words if filler_words is not None else 0)
            session.grammar = round(grammar if grammar is not None else 0)
            session.posture = round(posture if posture is not None else 0)
            session.motion = round(motion if motion is not None else 0)
            session.transformative_potential = round(
                transformative_potential if transformative_potential is not None else 0)

            # Save derived fields (FloatFields in PracticeSession)
            session.audience_engagement = round(audience_engagement if audience_engagement is not None else 0.0)
            session.overall_captured_impact = round(
                overall_captured_impact if overall_captured_impact is not None else 0.0)
            session.vocal_variety = round(vocal_variety if vocal_variety is not None else 0.0)
            session.emotional_impact = round(emotional_impact if emotional_impact is not None else 0.0)
            session.body_language = round(body_language if body_language is not None else 0.0)
            session.transformative_communication = round(
                transformative_communication if transformative_communication is not None else 0.0)
            session.structure_and_clarity = round(structure_and_clarity if structure_and_clarity is not None else 0.0)
            session.language_and_word_choice = round(
                language_and_word_choice if language_and_word_choice is not None else 0.0)
            session.gestures_score_for_body_language = round(
                gestures_score_for_body_language if gestures_score_for_body_language is not None else 0.0)
            # Save boolean gestures field (True if any positive gestures were recorded)
            session.gestures = total_true_gestures > 0  # True if sum > 0

            metrics_string = f"Final Scores: volume score: {session.volume}, pitch variability score: {session.pitch_variability}, pace score: {session.pace}, pauses score: {session.pauses}, conviction score: {session.conviction}, clarity score: {session.clarity}, impact score: {session.impact}, brevity score: {session.brevity}, trigger response score: {session.trigger_response}, filler words score: {session.filler_words}, grammar score: {session.grammar}, posture score: {session.posture}, motion score: {session.motion}, transformative potential score: {session.transformative_potential}, gestures score: {session.gestures_score_for_body_language}"

            # --- Generate Full Summary using OpenAI ---
            print("Generating full summary...")
            full_summary_data = self.generate_full_summary(session_id, metrics_string)
            strength_summary = full_summary_data.get("Strength", "N/A")
            improvement_summary = full_summary_data.get("Area of Improvement", "N/A")
            general_feedback = full_summary_data.get("General Feedback Summary", "N/A")

            # Save the text summaries
            session.strength = strength_summary
            session.area_of_improvement = improvement_summary
            session.general_feedback_summary = general_feedback

            session.save()
            print(f"session.strength: {session.strength}")
            print(f"session.area_of_improvement: {session.area_of_improvement}")
            print(f"PracticeSession {session_id} updated with report data and summary.")

            # --- Prepare Response ---
            # You can include the calculated aggregated data and summary in the response
            report_response_data = {
                "session_id": session.id,
                "session_name": session.session_name,
                "company": company,
                "duration": str(session.duration) if session.duration else None,
                "slide_specific_timing": session.slide_specific_timing if session.slide_specific_timing else {},
                "aggregated_scores": {
                    "volume": round(session.volume or 0),
                    "pitch_variability": round(session.pitch_variability or 0),
                    "pace": round(session.pace or 0),
                    "pauses": round(session.pauses or 0),  # Return the AVERAGE here (stored in session.pauses)
                    "conviction": round(session.conviction or 0),
                    "clarity": round(session.clarity or 0),
                    "impact": round(session.impact or 0),
                    "brevity": round(session.brevity or 0),
                    "trigger_response": round(session.trigger_response or 0),
                    "filler_words": round(session.filler_words or 0),
                    "grammar": round(session.grammar or 0),
                    "posture": round(session.posture or 0),
                    "motion": round(session.motion or 0),
                    "transformative_potential": round(session.transformative_potential or 0),
                    "gestures_present": session.gestures,  # Boolean from session model
                    "slide_efficiency": session.slide_efficiency,
                    "text_economy": session.text_economy,
                    "visual_communication": session.visual_communication
                },
                "derived_scores": {
                    "audience_engagement": round(session.audience_engagement or 0),
                    "overall_captured_impact": round(session.overall_captured_impact or 0),
                    "vocal_variety": round(session.vocal_variety or 0),
                    "emotional_impact": round(session.emotional_impact or 0),
                    "gestures_score_for_body_language": round(session.gestures_score_for_body_language or 0),
                    "body_language": round(session.body_language or 0),
                    "transformative_communication": round(session.transformative_communication or 0),
                    "structure_and_clarity": round(session.structure_and_clarity or 0),
                    "language_and_word_choice": round(session.language_and_word_choice or 0),
                },
                "full_summary": {
                    "Strength": session.strength,
                    "Area of Improvement": session.area_of_improvement,
                    "General Feedback Summary": session.general_feedback_summary,
                },
                "performance_analytics": list(performance_analytics_over_time)
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
            traceback.print_exc()  # Print traceback for detailed error logging
            return Response(
                {"error": "An error occurred during report generation.", "details": str(e)},
                # Include error details in response
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


class PerformanceMetricsComparison(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, sequence_id):
        session_metrics = (
            PracticeSession.objects
            .filter(sequence=sequence_id)
            .annotate(
                session_type_display=Case(
                    When(session_type="pitch", then=Value("Pitch Practice")),
                    When(session_type="public", then=Value("Public Speaking")),
                    When(session_type="presentation", then=Value("Presentation")),
                    default=Value("Unknown"),
                    output_field=CharField(),
                )
            )
            .values(
                "id",
                "session_type",
                "vocal_variety",
                "body_language",
                "audience_engagement",
                "filler_words",
                "emotional_impact",
                "transformative_communication",
                "structure_and_clarity",
                "language_and_word_choice"
            )
        )
        return Response(session_metrics)


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
        return Response(data)


class GoalAchievementView(APIView):

    def get(self, request):
        user = request.user
        goals = defaultdict(int)

        fields = [
            "vocal_variety",
            "body_language",
            "gestures_score_for_body_language",
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
                if value >= 80 and goals[field] < 10:
                    goals[field] += 1
                else:
                    goals[field] += 0

        return Response(dict(goals))


class ImproveNewSequence(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_description="",
        request_body=PracticeSequenceSerializer,
        responses={},
    )
    def post(self, request, session_id):
        sequence_serializer = PracticeSequenceSerializer(data=request.data)
        if sequence_serializer.is_valid():
            sequence = sequence_serializer.save(user=request.user)

            try:
                session = PracticeSession.objects.get(id=session_id)
                if session.sequence:
                    return Response(data={"error": "Session already in a seqeunce"}, status=404)
            except PracticeSession.DoesNotExist:
                return Response({"error": "Session not found"}, status=404)

            session.sequence = sequence
            session.save()

            # Step 4: Return session details in the response
            session_serializer = PracticeSessionSerializer(session)  # Assuming you have a session serializer
            return Response({
                "message": "Sequence created and session added successfully",
                "session": session_serializer.data  # Returning the session data
            }, status=status.HTTP_201_CREATED)

        return Response(sequence_serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def get(self, request):
        # Retrieve all sessions for the current user that don't have an associated sequence
        sessions = PracticeSession.objects.filter(user=request.user, sequence__isnull=True)

        # Serialize the sessions
        session_serializer = PracticeSessionSerializer(sessions, many=True)

        return Response(session_serializer.data)


from django.db.models import Min, Max, Count


class ImproveExistingSequence(APIView):
    permission_classes = [IsAuthenticated]

    def format_timedelta(self, td):
        if not td:
            return "0 min"
        total_minutes = int(td.total_seconds() // 60)
        hours = total_minutes // 60
        minutes = total_minutes % 60

        if hours and minutes:
            return f"{hours} hr {minutes} min"
        elif hours:
            return f"{hours} hr"
        else:
            return f"{minutes} min"

    def get(self, request):
        user = request.user

        sequences = (
            PracticeSequence.objects
            .filter(user=user)  # or whatever filter
            .annotate(
                start_date=Min("sessions__created_at"),
                updated_at=Max("sessions__updated_at"),
                total_sessions=Count("sessions")
            )
            .prefetch_related("sessions")
        )
        print(sequences)
        response_data = []
        if sequences:
            for sequence in sequences:
                print(sequence.sessions.all())
                response_data.append({
                    "sequence_name": sequence.sequence_name,
                    "start_date": sequence.start_date,
                    "updated_at": sequence.updated_at,
                    "total_sessions": sequence.total_sessions,
                    "sessions": [
                        {
                            "name": session.session_name,  # assuming your PracticeSession has a 'name' field
                            "date": session.created_at,
                            "duration": self.format_timedelta(session.duration)  # assuming you store session duration
                        }
                        for session in sequence.sessions.all()
                    ]
                })
        else:
            response_data = None

        return Response(response_data)


class SlidePreviewView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        user = request.user
        serializer = SlidePreviewSerializer(data=request.data)
        if serializer.is_valid():
            slide_preview = SlidePreview.objects.create(
                user=user
            )

            uploaded = serializer.validated_data.get("slides_file")
            print(uploaded)

            if uploaded.name.endswith('pptx'):
                pdf_path = convert_pptx_to_pdf(uploaded)
                print(pdf_path)

                with open(pdf_path, 'rb') as pdf_file:
                    slide_preview.slides_file.save(
                        f"{user.id}_slides.pdf",
                        File(pdf_file),
                        save=False
                    )
                slide_preview.save()
                # Serialize the saved instance, not the incoming data
            data = SlidePreviewSerializer(slide_preview).data
            return Response(
                {
                    "status": "success",
                    "message": "Slides uploaded successfully.",
                    "data": data,
                },
                status=status.HTTP_200_OK,
            )
        return Response()
