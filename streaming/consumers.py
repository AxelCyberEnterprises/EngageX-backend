import json
import os
import time
import asyncio
import tempfile
import threading
import boto3
import logging
import concurrent.futures
from botocore.exceptions import ClientError
from asgiref.sync import async_to_sync
from channels.generic.websocket import AsyncWebsocketConsumer
from .sentiment_analysis import analyze_results
from base64 import b64decode
import subprocess
import openai


openai.api_key = os.environ.get("OPENAI_API_KEY")
client = openai.OpenAI()

s3 = boto3.client("s3", region_name=os.environ.get('AWS_REGION'))
BUCKET_NAME = "engagex-user-content-1234"
BASE_FOLDER = "user-videos/UserID/"

class LiveSessionConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        """Initialize session storage for the connected client."""
        await self.accept()

        # Create unique temporary files for each client
        self.session_data = {
            "video_temp_file": tempfile.NamedTemporaryFile(delete=False, suffix=".webm"),
            "audio_temp_file": tempfile.NamedTemporaryFile(delete=False, suffix=".webm"),
            "client_id": self.scope["client"],
        }

        self.chunk_counter = 0
        self.session_id = str(self.scope["client"][-1])
        print(f"Client {self.session_data['client_id']} connected. Video File: {self.session_data['video_temp_file'].name}, Audio File: {self.session_data['audio_temp_file'].name}")

        await self.send(
            text_data=json.dumps({
                "type": "connection_established",
                "message": "You are now connected",
            })
        )

    async def disconnect(self, close_code):
        """Cleanup session storage when the client disconnects."""
        if "video_temp_file" in self.session_data:
            self.session_data["video_temp_file"].close()
            os.remove(self.session_data["video_temp_file"].name)

        if "audio_temp_file" in self.session_data:
            self.session_data["audio_temp_file"].close()
            os.remove(self.session_data["audio_temp_file"].name)

        print(f"Client {self.session_data['client_id']} disconnected. Temporary files removed.")

    # 3rd attempt
    async def receive(self, text_data=None, bytes_data=None):
        """Receive a message from the WebSocket, which could be text or binary data."""
        try:
            if text_data:
                # Handle text messages (JSON formatted)
                data = json.loads(text_data)
                message_type = data.get("type")
                print(f"Received message type: {message_type}") # Added logging for message type

                if message_type == "video":
                    self.chunk_counter += 1
                    video_blob = data.get("data")
                    if video_blob:
                        video_bytes = b64decode(video_blob)
                        video_chunk_path = os.path.join(tempfile.gettempdir(), f"{self.session_id}_{self.chunk_counter}_video.webm")
                        with open(video_chunk_path, "wb") as vf:
                            vf.write(video_bytes)
                        print(f"Received video chunk {self.chunk_counter} for Session {self.session_id}. Saved to {video_chunk_path}")
                        await self.send(json.dumps({
                            "status": "received",
                            "session_id": self.session_id,
                            "chunk_number": self.chunk_counter,
                            "media_type": "video"
                        }))
                        asyncio.create_task(asyncio.to_thread(self.process_chunk_sync, video_chunk_path, None))
                elif message_type == "audio":
                    self.chunk_counter += 1
                    audio_blob = data.get("data")
                    if audio_blob:
                        audio_bytes = b64decode(audio_blob)
                        print(f"Audio chunk size (bytes): {len(audio_bytes)}") # Added logging for audio chunk size
                        audio_chunk_path = os.path.join(tempfile.gettempdir(), f"{self.session_id}_{self.chunk_counter}_audio.webm")
                        with open(audio_chunk_path, "wb") as af:
                            af.write(audio_bytes)
                        print(f"Received audio chunk {self.chunk_counter} for Session {self.session_id}. Saved to {audio_chunk_path}")
                        await self.send(json.dumps({
                            "status": "received",
                            "session_id": self.session_id,
                            "chunk_number": self.chunk_counter,
                            "media_type": "audio"
                        }))
                        # For now, let's just print that it's received. Processing will need to be defined.
                        print("Audio chunk received and saved. Processing needs to be defined for individual audio.")
                elif message_type == "video_audio":
                    self.chunk_counter += 1
                    video_blob = data.get("video")
                    audio_blob = data.get("audio")

                    if video_blob and audio_blob:
                        # Convert Base64 blobs to binary and save them
                        video_bytes = b64decode(video_blob)
                        audio_bytes = b64decode(audio_blob)
                        print(f"Audio chunk size (bytes) in video_audio: {len(audio_bytes)}") # Added logging for audio chunk size

                        video_chunk_path = os.path.join(tempfile.gettempdir(), f"{self.session_id}_{self.chunk_counter}_video.webm")
                        audio_chunk_path = os.path.join(tempfile.gettempdir(), f"{self.session_id}_{self.chunk_counter}_audio.webm")

                        with open(video_chunk_path, "wb") as vf:
                            vf.write(video_bytes)

                        with open(audio_chunk_path, "wb") as af:
                            af.write(audio_bytes)

                        print(f"Received chunk {self.chunk_counter} for Session {self.session_id}. Video: {video_chunk_path}, Audio: {audio_chunk_path}")

                        await self.send(json.dumps({
                            "status": "received",
                            "session_id": self.session_id,
                            "chunk_number": self.chunk_counter,
                            "media_type": "video_audio"
                        }))

                        asyncio.create_task(asyncio.to_thread(self.process_chunk_sync, video_chunk_path, audio_chunk_path))

            elif bytes_data:
                # Handle binary data received from the frontend
                self.chunk_counter += 1
                file_path = os.path.join(tempfile.gettempdir(), f"{self.session_id}_{self.chunk_counter}.webm")
                with open(file_path, "wb") as f:
                    f.write(bytes_data)
                print(f"Received binary chunk {self.chunk_counter} for Session {self.session_id}. Saved to {file_path}")
                await self.send(json.dumps({
                    "status": "received",
                    "session_id": self.session_id,
                    "chunk_number": self.chunk_counter,
                }))
                asyncio.create_task(asyncio.to_thread(self.process_chunk_sync, file_path, None))

        except Exception as e:
            print(f"Error processing received data: {e}")

    def process_chunk_sync(self, video_chunk_path, audio_chunk_path):
        """Runs sentiment analysis after audio extraction, while S3 upload happens in parallel."""
        print(f"Processing video: {video_chunk_path}, audio: {audio_chunk_path} in a separate thread")

        with concurrent.futures.ThreadPoolExecutor() as executor:
            futures = []
            if video_chunk_path:
                futures.append(executor.submit(self.upload_to_s3, video_chunk_path))
            else:
                print("No video chunk path provided for S3 upload.")

            if video_chunk_path and audio_chunk_path:
                futures.append(executor.submit(self.run_sentiment_analysis, video_chunk_path, audio_chunk_path))
            elif video_chunk_path:
                print("Audio chunk missing, sentiment analysis will not be performed.")
            elif audio_chunk_path:
                print("Video chunk missing, sentiment analysis will not be performed as currently configured.")

            for future in concurrent.futures.as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    print(f"Task failed: {e}")


    def upload_to_s3(self, chunk_path):
        """Upload the video chunk to AWS S3."""
        file_name = os.path.basename(chunk_path)
        print(f"file_name: {file_name}")

        folder_path = f"{BASE_FOLDER}{self.session_id}/"
        print(f"folder_path: {folder_path}")

        s3_key = f"{folder_path}{file_name}"
        print(f"s3_key: {s3_key}")

        print(f"Uploading {chunk_path} to S3...")

        try:
            s3.upload_file(chunk_path, BUCKET_NAME, s3_key)
            print(f"Uploaded {chunk_path} to {s3_key}")
        except Exception as e:
            print("S3 upload failed: ",e)

    def run_sentiment_analysis(self, video_chunk_path, audio_chunk_path):
        """Perform sentiment analysis using the extracted audio."""
        try:
            transcript_text = None # Initialize transcript_text

            if audio_chunk_path:
                audio_base, audio_ext = os.path.splitext(audio_chunk_path)
                audio_mp3_path = f"{audio_base}.mp3"
                print(f"video_path: {video_chunk_path}, audio_output: {audio_mp3_path}")
                print("Checking if original audio file exists:", os.path.exists(audio_chunk_path))
                print(f"Attempting to convert: ffmpeg -i {audio_chunk_path} -vn -acodec libmp3lame -ab 128k {audio_mp3_path}")

                ffmpeg_command = f"ffmpeg -i {audio_chunk_path} -vn -acodec libmp3lame -ab 128k {audio_mp3_path}"
                process = subprocess.Popen(ffmpeg_command, shell=True, stderr=subprocess.PIPE, stdout=subprocess.PIPE)
                stdout, stderr = process.communicate()

                transcription_file = None
                if process.returncode == 0:
                    print(f"Successfully converted to: {audio_mp3_path}")
                    print("Using converted audio for transcription:", audio_mp3_path)
                    transcription_file = audio_mp3_path
                else:
                    print(f"FFmpeg Conversion Error: Error converting audio: {stderr.decode()}")
                    print("Attempting transcription with original audio:", audio_chunk_path)
                    print("Checking if original audio file exists (fallback):", os.path.exists(audio_chunk_path))
                    transcription_file = audio_chunk_path

                if transcription_file:
                    try:
                        print("Submitting transcription task with:", transcription_file)
                        with open(transcription_file, 'rb') as audio_file:
                            transcript = client.audio.transcriptions.create(
                                model="whisper-1",
                                file=audio_file
                            )
                            transcript_text = transcript.text
                            print(f"Transcription Result: {transcript_text}")
                    except Exception as e:
                        print(f"Error during audio transcription: {e}")
                else:
                    print("No audio file available for transcription.")
            else:
                print("No audio chunk provided for sentiment analysis.")

            if transcript_text is not None:
                analysis_result = analyze_results(transcript_text, video_chunk_path, audio_mp3_path if os.path.exists(audio_mp3_path) else audio_chunk_path)
                print(f"Final Analysis Result: {analysis_result}")
            else:
                print("No transcript available, skipping final analysis.")

        except Exception as e:
            print(f"Error in sentiment analysis: {e}")