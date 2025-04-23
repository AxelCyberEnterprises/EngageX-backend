################################################################################################
# original version
################################################################################################

# import asyncio
# import platform

# if platform.system() == 'Windows':
#     asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())


# import json
# import os
# import asyncio
# import tempfile
# import concurrent.futures
# import subprocess
# import boto3
# import openai
# import django
# import time
# import traceback
# import random

# from base64 import b64decode
# from datetime import timedelta

# from channels.generic.websocket import AsyncWebsocketConsumer
# from channels.db import database_sync_to_async

# from .sentiment_analysis import analyze_results, transcribe_audio
# from practice_sessions.models import PracticeSession, SessionChunk, ChunkSentimentAnalysis
# from practice_sessions.serializers import SessionChunkSerializer, ChunkSentimentAnalysisSerializer, PracticeSessionSerializer

# os.environ.setdefault("DJANGO_SETTINGS_MODULE", "EngageX.settings")
# django.setup()

# openai.api_key = os.environ.get("OPENAI_API_KEY")
# client = openai.OpenAI()

# s3 = boto3.client("s3", region_name=os.environ.get('AWS_REGION'))
# BUCKET_NAME = "engagex-user-content-1234"
# BASE_FOLDER = "user-videos/"
# TEMP_MEDIA_ROOT = tempfile.gettempdir()
# EMOTION_STATIC_FOLDER = "static-videos"  # Top-level folder for static emotion videos

# # Define the rooms the user can choose from. Used for validation.
# POSSIBLE_ROOMS = ['conference_room', 'board_room_1', 'board_room_2']

# # Assume a fixed number of variations for each emotion video (1.mp4 to 5.mp4)
# NUMBER_OF_VARIATIONS = 5


# class LiveSessionConsumer(AsyncWebsocketConsumer):
#     def __init__(self, *args, **kwargs):
#         super().__init__(*args, **kwargs)
#         self.session_id = None
#         self.room_name = None # Store the chosen room name
#         self.chunk_counter = 0
#         self.media_buffer = []
#         self.audio_buffer = {}  # Dictionary to map media_path to audio_path
#         self.media_path_to_chunk = {} # Map media_path to SessionChunk ID


#     async def connect(self):
#         query_string = self.scope['query_string'].decode()
#         query_params = {}
#         if query_string:
#             for param in query_string.split('&'):
#                 try:
#                     key, value = param.split('=', 1)
#                     query_params[key] = value
#                 except ValueError:
#                     print(f"WS: Warning: Could not parse query parameter: {param}")

#         self.session_id = query_params.get('session_id', None)
#         self.room_name = query_params.get('room_name', None) # Get room_name from query params

#         # Validate session_id and room_name
#         if self.session_id and self.room_name in POSSIBLE_ROOMS:
#             print(f"WS: Client connected for Session ID: {self.session_id}, Room: {self.room_name}")
#             await self.accept()
#             await self.send(json.dumps({
#                 "type": "connection_established",
#                 "message": f"Connected to session {self.session_id} in room {self.room_name}"
#             }))
#         else:
#             if not self.session_id:
#                 print("WS: Connection rejected: Missing session_id.")
#             elif self.room_name is None:
#                  print("WS: Connection rejected: Missing room_name.")
#             else: # room_name is provided but not in POSSIBLE_ROOMS
#                  print(f"WS: Connection rejected: Invalid room_name '{self.room_name}'.")

#             await self.close()

#     async def disconnect(self, close_code):
#         print(f"WS: Client disconnected for Session ID: {self.session_id}. Cleaning up...")
#         # Add cleanup for audio and media files from buffers
#         # Get all paths from buffers and the map keys
#         audio_paths_to_clean = list(self.audio_buffer.values())
#         media_paths_to_clean_from_buffer = list(self.media_buffer)
#         media_paths_to_clean_from_map = list(self.media_path_to_chunk.keys()) # Clean up any not yet popped from buffer

#         # Combine all potential paths and remove duplicates
#         all_paths_to_clean = set(audio_paths_to_clean + media_paths_to_clean_from_buffer + media_paths_to_clean_from_map)

#         for file_path in all_paths_to_clean:
#             try:
#                 if file_path and os.path.exists(file_path):
#                     os.remove(file_path)
#                     print(f"WS: Removed temporary file: {file_path}")
#             except Exception as e:
#                 print(f"WS: Error removing file {file_path}: {e}")

#         # Clear buffers and maps
#         self.audio_buffer = {}
#         self.media_buffer = []
#         self.media_path_to_chunk = {}

#         print(f"WS: Session {self.session_id} cleanup complete.")


#     async def receive(self, text_data=None, bytes_data=None):
#         if not self.session_id:
#             print("WS: Error: Session ID not available, cannot process data.")
#             return

#         try:
#             if text_data:
#                 data = json.loads(text_data)
#                 message_type = data.get("type")
#                 if message_type == "media":
#                     self.chunk_counter += 1
#                     media_blob = data.get("data")
#                     if media_blob:
#                         media_bytes = b64decode(media_blob)
#                         media_path = os.path.join(TEMP_MEDIA_ROOT, f"{self.session_id}_{self.chunk_counter}_media.webm")
#                         with open(media_path, "wb") as mf:
#                             mf.write(media_bytes)
#                         print(f"WS: Received media chunk {self.chunk_counter} for Session {self.session_id}. Saved to {media_path}")
#                         self.media_buffer.append(media_path)

#                         print(f"WS: Starting process_media_chunk for chunk {self.chunk_counter} and WAITING for it to complete.")
#                         # This ensures audio extraction and saving to buffer completes before analysis check
#                         process_task = asyncio.create_task(self.process_media_chunk(media_path))
#                         await process_task

#                         # Trigger windowed analysis based on buffer size
#                         if len(self.media_buffer) >= 4: # Trigger analysis when buffer is 4 or more
#                             window_paths = list(self.media_buffer[-4:]) # Always take the last 4 chunks
#                             print(f"WS: Triggering windowed analysis for sliding window (chunks ending with {self.chunk_counter})")
#                             # Pass the last chunk number in the window
#                             asyncio.create_task(self.analyze_windowed_media(window_paths, self.chunk_counter))

#                     else:
#                         print("WS: Error: Missing 'data' in media message.")
#                 else:
#                     print(f"WS: Received text message of type: {message_type}")
#             elif bytes_data:
#                 print(f"WS: Received binary data of length: {len(bytes_data)}")
#         except json.JSONDecodeError:
#              print(f"WS: Received invalid JSON data: {text_data}")
#         except Exception as e:
#             print(f"WS: Error processing received data: {e}")
#             traceback.print_exc()


#     async def process_media_chunk(self, media_path):
#         start_time = time.time()
#         print(f"WS: process_media_chunk started for: {media_path} at {start_time}")
#         audio_path = None
#         s3_url = None
#         try:
#             # Use concurrent.futures.ThreadPoolExecutor for sync operations (upload and audio extraction)
#             with concurrent.futures.ThreadPoolExecutor() as executor:
#                 # Submit upload to S3 as a task
#                 upload_future = executor.submit(self.upload_to_s3, media_path)

#                 # Submit audio extraction as a task
#                 extract_future = executor.submit(self.extract_audio, media_path)

#                 # Wait for both tasks to complete in a thread-safe way
#                 s3_url = await asyncio.to_thread(upload_future.result)
#                 audio_path = await asyncio.to_thread(extract_future.result) # Wait for audio extraction here

#             if audio_path and os.path.exists(audio_path): # Also check if the extracted file actually exists
#                 print(f"WS: Audio extracted and found at: {audio_path}")
#                 self.audio_buffer[media_path] = audio_path # Store the mapping
#             else:
#                 print(f"WS: Audio extraction failed or file not found for {media_path}. Audio path: {audio_path}")


#             if s3_url:
#                 print(f"WS: Attempting to save SessionChunk for {media_path} with S3 URL {s3_url}.")
#                 # Call _save_chunk_data with the S3 URL
#                 await asyncio.to_thread(self._save_chunk_data, media_path, s3_url) # Pass the obtained s3_url
#             else:
#                 print(f"WS: Error: S3 upload failed for {media_path}. Cannot save SessionChunk.")

#         except Exception as e:
#             print(f"WS: Error in process_media_chunk for {media_path}: {e}")
#             traceback.print_exc()

#         print(f"WS: process_media_chunk finished for: {media_path} after {time.time() - start_time:.2f} seconds")


#     async def analyze_windowed_media(self, window_paths, latest_chunk_number):
#         """
#         Handles concatenation, transcription, analysis, and saving sentiment data for a window.
#         Assumes audio data for chunks in window_paths is already in the audio_buffer
#         because process_media_chunk is awaited in receive.
#         """
#         # Check buffer content and existence - this check is now expected to pass if process_media_chunk succeeded
#         if len(window_paths) != 4:
#             print(f"WS: analyze_windowed_media called with {len(window_paths)} paths for window ending with chunk {latest_chunk_number}, expected 4. Skipping analysis for this window instance.")
#             # Cleanup of oldest chunk happens in finally block
#             return

#         start_time = time.time()
#         last_media_path = window_paths[-1]
#         window_chunk_number = latest_chunk_number

#         print(f"WS: analyze_windowed_media started for window ending with {last_media_path} (chunk {window_chunk_number}) at {start_time}")

#         combined_audio_path = None
#         transcript_text = None
#         analysis_result = None

#         try:
#             # Filter out None values or paths that don't exist on disk
#             required_audio_paths = [self.audio_buffer.get(media_path) for media_path in window_paths]
#             valid_audio_paths = [path for path in required_audio_paths if path is not None and os.path.exists(path)]


#             if len(valid_audio_paths) != 4:
#                  print(f"WS: Audio not found for all 4 chunks in window ending with chunk {latest_chunk_number} despite waiting for process_media_chunk. Ready audio paths: {len(valid_audio_paths)}/4. Skipping analysis for this window instance.")
#                  return
#             # *** End SIMPLIFIED ***

#             print(f"WS: Valid audio paths for concatenation: {valid_audio_paths}") # Now expected to have 4 valid paths


#             # --- FFmpeg concatenation ---
#             combined_audio_path = os.path.join(TEMP_MEDIA_ROOT, f"{self.session_id}_window_{window_chunk_number}.mp3")
#             concat_command = ["ffmpeg", "-y"]
#             for audio_path in valid_audio_paths: # Use the validated paths
#                 concat_command.extend(["-i", audio_path])
#             concat_command.extend(["-filter_complex", f"concat=n={len(valid_audio_paths)}:a=1:v=0", "-acodec", "libmp3lame", "-b:a", "128k", combined_audio_path]) # Added bitrate for safety

#             print(f"WS: Running FFmpeg command: {' '.join(concat_command)}")
#             process = subprocess.Popen(concat_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
#             stdout, stderr = await asyncio.to_thread(process.communicate) # Run blocking communicate in a thread
#             returncode = await asyncio.to_thread(lambda p: p.returncode, process) # Get return code in thread


#             if returncode != 0:
#                 error_output = stderr.decode()
#                 print(f"WS: FFmpeg audio concatenation error (code {returncode}) for window ending with chunk {window_chunk_number}: {error_output}")
#                 print(f"WS: FFmpeg stdout: {stdout.decode()}")
#                 # Cleanup of oldest chunk happens in finally block
#                 return # Stop analysis if concatenation fails

#             print(f"WS: Audio files concatenated to: {combined_audio_path}")

#             # --- Transcription (blocking network I/O) ---
#             if client:
#                 print(f"WS: Attempting transcription for {combined_audio_path}")
#                 transcription_start_time = time.time()
#                 # Using asyncio.to_thread for blocking Deepgram call
#                 transcript_text = await asyncio.to_thread(transcribe_audio, combined_audio_path)
#                 print(f"WS: Deepgram Transcription Result: {transcript_text} after {time.time() - transcription_start_time:.2f} seconds")
#             else:
#                  print("WS: OpenAI client not initialized (missing API key?). Skipping transcription.")


#             if transcript_text and client: # If transcript obtained AND client is available
#                 # --- Analyze results using OpenAI (blocking network I/O) ---
#                 print(f"WS: Running analyze_results for combined transcript.")
#                 analysis_start_time = time.time()
#                 # Pass the video path of the first chunk in the window for visual analysis reference
#                 analysis_result = await asyncio.to_thread(analyze_results, transcript_text, window_paths[0], combined_audio_path) # Using window_paths[0] as before
#                 print(f"WS: Analysis Result: {analysis_result} after {time.time() - analysis_start_time:.2f} seconds")
#             elif transcript_text:
#                  print("WS: OpenAI client not initialized. Skipping analysis despite having transcript.")
#             else:
#                 print(f"WS: No transcript obtained. Skipping analysis.")

#             # --- Saving Analysis and sending updates ---
#             if analysis_result:
#                 # Send analysis updates to the frontend
#                 audience_emotion = analysis_result.get('Feedback', {}).get('Audience Emotion')

#                 emotion_s3_url = None
#                 # Only try to construct URL if we have an emotion, S3 client, and room name
#                 if audience_emotion and s3 and self.room_name:
#                     try:
#                         # Convert emotion to lowercase for S3 path lookup
#                         lowercase_emotion = audience_emotion.lower()

#                         # Randomly select a variation number between 1 and NUMBER_OF_VARIATIONS
#                         selected_variation = random.randint(1, NUMBER_OF_VARIATIONS)

#                         # Construct the new S3 URL with room and variation
#                         region_name = os.environ.get('AWS_S3_REGION_NAME', os.environ.get('AWS_REGION', 'us-east-1')) # Default region if none set
#                         emotion_s3_url = f"https://{BUCKET_NAME}.s3.{region_name}.amazonaws.com/{EMOTION_STATIC_FOLDER}/{self.room_name}/{lowercase_emotion}/{selected_variation}.mp4"

#                         print(f"WS: Sending window emotion update: {audience_emotion}, URL: {emotion_s3_url} (Room: {self.room_name}, Variation: {selected_variation})")
#                         await self.send(json.dumps({
#                             "type": "window_emotion_update",
#                             "emotion": audience_emotion,
#                             "emotion_s3_url": emotion_s3_url
#                         }))
#                     except Exception as e:
#                          print(f"WS: Error constructing or sending emotion URL for emotion '{audience_emotion}': {e}")
#                          traceback.print_exc()

#                 elif audience_emotion:
#                      print("WS: Audience emotion detected but S3 client not configured or room_name is missing, cannot send static video URL.")
#                 else:
#                      print("WS: No audience emotion detected. Cannot send static video URL.")


#                 print(f"WS: Sending full analysis update to frontend for chunk {window_chunk_number}: {analysis_result}")
#                 await self.send(json.dumps({
#                     "type": "full_analysis_update",
#                     "analysis": analysis_result
#                 }))

#                 # Save the analysis for the last chunk in the window
#                 print(f"WS: Calling _save_window_analysis for chunk {window_chunk_number} ({last_media_path})...")
#                 await asyncio.to_thread(self._save_window_analysis, last_media_path, analysis_result, transcript_text, window_chunk_number)
#             else:
#                 print(f"WS: No analysis result obtained for window ending with chunk {window_chunk_number}. Skipping analysis save.")

