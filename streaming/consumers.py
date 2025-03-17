import socketio
import json
import os
import tempfile
import asyncio  # For asynchronous tasks
import random  # Mock sentiment analysis (for demonstration)
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files import File
from sentiment_analysis import analyze_results

# Initialize Socket.IO ASGI server, allowing CORS for development.
sio = socketio.AsyncServer(async_mode='asgi', cors_allowed_origins='*')

# Create a Socket.IO ASGI application.
app = socketio.ASGIApp(sio)

# Session data storage (in-memory dictionary, consider Redis or similar for production)
client_sessions = {}

ANALYSIS_INTERVAL_SECONDS = 60  # Interval for performing video sentiment analysis (seconds)


@sio.event
async def connect(sid, environ):
    """
    Handles WebSocket connection from a client.

    Upon connection, initializes session-specific data and starts the analysis timer.

    Args:
        sid (str): Session ID assigned by Socket.IO.
        environ (dict): Environment dictionary (e.g., headers, query parameters).
    """
    print(f"Client connected: {sid}")
    client_sessions[sid] = {
        'temp_video_file': tempfile.NamedTemporaryFile(delete=False, suffix='.webm'),  # Temp file for video chunks (consider .mp4, .webm based on frontend encoding)
        'accumulated_video_data': b"",  # Buffer for accumulating video data for analysis intervals
        'frame_count': 0,  # Frame counter for the session
        'analysis_timer': None,  # Asynchronous task for periodic analysis
    }
    await sio.emit('connection_established', {'message': 'WebSocket connection established for video stream'}, to=sid)
    await start_analysis_timer(sid)  # Begin periodic analysis scheduling


async def start_analysis_timer(sid):
    """
    Starts a recurring timer that triggers video sentiment analysis at intervals.

    Analysis is performed every ANALYSIS_INTERVAL_SECONDS.
    """
    async def trigger_analysis():
        """Inner task to trigger and reschedule analysis."""
        if sid in client_sessions:
            await perform_video_sentiment_analysis(sid)
            client_sessions[sid]['analysis_timer'] = asyncio.create_task(trigger_analysis())  # Reschedule itself

    client_sessions[sid]['analysis_timer'] = asyncio.create_task(trigger_analysis())  # Initial timer start


async def stop_analysis_timer(sid):
    """
    Stops the periodic video sentiment analysis timer for a session.

    This is called upon client disconnection or stream end.
    """
    if sid in client_sessions and client_sessions[sid]['analysis_timer']:
        client_sessions[sid]['analysis_timer'].cancel()
        try:
            await client_sessions[sid]['analysis_timer']  # Await cancellation for clean shutdown
        except asyncio.CancelledError:
            pass  # Expected exception when task is cancelled


@sio.event
async def disconnect(sid):
    """
    Handles client disconnection.

    Cleans up session-specific resources, including stopping the analysis timer
    and deleting temporary files.

    Args:
        sid (str): Session ID of the disconnected client.
    """
    print(f"Client disconnected: {sid}")
    await stop_analysis_timer(sid)  # Stop analysis timer on disconnect
    if sid in client_sessions:
        session = client_sessions[sid]
        if session['temp_video_file']:
            session['temp_video_file'].close()
            os.unlink(session['temp_video_file'].name)  # Delete temporary video file
        del client_sessions[sid]  # Remove session data


@sio.event
async def video_chunk(sid, data):
    """
    Handles incoming video data chunks from a client.

    Accumulates video chunks and may perform minimal real-time processing if needed.
    Sentiment analysis is triggered periodically by the analysis timer, not per chunk.

    Args:
        sid (str): Session ID of the client.
        data (dict): Dictionary containing video data chunk.
                     Expected format: {'frame': bytes} where bytes is an encoded video frame.
                     Format should align with frontend encoding (e.g., H.264 encoded frame).
    """
    if sid not in client_sessions:
        return

    session = client_sessions[sid]
    frame_bytes = data['frame']  # Assumes frontend sends encoded video frame as bytes

    session['accumulated_video_data'] += frame_bytes  # Accumulate video frame data
    session['frame_count'] += 1 # Increment frame count


