import socketio
import json
import os
import tempfile
import random
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files import File

# Create a Socket.IO server
sio = socketio.AsyncServer(async_mode='asgi', cors_allowed_origins='*')

# Create an ASGI application
app = socketio.ASGIApp(sio)

# Store client session information
client_sessions = {}

@sio.event
async def connect(sid, environ):
    """Handle client connection"""
    print(f"Client connected: {sid}")
    client_sessions[sid] = {
        'temp_file': tempfile.NamedTemporaryFile(delete=False, suffix='.wav'),
        'chunk_count': 0,
        'current_chunk': b"",
    }
    await sio.emit('connection_established', {'message': 'Connected to Socket.IO server'}, to=sid)

@sio.event
async def disconnect(sid):
    """Handle client disconnection"""
    print(f"Client disconnected: {sid}")
    if sid in client_sessions:
        # Clean up temporary file
        temp_file = client_sessions[sid]['temp_file']
        temp_file.close()
        os.unlink(temp_file.name)
        del client_sessions[sid]

@sio.event
async def audio_chunk(sid, data):
    """Handle incoming audio chunks"""
    if sid not in client_sessions:
        return
    
    session = client_sessions[sid]
    chunk = data['audio']  # Assuming the chunk is base64 encoded
    
    # Append the chunk to the current buffer
    session['current_chunk'] += chunk
    
    # If we have collected enough data for a 1-minute chunk
    if len(session['current_chunk']) >= 1024 * 1024:  # Arbitrary size threshold
        # Write to temporary file
        session['temp_file'].write(session['current_chunk'])
        session['temp_file'].flush()
        
        # Perform mock sentiment analysis
        sentiment = await analyze_audio_chunk()
        
        # Send analysis results back to client
        await sio.emit('analysis_result', {
            'chunk_number': session['chunk_count'],
            'sentiment': sentiment
        }, to=sid)
        
        # Reset for next chunk
        session['current_chunk'] = b""
        session['chunk_count'] += 1

@sio.event
async def start_stream(sid, data):
    """Handle stream start"""
    await sio.emit('stream_started', {
        'message': 'Started recording stream'
    }, to=sid)

@sio.event
async def end_stream(sid, data):
    """Handle stream end"""
    if sid in client_sessions:
        session = client_sessions[sid]
        # Finalize the recording
        if session['temp_file']:
            session['temp_file'].close()
            # Here you would typically move the temporary file to permanent storage
            # and associate it with the user's session
        
        await sio.emit('stream_ended', {
            'message': 'Stream recording completed',
            'total_chunks': session['chunk_count']
        }, to=sid)

async def analyze_audio_chunk():
    """Mock sentiment analysis (placeholder)"""
    sentiments = ['positive', 'neutral', 'negative']
    confidence = random.random()
    return {
        'sentiment': random.choice(sentiments),
        'confidence': confidence
    } 