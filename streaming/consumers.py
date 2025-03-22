import socketio
import json
import os
import tempfile
import asyncio
import logging
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files import File
from .sentiment_analysis import analyze_results
from practice_sessions.models import PracticeSession, SessionChunk, ChunkSentimentAnalysis
from django.utils import timezone
from datetime import timedelta
import random

# Initialize logger
logger = logging.getLogger(__name__)

# Initialize Socket.IO ASGI server, allowing CORS for development.
sio = socketio.AsyncServer(async_mode='asgi', cors_allowed_origins='*')

# Create a Socket.IO ASGI application.
app = socketio.ASGIApp(sio)

# Session data storage (in-memory dictionary)
client_sessions = {}

ANALYSIS_INTERVAL_SECONDS = 30  # Interval for performing video sentiment analysis (seconds) # change to 30 seconds
QUESTION_PROBABILITY = 0.01 # Example probability of asking a question per frame


@sio.event
async def connect(sid, environ):
    """Handles WebSocket connection from a client."""
    print(f"Client connected: {sid}")
    client_sessions[sid] = {
        'temp_video_file': None,
        'accumulated_video_data': b"",
        'frame_count': 0,
        'analysis_timer': None,
        'practice_session_id': None,
        'session_start_time': timezone.now(),
        'chunks_processed': 0,
        'allow_ai_questions': False, # Initialize AI question flag
    }
    await sio.emit('connection_established', {'message': 'WebSocket connection established for video stream'}, to=sid)


async def start_analysis_timer(sid):
    """Starts a recurring timer that triggers video sentiment analysis at intervals."""
    async def trigger_analysis():
        """Inner task to trigger and reschedule analysis."""
        if sid in client_sessions and client_sessions[sid].get('practice_session_id'):
            await perform_video_sentiment_analysis(sid)
            if sid in client_sessions:
                client_sessions[sid]['analysis_timer'] = asyncio.create_task(trigger_analysis())

    if sid in client_sessions and client_sessions[sid].get('practice_session_id'):
        client_sessions[sid]['analysis_timer'] = asyncio.create_task(trigger_analysis())


async def stop_analysis_timer(sid):
    """Stops the periodic video sentiment analysis timer for a session."""
    if sid in client_sessions and client_sessions[sid].get('analysis_timer'):
        client_sessions[sid]['analysis_timer'].cancel()
        try:
            await client_sessions[sid]['analysis_timer']
        except asyncio.CancelledError:
            pass
        client_sessions[sid]['analysis_timer'] = None


@sio.event
async def disconnect(sid):
    """Handles client disconnection."""
    print(f"Client disconnected: {sid}")
    await stop_analysis_timer(sid)
    if sid in client_sessions:
        session = client_sessions[sid]
        if session.get('temp_video_file'):
            try:
                session['temp_video_file'].close()
                os.unlink(session['temp_video_file'].name)
            except Exception as e:
                logger.error(f"Error cleaning up temporary video file for session {sid}: {e}")
        del client_sessions[sid]


async def mock_ask_question(sid):
    """Mock function to simulate asking a question."""
    print(f"AI asking a question to client: {sid}")
    await sio.emit('ai_question', {'message': 'AI is asking a question...'}, to=sid) # Frontend will need to handle this event


@sio.event
async def video_chunk(sid, data):
    """Handles incoming video data chunks from a client."""
    if sid not in client_sessions:
        return

    session = client_sessions[sid]
    frame_bytes = data['frame']

    # sessionID_chunkNumber
    if session.get('temp_video_file') is None:
        session['temp_video_file'] = tempfile.NamedTemporaryFile(delete=False, suffix='.mp4') # changed format to .p4

    try:
        session['temp_video_file'].write(frame_bytes)
        session['accumulated_video_data'] += frame_bytes
        session['frame_count'] += 1

        # Mock AI question trigger
        if session.get('allow_ai_questions') and random.random() < QUESTION_PROBABILITY:
            await mock_ask_question(sid)

    except Exception as e:
        logger.error(f"Error processing video chunk for session {sid}: {e}")
        await sio.emit('stream_error', {'message': 'Error processing video chunk.'}, to=sid)


