import os
import stat
from django.test import TestCase
from django.conf import settings
from sentiment_analysis import (
    extract_audio,
    process_audio,
    transcribe_audio,
    analyze_sentiment,
    analyze_results
)


class AudioAnalysisTestCase(TestCase):

    def setUp(self):
        self.video_path = os.path.join(settings.BASE_DIR, 'streaming', 'test_assets', 'test_video.mp4')
        self.audio_output_path = os.path.join(settings.BASE_DIR, 'streaming', 'test_assets', 'test_audio.mp3')

        # Mark as read-only
        os.chmod(self.video_path, stat.S_IREAD)
        os.chmod(self.audio_output_path, stat.S_IREAD)

    # -------------------- TEST EXTRACT AUDIO --------------------

    def test_extract_audio(self):
        """Test that extract_audio returns a valid audio file path."""
        results = extract_audio(video_path=self.video_path, audio_output_path=self.audio_output_path)

        self.assertIsNotNone(results)
        self.assertTrue(results.endswith(('.mp3', '.wav', '.flac', '.ogg')))
        self.assertTrue(os.path.exists(results))

    # -------------------- TEST PROCESS AUDIO --------------------

    def test_process_audio(self):
        """Test that process_audio returns expected metrics and scores."""
        transcript = "Yoo my man"

        results = process_audio(self.audio_output_path, transcript)

        self.assertIsNotNone(results)
        self.assertIn("Metrics", results)
        self.assertIn("Scores", results)

        # Metric Assertions
        expected_metric_keys = [
            "Volume", "Pitch Variability", "Pitch Variability Metric Rationale",
            "Speaking Rate (syllables/sec)", "Speaking Rate Metric Rationale",
            "Appropriate Pauses", "Long Pauses", "Pause Metric Rationale"
        ]

        for key in expected_metric_keys:
            self.assertIn(key, results["Metrics"])

        # Score Assertions
        expected_score_keys = [
            "Volume Score", "Pitch Variability Score",
            "Speaking Rate Score", "Pause Score"
        ]

        for key in expected_score_keys:
            self.assertIn(key, results["Scores"])

    # -------------------- TEST TRANSCRIBE AUDIO --------------------

    def test_transcribe_audio(self):
        """Test that transcribe_audio returns valid text."""
        transcription = transcribe_audio(self.audio_output_path)

        self.assertIsNotNone(transcription)
        self.assertIsInstance(transcription, str)
        self.assertGreater(len(transcription.strip()), 0)

    # -------------------- TEST ANALYZE SENTIMENT --------------------

    def test_analyze_sentiment(self):
        """Test that analyze_sentiment produces structured output."""
        transcript = "Hello everyone, welcome to my talk."
        metrics = {
            "Metrics": {
                "Volume": 60,
                "Pitch Variability": 45,
                "Pitch Variability Metric Rationale": "Good variability.",
                "Speaking Rate (syllables/sec)": 2.3,
                "Speaking Rate Metric Rationale": "Clear and engaging pace.",
                "Appropriate Pauses": 15,
                "Long Pauses": 2,
                "Pause Metric Rationale": "Well-timed pauses."
            },
            "Scores": {
                "Volume Score": 8,
                "Pitch Variability Score": 7,
                "Speaking Rate Score": 9,
                "Pause Score": 8
            }
        }

        results = analyze_sentiment(self.video_path, transcript, metrics)

        self.assertIsNotNone(results)
        self.assertIsInstance(results, dict)

        expected_keys = [
            "Engagement", "Confidence", "Volume", "Pitch Variability", "Speech Rate",
            "Pauses", "Tone", "Curiosity", "Empathy", "Convictions", "Clarity",
            "Emotional Impact", "Audience Engagement", "Transformative Potential",
            "Posture Fluidity", "Body Posture", "Strengths",
            "Areas of Improvements", "General Feedback Summary"
        ]

        for key in expected_keys:
            self.assertIn(key, results)

        # Data Type Assertions
        self.assertIsInstance(results["Engagement"], (int, float))
        self.assertIsInstance(results["Confidence"], (int, float))
        self.assertIsInstance(results["Volume"], (int, float))
        self.assertIsInstance(results["Tone"], str)
        self.assertIsInstance(results["Strengths"], str)
        self.assertIsInstance(results["Areas of Improvements"], str)

    # -------------------- TEST ANALYZE RESULTS --------------------

    def test_analyze_results(self):
        """Test analyze_results end-to-end."""
        results = analyze_results(self.video_path, self.audio_output_path)

        self.assertIsInstance(results, dict)

        expected_keys = [
            "Engagement", "Confidence", "Volume", "Pitch Variability", "Speece",
            "Pauses", "Tone", "Curiosity", "Empathy", "Convictions", "Clarity",
            "Emotional Impact", "Audience Engagement", "Transformative Potential",
            "Posture Fluidity", "Body Posture", "Strengths",
            "Areas of Improvements", "General Feedback Summary"
        ]

        for key in expected_keys:
            self.assertIn(key, results)

        # Data Type Assertions
        self.assertIsInstance(results["Engagement"], (int, float))
        self.assertIsInstance(results["Volume"], (int, float))
        self.assertIsInstance(results["Pitch Variability"], (int, float))
        self.assertIsInstance(results["Speech Rate"], (int, float))
        self.assertIsInstance(results["General Feedback Summary"], str)

        # Logical Assertions (optional for guidance)
        self.assertGreaterEqual(results["Volume"], 0)
        self.assertLessEqual(results["Volume"], 100)
        self.assertGreaterEqual(results["Engagement"], 0)
        self.assertLessEqual(results["Engagement"], 100)
