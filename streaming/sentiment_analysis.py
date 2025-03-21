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
from moviepy import VideoFileClip
from concurrent.futures import ThreadPoolExecutor

from django.conf import settings

# chnage this shit too
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
    "neck_feedback": ""
}

lock = threading.Lock()

# ---------------------- AUDIO EXTRACTION FUNCTION ----------------------

def extract_audio(video_path, audio_output_path):
    """Extracts audio from a video file"""
    try:
        video = VideoFileClip(video_path)
        video.audio.write_audiofile(audio_output_path)
        video.close()
        return audio_output_path
    except Exception as e:
        print(f"Error extracting audio: {e}")
        return None


# ---------------------- SCORING FUNCTIONS ----------------------

def scale_to_score(value, min_val, max_val):
    """scales values between min and max into a score from 1-10."""
    if value > max_val:
        penalty = (value - min_val) / (max_val - min_val)
        score = 10 - (penalty * 5)  # apply penalty if value is too high
    else:
        # score = np.clip(10 * ((value - min_val) / (max_val - min_val)), 1, 10)
        value = np.clip(value, min_val, max_val)
        score = round(10 * (value - min_val) / (max_val - min_val))
    return round(score)

def score_pauses(appropriate_pauses, long_pauses):
    """scores pauses using discrete buckets."""
    if 12 <= appropriate_pauses <= 30:
        score = round(8 + 2 * (appropriate_pauses - 12) / (30 - 12))
        rationale = "Ideal pause frequency; pauses enhance clarity without disrupting flow."
    elif 5 <= appropriate_pauses < 12:
        score = round(4 + 3 * (appropriate_pauses - 5) / (12 - 5))
        rationale = "Insufficient pauses; speech may be rushed and less clear."
    elif 30 < appropriate_pauses <= 40:
        score = round(8 - 1 * (appropriate_pauses - 30) / (40 - 30))
        rationale = "Excessive pause frequency; too many breaks can disrupt continuity."
    else:
        score = 1
        if appropriate_pauses < 5:
            rationale = "Extremely few pauses; speech is overly rapid and lacks necessary breaks."
        else:
            rationale = "Extremely many pauses; frequent interruptions severely disrupt speech flow."

    # apply penalty for long pauses: each long pause beyond 3 reduces the score by 1.
    penalty = max(0, long_pauses - 3)
    score -= penalty
    if long_pauses > 3:
        rationale += f", with {long_pauses} long pauses (>2s) penalizing flow"
    return score, rationale

def score_speaking_rate(speaking_rate):
    """scores speaking rate with a peak at 1.5-2.5 words/sec, penalizing extremes."""
    if 2.0 <= speaking_rate <= 2.5:
        score = round(7 + 3 * (speaking_rate - 2.0) / (2.5 - 2.0))
        rationale = "Optimal speaking rate; clear, engaging, and well-paced delivery."
    elif 1.5 <= speaking_rate < 2.0:
        score = round(5 + 2 * (speaking_rate - 1.5) / (2.0 - 1.5))
        rationale = "Slightly slow speaking rate; may feel a bit drawn-out but generally clear."
    elif 2.5 < speaking_rate <= 3.5:
        score = max(1, round(4 - 3 * (speaking_rate - 2.5) / (3.5 - 2.5)))
        rationale = "Too fast speaking rate; rapid delivery can hinder audience comprehension."
    else:
        score = 1
        if speaking_rate < 1.5:
            rationale = "Extremely slow speaking rate; excessively prolonged delivery likely reduces engagement."
        else:
            rationale = "Extremely fast speaking rate; delivery is rushed and difficult to follow."
    return score, rationale

def score_pv(pitch_variability):
    """scores pitch variability with a peak at 50-60."""
    if 60 <= pitch_variability <= 80:
        score = round(8 + 2 * (pitch_variability - 60) / (80 - 60))
        rationale = "Optimal pitch variability; dynamic yet controlled expressiveness."
    elif 45 <= pitch_variability < 60:
        score = round(6 + 2 * (pitch_variability - 45) / (60 - 45))
        rationale = "Slightly low pitch variability; could benefit from more variation for expressiveness."
    elif 30 <= pitch_variability < 45:
        score = round(4 + 2 * (pitch_variability - 30) / (45 - 30))
        rationale = "Fair pitch variability; the delivery sounds somewhat monotone."
    elif 15 <= pitch_variability < 30:
        score = round(4 - 2 * (pitch_variability - 15) / (30 - 15))
        rationale = "Extremely low pitch variability; speech is overly monotone and lacks expressiveness."
    elif 80 < pitch_variability <= 85:
        score = round(4 - 2 * (pitch_variability - 80) / (85 - 80))
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
        score = 1
        if pitch_variability < 15:
            rationale = "Extremely low pitch variability; speech sounds robotic and unengaging."
        else:
            rationale = "Extremely high pitch variability; voice modulation is unnaturally dramatic."
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
    print(f"number of silen sequencies: {len(num_low)} \n")
    print(f"number of decibles tracked: {len(intensity.values[0])} \n")
    print(min(intensity.values[0]))
    return np.median(intensity.values[0])