#         except Exception as e:
#             print(f"WS: Error during windowed media analysis ending with chunk {window_chunk_number}: {e}")
#             traceback.print_exc()
#         finally:
#             # Clean up the temporary combined audio file
#             if combined_audio_path and os.path.exists(combined_audio_path):
#                 try:
#                     await asyncio.sleep(0.05) # Small delay before removing
#                     os.remove(combined_audio_path)
#                     print(f"WS: Removed temporary combined audio file: {combined_audio_path}")
#                 except Exception as e:
#                     print(f"WS: Error removing temporary combined audio file {combined_audio_path}: {e}")

#             # Clean up the oldest chunk from the buffers after an analysis attempt for a window finishes.
#             if len(self.media_buffer) >= 4:
#                  try:
#                      oldest_media_path = self.media_buffer.pop(0)
#                      print(f"WS: Removed oldest media chunk {oldest_media_path} from buffer.")
#                      oldest_audio_path = self.audio_buffer.pop(oldest_media_path, None)
#                      if oldest_audio_path and os.path.exists(oldest_audio_path):
#                           try:
#                               os.remove(oldest_audio_path)
#                               print(f"WS: Removed oldest temporary audio file: {oldest_audio_path}")
#                           except Exception as e:
#                                print(f"WS: Error removing oldest temporary audio file {oldest_audio_path}: {e}")
#                      elif oldest_audio_path:
#                          print(f"WS: Oldest audio path {oldest_audio_path} was in buffer but file not found during cleanup.")
#                      else:
#                          print(f"WS: No audio path found in buffer for oldest media path {oldest_media_path} during cleanup.")
#                  except IndexError:
#                      print("WS: media_buffer was empty during cleanup in analyze_windowed_media finally.")


#         print(f"WS: analyze_windowed_media finished (instance) for window ending with chunk {window_chunk_number} after {time.time() - start_time:.2f} seconds")

#     def extract_audio(self, media_path):
#         """Extracts audio from a media file using FFmpeg."""
#         start_time = time.time()
#         base, _ = os.path.splitext(media_path)
#         audio_mp3_path = f"{base}.mp3"
#         # Use list format for command for better security and compatibility
#         ffmpeg_command = ["ffmpeg", "-y", "-i", media_path, "-vn", "-acodec", "libmp3lame", "-ab", "128k", audio_mp3_path]
#         print(f"WS: Running FFmpeg command: {' '.join(ffmpeg_command)}")
#         try:
#             # Use shell=False (default) with list format
#             process = subprocess.Popen(ffmpeg_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
#             stdout, stderr = process.communicate()
#             returncode = process.returncode
#             if returncode == 0:
#                 print(f"WS: Audio extracted to: {audio_mp3_path} after {time.time() - start_time:.2f} seconds")
#                 # Verify file exists and has non-zero size
#                 if os.path.exists(audio_mp3_path) and os.path.getsize(audio_mp3_path) > 0:
#                     return audio_mp3_path
#                 else:
#                      print(f"WS: Extracted audio file is missing or empty: {audio_mp3_path}")
#                      return None

#             else:
#                 error_output = stderr.decode()
#                 print(f"WS: FFmpeg audio extraction error (code {returncode}): {error_output}")
#                 print(f"WS: FFmpeg stdout: {stdout.decode()}")
#                 # Clean up potentially created empty/partial file
#                 if os.path.exists(audio_mp3_path):
#                      try:
#                          os.remove(audio_mp3_path)
#                          print(f"WS: Removed incomplete audio file after FFmpeg error: {audio_mp3_path}")
#                      except Exception as e:
#                          print(f"WS: Error removing incomplete audio file {audio_mp3_path}: {e}")
#                 return None
#         except FileNotFoundError:
#              print(f"WS: FFmpeg command not found. Is FFmpeg installed and in your PATH?")
#              return None
#         except Exception as e:
#              print(f"WS: Error running FFmpeg for audio extraction: {e}")
#              traceback.print_exc()
#              return None

#     def upload_to_s3(self, file_path):
#         """Uploads a local file to S3."""
#         if s3 is None:
#              print(f"WS: S3 client is not initialized. Cannot upload file: {file_path}.")
#              return None

#         start_time = time.time()
#         file_name = os.path.basename(file_path)
#         folder_path = f"{BASE_FOLDER}{self.session_id}/"
#         s3_key = f"{folder_path}{file_name}"
#         try:
#             s3.upload_file(file_path, BUCKET_NAME, s3_key)
#             # Construct S3 URL - using regional endpoint format
#             region_name = os.environ.get('AWS_S3_REGION_NAME', os.environ.get('AWS_REGION', 'us-east-1'))
#             s3_url = f"https://{BUCKET_NAME}.s3.{region_name}.amazonaws.com/{s3_key}"
#             print(f"WS: Uploaded {file_path} to S3 successfully. S3 URL: {s3_url} after {time.time() - start_time:.2f} seconds.")
#             return s3_url
#         except Exception as e:
#             print(f"WS: S3 upload failed for {file_path}: {e}")
#             traceback.print_exc()
#             return None

#     def _save_chunk_data(self, media_path, s3_url):
#         """Saves the SessionChunk object and maps media path to chunk ID."""
#         start_time = time.time()
#         # Log the arguments received
#         print(f"WS: _save_chunk_data called for chunk at {media_path} with S3 URL {s3_url} at {start_time}")
#         if not self.session_id:
#             print("WS: Error: Session ID not available, cannot save chunk data.")
#             return

#         if not s3_url:
#              print(f"WS: Error: S3 URL not provided for {media_path}. Cannot save SessionChunk.")
#              return # Do not save if S3 URL is missing

#         try:
#             # Synchronous DB call: Get the session
#             print(f"WS: Attempting to get PracticeSession with id: {self.session_id}")
#             try:
#                  session = PracticeSession.objects.get(id=self.session_id)
#                  print(f"WS: Retrieved PracticeSession: {session.id}, {session.session_name}")
#             except PracticeSession.DoesNotExist:
#                  print(f"WS: Error: PracticeSession with id {self.session_id} not found. Cannot save chunk data.")
#                  return # Exit if session doesn't exist

#             print(f"WS: S3 URL for SessionChunk: {s3_url}")
#             session_chunk_data = {
#                 'session': session.id, # Link to the session using its ID
#                 'video_file': s3_url # Use the passed S3 URL
#             }
#             print(f"WS: SessionChunk data: {session_chunk_data}")
#             session_chunk_serializer = SessionChunkSerializer(data=session_chunk_data)

#             if session_chunk_serializer.is_valid():
#                 print("WS: SessionChunkSerializer is valid.")
#                 try:
#                     # Synchronous DB call: Save the SessionChunk
#                     session_chunk = session_chunk_serializer.save()
#                     print(f"WS: SessionChunk saved with ID: {session_chunk.id} for media path: {media_path} after {time.time() - start_time:.2f} seconds")
#                     # Store the mapping from temporary media path to the saved chunk's ID
#                     self.media_path_to_chunk[media_path] = session_chunk.id
#                     print(f"WS: Added mapping: {media_path} -> {session_chunk.id}")

#                 except Exception as save_error:
#                     print(f"WS: Error during SessionChunk save: {save_error}")
#                     traceback.print_exc()
#             else:
#                 print("WS: Error saving SessionChunk:", session_chunk_serializer.errors)

#         except Exception as e: # Catching other potential exceptions during DB interaction etc.
#             print(f"WS: Error in _save_chunk_data: {e}")
#             traceback.print_exc()
#         print(f"WS: _save_chunk_data finished after {time.time() - start_time:.2f} seconds")

#     # This method is called using asyncio.to_thread from analyze_windowed_media.
#     # It saves the analysis results to the database.
#     def _save_window_analysis(self, media_path, analysis_result, transcript_text, chunk_number):
#         start_time = time.time()
#         print(f"WS: _save_window_analysis started for media path: {media_path} (chunk {chunk_number}) at {start_time}")
#         if not self.session_id:
#             print("WS: Error: Session ID not available, cannot save window analysis.")
#             return

#         try:
#             # Get the SessionChunk ID from the map that was populated in _save_chunk_data
#             # This is synchronous because _save_window_analysis is already running in a thread.
#             session_chunk_id = self.media_path_to_chunk.get(media_path)

#             print(f"WS: In _save_window_analysis for {media_path} (chunk {chunk_number}): session_chunk_id found? {session_chunk_id is not None}. ID: {session_chunk_id}")

#             if session_chunk_id:
#                 print(f"WS: Found SessionChunk ID: {session_chunk_id} for media path: {media_path}")

#                 # Safely access nested dictionaries from analysis_result
#                 feedback_data = analysis_result.get('Feedback', {})
#                 posture_data = analysis_result.get('Posture', {})
#                 scores_data = analysis_result.get('Scores', {})

#                 # Prepare data for ChunkSentimentAnalysis based on the expected structure from analyze_results
#                 sentiment_data = {
#                     'chunk': session_chunk_id, # Link to the SessionChunk using its ID
#                     'chunk_number': chunk_number, # Store the chunk number for context

#                     # Map from 'Feedback'
#                     'audience_emotion': feedback_data.get('Audience Emotion'),
#                     'conviction': feedback_data.get('Conviction'), # Use get, default is None if key missing
#                     'clarity': feedback_data.get('Clarity'),
#                     'impact': feedback_data.get('Impact'),
#                     'brevity': feedback_data.get('Brevity'),
#                     'transformative_potential': feedback_data.get('Transformative Potential'),
#                     'trigger_response': feedback_data.get('Trigger Response'),
#                     'filler_words': feedback_data.get('Filler Words'),
#                     'grammar': feedback_data.get('Grammar'),
#                     'general_feedback_summary': feedback_data.get('General Feedback Summary', ''), # Default to empty string


#                     # Map from 'Posture'
#                     'posture': posture_data.get('Posture'),
#                     'motion': posture_data.get('Motion'),
#                     # Assuming Gestures is a boolean in analysis_result or can be converted
#                     'gestures': bool(posture_data.get('Gestures', False)), # Ensure boolean or default to False


#                     # Map from the 'Scores' nested dictionary
#                     'volume': scores_data.get('Volume Score'),
#                     'pitch_variability': scores_data.get('Pitch Variability Score'),
#                     'pace': scores_data.get('Pace Score'),
#                     'pauses': scores_data.get('Pause Score'), # Use Pause Score key

#                     # Add the combined transcript
#                     'chunk_transcript': transcript_text,
#                 }

#                 print(f"WS: ChunkSentimentAnalysis data (for window, chunk {chunk_number}): {sentiment_data}")

#                 # Use the serializer to validate and prepare data for saving
#                 sentiment_serializer = ChunkSentimentAnalysisSerializer(data=sentiment_data)

#                 if sentiment_serializer.is_valid():
#                     print(f"WS: ChunkSentimentAnalysisSerializer (for window, chunk {chunk_number}) is valid.")
#                     try:
#                         # Synchronous database call to save the sentiment analysis
#                         sentiment_analysis_obj = sentiment_serializer.save()

#                         print(f"WS: Window analysis data saved for chunk ID: {session_chunk_id} (chunk {chunk_number}) with sentiment ID: {sentiment_analysis_obj.id} after {time.time() - start_time:.2f} seconds")

#                     except Exception as save_error:
#                         print(f"WS: Error during ChunkSentimentAnalysis save (for window, chunk {chunk_number}): {save_error}")
#                         traceback.print_exc() # Print traceback for save errors
#                 else:
#                     # Print validation errors if serializer is not valid
#                     print(f"WS: Error saving ChunkSentimentAnalysis (chunk {chunk_number}):", sentiment_serializer.errors)

#             else:
#                 # This logs if session_chunk_id was None (meaning _save_chunk_data failed or hasn't run)
#                 error_message = f"SessionChunk ID not found in media_path_to_chunk for media path {media_path} during window analysis save for chunk {chunk_number}. Analysis will not be saved for this chunk."
#                 print(f"WS: {error_message}")

#         except Exception as e:
#             print(f"WS: Error in _save_window_analysis for media path {media_path} (chunk {chunk_number}): {e}")
#             traceback.print_exc() # Print traceback for general _save_window_analysis errors

#         print(f"WS: _save_window_analysis finished for media path {media_path} (chunk {chunk_number}) after {time.time() - start_time:.2f} seconds")



########################################################################################################
# This version works by transcribing each chunk instead of concatenating all the window chunks
########################################################################################################

# import asyncio
# import platform

# # Set the event loop policy for Windows if necessary
# if platform.system() == 'Windows':
#     asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())


# import json
# import os
# import asyncio
# import tempfile
# import concurrent.futures
# import subprocess
# import boto3
# import openai
# import django
# import time
# import traceback
# import random # Import random for selecting variations

# from base64 import b64decode
# from datetime import timedelta

# from channels.generic.websocket import AsyncWebsocketConsumer
# # Import database_sync_to_async for handling synchronous database operations in async context
# from channels.db import database_sync_to_async

# # Assuming these are in a local file sentiment_analysis.py
# # transcribe_audio now needs to handle a single audio file (used in process_media_chunk)
# # analyze_results now receives a concatenated transcript and the combined audio path
# from .sentiment_analysis import analyze_results, transcribe_audio

# from practice_sessions.models import PracticeSession, SessionChunk, ChunkSentimentAnalysis
# from practice_sessions.serializers import SessionChunkSerializer, ChunkSentimentAnalysisSerializer # PracticeSessionSerializer might not be directly needed here

# # Ensure Django settings are configured
# os.environ.setdefault("DJANGO_SETTINGS_MODULE", "EngageX.settings")
# django.setup()

# # Initialize OpenAI client
# # Ensure OPENAI_API_KEY is set in your environment
# openai.api_key = os.environ.get("OPENAI_API_KEY")
# client = openai.OpenAI() if openai.api_key else None # Initialize client only if API key is available

# # Initialize S3 client
# # Ensure AWS_REGION is set in your environment or settings
# s3 = boto3.client("s3", region_name=os.environ.get('AWS_REGION'))
# BUCKET_NAME = "engagex-user-content-1234" # Replace with your actual S3 bucket name
# BASE_FOLDER = "user-videos/"
# TEMP_MEDIA_ROOT = tempfile.gettempdir() # Use system's temporary directory
# EMOTION_STATIC_FOLDER = "static-videos"  # Top-level folder for static emotion videos

# # Define the rooms the user can choose from. Used for validation.
# POSSIBLE_ROOMS = ['conference_room', 'board_room_1', 'board_room_2']

# # Assume a fixed number of variations for each emotion video (1.mp4 to 5.mp4)
# NUMBER_OF_VARIATIONS = 5

# # Define the window size for analysis (number of chunks)
# ANALYSIS_WINDOW_SIZE = 4


# class LiveSessionConsumer(AsyncWebsocketConsumer):
#     def __init__(self, *args, **kwargs):
#         super().__init__(*args, **kwargs)
#         self.session_id = None
#         self.room_name = None # Store the chosen room name
#         self.chunk_counter = 0
#         self.media_buffer = [] # Stores temporary media file paths (full video+audio chunk)
#         self.audio_buffer = {}  # Dictionary to map media_path to temporary audio_path (extracted audio)
#         self.transcript_buffer = {} # Dictionary to map media_path to transcript text (transcript of single chunk)
#         self.media_path_to_chunk = {} # Map temporary media_path to SessionChunk ID (from DB, after saving)


#     async def connect(self):
#         query_string = self.scope['query_string'].decode()
#         query_params = {}
#         if query_string:
#             for param in query_string.split('&'):
#                 try:
#                     key, value = param.split('=', 1)
#                     query_params[key] = value
#                 except ValueError:
#                     print(f"WS: Warning: Could not parse query parameter: {param}")

