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

# from base64 import b64decode
# from datetime import timedelta

# from channels.generic.websocket import AsyncWebsocketConsumer
# from channels.db import database_sync_to_async

# from .sentiment_analysis import analyze_results
# from practice_sessions.models import PracticeSession, SessionChunk, ChunkSentimentAnalysis
# from practice_sessions.serializers import SessionChunkSerializer, ChunkSentimentAnalysisSerializer, PracticeSessionSerializer

# os.environ.setdefault("DJANGO_SETTINGS_MODULE", "engagex_backend.settings")
# django.setup()


# openai.api_key = os.environ.get("OPENAI_API_KEY")
# client = openai.OpenAI()

# s3 = boto3.client("s3", region_name=os.environ.get('AWS_REGION'))
# BUCKET_NAME = "engagex-user-content-1234"
# BASE_FOLDER = "user-videos/"
# TEMP_MEDIA_ROOT = tempfile.gettempdir()

# class LiveSessionConsumer(AsyncWebsocketConsumer):
#     def __init__(self, *args, **kwargs):
#         super().__init__(*args, **kwargs)
#         self.session_id = None
#         self.chunk_counter = 0
#         self.media_buffer = []
#         self.audio_buffer = [] # Initialize here
#         self.chunk_paths = []

#     async def connect(self):
#         query_string = self.scope['query_string'].decode()
#         query_params = {}
#         if query_string:
#             for param in query_string.split('&'):
#                 try:
#                     key, value = param.split('=', 1)
#                     query_params[key] = value
#                 except ValueError:
#                     print(f"Warning: Could not parse query parameter: {param}")
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
#         for file_path in self.audio_buffer:
#             try:
#                 os.remove(file_path)
#                 print(f"WS: Removed temporary audio file: {file_path}")
#             except Exception as e:
#                 print(f"WS: Error removing file: {e}")

#         # Analyze any remaining chunks at the end of the session
#         if self.media_buffer:
#             print(f"WS: Analyzing remaining {len(self.media_buffer)} chunks at the end of the session.")
#             await self.analyze_windowed_media(list(self.media_buffer))

#         if self.session_id:
#             print(f"WS: Calling aggregate_and_save_analysis for session {self.session_id}")
#             await self.aggregate_and_save_analysis()
#         else:
#             print("WS: Session ID not available during disconnect, skipping aggregation.")

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
#                         # print(f"WS: Received media chunk {self.chunk_counter} for Session {self.session_id}. Saved to {media_path}")
#                         self.media_buffer.append(media_path)
#                         await self.send(json.dumps({
#                             "status": "received",
#                             "session_id": self.session_id,
#                             "chunk_number": self.chunk_counter,
#                             "media_type": "media"
#                         }))
#                         print(f"WS: Starting process_media_chunk for chunk {self.chunk_counter}")
#                         asyncio.create_task(self.process_media_chunk(media_path))

#                         if self.chunk_counter % 3 == 0 and self.chunk_counter >= 3:
#                             window_paths = self.media_buffer[-3:]
#                             print(f"WS: Triggering windowed analysis for chunks: {self.chunk_counter - 2} to {self.chunk_counter}")
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
#                     self.audio_buffer.append(audio_path)
#                     if len(self.audio_buffer) > 9:
#                         old = self.audio_buffer.pop(0)
#                         try:
#                             os.remove(old)
#                             print(f"WS: Removed old audio buffer file: {old}")
#                         except Exception as e:
#                             print(f"WS: Error removing old audio file: {e}")
#                     else:
#                         print("WS: Audio extraction failed.")

#                 for future in concurrent.futures.as_completed(futures):
#                     try:
#                         future.result()
#                     except Exception as e:
#                         print(f"WS: Task failed in process_media_chunk: {e}")

#             # Call save_chunk_data and wait for it to complete
#             await self.save_chunk_data(media_path, None, None) # We don't have analysis result or transcript here

#         except Exception as e:
#             print(f"WS: Error in process_media_chunk: {e}")
#         print(f"WS: process_media_chunk finished for: {media_path} after {time.time() - start_time:.2f} seconds")

#     async def analyze_windowed_media(self, window_paths):
#         if not window_paths:
#             print("WS: analyze_windowed_media called with no media paths.")
#             return

#         start_time = time.time()
#         print(f"WS: analyze_windowed_media started for window: {window_paths} at {start_time}")

#         window_analyses = []
#         for media_path in window_paths:
#             audio_path = await asyncio.to_thread(self.extract_audio, media_path)
#             if audio_path:
#                 try:
#                     print(f"WS: Attempting transcription for window media: {media_path}")
#                     transcription_start_time = time.time()
#                     with open(audio_path, 'rb') as audio_file:
#                         transcript = await asyncio.to_thread(client.audio.transcriptions.create,
#                             model="whisper-1",
#                             file=audio_file
#                         )
#                     transcript_text = transcript.text
#                     print(f"WS: Transcription Result for window media {media_path}: {transcript_text} after {time.time() - transcription_start_time:.2f} seconds")

