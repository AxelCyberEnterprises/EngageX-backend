#################################################################################################################
# This version uses the original mechanism but now runs the database querying and s3 upload in the background
#################################################################################################################

# Cleanest working version 6-8 secs sentiment analysis
# Fixed the order of the first three chunks
# S3 video concatenation
# Added AI Audience Question functionality triggered at intervals and controlled by a toggle
# Updated S3 bucket structure to include user ID
# Fixed SynchronousOnlyOperation error in connect by accessing user within sync_to_async
# Fixed S3 key extraction for compilation with new URL format
# Handled CancelledError in background task

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
import random # Import random for selecting variations
import numpy as np # Import numpy to handle potential numpy types
from urllib.parse import urlparse # Import urlparse for S3 URL parsing

from base64 import b64decode
from datetime import timedelta

from channels.generic.websocket import AsyncWebsocketConsumer
# Import database_sync_to_async for handling synchronous database operations in async context
from channels.db import database_sync_to_async

# Assuming these are in a local file sentiment_analysis.py
# transcribe_audio now needs to handle a single audio file (used in process_media_chunk)
# analyze_results now receives a concatenated transcript and the combined audio path (like the original)
# Import the ai_audience_question function
from .sentiment_analysis import analyze_results, transcribe_audio, ai_audience_question

from practice_sessions.models import PracticeSession, SessionChunk, ChunkSentimentAnalysis
from practice_sessions.serializers import SessionChunkSerializer, ChunkSentimentAnalysisSerializer # PracticeSessionSerializer might not be directly needed here
from django.contrib.auth import get_user_model # Import to get the User model

User = get_user_model() # Get the active user model

# Ensure Django settings are configured
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "EngageX.settings")
django.setup()

# Initialize OpenAI client
# Ensure OPENAI_API_KEY is set in your environment
openai.api_key = os.environ.get("OPENAI_API_KEY")
client = openai.OpenAI() if openai.api_key else None # Initialize client only if API key is available

# Initialize S3 client
# Ensure AWS_REGION is set in your environment or settings
s3 = boto3.client("s3", region_name=os.environ.get('AWS_REGION'))
BUCKET_NAME = "engagex-user-content-1234" # Replace with your actual S3 bucket name
BASE_FOLDER = "user-videos/" # Base folder in S3 bucket
TEMP_MEDIA_ROOT = tempfile.gettempdir() # Use system's temporary directory
EMOTION_STATIC_FOLDER = "static-videos"  # Top-level folder for static emotion videos

# Define the rooms the user can choose from. Used for validation.
POSSIBLE_ROOMS = ['conference_room', 'board_room_1', 'board_room_2']

# Assume a fixed number of variations for each emotion video (1.mp4 to 5.mp4)
NUMBER_OF_VARIATIONS = 5

# Define the window size for analysis (number of chunks)
ANALYSIS_WINDOW_SIZE = 3 # Keeping the reduced window size from the previous test

