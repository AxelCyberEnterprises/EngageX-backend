#################################################################################################################
# This version eliminates race conditions by removing shared mutable state and making each operation self-contained
#################################################################################################################

### Works but is slow on deployed (probably due to direct and excessive db querying) ###


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
        # Track active tasks for cleanup
        self.active_tasks = set()
        # Track media paths for cleanup
        self.media_paths = set()

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
        # Wait for all active tasks to complete
        if self.active_tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self.active_tasks, return_exceptions=True),
                    timeout=30.0
                )
            except (asyncio.TimeoutError, Exception):
                pass

        # Clean up all temporary files
        await self._cleanup_all_files()

    async def _cleanup_all_files(self):
        """Clean up all temporary files associated with this session."""
        cleanup_tasks = []
        for file_path in self.media_paths:
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
                            
                        self.media_paths.add(media_path)
                        # Create a task for processing this chunk
                        task = asyncio.create_task(self._process_new_chunk(media_path, self.chunk_counter))
                        self.active_tasks.add(task)
                        task.add_done_callback(self.active_tasks.discard)
        except Exception as e:
            traceback.print_exc()

    async def _process_new_chunk(self, media_path, chunk_number):
        """Process a new media chunk and trigger window analysis if needed."""
        try:
            print(f"Processing chunk {chunk_number} for session {self.session_id}")
            
            # Extract audio and get transcript concurrently
            audio_task = asyncio.create_task(asyncio.to_thread(self.extract_audio, media_path))
            transcript_task = None
            if client:
                transcript_task = asyncio.create_task(asyncio.to_thread(transcribe_audio, media_path))

            # Wait for audio extraction
            audio_path = await audio_task
            if audio_path:
                print(f"Audio extracted for chunk {chunk_number}: {audio_path}")
                self.media_paths.add(audio_path)
                
                # Get transcript if available
                chunk_transcript = None
                if transcript_task:
                    chunk_transcript = await transcript_task
                    print(f"Transcript received for chunk {chunk_number}: {chunk_transcript}")
                    
                    # Send immediate feedback about transcription
                    await self.send(json.dumps({
                        "type": "transcription_update",
                        "chunk_number": chunk_number,
                        "transcript": chunk_transcript
                    }))

                # Start S3 upload and wait for it to complete
                try:
                    s3_url = await asyncio.to_thread(self.upload_to_s3, media_path)
                    if not s3_url:
                        print(f"S3 upload failed for chunk {chunk_number}")
                        return
                    
                    # Save chunk data and wait for it to complete
                    chunk_id = await self._save_chunk_data(media_path, s3_url, chunk_number, chunk_transcript, audio_path)
                    if not chunk_id:
                        print(f"Failed to save chunk {chunk_number}")
                        return

                    # Trigger window analysis if we have enough chunks
                    if chunk_number >= ANALYSIS_WINDOW_SIZE:
                        print(f"Triggering window analysis for chunk {chunk_number}")
                        # Create a task for window analysis
                        analysis_task = asyncio.create_task(
                            self._analyze_window(chunk_number)
                        )
                        self.active_tasks.add(analysis_task)
                        analysis_task.add_done_callback(self.active_tasks.discard)
                except Exception as e:
                    print(f"Error during S3 upload for chunk {chunk_number}: {e}")

        except Exception as e:
            print(f"Error processing chunk {chunk_number}: {e}")
            traceback.print_exc()

    async def _analyze_window(self, latest_chunk_number):
        """Analyze a window of chunks."""
        try:
            print(f"Starting window analysis for chunk {latest_chunk_number}")
            
            # Add a longer delay to ensure chunks are saved
            await asyncio.sleep(1.0)
            
            # Add retry mechanism for database query
            max_retries = 3
            retry_delay = 0.5
            chunks = None
            
            for attempt in range(max_retries):
                try:
                    chunks = await self._get_window_chunks(latest_chunk_number)
                    if chunks:
                        break
                    print(f"Retry {attempt + 1}/{max_retries} for window ending with {latest_chunk_number}")
                    await asyncio.sleep(retry_delay)
                except RuntimeError as e:
                    if "cannot schedule new futures after shutdown" in str(e):
                        print("Event loop is shutting down, stopping window analysis")
                        return
                    raise
            
            if not chunks:
                print(f"No chunks found for window ending with {latest_chunk_number} after {max_retries} retries")
                return

            print(f"Found {len(chunks)} chunks for window analysis")
            # Get transcripts and audio paths
            transcripts = []
            audio_paths = []
            for chunk in chunks:
                if chunk.transcript:
                    transcripts.append(chunk.transcript)
                if chunk.audio_path and os.path.exists(chunk.audio_path):
                    audio_paths.append(chunk.audio_path)

            if not transcripts or not audio_paths:
                print(f"Missing transcripts or audio paths for window {latest_chunk_number}")
                return

            print(f"Combining {len(transcripts)} transcripts and {len(audio_paths)} audio files")
            # Combine transcripts and audio
            combined_transcript = " ".join(transcripts)
            combined_audio_path = await self._combine_audio_files(audio_paths, latest_chunk_number)

            if not combined_audio_path:
                print(f"Failed to combine audio files for window {latest_chunk_number}")
                return

            print(f"Running analysis for window {latest_chunk_number}")
            # Run analysis with timeout
            try:
                analysis_result = await asyncio.wait_for(
                    asyncio.to_thread(
                        analyze_results,
                        combined_transcript,
                        chunks[0].video_file,
                        combined_audio_path
                    ),
                    timeout=30.0
                )

                if analysis_result:
                    print(f"Analysis completed for window {latest_chunk_number}")
                    # Convert numpy types to native Python types before sending
                    serializable_result = convert_numpy_types(analysis_result)
                    
                    # Add posture data to the analysis result
                    if 'video_url' in analysis_result:
                        serializable_result['Posture'] = {
                            'video_url': analysis_result['video_url']
                        }

                    # Send updates to frontend immediately
                    await self.send(json.dumps({
                        "type": "full_analysis_update",
                        "analysis": serializable_result
                    }))

                    # Handle emotion update if available
                    audience_emotion = serializable_result.get('Feedback', {}).get('Audience Emotion')
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
                        except Exception as e:
                            print(f"Error sending emotion update: {e}")

                    # Save analysis in background
                    save_task = asyncio.create_task(
                        self._save_window_analysis(chunks, serializable_result, combined_transcript, latest_chunk_number)
                    )
                    self.active_tasks.add(save_task)
                    save_task.add_done_callback(self.active_tasks.discard)

            except asyncio.TimeoutError:
                print(f"Analysis timeout for window ending with chunk {latest_chunk_number}")
            except Exception as e:
                print(f"Error during analysis: {e}")
                traceback.print_exc()

        except Exception as e:
            print(f"Error in window analysis: {e}")
            traceback.print_exc()
        finally:
            # Clean up combined audio file
            if 'combined_audio_path' in locals():
                await self._remove_file_safe(combined_audio_path)

    @database_sync_to_async
    def _get_window_chunks(self, latest_chunk_number):
        """Get the chunks for a window from the database."""
        try:
            start_chunk = latest_chunk_number - ANALYSIS_WINDOW_SIZE + 1
            print(f"Querying database for chunks from {start_chunk} to {latest_chunk_number}")
            
            # Use select_related to optimize the query
            chunks = list(SessionChunk.objects.select_related('session').filter(
                session_id=self.session_id,
                chunk_number__gte=start_chunk,
                chunk_number__lte=latest_chunk_number
            ).order_by('chunk_number'))
            
            print(f"Found {len(chunks)} chunks in database")
            if chunks:
                print(f"Chunk IDs: {[chunk.id for chunk in chunks]}")
                print(f"Chunk numbers: {[chunk.chunk_number for chunk in chunks]}")
            return chunks
        except Exception as e:
            print(f"Error querying database for chunks: {e}")
            traceback.print_exc()
            return []

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

            print(f"Running FFmpeg command: {' '.join(cmd)}")
            
            # Use subprocess.Popen directly like in consumers2.py
            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            
            # Run the process in a thread pool to avoid blocking
            stdout, stderr = await asyncio.to_thread(process.communicate)
            returncode = process.returncode

            if returncode == 0 and os.path.exists(combined_path):
                # Verify the file was created and has content
                if os.path.getsize(combined_path) > 0:
                    print(f"Successfully combined {len(audio_paths)} audio files to {combined_path}")
                    return combined_path
                else:
                    print(f"Combined audio file is empty: {combined_path}")
                    return None
            else:
                error_output = stderr.decode() if stderr else "No error output"
                print(f"FFmpeg audio concatenation error (code {returncode}) for window {window_number}: {error_output}")
                return None
                
        except Exception as e:
            print(f"Error during audio concatenation for window {window_number}: {e}")
            traceback.print_exc()
            return None

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
    def _save_chunk_data(self, media_path, s3_url, chunk_number, transcript, audio_path):
        """Saves the SessionChunk object."""
        try:
            print(f"Saving chunk {chunk_number} data")
            if not s3_url:
                print(f"No S3 URL provided for chunk {chunk_number}")
                return None

            # Get the session
            session = PracticeSession.objects.get(id=self.session_id)
            
            # Create and save the chunk
            session_chunk_data = {
                'session': session.id,
                'video_file': s3_url,
                'chunk_number': chunk_number,
                'transcript': transcript,
                'audio_path': audio_path
            }
            
            print(f"Chunk data to save: {session_chunk_data}")
            session_chunk_serializer = SessionChunkSerializer(data=session_chunk_data)
            
            if session_chunk_serializer.is_valid():
                try:
                    # Use transaction.atomic to ensure the save is committed
                    from django.db import transaction
                    with transaction.atomic():
                        session_chunk = session_chunk_serializer.save()
                        print(f"Chunk {chunk_number} saved with ID: {session_chunk.id}")
                        # Verify the save by querying the database
                        saved_chunk = SessionChunk.objects.get(id=session_chunk.id)
                        print(f"Verified saved chunk: {saved_chunk.id}, number: {saved_chunk.chunk_number}")
                        return session_chunk.id
                except Exception as save_error:
                    print(f"Error during chunk save: {save_error}")
                    traceback.print_exc()
                    return None
            else:
                print(f"Invalid chunk data for {chunk_number}: {session_chunk_serializer.errors}")
            return None
        except Exception as e:
            print(f"Error saving chunk {chunk_number}: {e}")
            traceback.print_exc()
            return None

    @database_sync_to_async
    def _save_window_analysis(self, chunks, analysis_result, combined_transcript_text, window_chunk_number):
        """Saves the window's analysis result to the database."""
        if not self.session_id or not chunks:
            print("Missing session ID or chunks for window analysis save")
            return None

        try:
            print(f"Saving window analysis for chunk {window_chunk_number}")
            # Get the last chunk in the window
            last_chunk = chunks[-1]

            # Initialize sentiment data
            sentiment_data = {
                'chunk': last_chunk.id,
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
                print(f"Window analysis saved with ID: {sentiment_analysis_obj.id}")
                return sentiment_analysis_obj.id
            else:
                print(f"Invalid sentiment data: {sentiment_serializer.errors}")
            return None
        except Exception as e:
            print(f"Error saving window analysis: {e}")
            return None