#         self.session_id = query_params.get('session_id', None)
#         self.room_name = query_params.get('room_name', None) # Get room_name from query params

#         # Validate session_id and room_name
#         if self.session_id and self.room_name in POSSIBLE_ROOMS:
#             print(f"WS: Client connected for Session ID: {self.session_id}, Room: {self.room_name}")
#             await self.accept()
#             await self.send(json.dumps({
#                 "type": "connection_established",
#                 "message": f"Connected to session {self.session_id} in room {self.room_name}"
#             }))
#         else:
#             if not self.session_id:
#                 print("WS: Connection rejected: Missing session_id.")
#             elif self.room_name is None:
#                  print("WS: Connection rejected: Missing room_name.")
#             else: # room_name is provided but not in POSSIBLE_ROOMS
#                  print(f"WS: Connection rejected: Invalid room_name '{self.room_name}'.")

#             await self.close()


#     async def disconnect(self, close_code):
#         print(f"WS: Client disconnected for Session ID: {self.session_id}. Cleaning up...")
#         # Get all paths from buffers and the map keys
#         audio_paths_to_clean = list(self.audio_buffer.values())
#         media_paths_to_clean_from_buffer = list(self.media_buffer)
#         media_paths_to_clean_from_map_keys = list(self.media_path_to_chunk.keys())

#         # Combine all potential paths and remove duplicates
#         all_paths_to_clean = set(audio_paths_to_clean + media_paths_to_clean_from_buffer + media_paths_to_clean_from_map_keys)

#         for file_path in all_paths_to_clean:
#             try:
#                 # Add a small delay before removing to ensure no other process is using it
#                 await asyncio.sleep(0.05)
#                 if file_path and os.path.exists(file_path):
#                     os.remove(file_path)
#                     print(f"WS: Removed temporary file: {file_path}")
#             except Exception as e:
#                 print(f"WS: Error removing file {file_path}: {e}")

#         # Clear buffers and maps
#         self.audio_buffer = {}
#         self.media_buffer = []
#         self.transcript_buffer = {} # Clear the transcript buffer
#         self.media_path_to_chunk = {}

#         print(f"WS: Session {self.session_id} cleanup complete.")


#     async def receive(self, text_data=None, bytes_data=None):
#         if not self.session_id:
#             print("WS: Error: Session ID not available, cannot process data.")
#             return

#         try:
#             if text_data:
#                 data = json.loads(text_data)
#                 message_type = data.get("type")
#                 if message_type == "media":
#                     self.chunk_counter += 1
#                     media_blob = data.get("data")
#                     if media_blob:
#                         media_bytes = b64decode(media_blob)
#                         # Create a temporary file for the media chunk
#                         media_path = os.path.join(TEMP_MEDIA_ROOT, f"{self.session_id}_{self.chunk_counter}_media.webm")
#                         with open(media_path, "wb") as mf:
#                             mf.write(media_bytes)
#                         print(f"WS: Received media chunk {self.chunk_counter} for Session {self.session_id}. Saved to {media_path}")
#                         self.media_buffer.append(media_path)

#                         # Process the individual media chunk asynchronously
#                         print(f"WS: Starting process_media_chunk for chunk {self.chunk_counter} and WAITING for it to complete.")
#                         # Await processing of this specific chunk to ensure audio, transcript, and DB save are handled
#                         process_task = asyncio.create_task(self.process_media_chunk(media_path))
#                         await process_task # Ensure processing for this chunk finishes before considering window analysis

#                         # Trigger windowed analysis if buffer size is sufficient
#                         # This check happens *after* the latest chunk's processing is done
#                         if len(self.media_buffer) >= ANALYSIS_WINDOW_SIZE:
#                             # Take the last ANALYSIS_WINDOW_SIZE chunks for the sliding window
#                             window_paths = list(self.media_buffer[-ANALYSIS_WINDOW_SIZE:])
#                             print(f"WS: Triggering windowed analysis for sliding window (chunks ending with {self.chunk_counter})")
#                             # Pass the list of media paths in the window and the latest chunk number
#                             # The call to _save_window_analysis is now awaited within analyze_windowed_media using database_sync_to_async
#                             asyncio.create_task(self.analyze_windowed_media(window_paths, self.chunk_counter))

#                     else:
#                         print("WS: Error: Missing 'data' in media message.")
#                 else:
#                     print(f"WS: Received text message of type: {message_type}")
#             elif bytes_data:
#                 print(f"WS: Received binary data of length: {len(bytes_data)}")
#         except json.JSONDecodeError:
#              print(f"WS: Received invalid JSON data: {text_data}")
#         except Exception as e:
#             print(f"WS: Error processing received data: {e}")
#             traceback.print_exc()


#     async def process_media_chunk(self, media_path):
#         """
#         Processes a single media chunk: extracts audio, transcribes,
#         uploads media to S3, and saves SessionChunk data.
#         Runs in a separate task, awaits internal blocking calls via to_thread/database_sync_to_async.
#         """
#         start_time = time.time()
#         print(f"WS: process_media_chunk started for: {media_path} at {start_time}")
#         audio_path = None
#         s3_url = None
#         chunk_transcript = None # Initialize transcript as None

#         try:
#             # Use concurrent.futures.ThreadPoolExecutor for blocking operations like S3 upload and audio extraction
#             # This allows the main asyncio loop to not be blocked by these synchronous calls
#             with concurrent.futures.ThreadPoolExecutor() as executor:
#                 # Submit upload to S3 as a task
#                 # Await the result of this thread-pool task
#                 upload_future = executor.submit(self.upload_to_s3, media_path)

#                 # Submit audio extraction as a task
#                 # Await the result of this thread-pool task
#                 extract_future = executor.submit(self.extract_audio, media_path)

#                 # Wait for S3 upload and audio extraction to complete in thread pool
#                 s3_url = await asyncio.to_thread(upload_future.result)
#                 audio_path = await asyncio.to_thread(extract_future.result)


#             # Check if audio extraction was successful and file exists
#             if audio_path and os.path.exists(audio_path):
#                 print(f"WS: Audio extracted and found at: {audio_path}")
#                 self.audio_buffer[media_path] = audio_path # Store the mapping

#                 # --- Transcription of the single chunk (blocking network I/O) ---
#                 # Use asyncio.to_thread for the blocking transcription call
#                 if client: # Check if OpenAI client was initialized
#                     print(f"WS: Attempting transcription for single chunk audio: {audio_path}")
#                     transcription_start_time = time.time()
#                     try:
#                         # Assuming transcribe_audio returns the transcript string or None on failure
#                         chunk_transcript = await asyncio.to_thread(transcribe_audio, audio_path)
#                         print(f"WS: Single chunk Transcription Result: {chunk_transcript} after {time.time() - transcription_start_time:.2f} seconds")

#                         # Always store the result, even if it's None or empty string
#                         self.transcript_buffer[media_path] = chunk_transcript
#                         print(f"WS: Stored transcript for {media_path} in buffer.")

#                     except Exception as transcribe_error:
#                         print(f"WS: Error during single chunk transcription for {audio_path}: {transcribe_error}")
#                         traceback.print_exc() # Print traceback for transcription errors
#                         # If transcription fails, chunk_transcript is still None, and None is stored in buffer


#                 else:
#                     print("WS: OpenAI client not initialized (missing API key?). Skipping single chunk transcription.")
#                     self.transcript_buffer[media_path] = None # Store None if transcription is skipped

#             else:
#                 print(f"WS: Audio extraction failed or file not found for {media_path}. Audio path: {audio_path}. Skipping transcription for this chunk.")
#                 self.audio_buffer[media_path] = None # Store None if audio extraction failed
#                 self.transcript_buffer[media_path] = None # Store None if transcription is skipped


#             # --- Save SessionChunk data ---
#             # Use database_sync_to_async decorator for this blocking DB call
#             # This save happens regardless of transcription success, as the video file exists
#             if s3_url:
#                 print(f"WS: Attempting to save SessionChunk for {media_path} with S3 URL {s3_url}.")
#                 # Call _save_chunk_data which is now async thanks to the decorator @database_sync_to_async
#                 await self._save_chunk_data(media_path, s3_url)
#             else:
#                 print(f"WS: Error: S3 upload failed for {media_path}. Cannot save SessionChunk.")

#         except Exception as e:
#             print(f"WS: Error in process_media_chunk for {media_path}: {e}")
#             traceback.print_exc()

#         print(f"WS: process_media_chunk finished for: {media_path} after {time.time() - start_time:.2f} seconds")


#     async def analyze_windowed_media(self, window_paths, latest_chunk_number):
#         """
#         Handles concatenation (audio and transcript), analysis, and saving sentiment data for a window.
#         Audio concatenation is done to provide the analyze_results function with the
#         same input signature it had in the working version.
#         Assumes individual chunk audio extraction and transcription is complete for chunks
#         in window_paths because process_media_chunk is awaited for the latest chunk
#         and previous tasks should have completed or are ongoing.
#         """
#         start_time = time.time()
#         last_media_path = window_paths[-1]
#         window_chunk_number = latest_chunk_number # Refers to the number of the last chunk in the window

#         print(f"WS: analyze_windowed_media started for window ending with {last_media_path} (chunk {window_chunk_number}) at {start_time}")

#         combined_audio_path = None # Reintroduce combined audio path
#         combined_transcript_text = ""
#         analysis_result = None # Initialize analysis_result as None
#         window_transcripts_list = [] # List to hold individual transcripts for concatenation

#         try:
#             # --- Retrieve Individual Transcripts and Concatenate (Existing New Logic) ---
#             print(f"WS: Retrieving and concatenating transcripts for window ending with chunk {window_chunk_number}")
#             all_transcripts_found = True
#             for media_path in window_paths:
#                 # Retrieve transcript from the buffer using the media_path
#                 # Use .get() with a default of None to handle missing keys gracefully
#                 transcript = self.transcript_buffer.get(media_path, None)
#                 if transcript is not None: # Check if the value is not None
#                     window_transcripts_list.append(transcript)
#                 else:
#                     # If any transcript is missing (None) or key not in buffer, log a warning
#                     # and add an empty string for concatenation
#                     print(f"WS: Warning: Transcript not found or was None in buffer for chunk media path: {media_path}. Including empty string.")
#                     all_transcripts_found = False
#                     window_transcripts_list.append("")


#             combined_transcript_text = "".join(window_transcripts_list)
#             print(f"WS: Concatenated Transcript for window: '{combined_transcript_text}'")

#             if not all_transcripts_found:
#                  print(f"WS: Analysis for window ending with chunk {window_chunk_number} may be incomplete due to missing transcripts.")


#             # --- FFmpeg Audio Concatenation (Reintroduced) ---
#             # Filter out None audio paths or paths that don't exist on disk
#             required_audio_paths = [self.audio_buffer.get(media_path) for media_path in window_paths]
#             valid_audio_paths = [path for path in required_audio_paths if path is not None and os.path.exists(path)]

#             if len(valid_audio_paths) == ANALYSIS_WINDOW_SIZE: # Only concatenate if we have audio for all chunks
#                  print(f"WS: Valid audio paths for concatenation: {valid_audio_paths}")

#                  combined_audio_path = os.path.join(TEMP_MEDIA_ROOT, f"{self.session_id}_window_{window_chunk_number}.mp3")
#                  concat_command = ["ffmpeg", "-y"]
#                  for audio_path in valid_audio_paths:
#                      concat_command.extend(["-i", audio_path])
#                  # Added -nostats -loglevel 0 to reduce FFmpeg output noise
#                  concat_command.extend(["-filter_complex", f"concat=n={len(valid_audio_paths)}:a=1:v=0", "-acodec", "libmp3lame", "-b:a", "128k", "-nostats", "-loglevel", "0", combined_audio_path])

#                  print(f"WS: Running FFmpeg audio concatenation command: {' '.join(concat_command)}")
#                  process = subprocess.Popen(concat_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
#                  stdout, stderr = await asyncio.to_thread(process.communicate) # Run blocking communicate in a thread
#                  returncode = await asyncio.to_thread(lambda p: p.returncode, process) # Get return code in thread

#                  if returncode != 0:
#                      error_output = stderr.decode()
#                      print(f"WS: FFmpeg audio concatenation error (code {returncode}) for window ending with chunk {window_chunk_number}: {error_output}")
#                      print(f"WS: FFmpeg stdout: {stdout.decode()}")
#                      # combined_audio_path remains None if concatenation fails
#                  else:
#                      print(f"WS: Audio files concatenated to: {combined_audio_path}")
#                      # combined_audio_path is set here if successful

#             else:
#                  print(f"WS: Audio not found for all {ANALYSIS_WINDOW_SIZE} chunks in window ending with chunk {latest_chunk_number}. Ready audio paths: {len(valid_audio_paths)}/{ANALYSIS_WINDOW_SIZE}. Skipping audio concatenation for this window instance.")
#                  # combined_audio_path remains None


#             # --- Analyze results using OpenAI (blocking network I/O) ---
#             # Proceed with analysis if there is a non-empty concatenated transcript and the client is initialized
#             # Also ensure combined_audio_path is available if analyze_results requires it non-None
#             # Based on old code, analyze_results expects transcript, video_path, and audio_path_for_metrics
#             # We'll call it if we have a transcript AND the combined_audio_path is available (mimicking old behavior)
#             if combined_transcript_text.strip() and client and combined_audio_path and os.path.exists(combined_audio_path):
#                 print(f"WS: Running analyze_results for combined transcript and audio.")
#                 analysis_start_time = time.time()
#                 try:
#                     # Using asyncio.to_thread for blocking OpenAI/Analysis call
#                     # Pass the combined_transcript_text, video_path of the first chunk, and the combined_audio_path
#                     analysis_result = await asyncio.to_thread(analyze_results, combined_transcript_text, window_paths[0], combined_audio_path)
#                     print(f"WS: Analysis Result: {analysis_result} after {time.time() - analysis_start_time:.2f} seconds")

#                     # Check if the result is a dictionary and contains an error (as implemented previously for robustness)
#                     if analysis_result is None or (isinstance(analysis_result, dict) and 'error' in analysis_result):
#                          error_message = analysis_result.get('error') if isinstance(analysis_result, dict) else 'Unknown analysis error (result is None)'
#                          print(f"WS: Analysis returned an error structure: {error_message}")
#                          # analysis_result variable already holds the error dictionary or None

#                 except Exception as analysis_error:
#                     print(f"WS: Error during analysis (analyze_results) for window ending with chunk {window_chunk_number}: {analysis_error}")
#                     traceback.print_exc() # Print traceback for analysis errors
#                     # Structure the error result consistently as a dictionary with an error key
#                     analysis_result = {'error': str(analysis_error), 'Feedback': {}, 'Posture': {}, 'Scores': {}} # Provide empty nested dicts for serializer safety


#             elif combined_transcript_text.strip() and client:
#                  # Scenario where transcript exists and client is ready, but combined_audio_path is missing/failed
#                  print("WS: Skipping analysis: Combined audio path is missing or failed despite transcript being available.")
#                  # analysis_result remains None
#             elif combined_transcript_text.strip():
#                  # Scenario where transcript exists, but client is not initialized
#                  print("WS: OpenAI client not initialized. Skipping analysis despite having concatenated transcript.")
#                  # analysis_result remains None
#             else:
#                 print(f"WS: Concatenated transcript is empty or only whitespace for window ending with chunk {window_chunk_number}. Skipping analysis.")
#                 # analysis_result remains None


