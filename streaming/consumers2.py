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



s3 = boto3.client("s3")
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

    async def receive(self, text_data):
        """Receive a JSON message containing video & audio blobs, save them, and trigger processing."""
        try:
            data = json.loads(text_data)

            if data["type"] == "video_audio":
                self.chunk_counter += 1
                video_blob = data.get("video")
                audio_blob = data.get("audio")

                if video_blob and audio_blob:
                    # Convert Base64 blobs to binary and save them
                    video_bytes = self.decode_base64(video_blob)
                    audio_bytes = self.decode_base64(audio_blob)

                    video_chunk_path = os.path.join(tempfile.gettempdir(), f"{self.session_id}_{self.chunk_counter}_video.webm")
                    audio_chunk_path = os.path.join(tempfile.gettempdir(), f"{self.session_id}_{self.chunk_counter}_audio.webm")

                    with open(video_chunk_path, "wb") as vf:
                        vf.write(video_bytes)

                    with open(audio_chunk_path, "wb") as af:
                        af.write(audio_bytes)

                    print(f"Received chunk {self.chunk_counter} for Session {self.session_id}. Video: {video_chunk_path}, Audio: {audio_chunk_path}")

                    # Acknowledge receipt
                    await self.send(json.dumps({
                        "status": "received",
                        "session_id": self.session_id,
                        "chunk_number": self.chunk_counter,
                    }))

                    # Process both chunks asynchronously
                    asyncio.create_task(asyncio.to_thread(self.process_chunk_sync, video_chunk_path, audio_chunk_path))

        except Exception as e:
            print(f"Error processing received data: {e}")

    def process_chunk_sync(self,  video_chunk_path, audio_chunk_path):
        """Runs sentiment analysis after audio extraction, while S3 upload happens in parallel."""
        print(f"Processing {video_chunk_path} in a separate thread")

        with concurrent.futures.ThreadPoolExecutor() as executor:
            # Start both tasks but don't wait for them together 
            future_s3_upload = executor.submit(self.upload_to_s3, video_chunk_path)
            future_sentiment_analysis = executor.submit(self.run_sentiment_analysis, video_chunk_path, audio_chunk_path)

            future_sentiment_analysis.result()


    def upload_to_s3(self, chunk_path):
        """Upload the video chunk to AWS S3."""
        file_name = os.path.basename(chunk_path)
        folder_path = f"{BASE_FOLDER}{self.session_id}/"
        s3_key = f"{folder_path}{file_name}"

        print(f"Uploading {chunk_path} to S3...")

        try:
            s3.upload_file(chunk_path, BUCKET_NAME, s3_key)
            print(f"Uploaded {chunk_path} to {s3_key}")
        except ClientError as e:
            logging.error(e)

    def run_sentiment_analysis(self, video_chunk_path, audio_chunk_path):
        """Perform sentiment analysis using the extracted audio."""
        try:
            analysis = analyze_results(video_chunk_path, audio_chunk_path)
            print(f"Sentiment Analysis Result: {analysis}")
        except Exception as e:
            print(f"Error in sentiment analysis: {e}")