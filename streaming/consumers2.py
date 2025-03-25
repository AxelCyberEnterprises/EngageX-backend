import json
import os
import tempfile
import threading
import boto3
import logging
from botocore.exceptions import ClientError
from asgiref.sync import async_to_sync
from channels.generic.websocket import AsyncWebsocketConsumer
from .sentiment_analysis import analyze_results

s3 = boto3.resource("s3")
BUCKET_NAME = "engagex-user-content-1234"
BASE_FOLDER = "user-videos/UserID/"


class LiveSessionConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        """Initialize session storage for the connected client."""
        await self.accept()

        # Create a unique temporary file for each client
        self.session_data = {
            "temp_file": tempfile.NamedTemporaryFile(delete=False, suffix=".webm"),
            "client_id": self.scope["client"],
        }

        self.chunk_counter = 0
        self.session_id = str(self.scope["client"][-1])
        print(
            f"Client {self.session_data['client_id']} connected. Temporary file: {self.session_data['temp_file'].name}"
        )

        await self.send(
            text_data=json.dumps(
                {
                    "type": "connection_established",
                    "message": "you are now connected",
                }
            )
        )

    async def disconnect(self, close_code):
        """Cleanup session storage when the client disconnects."""
        if "temp_file" in self.session_data:
            self.session_data["temp_file"].close()
            os.remove(self.session_data["temp_file"].name)  # Delete temp file after use
            print(
                f"Client {self.session_data['client_id']} disconnected. Temporary file removed."
            )

    async def receive(self, bytes_data):
        """Receive a video chunk, save it, and trigger parallel processing."""
        self.chunk_counter += 1
        chunk_filename = f"{self.session_id}_{self.chunk_counter}.webm"
        chunk_path = os.path.join(tempfile.gettempdir(), chunk_filename)

        print(chunk_path)

        # Save the video chunk
        with open(chunk_path, "wb") as f:
            f.write(bytes_data)
            f.flush()
            file_name = os.path.basename(f.name)

        print(
            f"Received chunk {self.chunk_counter} for Session {self.session_id} - Saved as {file_name}"
        )

        # Send acknowledgment to client
        await self.send(
            text_data=json.dumps(
                {
                    "status": "received",
                    "session_id": self.session_id,
                    "chunk_number": self.chunk_counter,
                }
            )
        )

        threading.Thread(
            target=process_chunk, args=(self.session_id, chunk_path, file_name)
        ).start()


def process_chunk(session_id, chunk_path, file_name):
    """Run sentiment analysis & upload to S3 in parallel."""
    print(f"Processing {chunk_path}")
    # Upload to S3
    threading.Thread(
        target=upload_to_s3,
        args=(
            session_id,
            chunk_path,
            file_name,
        ),
    ).start()

    # Run sentiment analysis
    threading.Thread(target=run_sentiment_analysis, args=(chunk_path,)).start()


def run_sentiment_analysis(chunk_path):
    """Perform sentiment analysis on the video chunk."""
    print(f"Running sentiment analysis on {chunk_path}")
    try:
        analysis = analyze_results(video_path=chunk_path, audio_output_path=None)
        return analysis
        # TODO: Implement actual sentiment analysis logic
    except Exception as e:
        print(f"Error in sentiment analysis: {e}")


def upload_to_s3(session_id, chunk_path, file_name):
    """Upload the video chunk to AWS S3."""
    print(f"Uploading {chunk_path} to S3...")

    folder_path = f"{BASE_FOLDER}{session_id}/"
    s3_key = f"{folder_path}{file_name}"

    s3_client = boto3.client("s3")
    try:
        response = s3_client.upload_file(chunk_path, BUCKET_NAME, s3_key)
        print(f"Uploaded {chunk_path} to {s3_key}")
    except ClientError as e:
        logging.error(e)
        return False
    return True
