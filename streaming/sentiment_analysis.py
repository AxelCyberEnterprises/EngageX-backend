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
) # pip install deepgram-sdk


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


# ---------------------- SCORING FUNCTIONS ----------------------


def scale_to_score(value, min_val, max_val):
    """Scales values where min/max get exactly 70, midpoint gets 100, and outside drops smoothly to 0."""

    if value < min_val or value > max_val:
        # Smoother exponential drop-off for values outside min/max
        distance = min(abs(value - min_val), abs(value - max_val))
        score = 70 * np.exp(-0.1 * distance)  # Lower decay factor for smooth drop-off
    else:
        # Adjusted bell curve that ensures min/max hit exactly 70
        normalized = (value - min_val) / (max_val - min_val)  # Normalize between 0 and 1
        score = 70 + (30 * (1 - np.abs(2 * normalized - 1)))  # Linear interpolation to 100

    return max(0, round(score))  # Ensure score never goes below 0


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
    score = scale_to_score(appropriate_pauses, 8, 20) # adjusted for 40 seconds

    if 8 <= appropriate_pauses <= 20:
        rationale = "Ideal pause frequency; pauses enhance clarity without disrupting flow."
    elif appropriate_pauses < 12:
        rationale = "Insufficient pauses; speech may be rushed and less clear."
    else:
        rationale = "Excessive pause frequency; too many breaks can disrupt continuity."


    # apply penalty for long pauses: each long pause beyond 3 reduces the score by 1.
    if long_pauses > 2:
        penalty = (long_pauses -3) * 10
        score = max(0, score - penalty)
        rationale += f", with {long_pauses} long pauses (>2s) penalizing flow"
    return score, rationale

def score_pace(speaking_rate):
    """scores speaking rate with a peak at 1.5-2.5 words/sec, penalizing extremes."""
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
    score = scale_to_score(pitch_variability, 30, 70)

    if 50 <= pitch_variability <= 70:
        rationale = "Optimal pitch variability, with dynamic yet controlled expressiveness, promoting engagement and emotional impact"
    elif 40 <= pitch_variability < 50:
        rationale = "Fair pitch variability; could benefit from more variation for expressiveness."
    elif 30 <= pitch_variability < 40:
        rationale = "Slightly low pitch variability; the delivery sounds somewhat monotone."
    elif 15 <= pitch_variability < 30:
        rationale = "Extremely low pitch variability; speech is overly monotone and lacks expressiveness."
    else:
        rationale = "Slightly excessive pitch variability; the delivery may seem erratic."

    return score, rationale

def score_posture(angle, min_value, max_value, body):
    """Scores back posture with optimal range at 2.5 - 3.5 and smooth drop-off toward 1.5 and 5."""
    
    score = scale_to_score(angle, min_value, max_value)
    
    # Rationale logic for back posture interpretation
    if (5/3) * min_value <= angle <= (7/10) * max_value:
        rationale = f"Optimal {body} posture; steady, balanced, and confident presence."
    elif min_value <= angle < (5/3) * min_value:
        rationale = f"Good {body} posture; may appear rigid but controlled."
    elif (7/10) * max_value < angle <= max_value:
        rationale = f"Slightly unstable {body} posture; movement may reduce perceived confidence."
    elif angle < min_value:
        rationale = f"Extremely stiff {body} posture; may appear unnatural and uncomfortable."
    else:
        rationale = f"Excessive {body} movement; suggests restlessness or discomfort."

    return score, rationale

# ---------------------- FEATURE EXTRACTION FUNCTIONS ----------------------

def get_pitch_variability(audio_file):
    """extracts pitch variability using Praat."""
    sound = parselmouth.Sound(audio_file)
    pitch = sound.to_pitch()
    frequencies = pitch.selected_array["frequency"]
    return np.std([f for f in frequencies if f > 0]) or 0

def get_volume(audio_file, top_db = 20):
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
    return word_count/duration

