# import json
# import os
# import asyncio
# import tempfile
# import concurrent.futures
# import subprocess
# import boto3
# import openai
# import django

# from base64 import b64decode
# from datetime import timedelta

# from channels.generic.websocket import AsyncWebsocketConsumer
# from channels.db import database_sync_to_async

# from .sentiment_analysis import analyze_results
# from practice_sessions.models import PracticeSession, SessionChunk, ChunkSentimentAnalysis
# from practice_sessions.serializers import SessionChunkSerializer, ChunkSentimentAnalysisSerializer

# os.environ.setdefault("DJANGO_SETTINGS_MODULE", "engagex_backend.settings")
# django.setup()


# openai.api_key = os.environ.get("OPENAI_API_KEY")
# client = openai.OpenAI()

# s3 = boto3.client("s3", region_name=os.environ.get('AWS_REGION'))
# BUCKET_NAME = "engagex-user-content-1234"
# BASE_FOLDER = "user-videos/"
# TEMP_MEDIA_ROOT = tempfile.gettempdir()

# class LiveSessionConsumer(AsyncWebsocketConsumer):
#     async def connect(self):
#         self.session_id = self.scope['query_string'].decode().split('=')[1] if 'session_id' in self.scope['query_string'].decode() else None
#         if self.session_id:
#             print(f"WS: Client connected for Session ID: {self.session_id}")
#             await self.accept()
#             self.chunk_counter = 0
#             self.audio_buffer = []
#             self.chunk_paths = [] # Initialize a list to store chunk paths
#             await self.send(json.dumps({
#                 "type": "connection_established",
#                 "message": f"Connected to session {self.session_id}"
#             }))
#         else:
#             print("WS: Connection rejected: Missing session_id.")
#             await self.close()

#     async def disconnect(self, close_code):
#         print(f"WS: Client disconnected for Session ID: {self.session_id}. Cleaning up...")
#         # Cleanup audio buffer files.
#         for file_path in self.audio_buffer:
#             try:
#                 os.remove(file_path)
#                 print(f"WS: Removed temporary audio file: {file_path}")
#             except Exception as e:
#                 print(f"WS: Error removing file: {e}")

#         # Aggregate and save final analysis to PracticeSession
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
#                         print(f"WS: Received media chunk {self.chunk_counter} for Session {self.session_id}. Saved to {media_path}")
#                         self.chunk_paths.append(media_path) # Store the path of the saved chunk
#                         await self.send(json.dumps({
#                             "status": "received",
#                             "session_id": self.session_id,
#                             "chunk_number": self.chunk_counter,
#                             "media_type": "media"
#                         }))
#                         # Process the media file asynchronously.
#                         print(f"WS: Starting process_media_chunk for chunk {self.chunk_counter}")
#                         asyncio.create_task(self.process_media_chunk(media_path))
#                     else:
#                         print("WS: Error: Missing 'data' in media message.")
#                 else:
#                     print(f"WS: Received text message of type: {message_type}")
#             elif bytes_data:
#                 print(f"WS: Received binary data of length: {len(bytes_data)}")
#         except Exception as e:
#             print(f"WS: Error processing received data: {e}")

#     async def process_media_chunk(self, media_path):
#         print(f"WS: process_media_chunk started for: {media_path}")
#         audio_path = None
#         try:
#             with concurrent.futures.ThreadPoolExecutor() as executor:
#                 futures = []
#                 # Upload media file in parallel.
#                 print(f"WS: Submitting upload_to_s3 for: {media_path}")
#                 futures.append(executor.submit(self.upload_to_s3, media_path))
#                 # Extract audio from the media file.
#                 print(f"WS: Calling extract_audio for: {media_path}")
#                 audio_path = await asyncio.to_thread(self.extract_audio, media_path)
#                 if audio_path:
#                     print(f"WS: Audio extracted to: {audio_path}")
#                     self.audio_buffer.append(audio_path)
#                     # Limit the buffer to the last 3 audio segments.
#                     if len(self.audio_buffer) > 3:
#                         old = self.audio_buffer.pop(0)
#                         try:
#                             os.remove(old)
#                             print(f"WS: Removed old audio buffer file: {old}")
#                         except Exception as e:
#                             print(f"WS: Error removing old audio file: {e}")
#                     # Analyze the chunk
#                     await self.analyze_chunk(media_path, audio_path)
#                 else:
#                     print("WS: Audio extraction failed.")
#                 for future in concurrent.futures.as_completed(futures):
#                     try:
#                         future.result()
#                     except Exception as e:
#                         print(f"WS: Task failed in process_media_chunk: {e}")
#         finally:
#             # Clean up audio file if extraction failed or after processing
#             if audio_path and os.path.exists(audio_path):
#                 try:
#                     os.remove(audio_path)
#                     print(f"WS: Cleaned up temporary audio file: {audio_path}")
#                 except Exception as e:
#                     print(f"WS: Error during cleanup of audio file: {e}")
#         print(f"WS: process_media_chunk finished for: {media_path}")