#                     if transcript_text:
#                         print(f"WS: Running analyze_results for window media: {media_path} with audio: {audio_path}")
#                         analysis_start_time = time.time()
#                         analysis_result = await asyncio.to_thread(analyze_results, transcript_text, media_path, audio_path) # Pass media_path as video_path
#                         print(f"WS: Analysis Result for window media {media_path}: {analysis_result} after {time.time() - analysis_start_time:.2f} seconds")
#                         window_analyses.append(analysis_result)

#                         # Send feedback for each chunk in the window
#                         feedback_data = {
#                             "type": "realtime_feedback",
#                             "engagement": analysis_result.get('Feedback', {}).get('Engagement'),
#                             "audience_emotion": analysis_result.get('Feedback', {}).get('Audience Emotion'),
#                             "impact": analysis_result.get('Feedback', {}).get('Impact'),
#                             "clarity": analysis_result.get('Feedback', {}).get('Clarity'),
#                             "transformative_potential": analysis_result.get('Feedback', {}).get('Transformative Potential'),
#                             "volume": analysis_result.get('Scores', {}).get('Volume Score'),
#                             "pace": analysis_result.get('Scores', {}).get('Pace Score'),
#                         }
#                         print("WS: Sending Real-time Feedback for chunk in window:", feedback_data)
#                         await self.send(json.dumps(feedback_data))

#                         # Save analysis for each chunk in the window
#                         await self.save_window_analysis(media_path, analysis_result, transcript_text)

#                     else:
#                         print(f"WS: No transcript for window media: {media_path}")
#                 except Exception as e:
#                     print(f"WS: Error analyzing window media {media_path}: {e}")
#             else:
#                 print(f"WS: Error extracting audio for window media: {media_path}")

#         print(f"WS: analyze_windowed_media finished at {time.time() - start_time:.2f} seconds")

#     async def save_window_analysis(self, media_path, analysis_result, transcript_text):
#             start_time = time.time()
#             print(f"WS: save_window_analysis called for media path: {media_path} at {start_time}")
#             if not self.session_id:
#                 print("WS: Error: Session ID not available, cannot save window analysis.")
#                 return

#             try:
#                 async def _save_data():
#                     _save_data_start_time = time.time()
#                     print(f"WS: _save_data (for window) started at {_save_data_start_time}")
#                     try:
#                         # Find the SessionChunk associated with this media_path
#                         file_name = os.path.basename(media_path)
#                         session_chunks = await asyncio.to_thread(SessionChunk.objects.filter(session_id=self.session_id, video_file__icontains=file_name))
#                         if session_chunks.exists():
#                             session_chunk = session_chunks.first() # Assuming one chunk per file
#                             print(f"WS: Found SessionChunk for window analysis with video_file containing: {file_name}, ID: {session_chunk.id}")

#                             sentiment_data = {
#                                 'chunk': session_chunk.id,
#                                 'engagement': analysis_result.get('Feedback', {}).get('Engagement', 0),
#                                 'audience_emotion': analysis_result.get('Feedback', {}).get('Audience Emotion', ''),
#                                 'conviction': analysis_result.get('Feedback', {}).get('Conviction', 0),
#                                 'clarity': analysis_result.get('Feedback', {}).get('Clarity', 0),
#                                 'impact': analysis_result.get('Feedback', {}).get('Impact', 0),
#                                 'brevity': analysis_result.get('Feedback', {}).get('Brevity', 0),
#                                 'transformative_potential': analysis_result.get('Feedback', {}).get('Transformative Potential', 0),
#                                 'body_posture': analysis_result.get('Feedback', {}).get('Body Posture', 0),
#                                 'general_feedback_summary': analysis_result.get('Feedback', {}).get('General Feedback Summary', ''),
#                                 'volume': analysis_result.get('Scores', {}).get('Volume Score'),
#                                 'pitch_variability': analysis_result.get('Scores', {}).get('Pitch Variability Score'),
#                                 'pace': analysis_result.get('Scores', {}).get('Pace Score'),
#                                 'chunk_transcript': transcript_text,
#                             }
#                             print(f"WS: ChunkSentimentAnalysis data (for window): {sentiment_data}")
#                             sentiment_serializer = ChunkSentimentAnalysisSerializer(data=sentiment_data)
#                             if sentiment_serializer.is_valid():
#                                 print("WS: ChunkSentimentAnalysisSerializer (for window) is valid.")
#                                 try:
#                                     sentiment_analysis_save_start_time = time.time()
#                                     print(f"WS: Attempting to save ChunkSentimentAnalysis data: {sentiment_serializer.validated_data}") # Added log
#                                     sentiment_analysis_obj = await asyncio.to_thread(sentiment_serializer.save)
#                                     print(f"WS: Window analysis data saved with ID: {sentiment_analysis_obj.id} after {time.time() - sentiment_analysis_save_start_time:.2f} seconds")
#                                     print(f"WS: ChunkSentimentAnalysis save operation completed.") # Added log
#                                 except Exception as save_error:
#                                     print(f"WS: Error during ChunkSentimentAnalysis save (for window): {save_error}")
#                         else:
#                             error_message = f"SessionChunk not found for session {self.session_id} and media path {media_path} during window analysis save."
#                             print(f"WS: {error_message}")
#                             await self.send(json.dumps({"type": "error", "message": error_message}))