async def perform_video_sentiment_analysis(sid):
    """Performs video sentiment analysis on accumulated video data for a session."""
    if sid not in client_sessions:
        return

    session_data = client_sessions[sid]
    practice_session_id = session_data.get('practice_session_id')
    accumulated_video_data = session_data.get('accumulated_video_data')

    if not accumulated_video_data:
        print(f"No video data to analyze for session {sid} in this interval.")
        return

    chunk_number = session_data.get('chunks_processed', 0) + 1
    start_offset = session_data.get('chunks_processed', 0) * ANALYSIS_INTERVAL_SECONDS
    end_offset = chunk_number * ANALYSIS_INTERVAL_SECONDS

    print(f"Performing video sentiment analysis for chunk {chunk_number} (seconds {start_offset}-{end_offset}), session {sid}")

    temp_video_file = None
    audio_output_path = f"temp_audio_{sid}_{chunk_number}.mp3"

    # check for old transcript
    old_transcript = ""

    if session_data['temp_transcript_file'] is None:
        session_data['temp_transcript_file'] = tempfile.NamedTemporaryFile(delete=True, suffix='.txt')

    try:
        temp_video_file = tempfile.NamedTemporaryFile(delete=True, suffix='.mp4') # should be .mp4
        temp_video_file.write(accumulated_video_data)
        video_path = temp_video_file.name
        session_data['temp_transcript_file'].write(old_transcript)

        analysis_result = await analyze_results(video_path=video_path, audio_output_path=audio_output_path)

        try:
            practice_session = await asyncio.get_event_loop().run_in_executor(
                None, PracticeSession.objects.get, id=practice_session_id
            )

            session_chunk = await asyncio.get_event_loop().run_in_executor(
                None, SessionChunk.objects.create,
                session=practice_session,
                start_time=start_offset,
                end_time=end_offset,
            )

            sentiment_data = analysis_result.get('Feedback', {})
            scores = analysis_result.get('Scores', {})
            transcript = analysis_result.get('Transcript', {})

            await asyncio.get_event_loop().run_in_executor(
                None, ChunkSentimentAnalysis.objects.create,
                chunk=session_chunk,

                # AI response
                engagement=int(sentiment_data.get('Engagement', 0)),
                audience_emotion = int(sentiment_data.get('Audience Emotion', 0)),
                conviction=int(sentiment_data.get('Conviction', 0)),
                clarity=int(sentiment_data.get('Clarity', 0)),
                impact=int(sentiment_data.get('Impact', 0)),
                brevity=int(sentiment_data.get('Brevity', 0)),
                body_posture=int(sentiment_data.get('Body Posture', 0)),
                transformative_potential=int(sentiment_data.get('Transformative Potential', 0)),
                general_feedback=sentiment_data.get('General Feedback Summary', ''),
                
                # Scores
                volume=scores.get('Volume'),
                pitch_variability=scores.get('Pitch Variability'),
                pace=scores.get('Pace'),

                chunk_transcript=transcript,
            )
            client_sessions[sid]['chunks_processed'] = chunk_number
        except PracticeSession.DoesNotExist:
            logger.error(f"PracticeSession with id {practice_session_id} not found.")
        except Exception as e:
            logger.error(f"Error creating SessionChunk or ChunkSentimentAnalysis: {e}")
    except Exception as e:
        logger.error(f"Error during sentiment analysis for session {sid}, chunk {chunk_number}: {e}")
        await sio.emit('analysis_error', {'message': f'Analysis error for chunk {chunk_number}.'}, to=sid)
    finally:
        if temp_video_file:
            try:
                temp_video_file.close()
                os.remove(temp_video_file.name)
            except Exception as e:
                logger.error(f"Error cleaning up temporary video file for session {sid}, chunk {chunk_number}: {e}")
        if os.path.exists(audio_output_path):
            try:
                os.remove(audio_output_path)
            except Exception as e:
                logger.error(f"Error cleaning up temporary audio file for session {sid}, chunk {chunk_number}: {e}")

    client_sessions[sid]['accumulated_video_data'] = b""  # Reset buffer