#     async def analyze_chunk(self, media_path, audio_path):
#         print(f"WS: analyze_chunk started for: {media_path}")
#         transcript_text = None
#         try:
#             if audio_path and os.path.exists(audio_path):
#                 print(f"WS: Attempting transcription for chunk: {audio_path}")
#                 with open(audio_path, 'rb') as audio_file:
#                     transcript = await asyncio.to_thread(client.audio.transcriptions.create,
#                         model="whisper-1",
#                         file=audio_file
#                     )
#                     transcript_text = transcript.text
#                     print("WS: Transcription Result for chunk:", transcript_text)
#             else:
#                 print("WS: Audio file not found for transcription.")
#         except Exception as e:
#             print(f"WS: Error during transcription for chunk: {e}")

#         if transcript_text:
#             print("WS: Running analyze_results for chunk...")
#             analysis_result = analyze_results(transcript_text, media_path, audio_path)
#             print("WS: Analysis Result for chunk:", analysis_result)

#             # Debugging logs for analysis_result
#             print("WS: Analysis Result (inside analyze_chunk):", analysis_result)

#             # Send real-time feedback to the frontend
#             feedback_data = {
#                 "type": "realtime_feedback",
#                 "engagement": analysis_result.get('Feedback', {}).get('Engagement'),
#                 "audience_emotion": analysis_result.get('Feedback', {}).get('Audience Emotion'),
#                 "conviction": analysis_result.get('Feedback', {}).get('Conviction'),
#                 "clarity": analysis_result.get('Feedback', {}).get('Clarity'),
#                 "impact": analysis_result.get('Feedback', {}).get('Impact'),
#                 "brevity": analysis_result.get('Feedback', {}).get('Brevity'),
#                 "transformative_potential": analysis_result.get('Feedback', {}).get('Transformative Potential'),
#                 "body_posture": analysis_result.get('Feedback', {}).get('Body Posture'),
#                 "general_feedback_summary": analysis_result.get('Feedback', {}).get('General Feedback Summary'),
#                 "volume": analysis_result.get('Scores', {}).get('Volume Score'),
#                 "pace": analysis_result.get('Scores', {}).get('Pace Score'),
#                 "pitch_variability": analysis_result.get('Scores', {}).get('Pitch Variability Score'),
#             }
#             print("WS: Sending Real-time Feedback:", feedback_data)
#             await self.send(json.dumps(feedback_data))

#             await self.save_chunk_data(media_path, analysis_result, transcript_text)
#         else:
#             print("WS: No transcript available for chunk, skipping sentiment analysis and data saving.")
#         print(f"WS: analyze_chunk finished for: {media_path}")

#     def extract_audio(self, media_path):
#         base, _ = os.path.splitext(media_path)
#         audio_mp3_path = f"{base}.mp3"
#         ffmpeg_command = f"ffmpeg -y -i {media_path} -vn -acodec libmp3lame -ab 128k {audio_mp3_path}"
#         print(f"WS: Running FFmpeg command: {ffmpeg_command}")
#         process = subprocess.Popen(ffmpeg_command, shell=True, stderr=subprocess.PIPE, stdout=subprocess.PIPE)
#         stdout, stderr = process.communicate()
#         if process.returncode == 0:
#             print(f"WS: Extracted audio to {audio_mp3_path}")
#             return audio_mp3_path
#         else:
#             error_output = stderr.decode()
#             print(f"WS: FFmpeg audio extraction error (code {process.returncode}): {error_output}")
#             return None