def get_speaking_rate(y, sr, transcript):
    """calculates speaking rate using Librosa onset detection."""
    word_count = len(transcript.split())
    print(f"number of words: {word_count} \n")
    print(f"total audio time: {librosa.get_duration(y=y, sr=sr)} \n")
    return word_count/librosa.get_duration(y=y, sr=sr)

def get_pauses(y, sr):
    """detects appropriate and long pauses in speech."""
    onset_frames = librosa.onset.onset_detect(y=y, sr=sr, hop_length=256)
    onset_times = librosa.frames_to_time(onset_frames, sr=sr)
    pause_durations = np.diff(onset_times)
    appropriate_pauses = len([p for p in pause_durations if 0.75 < p < 1.5])
    long_pauses = len([p for p in pause_durations if p > 2])
    return appropriate_pauses, long_pauses

# ---------------------- PROCESS AUDIO ----------------------

def process_audio(audio_file, transcript):
    """processes audio file with Praat & Librosa in parallel to extract features."""
    start_time = time.time()

    # load audio
    y, sr = librosa.load(audio_file, sr=16000, res_type="kaiser_fast")  # faster loading

    with ThreadPoolExecutor() as executor:
        future_pitch_variability = executor.submit(get_pitch_variability, audio_file)
        future_volume = executor.submit(get_volume, audio_file)
        future_speaking_rate = executor.submit(get_speaking_rate, y, sr, transcript)
        future_pauses = executor.submit(get_pauses, y, sr)

    # fetch results from threads
    pitch_variability = future_pitch_variability.result()
    avg_volume = future_volume.result()
    speaking_rate = future_speaking_rate.result()
    appropriate_pauses, long_pauses = future_pauses.result()

    # score dalculation
    volume_score = scale_to_score(avg_volume, 40, 70)
    pitch_variability_score, pitch_variability_rationale = score_pv(pitch_variability) #(15, 85)
    speaking_rate_score, speaking_rate_rationale = score_speaking_rate(speaking_rate)
    pause_score, pause_score_rationale = score_pauses(appropriate_pauses, long_pauses)

    results = {
        "Metrics": {
            "Volume": avg_volume,
            "Pitch Variability": pitch_variability,
            "Pitch Variability Metric Rationale": pitch_variability_rationale,
            "Speaking Rate (syllables/sec)": speaking_rate,
            "Speaking Rate Metric Rationale": speaking_rate_rationale,
            "Appropriate Pauses": appropriate_pauses,
            "Long Pauses": long_pauses,
            "Pause Metric Rationale": pause_score_rationale
        },
        "Scores": {
            "Volume Score": volume_score,
            "Pitch Variability Score": pitch_variability_score,
            "Speaking Rate Score": speaking_rate_score,
            "Pause Score": pause_score,
        }
    }
    print(F"RESULTS JSON {results} \n")

    elapsed_time = time.time() - start_time
    print(f"\nElapsed time for process_audio: {elapsed_time:.2f} seconds")
    print(f"\nMetrics: \n", results)
    return results


# ---------------------- TRANSCRIPTION ----------------------

def transcribe_audio(audio_file):
    """transcribes audio using OpenAI Whisper-1."""
    with open(audio_file, "rb") as audio_file_obj:
        transcription = client.audio.transcriptions.create(
            model="whisper-1", file=audio_file_obj
        )
    return transcription.text


