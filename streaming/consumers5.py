### Origonal with a twist
### Performs transcript chunk by chunk, with individual transcription.

import asyncio
import platform

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
import random # Import random for selecting variations

from base64 import b64decode
from datetime import timedelta

from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async

from .sentiment_analysis import analyze_results, transcribe_audio
from practice_sessions.models import PracticeSession, SessionChunk, ChunkSentimentAnalysis
from practice_sessions.serializers import SessionChunkSerializer, ChunkSentimentAnalysisSerializer, PracticeSessionSerializer

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "EngageX.settings")
django.setup()

openai.api_key = os.environ.get("OPENAI_API_KEY")
client = openai.OpenAI()

s3 = boto3.client("s3", region_name=os.environ.get('AWS_REGION'))
BUCKET_NAME = "engagex-user-content-1234"
BASE_FOLDER = "user-videos/"
TEMP_MEDIA_ROOT = tempfile.gettempdir()
EMOTION_STATIC_FOLDER = "static-videos"  # Top-level folder for static emotion videos

# Define the rooms the user can choose from. Used for validation.
POSSIBLE_ROOMS = ['conference_room', 'board_room_1', 'board_room_2']

# Assume a fixed number of variations for each emotion video (1.mp4 to 5.mp4)
NUMBER_OF_VARIATIONS = 5

# Define window size for transcript analysis
WINDOW_SIZE = 4