#                     except Exception as e:
#                         error_message = f"Error finding SessionChunk for window analysis: {e}"
#                         print(f"WS: {error_message}")
#                         await self.send(json.dumps({"type": "error", "message": error_message}))
#                     print(f"WS: _save_data (for window) finished after {time.time() - _save_data_start_time:.2f} seconds")

#                 await asyncio.to_thread(_save_data)

#             except Exception as e:
#                 print(f"WS: Error saving window analysis: {e}")
#                 await self.send(json.dumps({"type": "error", "message": f"Error saving window analysis: {e}"}))
#             print(f"WS: save_window_analysis finished after {time.time() - start_time:.2f} seconds")


#     def extract_audio(self, media_path):
#         start_time = time.time()
#         base, _ = os.path.splitext(media_path)
#         audio_mp3_path = f"{base}.mp3"
#         ffmpeg_command = f"ffmpeg -y -i {media_path} -vn -acodec libmp3lame -ab 128k {audio_mp3_path}"
#         print(f"WS: Running FFmpeg command: {ffmpeg_command}")
#         process = subprocess.Popen(ffmpeg_command, shell=True, stderr=subprocess.PIPE, stdout=subprocess.PIPE)
#         stdout, stderr = process.communicate()
#         if process.returncode == 0:
#             # print(f"WS: Extracted audio to {audio_mp3_path} after {time.time() - start_time:.2f} seconds")
#             return audio_mp3_path
#         else:
#             error_output = stderr.decode()
#             print(f"WS: FFmpeg audio extraction error (code {process.returncode}): {error_output}")
#             return None

#     def upload_to_s3(self, file_path):
#         start_time = time.time()
#         file_name = os.path.basename(file_path)
#         folder_path = f"{BASE_FOLDER}{self.session_id}/"
#         s3_key = f"{folder_path}{file_name}"
#         try:
#             # print(f"WS: Uploading {file_path} to S3 at s3://{BUCKET_NAME}/{s3_key}")
#             s3.upload_file(file_path, BUCKET_NAME, s3_key)
#             print(f"WS: Uploaded {file_path} to S3 successfully after {time.time() - start_time:.2f} seconds.")
#             return f"s3://{BUCKET_NAME}/{s3_key}"
#         except Exception as e:
#             print(f"WS: S3 upload failed for {file_path}: {e}")
#             return None

#     async def save_chunk_data(self, media_path, analysis_result, transcript_text): # Keep this for saving individual chunk metadata
#             start_time = time.time()
#             print(f"WS: save_chunk_data called for chunk at {media_path} at {start_time}")
#             if not self.session_id:
#                 print("WS: Error: Session ID not available, cannot save chunk data.")
#                 return

#             try:
#                 async def _save_data():
#                     _save_data_start_time = time.time()
#                     print(f"WS: _save_data (individual chunk) started at {_save_data_start_time}")
#                     print(f"WS: Attempting to get PracticeSession with id: {self.session_id}")
#                     session = await asyncio.to_thread(PracticeSession.objects.get, id=self.session_id)
#                     print(f"WS: Retrieved PracticeSession: {session.id}, {session.session_name} after {time.time() - _save_data_start_time:.2f} seconds")

#                     print(f"WS: Attempting to upload media to S3 for SessionChunk")
#                     s3_upload_start_time = time.time()
#                     s3_url = await asyncio.to_thread(self.upload_to_s3, media_path)
#                     s3_upload_duration = time.time() - s3_upload_start_time
#                     print(f"WS: S3 upload attempt finished after {s3_upload_duration:.2f} seconds.")
#                     if s3_url:
#                         print(f"WS: S3 URL for SessionChunk: {s3_url}")
#                         session_chunk_data = {
#                             'session': session.id,
#                             'video_file': s3_url
#                         }
#                         print(f"WS: SessionChunk data: {session_chunk_data}")
#                         session_chunk_serializer = SessionChunkSerializer(data=session_chunk_data)
#                         if session_chunk_serializer.is_valid():
#                             print("WS: SessionChunkSerializer is valid.")
#                             session_chunk_save_start_time = time.time()
#                             print(f"WS: Attempting to save SessionChunk data: {session_chunk_serializer.validated_data}") # Added log
#                             session_chunk = await asyncio.to_thread(session_chunk_serializer.save)
#                             print(f"WS: SessionChunk saved with ID: {session_chunk.id} after {time.time() - session_chunk_save_start_time:.2f} seconds")
#                             print(f"WS: SessionChunk save operation completed.") # Added log
#                         else:
#                             print("WS: Error saving SessionChunk:", session_chunk_serializer.errors)
#                     else:
#                         print("WS: Error: S3 URL not obtained, cannot save SessionChunk.")
#                     print(f"WS: _save_data (individual chunk) finished after {time.time() - _save_data_start_time:.2f} seconds")

