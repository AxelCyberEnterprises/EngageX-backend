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
# EMOTION_FOLDER = "static-videos"  # Folder in S3 bucket containing emotion files

# class LiveSessionConsumer(AsyncWebsocketConsumer):
#     def __init__(self, *args, **kwargs):
#         super().__init__(*args, **kwargs)
#         self.session_id = None
#         self.chunk_counter = 0
#         self.media_buffer = []
#         self.audio_buffer = {}  # Dictionary to map media_path to audio_path
#         self.chunk_paths = []
#         self.media_path_to_chunk = {}

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
#         if self.session_id:
#             print(f"WS: Client connected for Session ID: {self.session_id}")
#             await self.accept()
#             await self.send(json.dumps({
#                 "type": "connection_established",
#                 "message": f"Connected to session {self.session_id}"
#             }))
#         else:
#             print("WS: Connection rejected: Missing session_id.")
#             await self.close()

#     async def disconnect(self, close_code):
#         print(f"WS: Client disconnected for Session ID: {self.session_id}. Cleaning up...")
#         for media_path, audio_path in self.audio_buffer.items():
#             try:
#                 if os.path.exists(audio_path):
#                     os.remove(audio_path)
#                     print(f"WS: Removed temporary audio file: {audio_path}")
#             except Exception as e:
#                 print(f"WS: Error removing audio file: {e}")
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
#                         await self.send(json.dumps({
#                             "status": "received",
#                             "session_id": self.session_id,
#                             "chunk_number": self.chunk_counter,
#                             "media_type": "media"
#                         }))
#                         print(f"WS: Starting process_media_chunk for chunk {self.chunk_counter}")
#                         process_task = asyncio.create_task(self.process_media_chunk(media_path))
#                         await process_task # Wait for the chunk processing to complete

#                         # Trigger windowed analysis based on buffer size
#                         if len(self.media_buffer) == 4:
#                             window_paths = list(self.media_buffer)
#                             print(f"WS: Triggering windowed analysis for initial window (chunks 1 to 4)")
#                             asyncio.create_task(self.analyze_windowed_media(window_paths))
#                         elif len(self.media_buffer) > 4:
#                             self.media_buffer.pop(0)
#                             window_paths = self.media_buffer[-4:]
#                             print(f"WS: Triggering windowed analysis for sliding window (chunks {self.chunk_counter - 3} to {self.chunk_counter})")
#                             asyncio.create_task(self.analyze_windowed_media(window_paths))
#                     else:
#                         print("WS: Error: Missing 'data' in media message.")
#                 else:
#                     print(f"WS: Received text message of type: {message_type}")
#             elif bytes_data:
#                 print(f"WS: Received binary data of length: {len(bytes_data)}")
#         except Exception as e:
#             print(f"WS: Error processing received data: {e}")

#     async def process_media_chunk(self, media_path):
#         start_time = time.time()
#         print(f"WS: process_media_chunk started for: {media_path} at {start_time}")
#         audio_path = None
#         try:
#             with concurrent.futures.ThreadPoolExecutor() as executor:
#                 futures = []
#                 print(f"WS: Submitting upload_to_s3 for: {media_path}")
#                 futures.append(executor.submit(self.upload_to_s3, media_path))
#                 print(f"WS: Calling extract_audio for: {media_path}")
#                 audio_path = await asyncio.to_thread(self.extract_audio, media_path)
#                 if audio_path:
#                     print(f"WS: Audio extracted to: {audio_path} after {time.time() - start_time:.2f} seconds")
#                     self.audio_buffer[media_path] = audio_path # Store the mapping
#                 else:
#                     print("WS: Audio extraction failed.")

#                 for future in concurrent.futures.as_completed(futures):
#                     try:
#                         future.result()
#                     except Exception as e:
#                         print(f"WS: Task failed in process_media_chunk: {e}")

#             # Save the chunk data. This should happen for every chunk.
#             await asyncio.to_thread(self._save_chunk_data, media_path, None, None)
#         except Exception as e:
#             print(f"WS: Error in process_media_chunk: {e}")
#         print(f"WS: process_media_chunk finished for: {media_path} after {time.time() - start_time:.2f} seconds")

#     async def analyze_windowed_media(self, window_paths):
#         if len(window_paths) != 4:
#             print(f"WS: analyze_windowed_media called with {len(window_paths)} paths, expected 4.")
#             return

#         start_time = time.time()
#         print(f"WS: analyze_windowed_media started for window: {window_paths} at {start_time}")

#         combined_audio_path = None
#         try:
#             # Get the audio paths from the buffer
#             valid_audio_paths = [self.audio_buffer.get(media_path) for media_path in window_paths if self.audio_buffer.get(media_path)]

#             if not valid_audio_paths or len(valid_audio_paths) != 4:
#                 print(f"WS: Could not retrieve 4 valid audio paths from buffer for window: {window_paths}")
#                 return

