import base64
import concurrent.futures
import openai
import os
import json
import traceback
import boto3
from botocore.exceptions import ClientError
import requests
import asyncio
from django.core.files import File
import logging
import tempfile
import subprocess
import platform
import time
# from aws_encryption_sdk import (
#     EncryptionSDKClient,
#     StrictAwsKmsMasterKeyProvider
# )
# from aws_encryption_sdk.identifiers import CommitmentPolicy


from rest_framework import viewsets, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.exceptions import PermissionDenied

from django.utils.decorators import method_decorator
from django.core.files.uploadedfile import UploadedFile
from django.db.models.fields.files import FieldFile
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
from django.db.models import F, FloatField
from django.db.models import Min, Max, Count

from requests import session
from datetime import datetime, timedelta, timezone
from collections import Counter
from openai import OpenAI
from drf_yasg.utils import swagger_auto_schema
from collections import defaultdict
from botocore.exceptions import NoCredentialsError, PartialCredentialsError, ClientError
from urllib.parse import urlparse # Added for S3 URL parsing
from asgiref.sync import async_to_sync


# For async DB operations within an async view
from channels.db import database_sync_to_async
from asgiref.sync import async_to_sync, sync_to_async # For async/sync bridging

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
from streaming.sentiment_analysis import ai_audience_question # This import might need adjustment based on project structure


User = get_user_model()
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


@csrf_exempt
def get_openai_realtime_token(request):
    headers = {
        "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": "gpt-4o-mini-realtime-preview",
        "modalities": ["text"],  # Only return text, not audio
        "instructions": """You are an advanced presentation evaluation system. Using the speaker's transcript.

Select one of these emotions that the audience is feeling most strongly ONLY choose from this list(thinking, sorrow, excitement, laughter, surprised, interested).

Respond only with the emotion. (thinking, sorrow, excitement, laughter, surprised, interested)""",
        "turn_detection": {
            "type": "server_vad",  # Use Server VAD
            "silence_duration_ms": 5  # 100ms silence threshold
        }
    }

    response = requests.post(
        "https://api.openai.com/v1/realtime/sessions",
        headers=headers,
        json=payload
    )

    return JsonResponse(response.json(), status=response.status_code)


def generate_slide_summary(pdf_file):
    logger.info(f"TYPE OF pdf_file: {type(pdf_file)}")
    logger.info("🔍 Starting slide summary generation...")

    try:
        client = OpenAI(api_key=settings.OPENAI_API_KEY)
        print("✅ OpenAI client initialized.")
    except Exception as e:
        print(f"❌ Failed to initialize OpenAI client: {e}")
        raise

    # STEP 2: Read and encode the PDF as Base64
    try:
        if hasattr(pdf_file, 'read'):
            print("📎 Reading PDF from uploaded file-like object.")
            pdf_file.seek(0)
            pdf_bytes = pdf_file.read()
        else:
            raise TypeError("Expected a file-like object or UploadedFile.")

        base64_pdf = base64.b64encode(pdf_bytes).decode('utf-8')
        print("✅ PDF successfully encoded to Base64.")
    except Exception as e:
        print(f"❌ Error reading or encoding PDF: {e}")
        raise

    # STEP 3: Construct the evaluation prompt
    prompt = """
        You are a presentation evaluator. Review the attached presentation and score it on:

        1. *Slide Efficiency*: Are too many slides used to deliver simple points?
        2. *Text Economy*: Is the presentation light on text per slide?
        3. *Visual Communication*: Is there a strong use of images, diagrams, or design elements?

        Give each a score from 1 (poor) to 100 (excellent).
    """
    print("🧠 Evaluation prompt constructed.")

    # STEP 4: Make the completion call
    try:
        print("🚀 Sending request to OpenAI...")
        response = client.chat.completions.create(
            model="gpt-4.1",
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
                    "strict": True,
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
                        ],
                        "additionalProperties": False
                    }
                }
            }
        )
        print("✅ Response received from OpenAI.")
    except Exception as e:
        print(f"❌ Error during OpenAI API call: {e}")
        raise

    # STEP 5: Parse and print the response
    try:
        result = json.loads(response.choices[0].message.content)
        print("\n✅ Evaluation Results:")
        print(f"Slide Efficiency: {result['SlideEfficiency']}/100")
        print(f"Text Economy: {result['TextEconomy']}/100")
        print(f"Visual Communication: {result['VisualCommunication']}/100")
    except Exception as e:
        print(f"❌ Error parsing response JSON: {e}")
        raise

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


def avg_top_scores(queryset, field, top_n=5):
    top_scores = (
        queryset
        .exclude(**{f"sentiment_analysis__{field}__isnull": True})
        .annotate(score=Cast(F(f"sentiment_analysis__{field}"), output_field=FloatField()))
        .order_by('-score')[:top_n]
        .values_list('score', flat=True)
    )
    values = list(top_scores)
    return round(sum(values) / len(values)) if values else 0


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


# class PracticeSessionViewSet(viewsets.ModelViewSet):
#     serializer_class = PracticeSessionSerializer
#     permission_classes = [IsAuthenticated]


#     def get_queryset(self):
#         user = self.request.user
#         if getattr(self, "swagger_fake_view", False) or user.is_anonymous:
#             return PracticeSession.objects.none()
#         if hasattr(user, "user_profile") and user.user_profile.is_admin():
#             return PracticeSession.objects.all().order_by("-date")
#         return PracticeSession.objects.filter(user=user).order_by("-date")

#     def perform_create(self, serializer):
#         serializer.save(user=self.request.user)

#     @action(detail=True, methods=['delete'], url_path='delete-session-media', permission_classes=[IsAuthenticated])
#     def delete_session_media(self, request, pk=None): # This method is now strictly synchronous
#         try:
#             # Correct usage: Use async_to_sync to run the coroutine returned by database_sync_to_async
#             session = async_to_sync(database_sync_to_async(self.get_object))()

#             # Now 'session' is the actual PracticeSession object, not a coroutine
#             if session.user != request.user and not (hasattr(request.user, 'user_profile') and request.user.user_profile.is_admin()):
#                 raise PermissionDenied("You do not have permission to delete media for this session.")

#             s3_client = boto3.client("s3", region_name=settings.AWS_S3_REGION_NAME)
#             s3_bucket_name = settings.AWS_STORAGE_BUCKET_NAME
#             s3_user_content_base_folder = "user-videos/"

#             s3_keys_to_delete = []

#             if session.compiled_video_url:
#                 try:
#                     parsed_url = urlparse(session.compiled_video_url)
#                     key_path = parsed_url.path.lstrip('/')
#                     if parsed_url.netloc.startswith(s3_bucket_name) and key_path.startswith(s3_user_content_base_folder):
#                         s3_keys_to_delete.append(key_path)
#                         print(f"Added compiled video key for deletion: {key_path}")
#                     else:
#                         print(f"WARNING: Compiled video URL {session.compiled_video_url} not in expected S3 bucket/folder, skipping S3 delete for this URL.")
#                 except Exception as e:
#                     print(f"Error parsing compiled_video_url {session.compiled_video_url}: {e}")

#             # Correct usage: Use async_to_sync to run the coroutine
#             chunks = async_to_sync(database_sync_to_async(list))(session.chunks.all())
#             for chunk in chunks:
#                 if chunk.video_file:
#                     try:
#                         parsed_url = urlparse(chunk.video_file)
#                         key_path = parsed_url.path.lstrip('/')
#                         if parsed_url.netloc.startswith(s3_bucket_name) and key_path.startswith(s3_user_content_base_folder):
#                             s3_keys_to_delete.append(key_path)
#                             print(f"Added chunk video key for deletion: {key_path}")
#                         else:
#                             print(f"WARNING: Chunk video URL {chunk.video_file} not in expected S3 bucket/folder, skipping S3 delete for this URL.")
#                     except Exception as e:
#                         print(f"Error parsing chunk video_file {chunk.video_file}: {e}")

#             if session.slides_file and session.slides_file.name:
#                 if settings.USE_S3:
#                     slide_s3_key = session.slides_file.name
#                     if slide_s3_key.startswith(f"{session.user.id}_slides") or slide_s3_key.startswith(f"slides/{session.user.id}_slides"):
#                         s3_keys_to_delete.append(slide_s3_key)
#                         print(f"Added slides file key for deletion: {slide_s3_key}")
#                     else:
#                         print(f"WARNING: Slides file {slide_s3_key} not in expected S3 user path, skipping S3 delete for slides.")
#                 else:
#                     print(f"Slides file {session.slides_file.name} is on local storage, skipping S3 delete.")

#             if s3_keys_to_delete:
#                 print(f"Attempting to delete {len(s3_keys_to_delete)} S3 objects for session {session.id}.")
#                 with concurrent.futures.ThreadPoolExecutor() as executor:
#                     futures = [executor.submit(self._delete_single_s3_object, s3_client, s3_bucket_name, s3_key) for s3_key in s3_keys_to_delete]
#                     for future in concurrent.futures.as_completed(futures):
#                         try:
#                             future.result()
#                         except Exception as s3_delete_error:
#                             print(f"Error deleting S3 object in background: {s3_delete_error}")
#                             traceback.print_exc()
#                 print(f"Completed S3 deletion attempts for session {session.id}.")
#             else:
#                 print(f"No S3 objects found to delete for session {session.id}.")