def get_pauses(audio_file):
    """
    Detects pauses using Praat's intensity feature via parselmouth.
    """

    # Load audio file with Praat
    sound = parselmouth.Sound(audio_file)

    intensity_threshold=30 
    min_pause_duration=0.5
    long_pause_duration=1.75
    
    # Extract intensity
    intensity = sound.to_intensity()
    
    # Identify pause segments (where intensity falls below the threshold)
    pause_times = []
    for i, value in enumerate(intensity.values[0]):
        if value < intensity_threshold:
            pause_times.append(intensity.xs()[i])
    
    # Identify continuous pause regions
    if not pause_times:
        print("NO PAUSES DETECTED")
        return 1, 1  # No pauses detected

    # Group pause segments into continuous pauses
    pauses = []
    start_time = pause_times[0]

    for i in range(1, len(pause_times)):
        if pause_times[i] - pause_times[i-1] > 0.1:  # Break detected
            pauses.append((start_time, pause_times[i-1]))
            start_time = pause_times[i]

    # Add the last pause
    pauses.append((start_time, pause_times[-1]))

    # Classify pauses
    appropriate_pauses = sum(min_pause_duration <= (end - start) < long_pause_duration for start, end in pauses)
    long_pauses = sum((end - start) >= long_pause_duration for start, end in pauses)

    # Ensure at least (1,1) if both are 0
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
        future_pace= executor.submit(get_pace, audio_file, transcript)
        future_pauses = executor.submit(get_pauses, audio_file)

    # fetch results from threads
    pitch_variability = future_pitch_variability.result()
    avg_volume = future_volume.result()
    pace= future_pace.result()
    appropriate_pauses, long_pauses = future_pauses.result()

    # score dalculation
    volume_score, volume_rationale = score_volume(avg_volume)
    pitch_variability_score, pitch_variability_rationale = score_pv(pitch_variability) #(15, 85)
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
    api_key=settings.DEEPGRAM_API_KEY

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



# Calculate Distance
def find_distance(x1, y1, x2, y2):
    return m.sqrt(((x2 - x1) * 2) + ((y2 - y1) * 2))

# Calculate Angles
def find_angle(x1, y1, x2, y2):
    dx, dy = x2 - x1, y2 - y1
    vertical = (0, 1)
    dot = dy
    norm_vector = find_distance(x1, y1, x2, y2)
    if norm_vector == 0:
        return 0.0
    cos_theta = max(min(dot / norm_vector, 1.0), -1.0)
    return m.degrees(m.acos(cos_theta))

