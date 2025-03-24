import json
import os
import tempfile
from asgiref.sync import async_to_sync
from channels.generic.websocket import AsyncWebsocketConsumer


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
        self.session_id = str(self.scope["client"].split())
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
        chunk_filename = f"{self.session_id}_{self.chunk_counter}.mp4"
        chunk_path = os.path.join(tempfile.gettempdir(), chunk_filename)

        # Save the video chunk
        with open(chunk_path, "wb") as f:
            f.write(bytes_data)

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