#             # Correct usage: Use async_to_sync to run the coroutine
#             async_to_sync(database_sync_to_async(self._clear_session_media_urls))(session, chunks)
#             print(f"Media URLs for session {session.id} cleared in database.")

#             return Response({"message": "Session media files deleted from S3 and URLs cleared in database."}, status=status.HTTP_200_OK)

#         except PracticeSession.DoesNotExist:
#             return Response({"error": "Session not found."}, status=status.HTTP_404_NOT_FOUND)
#         except PermissionDenied as e:
#             return Response({"error": str(e)}, status=status.HTTP_403_FORBIDDEN)
#         except Exception as e:
#             print(f"An unexpected error occurred during session media deletion for ID {pk}: {e}")
#             traceback.print_exc()
#             return Response({"error": f"An internal server error occurred during media deletion: {e}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

#     # _delete_single_s3_object and _clear_session_media_urls methods remain synchronous as before
#     def _delete_single_s3_object(self, s3_client, bucket_name, s3_key):
#         try:
#             s3_client.delete_object(Bucket=bucket_name, Key=s3_key)
#             print(f"Successfully deleted S3 object: {s3_key}")
#         except ClientError as e:
#             error_code = e.response.get("Error", {}).get("Code")
#             if error_code == 'NoSuchKey':
#                 print(f"S3 object {s3_key} not found (might have been already deleted or never existed).")
#             else:
#                 print(f"S3 ClientError deleting {s3_key}: {e}")
#         except Exception as e:
#             print(f"Unexpected error deleting S3 object {s3_key}: {e}")

#     def _clear_session_media_urls(self, session, chunks):
#         session.compiled_video_url = None
#         session.slides_file = None
#         session.save(update_fields=['compiled_video_url', 'slides_file'])

#         for chunk in chunks:
#             chunk.video_file = None
#             chunk.save(update_fields=['video_file'])


# Define BUCKET_NAME and BASE_FOLDER from settings
BUCKET_NAME = settings.AWS_STORAGE_BUCKET_NAME
BASE_FOLDER = getattr(settings, 'S3_BASE_FOLDER', 'user-videos/')