#                 await asyncio.to_thread(_save_data)

#             except PracticeSession.DoesNotExist:
#                 print(f"WS: Error: PracticeSession with id {self.session_id} not found.")
#             except Exception as e:
#                 print(f"WS: Error saving chunk data: {e}")
#             print(f"WS: save_chunk_data finished after {time.time() - start_time:.2f} seconds")

#     async def aggregate_and_save_analysis(self):
#             start_time = time.time()
#             print(f"WS: aggregate_and_save_analysis called for session {self.session_id} at {start_time}")
#             if not self.session_id:
#                 print("WS: Error: Session ID not available, cannot aggregate analysis.")
#                 return

#             try:
#                 session = await asyncio.to_thread(PracticeSession.objects.get, id=self.session_id)
#                 all_chunk_sentiments = await asyncio.to_thread(list, ChunkSentimentAnalysis.objects.filter(chunk__session=session))
#                 print(f"WS: Retrieved {len(all_chunk_sentiments)} ChunkSentimentAnalysis objects for aggregation.")

#                 if all_chunk_sentiments:
#                     total_engagement = 0
#                     total_conviction = 0
#                     total_clarity = 0
#                     total_impact = 0
#                     total_brevity = 0
#                     total_transformative_potential = 0
#                     total_body_posture = 0
#                     total_volume = 0
#                     total_pitch_variability = 0
#                     total_pace = 0
#                     emotion_counts = {}
#                     all_summaries = []

#                     for sentiment in all_chunk_sentiments:
#                         total_engagement += sentiment.engagement
#                         total_conviction += sentiment.conviction
#                         total_clarity += sentiment.clarity
#                         total_impact += sentiment.impact
#                         total_brevity += sentiment.brevity
#                         total_transformative_potential += sentiment.transformative_potential
#                         total_body_posture += sentiment.body_posture
#                         total_volume += sentiment.volume if sentiment.volume is not None else 0
#                         total_pitch_variability += sentiment.pitch_variability if sentiment.pitch_variability is not None else 0
#                         total_pace += sentiment.pace if sentiment.pace is not None else 0
#                         emotion = sentiment.audience_emotion # Assuming audience_emotion is a string in your model
#                         emotion_counts[emotion] = emotion_counts.get(emotion, 0) + 1
#                         if sentiment.general_feedback_summary:
#                             all_summaries.append(sentiment.general_feedback_summary)

#                     num_chunks = len(all_chunk_sentiments)
#                     average_engagement = total_engagement / num_chunks if num_chunks > 0 else 0
#                     average_conviction = total_conviction / num_chunks if num_chunks > 0 else 0
#                     average_clarity = total_clarity / num_chunks if num_chunks > 0 else 0
#                     average_impact = total_impact / num_chunks if num_chunks > 0 else 0
#                     average_brevity = total_brevity / num_chunks if num_chunks > 0 else 0
#                     average_transformative_potential = total_transformative_potential / num_chunks if num_chunks > 0 else 0
#                     average_body_posture = total_body_posture / num_chunks if num_chunks > 0 else 0
#                     average_volume = total_volume / num_chunks if num_chunks > 0 else 0
#                     average_pitch_variability = total_pitch_variability / num_chunks if num_chunks > 0 else 0
#                     average_pace = total_pace / num_chunks if num_chunks > 0 else 0

#                     dominant_emotion = max(emotion_counts, key=emotion_counts.get) if emotion_counts else None
#                     overall_summary = " ".join(all_summaries) # You might want a more sophisticated way to create the overall summary

