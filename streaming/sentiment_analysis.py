import os
import time
import json
import math as m
import threading
from queue import Queue
import numpy as np
import pandas as pd
import parselmouth
import cv2
import mediapipe as mp
from openai import OpenAI
import subprocess
from deepgram import (
    DeepgramClient,
    PrerecordedOptions,
    FileSource,
)  # pip install deepgram-sdk

from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv

from django.conf import settings

load_dotenv()

# load OpenAI API Key
client = OpenAI(api_key=settings.OPENAI_API_KEY)

# initialize Mediapipe Pose Detection
mp_pose = mp.solutions.pose
pose = mp_pose.Pose()

# queues for thread communication
frame_queue = Queue(maxsize=5)

# synchronization and STOP flag for thread termination
stop_flag = threading.Event()

results_data = {
    "good_back_frames": 0,
    "bad_back_frames": 0,
    "good_neck_frames": 0,
    "bad_neck_frames": 0,
    "back_angles": [],
    "neck_angles": [],
    "back_feedback": "",
    "neck_feedback": "",
    "is_hand_present": "",
}

lock = threading.Lock()


def ai_audience_question(transcript):
    prompt = f"""
        You are a curious audience member at a talk or presentation. Based on the following speaker transcript, ask a simple but insightful question
        ONLY return the question
        Transcript:{transcript}\n"""

    try:
        response = client.chat.completions.create(
            model="gpt-4.1",  # You can change to gpt-3.5-turbo or another if preferred
            messages=[
                {"role": "system", "content": "You are a helpful assistant"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            max_tokens=100,
        )
        question = response.choices[0].message.content.strip()
        return question
    except Exception as e:
        print(f"Error generating audience question: {e}")
        return None


# ---------------------- SCORING FUNCTIONS ----------------------
def scale_to_score(value, min_val, max_val):
    """
    Scales values where:
    - min_val and max_val get exactly 75
    - midpoint gets 100
    - outside drops smoothly and exponentially toward 40
    """

    if value < min_val or value > max_val:
        # Distance from the nearest boundary
        distance = min(abs(value - min_val), abs(value - max_val))
        # Exponential decay from 75 down to 40
        score = 40 + (35 * np.exp(-0.1 * distance))  # As distance increases, score approaches 40
    else:
        # Normalize value between 0 and 1
        normalized = (value - min_val) / (max_val - min_val)
        # Bell-like curve from 75 to 100 to 75
        score = 75 + 25 * (1 - abs(2 * normalized - 1))  # Peak at 100 in the middle

    return max(40, round(score))  # Ensure the score never drops below 40

def score_volume(volume):
    """Scores volume with a peak at 55 and smooth drop-off toward 40 and 70."""

    score = scale_to_score(volume, 40, 70)

    # Rationale logic based on common volume interpretation
    if 50 <= volume <= 60:
        rationale = "Optimal volume; clear, confident, and well-projected delivery."
    elif 40 <= volume < 50:
        rationale = "Volume slightly low; may be harder to hear in larger settings."
    elif 60 < volume <= 70:
        rationale = "Volume slightly high; may sound overpowering or less natural."
    elif volume < 40:
        rationale = "Volume too low; significantly reduces clarity and presence."
    else:
        rationale = "Volume too high; may overwhelm listeners or create discomfort."

    return score, rationale


def score_pauses(appropriate_pauses, long_pauses):
    """scores pauses using discrete buckets."""
    # call scale_to_score after getting rationale
    score = scale_to_score(appropriate_pauses, 3, 8)  # adjusted for 30 seconds

    if 3 <= appropriate_pauses <= 8:
        rationale = "Ideal pause frequency; pauses enhance clarity without disrupting flow."
    elif appropriate_pauses < 3:
        rationale = "Insufficient pauses; speech may be rushed and less clear."
    else:
        rationale = "Excessive pause frequency; too many breaks can disrupt continuity."

    # apply penalty for long pauses: each long pause beyond 3 reduces the score by 1.
    if long_pauses > 3:
        penalty = (long_pauses - 3) * 10
        score = max(0, score - penalty)
        rationale += f", with {long_pauses} long pauses (>2s) penalizing flow"
    return score, rationale


def score_pace(speaking_rate):
    """scores speaking rate with a peak at 2-3 words/sec, penalizing extremes."""
    score = scale_to_score(speaking_rate, 2.0, 3.0)

    if 2.0 <= speaking_rate <= 3.0:
        rationale = "Optimal speaking rate; clear, engaging, and well-paced delivery."
    elif speaking_rate < 2.0:
        rationale = "Slightly slow speaking rate; may feel a bit drawn-out but generally clear."
    else:
        rationale = "Too fast speaking rate; rapid delivery can hinder audience comprehension."

    return score, rationale


def score_pv(pitch_variability):
    """scores pitch variability with a peak at 50-70."""
    score = scale_to_score(pitch_variability, 50, 90)

    if 50 <= pitch_variability <= 90:
        rationale = "Optimal pitch variability, with dynamic yet controlled expressiveness, promoting engagement and emotional impact"
    elif 40 <= pitch_variability < 50:
        rationale = "Fair pitch variability; could benefit from more variation for expressiveness."
    elif 30 <= pitch_variability < 40:
        rationale = "Slightly low pitch variability; the delivery sounds somewhat monotone."
    elif 0 <= pitch_variability < 30:
        rationale = "Extremely low pitch variability; speech is overly monotone and lacks expressiveness."
    else:
        rationale = "Slightly excessive pitch variability; the delivery may seem erratic."

    return score, rationale


def score_posture(angle, min_value, max_value, body):
    """Scores back posture with optimal range at 2.5 - 3.5 and smooth drop-off toward 1.5 and 5."""

    score = scale_to_score(angle, min_value, max_value)

    # Rationale logic for back posture interpretation
    if (5 / 3) * min_value <= angle <= (7 / 10) * max_value:
        rationale = f"Optimal {body}; steady, balanced, and confident presence."
    elif min_value <= angle < (5 / 3) * min_value:
        rationale = f"Good {body}; may appear rigid but controlled."
    elif (7 / 10) * max_value < angle <= max_value:
        rationale = f"Slightly unstable {body}; may reduce perceived confidence."
    elif angle < min_value:
        rationale = f"Extremely stiff {body}; may appear unnatural and uncomfortable."
    else:
        rationale = f"Excessive {body}; suggests restlessness or discomfort."

    print(f"score_posture: {body}: {angle} {rationale}")

    return score, rationale


# ---------------------- FEATURE EXTRACTION FUNCTIONS ----------------------

def get_pitch_variability(audio_file):
    """extracts pitch variability using Praat."""
    sound = parselmouth.Sound(audio_file)
    pitch = sound.to_pitch()
    frequencies = pitch.selected_array["frequency"]
    return np.std([f for f in frequencies if f > 0]) or 0


def get_volume(audio_file, top_db=20):
    """extracts volume (intensity in dB) using Praat."""
    sound = parselmouth.Sound(audio_file)
    intensity = sound.to_intensity()
    num_low = [low for low in intensity.values[0] if low < top_db]
    return np.median(intensity.values[0])


def get_pace(audio_file, transcript):
    """calculates pauses."""
    start_time = time.time()

    sound = parselmouth.Sound(audio_file)
    duration = sound.duration

    word_count = len(transcript.split())

    elapsed_time = time.time() - start_time
    # print(f"\nElapsed time for pace: {elapsed_time:.2f} seconds")
    return word_count / duration


def get_pauses(audio_file):
    import numpy as np
    sound = parselmouth.Sound(audio_file)

    # Tunable values
    # intensity_threshold = 30
    min_pause_duration = 0.5
    long_pause_duration = 1.2

    # Extract intensity
    intensity = sound.to_intensity()
    times = intensity.xs()
    values = intensity.values[0]

    print("Intensity value stats:", np.min(values), np.max(values), np.mean(values))

    # Optionally use percentile threshold
    intensity_threshold = np.percentile(values, 30)

    pause_times = [times[i] for i, val in enumerate(values) if val < intensity_threshold]

    if not pause_times:
        print("NO PAUSES DETECTED")
        return 1, 1

    # Group into continuous pauses
    pauses = []
    start_time = pause_times[0]

    for i in range(1, len(pause_times)):
        if pause_times[i] - pause_times[i - 1] > 0.2:
            end_time = pause_times[i - 1]
            pauses.append((start_time, end_time))
            start_time = pause_times[i]
    pauses.append((start_time, pause_times[-1]))

    # Print pause segments
    print(f"Detected pause segments: {pauses}")

    appropriate_pauses = sum(min_pause_duration <= (end - start) < long_pause_duration for start, end in pauses)
    long_pauses = sum((end - start) >= long_pause_duration for start, end in pauses)

    print(f"Appropriate pauses: {appropriate_pauses}, Long pauses: {long_pauses}")

    if appropriate_pauses == 0 and long_pauses == 0:
        return 1, 1

    return appropriate_pauses, long_pauses


# ---------------------- PROCESS AUDIO ----------------------

def process_audio(audio_file, transcript):
    """processes audio file with Praat in parallel to extract features."""
    start_time = time.time()

    with ThreadPoolExecutor() as executor:
        future_pitch_variability = executor.submit(get_pitch_variability, audio_file)
        future_volume = executor.submit(get_volume, audio_file)
        future_pace = executor.submit(get_pace, audio_file, transcript)
        future_pauses = executor.submit(get_pauses, audio_file)

    # fetch results from threads
    pitch_variability = future_pitch_variability.result()
    avg_volume = future_volume.result()
    pace = future_pace.result()
    appropriate_pauses, long_pauses = future_pauses.result()

    # score dalculation
    volume_score, volume_rationale = score_volume(avg_volume)
    pitch_variability_score, pitch_variability_rationale = score_pv(pitch_variability)  # (15, 85)
    pace_score, pace_rationale = score_pace(pace)
    pause_score, pause_score_rationale = score_pauses(appropriate_pauses, long_pauses)
    # back_score, back_rationale = scale_to_score()

    results = {
        "Metrics": {
            "Volume": avg_volume,
            "Volume Rationale": volume_rationale,
            "Pitch Variability": pitch_variability,
            "Pitch Variability Rationale": pitch_variability_rationale,
            "Pace": pace,
            "Pace Rationale": pace_rationale,
            "Appropriate Pauses": appropriate_pauses,
            "Long Pauses": long_pauses,
            "Pause Metric Rationale": pause_score_rationale
        },
        "Scores": {
            "Volume Score": volume_score,
            "Pitch Variability Score": pitch_variability_score,
            "Pace Score": pace_score,
            "Pause Score": pause_score,
        }
    }
    print(F"RESULTS JSON {results} \n")

    elapsed_time = time.time() - start_time
    print(f"\nElapsed time for process_audio: {elapsed_time:.2f} seconds")
    # print(f"\nMetrics: \n", results)
    return results


# ---------------------- TRANSCRIPTION ----------------------

# no more whisper, change transcript to contain filler words (duplicate)
def transcribe_audio(audio_file):
    # Path to the audio file
    api_key = settings.DEEPGRAM_API_KEY

    try:
        # STEP 1 Create a Deepgram client using the API key
        deepgram = DeepgramClient(api_key=api_key)

        with open(audio_file, "rb") as file:
            buffer_data = file.read()

        payload: FileSource = {
            "buffer": buffer_data,
        }

        # STEP 2: Configure Deepgram options for audio analysis
        options = PrerecordedOptions(
            model="nova-3",
            smart_format=True,
            filler_words=True,
        )

        # STEP 3: Call the transcribe_file method with the text payload and options
        response = deepgram.listen.rest.v("1").transcribe_file(payload, options)

        # STEP 4: Print the response
        transcript = response['results']['channels'][0]['alternatives'][0]['transcript']
        return transcript

    except Exception as e:
        print(f"Exception: {e}")



def find_distance(x1, y1, x2, y2):
    return np.sqrt(((x2 - x1) ** 2) + ((y2 - y1) ** 2))


def find_angle(x1, y1, x2, y2):
    dx, dy = x2 - x1, y2 - y1
    dot = dy
    norm_vector = find_distance(x1, y1, x2, y2)
    if norm_vector == 0:
        return 0.0
    cos_theta = max(min(dot / norm_vector, 1.0), -1.0)
    return np.degrees(np.arccos(cos_theta))


def extract_posture_angles(landmarks, image_width, image_height):
    def to_pixel(landmark):
        return (int(landmark.x * image_width), int(landmark.y * image_height))

    visibility_threshold = 0.5

    # Only keep flags for hand points and required joints
    left_shoulder = to_pixel(landmarks[mp_pose.PoseLandmark.LEFT_SHOULDER.value])
    right_shoulder = to_pixel(landmarks[mp_pose.PoseLandmark.RIGHT_SHOULDER.value])
    left_ear = to_pixel(landmarks[mp_pose.PoseLandmark.LEFT_EAR.value])
    right_ear = to_pixel(landmarks[mp_pose.PoseLandmark.RIGHT_EAR.value])
    left_hip = to_pixel(landmarks[mp_pose.PoseLandmark.LEFT_HIP.value])
    right_hip = to_pixel(landmarks[mp_pose.PoseLandmark.RIGHT_HIP.value])

    # Only for is_hand_present
    hand_points = [
        mp_pose.PoseLandmark.LEFT_WRIST.value,
        mp_pose.PoseLandmark.RIGHT_WRIST.value,
        mp_pose.PoseLandmark.LEFT_PINKY.value,
        mp_pose.PoseLandmark.RIGHT_PINKY.value,
        mp_pose.PoseLandmark.LEFT_INDEX.value,
        mp_pose.PoseLandmark.RIGHT_INDEX.value,
        mp_pose.PoseLandmark.LEFT_THUMB.value,
        mp_pose.PoseLandmark.RIGHT_THUMB.value,
    ]
    hand_present = any(
        getattr(landmarks[i], "visibility", 0) > visibility_threshold for i in hand_points
    )

    shoulder_mid = (
        (left_shoulder[0] + right_shoulder[0]) // 2,
        (left_shoulder[1] + right_shoulder[1]) // 2,
    )
    hip_mid = (
        (left_hip[0] + right_hip[0]) // 2,
        (left_hip[1] + right_hip[1]) // 2,
    )
    ear_mid = (
        (left_ear[0] + right_ear[0]) // 2,
        (left_ear[1] + right_ear[1]) // 2,
    )

    neck_inclination = find_angle(ear_mid[0], ear_mid[1], shoulder_mid[0], shoulder_mid[1])
    back_inclination = find_angle(shoulder_mid[0], shoulder_mid[1], hip_mid[0], hip_mid[1])

    extracted_posture_angles = {
        "neck_inclination": neck_inclination,
        "back_inclination": back_inclination,
        "is_hand_present": hand_present,
    }
    # print(f"\n extracted_posture_angles: {extracted_posture_angles} \n", flush=True)
    return extracted_posture_angles

# --- VIDEO THREADS ---

def capture_frames(video_path):
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print(f"Error: Could not open video file {video_path}")
        return  # Was: False, but return nothing is idiomatic

    frame_number = 0
    frame_skip = 10

    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            break
        if frame_number % frame_skip == 0:
            if not frame_queue.full():
                frame_queue.put(frame)
        frame_number += 1

    cap.release()
    stop_flag.set()


def process_frames():
    posture_threshold = 5

    while not stop_flag.is_set() or not frame_queue.empty():
        if not frame_queue.empty():
            frame = frame_queue.get()
            image_height, image_width, _ = frame.shape
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            results = pose.process(frame_rgb)

            if results.pose_landmarks:
                angles = extract_posture_angles(results.pose_landmarks.landmark, image_width, image_height)
                with lock:
                    results_data["back_angles"].append(angles["back_inclination"])
                    results_data["neck_angles"].append(angles["neck_inclination"])
                    results_data["is_hand_present"] = angles["is_hand_present"]

                # Optionally display landmarks
                # mp.solutions.drawing_utils.draw_landmarks(
                #     frame, results.pose_landmarks, mp_pose.POSE_CONNECTIONS
                # )


def analyze_posture(video_path):
    start_time = time.time()
    print(f"analyze_posture called with video_path: {video_path}", flush=True)

    with ThreadPoolExecutor(max_workers=3) as executor:
        future_capture = executor.submit(capture_frames, video_path)
        future_process = executor.submit(process_frames)
        future_capture.result()
        future_process.result()

    with lock:
        mean_back = np.mean(results_data["back_angles"]) if results_data["back_angles"] else 0
        range_back = np.max(results_data["back_angles"]) - np.min(results_data["back_angles"]) if results_data["back_angles"] else 0
        mean_neck = np.mean(results_data["neck_angles"]) if results_data["neck_angles"] else 0
        range_neck = np.max(results_data["neck_angles"]) - np.min(results_data["neck_angles"]) if results_data["neck_angles"] else 0
        is_hand_present = results_data["is_hand_present"] if results_data["is_hand_present"] else 0

        elapsed_time = time.time() - start_time
        print(f"\nElapsed time for posture: {elapsed_time:.2f} seconds")

        return {
            "mean_back_inclination": mean_back,
            "range_back_inclination": range_back,
            "mean_neck_inclination": mean_neck,
            "range_neck_inclination": range_neck,
            "is_hand_present": is_hand_present,
        }

# ---------------------- SENTIMENT ANALYSIS ----------------------

def analyze_sentiment(transcript, metrics, posture_data):
    # Get posture scores
    mean_back_score, mean_back_rationale = score_posture(posture_data["mean_back_inclination"], 0, 10, "Back Posture")
    mean_neck_score, mean_neck_rationale = score_posture(posture_data["mean_neck_inclination"], 1, 13, "Neck Posture")
    mean_body_posture = (mean_back_score + mean_neck_score) / 2

    range_back_score, range_back_rationale = score_posture(posture_data["range_back_inclination"], 0, 15,
                                                           "Back range of movement")
    range_neck_score, range_neck_rationale = score_posture(posture_data["range_neck_inclination"], 7, 27,
                                                           "Neck range of movement")
    range_body_posture = (range_back_score + range_neck_score) / 2

    is_hand_present = posture_data["is_hand_present"]

    prompt = f"""
    You are an advanced presentation evaluation system. Using the provided speech metrics, their rationale and the speakers transcript, generate a performance analysis with the following scores (each on a scale of 1–100). Return valid JSON only
    
    Transcript Provided(overlook transcription errors):
    {transcript}


    Audience Emotion:
      - Select one of these emotions that the audience is feeling most strongly ONLY choose from this list(thinking, empathy, excitement, laughter, surprise, interested)
   
    Conviction:
      - Indicates firmness and clarity of beliefs or message. Evaluates how strongly and clearly the speaker presents their beliefs and message. Dependent on volume Volume_score: {metrics["Metrics"]["Volume"]} {metrics["Metrics"]["Volume Rationale"]}, pace_score: {metrics["Scores"]["Pace Score"]} {metrics["Metrics"]["Pace Rationale"]}, pause_score: {metrics["Scores"]["Pause Score"]} {metrics["Metrics"]["Pause Metric Rationale"]}, Posture score: {mean_body_posture} {mean_back_rationale} {mean_neck_rationale}, stiffness score: {range_body_posture} {range_back_rationale} {range_neck_rationale}, Hand Motion: {is_hand_present} and transcript content

    Clarity:
      -  Measures how easily the audience can understand the speaker’s message, dependent on pace, volume consistency, effective pause usage. Volume_score: {metrics["Metrics"]["Volume"]} {metrics["Metrics"]["Volume Rationale"]}, pace_score: {metrics["Scores"]["Pace Score"]} {metrics["Metrics"]["Pace Rationale"]}, pause_score: {metrics["Scores"]["Pause Score"]} {metrics["Metrics"]["Pause Metric Rationale"]}
      
    Brevity:
	- Measure of conciseness of words. To be graded by the transcript
      
    Transformative Potential:
      - Potential to motivate significant change or shift perspectives. Graded primarily on transcript content but also Volume_score: {metrics["Metrics"]["Volume"]} {metrics["Metrics"]["Volume Rationale"]}, pace_score: {metrics["Scores"]["Pace Score"]} {metrics["Metrics"]["Pace Rationale"]}, pause_score: {metrics["Scores"]["Pause Score"]} {metrics["Metrics"]["Pause Metric Rationale"]}, Posture score: {mean_body_posture} {mean_back_rationale} {mean_neck_rationale}, stiffness score: {range_body_posture} {range_back_rationale} {range_neck_rationale}, Hand Motion: {is_hand_present}

    Trigger Response:
      - Indicates to what extent the presentation is triggers an audience emotional response. Graded primarily on transcript content but also Volume_score: {metrics["Metrics"]["Volume"]} {metrics["Metrics"]["Volume Rationale"]}, pause_score: {metrics["Scores"]["Pause Score"]} {metrics["Metrics"]["Pause Metric Rationale"]}

    Filler Words
      - Filler words are bad and affect overall audience engagement. Score 100 if there are no filler words, then -10 for each filler word

    Grammar
      - Based on the transcript, grade the speaker's grammar. Score 100 if there are no grammatical errors, then -10 for each grammatical error

    Response Requirements:
    1) Output valid JSON only, no extra text.
    2) Each required field must appear in the JSON. Scores are numeric [1-100]
    """

    completion = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[{
            "role": "user", "content": prompt
        }],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "Feedback",
                "schema": {
                    "type": "object",
                    "strict": True,
                    "properties": {
                        "Audience Emotion": {
                            "type": "string",
                            "enum": [
                                "thinking",
                                "empathy",
                                "excitement",
                                "laughter",
                                "surprise",
                                "interested"
                            ]},
                        "Conviction": {"type": "number"},
                        "Clarity": {"type": "number"},
                        "Brevity": {"type": "number"},
                        "Transformative Potential": {"type": "number"},
                        "Trigger Response": {"type": "number"},
                        "Filler Words": {"type": "number"},
                        "Grammar": {"type": "number"},
                    },
                    "required": [
                        "Audience Emotion", "Conviction",
                        "Clarity", "Brevity",
                        "Transformative Potential", "Trigger Response",
                        "Filler Words", "Grammar"
                    ],
                    "additionalProperties": False
                }
            }
        }
    )

    response = completion.choices[0].message.content
    print(f"DATA TYPE OF RESPONSE:  {type(response)}")

    try:

        parsed_response = {}
        parsed_response['Feedback'] = json.loads(response)
        feedback = parsed_response['Feedback']
        general_feedback_summary = f"""Chunk analysis: The dominant audience emotion perceived was '{feedback['Audience Emotion']}'. Chunk Transcript: {transcript}\n"""

        # general_feedback_summary = f"""Speaker grades: conviction:{feedback['Conviction']}, clarity:{feedback['Clarity']}, impact: {feedback['Impact']}. 
        # Brevity: {feedback['Brevity']}, transformative potential: {feedback['Transformative Potential']}. The audience's trigger response: {feedback['Trigger Response']}, filler words usage score: {feedback['Filler Words']}, 
        # and grammar:{feedback['Grammar']}. The dominant audience emotion perceived was '{feedback['Audience Emotion']}'."""

        # Body posture score: {mean_body_posture}, Body movement score: {range_body_posture} Speaker Transcript: {transcript}\n Volume_score: {metrics["Metrics"]["Volume"]}, pitch_variability_score: {metrics["Scores"]["Pitch Variability Score"]}. pace score: {metrics["Scores"]["Pace Score"]}, pauses score: {metrics["Scores"]["Pause Score"]}, Hand Motion: {is_hand_present}"""
        # Speaker Transcript: {transcript}\n Body Language rationale: {mean_back_rationale}, {mean_neck_rationale}, {range_back_rationale}, {range_neck_rationale}. Volume rationale: {metrics['Metrics']['Volume Rationale']}. Pitch variability rationale: {metrics['Metrics']['Pitch Variability Rationale']}. Pace rationale: {metrics['Metrics']['Pace Rationale']}. Pause rationale: {metrics['Metrics']['Pause Metric Rationale']}."""
        parsed_response['Feedback']["General Feedback Summary"] = general_feedback_summary
        parsed_response['Feedback']["Impact"] = round((parsed_response['Feedback']["Conviction"] + parsed_response['Feedback']["Transformative Potential"] + parsed_response['Feedback']["Trigger Response"]) / 3 )
        parsed_response['Posture Scores'] = {
            "Posture": round(mean_body_posture),
            "Motion": round(range_body_posture),
            "Gestures": is_hand_present
        }
    except json.JSONDecoder:
        print("Invalid JSON format in response.")
        return None

    return parsed_response