# Define the interval for generating AI questions (in terms of number of analysis windows)
QUESTION_INTERVAL_WINDOWS = 3

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
        self.user_id = None # Store the user ID
        self.room_name = None # Store the chosen room name
        self.chunk_counter = 0
        self.media_buffer = [] # Stores temporary media file paths (full video+audio chunk)
        self.audio_buffer = {}  # Dictionary to map media_path to temporary audio_path (extracted audio)
        self.transcript_buffer = {} # Dictionary to map media_path to transcript text (transcript of single chunk)
        self.media_path_to_chunk = {} # Map temporary media_path to SessionChunk ID (from DB, after saving)
        # Dictionary to store background tasks for chunk saving, keyed by media_path
        self.background_chunk_save_tasks = {}
        self._running_tasks = []
        # Counter for analysis windows to trigger questions
        self.analysis_window_counter = 0
        self.ai_questions_enabled = True # Default to True, will be updated in connect

    # Make connect asynchronous to allow DB query
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
        # Get AI questions enabled status, default to True if not provided
        self.ai_questions_enabled = query_params.get('ai_questions_enabled', 'true').lower() == 'true'

        # Validate session_id and room_name
        if self.session_id and self.room_name in POSSIBLE_ROOMS:
            # Retrieve the user ID from the PracticeSession (requires async DB call)
            try:
                # Retrieve the user ID directly within the sync context
                # This ensures the access to session.user.id happens in a thread
                # Also handle the case where the session might not have a user linked
                user_id_or_none = await database_sync_to_async(lambda: PracticeSession.objects.filter(id=self.session_id).values_list('user__id', flat=True).first())()

                if user_id_or_none is not None:
                     self.user_id = str(user_id_or_none) # Store user ID as string
                     print(f"WS: Client connected for Session ID: {self.session_id}, User ID: {self.user_id}, Room: {self.room_name}, AI Questions Enabled: {self.ai_questions_enabled}")
                     await self.accept()
                     await self.send(json.dumps({
                         "type": "connection_established",
                         "message": f"Connected to session {self.session_id} for user {self.user_id} in room {self.room_name}"
                     }))
                     print("WS: Connect method successfully completed logic.") # Added diagnostic print

                else:
                     # This covers cases where the session_id is invalid or the session has no user
                     print(f"WS: Connection rejected for Session ID {self.session_id}: PracticeSession not found or has no associated user.")
                     await self.close()

            # We might still catch PracticeSession.DoesNotExist if the initial filter didn't exclude it,
            # but the values_list approach with first() should handle the no-session case gracefully with None.
            # Keeping this catch block for robustness against other potential DB errors.
            except Exception as e:
                 print(f"WS: Error retrieving PracticeSession or User ID during connect: {e}")
                 traceback.print_exc()
                 await self.close()

        else:
            print(f"WS: Connection rejected: Missing session_id or invalid room_name ({self.room_name}).") # Added more detailed message
            await self.close()


    async def disconnect(self, close_code):
            print(f"WS: Client disconnected for Session ID: {self.session_id}. Cleaning up...")

            # Trigger video compilation as a background task
            if self.session_id:
                print(f"WS: Triggering video compilation for session {self.session_id}")
                # Use asyncio.create_task to run compilation in the background
                asyncio.create_task(self.compile_session_video(self.session_id))

            # Attempt to wait for background chunk save tasks to finish gracefully
            print(f"WS: Waiting for {len(self.background_chunk_save_tasks)} pending background save tasks...")
            tasks_to_wait_for = list(self.background_chunk_save_tasks.values())
            if tasks_to_wait_for:
                try:
                    # Wait with a timeout for all tasks related to saving chunks
                    # Using asyncio.gather to wait for multiple tasks
                    # return_exceptions=True allows gathering to complete even if some tasks raise errors (like CancelledError on disconnect)
                    # Store the results and exceptions
                    results = await asyncio.wait_for(asyncio.gather(*tasks_to_wait_for, return_exceptions=True), timeout=10.0) # Wait up to 10 seconds
                    print("WS: Finished waiting for background save tasks during disconnect.")

                    # Explicitly process results to handle exceptions like CancelledError
                    for i, result in enumerate(results):
                        if isinstance(result, asyncio.CancelledError):
                            # This is expected if the task was cancelled on disconnect
                            print(f"WS: Background save task {i} was cancelled during disconnect wait (expected).")
                        elif isinstance(result, Exception):
                            # Log any other unexpected exceptions that occurred in the background tasks
                            print(f"WS: Background save task {i} finished with unexpected exception: {result}")
                            traceback.print_exc() # Print traceback for unexpected exceptions
                        # Else: The task completed successfully, no action needed here as the save logic
                        # and buffer updates happen within the task itself (_complete_chunk_save_in_background)

                except asyncio.TimeoutError:
                    print("WS: Timeout waiting for some background save tasks during disconnect.")
                except Exception as e:
                    # Catch any errors that occur *during* the gather or wait_for itself
                    print(f"WS: Error during asyncio.gather for background save tasks: {e}")
                    traceback.print_exc()


            # Get all paths from buffers and the map keys for final cleanup
            # Ensure we get paths associated with tasks that might have just finished or failed
            audio_paths_to_clean = list(self.audio_buffer.values())
            media_paths_to_clean_from_buffer = list(self.media_buffer)
            media_paths_to_clean_from_map_keys = list(self.media_path_to_chunk.keys()) # Includes paths for saved chunks

            # Combine all potential paths and remove duplicates
            all_paths_to_clean = set([p for p in audio_paths_to_clean + media_paths_to_clean_from_buffer + media_paths_to_clean_from_map_keys if p is not None])


            # Clean up temporary files
            print(f"WS: Attempting to clean up {len(all_paths_to_clean)} temporary files...")
            # Use asyncio.gather for file removals to potentially speed up cleanup
            cleanup_tasks = []
            for file_path in all_paths_to_clean:
                async def remove_file_safe(f_path):
                    try:
                        # Add a small delay before removing to ensure no other process is using it
                        await asyncio.sleep(0.05) # Small delay before removing
                        if os.path.exists(f_path):
                            os.remove(f_path)
                            print(f"WS: Removed temporary file: {f_path}")
                        else:
                            print(f"WS: Temporary file not found during disconnect cleanup: {f_path}")
                    except Exception as e:
                        print(f"WS: Error removing file {f_path} during disconnect cleanup: {e}")
                        traceback.print_exc() # Add traceback for cleanup errors
                cleanup_tasks.append(remove_file_safe(file_path))

            if cleanup_tasks:
                # Run cleanup tasks concurrently, don't worry about exceptions as they are caught within the task
                await asyncio.gather(*cleanup_tasks, return_exceptions=True)
                print("WS: Finished temporary file cleanup.")


            # Clear buffers and maps *after* attempting cleanup
            self.audio_buffer = {}
            self.media_buffer = []
            self.transcript_buffer = {} # Clear the transcript buffer
            self.media_path_to_chunk = {}
            self.background_chunk_save_tasks = {} # Clear background task tracking dictionary


            print(f"WS: Session {self.session_id} cleanup complete.")


    async def receive(self, text_data=None, bytes_data=None):
        print("WS: Received message or data.") # Added diagnostic print
        if not self.session_id:
            print("WS: Error: Session ID not available, cannot process data.")
            return
        # Ensure user_id is available before processing data
        if not self.user_id:
            print("WS: Error: User ID not available, cannot process data.")
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
                        # Create a temporary file for the media chunk
                        media_path = os.path.join(TEMP_MEDIA_ROOT, f"{self.session_id}_{self.chunk_counter}_media.webm")
                        with open(media_path, "wb") as mf:
                            mf.write(media_bytes)
                        print(f"WS: Received media chunk {self.chunk_counter} for Session {self.session_id}. Saved to {media_path}")
                        self.media_buffer.append(media_path)

                        # Start processing the media chunk (audio extraction, transcription)
                        # This part is still awaited to ensure audio/transcript are in buffers
                        # S3 upload and DB save are initiated as background tasks within process_media_chunk
                        print(f"WS: Starting processing (audio/transcript) for chunk {self.chunk_counter} and WAITING for it to complete.")
                        await self.process_media_chunk(media_path)


                        # Trigger windowed analysis if buffer size is sufficient
                        # analyze_windowed_media will run concurrently
                        # It will handle waiting for background chunk save before saving analysis results
                        if len(self.media_buffer) >= ANALYSIS_WINDOW_SIZE:
                            # Take the last ANALYSIS_WINDOW_SIZE chunks for the sliding window
                            window_paths = list(self.media_buffer[-ANALYSIS_WINDOW_SIZE:])
                            print(f"WS: Triggering windowed analysis for sliding window (chunks ending with {self.chunk_counter})")
                            # Pass the list of media paths in the window and the latest chunk number
                            asyncio.create_task(self.analyze_windowed_media(window_paths, self.chunk_counter))

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
        """
        Processes a single media chunk: extracts audio, transcribes,
        and initiates S3 upload and saves SessionChunk data in the background.
        This function returns after extracting audio and transcribing,
        allowing analyze_windowed_media to be triggered sooner.
        """
        start_time = time.time()
        print(f"WS: process_media_chunk started for: {media_path} at {start_time}")
        audio_path = None
        chunk_transcript = None # Initialize transcript as None

        try:
            # --- Audio Extraction (Blocking, but relatively fast) ---
            # Use asyncio.to_thread for the blocking audio extraction call
            # This part is awaited to ensure audio file is ready for transcription
            extract_future = asyncio.to_thread(self.extract_audio, media_path)
            audio_path = await extract_future # Await audio extraction

            # Check if audio extraction was successful and file exists
            if audio_path and os.path.exists(audio_path):
                print(f"WS: Audio extracted and found at: {audio_path}")
                self.audio_buffer[media_path] = audio_path # Store the mapping

                # --- Transcription of the single chunk (Blocking network I/O) ---
                # Only transcribe if AI questions are enabled or analysis requires it
                # Assuming transcription is always needed for analysis regardless of questions
                # If transcription was *only* for questions, we would gate it here.
                # For now, keep transcription as it's needed for general analysis too.
                if client: # Check if OpenAI client was initialized
                    print(f"WS: Attempting transcription for single chunk audio: {audio_path}")
                    transcription_start_time = time.time()
                    try:
                        # Assuming transcribe_audio returns the transcript string or None on failure
                        chunk_transcript = await asyncio.to_thread(transcribe_audio, audio_path)
                        print(f"WS: Single chunk Transcription Result: {chunk_transcript} after {time.time() - transcription_start_time:.2f} seconds")

                        # Always store the result, even if it's None or empty string
                        self.transcript_buffer[media_path] = chunk_transcript
                        print(f"WS: Stored transcript for {media_path} in buffer.")

                    except Exception as transcribe_error:
                        print(f"WS: Error during single chunk transcription for {audio_path}: {transcribe_error}")
                        traceback.print_exc() # Print traceback for transcription errors
                        # If transcription fails, chunk_transcript is still None, and None is stored in buffer


                else:
                    print("WS: OpenAI client not initialized (missing API key?). Skipping single chunk transcription.")
                    self.transcript_buffer[media_path] = None # Store None if transcription is skipped

            else:
                print(f"WS: Audio extraction failed or file not found for {media_path}. Audio path: {audio_path}. Skipping transcription for this chunk.")
                self.audio_buffer[media_path] = None # Store None if audio extraction failed
                self.transcript_buffer[media_path] = None # Store None if transcription is skipped

            # --- Initiate S3 Upload and Save SessionChunk data in the BACKGROUND ---
            # Create a task for S3 upload - this runs in a thread pool
            s3_upload_task = asyncio.create_task(asyncio.to_thread(self.upload_to_s3, media_path))

            # Create a task to await the S3 upload and then save the chunk data to the DB
            # This task runs in the background. Store the task so analyze_windowed_media can potentially wait for it.
            # Create the task first, then add it to the dictionary to ensure it's registered
            # Pass the current chunk_counter to the background task
            chunk_save_task = asyncio.create_task(self._complete_chunk_save_in_background(media_path, s3_upload_task, self.chunk_counter))
            print(f"WS: Created background chunk save task for {media_path}: {chunk_save_task}")
            self._running_tasks.append(chunk_save_task)

            await asyncio.sleep(0.1)  # Increase delay slightly in case of task registration lag

            if media_path in self.background_chunk_save_tasks:
                print(f"WS: WARNING: Overwriting existing task for {media_path}")
            self.background_chunk_save_tasks[media_path] = chunk_save_task

            # Confirm it was successfully added
            if media_path in self.background_chunk_save_tasks:
                print(f"WS: Registered background chunk save task for {media_path} ✅")
            else:
                print(f"WS: ❌ Failed to register background task for {media_path}")


        except Exception as e:
            print(f"WS: Error in process_media_chunk for {media_path}: {e}")
            traceback.print_exc()

        print(f"WS: process_media_chunk finished (background tasks initiated) for: {media_path} after {time.time() - start_time:.2f} seconds")
        # This function now returns sooner, allowing the next chunk's processing or analysis trigger to proceed.


    async def _complete_chunk_save_in_background(self, media_path, s3_upload_task, chunk_number):
        """Awaits S3 upload and then saves the SessionChunk data."""
        try:
            # Wait for S3 upload to complete in its thread
            s3_url = await s3_upload_task

            if s3_url:
                print(f"WS: S3 upload complete for {media_path}. Attempting to save SessionChunk data in background.")
                # Now call the database save method using the obtained S3 URL and the chunk_number
                await self._save_chunk_data(media_path, s3_url, chunk_number)
                # The chunk ID will be added to self.media_path_to_chunk inside _save_chunk_data

            else:
                print(f"WS: S3 upload failed for {media_path}. Cannot save SessionChunk data in background.")
        except asyncio.CancelledError:
            # Handle task cancellation gracefully during disconnect
            print(f"WS: Background chunk save task for {media_path} was cancelled.")
        except Exception as e:
            print(f"WS: Error in background chunk save for {media_path}: {e}")
            traceback.print_exc()
        finally:
            # Clean up the task tracking entry once this task is done (success or failure)
            if media_path in self.background_chunk_save_tasks:
                await asyncio.sleep(0.01)
                if media_path in self.background_chunk_save_tasks:
                    print(f"WS: ✅ Task for {media_path} finished. Removing from tracking.")
                    del self.background_chunk_save_tasks[media_path]
                else:
                    print(f"WS: ⚠️ Task for {media_path} already removed before cleanup.")
            else:
                print(f"WS: ⚠️ Task for {media_path} was not found in tracking dict at cleanup time.")

    async def analyze_windowed_media(self, window_paths, latest_chunk_number):
        """
        Handles concatenation (audio and transcript), analysis, and saving sentiment data for a window.
        Awaits the background chunk save for the last chunk in the window before saving analysis.
        Also triggers AI audience question generation at specified intervals if enabled.
        """
        start_time = time.time()
        last_media_path = window_paths[-1]
        window_chunk_number = latest_chunk_number # Refers to the number of the last chunk in the window

        print(f"WS: analyze_windowed_media started for window ending with {last_media_path} (chunk {window_chunk_number}) at {start_time}")

        # --- Add Logging Here ---
        print(f"WS: DEBUG: Current media_buffer: {[os.path.basename(p) for p in self.media_buffer]}", flush=True)
        print(f"WS: DEBUG: Current transcript_buffer keys: {[os.path.basename(k) for k in self.transcript_buffer.keys()]}", flush=True)
        print(f"WS: DEBUG: Current window_paths: {[os.path.basename(p) for p in window_paths]}", flush=True)
        # --- End Logging ---


        combined_audio_path = None # Reintroduce combined audio path
        combined_transcript_text = ""
        analysis_result = None # Initialize analysis_result as None
        window_transcripts_list = [] # List to hold individual transcripts for concatenation

        try:
            # --- Retrieve Individual Transcripts and Concatenate ---
            print(f"WS: Retrieving and concatenating transcripts for window ending with chunk {window_chunk_number}")
            all_transcripts_found = True
            for media_path in window_paths: # window_paths are the paths for the current window
                # Retrieve transcript from the buffer using the media_path
                # Use .get() with a default of None to handle missing keys gracefully
                transcript = self.transcript_buffer.get(media_path, None)
                if transcript is not None: # Check if the value is not None
                    window_transcripts_list.append(transcript)
                    # --- Add Logging for individual transcripts ---
                    print(f"WS: DEBUG: Transcript for {os.path.basename(media_path)}: '{transcript}'", flush=True)
                    # --- End Logging ---
                else:
                    # If any transcript is missing (None) or key not in buffer, log a warning
                    # and add an empty string for concatenation
                    print(f"WS: Warning: Transcript not found or was None in buffer for chunk media path: {media_path}. Including empty string.")
                    all_transcripts_found = False
                    window_transcripts_list.append("")


            combined_transcript_text = "".join(window_transcripts_list)
            print(f"WS: Concatenated Transcript for window: '{combined_transcript_text}'")

            if not all_transcripts_found:
                 print(f"WS: Analysis for window ending with chunk {window_chunk_number} may be incomplete due to missing transcripts.")


            # --- FFmpeg Audio Concatenation (Reintroduced) ---
            # Filter out None audio paths or paths that don't exist on disk from the audio_buffer
            required_audio_paths = [self.audio_buffer.get(media_path) for media_path in window_paths]
            valid_audio_paths = [path for path in required_audio_paths if path is not None and os.path.exists(path)]

            # We only need ANALYSIS_WINDOW_SIZE valid audio paths for concatenation
            # Only concatenate audio if we have valid paths AND if AI questions are enabled
            # or if any other analysis requires the combined audio.
            # Assuming combined audio is needed for analyze_results regardless of questions:
            if len(valid_audio_paths) == ANALYSIS_WINDOW_SIZE:
                 print(f"WS: Valid audio paths for concatenation: {valid_audio_paths}")

                 combined_audio_path = os.path.join(TEMP_MEDIA_ROOT, f"{self.session_id}_window_{window_chunk_number}.mp3")
                 concat_command = ["ffmpeg", "-y"]
                 for audio_path in valid_audio_paths:
                     concat_command.extend(["-i", audio_path])
                 # Added -nostats -loglevel 0 to reduce FFmpeg output noise
                 concat_command.extend(["-filter_complex", f"concat=n={len(valid_audio_paths)}:a=1:v=0", "-acodec", "libmp3lame", "-b:a", "128k", "-nostats", "-loglevel", "0", combined_audio_path])

                 print(f"WS: Running FFmpeg audio concatenation command: {' '.join(concat_command)}")
                 process = subprocess.Popen(concat_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                 stdout, stderr = await asyncio.to_thread(process.communicate) # Run blocking communicate in a thread
                 returncode = await asyncio.to_thread(lambda p: p.returncode, process) # Get return code in thread

                 if returncode != 0:
                     error_output = stderr.decode()
                     print(f"WS: FFmpeg audio concatenation error (code {returncode}) for window ending with chunk {window_chunk_number}: {error_output}")
                     print(f"WS: FFmpeg stdout: {stdout.decode()}")
                     combined_audio_path = None # Ensure combined_audio_path is None on failure
                 else:
                     print(f"WS: Audio files concatenated to: {combined_audio_path}")
                     # combined_audio_path is set here if successful

            else:
                 print(f"WS: Audio not found for all {ANALYSIS_WINDOW_SIZE} chunks in window ending with chunk {latest_chunk_number}. Ready audio paths: {len(valid_audio_paths)}/{ANALYSIS_WINDOW_SIZE}. Skipping audio concatenation for this window instance.")
                 combined_audio_path = None # Ensure combined_audio_path is None if not all audio paths are valid


            # --- Analyze results using OpenAI (blocking network I/O) ---
            # Proceed with analysis if there is a non-empty concatenated transcript and the client is initialized
            # AND combined_audio_path is available (mimicking old working behavior)
            # We will get the analysis result if possible, regardless of whether the chunk save is complete yet.
            # Analysis should still run even if AI questions are disabled, as it provides other feedback.
            if combined_transcript_text.strip() and client and combined_audio_path and os.path.exists(combined_audio_path):
                print(f"WS: Running analyze_results for combined transcript and audio.")
                analysis_start_time = time.time()
                try:
                    # Using asyncio.to_thread for blocking OpenAI/Analysis call
                    # Pass the combined_transcript_text, video_path of the first chunk, and the combined_audio_path
                    # This replicates the call signature from the working version
                    analysis_result = await asyncio.to_thread(analyze_results, combined_transcript_text, window_paths[0], combined_audio_path)
                    print(f"WS: Analysis Result: {analysis_result} after {time.time() - analysis_start_time:.2f} seconds")

                    # Check if the result is a dictionary and contains an error (as implemented previously for robustness)
                    if analysis_result is None or (isinstance(analysis_result, dict) and 'error' in analysis_result):
                         error_message = analysis_result.get('error') if isinstance(analysis_result, dict) else 'Unknown analysis error (result is None)'
                         print(f"WS: Analysis returned an error structure: {error_message}")
                         # analysis_result variable already holds the error dictionary or None

                except Exception as analysis_error:
                    print(f"WS: Error during analysis (analyze_results) for window ending with chunk {window_chunk_number}: {analysis_error}")
                    traceback.print_exc() # Print traceback for analysis errors
                    # Structure the error result consistently as a dictionary with an error key
                    analysis_result = {'error': str(analysis_error), 'Feedback': {}, 'Posture': {}, 'Scores': {}} # Provide empty nested dicts for serializer safety


            elif combined_transcript_text.strip() and client:
                 # Scenario where transcript exists and client is ready, but combined_audio_path is missing/failed
                 print("WS: Skipping analysis: Combined audio path is missing or failed despite transcript being available.")
                 # analysis_result remains None
            elif combined_transcript_text.strip():
                 # Scenario where transcript exists, but client is not initialized
                 print("WS: OpenAI client not initialized. Skipping analysis despite having concatenated transcript.")
                 # analysis_result remains None
            else:
                print(f"WS: Concatenated transcript is empty or only whitespace for window ending with chunk {window_chunk_number}. Skipping analysis.")
                # analysis_result remains None


            # --- Sending updates to the frontend (happens regardless of analysis save status) ---
            # We send the feedback as soon as analyze_results completes.
            if analysis_result is not None:
                # Apply the numpy type conversion before sending
                serializable_analysis_result = convert_numpy_types(analysis_result)

                # Send analysis updates to the frontend
                # Access Feedback/Audience Emotion safely, accounting for potential error structure
                audience_emotion = serializable_analysis_result.get('Feedback', {}).get('Audience Emotion')

                emotion_s3_url = None
                # Only try to construct URL if we have a detected emotion, S3 client, and room name
                if audience_emotion and s3 and self.room_name:
                    try:
                        # Convert emotion to lowercase for S3 path lookup
                        lowercase_emotion = audience_emotion.lower()

                        # Randomly select a variation number between 1 and NUMBER_OF_VARIATIONS
                        selected_variation = random.randint(1, NUMBER_OF_VARIATIONS)

                        # Construct the new S3 URL with room and variation
                        # Ensure AWS_S3_REGION_NAME or AWS_REGION is set
                        region_name = os.environ.get('AWS_S3_REGION_NAME', os.environ.get('AWS_REGION', 'us-east-1'))
                        emotion_s3_url = f"https://{BUCKET_NAME}.s3.{region_name}.amazonaws.com/{EMOTION_STATIC_FOLDER}/{self.room_name}/{lowercase_emotion}/{selected_variation}.mp4"

                        print(f"WS: Sending window emotion update: {audience_emotion}, URL: {emotion_s3_url} (Room: {self.room_name}, Variation: {selected_variation})")
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
                    # This will also print if analysis_result didn't have a 'Feedback'/'Audience Emotion' structure or if audience_emotion was None/empty
                    print("WS: No audience emotion detected or analysis structure unexpected. Cannot send static video URL.")


                print(f"WS: Sending full analysis update to frontend for window ending with chunk {window_chunk_number}: {serializable_analysis_result}")
                await self.send(json.dumps({
                    "type": "full_analysis_update",
                    "analysis": serializable_analysis_result
                }))

                # --- Trigger AI Audience Question Generation ---
                # Increment the analysis window counter
                self.analysis_window_counter += 1
                print(f"WS: Analysis window count: {self.analysis_window_counter}")

                # Check if it's time to generate a question based on the interval
                # AND if AI questions are enabled for this session
                if self.ai_questions_enabled and self.analysis_window_counter % QUESTION_INTERVAL_WINDOWS == 0:
                    print(f"WS: AI questions are ENABLED. Generating AI Audience Question for window ending with chunk {window_chunk_number}")
                    # Use asyncio.create_task to run the question generation in the background
                    # Pass the concatenated transcript to the function
                    asyncio.create_task(self.generate_and_send_question(combined_transcript_text))
                    # Reset the counter after generating a question if you want intervals based on *since last question*
                    # self.analysis_window_counter = 0 # Uncomment this if you want intervals based on *since last question*

                elif not self.ai_questions_enabled:
                     print(f"WS: AI questions are DISABLED. Skipping AI Audience Question generation for window ending with chunk {window_chunk_number}")
                else:
                     print(f"WS: AI questions are ENABLED but not time for a question yet (window {self.analysis_window_counter}). Skipping AI Audience Question generation.")


                # --- Wait for the background chunk save task for the LAST chunk in the window ---
                # before attempting to save the window analysis results to the database.
                last_chunk_save_task = None
                wait_start_time = time.time()
                wait_timeout = 30.0 # Increased timeout to wait for the task to appear/complete
                max_retries = 3 # Maximum number of retries to find the task
                retry_count = 0

                while (time.time() - wait_start_time) < wait_timeout and retry_count < max_retries:
                    last_chunk_save_task = self.background_chunk_save_tasks.get(last_media_path)
                    if last_chunk_save_task:
                        print(f"WS: Background save task found for {last_media_path}. Waiting for it to complete...")
                        try:
                            # Wait for the specific task to finish (with the remaining timeout)
                            await asyncio.wait_for(last_chunk_save_task, timeout=wait_timeout - (time.time() - wait_start_time))
                            print(f"WS: Background save task for {last_media_path} completed. Proceeding to save window analysis.")

                            # --- Initiate Saving Analysis data in the BACKGROUND ---
                            # Only create the analysis save task if the chunk save completed
                            print(f"WS: Initiating saving window analysis for chunk {window_chunk_number} in background.")
                            # Create a task to save the analysis result
                            # Pass the original analysis_result here, as the saving function might expect it
                            asyncio.create_task(self._save_window_analysis(last_media_path, analysis_result, combined_transcript_text, window_chunk_number))

                        except asyncio.TimeoutError:
                            print(f"WS: Timeout waiting for background save task for {last_media_path} to complete. Cannot save window analysis for chunk {window_chunk_number}.")
                        except Exception as task_error:
                            # This handles exceptions within the chunk save task itself if return_exceptions=True was used (it's not, but good practice)
                            print(f"WS: Background save task for {last_media_path} failed with error: {task_error}. Cannot save window analysis.")

                        break # Exit the while loop once the task is found and processed

                    # If task not found, wait a bit and retry
                    await asyncio.sleep(0.5) # Increased sleep time between retries
                    retry_count += 1
                    print(f"WS: Retry {retry_count}/{max_retries} - waiting for background task of {last_media_path}")
                    print(f"WS: Current keys in self.background_chunk_save_tasks: {list(self.background_chunk_save_tasks.keys())}")
                    print(f"WS: Current keys in self.media_path_to_chunk: {list(self.media_path_to_chunk.keys())}")


                # If the loop finished without finding/waiting for the task, check if the chunk ID is in the map and try to save analysis
                if not last_chunk_save_task:
                    if last_media_path in self.media_path_to_chunk:
                         print(f"WS: ⚠️ Background save task missing, but chunk ID found for {last_media_path}. Proceeding to save window analysis.")
                         asyncio.create_task(self._save_window_analysis(last_media_path, analysis_result, combined_transcript_text, window_chunk_number))
                    else:
                        print(f"WS: ❌ Background save task for the last chunk ({last_media_path}) in the window was not found within timeout AND no chunk ID. Cannot save window analysis.")
                        print(f"WS: DEBUG: Current background_chunk_save_tasks keys: {list(self.background_chunk_save_tasks.keys())}")
                        print(f"WS: DEBUG: Current media_path_to_chunk keys: {list(self.media_path_to_chunk.keys())}")


            else:
                print(f"WS: No analysis result obtained for window ending with chunk {window_chunk_number}. Skipping analysis save and sending updates.")

        except Exception as e: # Catch any exceptions during the analyze_windowed_media process itself (excluding analyze_results internal errors already caught)
            print(f"WS: Error during windowed media analysis ending with chunk {window_chunk_number}: {e}")
            traceback.print_exc() # Print traceback for general analyze_windowed_media errors
        finally:
            # Clean up the temporary combined audio file if it was created
            # This cleanup happens regardless of whether the analysis or save succeeded.
            if combined_audio_path and os.path.exists(combined_audio_path):
                try:
                    # Add a small delay before removing
                    await asyncio.sleep(0.05)
                    os.remove(combined_audio_path)
                    print(f"WS: Removed temporary combined audio file: {combined_audio_path}")
                except Exception as e:
                    print(f"WS: Error removing temporary combined audio file {combined_audio_path}: {e}")

            # Clean up the oldest chunk from the buffers after an analysis attempt for a window finishes.
            # This happens if the media_buffer has reached or exceeded the window size
            # We only want to remove *one* oldest chunk per analysis trigger
            # The condition `len(self.media_buffer) >= ANALYSIS_WINDOW_SIZE` ensures we maintain a buffer of ANALYSIS_WINDOW_SIZE
            # Corrected condition back to >= ANALYSIS_WINDOW_SIZE to match original logic and ensure cleanup happens
            while len(self.media_buffer) >= ANALYSIS_WINDOW_SIZE:
                 print(f"WS: Cleaning up oldest chunk after analysis. Current buffer size: {len(self.media_buffer)}")
                 try:
                     # Get the oldest media path from the buffer *without* removing it yet
                     oldest_media_path = self.media_buffer[0]
                     print(f"WS: Considering cleanup for oldest media chunk {oldest_media_path}...")

                     # --- Wait for the background chunk save task for this specific oldest chunk to complete ---
                     # This ensures the S3 upload and initial DB save for the chunk being removed from the buffer are done.
                     # This is distinct from waiting for the *last* chunk's save task for analysis saving.
                     save_task = self.background_chunk_save_tasks.get(oldest_media_path)

                     if save_task:
                         print(f"WS: Waiting for background save task for oldest chunk ({oldest_media_path}) to complete before cleaning up...")
                         try:
                             # Wait for the specific task to finish (with a reasonable timeout)
                             await asyncio.wait_for(save_task, timeout=90.0) # Use a reasonable timeout
                             print(f"WS: Background save task for oldest chunk ({oldest_media_path}) completed. Proceeding with cleanup.")

                         except asyncio.TimeoutError:
                             print(f"WS: Timeout waiting for background save task for oldest chunk ({oldest_media_path}). Skipping cleanup of this chunk for now.")
                             # Skip cleanup for this specific chunk in this iteration; it might be cleaned up later or on disconnect
                             # Break the while loop to avoid blocking further cleanup attempts for other chunks that might be ready
                             break # Exit the while loop after a cleanup attempt (successful or timed out)

                         except Exception as task_error:
                             # This handles exceptions within the background save task itself
                             print(f"WS: Background save task for oldest chunk ({oldest_media_path}) failed with error: {task_error}. Proceeding with cleanup as task is done.")
                             # The task failed but is finished. We can proceed with cleanup.


                     else:
                          # This case might happen if cleanup runs significantly later and the task finished/failed and removed itself from tracking,
                          # or if process_media_chunk had an error before starting the task.
                          print(f"WS: No background save task found for oldest chunk ({oldest_media_path}). Assuming it finished or wasn't started. Proceeding with cleanup.")
                          # We proceed with cleanup cautiously.


                     # --- If we reached here, either the task completed, failed, or didn't exist. Proceed with cleanup ---
                     # Now pop the oldest media path from the buffer as the save is considered complete/dealt with
                     # Check if the oldest media path is still in the buffer before popping
                     if self.media_buffer and self.media_buffer[0] == oldest_media_path:
                         oldest_media_path_to_clean = self.media_buffer.pop(0) # Pop it now
                         print(f"WS: Popped oldest media chunk {oldest_media_path_to_clean} from buffer for cleanup.")

                         # Remove associated entries from other buffers and maps
                         oldest_audio_path = self.audio_buffer.pop(oldest_media_path_to_clean, None)
                         oldest_transcript = self.transcript_buffer.pop(oldest_media_path_to_clean, None)
                         oldest_chunk_id = self.media_path_to_chunk.pop(oldest_media_path_to_clean, None)
                         # The background_chunk_save_tasks entry for this path is removed within _complete_chunk_save_in_background's finally block.

                         # Clean up the temporary files associated with this oldest chunk
                         files_to_remove = [oldest_media_path_to_clean, oldest_audio_path]
                         for file_path in files_to_remove:
                             if file_path and os.path.exists(file_path):
                                 try:
                                     await asyncio.sleep(0.05) # Small delay before removing
                                     os.remove(file_path)
                                     print(f"WS: Removed temporary file: {file_path}")
                                 except Exception as e:
                                     print(f"WS: Error removing temporary file {file_path}: {e}")
                             elif file_path:
                                print(f"WS: File path {file_path} was associated but not found on disk during cleanup.")


                         if oldest_transcript is not None:
                              print(f"WS: Removed transcript from buffer for oldest media path: {oldest_media_path_to_clean}")
                         else:
                              print(f"WS: No transcript found in buffer for oldest media path {oldest_media_path_to_clean} during cleanup.")

                         if oldest_chunk_id is not None:
                              print(f"WS: Removed chunk ID mapping from buffer for oldest media path: {oldest_media_path_to_clean}")
                         else:
                              print(f"WS: No chunk ID mapping found in buffer for oldest media path {oldest_media_path_to_clean} during cleanup.")

                     else:
                         print(f"WS: Oldest media path in buffer ({self.media_buffer[0] if self.media_buffer else 'None'}) is not the one considered for cleanup ({oldest_media_path}). Skipping cleanup loop iteration.")
                         # This might happen in complex async scenarios if the buffer changes unexpectedly.
                         break # Exit the while loop to prevent infinite loops

                 except IndexError:
                      # Should not happen with the while condition, but good practice
                      print("WS: media_buffer was unexpectedly empty during cleanup in analyze_windowed_media finally.")
                      break # Exit the while loop if buffer is empty
                 except Exception as cleanup_error:
                      print(f"WS: Error during cleanup of oldest chunk in analyze_windowed_media: {cleanup_error}")
                      traceback.print_exc()
                      break # Exit the while loop on general cleanup error
                 # The while loop condition `len(self.media_buffer) >= ANALYSIS_WINDOW_SIZE`
                 # will continue cleaning up the next oldest chunk if the buffer is still too large.


        print(f"WS: analyze_windowed_media finished (instance) for window ending with chunk {window_chunk_number} after {time.time() - start_time:.2f} seconds")


    # NEW METHOD: Generates and sends an AI audience question
    async def generate_and_send_question(self, transcript):
        """Generates an AI audience question based on the transcript and sends it to the frontend."""
        # Added check for self.ai_questions_enabled
        if not self.ai_questions_enabled or not transcript or not client:
            print("WS: Skipping AI audience question generation: Feature disabled, transcript is empty, or OpenAI client not initialized.")
            return

        print("WS: Calling ai_audience_question...")
        try:
            # Call the synchronous ai_audience_question function in a thread
            question = await asyncio.to_thread(ai_audience_question, transcript)

            if question:
                print(f"WS: Generated AI audience question: {question}")
                # Send the question to the frontend via WebSocket
                # Wrap in try/except in case the connection is closed
                try:
                    await self.send(json.dumps({
                        "type": "audience_question",
                        "question": question
                    }))
                    print("WS: Sent AI audience question to frontend.")
                except Exception as send_error:
                    print(f"WS: Error sending AI audience question to frontend: {send_error}")
            else:
                print("WS: AI audience question function returned None.")

        except Exception as e:
            print(f"WS: Error generating or sending AI audience question: {e}")
            traceback.print_exc()


    def extract_audio(self, media_path):
        """Extracts audio from a media file using FFmpeg. This is a synchronous operation."""
        start_time = time.time()
        base, _ = os.path.splitext(media_path)
        audio_mp3_path = f"{base}.mp3"
        # Use list format for command for better security and compatibility
        # Added -nostats -loglevel 0 to reduce FFmpeg output noise
        ffmpeg_command = ["ffmpeg", "-y", "-i", media_path, "-vn", "-acodec", "libmp3lame", "-ab", "128k", "-nostats", "-loglevel", "0", audio_mp3_path]
        print(f"WS: Running FFmpeg command: {' '.join(ffmpeg_command)}")
        try:
            # subprocess.Popen and communicate() are blocking calls
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
        """Uploads a local file to S3. This is a synchronous operation."""
        if s3 is None:
             print(f"WS: S3 client is not initialized. Cannot upload file: {file_path}.")
             return None
        # Ensure user_id is available before attempting upload
        if not self.user_id:
            print(f"WS: Error: User ID not available. Cannot upload file {file_path} to S3 with user structure.")
            return None


        start_time = time.time()
        file_name = os.path.basename(file_path)
        # Updated folder structure: BASE_FOLDER/user_id/session_id/file_name
        folder_path = f"{BASE_FOLDER}{self.user_id}/{self.session_id}/"
        s3_key = f"{folder_path}{file_name}"
        try:
            # s3.upload_file is a blocking call
            s3.upload_file(file_path, BUCKET_NAME, s3_key)
            # Construct S3 URL - using regional endpoint format
            region_name = os.environ.get('AWS_S3_REGION_NAME', os.environ.get('AWS_REGION', 'us-east-1'))
            s3_url = f"https://{BUCKET_NAME}.s3.{region_name}.amazonaws.com/{s3_key}"
            print(f"WS: Uploaded {file_path} to S3 successfully. S3 URL: {s3_url} after {time.time() - start_time:.2f} seconds.")
            return s3_url
        except Exception as e:
            print(f"WS: S3 upload failed for {file_path}: {e}")
            traceback.print_exc()
            return None

    # Decorate with database_sync_to_async to run this synchronous DB method in a thread
    @database_sync_to_async
    def _save_chunk_data(self, media_path, s3_url, chunk_number):
        """Saves the SessionChunk object and maps media path to chunk ID."""
        start_time = time.time()
        print(f"WS: _save_chunk_data called for chunk at {media_path} with S3 URL {s3_url} (chunk number: {chunk_number}) at {start_time}")
        if not self.session_id:
            print("WS: Error: Session ID not available, cannot save chunk data.")
            # Returning None explicitly for clarity with async decorator
            return None

        if not s3_url:
             print(f"WS: Error: S3 URL not provided for {media_path}. Cannot save SessionChunk.")
             return None # Returning None explicitly

        try:
            # Synchronous DB call: Get the session
            # Because this method is decorated, this runs in a sync context/thread
            print(f"WS: Attempting to get PracticeSession with id: {self.session_id}")
            try:
                 session = PracticeSession.objects.get(id=self.session_id)
                 print(f"WS: Retrieved PracticeSession: {session.id}, {session.session_name}")
            except PracticeSession.DoesNotExist:
                 print(f"WS: Error: PracticeSession with id {self.session_id} not found. Cannot save chunk data.")
                 return None # Returning None explicitly

            print(f"WS: S3 URL for SessionChunk: {s3_url}")
            session_chunk_data = {
                'session': session.id, # Link to the session using its ID
                'chunk_number': chunk_number, # Include the chunk number here
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
                    # Accessing self here is fine as it's the consumer instance
                    self.media_path_to_chunk[media_path] = session_chunk.id
                    print(f"WS: Added mapping: {media_path} -> {session_chunk.id}")
                    return session_chunk.id # Return the saved chunk ID

                except Exception as save_error:
                    print(f"WS: Error during SessionChunk save: {save_error}")
                    traceback.print_exc()
                    return None # Return None on save error
            else:
                # Corrected variable name from session_serializer.errors to session_chunk_serializer.errors
                print("WS: Error saving SessionChunk:", session_chunk_serializer.errors)
                return None # Return None if serializer is not valid

        except Exception as e: # Catching other potential exceptions during DB interaction etc.
            print(f"WS: Error in _save_chunk_data: {e}")
            traceback.print_exc()
            return None # Return None on general error
        finally:
             print(f"WS: _save_chunk_data finished after {time.time() - start_time:.2f} seconds")


# Decorate with database_sync_to_async to run this synchronous DB method in a thread
    @database_sync_to_async
    def _save_window_analysis(self, media_path_of_last_chunk_in_window, analysis_result, combined_transcript_text, window_chunk_number):
        """
        Saves the window's analysis result to the database, linked to the last chunk in the window.
        Runs in a separate thread thanks to database_sync_to_async.
        Handles cases where analysis_result might be an error dictionary.
        It will implicitly wait for the SessionChunk to exist via the ORM query for the chunk ID.
        """
        start_time = time.time()
        print(f"WS: _save_window_analysis started for window ending with media path: {media_path_of_last_chunk_in_window} (chunk {window_chunk_number}) at {start_time}")
        if not self.session_id:
            print("WS: Error: Session ID not available, cannot save window analysis.")
            return None # Returning None explicitly

        try:
            # Get the SessionChunk ID from the map for the *last* chunk in the window
            # This dictionary access is synchronous and fine within the decorated method.
            # The ORM query inside the serializer's is_valid() or save() method will
            # handle waiting for the chunk to exist in the DB.
            session_chunk_id = self.media_path_to_chunk.get(media_path_of_last_chunk_in_window)

            print(f"WS: In _save_window_analysis for {media_path_of_last_chunk_in_window} (chunk {window_chunk_number}): session_chunk_id found? {session_chunk_id is not None}. ID: {session_chunk_id}")

            if session_chunk_id:
                print(f"WS: Found SessionChunk ID: {session_chunk_id} for media path: {media_path_of_last_chunk_in_window}")

                # Initialize sentiment_data with basic required fields and the transcript
                sentiment_data = {
                    'chunk': session_chunk_id, # Link to the SessionChunk using its ID
                    'chunk_number': window_chunk_number, # Store the chunk number (this is the last chunk in the window)
                    'chunk_transcript': combined_transcript_text,
                }

                # Check if analysis_result is a valid dictionary and not an error structure
                if isinstance(analysis_result, dict) and 'error' not in analysis_result:
                    print("WS: Analysis result is valid, mapping feedback, posture, and scores.")
                    # Safely access nested dictionaries from analysis_result
                    feedback_data = analysis_result.get('Feedback', {})
                    posture_data = analysis_result.get('Posture', {})
                    scores_data = analysis_result.get('Scores', {})

                    # Map data from analyze_results
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
                        # Handle potential non-boolean values safely
                        'gestures': bool(posture_data.get('Gestures', False)) if posture_data.get('Gestures') is not None else False,

                        'volume': scores_data.get('Volume Score'),
                        'pitch_variability': scores_data.get('Pitch Variability Score'),
                        'pace': scores_data.get('Pace Score'),
                        'pauses': scores_data.get('Pause Score'),
                    })
                elif isinstance(analysis_result, dict) and 'error' in analysis_result:
                     print(f"WS: Analysis result contained an error: {analysis_result.get('error')}. Saving with error message and null analysis fields.")
                     # Optionally store the error message in a dedicated field if your model supports it
                     # For now, we just log it and proceed with saving basic data + transcript

                else:
                     print("WS: Analysis result was not a valid dictionary or was None. Saving with null analysis fields.")
                     # sentiment_data already only contains basic fields + transcript

                print(f"WS: ChunkSentimentAnalysis data (for window, chunk {window_chunk_number}) prepared for saving: {sentiment_data}")

                # Use the serializer to validate and prepare data for saving
                sentiment_serializer = ChunkSentimentAnalysisSerializer(data=sentiment_data)

                # The is_valid() call might trigger DB lookups (e.g., for the 'chunk' foreign key)
                # This runs in the sync thread provided by database_sync_to_async.
                # If the chunk corresponding to session_chunk_id does not yet exist,
                # this lookup will wait or fail depending on DB/ORM behavior.
                # With database_sync_to_async and typical ORM, it might wait.
                if sentiment_serializer.is_valid():
                    print(f"WS: ChunkSentimentAnalysisSerializer (for window, chunk {window_chunk_number}) is valid.")
                    try:
                        # Synchronous database call to save the sentiment analysis
                        sentiment_analysis_obj = sentiment_serializer.save()

                        print(f"WS: Window analysis data saved for chunk ID: {session_chunk_id} (chunk {window_chunk_number}) with sentiment ID: {sentiment_analysis_obj.id} after {time.time() - start_time:.2f} seconds")
                        return sentiment_analysis_obj.id # Return the saved sentiment ID

                    except Exception as save_error:
                        print(f"WS: Error during ChunkSentimentAnalysis save (for window, chunk {window_chunk_number}): {save_error}")
                        traceback.print_exc() # Print traceback for save errors
                        return None # Return None on save error
                else:
                    # Print validation errors if serializer is not valid
                    print(f"WS: Error saving ChunkSentimentAnalysis (chunk {window_chunk_number}):", sentiment_serializer.errors)
                    return None # Return None if serializer is not valid

            else:
                # This logs if session_chunk_id was None (meaning _save_chunk_data failed or hasn't run for the last chunk in the window)
                error_message = f"SessionChunk ID not found in media_path_to_chunk for media path {media_path_of_last_chunk_in_window} during window analysis save for chunk {window_chunk_number}. Analysis will not be saved for this chunk."
                print(f"WS: {error_message}")
                return None # Return None if chunk ID not found

        except Exception as e:
            print(f"WS: Error in _save_window_analysis for media path {media_path_of_last_chunk_in_window} (chunk {window_chunk_number}): {e}")
            traceback.print_exc() # Print traceback for general _save_window_analysis errors
            return None # Return None on general error
        finally:
             print(f"WS: _save_window_analysis finished after {time.time() - start_time:.2f} seconds")

    @database_sync_to_async
    def get_session_chunk_urls(self, session_id):
        """Retrieves S3 URLs for all chunks of a session."""
        try:
            # Order by chunk_number to ensure correct compilation order
            chunks = SessionChunk.objects.filter(session__id=session_id).order_by('chunk_number')
            # Directly use chunk.video_file as it's already the S3 URL string
            chunk_urls = [chunk.video_file for chunk in chunks if chunk.video_file]
            print(f"WS: Retrieved {len(chunk_urls)} chunk URLs for session {session_id}")
            return chunk_urls
        except Exception as e:
            print(f"WS: Error retrieving chunk URLs for session {session_id}: {e}")
            traceback.print_exc()
            return []

    @database_sync_to_async
    def update_session_with_video_url(self, session_id, video_url):
        """Updates the PracticeSession with the final compiled video URL."""
        try:
            session = PracticeSession.objects.get(id=session_id)
            session.compiled_video_url = video_url  # Assuming you have a field named compiled_video_url
            session.save(update_fields=['compiled_video_url'])
            print(f"WS: Updated session {session_id} with compiled video URL: {video_url}")
            # You might want to send a WebSocket message to the frontend here if the connection is still open
        except PracticeSession.DoesNotExist:
            print(f"WS: PracticeSession with id {session_id} not found during video URL update.")
        except Exception as e:
            print(f"WS: Error updating session {session_id} with compiled video URL: {e}")
            traceback.print_exc()


    async def compile_session_video(self, session_id):
        """Background task to compile all chunks for a session."""
        print(f"WS: Starting video compilation for session {session_id} in background task.")
        temp_file_paths = [] # To keep track of temporary files for cleanup
        try:
            # 1. Get all chunk S3 URLs for the session
            chunk_urls = await self.get_session_chunk_urls(session_id)
            if not chunk_urls:
                print(f"WS: No chunk URLs found for session {session_id}. Skipping compilation.")
                return

            # 2. Download chunks temporarily
            print(f"WS: Downloading {len(chunk_urls)} chunks for session {session_id}.")
            downloaded_chunk_paths = []
            for i, url in enumerate(chunk_urls):
                # Extract the S3 key from the URL more robustly
                s3_key = None
                try:
                    parsed_url = urlparse(url)
                    # For regional endpoints like bucket-name.s3.region.amazonaws.com
                    # the bucket name is the first part of the hostname
                    # the key is the path part without the leading slash
                    hostname_parts = parsed_url.hostname.split('.')
                    extracted_bucket_name = hostname_parts[0] if hostname_parts else None
                    key_path = parsed_url.path.lstrip('/') if parsed_url.path else None # Get path without leading slash

                    if extracted_bucket_name == BUCKET_NAME and key_path:
                         s3_key = key_path # This is the correct S3 key relative to the bucket root
                         print(f"WS: Extracted S3 key from URL {url}: {s3_key}")
                    else:
                         print(f"WS: Could not extract S3 key or bucket name from URL: {url}. Extracted bucket: {extracted_bucket_name}, Expected: {BUCKET_NAME}, Extracted key path: {key_path}. Skipping download for this chunk.")
                         continue # Skip this chunk if extraction fails


                except Exception as url_parse_error:
                     print(f"WS: Error parsing URL {url}: {url_parse_error}. Skipping download for this chunk.")
                     continue


                if not s3_key:
                     # This case should be caught by the try/except above, but as a safeguard
                     print(f"WS: S3 key is None after extraction attempt for URL: {url}. Skipping download for this chunk.")
                     continue


                temp_file_path = os.path.join(TEMP_MEDIA_ROOT, f"{session_id}_chunk_{i}_{os.path.basename(s3_key)}") # Include key filename in temp filename
                temp_file_paths.append(temp_file_path) # Add to cleanup list
                try:
                    # Use asyncio.to_thread for blocking download
                    # The s3.download_file method takes bucket name, S3 key, and local file path
                    await asyncio.to_thread(s3.download_file, BUCKET_NAME, s3_key, temp_file_path)
                    downloaded_chunk_paths.append(temp_file_path)
                    print(f"WS: Downloaded chunk {i+1}/{len(chunk_urls)} from {s3_key} to {temp_file_path}") # Log using the key
                except Exception as e:
                    print(f"WS: Error downloading chunk {i+1} from {s3_key}: {e}") # Log using the key
                    # Decide how to handle download errors - skip the chunk or fail compilation?
                    # For now, we'll try to compile with downloaded chunks.
                    continue

            if not downloaded_chunk_paths:
                 print(f"WS: No chunks were successfully downloaded for session {session_id}. Skipping compilation.")
                 return

            # 3. Create a file list for FFmpeg concat demuxer
            list_file_path = os.path.join(TEMP_MEDIA_ROOT, f"{session_id}_concat_list.txt")
            temp_file_paths.append(list_file_path) # Add to cleanup list
            with open(list_file_path, 'w') as f:
                for chunk_path in downloaded_chunk_paths:
                    # FFmpeg expects paths in 'file /path/to/file' format in the list file
                    # Ensure paths are correctly formatted for the environment FFmpeg runs in
                    f.write(f"file '{chunk_path.replace(os.sep, '/')}'\n") # Use forward slashes for FFmpeg
            print(f"WS: Created concat list file: {list_file_path}")

            # 4. Compile video using FFmpeg concat demuxer
            compiled_video_filename = f"{session_id}_compiled.webm"
            compiled_video_path = os.path.join(TEMP_MEDIA_ROOT, compiled_video_filename)
            temp_file_paths.append(compiled_video_path) # Add to cleanup list

            # FFmpeg command to concatenate using demuxer
            # Added -c copy for efficiency
            # Added -f webm explicitly if the input format is webm (which it seems to be from chunk names)
            ffmpeg_command = [
                "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file_path,
                 "-c", "copy", "-f", "webm", "-nostats", "-loglevel", "0", compiled_video_path
            ]
            print(f"WS: Running FFmpeg compilation command: {' '.join(ffmpeg_command)}")

            process = subprocess.Popen(ffmpeg_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            stdout, stderr = await asyncio.to_thread(process.communicate)
            returncode = await asyncio.to_thread(lambda p: p.returncode, process)

            if returncode != 0:
                error_output = stderr.decode()
                print(f"WS: FFmpeg compilation error (code {returncode}) for session {session_id}: {error_output}")
                print(f"WS: FFmpeg stdout: {stdout.decode()}")
                # Decide how to handle compilation errors - log, notify user?
                return
            else:
                print(f"WS: Video compiled successfully to: {compiled_video_path}")

            # 5. Upload compiled video to S3
            print(f"WS: Uploading compiled video to S3 for session {session_id}.")
            # Construct the S3 key for the compiled video
            # Updated compiled S3 key structure: BASE_FOLDER/user_id/session_id/compiled_video_filename
            # Ensure user_id is available
            if not self.user_id:
                 print(f"WS: Error: User ID not available. Cannot upload compiled video for session {session_id}.")
                 return # Exit if user ID is missing

            compiled_s3_key = f"{BASE_FOLDER}{self.user_id}/{self.session_id}/{compiled_video_filename}" # Use self.user_id and self.session_id
            # Upload the compiled video
            # Use asyncio.to_thread for blocking upload
            await asyncio.to_thread(s3.upload_file, compiled_video_path, BUCKET_NAME, compiled_s3_key)

            # Construct the final S3 URL
            region_name = os.environ.get('AWS_S3_REGION_NAME', os.environ.get('AWS_REGION', 'us-east-1'))
            compiled_s3_url = f"https://{BUCKET_NAME}.s3.{region_name}.amazonaws.com/{compiled_s3_key}"


            if compiled_s3_url:
                # 6. Update Session model with compiled video URL
                await self.update_session_with_video_url(session_id, compiled_s3_url)
                print(f"WS: Video compilation and upload complete for session {session_id}. URL: {compiled_s3_url}")
            else:
                 print(f"WS: Failed to upload compiled video to S3 for session {session_id}.")


        except Exception as e:
            print(f"WS: An error occurred during video compilation for session {session_id}: {e}")
            traceback.print_exc()
        finally:
            # Clean up temporary files
            print(f"WS: Cleaning up temporary files for session {session_id}.")
            for file_path in temp_file_paths:
                if os.path.exists(file_path):
                    try:
                        os.remove(file_path)
                        print(f"WS: Removed temporary file: {file_path}")
                    except Exception as e:
                        print(f"WS: Error removing temporary file {file_path} during cleanup: {e}")