# Extract posture angles
def extract_posture_angles(landmarks, image_width, image_height):
    def to_pixel(landmark):
        return (int(landmark.x * image_width), int(landmark.y * image_height))

    
    visibility_threshold = 0.5

    left_pinky_present = right_pinky_present = left_index_present = right_index_present = left_thumb_present = right_thumb_present = left_wrist_present = right_wrist_present = False

    left_shoulder = to_pixel(landmarks[mp_pose.PoseLandmark.LEFT_SHOULDER.value])
    right_shoulder = to_pixel(landmarks[mp_pose.PoseLandmark.RIGHT_SHOULDER.value])

    left_ear = to_pixel(landmarks[mp_pose.PoseLandmark.LEFT_EAR.value])
    right_ear = to_pixel(landmarks[mp_pose.PoseLandmark.RIGHT_EAR.value])

    left_hip = to_pixel(landmarks[mp_pose.PoseLandmark.LEFT_HIP.value])
    right_hip = to_pixel(landmarks[mp_pose.PoseLandmark.RIGHT_HIP.value])

    left_wrist = landmarks[mp_pose.PoseLandmark.LEFT_WRIST.value]
    right_wrist = landmarks[mp_pose.PoseLandmark.RIGHT_WRIST.value]

    left_pinky = (landmarks[mp_pose.PoseLandmark.LEFT_PINKY.value])
    right_pinky = (landmarks[mp_pose.PoseLandmark.RIGHT_PINKY.value])

    left_index = (landmarks[mp_pose.PoseLandmark.LEFT_INDEX.value])
    right_index = (landmarks[mp_pose.PoseLandmark.RIGHT_INDEX.value])

    left_thumb = (landmarks[mp_pose.PoseLandmark.LEFT_THUMB.value])
    right_thumb = (landmarks[mp_pose.PoseLandmark.RIGHT_THUMB.value])

    if left_wrist.visibility > visibility_threshold:
        left_wrist_present = True
        left_wrist = to_pixel(left_wrist)

    if right_wrist.visibility > visibility_threshold:
        right_wrist_present = True
        right_wrist = to_pixel(right_wrist)

    if left_pinky.visibility > visibility_threshold:
        left_pinky_present = True
        left_pinky = to_pixel(left_pinky)

    if right_pinky.visibility > visibility_threshold:
        right_pinky_present = True
        right_pinky = to_pixel(right_pinky)

    if left_index.visibility > visibility_threshold:
        left_index_present = True
        left_index = to_pixel(left_index)

    if right_index.visibility > visibility_threshold:
        right_index_present = True
        right_index = to_pixel(right_index)

    if left_thumb.visibility > visibility_threshold:
        left_thumb_present = True
        left_thumb = to_pixel(left_thumb)

    if right_thumb.visibility > visibility_threshold:
        right_thumb_present = True
        right_thumb = to_pixel(right_thumb)

    shoulder_mid = ((left_shoulder[0] + right_shoulder[0]) // 2, (left_shoulder[1] + right_shoulder[1]) // 2)
    hip_mid = ((left_hip[0] + right_hip[0]) // 2, (left_hip[1] + right_hip[1]) // 2)
    ear_mid = ((left_ear[0] + right_ear[0]) // 2, (left_ear[1] + right_ear[1]) // 2)

    neck_inclination = find_angle(ear_mid[0], ear_mid[1], shoulder_mid[0], shoulder_mid[1])
    back_inclination = find_angle(shoulder_mid[0], shoulder_mid[1], hip_mid[0], hip_mid[1])

    return {
        "neck_inclination": neck_inclination,
        "back_inclination": back_inclination,
        "left_wrist_present": left_wrist_present,
        "right_wrist_present": right_wrist_present,
        "left_pinky_present": left_pinky_present,
        "right_pinky_present": right_pinky_present,
        "left_index_present": left_index_present,
        "right_index_present": right_index_present,
        "left_thumb_present": left_thumb_present,
        "right_thumb_present": right_thumb_present
    }


# Capture Thread
def capture_frames(video_path):
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print(f"Error: Could not open video file {video_path}")
        False
    fps = cap.get(cv2.CAP_PROP_FPS)

    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            break
        if not frame_queue.full():
            frame_queue.put(frame)

    cap.release()
    stop_flag.set()  # signal other threads to stop


# Processing Thread
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
                    results_data["back_angles"].append(angles['back_inclination'])
                    results_data["neck_angles"].append(angles['neck_inclination'])

                
                # check if any points on the hand are present
                if (
                    angles["left_wrist_present"] == True or
                    angles["right_wrist_present"] == True or
                    angles["left_pinky_present"] == True or
                    angles["right_pinky_present"] == True or
                    angles["left_index_present"] == True or
                    angles["right_index_present"] == True or
                    angles["left_thumb_present"] == True or
                    angles["right_thumb_present"] == True
                ):
                    with lock:
                        results_data["is_hand_present"] = True
                else:
                    with lock:
                        results_data["is_hand_present"] = False
                
                # calculate time in posture
                if angles["back_inclination"] > posture_threshold:
                    with lock:
                        results_data["bad_back_frames"] += 1
                        results_data["back_feedback"] = "Bad back posture"
                else:
                    with lock:
                        results_data["good_back_frames"] += 1
                        results_data["back_feedback"] = "Good back posture"


                if angles["neck_inclination"] > posture_threshold:
                    with lock:
                        results_data["bad_neck_frames"] += 1
                        results_data["neck_feedback"] = "Bad neck posture"
                else:
                    with lock:
                        results_data["good_neck_frames"] += 1
                        results_data["neck_feedback"] = "Good neck posture"

                mp.solutions.drawing_utils.draw_landmarks(frame, results.pose_landmarks, mp_pose.POSE_CONNECTIONS)


# Main Analysis Function
def analyze_posture(video_path):
    start_time = time.time()
    print(f"analyze_posture called with video_path: {video_path}", flush=True) # Added logging

    with ThreadPoolExecutor(max_workers=3) as executor:
        executor.submit(capture_frames, video_path)
        executor.submit(process_frames)

    # Final results calculation
    with lock:
        print("Inside the lock in analyze_posture", flush=True) # Added logging
        mean_back = np.mean(results_data["back_angles"]) if results_data["back_angles"] else 0
        range_back = np.max(results_data["back_angles"]) - np.min(results_data["back_angles"]) if results_data["back_angles"] else 0

        mean_neck = np.mean(results_data["neck_angles"]) if results_data["neck_angles"] else 0
        range_neck = np.max(results_data["neck_angles"]) - np.min(results_data["neck_angles"]) if results_data["neck_angles"] else 0

        is_hand_present = results_data["is_hand_present"] if results_data["is_hand_present"] else 0

        # Normalize to match the known video length (60 seconds)
        video_duration = 40

        # Time spent in good/bad posture
        gb_time = results_data["good_back_frames"] / 30
        bb_time = results_data["bad_back_frames"] / 30

        # Time spent in good/bad posture
        gn_time = results_data["good_neck_frames"] / 30
        bn_time = results_data["bad_neck_frames"] / 30

        back_feedback = results_data["back_feedback"]
        neck_feedback = results_data["neck_feedback"]

        print(f"gb_time: {gb_time}, bb_time: {bb_time}, gn_time: {gn_time}, bn_time: {bn_time}", flush=True) # Added logging

        # calculate normalized time
        if (gb_time + bb_time) > 0:
            good_back_time = (gb_time / (gb_time + bb_time)) * video_duration
            bad_back_time = (bb_time / (gb_time + bb_time)) * video_duration
        else:
            good_back_time = 0
            bad_back_time = 0

        if (gn_time + bn_time) > 0:
            good_neck_time = (gn_time / (gn_time + bn_time)) * video_duration
            bad_neck_time = (bn_time / (gn_time + bn_time)) * video_duration
        else:
            good_neck_time = 0
            bad_neck_time = 0

        elapsed_time = time.time() - start_time
        print(f"\nElapsed time for posture: {elapsed_time:.2f} seconds")

        # return results in dictionary format
        return {
            "mean_back_inclination": mean_back,
            "range_back_inclination": range_back,
            "mean_neck_inclination": mean_neck,
            "range_neck_inclination": range_neck, # body fluidity (range)
            "back_feedback": back_feedback,
            "neck_feedback": neck_feedback,
            "good_back_time": round(good_back_time, 2), # body posture score (time)
            "bad_back_time": round(bad_back_time, 2),
            "good_neck_time": round(good_neck_time, 2),
            "bad_neck_time": round(bad_neck_time, 2),
            "is_hand_present": is_hand_present
        }


# ---------------------- SENTIMENT ANALYSIS ----------------------

def analyze_sentiment(transcript, metrics, posture_data):

    # Get posture scores
    mean_back_score, mean_back_rationale = score_posture(posture_data["mean_back_inclination"] ,1.5, 5, "Back")
    mean_neck_score, mean_neck_rationale = score_posture(posture_data["mean_neck_inclination"] ,1.5, 5, "Neck")
    mean_body_posture = (mean_back_score + mean_neck_score)/2

    range_back_score, range_back_rationale = score_posture(posture_data["range_back_inclination"] ,1.5, 5, "Back")
    range_neck_score, range_neck_rationale = score_posture(posture_data["range_neck_inclination"] ,1.5, 5, "Neck")
    range_body_posture = (range_back_score + range_neck_score)/2

    is_hand_present = posture_data["is_hand_present"]

    prompt = f"""
    You are an advanced presentation evaluation system. Using the provided speech metrics, their rationale and the speakers transcript, generate a performance analysis with the following scores (each on a scale of 1–100) and a general feedback summary. Return valid JSON only
    
    Transcript Provided:
    {transcript}


    Audience Emotion:
      - Select one of these emotions that the audience will be feeling most strongly (Curiosity, Empathy, Excitement, Inspiration, Amusement, Conviction, Surprise, Hope)
   
    Conviction:
      - Indicates firmness and clarity of beliefs or message. Evaluates how strongly and clearly the speaker presents their beliefs and message. Dependent on Confidence score and transcript

    Clarity:
      -  Measures how easily the audience can understand the speaker’s message, dependent on pace, volume consistency, effective pause usage. Volume_score: {metrics["Metrics"]["Volume"]} {metrics["Metrics"]["Volume Rationale"]}, pace_score: {metrics["Scores"]["Pace Score"]} {metrics["Metrics"]["Pace Rationale"]}, pause_score: {metrics["Scores"]["Pause Score"]} {metrics["Metrics"]["Pause Metric Rationale"]}
      
    Impact:
      - Overall measure of how captivating the talk is and how well the user visually presents himself. 
      Volume_score: {metrics["Metrics"]["Volume"]} {metrics["Metrics"]["Volume Rationale"]}, pitch_variability_score: {metrics["Scores"]["Pitch Variability Score"]} {metrics["Metrics"]["Pitch Variability Rationale"]}, 
      pace_score: {metrics["Scores"]["Pace Score"]} {metrics["Metrics"]["Pace Rationale"]}, pause_score: {metrics["Scores"]["Pause Score"]} {metrics["Metrics"]["Pause Metric Rationale"]}.
      Posture score: {mean_body_posture} {mean_back_rationale} {mean_neck_rationale}, stiffness score: {range_body_posture} {range_back_rationale} {range_neck_rationale}

    Brevity:
	- Measure of conciseness of words. To be graded by the transcript
      
    Transformative Potential:
      - Potential to motivate significant change or shift perspectives.

    Trigger Response:
      - Indicates to what extent the presentation is compelling to the audience emotions

    Filler Words
      - Filler words are bad and affect overall audience engagement. Score 100 if there are no filler words, then -10 for each filler word

    Grammar
      - Based on the transcript, grade the speaker's grammar. Score 100 if there are no grammatical errors, then -10 for each grammatical error
   

    Body Posture:
     - Based on the overall quality of posture alignment and stability. A high score reflects steady posture, minimal stiffness, and low time in poor posture.
     - Posture score: {mean_body_posture} {mean_back_rationale} {mean_neck_rationale}, stiffness score: {range_body_posture} {range_back_rationale} {range_neck_rationale}
   
    General Feedback Summary:
    Provide a holistic assessment that integrates insights from audio analysis scores, posture metrics, and transcript sentiment for a complete evaluation of the speaker's presentation.
    Explicitly reference key data points from audio metrics, posture analysis, and the transcript to justify observations.
    Explain how observed behaviors — such as monotonous speech, poor posture, or excessive movement — may influence the audience's perception
    Emphasize both strengths and areas for improvement to provide a balanced assessment.

    Response Requirements:
    1) Output valid JSON only, no extra text.
    2) Each required field must appear in the JSON. Scores are numeric [1-100]
    """

    completion = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{
            "role": "user", "content": prompt
        }],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "Feedback",
                "schema": {
                    "type": "object",
                    "properties": {
                        "Audience Emotion": {"type": "string"},
                        "Conviction": {"type": "number"},
                        "Clarity": {"type": "number"},
                        "Impact": {"type": "number"},
                        "Brevity": {"type": "number"},
                        "Transformative Potential": {"type": "number"},
                        "Trigger Response": {"type": "number"},
                        "Filler Words": {"type": "number"},
                        "Grammar": {"type": "number"},
                        "General Feedback Summary": {"type": "string"},
                    },
                    "required": [
                        "Audience Emotion", "Conviction", 
                        "Clarity", "Impact", "Brevity",
                        "Transformative Potential","Trigger Response",
                        "Filler Words", "Grammar", 
                        "General Feedback Summary",
                    ]
            }
            }
        }
    )

    response = completion.choices[0].message.content
    print(f"DATA TYPE OF RESPONSE:  {type(response)}")

    try:
        parsed_response = {}
        parsed_response['Feedback'] = json.loads(response)
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

        metrics = process_audio(audio_path_for_metrics, transcript_text) # Use the audio path for metrics calculation
        print(f"process audio metrics: {metrics}", flush=True)

        sentiment_analysis = analyze_sentiment(transcript_text, metrics, posture_data)

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
        return {'error': str(e)} # Return an error dictionary

    return final_json