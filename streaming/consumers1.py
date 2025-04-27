#################################################################################################################
# This version follows consumers2's approach of handling saves in background while keeping frontend-critical operations in main thread
#################################################################################################################

import asyncio
import platform

# Set the event loop policy for Windows if necessary
if platform.system() == 'Windows':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import json
import os
import asyncio
import tempfile
import concurrent.futures
import subprocess
import boto3
import openai
import django
import time
import traceback
import random
import numpy as np

from base64 import b64decode
from datetime import timedelta

from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async

from .sentiment_analysis import analyze_results, transcribe_audio
from practice_sessions.models import PracticeSession, SessionChunk, ChunkSentimentAnalysis
from practice_sessions.serializers import SessionChunkSerializer, ChunkSentimentAnalysisSerializer

# Ensure Django settings are configured
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "EngageX.settings")
django.setup()

# Initialize OpenAI client
openai.api_key = os.environ.get("OPENAI_API_KEY")
client = openai.OpenAI() if openai.api_key else None

# Initialize S3 client
s3 = boto3.client("s3", region_name=os.environ.get('AWS_REGION'))
BUCKET_NAME = "engagex-user-content-1234"
BASE_FOLDER = "user-videos/"
TEMP_MEDIA_ROOT = tempfile.gettempdir()
EMOTION_STATIC_FOLDER = "static-videos"

# Define the rooms the user can choose from
POSSIBLE_ROOMS = ['conference_room', 'board_room_1', 'board_room_2']

# Assume a fixed number of variations for each emotion video
NUMBER_OF_VARIATIONS = 5

# Define the window size for analysis
ANALYSIS_WINDOW_SIZE = 3