class PracticeSessionViewSet(viewsets.ModelViewSet):
    serializer_class = PracticeSessionSerializer
    permission_classes = [IsAuthenticated]
    queryset = PracticeSession.objects.all() # Define at class level, though get_queryset overrides.
    
    # AWS KMS Configuration
    AWS_KMS_KEY_ARN = getattr(settings, 'AWS_KMS_KEY_ARN', None)
    
    # def _get_kms_key_provider(self):
    #     """Initialize and return a KMS key provider for encryption/decryption."""
    #     if not self.AWS_KMS_KEY_ARN:
    #         raise ValueError("AWS_KMS_KEY_ARN is not configured in settings")
    #     return StrictAwsKmsMasterKeyProvider(key_ids=[self.AWS_KMS_KEY_ARN])
    
    # def _get_encryption_client(self):
    #     """Initialize and return an AWS Encryption SDK client."""
    #     return EncryptionSDKClient(commitment_policy=CommitmentPolicy.REQUIRE_ENCRYPT_REQUIRE_DECRYPT)
    
    # async def _encrypt_file(self, source_path, destination_path):
    #     """Encrypt a file using AWS KMS.
        
    #     Args:
    #         source_path: Path to the source file to encrypt
    #         destination_path: Path where the encrypted file will be saved
            
    #     Returns:
    #         dict: Encryption context used for decryption
    #     """
    #     encryption_context = {
    #         'source_file': source_path,
    #         'timestamp': datetime.utcnow().isoformat(),
    #         'purpose': 'secure_storage'
    #     }
        
    #     try:
    #         client = self._get_encryption_client()
    #         key_provider = self._get_kms_key_provider()
            
    #         # Encrypt the file
    #         with open(source_path, 'rb') as pt_file, open(destination_path, 'wb') as ct_file:
    #             with client.stream(
    #                 mode='e',
    #                 source=pt_file,
    #                 key_provider=key_provider,
    #                 encryption_context=encryption_context
    #             ) as encryptor:
    #                 for chunk in encryptor:
    #                     ct_file.write(chunk)
            
    #         return {
    #             'encryption_context': encryption_context,
    #             'key_id': self.AWS_KMS_KEY_ARN
    #         }
            
    #     except Exception as e:
    #         logger.error(f"Error encrypting file {source_path}: {str(e)}")
    #         raise
    
    # async def _decrypt_file(self, source_path, destination_path):
    #     """Decrypt a file encrypted with AWS KMS.
        
    #     Args:
    #         source_path: Path to the encrypted file
    #         destination_path: Path where the decrypted file will be saved
            
    #     Returns:
    #         dict: The encryption context from the encrypted file
    #     """
    #     try:
    #         client = self._get_encryption_client()
    #         key_provider = self._get_kms_key_provider()
            
    #         # Decrypt the file
    #         with open(source_path, 'rb') as ct_file, open(destination_path, 'wb') as pt_file:
    #             with client.stream(
    #                 mode='d',
    #                 source=ct_file,
    #                 key_provider=key_provider
    #             ) as decryptor:
    #                 # Get the encryption context from the header
    #                 encryption_context = decryptor.header.encryption_context
                    
    #                 # Write the decrypted data
    #                 for chunk in decryptor:
    #                     pt_file.write(chunk)
            
    #         return encryption_context
            
    #     except Exception as e:
    #         logger.error(f"Error decrypting file {source_path}: {str(e)}")
    #         raise

    def get_queryset(self):
        user = self.request.user
        # This handles cases for documentation generation (e.g., Swagger/DRF spectacular)
        # or for truly anonymous users (though IsAuthenticated should prevent most of this).
        if getattr(self, "swagger_fake_view", False) or user.is_anonymous:
            return PracticeSession.objects.none()
        # Admin/superuser can see all sessions
        if user.is_staff or user.is_superuser: # Using is_staff/is_superuser for admin check
            return PracticeSession.objects.all().order_by('-date')
        # Regular users see only their own sessions
        return PracticeSession.objects.filter(user=user).order_by('-date')
        
    def get_serializer_context(self):
        """
        Add extra context to the serializer.
        """
        context = super().get_serializer_context()
        if self.action in ['retrieve', 'list']:
            # Prefetch related enterprise settings to avoid N+1 queries
            self.queryset = self.queryset.prefetch_related('enterprise_settings')
        return context

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    # Helper methods for S3 operations
    async def _download_file_from_s3_async(self, s3_client, bucket_name, s3_key, local_path, is_encrypted=True):
        """Asynchronously downloads a file from S3 and decrypts it if needed.
        
        Args:
            s3_client: Boto3 S3 client
            bucket_name: Name of the S3 bucket
            s3_key: S3 object key to download
            local_path: Local path where the file will be saved
            is_encrypted: Whether the file is encrypted and needs decryption
        """
        temp_download_path = f"{local_path}.encrypted"
        
        try:
            # Download the file (encrypted or not)
            print(f"Downloading {s3_key} to {temp_download_path if is_encrypted else local_path}")
            await asyncio.to_thread(s3_client.download_file, bucket_name, s3_key, temp_download_path if is_encrypted else local_path)
            print(f"Finished downloading {s3_key}")
            
            if is_encrypted:
                # Get the metadata to check if the file was encrypted
                try:
                    head_response = await asyncio.to_thread(
                        s3_client.head_object,
                        Bucket=bucket_name,
                        Key=s3_key
                    )
                    
                    # Check if the file has encryption metadata
                    if 'x-amz-meta-encryption-context' in head_response.get('Metadata', {}):
                        print(f"Decrypting downloaded file: {temp_download_path} -> {local_path}")
                        # Decrypt the file
                        await self._decrypt_file(temp_download_path, local_path)
                        print(f"Successfully decrypted to {local_path}")
                    else:
                        # No encryption metadata found, just move the file
                        print(f"No encryption metadata found for {s3_key}, saving as is")
                        os.rename(temp_download_path, local_path)
                except ClientError as e:
                    if e.response['Error']['Code'] == '404':
                        print(f"Error: File {s3_key} not found in bucket {bucket_name}")
                        raise FileNotFoundError(f"File {s3_key} not found in bucket {bucket_name}")
                    print(f"Error getting metadata for {s3_key}: {str(e)}")
                    # If we can't check metadata, assume it's not encrypted
                    os.rename(temp_download_path, local_path)
        except Exception as e:
            print(f"Error during download/decrypt of {s3_key}: {str(e)}")
            raise
        finally:
            # Clean up the temporary downloaded file if it exists
            if is_encrypted and os.path.exists(temp_download_path):
                try:
                    os.remove(temp_download_path)
                    print(f"Cleaned up temporary downloaded file: {temp_download_path}")
                except Exception as e:
                    print(f"Warning: Failed to clean up temporary file {temp_download_path}: {e}")

    async def _upload_file_to_s3_async(self, s3_client, bucket_name, local_path, s3_key, encrypt_file=True):
        """Asynchronously uploads a file to S3 by running the blocking boto3 call in a thread.
        
        Args:
            s3_client: Boto3 S3 client
            bucket_name: Name of the S3 bucket
            local_path: Path to the local file to upload
            s3_key: S3 object key where the file will be stored
            encrypt_file: If True, encrypt the file before uploading
        """
        print(f"Preparing to upload {local_path} to s3://{bucket_name}/{s3_key}")
        
        upload_path = local_path
        temp_encrypted_path = None
        
        try:
            if encrypt_file:
                # Create a temporary file for the encrypted content
                temp_encrypted_path = f"{local_path}.encrypted"
                print(f"Encrypting file before upload: {local_path} -> {temp_encrypted_path}")
                
                # Encrypt the file
                encryption_result = await self._encrypt_file(local_path, temp_encrypted_path)
                print(f"File encrypted successfully. Key ID: {encryption_result['key_id']}")
                
                # Use the encrypted file for upload
                upload_path = temp_encrypted_path
            
            # Upload the file (encrypted or not)
            print(f"Uploading {upload_path} to s3://{bucket_name}/{s3_key}")
            await asyncio.to_thread(s3_client.upload_file, upload_path, bucket_name, s3_key)
            print(f"Finished uploading {s3_key}")
            
            # If we encrypted, store the encryption context in S3 object metadata
            if encrypt_file and temp_encrypted_path:
                metadata = {
                    'x-amz-meta-encryption-context': json.dumps(encryption_result['encryption_context']),
                    'x-amz-meta-encryption-key-id': encryption_result['key_id']
                }
                await asyncio.to_thread(
                    s3_client.copy_object,
                    Bucket=bucket_name,
                    Key=s3_key,
                    CopySource={'Bucket': bucket_name, 'Key': s3_key},
                    Metadata=metadata,
                    MetadataDirective='REPLACE'
                )
                print(f"Added encryption metadata to {s3_key}")
                
        finally:
            # Clean up the temporary encrypted file if it was created
            if temp_encrypted_path and os.path.exists(temp_encrypted_path):
                try:
                    os.remove(temp_encrypted_path)
                    print(f"Cleaned up temporary encrypted file: {temp_encrypted_path}")
                except Exception as e:
                    print(f"Warning: Failed to clean up temporary file {temp_encrypted_path}: {e}")

    def _delete_single_s3_object(self, s3_client_sync, bucket_name, s3_key):
        """Synchronously deletes a single S3 object. Intended to be called by ThreadPoolExecutor."""
        try:
            s3_client_sync.delete_object(Bucket=bucket_name, Key=s3_key)
            print(f"Successfully deleted S3 object: {s3_key}")
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code")
            if error_code == 'NoSuchKey':
                print(f"S3 object {s3_key} not found (might have been already deleted or never existed).")
            else:
                print(f"S3 ClientError deleting {s3_key}: {e}")
        except Exception as e:
            print(f"Unexpected error deleting S3 object {s3_key}: {e}")

    def _clear_session_media_urls_sync(self, session, chunks):
        """Synchronously clears media URLs in the database. Intended to be wrapped by sync_to_async."""
        session.compiled_video_url = None
        session.slides_file = None
        session.save(update_fields=['compiled_video_url', 'slides_file'])

        for chunk in chunks:
            chunk.video_file = None
            chunk.save(update_fields=['video_file'])
        print(f"Media URLs for session {session.id} cleared in database.")

    @action(detail=True, methods=['post'], url_path='queue-video-compilation')
    def start_compilation(self, request, pk=None):
        """
        Initiates the video compilation process for a session.
        Downloads chunks from S3, compiles using FFmpeg, uploads compiled video to S3,
        and updates the session with the new video URL.
        Uses asynchronous helpers for S3 operations to avoid blocking.
        """
        session = None # Initialize session to None for finally block
        temp_file_paths = [] # Initialize here to ensure it's available for finally
        try:
            # self.get_object() is a synchronous ORM call in ModelViewSet
            session = self.get_object()

            if session.user != request.user:
                raise PermissionDenied("You do not have permission to compile this session's video.")

            if not BUCKET_NAME:
                print("ERROR: S3 BUCKET_NAME is not configured in settings.")
                return Response({'status': 'Failed to start compilation', 'error': 'S3 bucket name is not configured.'},
                                status=status.HTTP_500_INTERNAL_SERVER_ERROR)

            s3_client_sync = boto3.client("s3", region_name=settings.AWS_S3_REGION_NAME)

            print(f"Starting immediate video compilation for session {session.id} by user {session.user.id}")

            # 1. Fetch chunk URLs
            # Using sync_to_async to run the synchronous ORM query in an async-safe manner
            # Convert to list immediately to avoid re-fetching or iterator issues later
            chunks = async_to_sync(sync_to_async(list))(session.chunks.all().order_by('chunk_number'))
            
            if not chunks:
                return Response({'status': 'No video chunks found for compilation'}, status=status.HTTP_400_BAD_REQUEST)

            input_files = []
            # We are in a synchronous ViewSet, so direct await calls are not possible here.
            # We use async_to_sync to bridge to our async helper methods which use asyncio.to_thread.
            for chunk in chunks:
                if chunk.video_file:
                    try:
                        parsed_url = urlparse(chunk.video_file)
                        s3_key = parsed_url.path.lstrip('/')
                        if not s3_key:
                            print(f"WARNING: Invalid S3 key derived from chunk video_file: {chunk.video_file}")
                            continue

                        temp_input_path = os.path.join(tempfile.gettempdir(), f"chunk_{chunk.id}_{os.path.basename(s3_key)}")
                        temp_file_paths.append(temp_input_path)

                        # Download video chunk from S3 (using async_to_sync for the async helper)
                        # Note: We assume chunks are stored encrypted, so we set is_encrypted=True
                        async_to_sync(self._download_file_from_s3_async)(
                            s3_client_sync, 
                            BUCKET_NAME, 
                            s3_key, 
                            temp_input_path,
                            is_encrypted=True  # Enable decryption of downloaded chunks
                        )
                        input_files.append(temp_input_path)
                    except Exception as e:
                        print(f"ERROR: Error processing chunk {chunk.id} video_file {chunk.video_file}: {e}")
                        traceback.print_exc()
                        return Response({'status': 'Failed to process video chunks', 'error': str(e)},
                                        status=status.HTTP_500_INTERNAL_SERVER_ERROR)
                else:
                    print(f"WARNING: Chunk {chunk.id} has no video_file. Skipping.")

            if not input_files:
                return Response({'status': 'No valid video files found to compile'}, status=status.HTTP_400_BAD_REQUEST)

            # Create a file list for ffmpeg concat demuxer
            file_list_path = os.path.join(tempfile.gettempdir(), f"ffmpeg_file_list_{session.id}.txt")
            temp_file_paths.append(file_list_path)

            with open(file_list_path, 'w') as f:
                for f_path in input_files:
                    f.write(f"file '{f_path}'\n")

            compiled_video_filename = f"compiled_session_{session.id}.mp4"
            compiled_video_path = os.path.join(tempfile.gettempdir(), compiled_video_filename)
            temp_file_paths.append(compiled_video_path)

            # 2. Compile video using ffmpeg concat demuxer
            print(f"Compiling video to {compiled_video_path}")
            ffmpeg_command = [
                'ffmpeg',
                '-y',  # Overwrite output files without asking
                '-f', 'concat',  # Concatenate demuxer
                '-safe', '0',  # Allow unsafe file paths (for tempfile paths)
                '-i', file_list_path,  # Input file list
                '-c:v', 'libx264', # Re-encode video to H.264
                '-preset', 'medium', # Encoding speed vs. compression efficiency tradeoff
                '-crf', '23', # Constant Rate Factor for quality (lower is higher quality)
                '-c:a', 'copy', # Copy audio stream without re-encoding
                '-pix_fmt', 'yuv420p', # Ensure common pixel format for broad compatibility
                compiled_video_path # Output compiled video file
            ]
            try:
                # subprocess.run is blocking, consider offloading if this action were async
                process = subprocess.run(ffmpeg_command, check=True, capture_output=True, text=True)
                print(f"FFmpeg compilation stdout: {process.stdout}")
                print(f"FFmpeg compilation stderr: {process.stderr}")
            except subprocess.CalledProcessError as e:
                print(f"ERROR: FFmpeg compilation failed: {e.stderr}")
                return Response({'status': 'Video compilation failed', 'error': e.stderr},
                                status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            except FileNotFoundError:
                print("ERROR: ffmpeg command not found. Please ensure ffmpeg is installed and in your PATH.")
                return Response({'status': 'ffmpeg not found', 'error': 'Server configuration error: ffmpeg is not installed or accessible.'},
                                status=status.HTTP_500_INTERNAL_SERVER_ERROR)

            # 3. Upload compiled video to S3 with encryption
            print(f"Uploading compiled video to S3 for session {session.id}.")
            s3_key = f"{BASE_FOLDER}{session.user.id}/{session.id}/{compiled_video_filename}"
            
            # Use async_to_sync for the async helper, with encryption enabled
            async_to_sync(self._upload_file_to_s3_async)(
                s3_client_sync, 
                BUCKET_NAME, 
                compiled_video_path, 
                s3_key,
                encrypt_file=True  # Enable encryption for the compiled video
            )
            print(f"Uploaded and encrypted {compiled_video_path} to s3://{BUCKET_NAME}/{s3_key}")

            # 4. Generate pre-signed URL (24 hours expiration)
            expiration_seconds = 24 * 3600
            
            # Add response-content-disposition to force download with a friendly filename
            params = {
                'Bucket': BUCKET_NAME, 
                'Key': s3_key,
                'ResponseContentDisposition': f'attachment; filename="{compiled_video_filename}"'
            }
            
            compiled_s3_url = s3_client_sync.generate_presigned_url(
                ClientMethod='get_object',
                Params=params,
                ExpiresIn=expiration_seconds
            )
            print(f"Generated pre-signed URL for {s3_key}: {compiled_s3_url}")
            
            # Store the S3 key in the session for future reference
            session.s3_video_key = s3_key

            # 5. Update PracticeSession with the pre-signed URL
            session.compiled_video_url = compiled_s3_url
            # Use sync_to_async to save the session asynchronously
            async_to_sync(sync_to_async(session.save))(update_fields=['compiled_video_url'])
            print(f"Session {session.id} updated with compiled video URL.")

            return Response({'status': 'Compilation complete', 'session_id': session.id, 'compiled_video_url': compiled_s3_url},
                            status=status.HTTP_200_OK)

        except PermissionDenied as e:
            return Response({'status': 'Permission Denied', 'error': str(e)},
                            status=status.HTTP_403_FORBIDDEN)
        except Exception as e:
            error_message = f"An unexpected error occurred during video compilation: {str(e)}"
            if session:
                error_message = f"An unexpected error occurred during video compilation for session {session.id}: {str(e)}"
            print(f"ERROR: {error_message}")
            traceback.print_exc()
            return Response({'status': 'Failed to complete compilation', 'error': error_message},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        finally:
            if temp_file_paths:
                print(f"Cleaning up temporary files for session {session.id if session else 'unknown'}.")
                for file_path in temp_file_paths:
                    if os.path.exists(file_path):
                        try:
                            os.remove(file_path)
                            print(f"Removed temporary file: {file_path}")
                        except OSError as e:
                            print(f"WARNING: Error removing temporary file {file_path}: {e}")

    @action(detail=True, methods=['delete'], url_path='delete-session-media', permission_classes=[IsAuthenticated])
    def delete_session_media(self, request, pk=None):
        """
        Deletes media files (compiled video, chunks, slides) from S3 for a given session
        and clears their URLs in the database.
        Includes logic to set a 'scheduled_delete_at' timestamp.
        Uses asynchronous database and S3 operations for efficiency.
        """
        try:
            # self.get_object() is synchronous in ModelViewSet
            session = self.get_object()

            # Permission check: User must own the session or be an admin/superuser
            if session.user != request.user and not (request.user.is_staff or request.user.is_superuser):
                raise PermissionDenied("You do not have permission to delete this session's media.")

            # Retain the logic of deleting media in 24 hours (by setting the timestamp)
            session.scheduled_delete_at = datetime.now(timezone.utc) + timedelta(hours=24)
            # Use sync_to_async to save the session (DB operation) asynchronously
            async_to_sync(sync_to_async(session.save))(update_fields=['scheduled_delete_at'])
            print(f"Session {session.id} marked for deletion at {session.scheduled_delete_at}.")
            # TODO: Placeholder for sending the corresponding email.
            # Example: YourAppEmailService.send_deletion_notification(session.user, session.id, session.scheduled_delete_at)

            s3_client_sync = boto3.client("s3", region_name=settings.AWS_S3_REGION_NAME)
            s3_bucket_name = BUCKET_NAME
            s3_user_content_base_folder = BASE_FOLDER # Already defined globally

            s3_keys_to_delete = []

            # Handle compiled video URL deletion
            if session.compiled_video_url:
                try:
                    parsed_url = urlparse(session.compiled_video_url)
                    key_path = parsed_url.path.lstrip('/')
                    # Security check: Ensure the key belongs to the expected bucket and base folder
                    # (parsed_url.netloc check is generally not needed if you enforce bucket ownership via IAM)
                    if parsed_url.netloc == f"{s3_bucket_name}.s3.{settings.AWS_S3_REGION_NAME}.amazonaws.com" and key_path.startswith(s3_user_content_base_folder):
                        s3_keys_to_delete.append(key_path)
                        print(f"Added compiled video key for deletion: {key_path}")
                    else:
                        print(f"WARNING: Compiled video URL {session.compiled_video_url} not in expected S3 bucket/folder, skipping S3 delete for this URL.")
                except Exception as e:
                    print(f"Error parsing compiled_video_url {session.compiled_video_url}: {e}")

            # Handle chunk video URLs deletion
            # Use sync_to_async to fetch chunks (DB operation) asynchronously
            chunks = async_to_sync(sync_to_async(list))(session.chunks.all())
            for chunk in chunks:
                if chunk.video_file:
                    try:
                        parsed_url = urlparse(chunk.video_file)
                        key_path = parsed_url.path.lstrip('/')
                        if parsed_url.netloc == f"{s3_bucket_name}.s3.{settings.AWS_S3_REGION_NAME}.amazonaws.com" and key_path.startswith(s3_user_content_base_folder):
                            s3_keys_to_delete.append(key_path)
                            print(f"Added chunk video key for deletion: {key_path}")
                        else:
                            print(f"WARNING: Chunk video URL {chunk.video_file} not in expected S3 bucket/folder, skipping S3 delete for this URL.")
                    except Exception as e:
                        print(f"Error parsing chunk video_file {chunk.video_file}: {e}")

            # Handle slides file deletion
            if session.slides_file and session.slides_file.name:
                slide_s3_key = session.slides_file.name
                # More robust checks for user-specific slide paths
                # Adjust these prefixes based on how your slides are actually stored in S3
                expected_slide_path_prefixes = [
                    f"slides/{session.user.id}/", # New typical path
                    f"{BASE_FOLDER}slides/{session.user.id}/", # If BASE_FOLDER is used for slides
                    f"{session.user.id}_slides/", # Older direct user ID prefix
                    f"slides/{session.user.id}_slides/", # Older "slides/user_id_slides/"
                ]
                
                is_safe_to_delete_slide = False
                for prefix in expected_slide_path_prefixes:
                    if slide_s3_key.startswith(prefix):
                        is_safe_to_delete_slide = True
                        break

                if is_safe_to_delete_slide:
                    s3_keys_to_delete.append(slide_s3_key)
                    print(f"Added slides file key for deletion: {slide_s3_key}")
                else:
                    print(f"WARNING: Slides file {slide_s3_key} not in expected S3 user path, skipping S3 delete for slides.")

            # Perform S3 deletions concurrently using ThreadPoolExecutor for blocking S3 calls
            if s3_keys_to_delete:
                print(f"Attempting to delete {len(s3_keys_to_delete)} S3 objects for session {session.id}.")
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    futures = [executor.submit(self._delete_single_s3_object, s3_client_sync, s3_bucket_name, s3_key)
                               for s3_key in s3_keys_to_delete]
                    # Wait for all futures to complete and handle potential exceptions
                    for future in concurrent.futures.as_completed(futures):
                        try:
                            future.result() # This will re-raise any exception from the thread
                        except Exception as s3_delete_error:
                            print(f"ERROR: Error deleting S3 object in background: {s3_delete_error}")
                            traceback.print_exc()
                print(f"Completed S3 deletion attempts for session {session.id}.")
            else:
                print(f"No S3 objects found to delete for session {session.id}.")

            # Clear URLs in the database after successful S3 deletion
            # Use sync_to_async to run the synchronous helper (DB operation) asynchronously
            async_to_sync(sync_to_async(self._clear_session_media_urls_sync))(session, chunks)
            print(f"Media URLs for session {session.id} cleared in database immediately.")

            return Response({'status': 'Media files deleted from S3 and URLs cleared in database immediately.'}, status=status.HTTP_200_OK)

        except PracticeSession.DoesNotExist:
            return Response({'detail': 'Practice session not found.'}, status=status.HTTP_404_NOT_FOUND)
        except PermissionDenied as e:
            return Response({"error": str(e)}, status=status.HTTP_403_FORBIDDEN)
        except Exception as e:
            print(f"ERROR: An unhandled error occurred during immediate media deletion for session {pk}: {e}")
            traceback.print_exc()
            return Response({'detail': f'An error occurred while deleting media: {e}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=['get'], url_path='compiled-video-status')
    def compiled_video_status(self, request, pk=None):
        """
        Retrieves the compilation status and URL of the compiled video for a session.
        Generates a new pre-signed URL for the encrypted video if needed.
        """
        session = self.get_object()

        # Check if the user has permission to view this session
        if session.user != request.user and not (request.user.is_staff or request.user.is_superuser):
            raise PermissionDenied("You do not have permission to view this session's video.")

        # Check if the video has been compiled
        if not hasattr(session, 's3_video_key') or not session.s3_video_key:
            return Response({
                'status': 'not_compiled',
                'message': 'Video has not been compiled yet.'
            }, status=status.HTTP_404_NOT_FOUND)

        try:
            # Initialize S3 client
            s3_client = boto3.client('s3', region_name=settings.AWS_S3_REGION_NAME)
            
            # Generate a new pre-signed URL with a 1-hour expiration
            expiration_seconds = 3600  # 1 hour
            
            # Get the filename from the S3 key for the content disposition
            filename = os.path.basename(session.s3_video_key)
            
            # Generate pre-signed URL with forced download and proper filename
            video_url = s3_client.generate_presigned_url(
                ClientMethod='get_object',
                Params={
                    'Bucket': BUCKET_NAME,
                    'Key': session.s3_video_key,
                    'ResponseContentDisposition': f'attachment; filename="{filename}"'
                },
                ExpiresIn=expiration_seconds
            )
            
            # Update the session with the new URL (optional, you might want to skip this to always generate fresh URLs)
            session.compiled_video_url = video_url
            session.save(update_fields=['compiled_video_url'])
            
            return Response({
                'status': 'completed',
                'video_url': video_url,
                'expires_in_seconds': expiration_seconds,
                'message': 'Video is ready for viewing.'
            })
            
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code')
            if error_code == 'NoSuchKey':
                return Response({
                    'status': 'not_found',
                    'message': 'The compiled video could not be found in storage.'
                }, status=status.HTTP_404_NOT_FOUND)
            logger.error(f"S3 ClientError generating pre-signed URL: {str(e)}")
            return Response({
                'status': 'error',
                'message': 'Failed to generate video URL. Please try again later.'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            
        except Exception as e:
            logger.error(f"Error generating pre-signed URL: {str(e)}")
            return Response({
                'status': 'error',
                'message': 'An unexpected error occurred. Please try again later.'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=['get'], url_path='sessions-by-month')
    def sessions_by_month(self, request):
        """
        Returns a count of practice sessions grouped by month for the authenticated user/admin.
        """
        sessions = self.get_queryset()
        monthly_counts = sessions.annotate(
            month=TruncMonth('date')).values('month').annotate(
            count=Count('id')).order_by('month')

        formatted_monthly_counts = [
            {'month': item['month'].strftime('%Y-%m'), 'count': item['count']}
            for item in monthly_counts
        ]

        return Response(formatted_monthly_counts)

    @action(detail=False, methods=['get'], url_path='sessions-by-day')
    def sessions_by_day(self, request):
        """
        Returns a count of practice sessions grouped by day for the authenticated user/admin.
        """
        sessions = self.get_queryset()
        daily_counts = sessions.annotate(
            day=TruncDay('date')).values('day').annotate(
            count=Count('id')).order_by('day')

        formatted_daily_counts = [
            {'day': item['day'].strftime('%Y-%m-%d'), 'count': item['count']}
            for item in daily_counts
        ]
        return Response(formatted_daily_counts)

    @action(detail=True, methods=['get'], url_path='analytics')
    def get_session_analytics(self, request, pk=None):
        """
        Retrieves comprehensive analytics for a specific practice session,
        including aggregated sentiment scores and calculated metrics.
        """
        session = self.get_object()
        if session.user != request.user and not (request.user.is_staff or request.user.is_superuser):
            raise PermissionDenied("You do not have permission to view this session's analytics.")

        # Aggregate chunk sentiment analysis data
        sentiment_data = session.chunks.aggregate(
            avg_conviction=Avg('sentiment_analysis__conviction', default=0),
            avg_clarity=Avg('sentiment_analysis__clarity', default=0),
            avg_impact=Avg('sentiment_analysis__impact', default=0),
            avg_brevity=Avg('sentiment_analysis__brevity', default=0),
            avg_transformative_potential=Avg('sentiment_analysis__transformative_potential', default=0),
            avg_volume=Avg('sentiment_analysis__volume', default=0),
            avg_pitch_variability=Avg('sentiment_analysis__pitch_variability', default=0),
            avg_pace=Avg('sentiment_analysis__pace', default=0),
            avg_trigger_response=Avg('sentiment_analysis__trigger_response', default=0),
            avg_filler_words=Avg('sentiment_analysis__filler_words', default=0),
            avg_grammar=Avg('sentiment_analysis__grammar', default=0),
            avg_posture=Avg('sentiment_analysis__posture', default=0),
            avg_motion=Avg('sentiment_analysis__motion', default=0),
            avg_pauses=Avg('sentiment_analysis__pauses', default=0),
            total_chunks=Count('id'))

        # Fetch all ChunkSentimentAnalysis objects for the session's chunks
        all_chunk_sentiment = ChunkSentimentAnalysis.objects.filter(chunk__session=session)

        # Calculate the count of positive gestures (True values)
        positive_gestures_count = all_chunk_sentiment.aggregate(
            positive_gestures_sum=Sum(Case(When(gestures=True, then=Value(1)), default=Value(0), output_field=IntegerField()))
        )['positive_gestures_sum'] or 0

        # Calculate gestures_score_for_body_language based on the percentage of positive gestures
        gestures_score = 0
        if sentiment_data['total_chunks'] > 0:
            gestures_score = (positive_gestures_count / sentiment_data['total_chunks']) * 100
            gestures_score = int(round(gestures_score))

        # Calculate the new aggregated fields based on the provided formulas
        audience_engagement = round( (sentiment_data['avg_impact'] + sentiment_data['avg_trigger_response'] + sentiment_data['avg_conviction']) / 3 , 2) if sentiment_data['total_chunks'] > 0 else 0
        overall_captured_impact = round(sentiment_data['avg_impact'] , 2)
        vocal_variety = round( (sentiment_data['avg_volume'] + sentiment_data['avg_pitch_variability'] + sentiment_data['avg_pace'] + sentiment_data['avg_pauses']) / 4 , 2) if sentiment_data['total_chunks'] > 0 else 0
        emotional_impact = round(sentiment_data['avg_trigger_response'] , 2)
        body_language = round( (sentiment_data['avg_posture'] + sentiment_data['avg_motion'] + gestures_score) / 3 , 2) if sentiment_data['total_chunks'] > 0 else 0
        transformative_communication = round(sentiment_data['avg_transformative_potential'] , 2)
        structure_and_clarity = round( (sentiment_data['avg_clarity']) , 2)
        language_and_word_choice = round( (sentiment_data['avg_brevity'] + sentiment_data['avg_filler_words'] + sentiment_data['avg_grammar']) / 3 , 2) if sentiment_data['total_chunks'] > 0 else 0

        # Update the PracticeSession instance with the aggregated scores
        session.volume = int(round(sentiment_data['avg_volume']))
        session.pitch_variability = int(round(sentiment_data['avg_pitch_variability']))
        session.pace = int(round(sentiment_data['avg_pace']))
        session.pauses = int(round(sentiment_data['avg_pauses']))
        session.conviction = int(round(sentiment_data['avg_conviction']))
        session.clarity = int(round(sentiment_data['avg_clarity']))
        session.impact = int(round(sentiment_data['avg_impact']))
        session.brevity = int(round(sentiment_data['avg_brevity']))
        session.trigger_response = int(round(sentiment_data['avg_trigger_response']))
        session.filler_words = int(round(sentiment_data['avg_filler_words']))
        session.grammar = int(round(sentiment_data['avg_grammar']))
        session.posture = int(round(sentiment_data['avg_posture']))
        session.motion = int(round(sentiment_data['avg_motion']))
        session.gestures = True if positive_gestures_count > 0 else False # Assuming gestures is a boolean field
        session.gestures_score_for_body_language = gestures_score
        session.transformative_potential = int(round(sentiment_data['avg_transformative_potential']))

        # Update the new calculated fields
        session.audience_engagement = audience_engagement
        session.overall_captured_impact = overall_captured_impact
        session.vocal_variety = vocal_variety
        session.emotional_impact = emotional_impact
        session.body_language = body_language
        session.transformative_communication = transformative_communication
        session.structure_and_clarity = structure_and_clarity
        session.language_and_word_choice = language_and_word_choice

        session.save() # Save the updated session fields

        # Prepare the response data, including all relevant analytics fields
        response_data = PracticeSessionSerializer(session).data
        return Response(response_data)

    @action(detail=True, methods=['get'], url_path='chunk-details')
    def get_chunk_details(self, request, pk=None):
        """
        Retrieves details for all chunks belonging to a specific practice session.
        """
        session = self.get_object()
        if session.user != request.user and not (request.user.is_staff or request.user.is_superuser):
            raise PermissionDenied("You do not have permission to view this session's chunk details.")
        chunks = session.chunks.all()
        serializer = SessionChunkSerializer(chunks, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['get'], url_path='sentiment-analysis-details')
    def get_sentiment_analysis_details(self, request, pk=None):
        """
        Retrieves detailed sentiment analysis data for all chunks of a specific practice session.
        """
        session = self.get_object()
        if session.user != request.user and not (request.user.is_staff or request.user.is_superuser):
            raise PermissionDenied("You do not have permission to view this session's sentiment analysis details.")
        sentiment_analyses = ChunkSentimentAnalysis.objects.filter(chunk__session=session)
        serializer = ChunkSentimentAnalysisSerializer(sentiment_analyses, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['get'], url_path='audio-transcript')
    def get_audio_transcript(self, request, pk=None):
        """
        Compiles and returns the full audio transcript for a specific practice session.
        """
        session = self.get_object()
        if session.user != request.user and not (request.user.is_staff or request.user.is_superuser):
            raise PermissionDenied("You do not have permission to view this session's transcript.")
        chunks = session.chunks.all().order_by('chunk_number')
        full_transcript = " ".join([chunk.transcript or "" for chunk in chunks])
        return Response({'session_id': session.id, 'full_transcript': full_transcript})

    @action(detail=True, methods=['get'], url_path='overall-feedback')
    def get_overall_feedback(self, request, pk=None):
        """
        Retrieves the overall feedback summary, strengths, and areas for improvement for a session.
        """
        session = self.get_object()
        if session.user != request.user and not (request.user.is_staff or request.user.is_superuser):
            raise PermissionDenied("You do not have permission to view this session's feedback.")

        feedback_data = {
            "session_id": session.id,
            "general_feedback_summary": session.general_feedback_summary,
            "strength": session.strength,
            "area_of_improvement": session.area_of_improvement,
        }
        return Response(feedback_data)

    @action(detail=True, methods=['post'], url_path='ai-question')
    def ai_question(self, request, pk=None):
        """
        Generates an AI audience question for a specific session, if allowed.
        """
        session = self.get_object()
        if session.user != request.user:
            raise PermissionDenied("You do not have permission to get AI questions for this session.")
        if not session.allow_ai_questions:
            return Response({"detail": "AI questions are not allowed for this session."},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            # Call your AI audience question function (assuming it's synchronous)
            question = ai_audience_question(session.id)
            if question:
                return Response({"session_id": session.id, "question": question})
            else:
                return Response({"detail": "Could not generate an AI question."}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            print(f"ERROR: Error generating AI question for session {session.id}: {e}")
            return Response({"detail": f"An error occurred: {e}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=['get'], url_path='sentiment-breakdown')
    def get_sentiment_breakdown(self, request, pk=None):
        """
        Provides a detailed breakdown of sentiment and communication scores for a session.
        """
        session = self.get_object()
        if session.user != request.user and not (request.user.is_staff or request.user.is_superuser):
            raise PermissionDenied("You do not have permission to view this session's sentiment breakdown.")

        # Check if any sentiment data exists for this session before returning.
        # This checks if any of the sentiment score fields are populated.
        if any([session.volume, session.pitch_variability, session.pace, session.pauses,
                session.conviction, session.clarity, session.impact, session.brevity,
                session.trigger_response, session.filler_words, session.grammar,
                session.posture, session.motion, session.gestures_score_for_body_language,
                session.transformative_potential]):
            response_data = {
                "session_id": session.id,
                "sentiment_scores": {
                    "volume": session.volume,
                    "pitch_variability": session.pitch_variability,
                    "pace": session.pace,
                    "pauses": session.pauses,
                    "conviction": session.conviction,
                    "clarity": session.clarity,
                    "impact": session.impact,
                    "brevity": session.brevity,
                    "trigger_response": session.trigger_response,
                    "filler_words": session.filler_words,
                    "grammar": session.grammar,
                    "posture": session.posture,
                    "motion": session.motion,
                    "gestures_score_for_body_language": session.gestures_score_for_body_language,
                    "transformative_potential": session.transformative_potential,
                },
                "calculated_metrics": {
                    "audience_engagement": session.audience_engagement,
                    "overall_captured_impact": session.overall_captured_impact,
                    "vocal_variety": session.vocal_variety,
                    "emotional_impact": session.emotional_impact,
                    "body_language": session.body_language,
                    "transformative_communication": session.transformative_communication,
                    "structure_and_clarity": session.structure_and_clarity,
                    "language_and_word_choice": session.language_and_word_choice,
                }
            }
        else:
            response_data = {
                "session_id": session.id,
                "message": "Sentiment breakdown not available or not yet calculated for this session.",
                "sentiment_scores": {},
                "calculated_metrics": {}
            }
        return Response(response_data)


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
                print("DEBUG pdf_path:", slides_path, type(slides_path))
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

        session = PracticeSession.objects.get(id=session_id)
        goals = session.goals
        name = session.user.first_name
        role = session.user.user_profile.user_intent

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

        if session.session_type == "enterprise":
            # Get the enterprise vertical from the user's enterprise
            enterprise_vertical = None
            if hasattr(session.user, 'enterprise_profile') and hasattr(session.user.enterprise_profile, 'enterprise'):
                # Get the available verticals from the enterprise
                enterprise = session.user.enterprise_profile.enterprise
                available_verticals = enterprise.get_available_verticals()
                
                # If there's only one vertical available, use it
                if len(available_verticals) == 1:
                    enterprise_vertical = available_verticals[0]
                # Otherwise, we'd need additional logic to determine which vertical to use
                # For now, we'll use the first available vertical as a fallback
                elif available_verticals:
                    enterprise_vertical = available_verticals[0]
            
            print(f"This is an Enterprise Specialty session. Vertical: {enterprise_vertical}")
            
            # Default prompt if vertical is not found
            prompt = ""
            
            if enterprise_vertical == Enterprise.Vertical.MEDIA_TRAINING:
                prompt = f"""
                My name is {name}, and I’m currently a {role} in my sports journey.

                You are my personal media trainer. Your role is to help young athletes like me become confident, clear, and compelling in interviews and public appearances. Your job is to sharpen how I show up under the spotlight — in press conferences, interviews, post-game chats, or high-stakes media moments. Help me express myself with purpose, connect with my audience, and represent myself and my team well.

                My goal with this talk is: {goals}.

                Use my transcript and the evaluation data to give me detailed, structured feedback in three parts. Talk to me like a coach who genuinely wants to see me grow — not just as an athlete, but as a communicator and role model.

                1. Strengths: Highlight what I did well in this appearance. Focus on how I handled questions, the way I carried myself, my clarity, tone, and any standout responses. Keep the language simple and encouraging. Be specific.

                2. Areas for Improvement: Give clear and constructive feedback on where I can get better. Focus on my media presence, how I answered questions, how I framed my thoughts, and whether I stayed composed and on message. Keep the suggestions practical and easy to apply.

                3. Media Response Breakdown: Go deeper into how I handled each question during the session. For every question in the transcript (including ones labeled "AUDIENCE QUESTION"), evaluate:
                - Did I fully answer the question or dodge it?
                - Did I control the narrative or let the interviewer steer me off message?
                - Was my answer memorable, respectful, and aligned with my personal or team values?
                - Did I stay calm and focused, even if the question was tricky?
                - Was there a moment where I really connected — with a story, perspective, or strong message?

                Also:
                - How effective was my opening? Did it show confidence, presence, or set a clear tone?
                - Did my message feel organized and lead somewhere meaningful?
                - Mention my goal {goals} only if I clearly stated one.
                - Finish with a short reflection on how I’m developing as a public voice — and where I should focus next to elevate my presence.

                Tone: Speak to me with respect and belief in my future. I don’t need grammar corrections. I need guidance on how to stand tall, communicate with intention, and leave an impact in every media moment.

                Evaluation data: {metrics_string}

                Transcript:
                {combined_feedback}
                """

            elif enterprise_vertical == Enterprise.Vertical.COACH:
                prompt = f"""
                My name is {name}, and I’m currently a {role} in my sports journey.

                You are my personal communication coach. Your job is to help me become better at communicating with my coach/GM — especially when it comes to expressing how I feel, asking for support, giving feedback, or having honest conversations. I want to build trust, show leadership, and strengthen my relationship with my coach/GM through how I communicate.

                My goal with this conversation is: {goals}.

                Use my transcript and the evaluation data to give me structured feedback in three parts. Talk to me like someone who wants to see me grow — not just as an athlete, but as someone who can communicate clearly, confidently, and with emotional maturity.

                1. Strengths: Highlight what I did well in this conversation with my coach. Focus on how I expressed myself, whether I stayed respectful and honest, and if I showed maturity, clarity, or emotional awareness. Be specific and use simple language.

                2. Areas for Improvement: Give clear, practical advice on what I could have done better. Look at how I handled disagreement, whether I avoided tough topics, if I rambled or got defensive, or if I missed a chance to be more open or thoughtful.

                3. Coach Conversation Breakdown: Go through key parts of the conversation where I was responding to my coach or bringing up something important. For each moment:
                - Did I clearly express my thoughts or feelings?
                - Did I stay calm, respectful, and focused — even if the topic was hard?
                - Did I show that I was listening and that I understood my coach’s point of view?
                - Was there any moment where I built trust or showed growth as a communicator?
                - Did I give off the impression of someone who’s coachable, driven, and self-aware?

                Also:
                - Was the opening of my conversation strong — did I set the tone or intention clearly?
                - Did I sound like someone who takes ownership of their growth?
                - Mention my goal {goals} only if I clearly stated one.
                - End with your thoughts on how I’m improving in how I show up with my coach — and what the next step should be.

                Tone: Speak to me with honesty and belief in my potential. I want to get better at owning my voice and building stronger relationships, starting with the one I have with my coach.

                Evaluation data: {metrics_string}

                Transcript:
                {combined_feedback}
                """

            elif enterprise_vertical == Enterprise.Vertical.GM:
                prompt = f"""
                My name is {name}, and I’m currently a {role} in my sports journey.

                You are my personal communication coach. Your job is to help me become better at communicating with my coach/GM — especially when it comes to expressing how I feel, asking for support, giving feedback, or having honest conversations. I want to build trust, show leadership, and strengthen my relationship with my coach/GM through how I communicate.

                My goal with this conversation is: {goals}.

                Use my transcript and the evaluation data to give me structured feedback in three parts. Talk to me like someone who wants to see me grow — not just as an athlete, but as someone who can communicate clearly, confidently, and with emotional maturity.

                1. Strengths: Highlight what I did well in this conversation with my coach. Focus on how I expressed myself, whether I stayed respectful and honest, and if I showed maturity, clarity, or emotional awareness. Be specific and use simple language.

                2. Areas for Improvement: Give clear, practical advice on what I could have done better. Look at how I handled disagreement, whether I avoided tough topics, if I rambled or got defensive, or if I missed a chance to be more open or thoughtful.

                3. Coach Conversation Breakdown: Go through key parts of the conversation where I was responding to my coach or bringing up something important. For each moment:
                - Did I clearly express my thoughts or feelings?
                - Did I stay calm, respectful, and focused — even if the topic was hard?
                - Did I show that I was listening and that I understood my coach’s point of view?
                - Was there any moment where I built trust or showed growth as a communicator?
                - Did I give off the impression of someone who’s coachable, driven, and self-aware?

                Also:
                - Was the opening of my conversation strong — did I set the tone or intention clearly?
                - Did I sound like someone who takes ownership of their growth?
                - Mention my goal {goals} only if I clearly stated one.
                - End with your thoughts on how I’m improving in how I show up with my coach — and what the next step should be.

                Tone: Speak to me with honesty and belief in my potential. I want to get better at owning my voice and building stronger relationships, starting with the one I have with my coach.

                Evaluation data: {metrics_string}

                Transcript:
                {combined_feedback}
                """
            elif enterprise_vertical == Enterprise.Vertical.COACHING:
                prompt = f"""
                My name is {name}, and I’m currently a {role} in my sales journey.

                You are my personal sales communication coach. Your job is to help me get better at handling objections, building trust, and closing conversations with clarity and confidence. I want to improve how I respond when a prospect pushes back, asks tough questions, or seems uncertain — without sounding defensive or desperate.

                My goal with this sales conversation is: {goals}.

                Use my transcript and the evaluation data to give me structured, honest feedback in three parts. Speak to me like a coach who wants me to win — not just close deals, but grow into someone who knows how to listen, adapt, and lead the sales conversation with confidence and empathy.

                1. Strengths: Identify what I did well in this sales interaction. Focus on how I listened, built rapport, stayed composed, or responded to objections clearly. Mention if I showed confidence, asked smart questions, or guided the conversation with intention. Use simple language and be specific.

                2. Areas for Improvement: Give me clear, direct, and practical advice on where I can improve. Focus on how I handled objections, whether I missed opportunities to go deeper, gave vague answers, or talked too much. Be honest — tell me where I lost control of the conversation or came across as uncertain or unprepared.

                3. Objection Handling Breakdown: Go through each major objection or hesitation the prospect raised. For each one:
                - Did I acknowledge and validate their concern?
                - Did I stay calm and confident while answering?
                - Did I give a clear, persuasive response that moved the conversation forward?
                - Did I use any strong reframes or real-world examples to build trust?
                - Were there moments where I gave in too quickly, over-explained, or lost leverage?

                Also:
                - Did I control the flow of the conversation, or did the prospect lead me?
                - Did I sound confident in the value of what I was offering?
                - Mention my goal {goals} only if I clearly stated one.
                - End with your thoughts on how I’m growing as a salesperson — and what I should work on next to level up my objection handling skills.

                Tone: Talk to me like a pro you believe in. Be real. Be specific. No fluff — just the kind of feedback that makes me sharper, stronger, and more effective in the next sales call.

                Evaluation data: {metrics_string}

                Transcript:
                {combined_feedback}
                """

            else:
                # Fallback to the original media training prompt if vertical is not recognized
                prompt = f"""
                My name is {name}, and I'm currently a {role} in my sports journey.

                You are my personal communication mentor. Your focus is to help young athletes like me grow as confident and impactful speakers — whether in interviews, team talks, public appearances, or leadership moments. Your feedback should guide me to express myself better, show up with presence, and connect with others.

                My goal with this talk is: {goals}.

                I want you to give me helpful feedback using my presentation and the evaluation data. Structure your feedback in three clear parts, and speak to me like a trusted coach who wants me to win — not just in sports, but in how I show up as a person.

                1. Strengths: Identify my most impactful specific strengths. Focus on concrete content choices, tone, delivery techniques, and audience engagement strategies. Use simple sentences, do not include transcript quotes here.

                2. Areas for Improvement: Provide clear, actionable, and specific feedback on where I can improve. Emphasize my delivery habits, missed emotional beats, and structural weaknesses. Use simple sentences, do not include transcript quotes here.

                3. General Feedback Summary: Craft a detailed, content-specific analysis of my session. Your summary must be grounded in specific parts of my speech. Your primary focus is how I handled the questions but include the following:
                - Evaluate the effectiveness of my opening: Was it attention-grabbing, relevant, or emotionally engaging? Did I clearly set the tone or premise for the rest of the talk?
                - Were there any standout words or phrases I used that really connected or showed emotion?
                - Did I use any filler words too often (like "uh," "you know," "like")? If so, quote a few examples.
                - How did I sound — confident, calm, excited, nervous? Did that shift anywhere in the talk?
                - Reflect on whether my style or personal story helped make the talk connect with more people.
                - Clearly state whether my talk was effective — and if so, effective at what specifically 
                - Did my message feel organized, and did I build it up toward something important?
                - "AUDIENCE QUESTION" is in my transcript, evaluate how I answered the audience questions. If no "AUDIENCE QUESTION" is in my transcript dont mention anything about questions
                - Mention my goal {goals} only if I had one — skip this part if I didn't mention any goal.
                - End with your view on how I'm growing as a speaker and how I can take the next step.

                Tone: Talk to me with respect, like you believe in my future. Don't use grammar corrections unless it really affects how I come across. Don't use headers, dashes, or bold formatting. Use \n\n between paragraphs and begin with a positive comment based on how I showed up in my presentation.

                Evaluation data: {metrics_string}

                Transcript:
                {combined_feedback}
                """

        else:
            prompt = f"""
                My name is {name}, and my career level is {role}.
                You are my personal expert communication mentor/coach specializing in public speaking, storytelling, pitching, and presentations. Your role is to critique me for my growth, and guide me to become a more impactful professional speaker for my career development.

                My goal with this presentation is: {goals}. Using my provided presentation evaluation data and speech, generate a structured JSON response with the following three components:

                1. Strengths: Identify my most impactful specific strengths. Focus on concrete content choices, tone, delivery techniques, and audience engagement strategies. Use simple sentences, do not include transcript quotes here.

                2. Areas for Improvement: Provide clear, actionable, and specific feedback on where I can improve. Emphasize my delivery habits, missed emotional beats, and structural weaknesses. Use simple sentences, do not include transcript quotes here.

                3. General Feedback Summary: Craft a detailed, content-specific analysis of my presentation. Your summary must be grounded in specific parts of my speech. Include the following:
                - Evaluate the effectiveness of my opening: Was it attention-grabbing, relevant, or emotionally engaging? Did I clearly set the tone or premise for the rest of the talk?
                - Highlight specific trigger words or emotionally resonant phrases I used that effectively drove engagement, and explain how they influenced the audience. Include the actual phrases from the transcript.
                - List any filler words I overused (e.g., "um", "like", "you know"). Quote a few instances where these occurred.
                - Comment on how I used powerful or evocative language—did I evoke empathy, joy, urgency, or excitement? Did I show vulnerability or emotional relatability?
                - Analyze my tone of voice, Was it confident, warm, authoritative, enthusiastic, or inconsistent? Note any tone shifts and how they impacted audience engagement. Back this up with quoted phrases that show tone variation.
                - Reflect on whether my style or personal story helped make the talk more memorable.
                - Was I persuasive enough, Did I inspire action, challenge assumptions, or shift perspectives? Highlight specific techniques like storytelling, analogies, or rhetorical questions.
                - Evaluate the structure and flow of my talk. Were transitions smooth? Did I build toward a clear message or emotional climax? Point to exact sentences where this occurred.
                - Clearly state whether my talk was effective — and if so, effective at what specifically (e.g., persuading the audience, building trust, sparking interest).
                - If "AUDIENCE QUESTION" is in my transcript, evaluate how I answered the audience questions. If no "AUDIENCE QUESTION" is in my transcript dont mention anything about questions
                - Reference my goal to {goals}. If I have no goals dont mention anything about goals.
                - Provide an overall evaluation of how well I demonstrated mastery in storytelling, public speaking, or pitching. Include tailored suggestions for improvement based on the context and audience. Ground all observations in direct excerpts from the transcript. Quote exact sentences where possible.

                Tone: speak to me personally but professionaly like a mentor coach, critique me for my growth while referencing my transcript not my evaluation data. Don't use headers or "**" for titles, dont use hyphens or dashes '—' in your response, just correct me and reference my transcript. Use \n \n for line breaks between paragraphs and also start with an encouraging remark relevant to my presentation with my name.

                Evaluation data: {metrics_string}
                Transcript:
                {combined_feedback}
                """

        try:
            print("Calling OpenAI for summary generation...")
            completion = client.chat.completions.create(
                model="gpt-4.1",
                messages=[{"role": "user", "content": prompt}],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "Feedback",
                        "strict": True,
                        "schema": {
                            "type": "object",
                            "properties": {
                                "Strength": {"type": "array","items": {"type": "string"}},
                                "Area of Improvement": {"type": "array","items": {"type": "string"}},
                                "General Feedback Summary": {"type": "string"},
                            },
                            "required": ["Strength", "Area of Improvement", "General Feedback Summary"],
                        "additionalProperties": False
                        }
                    }
                },
                temperature=0.8,  # Adjust temperature as needed
                max_tokens=2600  # Limit tokens to control response length
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
                "Strength": f"N/A - Error generating detailed summary.{e}",
                "Area of Improvement": f"N/A - Error generating detailed summary.{e}",
                "General Feedback Summary": f"Error processing AI summary. Raw feedback: {combined_feedback}",
            }
        except Exception as e:
            print(f"Error generating summary with OpenAI: {e}")
            # Fallback in case of any other OpenAI error
            return {
                "Strength": f"N/A - Error generating detailed summary.{e}",
                "Area of Improvement": f"N/A - Error generating detailed summary.{e}",
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

           # Calculate top 5 average values for specific metrics
            avg_conviction = avg_top_scores(chunks_with_sentiment, 'conviction')
            avg_trigger_response = avg_top_scores(chunks_with_sentiment, 'trigger_response')
            avg_impact = avg_top_scores(chunks_with_sentiment, 'impact')
            avg_transformative_potential = avg_top_scores(chunks_with_sentiment, 'transformative_potential')

            # Original Aggregation (excluding the ones we override manually)
            aggregation_results = chunks_with_sentiment.aggregate(
                avg_volume=Round(Avg("sentiment_analysis__volume"), output_field=IntegerField()),
                avg_pitch_variability=Round(Avg("sentiment_analysis__pitch_variability"), output_field=IntegerField()),
                avg_pace=Round(Avg("sentiment_analysis__pace"), output_field=IntegerField()),
                avg_clarity=Round(Avg("sentiment_analysis__clarity"), output_field=IntegerField()),
                avg_brevity=Round(Avg("sentiment_analysis__brevity"), output_field=IntegerField()),
                avg_filler_words=Round(Avg("sentiment_analysis__filler_words"), output_field=IntegerField()),
                avg_grammar=Round(Avg("sentiment_analysis__grammar"), output_field=IntegerField()),
                avg_posture=Round(Avg("sentiment_analysis__posture"), output_field=IntegerField()),
                avg_motion=Round(Avg("sentiment_analysis__motion"), output_field=IntegerField()),
                avg_pauses=Round(Avg("sentiment_analysis__pauses"), output_field=IntegerField()),
                total_true_gestures=Round(Sum(Cast('sentiment_analysis__gestures', output_field=IntegerField()))),
                total_chunks_for_aggregation=Count('sentiment_analysis__clarity'),
            )

            # Helper to safely fetch or default a value
            def get_agg_value(key, default):
                value = aggregation_results.get(key, default)
                return value if value is not None else default

            # Use top 5 average values directly
            conviction = avg_conviction
            trigger_response = avg_trigger_response
            impact = avg_impact
            transformative_potential = avg_transformative_potential

            # Pull the remaining values from aggregate results
            volume = get_agg_value("avg_volume", 0.0)
            pitch_variability = get_agg_value("avg_pitch_variability", 0.0)
            pace = get_agg_value("avg_pace", 0.0)
            pauses_average = get_agg_value("avg_pauses", 0.0)
            clarity = get_agg_value("avg_clarity", 0.0)
            brevity = get_agg_value("avg_brevity", 0.0)
            filler_words = get_agg_value("avg_filler_words", 0.0)
            grammar = get_agg_value("avg_grammar", 0.0)
            posture = get_agg_value("avg_posture", 0.0)
            motion = get_agg_value("avg_motion", 0.0)

            # Gesture calculations
            total_true_gestures = get_agg_value("total_true_gestures", 0)
            total_chunks_for_aggregation = get_agg_value("total_chunks_for_aggregation", 0)
            gestures_proportion = (3 * total_true_gestures / total_chunks_for_aggregation) if total_chunks_for_aggregation > 0 else 0.0
            gestures_proportion = min(gestures_proportion, 0.95)
            gestures_score_for_body_language = gestures_proportion * 100

            # Derived scores
            def safe_division(numerator, denominator):
                return (numerator / denominator) if denominator > 0 else 0.0

            audience_engagement = safe_division((impact + trigger_response + conviction), 3.0)
            overall_captured_impact = impact
            vocal_variety = safe_division((volume + pitch_variability + pace + pauses_average), 4.0)
            emotional_impact = trigger_response
            body_language = safe_division((posture + motion + gestures_score_for_body_language), 3.0)
            transformative_communication = transformative_potential
            structure_and_clarity = clarity
            language_and_word_choice = safe_division((brevity + filler_words + grammar), 3.0)

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

            metrics_string = f"Final Scores: volume score: {session.volume}, pitch variability score: {session.pitch_variability}, pace score: {session.pace}, pauses score: {session.pauses}, conviction score: {session.conviction}, clarity score: {session.clarity}, impact score: {session.impact}, brevity score: {session.brevity}, trigger response score: {session.trigger_response}, filler words score: {session.filler_words}, grammar score: {session.grammar}, transformative potential score: {session.transformative_potential}"

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

        start_date = request.query_params.get("start_date")
        end_date = request.query_params.get("end_date")
        sort = request.query_params.get("sort")
        sort_type = {"max-date":"-date",'min-date':"date",'max-impact':'-impact','min-impact':"impact",'max-duration':"-duration", 'min-duration':'duration'}

        if start_date and end_date:
            parsed_start = datetime.strptime(start_date, "%Y-%m-%d").date()
            parsed_end = datetime.strptime(end_date, "%Y-%m-%d").date()

            graph_session = PracticeSession.objects.filter(user=user, date__date__range=(parsed_start, parsed_end))
        else:
            graph_session = PracticeSession.objects.filter(user=user)

        if sort and sort in sort_type.keys():
            recent_session = PracticeSession.objects.filter(user=user).order_by(sort_type[sort])[:5]
        else:
            recent_session = PracticeSession.objects.filter(user=user).order_by("-date")[:5]

        card_data = session.aggregate(
            speaking_time=Sum("duration"),
            total_session=Count("id"),
            impact=Round(Avg("impact")),
            transformative_communication=Round(Avg("transformative_communication"))
        )
        # Convert timedelta to HH:MM:SS
        if card_data["speaking_time"]:
            card_data["speaking_time"] = str(card_data["speaking_time"])

        recent_data = (
            recent_session.annotate(
                session_type_display=Case(
                    When(session_type="pitch", then=Value("Pitch Practice")),
                    When(session_type="public", then=Value("Public Speaking")),
                    When(session_type="presentation", then=Value("Presentation")),
                    output_field=CharField(),
                ),
                formatted_duration=Cast("duration", output_field=CharField()),
            )
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
            graph_session
            .annotate(
                day=TruncDay("date"),
            )
            .values("day", "trigger_response","impact","conviction")
            .order_by("day")
        )

        result = (
            {
                "date": item["day"],
                "trigger_response": item["trigger_response"] or 0,
                "impact": item["impact"] or 0,
                "conviction": item["conviction"] or 0,
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

    def post(self, request):
        serializer = PracticeSessionSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(data=serializer.data, status=status.HTTP_200_OK)
        return Response(status=status.HTTP_404_NOT_FOUND)


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
                            "duration": self.format_timedelta(session.duration), # assuming you store session duration
                            "virtual_environment":session.virtual_environment,
                            "session_type":session.session_type
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