#             print(f"WS: Valid audio paths for concatenation: {valid_audio_paths}")

#             # Concatenate the audio files
#             combined_audio_path = os.path.join(TEMP_MEDIA_ROOT, f"{self.session_id}_window_{self.chunk_counter}.mp3")
#             concat_command = ["ffmpeg", "-y"]
#             input_files = []
#             for audio_path in valid_audio_paths:
#                 input_files.extend(["-i", audio_path])
#             concat_command.extend(input_files)
#             concat_command.extend(["-filter_complex", f"concat=n={len(valid_audio_paths)}:a=1:v=0", "-acodec", "libmp3lame", combined_audio_path])

#             print(f"WS: Running FFmpeg command to concatenate audio using Popen: {' '.join(concat_command)}")

#             process = subprocess.Popen(concat_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

#             async def monitor_process(process):
#                 loop = asyncio.get_running_loop()
#                 stdout, stderr = await loop.run_in_executor(None, process.communicate)
#                 return process.returncode, stdout, stderr

#             returncode, stdout, stderr = await monitor_process(process)

#             if returncode != 0:
#                 error_output = stderr.decode()
#                 print(f"WS: FFmpeg audio concatenation error (code {returncode}): {error_output}")
#                 return

#             print(f"WS: Audio files concatenated to: {combined_audio_path}")

#             # --- With the Deepgram transcription ---
#             print(f"WS: Attempting Deepgram transcription for the combined window audio: {combined_audio_path}")
#             transcription_start_time = time.time()
#             transcript_text = await asyncio.to_thread(transcribe_audio, combined_audio_path)
#             print(f"WS: Deepgram Transcription Result for the window: {transcript_text} after {time.time() - transcription_start_time:.2f} seconds")

#             if transcript_text:
#                 print(f"WS: Running analyze_results for the combined window transcript.")
#                 analysis_start_time = time.time()
#                 analysis_result = await asyncio.to_thread(analyze_results, transcript_text, window_paths[0], combined_audio_path) # Passing the first media path as a reference
#                 print(f"WS: Analysis Result for the window: {analysis_result} after {time.time() - analysis_start_time:.2f} seconds")

#                 audience_emotion = analysis_result.get('Feedback', {}).get('Audience Emotion')
#                 if audience_emotion:
#                     # Capitalize the first letter of the emotion
#                     capitalized_emotion = audience_emotion[0].upper() + audience_emotion[1:] if len(audience_emotion) > 0 else ""
#                     # Construct the S3 URL for the emotion with .mp4 extension
#                     emotion_s3_url = f"https://{BUCKET_NAME}.s3.{os.environ.get('AWS_S3_REGION_NAME')}.amazonaws.com/{EMOTION_FOLDER}/{capitalized_emotion}.mp4"
#                     print(f"WS: Sending window emotion update: {audience_emotion}, URL: {emotion_s3_url}")
#                     await self.send(json.dumps({
#                         "type": "window_emotion_update",
#                         "emotion": audience_emotion,
#                         "emotion_s3_url": emotion_s3_url
#                     }))

#                 # Send the full analysis result to the frontend
#                 print(f"WS: Sending full analysis update to frontend: {analysis_result}")
#                 await self.send(json.dumps({
#                     "type": "full_analysis_update",
#                     "analysis": analysis_result
#                 }))

#                 # Save the analysis for the last chunk in the window
#                 last_media_path = window_paths[-1]
#                 await asyncio.to_thread(self._save_window_analysis, last_media_path, analysis_result, transcript_text)

#             else:
#                 print("WS: No transcript obtained for the current window.")

#         except Exception as e:
#             print(f"WS: Error during windowed media analysis: {e}")
#             traceback.print_exc() # Log traceback for general window analysis errors
#         finally:
#             # Clean up the temporary combined audio file
#             if combined_audio_path and os.path.exists(combined_audio_path):
#                 try:
#                     await asyncio.sleep(0.1) # Add a small delay before removing the file
#                     os.remove(combined_audio_path)
#                     print(f"WS: Removed temporary combined audio file: {combined_audio_path}")
#                 except Exception as e:
#                     print(f"WS: Error removing temporary combined audio file: {e}")

#         print(f"WS: analyze_windowed_media finished at {time.time() - start_time:.2f} seconds")

#     def _save_window_analysis(self, media_path, analysis_result, transcript_text):
#         start_time = time.time()
#         print(f"WS: _save_window_analysis started for media path: {media_path} at {start_time}")
#         if not self.session_id:
#             print("WS: Error: Session ID not available, cannot save window analysis.")
#             return

#         try:
#             session_chunk_id = self.media_path_to_chunk.get(media_path)  # Get chunk ID from the map
#             if session_chunk_id:
#                 print(f"WS: Found SessionChunk ID: {session_chunk_id} for media path: {media_path}")