# Helper function to convert numpy types to native Python types for JSON serialization
def convert_numpy_types(obj):
    """Recursively converts numpy types within a dict or list to native Python types."""
    if isinstance(obj, np.integer):
        return int(obj)
    elif isinstance(obj, np.floating):
        return float(obj)
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    elif isinstance(obj, dict):
        return {k: convert_numpy_types(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_numpy_types(elem) for elem in obj]
    else:
        return obj

class LiveSessionConsumer(AsyncWebsocketConsumer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.session_id = None
        self.room_name = None
        self.chunk_counter = 0
        self.media_buffer = []  # Stores temporary media file paths
        self.audio_buffer = {}  # Maps media_path to audio_path
        self.transcript_buffer = {}  # Maps media_path to transcript
        self.media_path_to_chunk = {}  # Maps media_path to SessionChunk ID
        self.background_chunk_save_tasks = {}  # Tracks background save tasks

    async def connect(self):
        query_string = self.scope['query_string'].decode()
        query_params = {}
        if query_string:
            for param in query_string.split('&'):
                try:
                    key, value = param.split('=', 1)
                    query_params[key] = value
                except ValueError:
                    continue

        self.session_id = query_params.get('session_id', None)
        self.room_name = query_params.get('room_name', None)

        if self.session_id and self.room_name in POSSIBLE_ROOMS:
            await self.accept()
            await self.send(json.dumps({
                "type": "connection_established",
                "message": f"Connected to session {self.session_id} in room {self.room_name}"
            }))
        else:
            await self.close()

    async def disconnect(self, close_code):
        # Wait for pending background tasks to finish
        if self.background_chunk_save_tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self.background_chunk_save_tasks.values(), return_exceptions=True),
                    timeout=30.0
                )
            except (asyncio.TimeoutError, Exception):
                pass

        # Clean up all temporary files
        await self._cleanup_all_files()

    async def _cleanup_all_files(self):
        """Clean up all temporary files associated with this session."""
        all_paths = set()
        all_paths.update(self.media_buffer)
        all_paths.update(self.audio_buffer.values())
        all_paths.update(self.media_path_to_chunk.keys())

        cleanup_tasks = []
        for file_path in all_paths:
            if file_path and os.path.exists(file_path):
                cleanup_tasks.append(self._remove_file_safe(file_path))

        if cleanup_tasks:
            await asyncio.gather(*cleanup_tasks, return_exceptions=True)

    async def _remove_file_safe(self, file_path):
        """Safely remove a file with retries."""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                await asyncio.sleep(0.05 * (attempt + 1))
                if os.path.exists(file_path):
                    os.remove(file_path)
                    return True
            except Exception:
                pass
        return False

    async def receive(self, text_data=None, bytes_data=None):
        if not self.session_id:
            return

        try:
            if text_data:
                data = json.loads(text_data)
                message_type = data.get("type")
                if message_type == "media":
                    self.chunk_counter += 1
                    
                    media_blob = data.get("data")
                    if media_blob:
                        media_bytes = b64decode(media_blob)
                        media_path = os.path.join(TEMP_MEDIA_ROOT, f"{self.session_id}_{self.chunk_counter}_media.webm")
                        
                        with open(media_path, "wb") as mf:
                            mf.write(media_bytes)
                            
                        self.media_buffer.append(media_path)
                        await self._process_new_chunk(media_path)
        except Exception as e:
            traceback.print_exc()

    async def _process_new_chunk(self, media_path):
        """Process a new media chunk and trigger window analysis if needed."""
        try:
            # Extract audio and get transcript concurrently
            audio_task = asyncio.create_task(asyncio.to_thread(self.extract_audio, media_path))
            transcript_task = None
            if client:
                transcript_task = asyncio.create_task(asyncio.to_thread(transcribe_audio, media_path))

            # Wait for audio extraction
            audio_path = await audio_task
            if audio_path:
                self.audio_buffer[media_path] = audio_path
                
                # Get transcript if available
                if transcript_task:
                    chunk_transcript = await transcript_task
                    self.transcript_buffer[media_path] = chunk_transcript
                    
                    # Send immediate feedback about transcription
                    await self.send(json.dumps({
                        "type": "transcription_update",
                        "chunk_number": self.chunk_counter,
                        "transcript": chunk_transcript
                    }))

            # Start S3 upload in background
            s3_upload_task = asyncio.create_task(asyncio.to_thread(self.upload_to_s3, media_path))
            
            # Save chunk data in background
            save_task = asyncio.create_task(
                self._complete_chunk_save_in_background(media_path, s3_upload_task)
            )
            self.background_chunk_save_tasks[media_path] = save_task

            # Trigger window analysis if buffer size is sufficient
            if len(self.media_buffer) >= ANALYSIS_WINDOW_SIZE:
                window_paths = list(self.media_buffer[-ANALYSIS_WINDOW_SIZE:])
                asyncio.create_task(self.analyze_windowed_media(window_paths, self.chunk_counter))

            # Clean up old chunks if buffer is too large
            if len(self.media_buffer) > ANALYSIS_WINDOW_SIZE * 2:
                await self._cleanup_old_chunks()

        except Exception as e:
            traceback.print_exc()

    async def analyze_windowed_media(self, window_paths, latest_chunk_number):
        """Handles window analysis with improved performance."""
        try:
            # Get transcripts and audio paths
            transcripts = []
            audio_paths = []
            for media_path in window_paths:
                transcript = self.transcript_buffer.get(media_path, "")
                audio_path = self.audio_buffer.get(media_path)
                if transcript and audio_path:
                    transcripts.append(transcript)
                    audio_paths.append(audio_path)

            if not transcripts or not audio_paths:
                return

            # Combine transcripts and audio
            combined_transcript = " ".join(transcripts)
            combined_audio_path = await self._combine_audio_files(audio_paths, latest_chunk_number)

            if not combined_audio_path:
                return

            # Run analysis with timeout
            try:
                analysis_result = await asyncio.wait_for(
                    asyncio.to_thread(
                        analyze_results,
                        combined_transcript,
                        window_paths[0],
                        combined_audio_path
                    ),
                    timeout=30.0
                )

                if analysis_result:
                    # Send updates to frontend immediately
                    await self.send(json.dumps({
                        "type": "full_analysis_update",
                        "analysis": analysis_result
                    }))

                    # Handle emotion update if available
                    audience_emotion = analysis_result.get('Feedback', {}).get('Audience Emotion')
                    if audience_emotion and s3 and self.room_name:
                        try:
                            lowercase_emotion = audience_emotion.lower()
                            selected_variation = random.randint(1, NUMBER_OF_VARIATIONS)
                            region_name = os.environ.get('AWS_S3_REGION_NAME', os.environ.get('AWS_REGION', 'us-east-1'))
                            emotion_s3_url = f"https://{BUCKET_NAME}.s3.{region_name}.amazonaws.com/{EMOTION_STATIC_FOLDER}/{self.room_name}/{lowercase_emotion}/{selected_variation}.mp4"
                            await self.send(json.dumps({
                                "type": "window_emotion_update",
                                "emotion": audience_emotion,
                                "emotion_s3_url": emotion_s3_url
                            }))
                        except Exception:
                            pass

                    # Save analysis in background
                    save_task = asyncio.create_task(self._save_window_analysis(
                        window_paths[-1],
                        analysis_result,
                        combined_transcript,
                        latest_chunk_number
                    ))
                    self.background_chunk_save_tasks[f"analysis_{latest_chunk_number}"] = save_task

            except asyncio.TimeoutError:
                print(f"Analysis timeout for window ending with chunk {latest_chunk_number}")
            except Exception as e:
                traceback.print_exc()

        except Exception as e:
            traceback.print_exc()
        finally:
            # Clean up combined audio file
            if 'combined_audio_path' in locals():
                await self._remove_file_safe(combined_audio_path)

    async def _combine_audio_files(self, audio_paths, window_number):
        """Combine audio files with improved error handling."""
        if not audio_paths:
            return None

        combined_path = os.path.join(TEMP_MEDIA_ROOT, f"{self.session_id}_window_{window_number}.mp3")
        try:
            # Build FFmpeg command
            cmd = ["ffmpeg", "-y"]
            for path in audio_paths:
                cmd.extend(["-i", path])
            cmd.extend([
                "-filter_complex", f"concat=n={len(audio_paths)}:a=1:v=0",
                "-acodec", "libmp3lame", "-b:a", "128k",
                "-nostats", "-loglevel", "0",
                combined_path
            ])

            # Run FFmpeg with timeout
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await asyncio.wait_for(process.communicate(), timeout=5.0)

            if process.returncode == 0 and os.path.exists(combined_path):
                return combined_path
            return None
        except Exception:
            return None

    async def _cleanup_old_chunks(self):
        """Clean up old chunks with improved error handling."""
        while len(self.media_buffer) > ANALYSIS_WINDOW_SIZE:
            try:
                oldest_media_path = self.media_buffer[0]
                save_task = self.background_chunk_save_tasks.get(oldest_media_path)
                
                if save_task:
                    try:
                        await asyncio.wait_for(save_task, timeout=5.0)
                    except (asyncio.TimeoutError, Exception):
                        continue

                # Remove from buffers
                self.media_buffer.pop(0)
                audio_path = self.audio_buffer.pop(oldest_media_path, None)
                self.transcript_buffer.pop(oldest_media_path, None)
                self.media_path_to_chunk.pop(oldest_media_path, None)
                self.background_chunk_save_tasks.pop(oldest_media_path, None)

                # Clean up files with retries
                if oldest_media_path:
                    await self._remove_file_safe(oldest_media_path)
                if audio_path:
                    await self._remove_file_safe(audio_path)

            except IndexError:
                break
            except Exception:
                break

    def extract_audio(self, media_path):
        """Extracts audio from a media file using FFmpeg."""
        base, _ = os.path.splitext(media_path)
        audio_mp3_path = f"{base}.mp3"
        ffmpeg_command = ["ffmpeg", "-y", "-i", media_path, "-vn", "-acodec", "libmp3lame", "-ab", "128k", "-nostats", "-loglevel", "0", audio_mp3_path]
        
        try:
            process = subprocess.Popen(ffmpeg_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            stdout, stderr = process.communicate()
            
            if process.returncode == 0 and os.path.exists(audio_mp3_path) and os.path.getsize(audio_mp3_path) > 0:
                return audio_mp3_path
            return None
        except Exception:
            return None

    def upload_to_s3(self, file_path):
        """Uploads a local file to S3."""
        if s3 is None:
            return None

        file_name = os.path.basename(file_path)
        folder_path = f"{BASE_FOLDER}{self.session_id}/"
        s3_key = f"{folder_path}{file_name}"
        
        try:
            s3.upload_file(file_path, BUCKET_NAME, s3_key)
            region_name = os.environ.get('AWS_S3_REGION_NAME', os.environ.get('AWS_REGION', 'us-east-1'))
            s3_url = f"https://{BUCKET_NAME}.s3.{region_name}.amazonaws.com/{s3_key}"
            return s3_url
        except Exception:
            return None

    @database_sync_to_async
    def _save_chunk_data(self, media_path, s3_url):
        """Saves the SessionChunk object and maps media path to chunk ID."""
        if not self.session_id or not s3_url:
            return None

        try:
            session = PracticeSession.objects.get(id=self.session_id)
            session_chunk_data = {
                'session': session.id,
                'video_file': s3_url
            }
            
            session_chunk_serializer = SessionChunkSerializer(data=session_chunk_data)
            if session_chunk_serializer.is_valid():
                session_chunk = session_chunk_serializer.save()
                self.media_path_to_chunk[media_path] = session_chunk.id
                return session_chunk.id
            return None
        except Exception:
            return None

    @database_sync_to_async
    def _save_window_analysis(self, media_path_of_last_chunk_in_window, analysis_result, combined_transcript_text, window_chunk_number):
        """Saves the window's analysis result to the database."""
        if not self.session_id:
            return None

        try:
            session_chunk_id = self.media_path_to_chunk.get(media_path_of_last_chunk_in_window)
            if session_chunk_id:
                sentiment_data = {
                    'chunk': session_chunk_id,
                    'chunk_number': window_chunk_number,
                    'chunk_transcript': combined_transcript_text,
                }

                if isinstance(analysis_result, dict) and 'error' not in analysis_result:
                    feedback_data = analysis_result.get('Feedback', {})
                    posture_data = analysis_result.get('Posture', {})
                    scores_data = analysis_result.get('Scores', {})

                    sentiment_data.update({
                        'audience_emotion': feedback_data.get('Audience Emotion'),
                        'conviction': feedback_data.get('Conviction'),
                        'clarity': feedback_data.get('Clarity'),
                        'impact': feedback_data.get('Impact'),
                        'brevity': feedback_data.get('Brevity'),
                        'transformative_potential': feedback_data.get('Transformative Potential'),
                        'trigger_response': feedback_data.get('Trigger Response'),
                        'filler_words': feedback_data.get('Filler Words'),
                        'grammar': feedback_data.get('Grammar'),
                        'general_feedback_summary': feedback_data.get('General Feedback Summary', ''),
                        'posture': posture_data.get('Posture'),
                        'motion': posture_data.get('Motion'),
                        'gestures': bool(posture_data.get('Gestures', False)) if posture_data.get('Gestures') is not None else False,
                        'volume': scores_data.get('Volume Score'),
                        'pitch_variability': scores_data.get('Pitch Variability Score'),
                        'pace': scores_data.get('Pace Score'),
                        'pauses': scores_data.get('Pause Score'),
                    })

                sentiment_serializer = ChunkSentimentAnalysisSerializer(data=sentiment_data)
                if sentiment_serializer.is_valid():
                    sentiment_analysis_obj = sentiment_serializer.save()
                    return sentiment_analysis_obj.id
            return None
        except Exception:
            return None

    async def _complete_chunk_save_in_background(self, media_path, s3_upload_task):
        """Awaits S3 upload and then saves the SessionChunk data."""
        try:
            # Wait for S3 upload to complete in its thread
            s3_url = await s3_upload_task

            if s3_url:
                await self._save_chunk_data(media_path, s3_url)
        except Exception as e:
            traceback.print_exc()
        finally:
            # Clean up the task tracking entry once this task is done
            if media_path in self.background_chunk_save_tasks:
                await asyncio.sleep(0.01)
                if media_path in self.background_chunk_save_tasks:
                    del self.background_chunk_save_tasks[media_path]