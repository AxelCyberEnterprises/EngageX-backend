import json
import os
import tempfile
import threading
import boto3
from asgiref.sync import async_to_sync
from channels.generic.websocket import AsyncWebsocketConsumer

s3 = boto3.resource("s3")

# for bucket in s3.buckets.all():
#     print(bucket)


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

        # with open(chunk_path, "rb") as f:
        #     content = f.read()
        #     print(content)

        print(
            f"Received chunk {self.chunk_counter} for Session {self.session_id} - Saved as {chunk_path}"
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

        # threading.Thread(target=self.process_chunk, args=(chunk_path,)).start()


def process_chunk(self, chunk_path):
    """Run sentiment analysis & upload to S3 in parallel."""
    print(f"Processing {chunk_path}")

    # Run sentiment analysis
    threading.Thread(target=self.run_sentiment_analysis, args=(chunk_path,)).start()

    # Upload to S3
    threading.Thread(target=self.upload_to_s3, args=(chunk_path,)).start()


def run_sentiment_analysis(self, chunk_path):
    """Perform sentiment analysis on the video chunk."""
    print(f"Running sentiment analysis on {chunk_path}")
    # try:
    #     video_clip = VideoFileClip(chunk_path)
    #     duration = video_clip.duration
    #     print(f"Extracted {duration} seconds of video for sentiment analysis.")
    #     # TODO: Implement actual sentiment analysis logic
    # except Exception as e:
    #     print(f"Error in sentiment analysis: {e}")


def upload_to_s3(self, chunk_path):
    """Upload the video chunk to AWS S3."""
    print(f"Uploading {chunk_path} to S3...")
    # try:
    #     s3_key = f"video_chunks/{os.path.basename(chunk_path)}"
    #     s3_client.upload_file(chunk_path, S3_BUCKET_NAME, s3_key)
    #     print(f"Uploaded {chunk_path} to S3 as {s3_key}")
    # except Exception as e:
    #     print(f"Error uploading to S3: {e}")