#                 sentiment_data = {
#                     'chunk': session_chunk_id,
#                     'audience_emotion': analysis_result.get('Feedback', {}).get('Audience Emotion', 0),
#                     'conviction': analysis_result.get('Feedback', {}).get('Conviction', 0),
#                     'clarity': analysis_result.get('Feedback', {}).get('Clarity', 0),
#                     'impact': analysis_result.get('Feedback', {}).get('Impact', 0),
#                     'brevity': analysis_result.get('Feedback', {}).get('Brevity', 0),
#                     'transformative_potential': analysis_result.get('Feedback', {}).get('Transformative Potential', 0),
#                     'trigger_response': analysis_result.get('Feedback', {}).get('Trigger Response', 0),
#                     'filler_words': analysis_result.get('Feedback', {}).get('Filler Words', 0),
#                     'grammar': analysis_result.get('Feedback', {}).get('Grammar', 0),
#                     'general_feedback_summary': analysis_result.get('Feedback', {}).get('General Feedback Summary', ''),
#                     'posture': analysis_result.get('Posture Scores', {}).get('Posture', 0),
#                     'motion': analysis_result.get('Posture Scores', {}).get('Motion', 0),
#                     'gestures': analysis_result.get('Posture Scores', {}).get('Gestures', 0),
#                     'volume': analysis_result.get('Scores', {}).get('Volume Score'),
#                     'pitch_variability': analysis_result.get('Scores', {}).get('Pitch Variability Score'),
#                     'pace': analysis_result.get('Scores', {}).get('Pace Score'),
#                     'chunk_transcript': transcript_text, # Saving the combined transcript for each chunk
#                 }
#                 print(f"WS: ChunkSentimentAnalysis data (for window): {sentiment_data}")
#                 sentiment_serializer = ChunkSentimentAnalysisSerializer(data=sentiment_data)
#                 if sentiment_serializer.is_valid():
#                     print("WS: ChunkSentimentAnalysisSerializer (for window) is valid.")
#                     try:
#                         sentiment_analysis_obj = sentiment_serializer.save() # Removed await here
#                         print(f"WS: Window analysis data saved for chunk ID: {session_chunk_id} with sentiment ID: {sentiment_analysis_obj.id} after {time.time() - start_time:.2f} seconds")
#                     except Exception as save_error:
#                         print(f"WS: Error during ChunkSentimentAnalysis save (for window): {save_error}")
#                 else:
#                     print("WS: Error saving ChunkSentimentAnalysis:", sentiment_serializer.errors)
#             else:
#                 error_message = f"SessionChunk ID not found in media_path_to_chunk for media path {media_path} during window analysis save."
#                 print(f"WS: {error_message}")
#         except Exception as e:
#             print(f"WS: Error in _save_window_analysis: {e}")
#         print(f"WS: _save_window_analysis finished after {time.time() - start_time:.2f} seconds")

#     def extract_audio(self, media_path):
#         start_time = time.time()
#         base, _ = os.path.splitext(media_path)
#         audio_mp3_path = f"{base}.mp3"
#         ffmpeg_command = f"ffmpeg -y -i {media_path} -vn -acodec libmp3lame -ab 128k {audio_mp3_path}"
#         print(f"WS: Running FFmpeg command: {ffmpeg_command}")
#         process = subprocess.Popen(ffmpeg_command, shell=True, stderr=subprocess.PIPE, stdout=subprocess.PIPE)
#         stdout, stderr = process.communicate()
#         if process.returncode == 0:
#             return audio_mp3_path
#         else:
#             error_output = stderr.decode()
#             print(f"WS: FFmpeg audio extraction error (code {process.returncode}): {error_output}")
#             return None

#     def upload_to_s3(self, file_path): # Changed to def (non-async)
#         start_time = time.time()
#         file_name = os.path.basename(file_path)
#         folder_path = f"{BASE_FOLDER}{self.session_id}/"
#         s3_key = f"{folder_path}{file_name}"
#         try:
#             s3.upload_file(file_path, BUCKET_NAME, s3_key) # Removed await
#             s3_url = f"s3://{BUCKET_NAME}/{s3_key}"
#             print(f"WS: Uploaded {file_path} to S3 successfully. S3 URL: {s3_url} after {time.time() - start_time:.2f} seconds.")
#             return s3_url
#         except Exception as e:
#             print(f"WS: S3 upload failed for {file_path}: {e}")
#             return None

#     def _save_chunk_data(self, media_path, analysis_result, transcript_text): # Changed to def (non-async)
#         start_time = time.time()
#         print(f"WS: _save_chunk_data called for chunk at {media_path} at {start_time}")
#         if not self.session_id:
#             print("WS: Error: Session ID not available, cannot save chunk data.")
#             return

#         try:
#             print(f"WS: Attempting to get PracticeSession with id: {self.session_id}")
#             session = PracticeSession.objects.get(id=self.session_id) # Removed await and asyncio.to_thread here. This will be called within asyncio.to_thread.
#             print(f"WS: Retrieved PracticeSession: {session.id}, {session.session_name}")

