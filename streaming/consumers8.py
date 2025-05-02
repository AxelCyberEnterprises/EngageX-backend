#################################################################################################################
# Reworked version to store chunks locally, compile on the server, and upload only the final video to S3.
# This version addresses:
# - Race condition with file cleanup (further refined)
# - Incorrect type for SessionChunk serializer 'session' field
# - Returning the compiled video URL to the frontend via WebSocket
# - Logging the total time for compilation/upload
# - Improved robustness for waiting on background tasks
# - Removed premature cleanup of media_path_to_chunk
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
import random # Import random for selecting variations
import numpy as np # Import numpy to handle potential numpy types

from base64 import b64decode
from datetime import timedelta

from channels.generic.websocket import AsyncWebsocketConsumer
# Import database_sync_to_async for handling synchronous database operations in async context
from channels.db import database_sync_to_async

# Assuming these are in a local file sentiment_analysis.py
# transcribe_audio now needs to handle a single audio file (used in process_media_chunk)
# analyze_results now receives a concatenated transcript and the combined audio path (like the original)
from .sentiment_analysis import analyze_results, transcribe_audio

from practice_sessions.models import PracticeSession, SessionChunk, ChunkSentimentAnalysis
from practice_sessions.serializers import SessionChunkSerializer, ChunkSentimentAnalysisSerializer # PracticeSessionSerializer might not be directly needed here

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
BASE_FOLDER = "user-videos/" # Folder within S3 bucket for compiled videos
TEMP_MEDIA_ROOT = tempfile.gettempdir() # Use system's temporary directory for chunks and compilation
EMOTION_STATIC_FOLDER = "static-videos"  # Top-level folder for static emotion videos

# Define the rooms the user can choose from. Used for validation.
POSSIBLE_ROOMS = ['conference_room', 'board_room_1', 'board_room_2']

# Assume a fixed number of variations for each emotion video (1.mp4 to 5.mp4)
NUMBER_OF_VARIATIONS = 5