# def analyze_results(video_path, audio_output_path):
#     start_time = time.time()
#     print(f"video_path: {video_path}, audio_output: {audio_output_path}")


#     try:
#         with ThreadPoolExecutor() as executor:
#             future_transcription = executor.submit(transcribe_audio, audio_output_path)
#             future_analyze_posture = executor.submit(analyze_posture, video_path=video_path)


#         # Fetch results AFTER both are submitted
#         transcript = future_transcription.result()  # Now transcription runs truly in parallel
#         posture_data = future_analyze_posture.result()  # Now posture runs in parallel
#         print(f"posture_data: {posture_data}")

#         metrics = process_audio(audio_output_path, transcript)
#         print(f"process audio metrics: {metrics}")

#         sentiment_analysis = analyze_sentiment(transcript, metrics, posture_data)

#         final_json = {
#             'Feedback': sentiment_analysis.get('Feedback'),
#             'Scores': metrics.get('Scores', {}),
#             'Transcript': transcript
#         }

#         print(f"\nSentiment Analysis for {audio_output_path}:\n\n", sentiment_analysis)
#         elapsed_time = time.time() - start_time
#         print(f"\nElapsed time for everything: {elapsed_time:.2f} seconds")

#     except Exception as e:
#         print(f"Error during audio extraction: {e}")