class LiveSessionConsumer(AsyncWebsocketConsumer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.session_id = None
        self.room_name = None # Store the chosen room name
        self.chunk_counter = 0
        self.media_buffer = []
        self.audio_buffer = {}  # Dictionary to map media_path to audio_path
        self.media_path_to_chunk = {} # Map media_path to SessionChunk ID
        self.transcript_buffer = []  # NEW: List to store individual chunk transcripts
        self.transcript_to_audio_path = {}  # NEW: Map transcript to its source audio path


    async def connect(self):
        query_string = self.scope['query_string'].decode()
        query_params = {}
        if query_string:
            for param in query_string.split('&'):
                try:
                    key, value = param.split('=', 1)
                    query_params[key] = value
                except ValueError:
                    print(f"WS: Warning: Could not parse query parameter: {param}")

        self.session_id = query_params.get('session_id', None)
        self.room_name = query_params.get('room_name', None) # Get room_name from query params

        # Validate session_id and room_name
        if self.session_id and self.room_name in POSSIBLE_ROOMS:
            print(f"WS: Client connected for Session ID: {self.session_id}, Room: {self.room_name}")
            await self.accept()
            await self.send(json.dumps({
                "type": "connection_established",
                "message": f"Connected to session {self.session_id} in room {self.room_name}"
            }))
        else:
            if not self.session_id:
                print("WS: Connection rejected: Missing session_id.")
            elif self.room_name is None:
                 print("WS: Connection rejected: Missing room_name.")
            else: # room_name is provided but not in POSSIBLE_ROOMS
                 print(f"WS: Connection rejected: Invalid room_name '{self.room_name}'.")

            await self.close()

    async def disconnect(self, close_code):
        print(f"WS: Client disconnected for Session ID: {self.session_id}. Cleaning up...")
        # Add cleanup for audio and media files from buffers
        # Get all paths from buffers and the map keys
        audio_paths_to_clean = list(self.audio_buffer.values())
        media_paths_to_clean_from_buffer = list(self.media_buffer)
        media_paths_to_clean_from_map = list(self.media_path_to_chunk.keys()) # Clean up any not yet popped from buffer

        # Combine all potential paths and remove duplicates
        all_paths_to_clean = set(audio_paths_to_clean + media_paths_to_clean_from_buffer + media_paths_to_clean_from_map)

        for file_path in all_paths_to_clean:
            try:
                if file_path and os.path.exists(file_path):
                    os.remove(file_path)
                    print(f"WS: Removed temporary file: {file_path}")
            except Exception as e:
                print(f"WS: Error removing file {file_path}: {e}")

        # Clear buffers and maps
        self.audio_buffer = {}
        self.media_buffer = []
        self.media_path_to_chunk = {}
        self.transcript_buffer = []  # NEW: Clear transcript buffer
        self.transcript_to_audio_path = {}  # NEW: Clear transcript to audio path mapping

        print(f"WS: Session {self.session_id} cleanup complete.")


    async def receive(self, text_data=None, bytes_data=None):
        if not self.session_id:
            print("WS: Error: Session ID not available, cannot process data.")
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
                        print(f"WS: Received media chunk {self.chunk_counter} for Session {self.session_id}. Saved to {media_path}")
                        self.media_buffer.append(media_path)

                        # Wait for process_media_chunk to complete (ensuring audio extraction and transcription)
                        process_task = asyncio.create_task(self.process_media_chunk(media_path))
                        await process_task

                        # Trigger windowed analysis based on transcript buffer size
                        if len(self.transcript_buffer) >= WINDOW_SIZE:
                            window_transcripts = list(self.transcript_buffer[-WINDOW_SIZE:])  # Take the last WINDOW_SIZE transcripts
                            print(f"WS: Triggering windowed analysis for sliding window (chunks ending with {self.chunk_counter})")
                            # Pass the last chunk number in the window and window transcripts
                            asyncio.create_task(self.analyze_windowed_transcripts(window_transcripts, self.chunk_counter))

                    else:
                        print("WS: Error: Missing 'data' in media message.")
                else:
                    print(f"WS: Received text message of type: {message_type}")
            elif bytes_data:
                print(f"WS: Received binary data of length: {len(bytes_data)}")
        except json.JSONDecodeError:
             print(f"WS: Received invalid JSON data: {text_data}")
        except Exception as e:
            print(f"WS: Error processing received data: {e}")
            traceback.print_exc()


    async def process_media_chunk(self, media_path):
        start_time = time.time()
        print(f"WS: process_media_chunk started for: {media_path} at {start_time}")
        audio_path = None
        s3_url = None
        try:
            # Use concurrent.futures.ThreadPoolExecutor for sync operations (upload and audio extraction)
            with concurrent.futures.ThreadPoolExecutor() as executor:
                # Submit upload to S3 as a task
                upload_future = executor.submit(self.upload_to_s3, media_path)

                # Submit audio extraction as a task
                extract_future = executor.submit(self.extract_audio, media_path)

                # Wait for both tasks to complete in a thread-safe way
                s3_url = await asyncio.to_thread(upload_future.result)
                audio_path = await asyncio.to_thread(extract_future.result) # Wait for audio extraction here

            # Store audio_path in buffer AFTER extraction completes
            if audio_path and os.path.exists(audio_path): # Also check if the extracted file actually exists
                print(f"WS: Audio extracted and found at: {audio_path}")
                self.audio_buffer[media_path] = audio_path # Store the mapping
                
                # NEW: Transcribe individual audio chunk
                if client:
                    print(f"WS: Transcribing individual audio chunk: {audio_path}")
                    transcript_text = await asyncio.to_thread(transcribe_audio, audio_path)
                    print(f"WS: Individual chunk transcription result: {transcript_text}")
                    
                    # Store the transcript in the buffer and map it to its audio path
                    if transcript_text:
                        self.transcript_buffer.append(transcript_text)
                        self.transcript_to_audio_path[transcript_text] = audio_path
                    else:
                        print(f"WS: No transcript obtained for audio chunk: {audio_path}")
                else:
                    print("WS: OpenAI client not initialized (missing API key?). Skipping transcription.")
            else:
                print(f"WS: Audio extraction failed or file not found for {media_path}. Audio path: {audio_path}")

            if s3_url:
                print(f"WS: Attempting to save SessionChunk for {media_path} with S3 URL {s3_url}.")
                # Call _save_chunk_data with the S3 URL
                await asyncio.to_thread(self._save_chunk_data, media_path, s3_url) # Pass the obtained s3_url
            else:
                print(f"WS: Error: S3 upload failed for {media_path}. Cannot save SessionChunk.")

        except Exception as e:
            print(f"WS: Error in process_media_chunk for {media_path}: {e}")
            traceback.print_exc()

        print(f"WS: process_media_chunk finished for: {media_path} after {time.time() - start_time:.2f} seconds")


    async def analyze_windowed_transcripts(self, window_transcripts, latest_chunk_number):
        """
        Handles analysis of a window of transcripts and saving sentiment data.
        This replaces the previous analyze_windowed_media function.
        """
        if len(window_transcripts) != WINDOW_SIZE:
            print(f"WS: analyze_windowed_transcripts called with {len(window_transcripts)} transcripts for window ending with chunk {latest_chunk_number}, expected {WINDOW_SIZE}. Skipping analysis.")
            return

        start_time = time.time()
        window_chunk_number = latest_chunk_number
        
        # Get the last media path in the window for reference
        last_media_path = self.media_buffer[-1] if self.media_buffer else None
        if not last_media_path:
            print(f"WS: No last media path found for window ending with chunk {window_chunk_number}. Skipping analysis.")
            return

        print(f"WS: analyze_windowed_transcripts started for window ending with chunk {window_chunk_number} at {start_time}")

        analysis_result = None

        try:
            # Combine the transcripts in the window
            combined_transcript = " ".join(window_transcripts)
            print(f"WS: Combined transcript for window ending with chunk {window_chunk_number}: {combined_transcript}")

            # Get the audio path of the first transcript for visual analysis reference
            first_audio_associated_media = None
            if self.transcript_to_audio_path.get(window_transcripts[0]):
                # Find the media path associated with this audio path
                for media_path, audio_path in self.audio_buffer.items():
                    if audio_path == self.transcript_to_audio_path.get(window_transcripts[0]):
                        first_audio_associated_media = media_path
                        break

            # Use the first media path in the window if we couldn't find the specific one
            if not first_audio_associated_media and self.media_buffer:
                first_audio_associated_media = self.media_buffer[0]

            # Get the audio path associated with the last transcript for analysis
            last_audio_path = self.transcript_to_audio_path.get(window_transcripts[-1])

            if combined_transcript and client:
                # Analyze results using OpenAI
                analysis_start_time = time.time()
                analysis_result = await asyncio.to_thread(
                    analyze_results, 
                    combined_transcript, 
                    first_audio_associated_media,  # Visual reference from first in window
                    last_audio_path if last_audio_path else None  # Audio reference from last in window
                )
                print(f"WS: Analysis Result: {analysis_result} after {time.time() - analysis_start_time:.2f} seconds")
            elif combined_transcript:
                print("WS: OpenAI client not initialized. Skipping analysis despite having transcript.")
            else:
                print(f"WS: No combined transcript obtained. Skipping analysis.")

            # Saving Analysis and sending updates - same as before
            if analysis_result:
                # Send analysis updates to the frontend
                audience_emotion = analysis_result.get('Feedback', {}).get('Audience Emotion')

                emotion_s3_url = None
                # Only try to construct URL if we have an emotion, S3 client, and room name
                if audience_emotion and s3 and self.room_name:
                    try:
                        # Convert emotion to lowercase for S3 path lookup
                        lowercase_emotion = audience_emotion.lower()

                        # Randomly select a variation number between 1 and NUMBER_OF_VARIATIONS
                        selected_variation = random.randint(1, NUMBER_OF_VARIATIONS)

                        # Construct the new S3 URL with room and variation
                        region_name = os.environ.get('AWS_S3_REGION_NAME', os.environ.get('AWS_REGION', 'us-east-1')) # Default region if none set
                        emotion_s3_url = f"https://{BUCKET_NAME}.s3.{region_name}.amazonaws.com/{EMOTION_STATIC_FOLDER}/{self.room_name}/{lowercase_emotion}/{selected_variation}.mp4"

                        await self.send(json.dumps({
                            "type": "window_emotion_update",
                            "emotion": audience_emotion,
                            "emotion_s3_url": emotion_s3_url
                        }))
                    except Exception as e:
                         print(f"WS: Error constructing or sending emotion URL for emotion '{audience_emotion}': {e}")
                         traceback.print_exc()

                elif audience_emotion:
                     print("WS: Audience emotion detected but S3 client not configured or room_name is missing, cannot send static video URL.")
                else:
                     print("WS: No audience emotion detected. Cannot send static video URL.")

                await self.send(json.dumps({
                    "type": "full_analysis_update",
                    "analysis": analysis_result
                }))

                # Save the analysis for the last chunk in the window
                print(f"WS: Calling _save_window_analysis for chunk {window_chunk_number} ({last_media_path})...")
                await asyncio.to_thread(self._save_window_analysis, last_media_path, analysis_result, combined_transcript, window_chunk_number)
            else:
                print(f"WS: No analysis result obtained for window ending with chunk {window_chunk_number}. Skipping analysis save.")

        except Exception as e:
            print(f"WS: Error during windowed transcript analysis ending with chunk {window_chunk_number}: {e}")
            traceback.print_exc()
        finally:
            # Clean up the oldest transcript from the buffer after an analysis attempt
            if len(self.transcript_buffer) > WINDOW_SIZE:
                try:
                    oldest_transcript = self.transcript_buffer.pop(0)
                    print(f"WS: Removed oldest transcript from buffer.")
                    # Remove from mapping if it exists
                    if oldest_transcript in self.transcript_to_audio_path:
                        del self.transcript_to_audio_path[oldest_transcript]
                except IndexError:
                    print("WS: transcript_buffer was empty during cleanup in analyze_windowed_transcripts finally.")

            # Clean up the oldest chunk from the buffers after an analysis attempt for a window finishes.
            # This happens for the oldest chunk if the buffer is >= WINDOW_SIZE.
            if len(self.media_buffer) >= WINDOW_SIZE:
                try:
                    oldest_media_path = self.media_buffer.pop(0)
                    print(f"WS: Removed oldest media chunk {oldest_media_path} from buffer.")
                    oldest_audio_path = self.audio_buffer.pop(oldest_media_path, None)
                    if oldest_audio_path and os.path.exists(oldest_audio_path):
                        try:
                            os.remove(oldest_audio_path)
                            print(f"WS: Removed oldest temporary audio file: {oldest_audio_path}")
                        except Exception as e:
                            print(f"WS: Error removing oldest temporary audio file {oldest_audio_path}: {e}")
                    elif oldest_audio_path:
                        print(f"WS: Oldest audio path {oldest_audio_path} was in buffer but file not found during cleanup.")
                    else:
                        print(f"WS: No audio path found in buffer for oldest media path {oldest_media_path} during cleanup.")
                except IndexError:
                    # Buffer might have been cleared by disconnect during processing
                    print("WS: media_buffer was empty during cleanup in analyze_windowed_transcripts finally.")

        print(f"WS: analyze_windowed_transcripts finished for window ending with chunk {window_chunk_number} after {time.time() - start_time:.2f} seconds")

    def extract_audio(self, media_path):
        """Extracts audio from a media file using FFmpeg."""
        start_time = time.time()
        base, _ = os.path.splitext(media_path)
        audio_mp3_path = f"{base}.mp3"
        # Use list format for command for better security and compatibility
        ffmpeg_command = ["ffmpeg", "-y", "-i", media_path, "-vn", "-acodec", "libmp3lame", "-ab", "128k", audio_mp3_path]
        print(f"WS: Running FFmpeg command: {' '.join(ffmpeg_command)}")
        try:
            # Use shell=False (default) with list format
            process = subprocess.Popen(ffmpeg_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            stdout, stderr = process.communicate()
            returncode = process.returncode
            if returncode == 0:
                print(f"WS: Audio extracted to: {audio_mp3_path} after {time.time() - start_time:.2f} seconds")
                # Verify file exists and has non-zero size
                if os.path.exists(audio_mp3_path) and os.path.getsize(audio_mp3_path) > 0:
                    return audio_mp3_path
                else:
                     print(f"WS: Extracted audio file is missing or empty: {audio_mp3_path}")
                     return None

            else:
                error_output = stderr.decode()
                print(f"WS: FFmpeg audio extraction error (code {returncode}): {error_output}")
                print(f"WS: FFmpeg stdout: {stdout.decode()}")
                # Clean up potentially created empty/partial file
                if os.path.exists(audio_mp3_path):
                     try:
                         os.remove(audio_mp3_path)
                         print(f"WS: Removed incomplete audio file after FFmpeg error: {audio_mp3_path}")
                     except Exception as e:
                         print(f"WS: Error removing incomplete audio file {audio_mp3_path}: {e}")
                return None
        except FileNotFoundError:
             print(f"WS: FFmpeg command not found. Is FFmpeg installed and in your PATH?")
             return None
        except Exception as e:
             print(f"WS: Error running FFmpeg for audio extraction: {e}")
             traceback.print_exc()
             return None

    def upload_to_s3(self, file_path):
        """Uploads a local file to S3."""
        if s3 is None:
             print(f"WS: S3 client is not initialized. Cannot upload file: {file_path}.")
             return None

        start_time = time.time()
        file_name = os.path.basename(file_path)
        folder_path = f"{BASE_FOLDER}{self.session_id}/"
        s3_key = f"{folder_path}{file_name}"
        try:
            s3.upload_file(file_path, BUCKET_NAME, s3_key)
            # Construct S3 URL - using regional endpoint format
            region_name = os.environ.get('AWS_S3_REGION_NAME', os.environ.get('AWS_REGION', 'us-east-1'))
            s3_url = f"https://{BUCKET_NAME}.s3.{region_name}.amazonaws.com/{s3_key}"
            # print(f"WS: Uploaded {file_path} to S3 successfully. S3 URL: {s3_url} after {time.time() - start_time:.2f} seconds.")
            return s3_url
        except Exception as e:
            print(f"WS: S3 upload failed for {file_path}: {e}")
            traceback.print_exc()
            return None

    def _save_chunk_data(self, media_path, s3_url):
        """Saves the SessionChunk object and maps media path to chunk ID."""
        start_time = time.time()
        # Log the arguments received
        print(f"WS: _save_chunk_data called for chunk at {media_path} with S3 URL {s3_url} at {start_time}")
        if not self.session_id:
            print("WS: Error: Session ID not available, cannot save chunk data.")
            return

        if not s3_url:
             print(f"WS: Error: S3 URL not provided for {media_path}. Cannot save SessionChunk.")
             return # Do not save if S3 URL is missing

        try:
            # Synchronous DB call: Get the session
            print(f"WS: Attempting to get PracticeSession with id: {self.session_id}")
            try:
                 session = PracticeSession.objects.get(id=self.session_id)
                 print(f"WS: Retrieved PracticeSession: {session.id}, {session.session_name}")
            except PracticeSession.DoesNotExist:
                 print(f"WS: Error: PracticeSession with id {self.session_id} not found. Cannot save chunk data.")
                 return # Exit if session doesn't exist

            print(f"WS: S3 URL for SessionChunk: {s3_url}")
            session_chunk_data = {
                'session': session.id, # Link to the session using its ID
                'video_file': s3_url # Use the passed S3 URL
            }
            print(f"WS: SessionChunk data: {session_chunk_data}")
            session_chunk_serializer = SessionChunkSerializer(data=session_chunk_data)

            if session_chunk_serializer.is_valid():
                print("WS: SessionChunkSerializer is valid.")
                try:
                    # Synchronous DB call: Save the SessionChunk
                    session_chunk = session_chunk_serializer.save()
                    print(f"WS: SessionChunk saved with ID: {session_chunk.id} for media path: {media_path} after {time.time() - start_time:.2f} seconds")
                    # Store the mapping from temporary media path to the saved chunk's ID
                    self.media_path_to_chunk[media_path] = session_chunk.id
                    print(f"WS: Added mapping: {media_path} -> {session_chunk.id}")

                except Exception as save_error:
                    print(f"WS: Error during SessionChunk save: {save_error}")
                    traceback.print_exc()
            else:
                print("WS: Error saving SessionChunk:", session_chunk_serializer.errors)

        except Exception as e: # Catching other potential exceptions during DB interaction etc.
            print(f"WS: Error in _save_chunk_data: {e}")
            traceback.print_exc()
        print(f"WS: _save_chunk_data finished after {time.time() - start_time:.2f} seconds")

    # This method is called using asyncio.to_thread from analyze_windowed_transcripts.
    # It saves the analysis results to the database.
    def _save_window_analysis(self, media_path, analysis_result, transcript_text, chunk_number):
        start_time = time.time()
        print(f"WS: _save_window_analysis started for media path: {media_path} (chunk {chunk_number}) at {start_time}")
        if not self.session_id:
            print("WS: Error: Session ID not available, cannot save window analysis.")
            return

        try:
            # Get the SessionChunk ID from the map that was populated in _save_chunk_data
            # This is synchronous because _save_window_analysis is already running in a thread.
            session_chunk_id = self.media_path_to_chunk.get(media_path)

            # print(f"WS: In _save_window_analysis for {media_path} (chunk {chunk_number}): session_chunk_id found? {session_chunk_id is not None}. ID: {session_chunk_id}")

            if session_chunk_id:
                # print(f"WS: Found SessionChunk ID: {session_chunk_id} for media path: {media_path}")

                # Safely access nested dictionaries from analysis_result
                feedback_data = analysis_result.get('Feedback', {})
                posture_data = analysis_result.get('Posture', {})
                scores_data = analysis_result.get('Scores', {})

                # Prepare data for ChunkSentimentAnalysis based on the expected structure from analyze_results
                sentiment_data = {
                    'chunk': session_chunk_id, # Link to the SessionChunk using its ID
                    'chunk_number': chunk_number, # Store the chunk number for context

                    # Map from 'Feedback'
                    'audience_emotion': feedback_data.get('Audience Emotion'),
                    'conviction': feedback_data.get('Conviction'), # Use get, default is None if key missing
                    'clarity': feedback_data.get('Clarity'),
                    'impact': feedback_data.get('Impact'),
                    'brevity': feedback_data.get('Brevity'),
                    'transformative_potential': feedback_data.get('Transformative Potential'),
                    'trigger_response': feedback_data.get('Trigger Response'),
                    'filler_words': feedback_data.get('Filler Words'),
                    'grammar': feedback_data.get('Grammar'),
                    'general_feedback_summary': feedback_data.get('General Feedback Summary', ''), # Default to empty string


                    # Map from 'Posture'
                    'posture': posture_data.get('Posture'),
                    'motion': posture_data.get('Motion'),
                    # Assuming Gestures is a boolean in analysis_result or can be converted
                    'gestures': bool(posture_data.get('Gestures', False)), # Ensure boolean or default to False


                    # Map from the 'Scores' nested dictionary
                    'volume': scores_data.get('Volume Score'),
                    'pitch_variability': scores_data.get('Pitch Variability Score'),
                    'pace': scores_data.get('Pace Score'),
                    'pauses': scores_data.get('Pause Score'), # Use Pause Score key

                    # Add the combined transcript
                    'chunk_transcript': transcript_text,
                }

                # print(f"WS: ChunkSentimentAnalysis data (for window, chunk {chunk_number}): {sentiment_data}")

                # Use the serializer to validate and prepare data for saving
                sentiment_serializer = ChunkSentimentAnalysisSerializer(data=sentiment_data)

                if sentiment_serializer.is_valid():
                    # print(f"WS: ChunkSentimentAnalysisSerializer (for window, chunk {chunk_number}) is valid.")
                    try:
                        # Synchronous database call to save the sentiment analysis
                        sentiment_analysis_obj = sentiment_serializer.save()

                        # print(f"WS: Window analysis data saved for chunk ID: {session_chunk_id} (chunk {chunk_number}) with sentiment ID: {sentiment_analysis_obj.id} after {time.time() - start_time:.2f} seconds")

                    except Exception as save_error:
                        print(f"WS: Error during ChunkSentimentAnalysis save (for window, chunk {chunk_number}): {save_error}")
                        traceback.print_exc() # Print traceback for save errors
                else:
                    # Print validation errors if serializer is not valid
                    print(f"WS: Error saving ChunkSentimentAnalysis (chunk {chunk_number}):", sentiment_serializer.errors)

            else:
                # This logs if session_chunk_id was None (meaning _save_chunk_data failed or hasn't run)
                error_message = f"SessionChunk ID not found in media_path_to_chunk for media path {media_path} during window analysis save for chunk {chunk_number}. Analysis will not be saved for this chunk."
                print(f"WS: {error_message}")

        except Exception as e:
            print(f"WS: Error in _save_window_analysis for media path {media_path} (chunk {chunk_number}): {e}")
            traceback.print_exc() # Print traceback for general _save_window_analysis errors

        print(f"WS: _save_window_analysis finished for media path {media_path} (chunk {chunk_number}) after {time.time() - start_time:.2f} seconds")