#                     aggregated_data = {
#                         'average_engagement': average_engagement,
#                         'dominant_audience_emotion': dominant_emotion,
#                         'average_conviction': average_conviction,
#                         'average_clarity': average_clarity,
#                         'average_impact': average_impact,
#                         'average_brevity': average_brevity,
#                         'average_transformative_potential': average_transformative_potential,
#                         'average_body_posture': average_body_posture,
#                         'overall_feedback_summary': overall_summary,
#                         'average_volume': average_volume,
#                         'average_pitch_variability': average_pitch_variability,
#                         'average_pace': average_pace,
#                         'session_status': 'completed'
#                     }
#                     print(f"WS: Aggregated data: {aggregated_data}")
#                     session_serializer = PracticeSessionSerializer(session, data=aggregated_data, partial=True)
#                     if session_serializer.is_valid():
#                         print("WS: PracticeSessionSerializer (for aggregation) is valid.")
#                         try:
#                             aggregation_save_start_time = time.time()
#                             print(f"WS: Attempting to save aggregated PracticeSession data: {session_serializer.validated_data}")
#                             await asyncio.to_thread(session_serializer.save)
#                             print(f"WS: Aggregated analysis saved to PracticeSession ID: {session.id} after {time.time() - aggregation_save_start_time:.2f} seconds")
#                             print(f"WS: PracticeSession aggregation save operation completed.")
#                         except Exception as e:
#                             print(f"WS: Error saving aggregated analysis: {e}")
#                     else:
#                         print("WS: Error validating aggregated analysis:", session_serializer.errors)
#                 else:
#                     print("WS: No ChunkSentimentAnalysis objects found for aggregation.")
#                     session.session_status = 'completed'
#                     await asyncio.to_thread(session.save) # Save status even if no chunks
#                     print(f"WS: PracticeSession status updated to completed (no chunks).")

#             except PracticeSession.DoesNotExist:
#                 print(f"WS: Error: PracticeSession with id {self.session_id} not found for aggregation.")
#             except Exception as e:
#                 print(f"WS: Error during aggregation: {e}")
#             print(f"WS: aggregate_and_save_analysis finished after {time.time() - start_time:.2f} seconds")




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

from base64 import b64decode
from datetime import timedelta

from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async

from .sentiment_analysis import analyze_results
from practice_sessions.models import PracticeSession, SessionChunk, ChunkSentimentAnalysis
from practice_sessions.serializers import SessionChunkSerializer, ChunkSentimentAnalysisSerializer, PracticeSessionSerializer

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "engagex_backend.settings")
django.setup()

openai.api_key = os.environ.get("OPENAI_API_KEY")
client = openai.OpenAI()

s3 = boto3.client("s3", region_name=os.environ.get('AWS_REGION'))
BUCKET_NAME = "engagex-user-content-1234"
BASE_FOLDER = "user-videos/"
TEMP_MEDIA_ROOT = tempfile.gettempdir()