#             # --- Saving Analysis and sending updates ---
#             # Proceed if there is a valid analysis_result (even if it contains an error structure from a failed analysis)
#             if analysis_result is not None:
#                 # Send analysis updates to the frontend
#                 # Access Feedback/Audience Emotion safely, accounting for potential error structure
#                 audience_emotion = analysis_result.get('Feedback', {}).get('Audience Emotion')

#                 emotion_s3_url = None
#                 # Only try to construct URL if we have a detected emotion, S3 client, and room name
#                 if audience_emotion and s3 and self.room_name:
#                     try:
#                         # Convert emotion to lowercase for S3 path lookup
#                         lowercase_emotion = audience_emotion.lower()

#                         # Randomly select a variation number between 1 and NUMBER_OF_VARIATIONS
#                         selected_variation = random.randint(1, NUMBER_OF_VARIATIONS)

#                         # Construct the new S3 URL with room and variation
#                         # Ensure AWS_S3_REGION_NAME or AWS_REGION is set
#                         region_name = os.environ.get('AWS_S3_REGION_NAME', os.environ.get('AWS_REGION', 'us-east-1'))
#                         emotion_s3_url = f"https://{BUCKET_NAME}.s3.{region_name}.amazonaws.com/{EMOTION_STATIC_FOLDER}/{self.room_name}/{lowercase_emotion}/{selected_variation}.mp4"

#                         print(f"WS: Sending window emotion update: {audience_emotion}, URL: {emotion_s3_url} (Room: {self.room_name}, Variation: {selected_variation})")
#                         await self.send(json.dumps({
#                             "type": "window_emotion_update",
#                             "emotion": audience_emotion,
#                             "emotion_s3_url": emotion_s3_url
#                         }))
#                     except Exception as e:
#                          print(f"WS: Error constructing or sending emotion URL for emotion '{audience_emotion}': {e}")
#                          traceback.print_exc()

#                 elif audience_emotion:
#                      print("WS: Audience emotion detected but S3 client not configured or room_name is missing, cannot send static video URL.")
#                 else:
#                     # This will also print if analysis_result didn't have a 'Feedback'/'Audience Emotion' structure or if audience_emotion was None/empty
#                     print("WS: No audience emotion detected or analysis structure unexpected. Cannot send static video URL.")


#                 print(f"WS: Sending full analysis update to frontend for window ending with chunk {window_chunk_number}: {analysis_result}")
#                 await self.send(json.dumps({
#                     "type": "full_analysis_update",
#                     "analysis": analysis_result
#                 }))

#                 # Save the analysis for the last chunk in the window
#                 print(f"WS: Calling _save_window_analysis for chunk {window_chunk_number} ({last_media_path})...")
#                 # Use database_sync_to_async decorator for this blocking DB call
#                 # Pass the last media path, analysis result, the COMBINED transcript, and chunk number
#                 # This is now awaited because _save_window_analysis is decorated with @database_sync_to_async
#                 await self._save_window_analysis(last_media_path, analysis_result, combined_transcript_text, window_chunk_number)
#             else:
#                 print(f"WS: No analysis result obtained for window ending with chunk {window_chunk_number}. Skipping analysis save and sending updates.")

#         except Exception as e: # Catch any exceptions during the analyze_windowed_media process itself (excluding analyze_results internal errors already caught)
#             print(f"WS: Error during windowed media analysis ending with chunk {window_chunk_number}: {e}")
#             traceback.print_exc() # Print traceback for general analyze_windowed_media errors
#         finally:
#             # Clean up the temporary combined audio file if it was created
#             if combined_audio_path and os.path.exists(combined_audio_path):
#                 try:
#                     await asyncio.sleep(0.05) # Small delay before removing
#                     os.remove(combined_audio_path)
#                     print(f"WS: Removed temporary combined audio file: {combined_audio_path}")
#                 except Exception as e:
#                     print(f"WS: Error removing temporary combined audio file {combined_audio_path}: {e}")

#             # Clean up the oldest chunk from the buffers after an analysis attempt for a window finishes.
#             # This happens if the media_buffer has reached or exceeded the window size
#             # We only want to remove *one* oldest chunk per analysis trigger
#             # The condition `len(self.media_buffer) > ANALYSIS_WINDOW_SIZE` ensures we maintain a buffer of ANALYSIS_WINDOW_SIZE
#             while len(self.media_buffer) > ANALYSIS_WINDOW_SIZE:
#                  print(f"WS: Cleaning up oldest chunk after analysis. Current buffer size: {len(self.media_buffer)}")
#                  try:
#                      # Remove the oldest media path from the buffer
#                      oldest_media_path = self.media_buffer.pop(0)
#                      print(f"WS: Removed oldest media chunk {oldest_media_path} from buffer.")

#                      # Remove associated entries from other buffers and maps
#                      oldest_audio_path = self.audio_buffer.pop(oldest_media_path, None)
#                      oldest_transcript = self.transcript_buffer.pop(oldest_media_path, None)
#                      oldest_chunk_id = self.media_path_to_chunk.pop(oldest_media_path, None)


#                      # Clean up the temporary files associated with this oldest chunk
#                      files_to_remove = [oldest_media_path, oldest_audio_path]
#                      for file_path in files_to_remove:
#                          if file_path and os.path.exists(file_path):
#                              try:
#                                  await asyncio.sleep(0.05) # Small delay before removing
#                                  os.remove(file_path)
#                                  print(f"WS: Removed temporary file: {file_path}")
#                              except Exception as e:
#                                  print(f"WS: Error removing temporary file {file_path}: {e}")
#                          elif file_path:
#                             print(f"WS: File path {file_path} was associated but not found on disk during cleanup.")


#                      if oldest_transcript is not None:
#                           print(f"WS: Removed transcript from buffer for oldest media path: {oldest_media_path}")
#                      else:
#                           print(f"WS: No transcript found in buffer for oldest media path {oldest_media_path} during cleanup.")

#                      if oldest_chunk_id is not None:
#                           print(f"WS: Removed chunk ID mapping from buffer for oldest media path: {oldest_media_path}")
#                      else:
#                           print(f"WS: No chunk ID mapping found in buffer for oldest media path {oldest_media_path} during cleanup.")

#                  except IndexError:
#                       # Should not happen with the while condition, but good practice
#                       print("WS: media_buffer was unexpectedly empty during cleanup in analyze_windowed_media finally.")
#                  except Exception as cleanup_error:
#                       print(f"WS: Error during cleanup of oldest chunk in analyze_windowed_media: {cleanup_error}")
#                       traceback.print_exc()
#             # The loop condition handles removing multiple chunks if they arrived while analysis was running,
#             # until the buffer size is back down to ANALYSIS_WINDOW_SIZE.


#         print(f"WS: analyze_windowed_media finished (instance) for window ending with chunk {window_chunk_number} after {time.time() - start_time:.2f} seconds")


#     def extract_audio(self, media_path):
#         """Extracts audio from a media file using FFmpeg. This is a synchronous operation."""
#         start_time = time.time()
#         base, _ = os.path.splitext(media_path)
#         audio_mp3_path = f"{base}.mp3"
#         # Use list format for command for better security and compatibility
#         # Added -nostats -loglevel 0 to reduce FFmpeg output noise
#         ffmpeg_command = ["ffmpeg", "-y", "-i", media_path, "-vn", "-acodec", "libmp3lame", "-ab", "128k", "-nostats", "-loglevel", "0", audio_mp3_path]
#         print(f"WS: Running FFmpeg command: {' '.join(ffmpeg_command)}")
#         try:
#             # subprocess.Popen and communicate() are blocking calls
#             process = subprocess.Popen(ffmpeg_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
#             stdout, stderr = process.communicate()
#             returncode = process.returncode
#             if returncode == 0:
#                 print(f"WS: Audio extracted to: {audio_mp3_path} after {time.time() - start_time:.2f} seconds")
#                 # Verify file exists and has non-zero size
#                 if os.path.exists(audio_mp3_path) and os.path.getsize(audio_mp3_path) > 0:
#                     return audio_mp3_path
#                 else:
#                      print(f"WS: Extracted audio file is missing or empty: {audio_mp3_path}")
#                      return None

#             else:
#                 error_output = stderr.decode()
#                 print(f"WS: FFmpeg audio extraction error (code {returncode}): {error_output}")
#                 print(f"WS: FFmpeg stdout: {stdout.decode()}")
#                 # Clean up potentially created empty/partial file
#                 if os.path.exists(audio_mp3_path):
#                      try:
#                           os.remove(audio_mp3_path)
#                           print(f"WS: Removed incomplete audio file after FFmpeg error: {audio_mp3_path}")
#                      except Exception as e:
#                           print(f"WS: Error removing incomplete audio file {audio_mp3_path}: {e}")
#                 return None
#         except FileNotFoundError:
#              print(f"WS: FFmpeg command not found. Is FFmpeg installed and in your PATH?")
#              return None
#         except Exception as e:
#              print(f"WS: Error running FFmpeg for audio extraction: {e}")
#              traceback.print_exc()
#              return None

#     def upload_to_s3(self, file_path):
#         """Uploads a local file to S3. This is a synchronous operation."""
#         if s3 is None:
#              print(f"WS: S3 client is not initialized. Cannot upload file: {file_path}.")
#              return None

#         start_time = time.time()
#         file_name = os.path.basename(file_path)
#         folder_path = f"{BASE_FOLDER}{self.session_id}/"
#         s3_key = f"{folder_path}{file_name}"
#         try:
#             # s3.upload_file is a blocking call
#             s3.upload_file(file_path, BUCKET_NAME, s3_key)
#             # Construct S3 URL - using regional endpoint format
#             region_name = os.environ.get('AWS_S3_REGION_NAME', os.environ.get('AWS_REGION', 'us-east-1'))
#             s3_url = f"https://{BUCKET_NAME}.s3.{region_name}.amazonaws.com/{s3_key}"
#             print(f"WS: Uploaded {file_path} to S3 successfully. S3 URL: {s3_url} after {time.time() - start_time:.2f} seconds.")
#             return s3_url
#         except Exception as e:
#             print(f"WS: S3 upload failed for {file_path}: {e}")
#             traceback.print_exc()
#             return None

#     # Decorate with database_sync_to_async to run this synchronous DB method in a thread
#     @database_sync_to_async
#     def _save_chunk_data(self, media_path, s3_url):
#         """Saves the SessionChunk object and maps media path to chunk ID."""
#         start_time = time.time()
#         print(f"WS: _save_chunk_data called for chunk at {media_path} with S3 URL {s3_url} at {start_time}")
#         if not self.session_id:
#             print("WS: Error: Session ID not available, cannot save chunk data.")
#             # Returning None explicitly for clarity with async decorator
#             return None

#         if not s3_url:
#              print(f"WS: Error: S3 URL not provided for {media_path}. Cannot save SessionChunk.")
#              return None # Returning None explicitly

#         try:
#             # Synchronous DB call: Get the session
#             # Because this method is decorated, this runs in a sync context/thread
#             print(f"WS: Attempting to get PracticeSession with id: {self.session_id}")
#             try:
#                  session = PracticeSession.objects.get(id=self.session_id)
#                  print(f"WS: Retrieved PracticeSession: {session.id}, {session.session_name}")
#             except PracticeSession.DoesNotExist:
#                  print(f"WS: Error: PracticeSession with id {self.session_id} not found. Cannot save chunk data.")
#                  return None # Returning None explicitly

#             print(f"WS: S3 URL for SessionChunk: {s3_url}")
#             session_chunk_data = {
#                 'session': session.id, # Link to the session using its ID
#                 'video_file': s3_url # Use the passed S3 URL
#             }
#             print(f"WS: SessionChunk data: {session_chunk_data}")
#             session_chunk_serializer = SessionChunkSerializer(data=session_chunk_data)

#             if session_chunk_serializer.is_valid():
#                 print("WS: SessionChunkSerializer is valid.")
#                 try:
#                     # Synchronous DB call: Save the SessionChunk
#                     session_chunk = session_chunk_serializer.save()
#                     print(f"WS: SessionChunk saved with ID: {session_chunk.id} for media path: {media_path} after {time.time() - start_time:.2f} seconds")
#                     # Store the mapping from temporary media path to the saved chunk's ID
#                     # Accessing self here is fine as it's the consumer instance
#                     self.media_path_to_chunk[media_path] = session_chunk.id
#                     print(f"WS: Added mapping: {media_path} -> {session_chunk.id}")
#                     return session_chunk.id # Return the saved chunk ID

#                 except Exception as save_error:
#                     print(f"WS: Error during SessionChunk save: {save_error}")
#                     traceback.print_exc()
#                     return None # Return None on save error
#             else:
#                 print("WS: Error saving SessionChunk:", session_chunk_serializer.errors)
#                 return None # Return None if serializer is not valid

#         except Exception as e: # Catching other potential exceptions during DB interaction etc.
#             print(f"WS: Error in _save_chunk_data: {e}")
#             traceback.print_exc()
#             return None # Return None on general error
#         finally:
#              print(f"WS: _save_chunk_data finished after {time.time() - start_time:.2f} seconds")


#     # Decorate with database_sync_to_async to run this synchronous DB method in a thread
#     @database_sync_to_async
#     def _save_window_analysis(self, media_path_of_last_chunk_in_window, analysis_result, combined_transcript_text, window_chunk_number):
#         """
#         Saves the window's analysis result to the database, linked to the last chunk in the window.
#         Runs in a separate thread thanks to database_sync_to_async.
#         Handles cases where analysis_result might be an error dictionary.
#         """
#         start_time = time.time()
#         print(f"WS: _save_window_analysis started for window ending with media path: {media_path_of_last_chunk_in_window} (chunk {window_chunk_number}) at {start_time}")
#         if not self.session_id:
#             print("WS: Error: Session ID not available, cannot save window analysis.")
#             return None # Returning None explicitly

#         try:
#             # Get the SessionChunk ID from the map for the *last* chunk in the window
#             # This dictionary access is synchronous and fine within the decorated method.
#             session_chunk_id = self.media_path_to_chunk.get(media_path_of_last_chunk_in_window)

#             print(f"WS: In _save_window_analysis for {media_path_of_last_chunk_in_window} (chunk {window_chunk_number}): session_chunk_id found? {session_chunk_id is not None}. ID: {session_chunk_id}")

#             if session_chunk_id:
#                 print(f"WS: Found SessionChunk ID: {session_chunk_id} for media path: {media_path_of_last_chunk_in_window}")

#                 # Initialize sentiment_data with basic required fields and the transcript
#                 sentiment_data = {
#                     'chunk': session_chunk_id, # Link to the SessionChunk using its ID
#                     'chunk_number': window_chunk_number, # Store the chunk number (this is the last chunk in the window)
#                     'chunk_transcript': combined_transcript_text,
#                 }

#                 # Check if analysis_result is a valid dictionary and not an error structure
#                 if isinstance(analysis_result, dict) and 'error' not in analysis_result:
#                     print("WS: Analysis result is valid, mapping feedback, posture, and scores.")
#                     # Safely access nested dictionaries from analysis_result
#                     feedback_data = analysis_result.get('Feedback', {})
#                     posture_data = analysis_result.get('Posture', {})
#                     scores_data = analysis_result.get('Scores', {})

