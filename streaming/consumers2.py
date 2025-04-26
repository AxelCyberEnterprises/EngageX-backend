#################################################################################################################
# This version uses the original mechanism but now runs the database querying and s3 upload in the background
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
BASE_FOLDER = "user-videos/"
TEMP_MEDIA_ROOT = tempfile.gettempdir() # Use system's temporary directory
EMOTION_STATIC_FOLDER = "static-videos"  # Top-level folder for static emotion videos

# Define the rooms the user can choose from. Used for validation.
POSSIBLE_ROOMS = ['conference_room', 'board_room_1', 'board_room_2']

# Assume a fixed number of variations for each emotion video (1.mp4 to 5.mp4)
NUMBER_OF_VARIATIONS = 5

# Define the window size for analysis (number of chunks)
ANALYSIS_WINDOW_SIZE = 3 # Keeping the reduced window size from the previous test

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
        self.media_path_to_chunk = {} # Map temporary media_path to SessionChunk ID (from DB, after saving)
        # Dictionary to store background tasks for chunk saving, keyed by media_path
        self.background_chunk_save_tasks = {}
        
        # New counters for detailed logging
        self.total_chunks_received = 0
        self.total_chunks_saved = 0
        self.total_window_analyses = 0
        self.window_analysis_details = []  # List of dicts: {'window_number': X, 'chunk_numbers': [...], 'saved': True/False}
        self.session_start_time = None

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
        self.session_start_time = time.time()  # Record session start time

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
            session_duration = time.time() - self.session_start_time if self.session_start_time else 0
            print(f"WS: Client disconnected for Session ID: {self.session_id}. Cleaning up...")
            print(f"WS: Session duration: {session_duration:.2f} seconds")
            print(f"WS: Session summary for {self.session_id}:")
            print(f"  Total chunks received: {self.total_chunks_received}")
            print(f"  Total chunks saved: {self.total_chunks_saved}")
            print(f"  Total window analyses performed: {self.total_window_analyses}")
            print(f"  Window analysis details: {self.window_analysis_details}")

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
        if not self.session_id:
            print("WS: Error: Session ID not available, cannot process data.")
            return

        try:
            if text_data:
                data = json.loads(text_data)
                message_type = data.get("type")
                if message_type == "media":
                    self.chunk_counter += 1
                    self.total_chunks_received += 1
                    print(f"WS: Total chunks received so far: {self.total_chunks_received}")
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
                        print(f"WS: Starting processing (audio/transcript) for chunk {self.chunk_counter} and WAITING for it to complete.")
                        await self.process_media_chunk(media_path)

                        # Trigger windowed analysis if buffer size is sufficient
                        if len(self.media_buffer) >= ANALYSIS_WINDOW_SIZE:
                            window_paths = list(self.media_buffer[-ANALYSIS_WINDOW_SIZE:])
                            print(f"WS: Triggering windowed analysis for sliding window (chunks ending with {self.chunk_counter})")
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

            # --- Initiate S3 Upload and Save SessionChunk data in the BACKGROUND ---
            # Create a task for S3 upload - this runs in a thread pool
            s3_upload_task = asyncio.create_task(asyncio.to_thread(self.upload_to_s3, media_path))

            # Create a task to await the S3 upload and then save the chunk data to the DB
            # This task runs in the background. Store the task so analyze_windowed_media can potentially wait for it.
            self.background_chunk_save_tasks[media_path] = asyncio.create_task(self._complete_chunk_save_in_background(media_path, s3_upload_task))


        except Exception as e:
            print(f"WS: Error in process_media_chunk for {media_path}: {e}")
            traceback.print_exc()

        print(f"WS: process_media_chunk finished (background tasks initiated) for: {media_path} after {time.time() - start_time:.2f} seconds")
        # This function now returns sooner, allowing the next chunk's processing or analysis trigger to proceed.


    async def _complete_chunk_save_in_background(self, media_path, s3_upload_task):
        """Awaits S3 upload and then saves the SessionChunk data."""
        try:
            # Wait for S3 upload to complete in its thread
            s3_url = await s3_upload_task

            if s3_url:
                print(f"WS: S3 upload complete for {media_path}. Attempting to save SessionChunk data in background.")
                # Now call the database save method using the obtained S3 URL
                # This call is decorated with @database_sync_to_async, running in a separate thread
                await self._save_chunk_data(media_path, s3_url)
                # The chunk ID will be added to self.media_path_to_chunk inside _save_chunk_data

            else:
                print(f"WS: S3 upload failed for {media_path}. Cannot save SessionChunk data in background.")
        except Exception as e:
            print(f"WS: Error in background chunk save for {media_path}: {e}")
            traceback.print_exc()
        finally:
            # Clean up the task tracking entry once this task is done (success or failure)
            if media_path in self.background_chunk_save_tasks:
                 # Give a brief moment for any dependent awaits (like in cleanup) to potentially register
                 await asyncio.sleep(0.01)
                 if media_path in self.background_chunk_save_tasks: # Re-check in case something added it back
                      del self.background_chunk_save_tasks[media_path]
                      print(f"WS: Removed background chunk save task tracking for {media_path}")


    async def analyze_windowed_media(self, window_paths, latest_chunk_number):
        """Handles window analysis with guaranteed synchronization between chunk saving and analysis."""
        start_time = time.time()
        last_media_path = window_paths[-1]
        window_chunk_number = latest_chunk_number

        print(f"WS: Starting window analysis for chunk {window_chunk_number}")

        # Wait for all chunks in the window to be saved and have IDs
        print(f"WS: Waiting for all chunks in window to be saved...")
        for media_path in window_paths:
            if media_path not in self.media_path_to_chunk:
                print(f"WS: Waiting for chunk ID for {media_path}...")
                # Wait for the chunk to be saved (up to 30 seconds)
                wait_start = time.time()
                while media_path not in self.media_path_to_chunk:
                    if time.time() - wait_start > 30:
                        print(f"WS: Timeout waiting for chunk ID for {media_path}")
                        return
                    await asyncio.sleep(0.1)
                print(f"WS: Got chunk ID for {media_path}: {self.media_path_to_chunk[media_path]}")

        # Get chunk numbers for this window
        window_chunk_numbers = [self.media_path_to_chunk.get(mp) for mp in window_paths]
        print(f"WS: Window chunks to analyze: {window_chunk_numbers}")

        # --- Add Logging Here ---
        print(f"WS: DEBUG: Current media_buffer: {[os.path.basename(p) for p in self.media_buffer]}", flush=True)
        print(f"WS: DEBUG: Current transcript_buffer keys: {[os.path.basename(k) for k in self.transcript_buffer.keys()]}", flush=True)
        print(f"WS: DEBUG: Current window_paths: {[os.path.basename(p) for p in window_paths]}", flush=True)
        # --- End Logging ---

        combined_audio_path = None
        combined_transcript_text = ""
        analysis_result = None
        window_transcripts_list = []

        try:
            # --- Retrieve Individual Transcripts and Concatenate ---
            print(f"WS: Retrieving and concatenating transcripts for window ending with chunk {window_chunk_number}")
            all_transcripts_found = True
            for media_path in window_paths:
                transcript = self.transcript_buffer.get(media_path)
                if transcript is not None:
                    window_transcripts_list.append(transcript)
                    print(f"WS: DEBUG: Transcript for {os.path.basename(media_path)}: '{transcript}'", flush=True)
                else:
                    print(f"WS: Warning: Transcript not found in buffer for chunk media path: {media_path}. Including empty string.")
                    all_transcripts_found = False
                    window_transcripts_list.append("")

            combined_transcript_text = "".join(window_transcripts_list)
            print(f"WS: Concatenated Transcript for window: '{combined_transcript_text}'")

            if not all_transcripts_found:
                print(f"WS: Analysis for window ending with chunk {window_chunk_number} may be incomplete due to missing transcripts.")

            # --- FFmpeg Audio Concatenation ---
            required_audio_paths = [self.audio_buffer.get(media_path) for media_path in window_paths]
            valid_audio_paths = [path for path in required_audio_paths if path is not None and os.path.exists(path)]

            if len(valid_audio_paths) == ANALYSIS_WINDOW_SIZE:
                print(f"WS: Valid audio paths for concatenation: {valid_audio_paths}")
                combined_audio_path = os.path.join(TEMP_MEDIA_ROOT, f"{self.session_id}_window_{window_chunk_number}.mp3")
                concat_command = ["ffmpeg", "-y"]
                for audio_path in valid_audio_paths:
                    concat_command.extend(["-i", audio_path])
                concat_command.extend(["-filter_complex", f"concat=n={len(valid_audio_paths)}:a=1:v=0", "-acodec", "libmp3lame", "-b:a", "128k", "-nostats", "-loglevel", "0", combined_audio_path])

                print(f"WS: Running FFmpeg audio concatenation command: {' '.join(concat_command)}")
                try:
                    result = await asyncio.to_thread(
                        subprocess.run,
                        concat_command,
                        capture_output=True,
                        text=True
                    )
                    
                    if result.returncode != 0:
                        print(f"WS: FFmpeg audio concatenation error (code {result.returncode}) for window ending with chunk {window_chunk_number}: {result.stderr}")
                        print(f"WS: FFmpeg stdout: {result.stdout}")
                        combined_audio_path = None
                    else:
                        print(f"WS: Audio files concatenated to: {combined_audio_path}")
                except Exception as e:
                    print(f"WS: Error during FFmpeg audio concatenation: {e}")
                    traceback.print_exc()
                    combined_audio_path = None
            else:
                print(f"WS: Audio not found for all {ANALYSIS_WINDOW_SIZE} chunks in window ending with chunk {latest_chunk_number}. Ready audio paths: {len(valid_audio_paths)}/{ANALYSIS_WINDOW_SIZE}. Skipping audio concatenation for this window instance.")
                combined_audio_path = None

            # --- Analyze results using OpenAI (blocking network I/O) ---
            if combined_transcript_text.strip() and client and combined_audio_path and os.path.exists(combined_audio_path):
                print(f"WS: Running analyze_results for combined transcript and audio.")
                analysis_start_time = time.time()
                try:
                    analysis_result = await asyncio.to_thread(
                        analyze_results,
                        combined_transcript_text,
                        window_paths[0],
                        combined_audio_path
                    )
                    print(f"WS: Analysis Result: {analysis_result} after {time.time() - analysis_start_time:.2f} seconds")

                    if analysis_result is None or (isinstance(analysis_result, dict) and 'error' in analysis_result):
                        error_message = analysis_result.get('error') if isinstance(analysis_result, dict) else 'Unknown analysis error (result is None)'
                        print(f"WS: Analysis returned an error structure: {error_message}")
                except Exception as analysis_error:
                    print(f"WS: Error during analysis (analyze_results) for window ending with chunk {window_chunk_number}: {analysis_error}")
                    traceback.print_exc()
                    analysis_result = {'error': str(analysis_error), 'Feedback': {}, 'Posture': {}, 'Scores': {}}
            elif combined_transcript_text.strip() and client:
                print("WS: Skipping analysis: Combined audio path is missing or failed despite transcript being available.")
            elif combined_transcript_text.strip():
                print("WS: OpenAI client not initialized. Skipping analysis despite having concatenated transcript.")
            else:
                print(f"WS: Concatenated transcript is empty or only whitespace for window ending with chunk {window_chunk_number}. Skipping analysis.")

            # --- Sending updates to the frontend ---
            if analysis_result is not None:
                serializable_analysis_result = convert_numpy_types(analysis_result)
                audience_emotion = serializable_analysis_result.get('Feedback', {}).get('Audience Emotion')

                emotion_s3_url = None
                if audience_emotion and s3 and self.room_name:
                    try:
                        lowercase_emotion = audience_emotion.lower()
                        selected_variation = random.randint(1, NUMBER_OF_VARIATIONS)
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
                    print("WS: No audience emotion detected or analysis structure unexpected. Cannot send static video URL.")

                print(f"WS: Sending full analysis update to frontend for window ending with chunk {window_chunk_number}: {serializable_analysis_result}")
                await self.send(json.dumps({
                    "type": "full_analysis_update",
                    "analysis": serializable_analysis_result
                }))

                # Save analysis in MAIN THREAD
                print(f"WS: Starting MAIN THREAD analysis save for {window_chunk_number}")
                sentiment_id = await self._save_window_analysis(
                    last_media_path,
                    analysis_result,
                    combined_transcript_text,
                    window_chunk_number
                )

                if sentiment_id:
                    self.total_window_analyses += 1
                    self.window_analysis_details.append({
                        'window_number': window_chunk_number,
                        'chunk_numbers': window_chunk_numbers,
                        'saved': True,
                        'sentiment_id': sentiment_id
                    })
                    print(f"WS: Window analysis #{self.total_window_analyses} performed for window ending with chunk {window_chunk_number}. Chunks used: {window_chunk_numbers}")
                else:
                    self.window_analysis_details.append({
                        'window_number': window_chunk_number,
                        'chunk_numbers': window_chunk_numbers,
                        'saved': False
                    })
                    print(f"WS: Window analysis for window ending with chunk {window_chunk_number} was skipped due to save failure.")
            else:
                print(f"WS: No analysis result obtained for window ending with chunk {window_chunk_number}. Skipping analysis save and sending updates.")
                self.window_analysis_details.append({
                    'window_number': window_chunk_number,
                    'chunk_numbers': window_chunk_numbers,
                    'saved': False
                })

        except Exception as e:
            print(f"WS: Critical error in window analysis for chunk {window_chunk_number}: {str(e)}")
            traceback.print_exc()
            self.window_analysis_details.append({
                'window_number': window_chunk_number,
                'chunk_numbers': window_chunk_numbers,
                'saved': False,
                'error': str(e)
            })
        finally:
            # Clean up the temporary combined audio file
            if combined_audio_path and os.path.exists(combined_audio_path):
                try:
                    await asyncio.sleep(0.05)
                    os.remove(combined_audio_path)
                    print(f"WS: Removed temporary combined audio file: {combined_audio_path}")
                except Exception as e:
                    print(f"WS: Error removing temporary combined audio file {combined_audio_path}: {e}")

            # Clean up the oldest chunk from the buffers
            while len(self.media_buffer) >= ANALYSIS_WINDOW_SIZE:
                print(f"WS: Cleaning up oldest chunk after analysis. Current buffer size: {len(self.media_buffer)}")
                try:
                    oldest_media_path = self.media_buffer[0]
                    print(f"WS: Considering cleanup for oldest media chunk {oldest_media_path}...")
                    save_task = self.background_chunk_save_tasks.get(oldest_media_path)

                    if save_task:
                        print(f"WS: Waiting for background save task for oldest chunk ({oldest_media_path}) to complete before cleaning up...")
                        try:
                            await asyncio.wait_for(save_task, timeout=90.0)
                            print(f"WS: Background save task for oldest chunk ({oldest_media_path}) completed. Proceeding with cleanup.")
                        except asyncio.TimeoutError:
                            print(f"WS: Timeout waiting for background save task for oldest chunk ({oldest_media_path}). Skipping cleanup of this chunk for now.")
                            break
                        except Exception as task_error:
                            print(f"WS: Background save task for oldest chunk ({oldest_media_path}) failed with error: {task_error}. Proceeding with cleanup as task is done.")
                    else:
                        print(f"WS: No background save task found for oldest chunk ({oldest_media_path}). Assuming it finished or wasn't started. Proceeding with cleanup.")

                    if self.media_buffer and self.media_buffer[0] == oldest_media_path:
                        oldest_media_path_to_clean = self.media_buffer.pop(0)
                        print(f"WS: Popped oldest media chunk {oldest_media_path_to_clean} from buffer for cleanup.")

                        oldest_audio_path = self.audio_buffer.pop(oldest_media_path_to_clean, None)
                        oldest_transcript = self.transcript_buffer.pop(oldest_media_path_to_clean, None)
                        oldest_chunk_id = self.media_path_to_chunk.pop(oldest_media_path_to_clean, None)

                        files_to_remove = [oldest_media_path_to_clean, oldest_audio_path]
                        for file_path in files_to_remove:
                            if file_path and os.path.exists(file_path):
                                try:
                                    await asyncio.sleep(0.05)
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
                        break
                except IndexError:
                    print("WS: media_buffer was unexpectedly empty during cleanup in analyze_windowed_media finally.")
                    break
                except Exception as cleanup_error:
                    print(f"WS: Error during cleanup of oldest chunk in analyze_windowed_media: {cleanup_error}")
                    traceback.print_exc()
                    break

            print(f"WS: Window analysis completed for {window_chunk_number} in {time.time()-start_time:.2f}s")

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

        start_time = time.time()
        file_name = os.path.basename(file_path)
        folder_path = f"{BASE_FOLDER}{self.session_id}/"
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
    def _save_chunk_data(self, media_path, s3_url):
        """Saves the SessionChunk object and maps media path to chunk ID."""
        start_time = time.time()
        print(f"WS: _save_chunk_data called for chunk at {media_path} with S3 URL {s3_url} at {start_time}")
        if not self.session_id:
            print("WS: Error: Session ID not available, cannot save chunk data.")
            return None

        if not s3_url:
             print(f"WS: Error: S3 URL not provided for {media_path}. Cannot save SessionChunk.")
             return None

        try:
            print(f"WS: Attempting to get PracticeSession with id: {self.session_id}")
            try:
                 session = PracticeSession.objects.get(id=self.session_id)
                 print(f"WS: Retrieved PracticeSession: {session.id}, {session.session_name}")
            except PracticeSession.DoesNotExist:
                 print(f"WS: Error: PracticeSession with id {self.session_id} not found. Cannot save chunk data.")
                 return None

            print(f"WS: S3 URL for SessionChunk: {s3_url}")
            session_chunk_data = {
                'session': session.id,
                'video_file': s3_url
            }
            print(f"WS: SessionChunk data: {session_chunk_data}")
            session_chunk_serializer = SessionChunkSerializer(data=session_chunk_data)

            if session_chunk_serializer.is_valid():
                print("WS: SessionChunkSerializer is valid.")
                try:
                    session_chunk = session_chunk_serializer.save()
                    self.total_chunks_saved += 1
                    print(f"WS: SessionChunk saved with ID: {session_chunk.id} for media path: {media_path} after {time.time() - start_time:.2f} seconds")
                    print(f"WS: Total chunks saved to DB so far: {self.total_chunks_saved}")
                    self.media_path_to_chunk[media_path] = session_chunk.id
                    print(f"WS: Added mapping: {media_path} -> {session_chunk.id}")
                    return session_chunk.id

                except Exception as save_error:
                    print(f"WS: Error during SessionChunk save: {save_error}")
                    traceback.print_exc()
                    return None
            else:
                print("WS: Error saving SessionChunk:", session_chunk_serializer.errors)
                return None

        except Exception as e:
            print(f"WS: Error in _save_chunk_data: {e}")
            traceback.print_exc()
            return None
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
            return None

        try:
            # Get the SessionChunk ID from the map for the *last* chunk in the window
            session_chunk_id = self.media_path_to_chunk.get(media_path_of_last_chunk_in_window)
            print(f"WS: In _save_window_analysis for {media_path_of_last_chunk_in_window} (chunk {window_chunk_number}): session_chunk_id found? {session_chunk_id is not None}. ID: {session_chunk_id}")

            if session_chunk_id:
                print(f"WS: Found SessionChunk ID: {session_chunk_id} for media path: {media_path_of_last_chunk_in_window}")

                # Initialize sentiment_data with basic required fields and the transcript
                sentiment_data = {
                    'chunk': session_chunk_id,
                    'chunk_number': window_chunk_number,
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
                        'gestures': bool(posture_data.get('Gestures', False)) if posture_data.get('Gestures') is not None else False,

                        'volume': scores_data.get('Volume Score'),
                        'pitch_variability': scores_data.get('Pitch Variability Score'),
                        'pace': scores_data.get('Pace Score'),
                        'pauses': scores_data.get('Pause Score'),
                    })
                elif isinstance(analysis_result, dict) and 'error' in analysis_result:
                     print(f"WS: Analysis result contained an error: {analysis_result.get('error')}. Saving with error message and null analysis fields.")
                else:
                     print("WS: Analysis result was not a valid dictionary or was None. Saving with null analysis fields.")

                print(f"WS: ChunkSentimentAnalysis data (for window, chunk {window_chunk_number}) prepared for saving: {sentiment_data}")

                # Use the serializer to validate and prepare data for saving
                sentiment_serializer = ChunkSentimentAnalysisSerializer(data=sentiment_data)

                if sentiment_serializer.is_valid():
                    print(f"WS: ChunkSentimentAnalysisSerializer (for window, chunk {window_chunk_number}) is valid.")
                    try:
                        # Synchronous database call to save the sentiment analysis
                        sentiment_analysis_obj = sentiment_serializer.save()
                        print(f"WS: Window analysis data saved for chunk ID: {session_chunk_id} (chunk {window_chunk_number}) with sentiment ID: {sentiment_analysis_obj.id} after {time.time() - start_time:.2f} seconds")
                        
                        # Add to window analysis details
                        self.window_analysis_details.append({
                            'window_number': window_chunk_number,
                            'chunk_numbers': [self.media_path_to_chunk.get(mp) for mp in self.media_buffer[-ANALYSIS_WINDOW_SIZE:]],
                            'saved': True,
                            'sentiment_id': sentiment_analysis_obj.id
                        })
                        print(f"WS: Added window analysis details: {self.window_analysis_details[-1]}")
                        
                        return sentiment_analysis_obj.id

                    except Exception as save_error:
                        print(f"WS: Error during ChunkSentimentAnalysis save (for window, chunk {window_chunk_number}): {save_error}")
                        traceback.print_exc()
                        return None
                else:
                    print(f"WS: Error saving ChunkSentimentAnalysis (chunk {window_chunk_number}):", sentiment_serializer.errors)
                    return None

            else:
                error_message = f"SessionChunk ID not found in media_path_to_chunk for media path {media_path_of_last_chunk_in_window} during window analysis save for chunk {window_chunk_number}. Analysis will not be saved for this chunk."
                print(f"WS: {error_message}")
                return None

        except Exception as e:
            print(f"WS: Error in _save_window_analysis for media path {media_path_of_last_chunk_in_window} (chunk {window_chunk_number}): {e}")
            traceback.print_exc()
            return None