#     def combine_audio_files(self, audio_files):
#         if not audio_files:
#             print("WS: No audio files to combine.")
#             return None
#         list_path = os.path.join(TEMP_MEDIA_ROOT, f"{self.session_id}_audio_list.txt")
#         with open(list_path, "w") as f:
#             for file_path in audio_files:
#                 f.write(f"file '{file_path}'\n")
#         combined_path = os.path.join(TEMP_MEDIA_ROOT, f"{self.session_id}_combined.mp3")
#         ffmpeg_command = f"ffmpeg -y -f concat -safe 0 -i {list_path} -c copy {combined_path}"
#         print(f"WS: Running FFmpeg combine command: {ffmpeg_command}")
#         process = subprocess.Popen(ffmpeg_command, shell=True, stderr=subprocess.PIPE, stdout=subprocess.PIPE)
#         stdout, stderr = process.communicate()
#         if process.returncode == 0:
#             print(f"WS: Combined audio file created at {combined_path}")
#             os.remove(list_path)
#             return combined_path
#         else:
#             error_output = stderr.decode()
#             print(f"WS: FFmpeg audio combining error (code {process.returncode}): {error_output}")
#             return None

#     def upload_to_s3(self, file_path):
#         file_name = os.path.basename(file_path)
#         folder_path = f"{BASE_FOLDER}{self.session_id}/"
#         s3_key = f"{folder_path}{file_name}"
#         try:
#             print(f"WS: Uploading {file_path} to S3 at s3://{BUCKET_NAME}/{s3_key}")
#             s3.upload_file(file_path, BUCKET_NAME, s3_key)
#             print(f"WS: Uploaded {file_path} to S3 successfully.")
#             return f"s3://{BUCKET_NAME}/{s3_key}" # Return the S3 URL
#         except Exception as e:
#             print(f"WS: S3 upload failed for {file_path}: {e}")
#             return None

#     async def save_chunk_data(self, media_path, analysis_result, transcript_text):
#         print(f"WS: save_chunk_data called for chunk {self.chunk_counter}")
#         if not self.session_id:
#             print("WS: Error: Session ID not available, cannot save chunk data.")
#             return

#         try:
#             async def _save_data():
#                 print(f"WS: _save_data started")
#                 print(f"WS: Attempting to get PracticeSession with id: {self.session_id}")
#                 session = await asyncio.to_thread(PracticeSession.objects.get, id=self.session_id)
#                 print(f"WS: Retrieved PracticeSession: {session.id}, {session.session_name}")

#                 # Save SessionChunk
#                 print(f"WS: Attempting to upload media to S3 for SessionChunk")
#                 s3_url = await asyncio.to_thread(self.upload_to_s3, media_path)
#                 if s3_url:
#                     print(f"WS: S3 URL for SessionChunk: {s3_url}")
#                     session_chunk_data = {
#                         'session': session.id,
#                         'video_file': s3_url
#                     }
#                     print(f"WS: SessionChunk data: {session_chunk_data}")
#                     session_chunk_serializer = SessionChunkSerializer(data=session_chunk_data)
#                     if session_chunk_serializer.is_valid():
#                         print("WS: SessionChunkSerializer is valid.")
#                         session_chunk = await asyncio.to_thread(session_chunk_serializer.save)
#                         print(f"WS: SessionChunk saved with ID: {session_chunk.id}")