# Calculate Distance
def find_distance(x1, y1, x2, y2):
    return m.sqrt(((x2 - x1) ** 2) + ((y2 - y1) ** 2))

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

    left_shoulder = to_pixel(landmarks[mp_pose.PoseLandmark.LEFT_SHOULDER.value])
    right_shoulder = to_pixel(landmarks[mp_pose.PoseLandmark.RIGHT_SHOULDER.value])
    left_ear = to_pixel(landmarks[mp_pose.PoseLandmark.LEFT_EAR.value])
    right_ear = to_pixel(landmarks[mp_pose.PoseLandmark.RIGHT_EAR.value])
    left_hip = to_pixel(landmarks[mp_pose.PoseLandmark.LEFT_HIP.value])
    right_hip = to_pixel(landmarks[mp_pose.PoseLandmark.RIGHT_HIP.value])

    shoulder_mid = ((left_shoulder[0] + right_shoulder[0]) // 2, (left_shoulder[1] + right_shoulder[1]) // 2)
    hip_mid = ((left_hip[0] + right_hip[0]) // 2, (left_hip[1] + right_hip[1]) // 2)
    ear_mid = ((left_ear[0] + right_ear[0]) // 2, (left_ear[1] + right_ear[1]) // 2)

    neck_inclination = find_angle(ear_mid[0], ear_mid[1], shoulder_mid[0], shoulder_mid[1])
    back_inclination = find_angle(shoulder_mid[0], shoulder_mid[1], hip_mid[0], hip_mid[1])

    return {
        "neck_inclination": neck_inclination,
        "back_inclination": back_inclination
    }

# Capture Thread
def capture_frames(video_path):
    cap = cv2.VideoCapture(video_path)
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
    back_threshold = 10
    neck_threshold = 10

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

                if angles["back_inclination"] > back_threshold:
                    with lock:
                        results_data["bad_back_frames"] += 1
                        results_data["back_feedback"] = "Bad back posture"
                elif 5 <= angles["back_inclination"] < 10:
                    with lock:
                        results_data["good_back_frames"] += 1
                        results_data["back_feedback"] = "Good back posture"
                else:
                    with lock:
                        results_data["bad_back_frames"] += 1
                        results_data["back_feedback"] = "Stiff back postute"

                if angles["neck_inclination"] > neck_threshold:
                    with lock:
                        results_data["bad_neck_frames"] += 1
                        results_data["neck_feedback"] = "Bad neck posture"
                elif 5 <= angles["neck_inclination"] < 10:
                    with lock:
                        results_data["good_neck_frames"] += 1
                        results_data["neck_feedback"] = "Good neck posture"
                else:
                    with lock:
                        results_data["good_neck_frames"] += 1
                        results_data["neck_feedback"] = "Stiff neck posture"

                mp.solutions.drawing_utils.draw_landmarks(frame, results.pose_landmarks, mp_pose.POSE_CONNECTIONS)


# Main posture analysis function
def analyze_posture(video_path):
    with ThreadPoolExecutor(max_workers=3) as executor:
        executor.submit(capture_frames, video_path)
        executor.submit(process_frames)

    # final results calculation
    with lock:
        mean_back = np.mean(results_data["back_angles"]) if results_data["back_angles"] else 0
        range_back = np.max(results_data["back_angles"]) - np.min(results_data["back_angles"]) if results_data["back_angles"] else 0

        mean_neck = np.mean(results_data["neck_angles"]) if results_data["neck_angles"] else 0
        range_neck = np.max(results_data["neck_angles"]) - np.min(results_data["neck_angles"]) if results_data["neck_angles"] else 0

        # Normalize to match the known video length (60 seconds)
        video_duration = 30

        # time spent in good/bad posture
        gb_time = results_data["good_back_frames"] / 30
        bb_time = results_data["bad_back_frames"] / 30

        # time spent in good/bad posture
        gn_time = results_data["good_neck_frames"] / 30
        bn_time = results_data["bad_neck_frames"] / 30

        # store posture feedback
        back_feedback = results_data["back_feedback"]
        neck_feedback = results_data["neck_feedback"]

        # calculate normalized time
        good_back_time = (gb_time / (gb_time + bb_time)) * video_duration
        bad_back_time = (bb_time /(gb_time+ bb_time)) * video_duration

        good_neck_time = (gn_time / (gn_time + bn_time)) * video_duration
        bad_neck_time = (bn_time / (gn_time + bn_time)) * video_duration

    # return results in dictionary format
    return {
        "mean_back_inclination": mean_back,
        "range_back_inclination": range_back,
        "mean_neck_inclination": mean_neck,
        "range_neck_inclination": range_neck,
        "back_feedback": back_feedback,
        "neck_feedback": neck_feedback,
        "good_back_time": round(good_back_time, 2),
        "bad_back_time": round(bad_back_time, 2),
        "good_neck_time": round(good_neck_time, 2),
        "bad_neck_time": round(bad_neck_time, 2)
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

    prompt = f"""
    You are an advanced presentation evaluation system. Using the provided speech metrics, which include:
    - Raw values ({metrics['Metrics']})
    - Score values ({metrics['Scores']})
    - Rationale verdicts for Pitch Variability, Speaking Rate and Pauses:
    these verdicts interpret the meaning of the raw metric values for pitch variability, speaking rate and pauses
    - and the transcript of the speaker's presentation,

    ...generate a performance analysis with the following scores (each on a scale of 1–100)
    and a general feedback summary. Return valid JSON only, containing:
      - Engagement
      - Confidence
      - Volume
      - Pitch Variability
      - Speech Rate
      - Pauses
      - Tone
      - Curiosity
      - Empathy
      - Convictions
      - Clarity
      - Emotional Impact
      - Audience Engagement
      - Transformative Potential
      - Postue Fluidity
      - Body Posture
      - Strengths
      - Areas of Improvements
      - General Feedback Summary


    Below are the key metrics, with each metric listing its raw value (from metrics['Metrics']), its derived score (from metrics['Scores']),
    and an explanation of what the raw value means and how it is interpreted.
    The rationale verdicts (if provided) are the system's coded interpretation of the raw values.

    1) Volume Score: {metrics['Scores']['Volume Score']}/10
       Raw Volume (dB): {metrics['Metrics']['Volume']}
       Explanation:
         - Typically, a speaking volume between 45 and 65 dB is considered normal and confident.
         - Volumes lower than 45 dB are considered low and might be hard to hear, while volumes higher than 65 dB might be overwhelming.
         - This metric indicates the speaker’s vocal projection and clarity.

    2) Pitch Variability Score: {metrics['Scores']['Pitch Variability Score']}/10
       Raw Pitch Variability (Hz): {metrics['Metrics']['Pitch Variability']}
       Rationale Verdict: {metrics['Metrics']['Pitch Variability Metric Rationale']}
       Explanation:
         - This metric measures the standard deviation of the speaker’s pitch in voiced segments.
         - Values below 15 Hz indicate minimal variation (monotone), 15–30 Hz is low variability, 30–45 Hz is fair, 45–85 Hz is good, and above 85 Hz is extremely high (potentially distracting).
         - The rationale helps determine if the speaker’s vocal expressiveness is within an optimal range.

    3) Speaking Rate Score: {metrics['Scores']['Speaking Rate Score']}/10
       Raw Speaking Rate (words/sec): {metrics['Metrics']['Speaking Rate (syllables/sec)']}
       Rationale Verdict: {metrics['Metrics']['Speaking Rate Metric Rationale']}
       Explanation:
         - Speaking rate is calculated as the number of words (or syllables) spoken per second, excluding silent segments.
         - A rate below 1.5 words/sec is extremely slow, 1.5-2.0 words/sec is too slow, 2.0–2.5 words/sec is good, 2.5–3.5 words/sec is too fast, and above 3.5 words/sec is extremely fast.
         - This metric reflects how easily the audience can follow the presentation.

    4) Pause Score: {metrics['Scores']['Pause Score']}
       Appropriate Pauses: {metrics['Metrics']['Appropriate Pauses']}
       Long Pauses: {metrics['Metrics']['Long Pauses']}
       Rationale Verdict: {metrics['Metrics']['Pause Metric Rationale']}
       Explanation:
         - Appropriate pauses are short gaps (typically 0.75-1.5 seconds) calculated by detecting gaps between speech segments.
         - They are ideally used around 12-s30 times per minute to enhance clarity and emphasize points.
         - Long pauses (lasting more than 2 seconds) are counted separately because they may break the flow of speech and suggest hesitation.
         - The rationale verdict explains whether the number of appropriate pauses is near the ideal range and if excessive long pauses are penalized.
    below are the key posture metrics:

    5) Mean Back Inclination (degrees): {posture_data['mean_back_inclination']}
    Range of Back Inclination (degrees): {posture_data['range_back_inclination']}
    Back Posture Feedback: {posture_data['back_feedback']}
    Time in Good Back Posture: {posture_data['good_back_time']} seconds
    Time in Bad Back Posture: {posture_data['bad_back_time']} seconds
    Explanation:
    Mean Back Inclination
    Values below 10 degrees indicate good back posture. Values above 10 degrees suggest leaning or slouching.
    Range of Back Inclination:
    A low range (below 10 degrees) suggests controlled, stable posture.
    A high range (above 10 degrees) suggests excessive movement, potentially reflecting restlessness or discomfort.
    Back Feedback:
    Describes whether the back appeared "Stiff" (too rigid), "Fluid" (natural movement), or "Unstable" (frequent shifts).
    Time in Good/Bad Back Posture:
    High time in poor posture indicates sustained discomfort or lack of awareness.

    6)Mean Neck Inclination (degrees): {posture_data['mean_neck_inclination']}
    Range of Neck Inclination (degrees): {posture_data['range_neck_inclination']}
    Neck Posture Feedback: {posture_data['neck_feedback']}
    Time in Good Neck Posture: {posture_data['good_neck_time']} seconds
    Time in Bad Neck Posture: {posture_data['bad_neck_time']} seconds
    Explanation:
    Mean Neck Inclination:
    Values below 10 degrees indicate a steady, balanced head position. Values above 10 degrees indicate excessive head tilt.
    Range of Neck Inclination:
    A low range (below 10 degrees) suggests controlled movement.
    A high range (above 10 degrees) suggests frequent head movement or instability, often perceived as nervousness or discomfort.
    Neck Feedback:
    Describes if the user's head posture appeared "Stiff" (rigid), "Fluid" (natural), or "Unstable" (frequent changes).
    Time in Good/Bad Neck Posture:
    Consider how prolonged poor posture may have influenced audience perception.

    Transcript Provided:
    {transcript}

    ----------------------------------------------------
    Additional Scores to Output (each 1–100) + Explanation
    ----------------------------------------------------

    Engagement:
      - How well the speaker holds audience attention. Good volume, pitch variability, and pacing can boost engagement.


    Audience Emotion:
      - Select one of these emotions that the audience will be feeling most strongly (Curiosity, Empathy, Excitement, Inspiration, Amusement, Conviction, Surprise, Hope)


    Conviction:
      - Indicates firmness and clarity of beliefs or message. Evaluates how strongly and clearly the speaker presents their beliefs and message. Dependent on Confidence score and transcript

    Clarity:
      -  Measures how easily the audience can understand the speaker’s message, as reflected mostly by speaking rate, volume consistency, effective pause usage.

    Emotional Impact:
      - Represents the overall emotional effect of the presentation on the audience. It is calculated as the Average of Curiosity, Empathy, Convictions.

    Audience Engagement:
      - Overall measure of how captivating the talk is and how well the user visually presents himself.

    Brevity:
	- Measure of conciseness of words. To be graded by the transcript


    Transformative Potential:
      - Potential to motivate significant change or shift perspectives.

    Posture Fluidity:
      - Reflects how naturally the presenter moves. Combine data from mean inclination, range of motion, and time spent in good/bad posture to assess. Controlled Stability: Minimal movement with sustained good posture. Fluid Movement: Balanced motion without excessive stiffness or frequent shifts.
        Stiffness: Minimal motion that appears unnatural or rigid. Restlessness: Frequent shifts, suggesting discomfort or nervousness.

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
    2) Each required field must appear in the JSON. Scores are numeric [1–100]
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
                        "Engagement": {"type": "number"},
                        "Audience Emotion": {"type": "string"},
                        "Conviction": {"type": "number"},
                        "Clarity": {"type": "number"},
                        "Impact": {"type": "number"},
                        "Brevity": {"type": "number"},
                        "Transformative Potential": {"type": "number"},
                        "Body Posture": {"type": "number"},
                        "General Feedback Summary": {"type": "string"},
                    },
                    "required": [
                        "Engagement", "Audience Emotion", "Conviction",
                        "Clarity", "Impact", "Brevity",
                        "Transformative Potential","Body Posture", "General Feedback Summary"
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
    except json.JSONDecoder:
        print("Invalid JSON format in response.")
        return None

    return parsed_response


def analyze_results(video_path, audio_output_path):
    start_time = time.time()
    # audio_output_path = "test.mp3"
    # video_path = "video_3.mp4"

    # add try-excepts

        # run transcription and audio analysis in parallel
        with ThreadPoolExecutor() as executor:
            future_analyze_posture = executor.submit(analyze_posture, video_path=video_path)
            future_transcription = executor.submit(transcribe_audio, extracted_audio_path)
            future_audio_analysis = executor.submit(process_audio, extracted_audio_path, future_transcription.result())

        posture_data = future_analyze_posture.result()
        transcript = future_transcription.result()
        metrics = future_audio_analysis.result()

        sentiment_analysis = analyze_sentiment(transcript, metrics, posture_data)

        final_json = {
            'Feedback': sentiment_analysis.get('Feedback'),
            'Scores': metrics.get('Scores', {}),
            'Transcript': transcript
        }

    sentiment_analysis = analyze_sentiment(video_path, transcript, metrics)


    print(f"\nSentiment Analysis for {audio_output_path}:\n\n", sentiment_analysis)
    elapsed_time = time.time() - start_time
    print(f"\nElapsed time for everything: {elapsed_time:.2f} seconds")

    return sentiment_analysis