#                     # Map data from analyze_results
#                     sentiment_data.update({
#                         'audience_emotion': feedback_data.get('Audience Emotion'),
#                         'conviction': feedback_data.get('Conviction'),
#                         'clarity': feedback_data.get('Clarity'),
#                         'impact': feedback_data.get('Impact'),
#                         'brevity': feedback_data.get('Brevity'),
#                         'transformative_potential': feedback_data.get('Transformative Potential'),
#                         'trigger_response': feedback_data.get('Trigger Response'),
#                         'filler_words': feedback_data.get('Filler Words'),
#                         'grammar': feedback_data.get('Grammar'),
#                         'general_feedback_summary': feedback_data.get('General Feedback Summary', ''),

#                         'posture': posture_data.get('Posture'),
#                         'motion': posture_data.get('Motion'),
#                         # Handle potential non-boolean values safely
#                         'gestures': bool(posture_data.get('Gestures', False)) if posture_data.get('Gestures') is not None else False,

#                         'volume': scores_data.get('Volume Score'),
#                         'pitch_variability': scores_data.get('Pitch Variability Score'),
#                         'pace': scores_data.get('Pace Score'),
#                         'pauses': scores_data.get('Pause Score'),
#                     })
#                 elif isinstance(analysis_result, dict) and 'error' in analysis_result:
#                      print(f"WS: Analysis result contained an error: {analysis_result.get('error')}. Saving with error message and null analysis fields.")
#                      # Optionally store the error message in a dedicated field if your model supports it
#                      # For now, we just log it and proceed with saving basic data + transcript

#                 else:
#                      print("WS: Analysis result was not a valid dictionary or was None. Saving with null analysis fields.")
#                      # sentiment_data already only contains basic fields + transcript

#                 print(f"WS: ChunkSentimentAnalysis data (for window, chunk {window_chunk_number}) prepared for saving: {sentiment_data}")

#                 # Use the serializer to validate and prepare data for saving
#                 sentiment_serializer = ChunkSentimentAnalysisSerializer(data=sentiment_data)

#                 # The is_valid() call might trigger DB lookups (e.g., for the 'chunk' foreign key)
#                 # This runs in the sync thread provided by database_sync_to_async
#                 if sentiment_serializer.is_valid():
#                     print(f"WS: ChunkSentimentAnalysisSerializer (for window, chunk {window_chunk_number}) is valid.")
#                     try:
#                         # Synchronous database call to save the sentiment analysis
#                         sentiment_analysis_obj = sentiment_serializer.save()

#                         print(f"WS: Window analysis data saved for chunk ID: {session_chunk_id} (chunk {window_chunk_number}) with sentiment ID: {sentiment_analysis_obj.id} after {time.time() - start_time:.2f} seconds")
#                         return sentiment_analysis_obj.id # Return the saved sentiment ID

#                     except Exception as save_error:
#                         print(f"WS: Error during ChunkSentimentAnalysis save (for window, chunk {window_chunk_number}): {save_error}")
#                         traceback.print_exc() # Print traceback for save errors
#                         return None # Return None on save error
#                 else:
#                     # Print validation errors if serializer is not valid
#                     print(f"WS: Error saving ChunkSentimentAnalysis (chunk {window_chunk_number}):", sentiment_serializer.errors)
#                     return None # Return None if serializer is not valid

#             else:
#                 # This logs if session_chunk_id was None (meaning _save_chunk_data failed or hasn't run for the last chunk in the window)
#                 error_message = f"SessionChunk ID not found in media_path_to_chunk for media path {media_path_of_last_chunk_in_window} during window analysis save for chunk {window_chunk_number}. Analysis will not be saved for this chunk."
#                 print(f"WS: {error_message}")
#                 return None # Return None if chunk ID not found

#         except Exception as e:
#             print(f"WS: Error in _save_window_analysis for media path {media_path_of_last_chunk_in_window} (chunk {window_chunk_number}): {e}")
#             traceback.print_exc() # Print traceback for general _save_window_analysis errors
#             return None # Return None on general error
#         finally:
#              print(f"WS: _save_window_analysis finished for media path {media_path_of_last_chunk_in_window} (chunk {window_chunk_number}) after {time.time() - start_time:.2f} seconds")





#########################################################################################################################
# This version makes database saving and s3 upload happen as background tasks so as not to delay the realtime feedback being sent to the frontedn
#########################################################################################################################

# import asyncio
# import platform

# if platform.system() == 'Windows':
#     asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())


# import json
# import os
# import asyncio
# import tempfile
# import concurrent.futures
# import subprocess
# import boto3
# import openai
# import django
# import time
# import traceback
# import random # Import random for selecting variations

# from base64 import b64decode
# from datetime import timedelta

# from channels.generic.websocket import AsyncWebsocketConsumer
# # Import database_sync_to_async for handling synchronous database operations in async context
# from channels.db import database_sync_to_async
# from .sentiment_analysis import analyze_results, transcribe_audio
# from practice_sessions.models import PracticeSession, SessionChunk, ChunkSentimentAnalysis
# from practice_sessions.serializers import SessionChunkSerializer, ChunkSentimentAnalysisSerializer # PracticeSessionSerializer might not be directly needed here

# # Ensure Django settings are configured
# os.environ.setdefault("DJANGO_SETTINGS_MODULE", "EngageX.settings")
# django.setup()

# # Initialize OpenAI client
# # Ensure OPENAI_API_KEY is set in your environment
# openai.api_key = os.environ.get("OPENAI_API_KEY")
# client = openai.OpenAI() if openai.api_key else None # Initialize client only if API key is available

# # Initialize S3 client
# # Ensure AWS_REGION is set in your environment or settings
# s3 = boto3.client("s3", region_name=os.environ.get('AWS_REGION'))
# BUCKET_NAME = "engagex-user-content-1234" # Replace with your actual S3 bucket name
# BASE_FOLDER = "user-videos/"
# TEMP_MEDIA_ROOT = tempfile.gettempdir() # Use system's temporary directory
# EMOTION_STATIC_FOLDER = "static-videos"  # Top-level folder for static emotion videos

# # Define the rooms the user can choose from. Used for validation.
# POSSIBLE_ROOMS = ['conference_room', 'board_room_1', 'board_room_2']

# # Assume a fixed number of variations for each emotion video (1.mp4 to 5.mp4)
# NUMBER_OF_VARIATIONS = 5

# # Define the window size for analysis (number of chunks)
# ANALYSIS_WINDOW_SIZE = 3 # Keeping the reduced window size from the previous test


# class LiveSessionConsumer(AsyncWebsocketConsumer):
#     def __init__(self, *args, **kwargs):
#         super().__init__(*args, **kwargs)
#         self.session_id = None
#         self.room_name = None # Store the chosen room name
#         self.chunk_counter = 0
#         self.media_buffer = [] # Stores temporary media file paths (full video+audio chunk)
#         self.audio_buffer = {}  # Dictionary to map media_path to temporary audio_path (extracted audio)
#         self.transcript_buffer = {} # Dictionary to map media_path to transcript text (transcript of single chunk)
#         self.media_path_to_chunk = {} # Map temporary media_path to SessionChunk ID (from DB, after saving)
#         # Dictionary to store background tasks for chunk saving, keyed by media_path
#         self.background_chunk_save_tasks = {}


#     async def connect(self):
#         query_string = self.scope['query_string'].decode()
#         query_params = {}
#         if query_string:
#             for param in query_string.split('&'):
#                 try:
#                     key, value = param.split('=', 1)
#                     query_params[key] = value
#                 except ValueError:
#                     print(f"WS: Warning: Could not parse query parameter: {param}")

#         self.session_id = query_params.get('session_id', None)
#         self.room_name = query_params.get('room_name', None) # Get room_name from query params

#         # Validate session_id and room_name
#         if self.session_id and self.room_name in POSSIBLE_ROOMS:
#             print(f"WS: Client connected for Session ID: {self.session_id}, Room: {self.room_name}")
#             await self.accept()
#             await self.send(json.dumps({
#                 "type": "connection_established",
#                 "message": f"Connected to session {self.session_id} in room {self.room_name}"
#             }))
#         else:
#             if not self.session_id:
#                 print("WS: Connection rejected: Missing session_id.")
#             elif self.room_name is None:
#                  print("WS: Connection rejected: Missing room_name.")
#             else: # room_name is provided but not in POSSIBLE_ROOMS
#                  print(f"WS: Connection rejected: Invalid room_name '{self.room_name}'.")

#             await self.close()


#     async def disconnect(self, close_code):
#         print(f"WS: Client disconnected for Session ID: {self.session_id}. Cleaning up...")
#         # Get all paths from buffers and the map keys
#         audio_paths_to_clean = list(self.audio_buffer.values())
#         media_paths_to_clean_from_buffer = list(self.media_buffer)
#         media_paths_to_clean_from_map_keys = list(self.media_path_to_chunk.keys())

#         # Combine all potential paths and remove duplicates
#         all_paths_to_clean = set(audio_paths_to_clean + media_paths_to_clean_from_buffer + media_paths_to_clean_from_map_keys)

#         for file_path in all_paths_to_clean:
#             try:
#                 # Add a small delay before removing to ensure no other process is using it
#                 await asyncio.sleep(0.05)
#                 if file_path and os.path.exists(file_path):
#                     os.remove(file_path)
#                     print(f"WS: Removed temporary file: {file_path}")
#             except Exception as e:
#                 print(f"WS: Error removing file {file_path}: {e}")

#         # Clear buffers and maps
#         self.audio_buffer = {}
#         self.media_buffer = []
#         self.transcript_buffer = {} # Clear the transcript buffer
#         self.media_path_to_chunk = {}
#         self.background_chunk_save_tasks = {} # Clear background task tracking

#         print(f"WS: Session {self.session_id} cleanup complete.")


#     async def receive(self, text_data=None, bytes_data=None):
#         if not self.session_id:
#             print("WS: Error: Session ID not available, cannot process data.")
#             return

#         try:
#             if text_data:
#                 data = json.loads(text_data)
#                 message_type = data.get("type")
#                 if message_type == "media":
#                     self.chunk_counter += 1
#                     media_blob = data.get("data")
#                     if media_blob:
#                         media_bytes = b64decode(media_blob)
#                         # Create a temporary file for the media chunk
#                         media_path = os.path.join(TEMP_MEDIA_ROOT, f"{self.session_id}_{self.chunk_counter}_media.webm")
#                         with open(media_path, "wb") as mf:
#                             mf.write(media_bytes)
#                         print(f"WS: Received media chunk {self.chunk_counter} for Session {self.session_id}. Saved to {media_path}")
#                         self.media_buffer.append(media_path)

#                         # Start processing the media chunk (audio extraction, transcription)
#                         # This part is still awaited to ensure audio/transcript are in buffers
#                         # S3 upload and DB save are initiated as background tasks within process_media_chunk
#                         print(f"WS: Starting processing (audio/transcript) for chunk {self.chunk_counter} and WAITING for it to complete.")
#                         await self.process_media_chunk(media_path)


#                         # Trigger windowed analysis if buffer size is sufficient
#                         # analyze_windowed_media will run concurrently
#                         # It will handle waiting for background chunk save before saving analysis results
#                         if len(self.media_buffer) >= ANALYSIS_WINDOW_SIZE:
#                             # Take the last ANALYSIS_WINDOW_SIZE chunks for the sliding window
#                             window_paths = list(self.media_buffer[-ANALYSIS_WINDOW_SIZE:])
#                             print(f"WS: Triggering windowed analysis for sliding window (chunks ending with {self.chunk_counter})")
#                             # Pass the list of media paths in the window and the latest chunk number
#                             asyncio.create_task(self.analyze_windowed_media(window_paths, self.chunk_counter))

#                     else:
#                         print("WS: Error: Missing 'data' in media message.")
#                 else:
#                     print(f"WS: Received text message of type: {message_type}")
#             elif bytes_data:
#                 print(f"WS: Received binary data of length: {len(bytes_data)}")
#         except json.JSONDecodeError:
#              print(f"WS: Received invalid JSON data: {text_data}")
#         except Exception as e:
#             print(f"WS: Error processing received data: {e}")
#             traceback.print_exc()


#     async def process_media_chunk(self, media_path):
#         """
#         Processes a single media chunk: extracts audio, transcribes,
#         and initiates S3 upload and saves SessionChunk data in the background.
#         This function returns after extracting audio and transcribing,
#         allowing analyze_windowed_media to be triggered sooner.
#         """
#         start_time = time.time()
#         print(f"WS: process_media_chunk started for: {media_path} at {start_time}")
#         audio_path = None
#         chunk_transcript = None # Initialize transcript as None

#         try:
#             # --- Audio Extraction (Blocking, but relatively fast) ---
#             # Use asyncio.to_thread for the blocking audio extraction call
#             # This part is awaited to ensure audio file is ready for transcription
#             extract_future = asyncio.to_thread(self.extract_audio, media_path)
#             audio_path = await extract_future # Await audio extraction

#             # Check if audio extraction was successful and file exists
#             if audio_path and os.path.exists(audio_path):
#                 print(f"WS: Audio extracted and found at: {audio_path}")
#                 self.audio_buffer[media_path] = audio_path # Store the mapping

#                 # --- Transcription of the single chunk (Blocking network I/O) ---
#                 # Use asyncio.to_thread for the blocking transcription call
#                 # This part is awaited to ensure transcript is in buffer for concatenation
#                 if client: # Check if OpenAI client was initialized
#                     print(f"WS: Attempting transcription for single chunk audio: {audio_path}")
#                     transcription_start_time = time.time()
#                     try:
#                         # Assuming transcribe_audio returns the transcript string or None on failure
#                         chunk_transcript = await asyncio.to_thread(transcribe_audio, audio_path)
#                         print(f"WS: Single chunk Transcription Result: {chunk_transcript} after {time.time() - transcription_start_time:.2f} seconds")

#                         # Always store the result, even if it's None or empty string
#                         self.transcript_buffer[media_path] = chunk_transcript
#                         print(f"WS: Stored transcript for {media_path} in buffer.")

#                     except Exception as transcribe_error:
#                         print(f"WS: Error during single chunk transcription for {audio_path}: {transcribe_error}")
#                         traceback.print_exc() # Print traceback for transcription errors
#                         # If transcription fails, chunk_transcript is still None, and None is stored in buffer


#                 else:
#                     print("WS: OpenAI client not initialized (missing API key?). Skipping single chunk transcription.")
#                     self.transcript_buffer[media_path] = None # Store None if transcription is skipped

#             else:
#                 print(f"WS: Audio extraction failed or file not found for {media_path}. Audio path: {audio_path}. Skipping transcription for this chunk.")
#                 self.audio_buffer[media_path] = None # Store None if audio extraction failed
#                 self.transcript_buffer[media_path] = None # Store None if transcription is skipped

#             # --- Initiate S3 Upload and Save SessionChunk data in the BACKGROUND ---
#             # Create a task for S3 upload - this runs in a thread pool
#             s3_upload_task = asyncio.create_task(asyncio.to_thread(self.upload_to_s3, media_path))

#             # Create a task to await the S3 upload and then save the chunk data to the DB
#             # This task runs in the background. Store the task so analyze_windowed_media can potentially wait for it.
#             self.background_chunk_save_tasks[media_path] = asyncio.create_task(self._complete_chunk_save_in_background(media_path, s3_upload_task))


#         except Exception as e:
#             print(f"WS: Error in process_media_chunk for {media_path}: {e}")
#             traceback.print_exc()

#         print(f"WS: process_media_chunk finished (background tasks initiated) for: {media_path} after {time.time() - start_time:.2f} seconds")
#         # This function now returns sooner, allowing the next chunk's processing or analysis trigger to proceed.