#                         # Save ChunkSentimentAnalysis
#                         sentiment_data = {
#                             'chunk': session_chunk.id,
#                             'engagement': analysis_result.get('Feedback', {}).get('Engagement', 0),
#                             'audience_emotion': analysis_result.get('Feedback', {}).get('Audience Emotion', ''),
#                             'conviction': analysis_result.get('Feedback', {}).get('Conviction', 0),
#                             'clarity': analysis_result.get('Feedback', {}).get('Clarity', 0),
#                             'impact': analysis_result.get('Feedback', {}).get('Impact', 0),
#                             'brevity': analysis_result.get('Feedback', {}).get('Brevity', 0),
#                             'transformative_potential': analysis_result.get('Feedback', {}).get('Transformative Potential', 0),
#                             'body_posture': analysis_result.get('Feedback', {}).get('Body Posture', 0),
#                             'general_feedback_summary': analysis_result.get('Feedback', {}).get('General Feedback Summary', ''),
#                             'volume': analysis_result.get('Scores', {}).get('Volume Score'),
#                             'pitch_variability': analysis_result.get('Scores', {}).get('Pitch Variability Score'),
#                             'pace': analysis_result.get('Scores', {}).get('Pace Score'),
#                             'chunk_transcript': transcript_text,
#                         }
#                         print(f"WS: ChunkSentimentAnalysis data: {sentiment_data}")
#                         sentiment_serializer = ChunkSentimentAnalysisSerializer(data=sentiment_data)
#                         if sentiment_serializer.is_valid():
#                             print("WS: ChunkSentimentAnalysisSerializer is valid.")
#                             try:
#                                 sentiment_analysis_obj = await asyncio.to_thread(sentiment_serializer.save)
#                                 print(f"WS: Chunk {self.chunk_counter} sentiment data saved with ID: {sentiment_analysis_obj.id}")
#                             except Exception as save_error:
#                                 print(f"WS: Error during ChunkSentimentAnalysis save(): {save_error}")
#                         else:
#                             print("WS: Error saving ChunkSentimentAnalysis:", sentiment_serializer.errors)
#                     else:
#                         print("WS: Error saving SessionChunk:", session_chunk_serializer.errors)
#                 else:
#                     print("WS: Error: S3 URL not obtained, cannot save SessionChunk.")
#                 print(f"WS: _save_data finished")

#             await asyncio.to_thread(_save_data)

#         except PracticeSession.DoesNotExist:
#             print(f"WS: Error: PracticeSession with id {self.session_id} not found.")
#         except Exception as e:
#             print(f"WS: Error saving chunk data: {e}")

#     async def aggregate_and_save_analysis(self):
#         print(f"WS: aggregate_and_save_analysis called for session {self.session_id}")
#         if not self.session_id:
#             print("WS: Error: Session ID not available, cannot aggregate analysis.")
#             return

#         try:
#             print(f"WS: Attempting to get PracticeSession with id: {self.session_id} for aggregation")
#             session = await asyncio.to_thread(PracticeSession.objects.get, id=self.session_id)
#             print(f"WS: Retrieved PracticeSession for aggregation: {session.id}, {session.session_name}")
#             print(f"WS: Attempting to get ChunkSentimentAnalysis objects for session {self.session_id}")

#             @database_sync_to_async
#             def get_chunk_sentiments(session_obj):
#                 return list(ChunkSentimentAnalysis.objects.filter(chunk__session=session_obj))

#             all_chunk_sentiments = await get_chunk_sentiments(session)
#             print(f"WS: Retrieved {len(all_chunk_sentiments)} ChunkSentimentAnalysis objects.")

#             pauses = 0 # You'll need to determine how to count pauses
#             tone = "" # You'll need logic to aggregate tone
#             impact_scores = [item.impact for item in all_chunk_sentiments if item.impact is not None]
#             audience_engagement_scores = [item.engagement for item in all_chunk_sentiments if item.engagement is not None]
#             # Add logic to aggregate other relevant fields

#             aggregated_impact = sum(impact_scores) / len(impact_scores) if impact_scores else None
#             aggregated_engagement = sum(audience_engagement_scores) / len(audience_engagement_scores) if audience_engagement_scores else None

#             print(f"WS: Aggregated impact: {aggregated_impact}, engagement: {aggregated_engagement}")

#             await asyncio.to_thread(PracticeSession.objects.filter(id=self.session_id).update,
#                 pauses=pauses,
#                 tone=tone,
#                 impact=aggregated_impact,
#                 audience_engagement=aggregated_engagement,
#                 # Update other aggregated fields as needed
#                 duration=timedelta(seconds=self.chunk_counter * 10) # Update the total duration based on timeslice
#             )
#             print(f"WS: Aggregated analysis saved for session {self.session_id}.")