@sio.event
async def start_stream(sid, data):
    """Handles 'start_stream' event from the client."""
    print(f"Video stream started by client: {sid}")
    practice_session_id = data.get('practice_session_id')
    allow_ai_questions = data.get('allow_ai_questions', False) # Get the toggle value
    if sid in client_sessions:
        client_sessions[sid]['practice_session_id'] = practice_session_id
        client_sessions[sid]['session_start_time'] = timezone.now()
        client_sessions[sid]['allow_ai_questions'] = allow_ai_questions # Store the toggle value
        await sio.emit('stream_started', {'message': 'Video stream recording started on server'}, to=sid)
        await start_analysis_timer(sid)
    else:
        logger.error(f"Session data not found for client {sid} during start_stream.")
        await sio.emit('stream_error', {'message': 'Session initialization error.'}, to=sid)


@sio.event
async def end_stream(sid, data):
    """Handles 'end_stream' event from the client."""
    print(f"Video stream ended by client: {sid}")
    await stop_analysis_timer(sid)

    if sid in client_sessions:
        session_data = client_sessions[sid]
        practice_session_id = session_data.get('practice_session_id')

        if practice_session_id:
            try:
                practice_session = await asyncio.get_event_loop().run_in_executor(
                    None, PracticeSession.objects.get, id=practice_session_id
                )
                total_chunks = session_data.get('chunks_processed', 0)
                if total_chunks > 0:
                    try:
                        all_chunk_sentiments = await asyncio.get_event_loop().run_in_executor(
                            None, ChunkSentimentAnalysis.objects.filter, chunk__session=practice_session
                        )

                        # Aggregation Logic
                        total_pauses = sum(cs.appropriate_pauses + cs.long_pauses for cs in all_chunk_sentiments)
                        tones = [cs.tone for cs in all_chunk_sentiments if cs.tone]
                        most_common_tone = max(set(tones), key=tones.count) if tones else None
                        avg_emotional_impact = sum(cs.emotional_impact for cs in all_chunk_sentiments) / total_chunks if total_chunks > 0 else 0
                        avg_audience_engagement = sum(cs.engagement for cs in all_chunk_sentiments) / total_chunks if total_chunks > 0 else 0
                        # For emotional_expression, pronunciation, content_organization, etc.,
                        # you might need a more specific logic. For now, we can leave them.

                        practice_session.pauses = total_pauses
                        practice_session.tone = most_common_tone
                        practice_session.emotional_impact = round(avg_emotional_impact, 2)
                        practice_session.audience_engagement = round(avg_audience_engagement, 2)
                        practice_session.allow_ai_questions = session_data.get('allow_ai_questions', False) # Save the AI question toggle state

                        await asyncio.get_event_loop().run_in_executor(None, practice_session.save)

                    except Exception as e:
                        logger.error(f"Error during aggregation for session {practice_session_id}: {e}")
                        await sio.emit('session_error', {'message': 'Error aggregating session results.'}, to=sid)

            except PracticeSession.DoesNotExist:
                logger.error(f"PracticeSession with id {practice_session_id} not found for aggregation.")
                await sio.emit('session_error', {'message': 'Practice session not found.'}, to=sid)
            except Exception as e:
                logger.error(f"Error retrieving PracticeSession for aggregation: {e}")
                await sio.emit('session_error', {'message': 'Error finalizing session.'}, to=sid)

        if session_data.get('temp_video_file'):
            try:
                session_data['temp_video_file'].close()
                temp_file_path = session_data['temp_video_file'].name
                print(f"Temporary video file saved: {temp_file_path}")
                os.unlink(temp_file_path)
            except Exception as e:
                logger.error(f"Error cleaning up main temporary video file for session {sid}: {e}")

        end_time = timezone.now()
        duration = end_time - session_data['session_start_time']

        await sio.emit('stream_ended', {
            'message': 'Video stream recording completed and processed',
            'total_frames': session_data.get('frame_count', 0),
            'analysis_intervals': session_data.get('chunks_processed', 0),
            'session_duration': str(duration)
        }, to=sid)
        del client_sessions[sid]  # Clean up session data
    else:
        logger.error(f"Session data not found for client {sid} during end_stream.")
        await sio.emit('session_error', {'message': 'Session data not found on server.'}, to=sid)