#     async def _complete_chunk_save_in_background(self, media_path, s3_upload_task):
#         """Awaits S3 upload and then saves the SessionChunk data."""
#         try:
#             # Wait for S3 upload to complete in its thread
#             s3_url = await s3_upload_task

#             if s3_url:
#                 print(f"WS: S3 upload complete for {media_path}. Attempting to save SessionChunk data in background.")
#                 # Now call the database save method using the obtained S3 URL
#                 # This call is decorated with @database_sync_to_async, running in a separate thread
#                 await self._save_chunk_data(media_path, s3_url)
#                 # The chunk ID will be added to self.media_path_to_chunk inside _save_chunk_data

#             else:
#                 print(f"WS: S3 upload failed for {media_path}. Cannot save SessionChunk data in background.")
#         except Exception as e:
#             print(f"WS: Error in background chunk save for {media_path}: {e}")
#             traceback.print_exc()
#         finally:
#             # Clean up the task tracking entry once this task is done (success or failure)
#             if media_path in self.background_chunk_save_tasks:
#                  del self.background_chunk_save_tasks[media_path]
#                  print(f"WS: Removed background chunk save task tracking for {media_path}")


#     async def analyze_windowed_media(self, window_paths, latest_chunk_number):
#         """
#         Handles concatenation (audio and transcript), analysis, and saving sentiment data for a window.
#         Awaits the background chunk save for the last chunk in the window before saving analysis.
#         """
#         start_time = time.time()
#         last_media_path = window_paths[-1]
#         window_chunk_number = latest_chunk_number # Refers to the number of the last chunk in the window

#         print(f"WS: analyze_windowed_media started for window ending with {last_media_path} (chunk {window_chunk_number}) at {start_time}")

#         combined_audio_path = None # Reintroduce combined audio path
#         combined_transcript_text = ""
#         analysis_result = None # Initialize analysis_result as None
#         window_transcripts_list = [] # List to hold individual transcripts for concatenation

#         # --- No longer explicitly waiting/polling here ---
#         # The save of the ANALYSIS result (_save_window_analysis) will internally
#         # wait for the SessionChunk to exist by looking up the ID in self.media_path_to_chunk.
#         # The @database_sync_to_async decorator and Django ORM handle this wait implicitly
#         # when querying the database for the chunk with the given ID.
#         # If the chunk hasn't been saved by the time _save_window_analysis runs,
#         # the lookup will fail, and _save_window_analysis will log an error and not save the analysis.
#         # This is acceptable - we prioritize sending feedback over saving the analysis data immediately.
#         # The analysis data for that chunk can potentially be re-generated/saved later if needed.


#         try:
#             # --- Retrieve Individual Transcripts and Concatenate ---
#             print(f"WS: Retrieving and concatenating transcripts for window ending with chunk {window_chunk_number}")
#             all_transcripts_found = True
#             for media_path in window_paths:
#                 # Retrieve transcript from the buffer using the media_path
#                 # Use .get() with a default of None to handle missing keys gracefully
#                 transcript = self.transcript_buffer.get(media_path, None)
#                 if transcript is not None: # Check if the value is not None
#                     window_transcripts_list.append(transcript)
#                 else:
#                     # If any transcript is missing (None) or key not in buffer, log a warning
#                     # and add an empty string for concatenation
#                     print(f"WS: Warning: Transcript not found or was None in buffer for chunk media path: {media_path}. Including empty string.")
#                     all_transcripts_found = False
#                     window_transcripts_list.append("")


#             combined_transcript_text = "".join(window_transcripts_list)
#             print(f"WS: Concatenated Transcript for window: '{combined_transcript_text}'")

#             if not all_transcripts_found:
#                  print(f"WS: Analysis for window ending with chunk {window_chunk_number} may be incomplete due to missing transcripts.")


#             # --- FFmpeg Audio Concatenation (Reintroduced) ---
#             # Filter out None audio paths or paths that don't exist on disk from the audio_buffer
#             required_audio_paths = [self.audio_buffer.get(media_path) for media_path in window_paths]
#             valid_audio_paths = [path for path in required_audio_paths if path is not None and os.path.exists(path)]

#             # We only need ANALYSIS_WINDOW_SIZE valid audio paths for concatenation
#             if len(valid_audio_paths) == ANALYSIS_WINDOW_SIZE:
#                  print(f"WS: Valid audio paths for concatenation: {valid_audio_paths}")

#                  combined_audio_path = os.path.join(TEMP_MEDIA_ROOT, f"{self.session_id}_window_{window_chunk_number}.mp3")
#                  concat_command = ["ffmpeg", "-y"]
#                  for audio_path in valid_audio_paths:
#                      concat_command.extend(["-i", audio_path])
#                  # Added -nostats -loglevel 0 to reduce FFmpeg output noise
#                  concat_command.extend(["-filter_complex", f"concat=n={len(valid_audio_paths)}:a=1:v=0", "-acodec", "libmp3lame", "-b:a", "128k", "-nostats", "-loglevel", "0", combined_audio_path])

#                  print(f"WS: Running FFmpeg audio concatenation command: {' '.join(concat_command)}")
#                  process = subprocess.Popen(concat_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
#                  stdout, stderr = await asyncio.to_thread(process.communicate) # Run blocking communicate in a thread
#                  returncode = await asyncio.to_thread(lambda p: p.returncode, process) # Get return code in thread

#                  if returncode != 0:
#                      error_output = stderr.decode()
#                      print(f"WS: FFmpeg audio concatenation error (code {returncode}) for window ending with chunk {window_chunk_number}: {error_output}")
#                      print(f"WS: FFmpeg stdout: {stdout.decode()}")
#                      combined_audio_path = None # Ensure combined_audio_path is None on failure
#                  else:
#                      print(f"WS: Audio files concatenated to: {combined_audio_path}")
#                      # combined_audio_path is set here if successful

#             else:
#                  print(f"WS: Audio not found for all {ANALYSIS_WINDOW_SIZE} chunks in window ending with chunk {latest_chunk_number}. Ready audio paths: {len(valid_audio_paths)}/{ANALYSIS_WINDOW_SIZE}. Skipping audio concatenation for this window instance.")
#                  combined_audio_path = None # Ensure combined_audio_path is None if not all audio paths are valid


#             # --- Analyze results using OpenAI (blocking network I/O) ---
#             # Proceed with analysis if there is a non-empty concatenated transcript and the client is initialized
#             # AND combined_audio_path is available (mimicking old working behavior)
#             # We will get the analysis result if possible, regardless of whether the chunk save is complete yet.
#             if combined_transcript_text.strip() and client and combined_audio_path and os.path.exists(combined_audio_path):
#                 print(f"WS: Running analyze_results for combined transcript and audio.")
#                 analysis_start_time = time.time()
#                 try:
#                     # Using asyncio.to_thread for blocking OpenAI/Analysis call
#                     # Pass the combined_transcript_text, video_path of the first chunk, and the combined_audio_path
#                     # This replicates the call signature from the working version
#                     analysis_result = await asyncio.to_thread(analyze_results, combined_transcript_text, window_paths[0], combined_audio_path)
#                     print(f"WS: Analysis Result: {analysis_result} after {time.time() - analysis_start_time:.2f} seconds")

#                     # Check if the result is a dictionary and contains an error (as implemented previously for robustness)
#                     if analysis_result is None or (isinstance(analysis_result, dict) and 'error' in analysis_result):
#                          error_message = analysis_result.get('error') if isinstance(analysis_result, dict) else 'Unknown analysis error (result is None)'
#                          print(f"WS: Analysis returned an error structure: {error_message}")
#                          # analysis_result variable already holds the error dictionary or None

#                 except Exception as analysis_error:
#                     print(f"WS: Error during analysis (analyze_results) for window ending with chunk {window_chunk_number}: {analysis_error}")
#                     traceback.print_exc() # Print traceback for analysis errors
#                     # Structure the error result consistently as a dictionary with an error key
#                     analysis_result = {'error': str(analysis_error), 'Feedback': {}, 'Posture': {}, 'Scores': {}} # Provide empty nested dicts for serializer safety


#             elif combined_transcript_text.strip() and client:
#                  # Scenario where transcript exists and client is ready, but combined_audio_path is missing/failed
#                  print("WS: Skipping analysis: Combined audio path is missing or failed despite transcript being available.")
#                  # analysis_result remains None
#             elif combined_transcript_text.strip():
#                  # Scenario where transcript exists, but client is not initialized
#                  print("WS: OpenAI client not initialized. Skipping analysis despite having concatenated transcript.")
#                  # analysis_result remains None
#             else:
#                 print(f"WS: Concatenated transcript is empty or only whitespace for window ending with chunk {window_chunk_number}. Skipping analysis.")
#                 # analysis_result remains None


#             # --- Sending updates to the frontend (happens regardless of analysis save status) ---
#             # We send the feedback as soon as analyze_results completes.
#             if analysis_result is not None:
#                 # Send analysis updates to the frontend
#                 # Access Feedback/Audience Emotion safely, accounting for potential error structure
#                 audience_emotion = analysis_result.get('Feedback', {}).get('Audience Emotion')

#                 emotion_s3_url = None
#                 # Only try to construct URL if we have a detected emotion, S3 client, and room name
#                 if audience_emotion and s3 and self.room_name:
#                     try:
#                         # Convert emotion to lowercase for S3 path lookup
#                         lowercase_emotion = audience_emotion.lower()

#                         # Randomly select a variation number between 1 and NUMBER_OF_VARIATIONS
#                         selected_variation = random.randint(1, NUMBER_OF_VARIATIONS)

#                         # Construct the new S3 URL with room and variation
#                         # Ensure AWS_S3_REGION_NAME or AWS_REGION is set
#                         region_name = os.environ.get('AWS_S3_REGION_NAME', os.environ.get('AWS_REGION', 'us-east-1'))
#                         emotion_s3_url = f"https://{BUCKET_NAME}.s3.{region_name}.amazonaws.com/{EMOTION_STATIC_FOLDER}/{self.room_name}/{lowercase_emotion}/{selected_variation}.mp4"

#                         print(f"WS: Sending window emotion update: {audience_emotion}, URL: {emotion_s3_url} (Room: {self.room_name}, Variation: {selected_variation})")
#                         await self.send(json.dumps({
#                             "type": "window_emotion_update",
#                             "emotion": audience_emotion,
#                             "emotion_s3_url": emotion_s3_url
#                         }))
#                     except Exception as e:
#                          print(f"WS: Error constructing or sending emotion URL for emotion '{audience_emotion}': {e}")
#                          traceback.print_exc()

#                 elif audience_emotion:
#                      print("WS: Audience emotion detected but S3 client not configured or room_name is missing, cannot send static video URL.")
#                 else:
#                     # This will also print if analysis_result didn't have a 'Feedback'/'Audience Emotion' structure or if audience_emotion was None/empty
#                     print("WS: No audience emotion detected or analysis structure unexpected. Cannot send static video URL.")


#                 print(f"WS: Sending full analysis update to frontend for window ending with chunk {window_chunk_number}: {analysis_result}")
#                 await self.send(json.dumps({
#                     "type": "full_analysis_update",
#                     "analysis": analysis_result
#                 }))

#                 # --- Initiate Saving Analysis data in the BACKGROUND ---
#                 # We don't await this here. It will run concurrently.
#                 # The _save_window_analysis method needs to be robust enough to handle
#                 # the case where the chunk's DB entry is not yet available (it will wait implicitly via ORM query).
#                 if analysis_result is not None:
#                      print(f"WS: Initiating saving window analysis for chunk {window_chunk_number} in background.")
#                      # Create a task to save the analysis result
#                      asyncio.create_task(self._save_window_analysis(last_media_path, analysis_result, combined_transcript_text, window_chunk_number))
#                 else:
#                      print(f"WS: No analysis result obtained for window ending with chunk {window_chunk_number}. Skipping analysis save.")


#             else:
#                 print(f"WS: No analysis result obtained for window ending with chunk {window_chunk_number}. Skipping analysis save and sending updates.")

#         except Exception as e: # Catch any exceptions during the analyze_windowed_media process itself (excluding analyze_results internal errors already caught)
#             print(f"WS: Error during windowed media analysis ending with chunk {window_chunk_number}: {e}")
#             traceback.print_exc() # Print traceback for general analyze_windowed_media errors
#         finally:
#             # Clean up the temporary combined audio file if it was created
#             if combined_audio_path and os.path.exists(combined_audio_path):
#                 try:
#                     await asyncio.sleep(0.05) # Small delay before removing
#                     os.remove(combined_audio_path)
#                     print(f"WS: Removed temporary combined audio file: {combined_audio_path}")
#                 except Exception as e:
#                     print(f"WS: Error removing temporary combined audio file {combined_audio_path}: {e}")

#             # Clean up the oldest chunk from the buffers after an analysis attempt for a window finishes.
#             # This happens if the media_buffer has reached or exceeded the window size
#             # We only want to remove *one* oldest chunk per analysis trigger
#             # The condition `len(self.media_buffer) > ANALYSIS_WINDOW_SIZE` ensures we maintain a buffer of ANALYSIS_WINDOW_SIZE
#             # Corrected condition back to >= ANALYSIS_WINDOW_SIZE to match original logic and ensure cleanup happens
#             while len(self.media_buffer) >= ANALYSIS_WINDOW_SIZE:
#                  print(f"WS: Cleaning up oldest chunk after analysis. Current buffer size: {len(self.media_buffer)}")
#                  try:
#                      # Remove the oldest media path from the buffer
#                      oldest_media_path = self.media_buffer.pop(0)
#                      print(f"WS: Removed oldest media chunk {oldest_media_path} from buffer.")

#                      # Remove associated entries from other buffers and maps
#                      oldest_audio_path = self.audio_buffer.pop(oldest_media_path, None)
#                      oldest_transcript = self.transcript_buffer.pop(oldest_media_path, None)
#                      oldest_chunk_id = self.media_path_to_chunk.pop(oldest_media_path, None)
#                      # Remove tracking for background save task if it exists
#                      if oldest_media_path in self.background_chunk_save_tasks:
#                           # Cancel the task if it's still running? Or just remove tracking?
#                           # Removing tracking is simpler, the task will finish or error on its own.
#                           # If it errors, it will log from _complete_chunk_save_in_background.
#                           del self.background_chunk_save_tasks[oldest_media_path]
#                           print(f"WS: Removed background chunk save task tracking for oldest media path: {oldest_media_path}")


#                      # Clean up the temporary files associated with this oldest chunk
#                      files_to_remove = [oldest_media_path, oldest_audio_path]
#                      for file_path in files_to_remove:
#                          if file_path and os.path.exists(file_path):
#                              try:
#                                  await asyncio.sleep(0.05) # Small delay before removing
#                                  os.remove(file_path)
#                                  print(f"WS: Removed temporary file: {file_path}")
#                              except Exception as e:
#                                  print(f"WS: Error removing temporary file {file_path}: {e}")
#                          elif file_path:
#                             print(f"WS: File path {file_path} was associated but not found on disk during cleanup.")


#                      if oldest_transcript is not None:
#                           print(f"WS: Removed transcript from buffer for oldest media path: {oldest_media_path}")
#                      else:
#                           print(f"WS: No transcript found in buffer for oldest media path {oldest_media_path} during cleanup.")