#         except PracticeSession.DoesNotExist:
#             print(f"WS: Error: PracticeSession with id {self.session_id} not found for aggregation.")
#         except Exception as e:
#             print(f"WS: Error during analysis aggregation: {e}")



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
from practice_sessions.serializers import SessionChunkSerializer, ChunkSentimentAnalysisSerializer

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "engagex_backend.settings")
django.setup()


openai.api_key = os.environ.get("OPENAI_API_KEY")
client = openai.OpenAI()

s3 = boto3.client("s3", region_name=os.environ.get('AWS_REGION'))
BUCKET_NAME = "engagex-user-content-1234"
BASE_FOLDER = "user-videos/"
TEMP_MEDIA_ROOT = tempfile.gettempdir()

class LiveSessionConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.session_id = self.scope['query_string'].decode().split('=')[1] if 'session_id' in self.scope['query_string'].decode() else None
        if self.session_id:
            print(f"WS: Client connected for Session ID: {self.session_id}")
            await self.accept()
            self.chunk_counter = 0
            self.audio_buffer = []
            self.chunk_paths = []
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
                        print(f"WS: Received media chunk {self.chunk_counter} for Session {self.session_id}. Saved to {media_path}")
                        self.chunk_paths.append(media_path)
                        await self.send(json.dumps({
                            "status": "received",
                            "session_id": self.session_id,
                            "chunk_number": self.chunk_counter,
                            "media_type": "media"
                        }))
                        print(f"WS: Starting process_media_chunk for chunk {self.chunk_counter}")
                        asyncio.create_task(self.process_media_chunk(media_path))
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
                        if len(self.audio_buffer) > 3:
                            old = self.audio_buffer.pop(0)
                            try:
                                os.remove(old)
                                print(f"WS: Removed old audio buffer file: {old}")
                            except Exception as e:
                                print(f"WS: Error removing old audio file: {e}")
                        print(f"WS: Starting analyze_chunk_async for: {media_path}, {audio_path}")
                        asyncio.create_task(self.analyze_chunk_async(media_path, audio_path))
                    else:
                        print("WS: Audio extraction failed.")
                    for future in concurrent.futures.as_completed(futures):
                        try:
                            future.result()
                        except Exception as e:
                            print(f"WS: Task failed in process_media_chunk: {e}")
            except Exception as e:
                print(f"WS: Error in process_media_chunk: {e}")
            print(f"WS: process_media_chunk finished for: {media_path} after {time.time() - start_time:.2f} seconds")

    async def analyze_chunk_async(self, media_path, audio_path):
        start_time = time.time()
        print(f"WS: analyze_chunk_async started for: {media_path}, {audio_path} at {start_time}")
        transcript_text = None
        analysis_result = None
        try:
            if audio_path and os.path.exists(audio_path):
                print(f"WS: Attempting transcription for chunk: {audio_path}")
                transcription_start_time = time.time()
                with open(audio_path, 'rb') as audio_file:
                    transcript = await asyncio.to_thread(client.audio.transcriptions.create,
                        model="whisper-1",
                        file=audio_file
                    )
                    transcript_text = transcript.text
                    print(f"WS: Transcription Result for chunk: {transcript_text} after {time.time() - transcription_start_time:.2f} seconds")
            else:
                print(f"WS: Audio file not found for transcription at path: {audio_path}")
        except Exception as e:
            print(f"WS: Error during transcription for chunk: {e}")

        if transcript_text:
            print("WS: Running analyze_results for chunk...")
            analysis_start_time = time.time()
            analysis_result = await asyncio.to_thread(analyze_results, transcript_text, media_path, audio_path)
            print(f"WS: Analysis Result for chunk: {analysis_result} after {time.time() - analysis_start_time:.2f} seconds")

            # Send the six required data points in real-time feedback
            feedback_data = {
                "type": "realtime_feedback",
                "engagement": analysis_result.get('Feedback', {}).get('Engagement'),
                "audience_emotion": analysis_result.get('Feedback', {}).get('Audience Emotion'),
                "impact": analysis_result.get('Feedback', {}).get('Impact'),
                "clarity": analysis_result.get('Feedback', {}).get('Clarity'),
                "transformative_potential": analysis_result.get('Feedback', {}).get('Transformative Potential'),
                "volume": analysis_result.get('Scores', {}).get('Volume Score'),
                "pace": analysis_result.get('Scores', {}).get('Pace Score'),
            }
            print("WS: Sending Real-time Feedback:", feedback_data)
            await self.send(json.dumps(feedback_data))

            await self.save_chunk_data(media_path, analysis_result, transcript_text)
        else:
            print("WS: No transcript available for chunk, skipping sentiment analysis and data saving.")

        # Clean up the audio file *after* analysis
        if audio_path and os.path.exists(audio_path):
            try:
                os.remove(audio_path)
                print(f"WS: Cleaned up temporary audio file: {audio_path}")
            except Exception as e:
                print(f"WS: Error during cleanup of audio file: {e}")

        print(f"WS: analyze_chunk_async finished for: {media_path} after {time.time() - start_time:.2f} seconds")

    def extract_audio(self, media_path):
        start_time = time.time()
        base, _ = os.path.splitext(media_path)
        audio_mp3_path = f"{base}.mp3"
        ffmpeg_command = f"ffmpeg -y -i {media_path} -vn -acodec libmp3lame -ab 128k {audio_mp3_path}"
        print(f"WS: Running FFmpeg command: {ffmpeg_command}")
        process = subprocess.Popen(ffmpeg_command, shell=True, stderr=subprocess.PIPE, stdout=subprocess.PIPE)
        stdout, stderr = process.communicate()
        if process.returncode == 0:
            print(f"WS: Extracted audio to {audio_mp3_path} after {time.time() - start_time:.2f} seconds")
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
            print(f"WS: Uploading {file_path} to S3 at s3://{BUCKET_NAME}/{s3_key}")
            s3.upload_file(file_path, BUCKET_NAME, s3_key)
            print(f"WS: Uploaded {file_path} to S3 successfully after {time.time() - start_time:.2f} seconds.")
            return f"s3://{BUCKET_NAME}/{s3_key}"
        except Exception as e:
            print(f"WS: S3 upload failed for {file_path}: {e}")
            return None

    async def save_chunk_data(self, media_path, analysis_result, transcript_text):
        start_time = time.time()
        print(f"WS: save_chunk_data called for chunk {self.chunk_counter} at {start_time}")
        if not self.session_id:
            print("WS: Error: Session ID not available, cannot save chunk data.")
            return

        try:
            async def _save_data():
                _save_data_start_time = time.time()
                print(f"WS: _save_data started at {_save_data_start_time}")
                print(f"WS: Attempting to get PracticeSession with id: {self.session_id}")
                session = await asyncio.to_thread(PracticeSession.objects.get, id=self.session_id)
                print(f"WS: Retrieved PracticeSession: {session.id}, {session.session_name} after {time.time() - _save_data_start_time:.2f} seconds")

                print(f"WS: Attempting to upload media to S3 for SessionChunk")
                s3_url = await asyncio.to_thread(self.upload_to_s3, media_path)
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
                        session_chunk_save_start_time = time.time()
                        session_chunk = await asyncio.to_thread(session_chunk_serializer.save)
                        print(f"WS: SessionChunk saved with ID: {session_chunk.id} after {time.time() - session_chunk_save_start_time:.2f} seconds")

                        sentiment_data = {
                            'chunk': session_chunk.id,
                            'engagement': analysis_result.get('Feedback', {}).get('Engagement', 0),
                            'audience_emotion': analysis_result.get('Feedback', {}).get('Audience Emotion', ''),
                            'conviction': analysis_result.get('Feedback', {}).get('Conviction', 0),
                            'clarity': analysis_result.get('Feedback', {}).get('Clarity', 0),
                            'impact': analysis_result.get('Feedback', {}).get('Impact', 0),
                            'brevity': analysis_result.get('Feedback', {}).get('Brevity', 0),
                            'transformative_potential': analysis_result.get('Feedback', {}).get('Transformative Potential', 0),
                            'body_posture': analysis_result.get('Feedback', {}).get('Body Posture', 0),
                            'general_feedback_summary': analysis_result.get('Feedback', {}).get('General Feedback Summary', ''),
                            'volume': analysis_result.get('Scores', {}).get('Volume Score'),
                            'pitch_variability': analysis_result.get('Scores', {}).get('Pitch Variability Score'),
                            'pace': analysis_result.get('Scores', {}).get('Pace Score'),
                            'chunk_transcript': transcript_text,
                        }
                        print(f"WS: ChunkSentimentAnalysis data: {sentiment_data}")
                        sentiment_serializer = ChunkSentimentAnalysisSerializer(data=sentiment_data)
                        if sentiment_serializer.is_valid():
                            print("WS: ChunkSentimentAnalysisSerializer is valid.")
                            try:
                                sentiment_analysis_save_start_time = time.time()
                                sentiment_analysis_obj = await asyncio.to_thread(sentiment_serializer.save)
                                print(f"WS: Chunk {self.chunk_counter} sentiment data saved with ID: {sentiment_analysis_obj.id} after {time.time() - sentiment_analysis_save_start_time:.2f} seconds")
                            except Exception as save_error:
                                print(f"WS: Error during ChunkSentimentAnalysis save(): {save_error}")
                        else:
                            print("WS: Error saving ChunkSentimentAnalysis:", sentiment_serializer.errors)
                    else:
                        print("WS: Error saving SessionChunk:", session_chunk_serializer.errors)
                else:
                    print("WS: Error: S3 URL not obtained, cannot save SessionChunk.")
                print(f"WS: _save_data finished after {time.time() - _save_data_start_time:.2f} seconds")

            await asyncio.to_thread(_save_data)

        except PracticeSession.DoesNotExist:
            print(f"WS: Error: PracticeSession with id {self.session_id} not found.")
        except Exception as e:
            print(f"WS: Error saving chunk data: {e}")
        print(f"WS: save_chunk_data finished after {time.time() - start_time:.2f} seconds")

    async def aggregate_and_save_analysis(self):
        print(f"WS: aggregate_and_save_analysis called for session {self.session_id}")
        if not self.session_id:
            print("WS: Error: Session ID not available, cannot aggregate analysis.")
            return

        try:
            print(f"WS: Attempting to get PracticeSession with id: {self.session_id} for aggregation")
            session = await asyncio.to_thread(PracticeSession.objects.get, id=self.session_id)
            print(f"WS: Retrieved PracticeSession for aggregation: {session.id}, {session.session_name}")
            print(f"WS: Attempting to get ChunkSentimentAnalysis objects for session {self.session_id}")

            @database_sync_to_async
            def get_chunk_sentiments(session_obj):
                return list(ChunkSentimentAnalysis.objects.filter(chunk__session=session_obj))

            all_chunk_sentiments = await get_chunk_sentiments(session)
            print(f"WS: Retrieved {len(all_chunk_sentiments)} ChunkSentimentAnalysis objects.")

            pauses = 0
            tone = ""
            impact_scores = [item.impact for item in all_chunk_sentiments if item.impact is not None]
            audience_engagement_scores = [item.engagement for item in all_chunk_sentiments if item.engagement is not None]

            aggregated_impact = sum(impact_scores) / len(impact_scores) if impact_scores else None
            aggregated_engagement = sum(audience_engagement_scores) / len(audience_engagement_scores) if audience_engagement_scores else None

            print(f"WS: Aggregated impact: {aggregated_impact}, engagement: {aggregated_engagement}")

            await asyncio.to_thread(PracticeSession.objects.filter(id=self.session_id).update,
                pauses=pauses,
                tone=tone,
                impact=aggregated_impact,
                audience_engagement=aggregated_engagement,
                duration=timedelta(seconds=self.chunk_counter * 10)
            )
            print(f"WS: Aggregated analysis saved for session {self.session_id}.")

        except PracticeSession.DoesNotExist:
            print(f"WS: Error: PracticeSession with id {self.session_id} not found for aggregation.")
        except Exception as e:
            print(f"WS: Error during analysis aggregation: {e}")