#     return final_json


def convert_webm_audio_to_mp3(webm_file_path, mp3_output_path):
    command = [
        'ffmpeg',
        '-i', webm_file_path,
        '-vn',  # No video
        '-acodec', 'libmp3lame',  # Use MP3 encoder
        '-ab', '128k',  # Audio bitrate
        mp3_output_path
    ]
    try:
        print(f"Attempting to convert: {' '.join(command)}", flush=True)
        print(f"System PATH: {os.environ.get('PATH')}", flush=True)
        subprocess.run(command, check=True, capture_output=True)
        print(f"Successfully converted to: {mp3_output_path}", flush=True)
        return mp3_output_path
    except subprocess.CalledProcessError as e:
        error_message = f"Error converting audio: {e.stderr.decode()}"
        print(f"FFmpeg Conversion Error: {error_message}", flush=True)
        return None
    except FileNotFoundError:
        print("Error: ffmpeg command not found. Make sure it's installed and in your PATH.", flush=True)
        return None


# def analyze_results(video_path, audio_output_path):
#     start_time = time.time()
#     print(f"video_path: {video_path}, audio_output: {audio_output_path}", flush=True)
#     print(f"Checking if original audio file exists: {os.path.exists(audio_output_path)}", flush=True)

#     # Convert audio to MP3 before transcription
#     mp3_output_path = audio_output_path.replace(".webm", ".mp3")
#     converted_audio_path = convert_webm_audio_to_mp3(audio_output_path, mp3_output_path)