#                      if oldest_chunk_id is not None:
#                           print(f"WS: Removed chunk ID mapping from buffer for oldest media path: {oldest_media_path}")
#                      else:
#                           print(f"WS: No chunk ID mapping found in buffer for oldest media path {oldest_media_path} during cleanup.")

#                  except IndexError:
#                       # Should not happen with the while condition, but good practice
#                       print("WS: media_buffer was unexpectedly empty during cleanup in analyze_windowed_media finally.")
#                  except Exception as cleanup_error:
#                       print(f"WS: Error during cleanup of oldest chunk in analyze_windowed_media: {cleanup_error}")
#                       traceback.print_exc()
#                  # Break after cleaning up one oldest chunk to ensure only one is removed per analysis trigger
#                  break


#         print(f"WS: analyze_windowed_media finished (instance) for window ending with chunk {window_chunk_number} after {time.time() - start_time:.2f} seconds")


#     def extract_audio(self, media_path):
#         """Extracts audio from a media file using FFmpeg. This is a synchronous operation."""
#         start_time = time.time()
#         base, _ = os.path.splitext(media_path)
#         audio_mp3_path = f"{base}.mp3"
#         # Use list format for command for better security and compatibility
#         # Added -nostats -loglevel 0 to reduce FFmpeg output noise
#         ffmpeg_command = ["ffmpeg", "-y", "-i", media_path, "-vn", "-acodec", "libmp3lame", "-ab", "128k", "-nostats", "-loglevel", "0", audio_mp3_path]
#         print(f"WS: Running FFmpeg command: {' '.join(ffmpeg_command)}")
#         try:
#             # subprocess.Popen and communicate() are blocking calls
#             process = subprocess.Popen(ffmpeg_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
#             stdout, stderr = process.communicate()
#             returncode = process.returncode
#             if returncode == 0:
#                 print(f"WS: Audio extracted to: {audio_mp3_path} after {time.time() - start_time:.2f} seconds")
#                 # Verify file exists and has non-zero size
#                 if os.path.exists(audio_mp3_path) and os.path.getsize(audio_mp3_path) > 0:
#                     return audio_mp3_path
#                 else:
#                      print(f"WS: Extracted audio file is missing or empty: {audio_mp3_path}")
#                      return None

#             else:
#                 error_output = stderr.decode()
#                 print(f"WS: FFmpeg audio extraction error (code {returncode}): {error_output}")
#                 print(f"WS: FFmpeg stdout: {stdout.decode()}")
#                 # Clean up potentially created empty/partial file
#                 if os.path.exists(audio_mp3_path):
#                      try:
#                           os.remove(audio_mp3_path)
#                           print(f"WS: Removed incomplete audio file after FFmpeg error: {audio_mp3_path}")
#                      except Exception as e:
#                           print(f"WS: Error removing incomplete audio file {audio_mp3_path}: {e}")
#                 return None
#         except FileNotFoundError:
#              print(f"WS: FFmpeg command not found. Is FFmpeg installed and in your PATH?")
#              return None
#         except Exception as e:
#              print(f"WS: Error running FFmpeg for audio extraction: {e}")
#              traceback.print_exc()
#              return None

#     def upload_to_s3(self, file_path):
#         """Uploads a local file to S3. This is a synchronous operation."""
#         if s3 is None:
#              print(f"WS: S3 client is not initialized. Cannot upload file: {file_path}.")
#              return None

#         start_time = time.time()
#         file_name = os.path.basename(file_path)
#         folder_path = f"{BASE_FOLDER}{self.session_id}/"
#         s3_key = f"{folder_path}{file_name}"
#         try:
#             # s3.upload_file is a blocking call
#             s3.upload_file(file_path, BUCKET_NAME, s3_key)
#             # Construct S3 URL - using regional endpoint format
#             region_name = os.environ.get('AWS_S3_REGION_NAME', os.environ.get('AWS_REGION', 'us-east-1'))
#             s3_url = f"https://{BUCKET_NAME}.s3.{region_name}.amazonaws.com/{s3_key}"
#             print(f"WS: Uploaded {file_path} to S3 successfully. S3 URL: {s3_url} after {time.time() - start_time:.2f} seconds.")
#             return s3_url
#         except Exception as e:
#             print(f"WS: S3 upload failed for {file_path}: {e}")
#             traceback.print_exc()
#             return None

#     # Decorate with database_sync_to_async to run this synchronous DB method in a thread
#     @database_sync_to_async
#     def _save_chunk_data(self, media_path, s3_url):
#         """Saves the SessionChunk object and maps media path to chunk ID."""
#         start_time = time.time()
#         print(f"WS: _save_chunk_data called for chunk at {media_path} with S3 URL {s3_url} at {start_time}")
#         if not self.session_id:
#             print("WS: Error: Session ID not available, cannot save chunk data.")
#             # Returning None explicitly for clarity with async decorator
#             return None

#         if not s3_url:
#              print(f"WS: Error: S3 URL not provided for {media_path}. Cannot save SessionChunk.")
#              return None # Returning None explicitly

#         try:
#             # Synchronous DB call: Get the session
#             # Because this method is decorated, this runs in a sync context/thread
#             print(f"WS: Attempting to get PracticeSession with id: {self.session_id}")
#             try:
#                  session = PracticeSession.objects.get(id=self.session_id)
#                  print(f"WS: Retrieved PracticeSession: {session.id}, {session.session_name}")
#             except PracticeSession.DoesNotExist:
#                  print(f"WS: Error: PracticeSession with id {self.session_id} not found. Cannot save chunk data.")
#                  return None # Returning None explicitly

#             print(f"WS: S3 URL for SessionChunk: {s3_url}")
#             session_chunk_data = {
#                 'session': session.id, # Link to the session using its ID
#                 'video_file': s3_url # Use the passed S3 URL
#             }
#             print(f"WS: SessionChunk data: {session_chunk_data}")
#             session_chunk_serializer = SessionChunkSerializer(data=session_chunk_data)

#             if session_chunk_serializer.is_valid():
#                 print("WS: SessionChunkSerializer is valid.")
#                 try:
#                     # Synchronous DB call: Save the SessionChunk
#                     session_chunk = session_chunk_serializer.save()
#                     print(f"WS: SessionChunk saved with ID: {session_chunk.id} for media path: {media_path} after {time.time() - start_time:.2f} seconds")
#                     # Store the mapping from temporary media path to the saved chunk's ID
#                     # Accessing self here is fine as it's the consumer instance
#                     self.media_path_to_chunk[media_path] = session_chunk.id
#                     print(f"WS: Added mapping: {media_path} -> {session_chunk.id}")
#                     return session_chunk.id # Return the saved chunk ID

#                 except Exception as save_error:
#                     print(f"WS: Error during SessionChunk save: {save_error}")
#                     traceback.print_exc()
#                     return None # Return None on save error
#             else:
#                 print("WS: Error saving SessionChunk:", session_chunk_serializer.errors)
#                 return None # Return None if serializer is not valid

#         except Exception as e: # Catching other potential exceptions during DB interaction etc.
#             print(f"WS: Error in _save_chunk_data: {e}")
#             traceback.print_exc()
#             return None # Return None on general error
#         finally:
#              print(f"WS: _save_chunk_data finished after {time.time() - start_time:.2f} seconds")


#     # Decorate with database_sync_to_async to run this synchronous DB method in a thread
#     @database_sync_to_async
#     def _save_window_analysis(self, media_path_of_last_chunk_in_window, analysis_result, combined_transcript_text, window_chunk_number):
#         """
#         Saves the window's analysis result to the database, linked to the last chunk in the window.
#         Runs in a separate thread thanks to database_sync_to_async.
#         Handles cases where analysis_result might be an error dictionary.
#         It will implicitly wait for the SessionChunk to exist via the ORM query for the chunk ID.
#         """
#         start_time = time.time()
#         print(f"WS: _save_window_analysis started for window ending with media path: {media_path_of_last_chunk_in_window} (chunk {window_chunk_number}) at {start_time}")
#         if not self.session_id:
#             print("WS: Error: Session ID not available, cannot save window analysis.")
#             return None # Returning None explicitly

#         try:
#             # Get the SessionChunk ID from the map for the *last* chunk in the window
#             # This dictionary access is synchronous and fine within the decorated method.
#             # The ORM query inside the serializer's is_valid() or save() method will
#             # handle waiting for the chunk to exist in the DB.
#             session_chunk_id = self.media_path_to_chunk.get(media_path_of_last_chunk_in_window)

#             print(f"WS: In _save_window_analysis for {media_path_of_last_chunk_in_window} (chunk {window_chunk_number}): session_chunk_id found? {session_chunk_id is not None}. ID: {session_chunk_id}")

#             if session_chunk_id:
#                 print(f"WS: Found SessionChunk ID: {session_chunk_id} for media path: {media_path_of_last_chunk_in_window}")

#                 # Initialize sentiment_data with basic required fields and the transcript
#                 sentiment_data = {
#                     'chunk': session_chunk_id, # Link to the SessionChunk using its ID
#                     'chunk_number': window_chunk_number, # Store the chunk number (this is the last chunk in the window)
#                     'chunk_transcript': combined_transcript_text,
#                 }

#                 # Check if analysis_result is a valid dictionary and not an error structure
#                 if isinstance(analysis_result, dict) and 'error' not in analysis_result:
#                     print("WS: Analysis result is valid, mapping feedback, posture, and scores.")
#                     # Safely access nested dictionaries from analysis_result
#                     feedback_data = analysis_result.get('Feedback', {})
#                     posture_data = analysis_result.get('Posture', {})
#                     scores_data = analysis_result.get('Scores', {})

#                     # Map data from analyze_results
#                     sentiment_data.update({
#                         'audience_emotion': feedback_data.get('Audience Emotion'),
#                         'conviction': feedback_data.get('Conviction'),
#                         'clarity': feedback_data.get('Clarity'),
#                         'impact': feedback_data.get('Impact'),
#                         'brevity': feedback_data.get('Brevity'),
#                         'transformative_potential': feedback_data.get('Transformative Potential'),
#                         'trigger_response': feedback_data.get('Trigger Response'),
#                         'filler_words': feedback_data.get('Filler Words'),
#                         'grammar': feedback_data.get('Grammar'),
#                         'general_feedback_summary': feedback_data.get('General Feedback Summary', ''),

#                         'posture': posture_data.get('Posture'),
#                         'motion': posture_data.get('Motion'),
#                         # Handle potential non-boolean values safely
#                         'gestures': bool(posture_data.get('Gestures', False)) if posture_data.get('Gestures') is not None else False,

#                         'volume': scores_data.get('Volume Score'),
#                         'pitch_variability': scores_data.get('Pitch Variability Score'),
#                         'pace': scores_data.get('Pace Score'),
#                         'pauses': scores_data.get('Pause Score'),
#                     })
#                 elif isinstance(analysis_result, dict) and 'error' in analysis_result:
#                      print(f"WS: Analysis result contained an error: {analysis_result.get('error')}. Saving with error message and null analysis fields.")
#                      # Optionally store the error message in a dedicated field if your model supports it
#                      # For now, we just log it and proceed with saving basic data + transcript

#                 else:
#                      print("WS: Analysis result was not a valid dictionary or was None. Saving with null analysis fields.")
#                      # sentiment_data already only contains basic fields + transcript

#                 print(f"WS: ChunkSentimentAnalysis data (for window, chunk {window_chunk_number}) prepared for saving: {sentiment_data}")

#                 # Use the serializer to validate and prepare data for saving
#                 sentiment_serializer = ChunkSentimentAnalysisSerializer(data=sentiment_data)

#                 # The is_valid() call might trigger DB lookups (e.g., for the 'chunk' foreign key)
#                 # This runs in the sync thread provided by database_sync_to_async.
#                 # If the chunk corresponding to session_chunk_id does not yet exist,
#                 # this lookup will wait or fail depending on DB/ORM behavior.
#                 # With database_sync_to_async and typical ORM, it might wait.
#                 if sentiment_serializer.is_valid():
#                     print(f"WS: ChunkSentimentAnalysisSerializer (for window, chunk {window_chunk_number}) is valid.")
#                     try:
#                         # Synchronous database call to save the sentiment analysis
#                         sentiment_analysis_obj = sentiment_serializer.save()

#                         print(f"WS: Window analysis data saved for chunk ID: {session_chunk_id} (chunk {window_chunk_number}) with sentiment ID: {sentiment_analysis_obj.id} after {time.time() - start_time:.2f} seconds")
#                         return sentiment_analysis_obj.id # Return the saved sentiment ID

#                     except Exception as save_error:
#                         print(f"WS: Error during ChunkSentimentAnalysis save (for window, chunk {window_chunk_number}): {save_error}")
#                         traceback.print_exc() # Print traceback for save errors
#                         return None # Return None on save error
#                 else:
#                     # Print validation errors if serializer is not valid
#                     print(f"WS: Error saving ChunkSentimentAnalysis (chunk {window_chunk_number}):", sentiment_serializer.errors)
#                     return None # Return None if serializer is not valid

#             else:
#                 # This logs if session_chunk_id was None (meaning _save_chunk_data failed or hasn't run for the last chunk in the window)
#                 error_message = f"SessionChunk ID not found in media_path_to_chunk for media path {media_path_of_last_chunk_in_window} during window analysis save for chunk {window_chunk_number}. Analysis will not be saved for this chunk."
#                 print(f"WS: {error_message}")
#                 return None # Return None if chunk ID not found

#         except Exception as e:
#             print(f"WS: Error in _save_window_analysis for media path {media_path_of_last_chunk_in_window} (chunk {window_chunk_number}): {e}")
#             traceback.print_exc() # Print traceback for general _save_window_analysis errors
#             return None # Return None on general error
#         finally:
#              print(f"WS: _save_window_analysis finished for media path {media_path_of_last_chunk_in_window} (chunk {window_chunk_number}) after {time.time() - start_time:.2f} seconds")



#################################################################################################################
# This version uses the original mechanism but now runs the database querying and s3 upload in the background
#################################################################################################################


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
import random

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

# Define the rooms the user can choose from. Used for validation.
POSSIBLE_ROOMS = ['conference_room', 'board_room_1', 'board_room_2']
NUMBER_OF_VARIATIONS = 5
ANALYSIS_WINDOW_SIZE = 3