class LiveSessionConsumer(AsyncWebsocketConsumer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.session_id = None
        self.chunk_counter = 0
        self.media_buffer = []
        self.audio_buffer = []  # Initialize here
        self.chunk_paths = []

    async def connect(self):
        query_string = self.scope['query_string'].decode()
        query_params = {}
        if query_string:
            for param in query_string.split('&'):
                try:
                    key, value = param.split('=', 1)
                    query_params[key] = value
                except ValueError:
                    print(f"Warning: Could not parse query parameter: {param}")
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
        for file_path in self.audio_buffer:
            try:
                os.remove(file_path)
                print(f"WS: Removed temporary audio file: {file_path}")
            except Exception as e:
                print(f"WS: Error removing file: {e}")

        # Analyze any remaining chunks at the end of the session.
        if self.media_buffer:
            print(f"WS: Analyzing remaining {len(self.media_buffer)} chunks at the end of the session.")
            await self.analyze_windowed_media(list(self.media_buffer))

        if self.session_id:
            print(f"WS: Calling aggregate_and_save_analysis for session {self.session_id}")
            await self.aggregate_and_save_analysis()
        else:
            print("WS: Session ID not available during disconnect, skipping aggregation.")

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
                        # Log the received chunk details.
                        print(f"WS: Received media chunk {self.chunk_counter} for Session {self.session_id}. Saved to {media_path}")
                        self.media_buffer.append(media_path)
                        await self.send(json.dumps({
                            "status": "received",
                            "session_id": self.session_id,
                            "chunk_number": self.chunk_counter,
                            "media_type": "media"
                        }))
                        print(f"WS: Starting process_media_chunk for chunk {self.chunk_counter}")
                        asyncio.create_task(self.process_media_chunk(media_path))

                        # Trigger windowed analysis every 3 chunks.
                        if self.chunk_counter % 3 == 0 and self.chunk_counter >= 3:
                            window_paths = self.media_buffer[-3:]
                            print(f"WS: Triggering windowed analysis for chunks: {self.chunk_counter - 2} to {self.chunk_counter}")
                            asyncio.create_task(self.analyze_windowed_media(window_paths))
                    else:
                        print("WS: Error: Missing 'data' in media message.")
                else:
                    print(f"WS: Received text message of type: {message_type}")
            elif bytes_data:
                print(f"WS: Received binary data of length: {len(bytes_data)}")
        except Exception as e:
            print(f"WS: Error processing received data: {e}")

    async def process_media_chunk(self, media_path):
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
                    self.audio_buffer.append(audio_path)
                    if len(self.audio_buffer) > 9:
                        old = self.audio_buffer.pop(0)
                        try:
                            os.remove(old)
                            print(f"WS: Removed old audio buffer file: {old}")
                        except Exception as e:
                            print(f"WS: Error removing old audio file: {e}")
                else:
                    print("WS: Audio extraction failed.")

                for future in concurrent.futures.as_completed(futures):
                    try:
                        future.result()
                    except Exception as e:
                        print(f"WS: Task failed in process_media_chunk: {e}")

            # Save the chunk data.
            await asyncio.to_thread(self._save_chunk_data, media_path, None, None)
        except Exception as e:
            print(f"WS: Error in process_media_chunk: {e}")
        print(f"WS: process_media_chunk finished for: {media_path} after {time.time() - start_time:.2f} seconds")

    async def analyze_windowed_media(self, window_paths):
        if not window_paths:
            print("WS: analyze_windowed_media called with no media paths.")
            return

        start_time = time.time()
        print(f"WS: analyze_windowed_media started for window: {window_paths} at {start_time}")

        window_analyses = []
        for media_path in window_paths:
            audio_path = await asyncio.to_thread(self.extract_audio, media_path)
            if audio_path:
                try:
                    # change transcription
                    print(f"WS: Attempting transcription for window media: {media_path}")
                    transcription_start_time = time.time()
                    with open(audio_path, 'rb') as audio_file:
                        transcript = await asyncio.to_thread(
                            client.audio.transcriptions.create,
                            model="whisper-1",
                            file=audio_file
                        )
                    transcript_text = transcript.text
                    print(f"WS: Transcription Result for window media {media_path}: {transcript_text} after {time.time() - transcription_start_time:.2f} seconds")

                    if transcript_text:
                        print(f"WS: Running analyze_results for window media: {media_path} with audio: {audio_path}")
                        analysis_start_time = time.time()
                        analysis_result = await asyncio.to_thread(analyze_results, transcript_text, media_path, audio_path)
                        print(f"WS: Analysis Result for window media {media_path}: {analysis_result} after {time.time() - analysis_start_time:.2f} seconds")
                        window_analyses.append(analysis_result)

                        # Send real-time feedback.
                        feedback_data = {
                            "type": "realtime_feedback",
                            "engagement": analysis_result.get('Feedback', {}).get('Engagement'),
                            "audience_emotion": analysis_result.get('Feedback', {}).get('Audience Emotion'),
                            "impact": analysis_result.get('Feedback', {}).get('Impact'),
                            "clarity": analysis_result.get('Feedback', {}).get('Clarity'),
                            "Body Posture": analysis_result.get('Posture', {}).get('Body Posture'), # example call for body posture
                            "transformative_potential": analysis_result.get('Feedback', {}).get('Transformative Potential'),
                            "volume": analysis_result.get('Scores', {}).get('Volume Score'),
                            "pace": analysis_result.get('Scores', {}).get('Pace Score'),
                        }
                        print("WS: Sending Real-time Feedback for chunk in window:", feedback_data)
                        await self.send(json.dumps(feedback_data))

                        # Save analysis for each chunk in the window.
                        await asyncio.to_thread(self._save_window_analysis, media_path, analysis_result, transcript_text)
                    else:
                        print(f"WS: No transcript for window media: {media_path}")
                except Exception as e:
                    print(f"WS: Error analyzing window media {media_path}: {e}")
            else:
                print(f"WS: Error extracting audio for window media: {media_path}")

        print(f"WS: analyze_windowed_media finished at {time.time() - start_time:.2f} seconds")

    def _save_window_analysis(self, media_path, analysis_result, transcript_text):
        start_time = time.time()
        print(f"WS: _save_window_analysis started for media path: {media_path} at {start_time}")
        if not self.session_id:
            print("WS: Error: Session ID not available, cannot save window analysis.")
            return

        try:
            file_name = os.path.basename(media_path)
            print(f"WS: Looking for SessionChunk with video_file containing: {file_name}")
            session_chunks = SessionChunk.objects.filter(session_id=self.session_id, video_file__icontains=file_name)
            if session_chunks.exists():
                session_chunk = session_chunks.first()  # Assuming one chunk per file
                print(f"WS: Found SessionChunk for window analysis with video_file containing: {file_name}, ID: {session_chunk.id}")

                sentiment_data = {
                    'chunk': session_chunk.id,
                    'engagement': analysis_result.get('Feedback', {}).get('Engagement', 0),
                    'audience_emotion': analysis_result.get('Feedback', {}).get('Audience Emotion', 0),
                    'conviction': analysis_result.get('Feedback', {}).get('Conviction', 0),
                    'clarity': analysis_result.get('Feedback', {}).get('Clarity', 0),
                    'impact': analysis_result.get('Feedback', {}).get('Impact', 0),
                    'brevity': analysis_result.get('Feedback', {}).get('Brevity', 0),
                    'transformative_potential': analysis_result.get('Feedback', {}).get('Transformative Potential', 0),
                    'trigger_response': analysis_result.get('Feedback', {}).get('Trigger Response', 0),
                    'filler_words': analysis_result.get('Feedback', {}).get('Filler Words', 0),
                    'grammar': analysis_result.get('Feedback', {}).get('Grammar', 0),
                    'general_feedback_summary': analysis_result.get('Feedback', {}).get('General Feedback Summary', ''),
                    'posture': analysis_result.get('Posture Scores', {}).get('Posture', 0),
                    'motion': analysis_result.get('Posture Scores', {}).get('Motion', 0),
                    'gestures': analysis_result.get('Posture Scores', {}).get('Gestures', 0),
                    'volume': analysis_result.get('Scores', {}).get('Volume Score'),
                    'pitch_variability': analysis_result.get('Scores', {}).get('Pitch Variability Score'),
                    'pace': analysis_result.get('Scores', {}).get('Pace Score'),
                    'chunk_transcript': transcript_text,
                }
                print(f"WS: ChunkSentimentAnalysis data (for window): {sentiment_data}")
                sentiment_serializer = ChunkSentimentAnalysisSerializer(data=sentiment_data)
                if sentiment_serializer.is_valid():
                    print("WS: ChunkSentimentAnalysisSerializer (for window) is valid.")
                    try:
                        sentiment_analysis_obj = sentiment_serializer.save()
                        print(f"WS: Window analysis data saved with ID: {sentiment_analysis_obj.id} after {time.time() - start_time:.2f} seconds")
                    except Exception as save_error:
                        print(f"WS: Error during ChunkSentimentAnalysis save (for window): {save_error}")
                else:
                    print("WS: Error saving ChunkSentimentAnalysis:", sentiment_serializer.errors)
            else:
                error_message = f"SessionChunk not found for session {self.session_id} and media path {media_path} during window analysis save."
                print(f"WS: {error_message}")
        except Exception as e:
            print(f"WS: Error in _save_window_analysis: {e}")
        print(f"WS: _save_window_analysis finished after {time.time() - start_time:.2f} seconds")

    def extract_audio(self, media_path):
        start_time = time.time()
        base, _ = os.path.splitext(media_path)
        audio_mp3_path = f"{base}.mp3"
        ffmpeg_command = f"ffmpeg -y -i {media_path} -vn -acodec libmp3lame -ab 128k {audio_mp3_path}"
        print(f"WS: Running FFmpeg command: {ffmpeg_command}")
        process = subprocess.Popen(ffmpeg_command, shell=True, stderr=subprocess.PIPE, stdout=subprocess.PIPE)
        stdout, stderr = process.communicate()
        if process.returncode == 0:
            return audio_mp3_path
        else:
            error_output = stderr.decode()
            print(f"WS: FFmpeg audio extraction error (code {process.returncode}): {error_output}")
            return None

    def upload_to_s3(self, file_path):
        start_time = time.time()
        file_name = os.path.basename(file_path)
        folder_path = f"{BASE_FOLDER}{self.session_id}/"
        s3_key = f"{folder_path}{file_name}"
        try:
            s3.upload_file(file_path, BUCKET_NAME, s3_key)
            s3_url = f"s3://{BUCKET_NAME}/{s3_key}"
            print(f"WS: Uploaded {file_path} to S3 successfully. S3 URL: {s3_url} after {time.time() - start_time:.2f} seconds.")
            return s3_url
        except Exception as e:
            print(f"WS: S3 upload failed for {file_path}: {e}")
            return None

    def _save_chunk_data(self, media_path, analysis_result, transcript_text):
        start_time = time.time()
        print(f"WS: _save_chunk_data called for chunk at {media_path} at {start_time}")
        if not self.session_id:
            print("WS: Error: Session ID not available, cannot save chunk data.")
            return

        try:
            print(f"WS: Attempting to get PracticeSession with id: {self.session_id}")
            session = PracticeSession.objects.get(id=self.session_id)
            print(f"WS: Retrieved PracticeSession: {session.id}, {session.session_name}")
            
            print(f"WS: Attempting to upload media to S3 for SessionChunk")
            s3_upload_start_time = time.time()
            s3_url = self.upload_to_s3(media_path)
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
                        session_chunk = session_chunk_serializer.save()
                        print(f"WS: SessionChunk saved with ID: {session_chunk.id} after {time.time() - start_time:.2f} seconds")
                    except Exception as save_error:
                        print(f"WS: Error during SessionChunk save: {save_error}")
                else:
                    print("WS: Error saving SessionChunk:", session_chunk_serializer.errors)
            else:
                print("WS: Error: S3 URL not obtained, cannot save SessionChunk.")
        except PracticeSession.DoesNotExist:
            print(f"WS: Error: PracticeSession with id {self.session_id} not found.")
        except Exception as e:
            print(f"WS: Error in _save_chunk_data: {e}")
        print(f"WS: _save_chunk_data finished after {time.time() - start_time:.2f} seconds")

    async def aggregate_and_save_analysis(self):
        start_time = time.time()
        print(f"WS: aggregate_and_save_analysis called for session {self.session_id} at {start_time}")
        if not self.session_id:
            print("WS: Error: Session ID not available, cannot aggregate analysis.")
            return

        try:
            session = await asyncio.to_thread(PracticeSession.objects.get, id=self.session_id)
            all_chunk_sentiments = await asyncio.to_thread(list, ChunkSentimentAnalysis.objects.filter(chunk__session=session))
            print(f"WS: Retrieved {len(all_chunk_sentiments)} ChunkSentimentAnalysis objects for aggregation.")

            if all_chunk_sentiments:
                total_engagement = total_conviction = total_clarity = total_impact = 0
                total_brevity = total_transformative_potential = total_body_posture = 0
                total_volume = total_pitch_variability = total_pace = 0
                emotion_counts = {}
                all_summaries = []

                for sentiment in all_chunk_sentiments:
                    total_engagement += sentiment.engagement
                    total_conviction += sentiment.conviction
                    total_clarity += sentiment.clarity
                    total_impact += sentiment.impact
                    total_brevity += sentiment.brevity
                    total_transformative_potential += sentiment.transformative_potential
                    total_body_posture += sentiment.body_posture
                    total_volume += sentiment.volume if sentiment.volume is not None else 0
                    total_pitch_variability += sentiment.pitch_variability if sentiment.pitch_variability is not None else 0
                    total_pace += sentiment.pace if sentiment.pace is not None else 0
                    emotion = sentiment.audience_emotion
                    emotion_counts[emotion] = emotion_counts.get(emotion, 0) + 1
                    if sentiment.general_feedback_summary:
                        all_summaries.append(sentiment.general_feedback_summary)

                num_chunks = len(all_chunk_sentiments)
                average_engagement = total_engagement / num_chunks if num_chunks > 0 else 0
                average_conviction = total_conviction / num_chunks if num_chunks > 0 else 0
                average_clarity = total_clarity / num_chunks if num_chunks > 0 else 0
                average_impact = total_impact / num_chunks if num_chunks > 0 else 0
                average_brevity = total_brevity / num_chunks if num_chunks > 0 else 0
                average_transformative_potential = total_transformative_potential / num_chunks if num_chunks > 0 else 0
                average_body_posture = total_body_posture / num_chunks if num_chunks > 0 else 0
                average_volume = total_volume / num_chunks if num_chunks > 0 else 0
                average_pitch_variability = total_pitch_variability / num_chunks if num_chunks > 0 else 0
                average_pace = total_pace / num_chunks if num_chunks > 0 else 0

                dominant_emotion = max(emotion_counts, key=emotion_counts.get) if emotion_counts else None
                overall_summary = " ".join(all_summaries)

                aggregated_data = {
                    'average_engagement': average_engagement,
                    'dominant_audience_emotion': dominant_emotion,
                    'average_conviction': average_conviction,
                    'average_clarity': average_clarity,
                    'average_impact': average_impact,
                    'average_brevity': average_brevity,
                    'average_transformative_potential': average_transformative_potential,
                    'average_body_posture': average_body_posture,
                    'overall_feedback_summary': overall_summary,
                    'average_volume': average_volume,
                    'average_pitch_variability': average_pitch_variability,
                    'average_pace': average_pace,
                    'session_status': 'completed'
                }
                print(f"WS: Aggregated data: {aggregated_data}")
                session_serializer = PracticeSessionSerializer(session, data=aggregated_data, partial=True)
                if session_serializer.is_valid():
                    print("WS: PracticeSessionSerializer (for aggregation) is valid.")
                    try:
                        await asyncio.to_thread(session_serializer.save)
                        print(f"WS: Aggregated analysis saved to PracticeSession ID: {session.id} after {time.time() - start_time:.2f} seconds")
                    except Exception as e:
                        print(f"WS: Error saving aggregated analysis: {e}")
                else:
                    print("WS: Error validating aggregated analysis:", session_serializer.errors)
            else:
                print("WS: No ChunkSentimentAnalysis objects found for aggregation.")
                session.session_status = 'completed'
                await asyncio.to_thread(session.save)
                print(f"WS: PracticeSession status updated to completed (no chunks).")
        except PracticeSession.DoesNotExist:
            print(f"WS: Error: PracticeSession with id {self.session_id} not found for aggregation.")
        except Exception as e:
            print(f"WS: Error during aggregation: {e}")
        print(f"WS: aggregate_and_save_analysis finished after {time.time() - start_time:.2f} seconds")