#             print(f"WS: Attempting to upload media to S3 for SessionChunk")
#             s3_upload_start_time = time.time()
#             s3_url = self.upload_to_s3(media_path) # This is a regular function now.
#             print(f"WS: S3 upload finished after {time.time() - s3_upload_start_time:.2f} seconds.")
#             if s3_url:
#                 print(f"WS: S3 URL for SessionChunk: {s3_url}")
#                 session_chunk_data = {
#                     'session': session.id,
#                     'video_file': s3_url
#                 }
#                 print(f"WS: SessionChunk data: {session_chunk_data}")
#                 session_chunk_serializer = SessionChunkSerializer(data=session_chunk_data)
#                 if session_chunk_serializer.is_valid():
#                     print("WS: SessionChunkSerializer is valid.")
#                     try:
#                         session_chunk = session_chunk_serializer.save() # Removed await and asyncio.to_thread here. This will be called within asyncio.to_thread.
#                         print(f"WS: SessionChunk saved with ID: {session_chunk.id} after {time.time() - start_time:.2f} seconds")
#                         self.media_path_to_chunk[media_path] = session_chunk.id  # Store the mapping here
#                     except Exception as save_error:
#                         print(f"WS: Error during SessionChunk save: {save_error}")
#                 else:
#                     print("WS: Error saving SessionChunk:", session_chunk_serializer.errors)
#             else:
#                 print("WS: Error: S3 URL not obtained, cannot save SessionChunk.")
#         except PracticeSession.DoesNotExist:
#             print(f"WS: Error: PracticeSession with id {self.session_id} not found.")
#         except Exception as e:
#             print(f"WS: Error in _save_chunk_data: {e}")
#         print(f"WS: _save_chunk_data finished after {time.time() - start_time:.2f} seconds")




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
EMOTION_FOLDER = "static-videos"  # Folder in S3 bucket containing emotion files