#     if converted_audio_path:
#         audio_path_for_transcription = converted_audio_path
#         print(f"Using converted audio for transcription: {audio_path_for_transcription}", flush=True)
#         print(f"Checking if converted audio file exists: {os.path.exists(converted_audio_path)}", flush=True)
#     else:
#         audio_path_for_transcription = audio_output_path
#         print(f"Audio conversion failed!", flush=True)
#         print(f"Attempting transcription with original audio: {audio_path_for_transcription}", flush=True)
#         print(f"Checking if original audio file exists (fallback): {os.path.exists(audio_path_for_transcription)}", flush=True)

#     try:
#         with ThreadPoolExecutor() as executor:
#             print(f"Submitting transcription task with: {audio_path_for_transcription}", flush=True)
#             future_transcription = executor.submit(transcribe_audio, audio_path_for_transcription)
#             future_analyze_posture = executor.submit(analyze_posture, video_path=video_path)

#         # Fetch results AFTER both are submitted
#         transcript = future_transcription.result()  # Now transcription runs truly in parallel
#         posture_data = future_analyze_posture.result()  # Now posture runs in parallel
#         print(f"posture_data: {posture_data}", flush=True)

#         metrics = process_audio(audio_path_for_transcription, transcript) # Use the path of the audio used for transcription
#         print(f"process audio metrics: {metrics}", flush=True)