# Define the window size for analysis (number of chunks)
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
        self.room_name = None # Store the chosen room name
        self.chunk_counter = 0
        self.media_buffer = [] # Stores temporary media file paths (full video+audio chunk)
        self.audio_buffer = {}  # Dictionary to map media_path to temporary audio_path (extracted audio)
        self.transcript_buffer = {} # Dictionary to map media_path to transcript text (transcript of single chunk)
        # Map temporary media_path to SessionChunk ID (from DB, after saving with local path)
        self.media_path_to_chunk = {}
        # Dictionary to store background tasks for chunk saving to DB, keyed by media_path
        self.background_chunk_save_tasks = {}
        # Keep track of paths that need cleanup at the very end (used by compile_session_video and disconnect)
        self._temp_files_to_clean = set()


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

            # Trigger video compilation as a background task
            # Compilation will now read from local temporary files stored in self.media_buffer
            if self.session_id:
                print(f"WS: Triggering video compilation for session {self.session_id}")
                # Use asyncio.create_task to run compilation in the background
                # Pass the list of accumulated media paths to the compilation task
                # Make a copy of the media_buffer as it might be modified during cleanup
                media_paths_for_compilation = list(self.media_buffer)
                # Pass the current instance to the compilation task for WebSocket sending and accessing buffers
                asyncio.create_task(self.compile_session_video(self.session_id, media_paths_for_compilation, self))

            # Attempt to wait for background chunk save tasks to finish gracefully
            # These tasks now only save to the DB, not S3 upload
            print(f"WS: Waiting for {len(self.background_chunk_save_tasks)} pending background DB save tasks...")
            tasks_to_wait_for = list(self.background_chunk_save_tasks.values())
            if tasks_to_wait_for:
                try:
                    # Wait with a timeout for all tasks related to saving chunks
                    # Using return_exceptions=True so one failing task doesn't cancel all
                    results = await asyncio.wait_for(asyncio.gather(*tasks_to_wait_for, return_exceptions=True), timeout=10.0) # Wait up to 10 seconds
                    print("WS: Finished waiting for background DB save tasks during disconnect.")

                    for i, result in enumerate(results):
                        if isinstance(result, asyncio.CancelledError):
                            print(f"WS: Background DB save task {i} was cancelled during disconnect wait (expected).")
                        elif isinstance(result, Exception):
                            print(f"WS: Background DB save task {i} finished with unexpected exception: {result}")
                            traceback.print_exc()
                        # If result is not an exception, it finished successfully (or returned None)

                except asyncio.TimeoutError:
                    print("WS: Timeout waiting for some background DB save tasks during disconnect.")
                except Exception as e:
                    print(f"WS: Error during asyncio.gather for background DB save tasks: {e}")
                    traceback.print_exc()


            # FIX (Part 2): Simplified disconnect cleanup. It now primarily cleans up
            # files added to _temp_files_to_clean (like concat list, compiled video)
            # and logs if main buffers/maps are not empty. Chunk files are cleaned by compile_session_video.

            # Clean up temporary files added to _temp_files_to_clean by compile_session_video
            # or other parts of the code for final removal.
            files_for_final_disconnect_cleanup = list(self._temp_files_to_clean)

            if files_for_final_disconnect_cleanup:
                print(f"WS: Attempting to clean up {len(files_for_final_disconnect_cleanup)} files marked for final disconnect cleanup...")
                cleanup_tasks = []
                for file_path in files_for_final_disconnect_cleanup:
                    async def remove_file_safe(f_path):
                        try:
                            # Add a small delay before removing to ensure no other process is using it
                            await asyncio.sleep(0.05)
                            if os.path.exists(f_path):
                                os.remove(f_path)
                                print(f"WS: Removed final temporary file: {f_path}")
                            # Don't print 'not found' here, as some paths might have been cleaned by compile_session_video
                        except Exception as e:
                            print(f"WS: Error removing final file {f_path} during disconnect cleanup: {e}")
                            traceback.print_exc()
                    cleanup_tasks.append(remove_file_safe(file_path))

                if cleanup_tasks:
                    await asyncio.gather(*cleanup_tasks, return_exceptions=True)
                    print("WS: Finished final temporary file cleanup in disconnect.")
            else:
                print("WS: No files marked for final disconnect cleanup.")


            # Log if buffers/maps are not empty after cleanup attempt (indicates potential issue)
            if self.media_buffer:
                 print(f"WS: WARNING: media_buffer is not empty after disconnect cleanup: {len(self.media_buffer)} paths remaining.")
            if self.audio_buffer:
                 print(f"WS: WARNING: audio_buffer is not empty after disconnect cleanup: {len(self.audio_buffer)} paths remaining.")
            if self.transcript_buffer:
                 print(f"WS: WARNING: transcript_buffer is not empty after disconnect cleanup: {len(self.transcript_buffer)} paths remaining.")
            if self.media_path_to_chunk:
                 print(f"WS: WARNING: media_path_to_chunk is not empty after disconnect cleanup: {len(self.media_path_to_chunk)} entries remaining.")
            if self.background_chunk_save_tasks:
                 print(f"WS: WARNING: background_chunk_save_tasks is not empty after disconnect cleanup: {len(self.background_chunk_save_tasks)} tasks remaining.")


            # Clear buffers and maps *after* attempting cleanup and logging
            self.audio_buffer = {}
            self.media_buffer = []
            self.transcript_buffer = {}
            self.media_path_to_chunk = {} # FIX: Clear the map here at the very end
            self.background_chunk_save_tasks = {} # FIX: Clear the task tracking map here
            self._temp_files_to_clean = set() # Clear the final cleanup set

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
                        # Create a temporary file for the media chunk
                        media_path = os.path.join(TEMP_MEDIA_ROOT, f"{self.session_id}_{self.chunk_counter}_media.webm")
                        with open(media_path, "wb") as mf:
                            mf.write(media_bytes)
                        print(f"WS: Received media chunk {self.chunk_counter} for Session {self.session_id}. Saved to {media_path}")
                        self.media_buffer.append(media_path) # Store the local temporary path

                        # Start processing the media chunk (audio extraction, transcription)
                        # This part is still awaited to ensure audio/transcript are in buffers
                        # DB save is initiated as a background task within process_media_chunk
                        print(f"WS: Starting processing (audio/transcript/DB save) for chunk {self.chunk_counter} and WAITING for audio/transcript.")
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
        and initiates saving SessionChunk data with the LOCAL path in the background.
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
                # Use asyncio.to_thread for the blocking transcription call
                # This part is awaited to ensure transcript is in buffer for concatenation
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

            # --- Initiate Saving SessionChunk data with the LOCAL path in the BACKGROUND ---
            # Create a task to save the chunk data to the DB with the local path.
            # This task runs in the background. Store the task so analyze_windowed_media can potentially wait for it.
            # Create the task first, then add it to the dictionary to ensure it's registered
            # FIX: Ensure task is added to dict BEFORE the await, with a small sleep to help scheduler
            chunk_save_task = asyncio.create_task(self._save_session_chunk_in_background(media_path, self.chunk_counter))
            print(f"WS: Created background chunk DB save task for {media_path}: {chunk_save_task}")

            # Add to tracking dictionary immediately
            if media_path in self.background_chunk_save_tasks:
                print(f"WS: WARNING: Overwriting existing DB save task for {media_path}")
            self.background_chunk_save_tasks[media_path] = chunk_save_task
            print(f"WS: Registered background chunk DB save task for {media_path} ✅")

            # Add a small delay here after creating/registering the task to give the scheduler
            # a chance to potentially start it and populate media_path_to_chunk before analyze_windowed_media checks.
            await asyncio.sleep(0.05) # Slightly increased sleep

        except Exception as e:
            print(f"WS: Error in process_media_chunk for {media_path}: {e}")
            traceback.print_exc()

        print(f"WS: process_media_chunk finished (background DB save task initiated) for: {media_path} after {time.time() - start_time:.2f} seconds")
        # This function now returns sooner, allowing the next chunk's processing or analysis trigger to proceed.


    # MODIFIED: This function now saves SessionChunk with the LOCAL file path
    async def _save_session_chunk_in_background(self, media_path, chunk_number):
        """Saves the SessionChunk object with the local file path."""
        try:
            print(f"WS: _save_session_chunk_in_background called for chunk at {media_path} (chunk number: {chunk_number}).")
            # Call the database save method using the local media_path
            await self._save_chunk_data_local(media_path, chunk_number)
            # The chunk ID will be added to self.media_path_to_chunk inside _save_chunk_data_local

        except Exception as e:
            print(f"WS: Error in background chunk DB save for {media_path}: {e}")
            traceback.print_exc()
        finally:
            # Clean up the task tracking entry once this task is done (success or failure)
            # FIX: Removed the manual deletion from self.background_chunk_save_tasks here
            # to avoid race conditions with analyze_windowed_media checking the dict.
            # The dict will be cleared during the final disconnect cleanup.
            pass # No cleanup of tracking dict here


    async def analyze_windowed_media(self, window_paths, latest_chunk_number):
        """
        Handles concatenation (audio and transcript), analysis, and saving sentiment data for a window.
        Awaits the background chunk's DB save for the last chunk in the window before saving analysis.
        """
        start_time = time.time()
        last_media_path = window_paths[-1]
        window_chunk_number = latest_chunk_number # Refers to the number of the last chunk in the window

        print(f"WS: analyze_windowed_media started for window ending with {last_media_path} (chunk {window_chunk_number}) at {start_time}")

        # --- Add Logging Here ---
        print(f"WS: DEBUG: Current media_buffer: {[os.path.basename(p) for p in self.media_buffer]}", flush=True)
        print(f"WS: DEBUG: Current transcript_buffer keys: {[os.path.basename(k) for k in self.transcript_buffer.keys()]}", flush=True)
        print(f"WS: DEBUG: Current window_paths: {[os.path.basename(p) for p in window_paths]}", flush=True)
        print(f"WS: DEBUG: Current media_path_to_chunk keys: {[os.path.basename(k) for k in self.media_path_to_chunk.keys()] if self.media_path_to_chunk else '[]'}", flush=True)
        print(f"WS: DEBUG: Current background_chunk_save_tasks keys: {list(self.background_chunk_save_tasks.keys())}", flush=True)
        # --- End Logging ---


        combined_audio_path = None
        combined_transcript_text = ""
        analysis_result = None # Initialize analysis_result as None
        window_transcripts_list = [] # List to hold individual transcripts for concatenation

        try:
            # --- Retrieve Individual Transcripts and Concatenate ---
            print(f"WS: Retrieving and concatenating transcripts for window ending with chunk {window_chunk_number}")
            all_transcripts_found = True
            for media_path in window_paths: # window_paths are the paths for the current window
                # Retrieve transcript from the buffer using the media_path
                transcript = self.transcript_buffer.get(media_path, None)
                if transcript is not None:
                    window_transcripts_list.append(transcript)
                    print(f"WS: DEBUG: Transcript for {os.path.basename(media_path)}: '{transcript}'", flush=True)
                else:
                    print(f"WS: Warning: Transcript not found or was None in buffer for chunk media path: {media_path}. Including empty string.")
                    all_transcripts_found = False
                    window_transcripts_list.append("")

            combined_transcript_text = "".join(window_transcripts_list)
            print(f"WS: Concatenated Transcript for window: '{combined_transcript_text}'")

            if not all_transcripts_found:
                 print(f"WS: Analysis for window ending with chunk {window_chunk_number} may be incomplete due to missing transcripts.")


            # --- FFmpeg Audio Concatenation ---
            # Filter out None audio paths or paths that don't exist on disk from the audio_buffer
            required_audio_paths = [self.audio_buffer.get(media_path) for media_path in window_paths]
            valid_audio_paths = [path for path in required_audio_paths if path is not None and os.path.exists(path)]

            # We only need ANALYSIS_WINDOW_SIZE valid audio paths for concatenation
            if len(valid_audio_paths) == ANALYSIS_WINDOW_SIZE:
                 print(f"WS: Valid audio paths for concatenation: {valid_audio_paths}")

                 combined_audio_path = os.path.join(TEMP_MEDIA_ROOT, f"{self.session_id}_window_{window_chunk_number}_audio.mp3")
                 # FIX: Removed from _temp_files_to_clean here, handled in finally block
                 # self._temp_files_to_clean.add(combined_audio_path)

                 concat_command = ["ffmpeg", "-y"]
                 for audio_path in valid_audio_paths:
                     concat_command.extend(["-i", audio_path])
                 concat_command.extend(["-filter_complex", f"concat=n={len(valid_audio_paths)}:a=1:v=0", "-acodec", "libmp3lame", "-b:a", "128k", "-nostats", "-loglevel", "0", combined_audio_path])

                 print(f"WS: Running FFmpeg audio concatenation command: {' '.join(concat_command)}")
                 process = subprocess.Popen(concat_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                 stdout, stderr = await asyncio.to_thread(process.communicate)
                 returncode = await asyncio.to_thread(lambda p: p.returncode, process)

                 if returncode != 0:
                     error_output = stderr.decode()
                     print(f"WS: FFmpeg audio concatenation error (code {returncode}) for window ending with chunk {window_chunk_number}: {error_output}")
                     print(f"WS: FFmpeg stdout: {stdout.decode()}")
                     combined_audio_path = None # Ensure combined_audio_path is None on failure
                 else:
                     print(f"WS: Audio files concatenated to: {combined_audio_path}")

            else:
                 print(f"WS: Audio not found for all {ANALYSIS_WINDOW_SIZE} chunks in window ending with chunk {latest_chunk_number}. Ready audio paths: {len(valid_audio_paths)}/{ANALYSIS_WINDOW_SIZE}. Skipping audio concatenation for this window instance.")
                 combined_audio_path = None


            # --- Analyze results using OpenAI ---
            # Proceed with analysis if there is a non-empty concatenated transcript and the client is initialized
            # AND combined_audio_path is available
            if combined_transcript_text.strip() and client and combined_audio_path and os.path.exists(combined_audio_path):
                print(f"WS: Running analyze_results for combined transcript and audio.")
                analysis_start_time = time.time()
                try:
                    # Using asyncio.to_thread for blocking OpenAI/Analysis call
                    # Pass the combined_transcript_text, video_path of the first chunk (for context, though not used for analysis file), and the combined_audio_path
                    analysis_result = await asyncio.to_thread(analyze_results, combined_transcript_text, window_paths[0], combined_audio_path)
                    print(f"WS: Analysis Result: {analysis_result} after {time.time() - analysis_start_time:.2f} seconds")

                    if analysis_result is None or (isinstance(analysis_result, dict) and 'error' in analysis_result):
                         error_message = analysis_result.get('error') if isinstance(analysis_result, dict) else 'Unknown analysis error (result is None)'
                         print(f"WS: Analysis returned an error structure: {error_message}")

                except Exception as analysis_error:
                    print(f"WS: Error during analysis (analyze_results) for window ending with chunk {window_chunk_number}: {analysis_error}")
                    traceback.print_exc()
                    analysis_result = {'error': str(analysis_error), 'Feedback': {}, 'Posture': {}, 'Scores': {}} # Provide empty nested dicts for serializer safety


            elif combined_transcript_text.strip() and client:
                 print("WS: Skipping analysis: Combined audio path is missing or failed despite transcript being available.")
            elif combined_transcript_text.strip():
                 print("WS: OpenAI client not initialized. Skipping analysis despite having concatenated transcript.")
            else:
                print(f"WS: Concatenated transcript is empty or only whitespace for window ending with chunk {window_chunk_number}. Skipping analysis.")


            # --- Sending updates to the frontend ---
            if analysis_result is not None:
                serializable_analysis_result = convert_numpy_types(analysis_result)

                # Send analysis updates to the frontend
                audience_emotion = serializable_analysis_result.get('Feedback', {}).get('Audience Emotion')

                emotion_s3_url = None
                if audience_emotion and s3 and self.room_name:
                    try:
                        lowercase_emotion = audience_emotion.lower()
                        selected_variation = random.randint(1, NUMBER_OF_VARIATIONS)
                        region_name = os.environ.get('AWS_S3_REGION_NAME', os.environ.get('AWS_REGION', 'us-east-1'))
                        emotion_s3_url = f"https://{BUCKET_NAME}.s3.{region_name}.amazonaws.com/{EMOTION_STATIC_FOLDER}/{self.room_name}/{lowercase_emotion}/{selected_variation}.mp4"

                        print(f"WS: Sending window emotion update: {audience_emotion}, URL: {emotion_s3_url} (Room: {self.room_name}, Variation: {selected_variation})")
                        # FIX: Wrapped send in try/except in case connection is closed
                        try:
                            await self.send(json.dumps({
                                "type": "window_emotion_update",
                                "emotion": audience_emotion,
                                "emotion_s3_url": emotion_s3_url
                            }))
                        except Exception as send_error:
                            print(f"WS: Error sending window_emotion_update to frontend: {send_error}")


                    except Exception as e:
                         print(f"WS: Error constructing or sending emotion URL for emotion '{audience_emotion}': {e}")
                         traceback.print_exc()

                elif audience_emotion:
                     print("WS: Audience emotion detected but S3 client not configured or room_name is missing, cannot send static video URL.")
                else:
                    print("WS: No audience emotion detected or analysis structure unexpected. Cannot send static video URL.")

                print(f"WS: Sending full analysis update to frontend for window ending with chunk {window_chunk_number}: {serializable_analysis_result}")
                # FIX: Wrapped send in try/except in case connection is closed
                try:
                    await self.send(json.dumps({
                        "type": "full_analysis_update",
                        "analysis": serializable_analysis_result
                    }))
                except Exception as send_error:
                     print(f"WS: Error sending full_analysis_update to frontend: {send_error}")


                # --- Wait for the background chunk DB save task for the LAST chunk in the window ---
                # before attempting to save the window analysis results to the database.
                # Add a loop to wait for the task to appear in the dictionary, with a timeout
                last_chunk_save_task = None
                wait_start_time = time.time()
                wait_timeout = 30.0 # Increased timeout to wait for the task to appear/complete
                max_retries = 60 # Increased max retries with smaller sleep
                retry_count = 0

                while (time.time() - wait_start_time) < wait_timeout and retry_count < max_retries:
                    last_chunk_save_task = self.background_chunk_save_tasks.get(last_media_path)
                    if last_chunk_save_task:
                        print(f"WS: Background DB save task found for {last_media_path}. Waiting for it to complete...")
                        try:
                            # Wait for the specific task to finish (with the remaining timeout)
                            await asyncio.wait_for(last_chunk_save_task, timeout=wait_timeout - (time.time() - wait_start_time))
                            print(f"WS: Background DB save task for {last_media_path} completed. Proceeding to save window analysis.")

                            # --- Initiate Saving Analysis data in the BACKGROUND ---
                            # Only create the analysis save task if the chunk DB save completed
                            # FIX: Check if session_chunk_id is available before creating analysis save task
                            session_chunk_id_for_analysis = self.media_path_to_chunk.get(last_media_path)
                            if session_chunk_id_for_analysis:
                                print(f"WS: Initiating saving window analysis for chunk {window_chunk_number} in background.")
                                asyncio.create_task(self._save_window_analysis(last_media_path, analysis_result, combined_transcript_text, window_chunk_number))
                            else:
                                print(f"WS: ❌ Cannot initiate saving window analysis for chunk {window_chunk_number}: session_chunk_id not found in media_path_to_chunk after DB save task completed.")


                        except asyncio.TimeoutError:
                            print(f"WS: Timeout waiting for background DB save task for {last_media_path} to complete. Cannot save window analysis for chunk {window_chunk_number}.")
                        except Exception as task_error:
                            # This handles exceptions within the chunk save task itself
                            print(f"WS: Background DB save task for {last_media_path} failed with error: {task_error}. Cannot save window analysis.")

                        break # Exit the while loop once the task is found and processed

                    # If task not found, wait a bit and retry
                    await asyncio.sleep(0.5) # Increased sleep time between retries
                    retry_count += 1
                    print(f"WS: Retry {retry_count}/{max_retries} - waiting for background DB task of {last_media_path}")
                    print(f"WS: Current keys in self.background_chunk_save_tasks: {list(self.background_chunk_save_tasks.keys())}")
                    print(f"WS: Current keys in self.media_path_to_chunk: {list(self.media_path_to_chunk.keys())}")

                # FIX: This else block is only reached if the while loop finishes without finding/waiting for the task
                if not last_chunk_save_task:
                     # If the task wasn't found within timeout, check if the chunk ID made it to the map
                     if last_media_path in self.media_path_to_chunk:
                         print(f"WS: ⚠️ Background DB save task for {last_media_path} was not found within timeout, but chunk ID found. Proceeding to save window analysis.")
                         asyncio.create_task(self._save_window_analysis(last_media_path, analysis_result, combined_transcript_text, window_chunk_number))
                     else:
                        print(f"WS: ❌ Background DB save task for the last chunk ({last_media_path}) in the window was not found within timeout AND no chunk ID. Cannot save window analysis.")
                        print(f"WS: DEBUG: Current background_chunk_save_tasks keys: {list(self.background_chunk_save_tasks.keys())}")
                        print(f"WS: DEBUG: Current media_path_to_chunk keys: {list(self.media_path_to_chunk.keys())}")

            else:
                print(f"WS: No analysis result obtained for window ending with chunk {window_chunk_number}. Skipping analysis save and sending updates.")

        except Exception as e:
            print(f"WS: Error during windowed media analysis ending with chunk {window_chunk_number}: {e}")
            traceback.print_exc()
        finally:
            # Clean up the temporary combined audio file if it was created
            if combined_audio_path and os.path.exists(combined_audio_path):
                try:
                    await asyncio.sleep(0.05)
                    os.remove(combined_audio_path)
                    print(f"WS: Removed temporary combined audio file: {combined_audio_path}")
                    # Also remove from the final cleanup set if it was added there accidentally
                    self._temp_files_to_clean.discard(combined_audio_path)
                except Exception as e:
                    print(f"WS: Error removing temporary combined audio file {combined_audio_path}: {e}")

            # Clean up the oldest chunk from the buffers after an analysis attempt for a window finishes.
            # This happens if the media_buffer has reached or exceeded the window size.
            # This cleanup should now wait for the *DB save* task of the oldest chunk
            # AND remove it from buffers, BUT NOT DELETE THE FILE.
            while len(self.media_buffer) >= ANALYSIS_WINDOW_SIZE:
                 print(f"WS: Considering cleanup of oldest chunk from buffers after analysis. Current buffer size: {len(self.media_buffer)}")
                 try:
                     # Get the oldest media path from the buffer *without* removing it yet
                     oldest_media_path = self.media_buffer[0]
                     print(f"WS: Considering buffer cleanup for oldest media chunk {oldest_media_path}...")

                     # --- Wait for the background chunk DB save task for this specific oldest chunk to complete ---
                     save_task = self.background_chunk_save_tasks.get(oldest_media_path)

                     if save_task:
                         print(f"WS: Waiting for background DB save task for oldest chunk ({oldest_media_path}) to complete before buffer cleanup...")
                         try:
                             # Wait for the specific task to finish (with a reasonable timeout)
                             await asyncio.wait_for(save_task, timeout=90.0)
                             print(f"WS: Background DB save task for oldest chunk ({oldest_media_path}) completed. Proceeding with buffer cleanup.")
                             # If the task completed successfully, it removed itself from background_chunk_save_tasks (commented out removal)

                         except asyncio.TimeoutError:
                             print(f"WS: Timeout waiting for background DB save task for oldest chunk ({oldest_media_path}). Skipping buffer cleanup of this chunk for now.")
                             # Skip buffer cleanup for this specific chunk in this iteration; it might be cleaned up later or on disconnect
                             break # Exit the while loop to avoid blocking further cleanup attempts for other chunks that might be ready

                         except Exception as task_error:
                             print(f"WS: Background DB save task for oldest chunk ({oldest_media_path}) failed with error: {task_error}. Proceeding with buffer cleanup as task is done.")
                             # The task failed but is finished. We can proceed with buffer cleanup.


                     else:
                          # This case might happen if cleanup runs significantly later and the task finished/failed and removed itself from tracking,
                          # or if process_media_chunk had an error before starting the task.
                          print(f"WS: No background DB save task found for oldest chunk ({oldest_media_path}). Assuming it finished or wasn't started. Proceeding with buffer cleanup.")
                          # We proceed with buffer cleanup cautiously.


                     # --- If we reached here, either the task completed, failed, or didn't exist. Proceed with buffer cleanup ---
                     # Now pop the oldest media path from the buffer as the save is considered complete/dealt with
                     # Check if the oldest media path is still in the buffer before popping
                     if self.media_buffer and self.media_buffer[0] == oldest_media_path:
                         oldest_media_path_to_clean = self.media_buffer.pop(0) # Pop it now
                         print(f"WS: Popped oldest media chunk {oldest_media_path_to_clean} from buffer for cleanup.")

                         # Remove associated entries from other buffers and maps
                         # FIX: Removed FILE DELETION from here. Only removing from buffers/maps.
                         oldest_audio_path = self.audio_buffer.pop(oldest_media_path_to_clean, None)
                         oldest_transcript = self.transcript_buffer.pop(oldest_media_path_to_clean, None)
                         # FIX: Removed media_path_to_chunk pop from here. This map is cleared at the very end.
                         # oldest_chunk_id = self.media_path_to_chunk.pop(oldest_media_path_to_clean, None)


                         if oldest_transcript is not None:
                              print(f"WS: Removed transcript from buffer for oldest media path: {oldest_media_path_to_clean}")
                         else:
                              print(f"WS: No transcript found in buffer for oldest media path {oldest_media_path_to_clean} during buffer cleanup.")

                         # FIX: Removed chunk ID mapping log - map is not popped here anymore
                         # if oldest_chunk_id is not None:
                         #      print(f"WS: Removed chunk ID mapping from buffer for oldest media path: {oldest_media_path_to_clean}")
                         # else:
                         #      print(f"WS: No chunk ID mapping found in buffer for oldest media path {oldest_media_path_to_clean} during buffer cleanup.")

                     else:
                         print(f"WS: Oldest media path in buffer ({self.media_buffer[0] if self.media_buffer else 'None'}) is not the one considered for buffer cleanup ({oldest_media_path}). Skipping buffer cleanup loop iteration.")
                         # This might happen in complex async scenarios if the buffer changes unexpectedly.
                         break # Exit the while loop to prevent infinite loops

                 except IndexError:
                      # Should not happen with the while condition, but good practice
                      print("WS: media_buffer was unexpectedly empty during buffer cleanup in analyze_windowed_media finally.")
                      break # Exit the while loop if buffer is empty
                 except Exception as cleanup_error:
                      print(f"WS: Error during buffer cleanup of oldest chunk in analyze_windowed_media: {cleanup_error}")
                      traceback.print_exc()
                      break # Exit the while loop on general cleanup error
                 # The while loop condition `len(self.media_buffer) >= ANALYSIS_WINDOW_SIZE`
                 # will continue cleaning up the next oldest chunk's buffers if the buffer is still too large.


        print(f"WS: analyze_windowed_media finished (instance) for window ending with chunk {window_chunk_number} after {time.time() - start_time:.2f} seconds")


    def extract_audio(self, media_path):
        """Extracts audio from a media file using FFmpeg. This is a synchronous operation."""
        start_time = time.time()
        base, _ = os.path.splitext(media_path)
        audio_mp3_path = f"{base}.mp3"
        # FIX: Removed from _temp_files_to_clean here, handled in compile_session_video finally block
        # self._temp_files_to_clean.add(audio_mp3_path)

        ffmpeg_command = ["ffmpeg", "-y", "-i", media_path, "-vn", "-acodec", "libmp3lame", "-ab", "128k", "-nostats", "-loglevel", "0", audio_mp3_path]
        print(f"WS: Running FFmpeg command: {' '.join(ffmpeg_command)}")
        try:
            process = subprocess.Popen(ffmpeg_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            stdout, stderr = process.communicate()
            returncode = process.returncode
            if returncode == 0:
                print(f"WS: Audio extracted to: {audio_mp3_path} after {time.time() - start_time:.2f} seconds")
                if os.path.exists(audio_mp3_path) and os.path.getsize(audio_mp3_path) > 0:
                    return audio_mp3_path
                else:
                     print(f"WS: Extracted audio file is missing or empty: {audio_mp3_path}")
                     # self._temp_files_to_clean.discard(audio_mp3_path) # Removed
                     return None

            else:
                error_output = stderr.decode()
                print(f"WS: FFmpeg audio extraction error (code {returncode}): {error_output}")
                print(f"WS: FFmpeg stdout: {stdout.decode()}")
                if os.path.exists(audio_mp3_path):
                     try:
                          os.remove(audio_mp3_path)
                          print(f"WS: Removed incomplete audio file after FFmpeg error: {audio_mp3_path}")
                          # self._temp_files_to_clean.discard(audio_mp3_path) # Removed
                     except Exception as e:
                          print(f"WS: Error removing incomplete audio file {audio_mp3_path}: {e}")
                # self._temp_files_to_clean.discard(audio_mp3_path) # Removed
                return None
        except FileNotFoundError:
             print(f"WS: FFmpeg command not found. Is FFmpeg installed and in your PATH?")
             # self._temp_files_to_clean.discard(audio_mp3_path) # Removed
             return None
        except Exception as e:
             print(f"WS: Error running FFmpeg for audio extraction: {e}")
             traceback.print_exc()
             # self._temp_files_to_clean.discard(audio_mp3_path) # Removed
             return None

    # REMOVED: This function is no longer used for individual chunk uploads
    # def upload_to_s3(self, file_path):
    #     """Uploads a local file to S3. This is a synchronous operation."""
    #     ... (removed)


    # MODIFIED: This function now saves the SessionChunk with the LOCAL file path
    @database_sync_to_async
    def _save_chunk_data_local(self, media_path, chunk_number):
        """Saves the SessionChunk object with the LOCAL file path and maps media path to chunk ID."""
        start_time = time.time()
        print(f"WS: _save_chunk_data_local called for chunk at {media_path} (chunk number: {chunk_number}) at {start_time}")
        if not self.session_id:
            print("WS: Error: Session ID not available, cannot save chunk data locally.")
            return None

        try:
            print(f"WS: Attempting to get PracticeSession with id: {self.session_id}")
            try:
                 session = PracticeSession.objects.get(id=self.session_id)
                 print(f"WS: Retrieved PracticeSession: {session.id}, {session.session_name}")
            except PracticeSession.DoesNotExist:
                 print(f"WS: Error: PracticeSession with id {self.session_id} not found. Cannot save chunk data locally.")
                 return None

            # Use the local media_path for the video_file field
            # FIX: Pass the session ID (PK) to the serializer, not the object
            session_chunk_data = {
                'session': session.id, # Link to the session using its ID (PK)
                'chunk_number': chunk_number, # Include the chunk number here
                'video_file': media_path # Store the local temporary file path
            }
            print(f"WS: SessionChunk data (local path): {session_chunk_data}")
            session_chunk_serializer = SessionChunkSerializer(data=session_chunk_data)

            if session_chunk_serializer.is_valid():
                print("WS: SessionChunkSerializer is valid.")
                try:
                    # Synchronous DB call: Save the SessionChunk
                    # The serializer's save method handles the foreign key relationship
                    session_chunk = session_chunk_serializer.save()
                    print(f"WS: SessionChunk saved with ID: {session_chunk.id} for media path: {media_path} after {time.time() - start_time:.2f} seconds")
                    # Store the mapping from temporary media path to the saved chunk's ID
                    # This is done *after* successful DB save.
                    self.media_path_to_chunk[media_path] = session_chunk.id
                    print(f"WS: Added mapping: {media_path} -> {session_chunk.id}")
                    return session_chunk.id # Return the saved chunk ID

                except Exception as save_error:
                    print(f"WS: Error during SessionChunk save (local path): {save_error}")
                    traceback.print_exc()
                    return None
            else:
                # This includes the Incorrect type error
                print("WS: Error saving SessionChunk (local path):", session_chunk_serializer.errors)
                return None

        except Exception as e:
            print(f"WS: Error in _save_chunk_data_local: {e}")
            traceback.print_exc()
            return None
        finally:
             print(f"WS: _save_chunk_data_local finished after {time.time() - start_time:.2f} seconds")


    # _save_window_analysis logic remains largely the same, as it links analysis
    # to the SessionChunk ID, which is now saved with the local path.
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
            # FIX: This lookup is the source of the error. The map might be cleared or not populated.
            # Let's keep the lookup but the fix for the map cleanup should resolve the 'not found' issue.
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

                if sentiment_serializer.is_valid():
                    print(f"WS: ChunkSentimentAnalysisSerializer (for window, chunk {window_chunk_number}) is valid.")
                    try:
                        # Synchronous database call to save the sentiment analysis
                        sentiment_analysis_obj = sentiment_serializer.save()

                        print(f"WS: Window analysis data saved for chunk ID: {session_chunk_id} (chunk {window_chunk_number}) with sentiment ID: {sentiment_analysis_obj.id} after {time.time() - start_time:.2f} seconds")
                        return sentiment_analysis_obj.id # Return the saved sentiment ID

                    except Exception as save_error:
                        print(f"WS: Error during ChunkSentimentAnalysis save (for window, chunk {window_chunk_number}): {save_error}")
                        traceback.print_exc()
                        return None
                else:
                    print(f"WS: Error saving ChunkSentimentAnalysis (chunk {window_chunk_number}):", sentiment_serializer.errors)
                    return None

            else:
                # This is the error path seen in the logs
                error_message = f"SessionChunk ID not found in media_path_to_chunk for media path {media_path_of_last_chunk_in_window} during window analysis save for chunk {window_chunk_number}. Analysis will not be saved for this chunk."
                print(f"WS: {error_message}")
                # FIX: Log the current state of media_path_to_chunk for debugging
                print(f"WS: DEBUG: Current media_path_to_chunk keys in _save_window_analysis: {list(self.media_path_to_chunk.keys())}")
                return None

        except Exception as e:
            print(f"WS: Error in _save_window_analysis for media path {media_path_of_last_chunk_in_window} (chunk {window_chunk_number}): {e}")
            traceback.print_exc()
            return None
        finally:
             print(f"WS: _save_window_analysis finished after {time.time() - start_time:.2f} seconds")

    # MODIFIED: This function now retrieves LOCAL temporary file paths from SessionChunk objects
    # This function is not strictly needed anymore since compile_session_video uses the passed buffer,
    # but keeping it in case it's useful elsewhere.
    @database_sync_to_async
    def get_session_chunk_local_paths(self, session_id):
        """Retrieves local temporary file paths for all chunks of a session from the database."""
        try:
            # Order by chunk_number to ensure correct compilation order
            # Retrieve SessionChunk objects which now have local paths in video_file
            chunks = SessionChunk.objects.filter(session__id=session_id).order_by('chunk_number')
            # Extract the local file paths from the video_file field
            chunk_paths = [chunk.video_file for chunk in chunks if chunk.video_file and os.path.exists(chunk.video_file)]
            print(f"WS: Retrieved {len(chunk_paths)} local chunk paths from DB for session {session_id}")
            return chunk_paths
        except Exception as e:
            print(f"WS: Error retrieving local chunk paths for session {session_id}: {e}")
            traceback.print_exc()
            return []

    # REMOVED: This function is no longer used as we don't need S3 URLs for individual chunks for compilation
    # @database_sync_to_async
    # def get_session_chunk_urls(self, session_id):
    #    ... (removed)


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

    # NEW METHOD: To delete individual SessionChunk records after successful compilation
    @database_sync_to_async
    def delete_session_chunks(self, session_id):
        """Deletes all SessionChunk objects for a session after compilation."""
        try:
            deleted_count, _ = SessionChunk.objects.filter(session__id=session_id).delete()
            print(f"WS: Deleted {deleted_count} SessionChunk records for session {session_id} after compilation.")
        except Exception as e:
            print(f"WS: Error deleting SessionChunk records for session {session_id}: {e}")
            traceback.print_exc()


    # MODIFIED: This task now compiles from local temporary files, handles cleanup, sends URL, and logs time
    async def compile_session_video(self, session_id, media_paths_for_compilation, consumer_instance):
        """
        Background task to compile all chunks for a session from local temporary files.
        Responsible for cleaning up chunk files after compilation.
        """
        compile_start_time = time.time() # Start timer
        print(f"WS: Starting video compilation from local files for session {session_id} in background task at {compile_start_time}.")
        temp_files_created_during_compilation = [] # To keep track of temporary files created *within* this function
        valid_chunk_local_paths = [] # Declare here to be accessible in finally

        try:
            # 1. Get all local temporary file paths for the session's chunks
            # We will rely on the media_paths_for_compilation passed in, which is a copy of the buffer.
            chunk_local_paths = media_paths_for_compilation
            if not chunk_local_paths:
                print(f"WS: No local chunk paths provided for session {session_id}. Skipping compilation.")
                return

            # Ensure all paths still exist on disk before attempting compilation
            valid_chunk_local_paths = [path for path in chunk_local_paths if os.path.exists(path)]

            if not valid_chunk_local_paths:
                 print(f"WS: No valid local chunk files found on disk for session {session_id}. Skipping compilation.")
                 return

            # 2. Create a file list for FFmpeg concat demuxer
            list_file_path = os.path.join(TEMP_MEDIA_ROOT, f"{session_id}_concat_list.txt")
            temp_files_created_during_compilation.append(list_file_path) # Add to cleanup list for *this* task
            with open(list_file_path, 'w') as f:
                for chunk_path in valid_chunk_local_paths:
                    # FFmpeg expects paths in 'file /path/to/file' format in the list file
                    # Ensure paths are correctly formatted for the environment FFmpeg runs in
                    f.write(f"file '{chunk_path.replace(os.sep, '/')}'\n") # Use forward slashes for FFmpeg
            print(f"WS: Created concat list file: {list_file_path}")

            # 3. Compile video using FFmpeg concat demuxer
            compiled_video_filename = f"{session_id}_compiled.webm"
            compiled_video_path = os.path.join(TEMP_MEDIA_ROOT, compiled_video_filename)
            temp_files_created_during_compilation.append(compiled_video_path) # Add to cleanup list for *this* task

            # FFmpeg command to concatenate using demuxer
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
                # If compilation fails, we still proceed to cleanup but don't upload/update DB.
                # Add compiled video path to _temp_files_to_clean for final cleanup in disconnect if it was created but FFmpeg failed
                if os.path.exists(compiled_video_path):
                     consumer_instance._temp_files_to_clean.add(compiled_video_path)

                return # Exit the try block on compilation failure
            else:
                print(f"WS: Video compiled successfully to: {compiled_video_path}")

            # 4. Upload compiled video to S3
            print(f"WS: Uploading compiled video to S3 for session {session_id}.")
            compiled_s3_key = f"{BASE_FOLDER}{session_id}/{compiled_video_filename}"

            # Use asyncio.to_thread for blocking upload
            await asyncio.to_thread(s3.upload_file, compiled_video_path, BUCKET_NAME, compiled_s3_key)

            # Construct the final S3 URL
            region_name = os.environ.get('AWS_S3_REGION_NAME', os.environ.get('AWS_REGION', 'us-east-1'))
            compiled_s3_url = f"https://{BUCKET_NAME}.s3.{region_name}.amazonaws.com/{compiled_s3_key}"


            if compiled_s3_url:
                # FIX (User Request B): Send compiled video URL to frontend via WebSocket
                print(f"WS: Sending compiled video URL to frontend: {compiled_s3_url}")
                try:
                    # Use the consumer instance passed to the task
                    await consumer_instance.send(json.dumps({
                        "type": "video_compilation_complete",
                        "session_id": session_id,
                        "compiled_video_url": compiled_s3_url
                    }))
                    print(f"WS: Sent compiled video URL via WebSocket.")
                except Exception as send_error:
                    print(f"WS: Error sending compiled video URL to frontend via WebSocket: {send_error}")


                # 5. Update Session model with compiled video URL
                await self.update_session_with_video_url(session_id, compiled_s3_url)
                print(f"WS: Video compilation and upload complete for session {session_id}. URL: {compiled_s3_url}")

                # 6. Delete individual SessionChunk records from the database
                # These records now point to temporary local files which are being cleaned up.
                # The final video URL is stored on the main PracticeSession model.
                await self.delete_session_chunks(session_id)

            else:
                 print(f"WS: Failed to upload compiled video to S3 for session {session_id}.")
                 # If upload fails, add the compiled video path to _temp_files_to_clean
                 if os.path.exists(compiled_video_path):
                     consumer_instance._temp_files_to_clean.add(compiled_video_path)


        except Exception as e:
            print(f"WS: An error occurred during video compilation for session {session_id}: {e}")
            traceback.print_exc()

            # Ensure compiled video path is added to final cleanup if an error occurred before upload
            if os.path.exists(compiled_video_path):
                 consumer_instance._temp_files_to_clean.add(compiled_video_path)

        finally:
            # FIX (User Request A & C): Clean up temporary chunk files and compilation files *after* compilation attempt
            # Also log the total time here.
            compile_end_time = time.time()
            elapsed_compile_time = compile_end_time - compile_start_time
            print(f"WS: Video compilation task finished for session {session_id}. Total time: {elapsed_compile_time:.2f} seconds.")

            print(f"WS: Cleaning up temporary chunk and compilation files used by compilation for session {session_id}.")

            # Clean up temporary files created *within* this compilation task (concat list, compiled video)
            # These might have already been added to _temp_files_to_clean on failure, but clean them here too.
            cleanup_list = list(temp_files_created_during_compilation)

            # FIX: Also clean up the original temporary chunk files (.webm) and their associated audio (.mp3)
            # Iterate through the list of paths that were VALID for compilation
            for media_path in valid_chunk_local_paths:
                 cleanup_list.append(media_path) # Add the chunk video file
                 # Look up the associated audio path in the consumer instance's audio_buffer
                 associated_audio_path = consumer_instance.audio_buffer.get(media_path)
                 if associated_audio_path:
                      cleanup_list.append(associated_audio_path) # Add the chunk audio file

            # Ensure uniqueness just in case
            paths_to_clean_in_compile_finally = set(cleanup_list)


            # Ensure cleanup happens
            cleanup_tasks = []
            for file_path in paths_to_clean_in_compile_finally:
                 async def remove_file_safe(f_path):
                     try:
                         # Add a small delay before removing
                         await asyncio.sleep(0.05)
                         if os.path.exists(f_path):
                             os.remove(f_path)
                             print(f"WS: Removed temporary file in compilation cleanup: {f_path}")
                         else:
                              # Log as a warning if a file expected to be cleaned by compilation isn't found
                              print(f"WS: WARNING: Temporary file not found during compilation cleanup: {f_path}")
                     except Exception as e:
                         print(f"WS: Error removing file {f_path} during compilation cleanup: {e}")
                         traceback.print_exc()

                 cleanup_tasks.append(remove_file_safe(file_path))

            if cleanup_tasks:
                 await asyncio.gather(*cleanup_tasks, return_exceptions=True)
                 print(f"WS: Finished temporary file cleanup in compile_session_video finally block for session {session_id}.")

            # Note: The buffers (media_buffer, audio_buffer, etc.) and the maps
            # (media_path_to_chunk, background_chunk_save_tasks) in the consumer instance
            # are cleared by the disconnect method's cleanup logic *after* this task completes.