class LiveSessionConsumer(AsyncWebsocketConsumer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.session_id = None
        self.chunk_counter = 0
        self.media_buffer = []
        self.audio_buffer = {}  # Dictionary to map media_path to audio_path
        self.chunk_paths = [] # This list seems unused, can potentially remove later
        self.media_path_to_chunk = {}

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
        if self.session_id:
            print(f"WS: Client connected for Session ID: {self.session_id}")
            await self.accept()
            await self.send(json.dumps({
                "type": "connection_established",
                "message": f"Connected to session {self.session_id}"
            }))
        else:
            print("WS: Connection rejected: Missing session_id.")
            await self.close()

    async def disconnect(self, close_code):
        print(f"WS: Client disconnected for Session ID: {self.session_id}. Cleaning up...")
        for media_path, audio_path in self.audio_buffer.items():
            try:
                if audio_path and os.path.exists(audio_path):
                    os.remove(audio_path)
                    print(f"WS: Removed temporary audio file: {audio_path}")
            except Exception as e:
                print(f"WS: Error removing audio file: {e}")
        # Adding cleanup for media_buffer paths as well
        for media_path in list(self.media_buffer):
             try:
                if media_path and os.path.exists(media_path):
                    os.remove(media_path)
                    print(f"WS: Removed temporary media file: {media_path}")
             except Exception as e:
                print(f"WS: Error removing temporary media file: {e}")

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
                        await self.send(json.dumps({
                            "status": "received",
                            "session_id": self.session_id,
                            "chunk_number": self.chunk_counter,
                            "media_type": "media"
                        }))
                        print(f"WS: Starting process_media_chunk for chunk {self.chunk_counter}")
                        process_task = asyncio.create_task(self.process_media_chunk(media_path))
                        await process_task # Wait for the chunk processing to complete - this might help with race conditions for analysis

                        # Trigger windowed analysis based on buffer size
                        if len(self.media_buffer) == 4:
                            window_paths = list(self.media_buffer)
                            print(f"WS: Triggering windowed analysis for initial window (chunks 1 to 4)")
                            # Pass the last chunk number in the window
                            asyncio.create_task(self.analyze_windowed_media(window_paths, self.chunk_counter))
                        elif len(self.media_buffer) > 4:
                            self.media_buffer.pop(0)
                            window_paths = self.media_buffer[-4:]
                            print(f"WS: Triggering windowed analysis for sliding window (chunks {self.chunk_counter - 3} to {self.chunk_counter})")
                             # Pass the last chunk number in the window
                            asyncio.create_task(self.analyze_windowed_media(window_paths, self.chunk_counter))
                    else:
                        print("WS: Error: Missing 'data' in media message.")
                else:
                    print(f"WS: Received text message of type: {message_type}")
            elif bytes_data:
                print(f"WS: Received binary data of length: {len(bytes_data)}")
        except Exception as e:
            print(f"WS: Error processing received data: {e}")

    async def process_media_chunk(self, media_path):
        # This method is kept exactly as in your provided code
        start_time = time.time()
        print(f"WS: process_media_chunk started for: {media_path} at {start_time}")
        audio_path = None
        try:
            with concurrent.futures.ThreadPoolExecutor() as executor:
                futures = []
                print(f"WS: Submitting upload_to_s3 for: {media_path}")
                futures.append(executor.submit(self.upload_to_s3, media_path))
                print(f"WS: Calling extract_audio for: {media_path}")
                audio_path = await asyncio.to_thread(self.extract_audio, media_path)
                if audio_path:
                    print(f"WS: Audio extracted to: {audio_path} after {time.time() - start_time:.2f} seconds")
                    self.audio_buffer[media_path] = audio_path # Store the mapping
                else:
                    print("WS: Audio extraction failed.")

                for future in concurrent.futures.as_completed(futures):
                    try:
                        future.result()
                    except Exception as e:
                        print(f"WS: Task failed in process_media_chunk: {e}")

            # Save the chunk data. This should happen for every chunk.
            # This method will save the SessionChunk object
            print(f"WS: Attempting to save SessionChunk for {media_path}.")
            await asyncio.to_thread(self._save_chunk_data, media_path, None, None)
        except Exception as e:
            print(f"WS: Error in process_media_chunk: {e}")
        print(f"WS: process_media_chunk finished for: {media_path} after {time.time() - start_time:.2f} seconds")

    async def analyze_windowed_media(self, window_paths, latest_chunk_number): # Added latest_chunk_number argument back
        """Handles concatenation, transcription, analysis, and saving sentiment data."""
        # Check buffer content and existence as in the provided code
        if len(window_paths) != 4: # Ensure correct window size
            print(f"WS: analyze_windowed_media called with {len(window_paths)} paths for window ending with chunk {latest_chunk_number}, expected 4.")
            return

        start_time = time.time()
        # Use the media path of the last chunk in the window as a reference for saving analysis
        last_media_path = window_paths[-1] # Get the last media path for saving later
        # Use the passed latest_chunk_number for logging and saving
        window_chunk_number = latest_chunk_number


        print(f"WS: analyze_windowed_media started for window ending with {last_media_path} (chunk {window_chunk_number}) at {start_time}")

        combined_audio_path = None
        transcript_text = None # Initialize transcript_text
        analysis_result = None # Initialize analysis_result

        try:
            # Get the audio paths from the buffer as in the provided code
            # This implicitly checks if the audio extraction for these chunks has completed
            valid_audio_paths = [self.audio_buffer.get(media_path) for media_path in window_paths if self.audio_buffer.get(media_path)]

            # Check if we got 4 valid audio paths - analysis proceeds ONLY if all 4 are found
            if not valid_audio_paths or len(valid_audio_paths) != 4:
                print(f"WS: Could not retrieve 4 valid audio paths from buffer for window ending with {last_media_path} (chunk {window_chunk_number}). Cannot analyze this window.")
                # If audio is not ready/available, we skip analysis for this window instance.
                return # Skip analysis for this window if audio is missing/not ready


            print(f"WS: Valid audio paths for concatenation: {valid_audio_paths}")

            # --- MODIFIED FFmpeg command execution - Reverted to list format with shell=False ---
            combined_audio_path = os.path.join(TEMP_MEDIA_ROOT, f"{self.session_id}_window_{window_chunk_number}.mp3") # Use window_chunk_number for filename

            # Construct the command as a list of arguments
            concat_command = ["ffmpeg", "-y"]
            for audio_path in valid_audio_paths:
                concat_command.extend(["-i", audio_path])
            concat_command.extend(["-filter_complex", f"concat=n={len(valid_audio_paths)}:a=1:v=0", "-acodec", "libmp3lame", combined_audio_path])

            print(f"WS: Running FFmpeg command to concatenate audio using Popen (list format): {' '.join(concat_command)}")

            # Run FFmpeg using Popen with list format (shell=False by default)
            process = subprocess.Popen(concat_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

            # Wait for the FFmpeg process to complete in a thread
            stdout, stderr = await asyncio.to_thread(process.communicate)
            returncode = await asyncio.to_thread(lambda p: p.returncode, process) # Get return code in thread

            # --- END MODIFIED FFmpeg command execution ---


            if returncode != 0:
                error_output = stderr.decode()
                print(f"WS: FFmpeg audio concatenation error (code {returncode}) for window ending with chunk {window_chunk_number}: {error_output}")
                print(f"WS: FFmpeg stdout: {stdout.decode()}")
                return # Stop analysis if concatenation fails

            print(f"WS: Audio files concatenated to: {combined_audio_path}")

            # --- With the Deepgram transcription (blocking network I/O) ---
            if client: # Check if OpenAI client was initialized (as in previous versions)
                print(f"WS: Attempting Deepgram transcription for the combined window audio: {combined_audio_path}")
                transcription_start_time = time.time()
                transcript_text = await asyncio.to_thread(transcribe_audio, combined_audio_path)
                print(f"WS: Deepgram Transcription Result for the window ending with chunk {window_chunk_number}: {transcript_text} after {time.time() - transcription_start_time:.2f} seconds")
            else:
                 print("WS: OpenAI client not initialized (missing API key?). Skipping transcription.")


            if transcript_text and client: # If transcript obtained AND client is available
                # --- Analyze results using OpenAI (blocking network I/O) ---
                print(f"WS: Running analyze_results for the combined window transcript.")
                analysis_start_time = time.time()
                # Pass the video path of the first chunk in the window for visual analysis reference
                analysis_result = await asyncio.to_thread(analyze_results, transcript_text, window_paths[0], combined_audio_path) # Using window_paths[0] as before
                print(f"WS: Analysis Result for the window ending with chunk {window_chunk_number}: {analysis_result} after {time.time() - analysis_start_time:.2f} seconds")
            elif transcript_text:
                 print("WS: OpenAI client not initialized. Skipping analysis despite having transcript.")
            else:
                print(f"WS: No transcript obtained for window ending with chunk {window_chunk_number}. Skipping analysis.")


            # If analysis result is available, send updates and attempt to save
            if analysis_result:
                # Send analysis updates to the frontend
                audience_emotion = analysis_result.get('Feedback', {}).get('Audience Emotion')
                # Check if S3 client is initialized before trying to construct S3 URL
                if audience_emotion and s3:
                    capitalized_emotion = audience_emotion[0].upper() + audience_emotion[1:] if audience_emotion else ""
                    # Use AWS_REGION if AWS_S3_REGION_NAME is not set, or default to us-east-1
                    region_name = os.environ.get('AWS_S3_REGION_NAME', os.environ.get('AWS_REGION', 'us-east-1')) # Default region if none set
                    emotion_s3_url = f"https://{BUCKET_NAME}.s3.{region_name}.amazonaws.com/{EMOTION_FOLDER}/{capitalized_emotion}.mp4"
                    print(f"WS: Sending window emotion update: {audience_emotion}, URL: {emotion_s3_url}")
                    await self.send(json.dumps({
                        "type": "window_emotion_update",
                        "emotion": audience_emotion,
                        "emotion_s3_url": emotion_s3_url
                    }))
                elif audience_emotion:
                     print("WS: Audience emotion detected but S3 client not configured, cannot send static video URL.")


                print(f"WS: Sending full analysis update to frontend for chunk {window_chunk_number}: {analysis_result}")
                await self.send(json.dumps({
                    "type": "full_analysis_update",
                    "analysis": analysis_result
                }))

                # Save the analysis for the last chunk in the window
                # Call the synchronous _save_window_analysis using asyncio.to_thread
                print(f"WS: Calling _save_window_analysis for chunk {window_chunk_number} ({last_media_path})...")
                # Pass window_chunk_number which is the chunk_counter when this window analysis started
                await asyncio.to_thread(self._save_window_analysis, last_media_path, analysis_result, transcript_text, window_chunk_number)


            else:
                print(f"WS: No analysis result obtained for window ending with chunk {window_chunk_number}. Skipping analysis save.")


        except Exception as e:
            print(f"WS: Error during windowed media analysis ending with chunk {window_chunk_number}: {e}")
            traceback.print_exc() # Log traceback for general window analysis errors
        finally:
            # Clean up the temporary combined audio file for the window
            if combined_audio_path and os.path.exists(combined_audio_path):
                try:
                    # Small delay before removing the file
                    await asyncio.sleep(0.05)
                    os.remove(combined_audio_path)
                    print(f"WS: Removed temporary combined audio file: {combined_audio_path}")
                except Exception as e:
                    print(f"WS: Error removing temporary combined audio file {combined_audio_path}: {e}")


        print(f"WS: analyze_windowed_media finished for window ending with chunk {window_chunk_number} after {time.time() - start_time:.2f} seconds")


    def extract_audio(self, media_path):
        """Extracts audio from a media file using FFmpeg."""
        # This method is kept exactly as in your provided code
        start_time = time.time()
        base, _ = os.path.splitext(media_path)
        audio_mp3_path = f"{base}.mp3"
        ffmpeg_command = f"ffmpeg -y -i {media_path} -vn -acodec libmp3lame -ab 128k {audio_mp3_path}"
        print(f"WS: Running FFmpeg command: {ffmpeg_command}")
        try:
            process = subprocess.Popen(ffmpeg_command, shell=True, stderr=subprocess.PIPE, stdout=subprocess.PIPE)
            stdout, stderr = process.communicate()
            returncode = process.returncode
            if returncode == 0:
                print(f"WS: Audio extracted to: {audio_mp3_path} after {time.time() - start_time:.2f} seconds")
                return audio_mp3_path
            else:
                error_output = stderr.decode()
                print(f"WS: FFmpeg audio extraction error (code {returncode}): {error_output}")
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
        # This method is kept exactly as in your provided code
        if s3 is None:
             print(f"WS: S3 client is not initialized. Cannot upload file: {file_path}.")
             return None

        start_time = time.time()
        file_name = os.path.basename(file_path)
        folder_path = f"{BASE_FOLDER}{self.session_id}/"
        s3_key = f"{folder_path}{file_name}"
        try:
            s3.upload_file(file_path, BUCKET_NAME, s3_key)
            # Construct S3 URL - using regional endpoint format as before
            region_name = os.environ.get('AWS_S3_REGION_NAME', os.environ.get('AWS_REGION', 'us-east-1'))
            s3_url = f"https://{BUCKET_NAME}.s3.{region_name}.amazonaws.com/{s3_key}"
            print(f"WS: Uploaded {file_path} to S3 successfully. S3 URL: {s3_url} after {time.time() - start_time:.2f} seconds.")
            return s3_url
        except Exception as e:
            print(f"WS: S3 upload failed for {file_path}: {e}")
            traceback.print_exc()
            return None


    # This method is kept exactly as in your provided code structure
    # It remains a synchronous method called via asyncio.to_thread from process_media_chunk
    def _save_chunk_data(self, media_path, analysis_result, transcript_text): # Arguments match old code signature
        """Saves the SessionChunk object and maps media path to chunk ID."""
        start_time = time.time()
        print(f"WS: _save_chunk_data called for chunk at {media_path} at {start_time}")
        if not self.session_id:
            print("WS: Error: Session ID not available, cannot save chunk data.")
            return

        try:
            # Synchronous DB call in this sync method
            print(f"WS: Attempting to get PracticeSession with id: {self.session_id}")
            session = PracticeSession.objects.get(id=self.session_id)
            print(f"WS: Retrieved PracticeSession: {session.id}, {session.session_name}")

            # Call upload_to_s3 here as in the old code structure
            s3_upload_start_time = time.time()
            s3_url = self.upload_to_s3(media_path) # Call the sync upload method
            print(f"WS: S3 upload finished after {time.time() - s3_upload_start_time:.2f} seconds.")

            if s3_url:
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
                        # Synchronous DB call in this sync method
                        session_chunk = session_chunk_serializer.save()
                        print(f"WS: SessionChunk saved with ID: {session_chunk.id} for media path: {media_path} after {time.time() - start_time:.2f} seconds")
                        self.media_path_to_chunk[media_path] = session_chunk.id  # Store the mapping here
                    except Exception as save_error:
                        print(f"WS: Error during SessionChunk save: {save_error}")
                        traceback.print_exc()
                else:
                    print("WS: Error saving SessionChunk:", session_chunk_serializer.errors)
            else:
                # As in your old code, if S3 URL is not obtained, SessionChunk is NOT saved.
                # This means media_path_to_chunk will NOT be populated for this chunk.
                print("WS: Error: S3 URL not obtained, cannot save SessionChunk.")
                # Note: This means subsequent window analysis depending on this chunk's ID will fail to find it.


        except PracticeSession.DoesNotExist:
            print(f"WS: Error: PracticeSession with id {self.session_id} not found.")
        except Exception as e:
            print(f"WS: Error in _save_chunk_data: {e}")
            traceback.print_exc()
        print(f"WS: _save_chunk_data finished after {time.time() - start_time:.2f} seconds")


    # --- MODIFIED _save_window_analysis based on the provided code ---
    # This method is *not* decorated with @database_sync_to_async as in your base code,
    # but is called using await asyncio.to_thread from analyze_windowed_media.
    # It has the corrected mapping and improved logging.
    def _save_window_analysis(self, media_path, analysis_result, transcript_text, chunk_number): # Accepting chunk_number
        start_time = time.time()
        print(f"WS: _save_window_analysis started for media path: {media_path} (chunk {chunk_number}) at {start_time}")
        if not self.session_id:
            print("WS: Error: Session ID not available, cannot save window analysis.")
            return

        try:
            # Get the SessionChunk ID from the map
            # This requires _save_chunk_data to have successfully saved the SessionChunk
            session_chunk_id = self.media_path_to_chunk.get(media_path)

            # --- ADDED LOG ---
            print(f"WS: In _save_window_analysis for {media_path} (chunk {chunk_number}): session_chunk_id found? {session_chunk_id is not None}. ID: {session_chunk_id}")
            # --- END ADDED LOG ---

            if session_chunk_id:
                print(f"WS: Found SessionChunk ID: {session_chunk_id} for media path: {media_path}")

                # Safely access nested dictionaries
                feedback_data = analysis_result.get('Feedback', {})
                # --- CORRECTED MAPPING KEYS ---
                posture_data = analysis_result.get('Posture', {}) # Use 'Posture', not 'Posture Scores'
                scores_data = analysis_result.get('Scores', {})
                # --- END CORRECTED MAPPING KEYS ---


                # Prepare data for ChunkSentimentAnalysis
                sentiment_data = {
                    'chunk': session_chunk_id,
                    'chunk_number': chunk_number, # Using the passed chunk_number

                    # Map from 'Feedback' - as in your old code
                    'audience_emotion': feedback_data.get('Audience Emotion'), # Allow None if not present
                    'conviction': feedback_data.get('Conviction', 0),
                    'clarity': feedback_data.get('Clarity', 0),
                    'impact': feedback_data.get('Impact', 0),
                    'brevity': feedback_data.get('Brevity', 0),
                    'transformative_potential': feedback_data.get('Transformative Potential', 0),
                    'trigger_response': feedback_data.get('Trigger Response', 0),
                    'filler_words': feedback_data.get('Filler Words', 0),
                    'grammar': feedback_data.get('Grammar', 0),
                    'general_feedback_summary': feedback_data.get('General Feedback Summary', ''),

                    # --- CORRECTED MAPPING - Use data from posture_data ---
                    'posture': posture_data.get('Posture', 0),
                    'motion': posture_data.get('Motion', 0),
                    # Convert integer 0/1 to boolean False/True as needed by your model/serializer
                    'gestures': bool(posture_data.get('Gestures', False)), # Default to False if key missing
                    # --- END CORRECTED MAPPING ---

                    # Map from the 'Scores' nested dictionary - as in your old code
                    'volume': scores_data.get('Volume Score'), # Allow None if not present
                    'pitch_variability': scores_data.get('Pitch Variability Score'), # Allow None if not present
                    'pace': scores_data.get('Pace Score'), # Allow None if not present
                    # Assuming 'Pauses' key might be 'Pause Score' based on later logs
                    'pauses': scores_data.get('Pause Score', 0), # Use Pause Score key, default 0


                    'chunk_transcript': transcript_text, # Saving the combined transcript for each chunk
                }
                print(f"WS: ChunkSentimentAnalysis data (for window, chunk {chunk_number}): {sentiment_data}")

                # Use the serializer to validate and prepare data for saving
                sentiment_serializer = ChunkSentimentAnalysisSerializer(data=sentiment_data)

                if sentiment_serializer.is_valid():
                    print(f"WS: ChunkSentimentAnalysisSerializer (for window, chunk {chunk_number}) is valid.")
                    try:
                        # Check if a ChunkSentimentAnalysis already exists for this chunk
                        # SYNCHRONOUS database call inside this sync method (run via asyncio.to_thread)
                        # Note: Your old code did NOT check for existing sentiment before saving.
                        # If you want to update, uncomment and use the update logic.
                        # For now, let's stick closer to the old code's save-always pattern.
                        # existing_sentiment = ChunkSentimentAnalysis.objects.filter(chunk_id=session_chunk_id).first()
                        # if existing_sentiment:
                        #     print("WS: Updating existing ChunkSentimentAnalysis...")
                        #     # Update fields from validated_data if needed
                        #     validated_data = sentiment_serializer.validated_data
                        #     for key, value in validated_data.items():
                        #         if key != 'chunk':
                        #             setattr(existing_sentiment, key, value)
                        #     sentiment_analysis_obj = existing_sentiment
                        #     sentiment_analysis_obj.save()
                        # else:
                        #     print("WS: Creating new ChunkSentimentAnalysis...")
                        #     sentiment_analysis_obj = sentiment_serializer.save() # Sync save

                        # --- Original Save Logic (always create new) ---
                        sentiment_analysis_obj = sentiment_serializer.save() # Synchronous save in this sync method
                        # --- End Original Save Logic ---

                        print(f"WS: Window analysis data saved for chunk ID: {session_chunk_id} (chunk {chunk_number}) with sentiment ID: {sentiment_analysis_obj.id} after {time.time() - start_time:.2f} seconds")

                    except Exception as save_error:
                        print(f"WS: Error during ChunkSentimentAnalysis save (for window, chunk {chunk_number}): {save_error}")
                        # --- ADDED TRACEBACK ---
                        traceback.print_exc() # Print traceback for save errors
                        # --- END ADDED TRACEBACK ---
                else:
                    # Print validation errors if serializer is not valid
                    print(f"WS: Error saving ChunkSentimentAnalysis (chunk {chunk_number}):", sentiment_serializer.errors)

            else:
                # This logs if session_chunk_id was None
                error_message = f"SessionChunk ID not found in media_path_to_chunk for media path {media_path} during window analysis save for chunk {chunk_number}."
                print(f"WS: {error_message}")

        except Exception as e:
            print(f"WS: Error in _save_window_analysis for media path {media_path} (chunk {chunk_number}): {e}")
            # --- ADDED TRACEBACK ---
            traceback.print_exc() # Print traceback for general _save_window_analysis errors
            # --- END ADDED TRACEBACK ---

        print(f"WS: _save_window_analysis finished for media path {media_path} (chunk {chunk_number}) after {time.time() - start_time:.2f} seconds")