async def perform_video_sentiment_analysis(sid):
    """
    Performs video sentiment analysis on accumulated video data for a session.

    This function is triggered periodically by the analysis timer.
    It gets the accumulated video data since the last analysis, performs
    sentiment analysis (placeholder for actual AI model), and emits results.

    Args:
        sid (str): Session ID of the client for which to perform analysis.
    """
    if sid not in client_sessions:
        return

    session = client_sessions[sid]
    video_data_for_analysis = session['accumulated_video_data'] # Get accumulated video data

    # save to temp file
    audio_data_for_analysis = session['accumulated_audio_data'] # Path for extarcted audio

    if not video_data_for_analysis:
        print(f"No video data to analyze for session {sid} in this interval.") # Log if no data
        return # No data to analyze in this interval

    print(f"Performing video sentiment analysis for interval {session['frame_count'] // (ANALYSIS_INTERVAL_SECONDS * 30) + 1}, session {sid}") # Example interval count log (assuming ~30fps)


    # *** REPLACE THIS MOCK ANALYSIS WITH YOUR ACTUAL VIDEO SENTIMENT ANALYSIS LOGIC ***
    # analysis_result = await mock_video_sentiment_analysis(video_data_for_analysis, audio_data_for_analysis)  # Placeholder analysis

    analysis_result = await analyze_results(video_path=video_data_for_analysis, audio_output_path=audio_data_for_analysis)

    # Emit sentiment analysis result back to the specific client
    await sio.emit('video_analysis_result', {
        'interval_number': session['frame_count'] // (ANALYSIS_INTERVAL_SECONDS * 30) + 1, # Example interval number
        'sentiment_analysis': analysis_result  # Analysis result dictionary (structure depends on your AI model)
    }, to=sid)

    session['accumulated_video_data'] = b""  # Reset accumulated video data buffer after analysis


@sio.event
async def start_stream(sid, data):
    """Handles 'start_stream' event from the client (optional signaling)."""
    print(f"Video stream started by client: {sid}")
    await sio.emit('stream_started', {'message': 'Video stream recording started on server'}, to=sid)
    await start_analysis_timer(sid)  # Ensure analysis timer starts


@sio.event
async def end_stream(sid, data):
    """
    Handles 'end_stream' event from the client.

    Finalizes the video stream recording, performs any final tasks, and cleans up session resources.
    """
    print(f"Video stream ended by client: {sid}")
    await stop_analysis_timer(sid)  # Stop the analysis timer

    if sid in client_sessions:
        session = client_sessions[sid]
        if session['temp_video_file']:
            session['temp_video_file'].close()
            # In a real application, you would process the temp video file here:
            # - Save to permanent storage (S3, database, etc.)
            # - Trigger final, more comprehensive video analysis
            print(f"Temporary video file saved: {session['temp_video_file'].name}") # Log saved file path

        await sio.emit('stream_ended', {
            'message': 'Video stream recording completed and processed',
            'total_frames': session['frame_count'],
            'analysis_intervals': session['frame_count'] // (ANALYSIS_INTERVAL_SECONDS * 30) # Example interval count assuming ~30fps
        }, to=sid)
        del client_sessions[sid]  # Clean up session data


# async def mock_video_sentiment_analysis(video_frame_data):
#     """
#     Placeholder for AI-powered video sentiment analysis.
#     REPLACE THIS ENTIRE FUNCTION with your actual video sentiment analysis implementation.

#     This mock function simulates analyzing video frame data (which would ideally be
#     processed to extract relevant features like facial landmarks, body pose, etc.).

#     Args:
#         video_frame_data (bytes): Encoded video frame data (or features extracted from video).

#     Returns:
#         dict: Mock video sentiment analysis result.
#               Structure of this dict should reflect the output of your actual AI model.
#               Example: {'overall_sentiment': 'neutral', 'facial_expression': 'happy', 'body_language': 'confident'}.
#     """


#     await asyncio.sleep(0.02)  # Simulate processing time for video analysis (could be longer in reality)
#     sentiments = ['positive', 'neutral', 'negative']
#     emotions = ['happy', 'sad', 'angry', 'neutral', 'surprised'] # Example facial expressions

#     return {
#         'overall_sentiment': random.choice(sentiments),
#         'facial_expression': random.choice(emotions),
#         'body_language': random.choice(['confident', 'hesitant', 'engaged', 'disengaged']), # Example body language
#         'confidence_score': random.uniform(0.6, 0.95), # Example confidence score
#         'processing_time_ms': random.randint(20, 150) # Mock processing time in milliseconds
#     }