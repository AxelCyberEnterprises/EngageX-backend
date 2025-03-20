# streaming/consumers.py

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

ANALYSIS_INTERVAL_SECONDS = 60  # Interval for performing video sentiment analysis (seconds)
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

    if session.get('temp_video_file') is None:
        session['temp_video_file'] = tempfile.NamedTemporaryFile(delete=False, suffix='.webm')

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
    audio_output_path = f"temp_audio_{sid}_{chunk_number}.wav"

    try:
        temp_video_file = tempfile.NamedTemporaryFile(delete=True, suffix='.webm')
        temp_video_file.write(accumulated_video_data)
        video_path = temp_video_file.name

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

            sentiment_data = analysis_result.get('Feedback', {}).get('schema', {}).get('properties', {})
            audio_metrics = analysis_result.get('Metrics', {})
            audio_scores = analysis_result.get('Scores', {})
            posture_data = analysis_result.get('Posture', {})

            await asyncio.get_event_loop().run_in_executor(
                None, ChunkSentimentAnalysis.objects.create,
                chunk=session_chunk,
                engagement=sentiment_data.get('Engagement', {}).get('type') == 'number' and int(sentiment_data.get('Engagement', {}).get('example', 0)) or 0,
                confidence=sentiment_data.get('Confidence', {}).get('type') == 'number' and int(sentiment_data.get('Confidence', {}).get('example', 0)) or 0,
                volume_score=audio_scores.get('Volume Score', 0),
                pitch_variability_score=audio_scores.get('Pitch Variability Score', 0),
                speech_rate_score=audio_scores.get('Speaking Rate Score', 0),
                pauses_score=audio_scores.get('Pause Score', 0),
                tone=sentiment_data.get('Tone', {}).get('type') == 'string' and sentiment_data.get('Tone', {}).get('example') or None,
                curiosity=sentiment_data.get('Curiosity', {}).get('type') == 'number' and int(sentiment_data.get('Curiosity', {}).get('example', 0)) or 0,
                empathy=sentiment_data.get('Empathy', {}).get('type') == 'number' and int(sentiment_data.get('Empathy', {}).get('example', 0)) or 0,
                convictions=sentiment_data.get('Convictions', {}).get('type') == 'number' and int(sentiment_data.get('Convictions', {}).get('example', 0)) or 0,
                clarity=sentiment_data.get('Clarity', {}).get('type') == 'number' and int(sentiment_data.get('Clarity', {}).get('example', 0)) or 0,
                emotional_impact=sentiment_data.get('Emotional Impact', {}).get('type') == 'number' and int(sentiment_data.get('Emotional Impact', {}).get('example', 0)) or 0,
                authenticity=sentiment_data.get('Authenticity', {}).get('type') == 'number' and int(sentiment_data.get('Authenticity', {}).get('example', 0)) or 0,
                dynamism=sentiment_data.get('Dynamism', {}).get('type') == 'number' and int(sentiment_data.get('Dynamism', {}).get('example', 0)) or 0,
                pacing=sentiment_data.get('Pacing', {}).get('type') == 'number' and int(sentiment_data.get('Pacing', {}).get('example', 0)) or 0,
                filler_words=sentiment_data.get('Filler Words', {}).get('type') == 'number' and int(sentiment_data.get('Filler Words', {}).get('example', 0)) or 0,
                gestures=sentiment_data.get('Gestures', {}).get('type') == 'number' and int(sentiment_data.get('Gestures', {}).get('example', 0)) or 0,
                eye_contact=sentiment_data.get('Eye Contact', {}).get('type') == 'number' and int(sentiment_data.get('Eye Contact', {}).get('example', 0)) or 0,
                body_language=sentiment_data.get('Body Language', {}).get('type') == 'number' and int(sentiment_data.get('Body Language', {}).get('example', 0)) or 0,
                overall_score=sentiment_data.get('Overall Score', {}).get('type') == 'number' and int(sentiment_data.get('Overall Score', {}).get('example', 0)) or 0,
                volume=audio_metrics.get('Volume'),
                pitch_variability=audio_metrics.get('Pitch Variability'),
                speaking_rate=audio_metrics.get('Speaking Rate (syllables/sec)'),
                appropriate_pauses=audio_metrics.get('Appropriate Pauses'),
                long_pauses=audio_metrics.get('Long Pauses'),
                pitch_variability_rationale=audio_metrics.get('Pitch Variability Metric Rationale'),
                speaking_rate_rationale=audio_metrics.get('Speaking Rate Metric Rationale'),
                pause_metric_rationale=audio_metrics.get('Pause Metric Rationale'),
                mean_back_inclination=posture_data.get('mean_back_inclination'),
                range_back_inclination=posture_data.get('range_back_inclination'),
                mean_neck_inclination=posture_data.get('mean_neck_inclination'),
                range_neck_inclination=posture_data.get('range_neck_inclination'),
                back_feedback=posture_data.get('back_feedback'),
                neck_feedback=posture_data.get('neck_feedback'),
                good_back_time=posture_data.get('good_back_time'),
                bad_back_time=posture_data.get('bad_back_time'),
                good_neck_time=posture_data.get('good_neck_time'),
                bad_neck_time=posture_data.get('bad_neck_time'),
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