#         sentiment_analysis = analyze_sentiment(transcript, metrics, posture_data)

#         final_json = {
#             'Feedback': sentiment_analysis.get('Feedback'),
#             'Scores': metrics.get('Scores', {}),
#             'Transcript': transcript
#         }

#         print(f"\nSentiment Analysis for {audio_path_for_transcription}:\n\n", sentiment_analysis, flush=True)
#         elapsed_time = time.time() - start_time
#         print(f"\nElapsed time for everything: {elapsed_time:.2f} seconds", flush=True)

#     except Exception as e:
#         print(f"Error during audio extraction or analysis: {e}", flush=True)

#     return final_json

def analyze_results(transcript_text, video_path, audio_path_for_metrics):
    start_time = time.time()
    print(f"Transcript: {transcript_text}", flush=True)
    print(f"video_path: {video_path}, audio_path_for_metrics: {audio_path_for_metrics}", flush=True)

    try:
        with ThreadPoolExecutor() as executor:
            future_analyze_posture = executor.submit(analyze_posture, video_path=video_path)

        posture_data = future_analyze_posture.result()
        print(f"posture_data: {posture_data}", flush=True)

        metrics = process_audio(audio_path_for_metrics, transcript_text)  # Use the audio path for metrics calculation
        print(f"process audio metrics: {metrics}", flush=True)
        sentiment_analysis_start_time = time.time()
        sentiment_analysis = analyze_sentiment(transcript_text, metrics, posture_data)
        print(f"WS: sentiment_analysis after {time.time() - sentiment_analysis_start_time:.2f} seconds")

        final_json = {
            'Feedback': sentiment_analysis.get('Feedback'),
            'Posture': sentiment_analysis.get('Posture Scores'),
            'Scores': metrics.get('Scores', {}),
            'Transcript': transcript_text
        }

        print(f"\nSentiment Analysis for transcript:\n\n", sentiment_analysis, flush=True)
        elapsed_time = time.time() - start_time
        print(f"\nElapsed time for everything: {elapsed_time:.2f} seconds", flush=True)

    except Exception as e:
        print(f"Error during analysis: {e}", flush=True)
        return {'error': str(e)}  # Return an error dictionary

    return final_json