class LiveSessionConsumer(AsyncWebsocketConsumer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.session_id = None
        self.room_name = None
        self.chunk_counter = 0
        self.media_buffer = []  # Stores temporary media file paths
        self.audio_buffer = {}  # Maps media_path to temporary audio_path
        self.media_path_to_chunk = {}  # Maps temporary media_path to SessionChunk ID
        self.background_tasks = {}  # Tracks all background tasks by their identifiers
        

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
        self.room_name = query_params.get('room_name', None)

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
            else:
                 print(f"WS: Connection rejected: Invalid room_name '{self.room_name}'.")

            await self.close()


    async def disconnect(self, close_code):
        print(f"WS: Client disconnected for Session ID: {self.session_id}. Cleaning up...")
        
        # Cancel all pending background tasks
        for task_id, task in list(self.background_tasks.items()):
            if not task.done():
                task.cancel()
                print(f"WS: Cancelled background task: {task_id}")
        
        # Clean up temporary files
        audio_paths_to_clean = list(self.audio_buffer.values())
        media_paths_to_clean = list(self.media_buffer) + list(self.media_path_to_chunk.keys())
        
        # Combine all potential paths and remove duplicates
        all_paths_to_clean = set(audio_paths_to_clean + media_paths_to_clean)

        for file_path in all_paths_to_clean:
            try:
                await asyncio.sleep(0.05)
                if file_path and os.path.exists(file_path):
                    os.remove(file_path)
                    print(f"WS: Removed temporary file: {file_path}")
            except Exception as e:
                print(f"WS: Error removing file {file_path}: {e}")

        # Clear buffers and maps
        self.audio_buffer = {}
        self.media_buffer = []
        self.media_path_to_chunk = {}
        self.background_tasks = {}

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
                        self.media_buffer.append(media_path)

                        # Start processing the media chunk (audio extraction only)
                        # This part is awaited to ensure audio is extracted before window analysis
                        print(f"WS: Starting audio extraction for chunk {self.chunk_counter}")
                        await self.extract_audio_async(media_path)

                        # Start S3 upload and DB save as background tasks
                        task_id = f"save_chunk_{self.chunk_counter}"
                        self.background_tasks[task_id] = asyncio.create_task(
                            self.save_chunk_in_background(media_path, self.chunk_counter)
                        )

                        # Trigger windowed analysis if buffer size is sufficient
                        if len(self.media_buffer) >= ANALYSIS_WINDOW_SIZE:
                            # Take the last ANALYSIS_WINDOW_SIZE chunks for the sliding window
                            window_paths = list(self.media_buffer[-ANALYSIS_WINDOW_SIZE:])
                            print(f"WS: Triggering windowed analysis for chunks ending with {self.chunk_counter}")
                            # Create a task for windowed analysis
                            window_task_id = f"analyze_window_{self.chunk_counter}"
                            self.background_tasks[window_task_id] = asyncio.create_task(
                                self.analyze_windowed_media(window_paths, self.chunk_counter)
                            )

                            # Clean up the oldest chunk if we've exceeded the window size
                            if len(self.media_buffer) > ANALYSIS_WINDOW_SIZE:
                                await self.cleanup_oldest_chunk()

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


    async def extract_audio_async(self, media_path):
        """Extract audio from a media file asynchronously."""
        try:
            audio_path = await asyncio.to_thread(self.extract_audio, media_path)
            if audio_path:
                self.audio_buffer[media_path] = audio_path
                print(f"WS: Audio extracted and stored in buffer for {media_path}")
            else:
                print(f"WS: Audio extraction failed for {media_path}")
                self.audio_buffer[media_path] = None
        except Exception as e:
            print(f"WS: Error in extract_audio_async for {media_path}: {e}")
            traceback.print_exc()
            self.audio_buffer[media_path] = None


    async def save_chunk_in_background(self, media_path, chunk_number):
        """Handles S3 upload and database save for a chunk in the background."""
        try:
            print(f"WS: Starting background S3 upload for chunk {chunk_number}")
            s3_url = await asyncio.to_thread(self.upload_to_s3, media_path)
            
            if s3_url:
                print(f"WS: S3 upload complete for chunk {chunk_number}, saving to database")
                chunk_id = await self._save_chunk_data(media_path, s3_url)
                if chunk_id:
                    print(f"WS: Successfully saved chunk {chunk_number} to database with ID {chunk_id}")
                else:
                    print(f"WS: Failed to save chunk {chunk_number} to database")
            else:
                print(f"WS: S3 upload failed for chunk {chunk_number}")
        except Exception as e:
            print(f"WS: Error in save_chunk_in_background for chunk {chunk_number}: {e}")
            traceback.print_exc()
        finally:
            # Remove this task from tracking dictionary when complete
            task_id = f"save_chunk_{chunk_number}"
            if task_id in self.background_tasks:
                del self.background_tasks[task_id]


    async def analyze_windowed_media(self, window_paths, latest_chunk_number):
        """
        Analyzes a window of media: concatenates audio, transcribes the entire window,
        analyzes results, and sends updates to the frontend immediately.
        """
        start_time = time.time()
        print(f"WS: analyze_windowed_media started for window ending with chunk {latest_chunk_number}")
        
        combined_audio_path = None
        analysis_result = None
        
        try:
            # --- FFmpeg Audio Concatenation ---
            # Filter out None audio paths or paths that don't exist
            valid_audio_paths = [
                self.audio_buffer.get(media_path) 
                for media_path in window_paths 
                if self.audio_buffer.get(media_path) and os.path.exists(self.audio_buffer.get(media_path))
            ]
            
            if len(valid_audio_paths) == ANALYSIS_WINDOW_SIZE:
                combined_audio_path = os.path.join(TEMP_MEDIA_ROOT, f"{self.session_id}_window_{latest_chunk_number}.mp3")
                concat_command = ["ffmpeg", "-y"]
                for audio_path in valid_audio_paths:
                    concat_command.extend(["-i", audio_path])
                concat_command.extend([
                    "-filter_complex", f"concat=n={len(valid_audio_paths)}:a=1:v=0", 
                    "-acodec", "libmp3lame", "-b:a", "128k", "-nostats", "-loglevel", "0", 
                    combined_audio_path
                ])
                
                print(f"WS: Running FFmpeg audio concatenation command")
                process = subprocess.Popen(concat_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                stdout, stderr = await asyncio.to_thread(process.communicate)
                returncode = process.returncode
                
                if returncode != 0:
                    error_output = stderr.decode()
                    print(f"WS: FFmpeg audio concatenation error (code {returncode}): {error_output}")
                    combined_audio_path = None
                else:
                    print(f"WS: Audio files concatenated to: {combined_audio_path}")
            else:
                print(f"WS: Not enough valid audio files for concatenation. Found {len(valid_audio_paths)}/{ANALYSIS_WINDOW_SIZE}")
                return
            
            # --- Transcribe the entire window ---
            if combined_audio_path and os.path.exists(combined_audio_path) and client:
                print(f"WS: Transcribing complete window audio")
                transcript = await asyncio.to_thread(transcribe_audio, combined_audio_path)
                
                if transcript:
                    print(f"WS: Window transcript: '{transcript}'")
                    
                    # --- Analyze results using the window transcript and audio ---
                    print(f"WS: Running analysis on window transcript and audio")
                    analysis_result = await asyncio.to_thread(
                        analyze_results, transcript, window_paths[0], combined_audio_path
                    )
                    
                    # --- Send updates to the frontend IMMEDIATELY ---
                    if analysis_result and isinstance(analysis_result, dict) and 'error' not in analysis_result:
                        # Send emotion update if available
                        audience_emotion = analysis_result.get('Feedback', {}).get('Audience Emotion')
                        if audience_emotion and s3 and self.room_name:
                            try:
                                lowercase_emotion = audience_emotion.lower()
                                selected_variation = random.randint(1, NUMBER_OF_VARIATIONS)
                                region_name = os.environ.get('AWS_S3_REGION_NAME', os.environ.get('AWS_REGION', 'us-east-1'))
                                emotion_s3_url = f"https://{BUCKET_NAME}.s3.{region_name}.amazonaws.com/{EMOTION_STATIC_FOLDER}/{self.room_name}/{lowercase_emotion}/{selected_variation}.mp4"
                                
                                print(f"WS: Sending emotion update: {audience_emotion}, URL: {emotion_s3_url}")
                                await self.send(json.dumps({
                                    "type": "window_emotion_update",
                                    "emotion": audience_emotion,
                                    "emotion_s3_url": emotion_s3_url
                                }))
                            except Exception as e:
                                print(f"WS: Error constructing emotion URL: {e}")
                        
                        # Send full analysis to frontend
                        print(f"WS: Sending full analysis update to frontend")
                        await self.send(json.dumps({
                            "type": "full_analysis_update",
                            "analysis": analysis_result
                        }))
                        
                        # --- Save analysis results in the background ---
                        # Create a background task to save analysis results
                        analysis_task_id = f"save_analysis_{latest_chunk_number}"
                        self.background_tasks[analysis_task_id] = asyncio.create_task(
                            self.save_analysis_in_background(
                                window_paths[-1], analysis_result, transcript, latest_chunk_number
                            )
                        )
                    elif analysis_result:
                        print(f"WS: Analysis returned an error: {analysis_result.get('error', 'Unknown error')}")
                    else:
                        print(f"WS: Analysis failed to produce results")
                else:
                    print(f"WS: Transcription failed for window")
            else:
                print(f"WS: Cannot perform transcription - missing audio file or OpenAI client")
                
        except Exception as e:
            print(f"WS: Error in analyze_windowed_media: {e}")
            traceback.print_exc()
        finally:
            # Clean up the temporary combined audio file
            if combined_audio_path and os.path.exists(combined_audio_path):
                try:
                    await asyncio.sleep(0.05)
                    os.remove(combined_audio_path)
                    print(f"WS: Removed temporary combined audio file: {combined_audio_path}")
                except Exception as e:
                    print(f"WS: Error removing temporary file: {e}")
            
            # Remove task from tracking dictionary
            window_task_id = f"analyze_window_{latest_chunk_number}"
            if window_task_id in self.background_tasks:
                del self.background_tasks[window_task_id]
            
            print(f"WS: analyze_windowed_media finished for window ending with chunk {latest_chunk_number} after {time.time() - start_time:.2f} seconds")


    async def save_analysis_in_background(self, last_media_path, analysis_result, transcript, window_chunk_number):
        """Saves the analysis results to the database in the background."""
        try:
            print(f"WS: Saving analysis for window ending with chunk {window_chunk_number}")
            
            # We need to wait until the chunk is saved in the database
            # Try a few times with exponential backoff
            max_attempts = 5
            for attempt in range(max_attempts):
                # Check if we have a chunk ID for this media path
                chunk_id = self.media_path_to_chunk.get(last_media_path)
                
                if chunk_id:
                    print(f"WS: Found chunk ID {chunk_id} for last media path, saving analysis")
                    await self._save_window_analysis(last_media_path, analysis_result, transcript, window_chunk_number)
                    break
                else:
                    # Wait with exponential backoff before retrying
                    wait_time = 0.5 * (2 ** attempt)
                    print(f"WS: Chunk ID not found for {last_media_path}, waiting {wait_time}s before retry {attempt+1}/{max_attempts}")
                    await asyncio.sleep(wait_time)
            else:
                print(f"WS: Failed to find chunk ID for {last_media_path} after {max_attempts} attempts, cannot save analysis")
        except Exception as e:
            print(f"WS: Error in save_analysis_in_background: {e}")
            traceback.print_exc()
        finally:
            # Remove task from tracking
            analysis_task_id = f"save_analysis_{window_chunk_number}"
            if analysis_task_id in self.background_tasks:
                del self.background_tasks[analysis_task_id]


    async def cleanup_oldest_chunk(self):
        """Cleans up the oldest chunk from buffers and temporary files."""
        try:
            oldest_media_path = self.media_buffer.pop(0)
            print(f"WS: Cleaning up oldest chunk: {oldest_media_path}")
            
            # Remove associated audio file
            oldest_audio_path = self.audio_buffer.pop(oldest_media_path, None)
            
            # Clean up temporary files
            for file_path in [oldest_media_path, oldest_audio_path]:
                if file_path and os.path.exists(file_path):
                    try:
                        await asyncio.sleep(0.05)
                        os.remove(file_path)
                        print(f"WS: Removed temporary file: {file_path}")
                    except Exception as e:
                        print(f"WS: Error removing file {file_path}: {e}")
            
            # Remove from chunk mapping if it exists
            if oldest_media_path in self.media_path_to_chunk:
                del self.media_path_to_chunk[oldest_media_path]
                
        except IndexError:
            print("WS: media_buffer was unexpectedly empty during cleanup")
        except Exception as e:
            print(f"WS: Error during cleanup_oldest_chunk: {e}")
            traceback.print_exc()


    def extract_audio(self, media_path):
        """Extracts audio from a media file using FFmpeg. This is a synchronous operation."""
        start_time = time.time()
        base, _ = os.path.splitext(media_path)
        audio_mp3_path = f"{base}.mp3"
        
        ffmpeg_command = ["ffmpeg", "-y", "-i", media_path, "-vn", "-acodec", "libmp3lame", "-ab", "128k", "-nostats", "-loglevel", "0", audio_mp3_path]
        print(f"WS: Running FFmpeg command for audio extraction")
        
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
                    return None
            else:
                error_output = stderr.decode()
                print(f"WS: FFmpeg audio extraction error (code {returncode}): {error_output}")
                
                if os.path.exists(audio_mp3_path):
                    os.remove(audio_mp3_path)
                    print(f"WS: Removed incomplete audio file after FFmpeg error: {audio_mp3_path}")
                    
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
            s3.upload_file(file_path, BUCKET_NAME, s3_key)
            region_name = os.environ.get('AWS_S3_REGION_NAME', os.environ.get('AWS_REGION', 'us-east-1'))
            s3_url = f"https://{BUCKET_NAME}.s3.{region_name}.amazonaws.com/{s3_key}"
            print(f"WS: Uploaded {file_path} to S3 successfully. S3 URL: {s3_url} after {time.time() - start_time:.2f} seconds.")
            return s3_url
        except Exception as e:
            print(f"WS: S3 upload failed for {file_path}: {e}")
            traceback.print_exc()
            return None


    @database_sync_to_async
    def _save_chunk_data(self, media_path, s3_url):
        """Saves the SessionChunk object and maps media path to chunk ID."""
        start_time = time.time()
        print(f"WS: _save_chunk_data called with S3 URL {s3_url}")
        
        if not self.session_id or not s3_url:
            print("WS: Error: Missing session ID or S3 URL, cannot save chunk data.")
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
                chunk_id = session_chunk.id
                print(f"WS: SessionChunk saved with ID: {chunk_id} after {time.time() - start_time:.2f} seconds")
                
                # Store the mapping from media path to chunk ID
                self.media_path_to_chunk[media_path] = chunk_id
                print(f"WS: Added mapping: {media_path} -> {chunk_id}")
                return chunk_id
            else:
                print("WS: Error saving SessionChunk:", session_chunk_serializer.errors)
                return None
        except PracticeSession.DoesNotExist:
            print(f"WS: Error: PracticeSession with id {self.session_id} not found.")
            return None
        except Exception as e:
            print(f"WS: Error in _save_chunk_data: {e}")
            traceback.print_exc()
            return None


    @database_sync_to_async
    def _save_window_analysis(self, media_path, analysis_result, transcript, window_chunk_number):
        """Saves the window's analysis result to the database."""
        start_time = time.time()
        print(f"WS: _save_window_analysis started for window ending with chunk {window_chunk_number}")
        
        if not self.session_id:
            print("WS: Error: Session ID not available, cannot save window analysis.")
            return None

        try:
            # Get the SessionChunk ID for the last chunk in the window
            session_chunk_id = self.media_path_to_chunk.get(media_path)
            if not session_chunk_id:
                print(f"WS: Error: SessionChunk ID not found for {media_path}")
                return None
                
            # Initialize sentiment data with basic fields
            sentiment_data = {
                'chunk': session_chunk_id,
                'chunk_number': window_chunk_number,
                'chunk_transcript': transcript,
            }
            
            # Add analysis data if available
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
                sentiment_analysis = sentiment_serializer.save()
                print(f"WS: Analysis saved with ID: {sentiment_analysis.id} after {time.time() - start_time:.2f} seconds")
                return sentiment_analysis.id
            else:
                print("WS: Error saving ChunkSentimentAnalysis:", sentiment_serializer.errors)
                return None
        except Exception as e:
            print(f"WS: Error in _save_window_analysis: {e}")
            traceback.print_exc()
            return None