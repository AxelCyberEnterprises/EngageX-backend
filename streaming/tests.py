import os
import stat
from django.test import TestCase
from django.conf import settings
from sentiment_analysis import analyze_results


class AudioAnalysisTestCase(TestCase):
    
    def setUp(self):
        self.video_path = os.path.join(settings.BASE_DIR, 'myapp', 'test_assets', 'test_video.mp4')
        self.audio_output_path = os.path.join(settings.BASE_DIR, 'myapp', 'test_assets', 'test_audio.mp3')

        # Mark as read-only
        os.chmod(self.video_path, stat.S_IREAD)
        os.chmod(self.audio_output_path, stat.S_IREAD)

    def test_analyze_results(self):
            """Test analyze_results using existing test files."""
            results = analyze_results(self.video_path, self.audio_output_path)

            # Assert results are in correct format
            self.assertIsInstance(results, dict)

            # Key Assertions
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
            self.assertIsInstance(results["Pitch Variability"], (int, float))
            self.assertIsInstance(results["Speech Rate"], (int, float))
            self.assertIsInstance(results["Pauses"], (int, float))

            # Text Assertions
            self.assertIsInstance(results["Tone"], str)
            self.assertIsInstance(results["Strengths"], str)
            self.assertIsInstance(results["Areas of Improvements"], str)
            self.assertIsInstance(results["General Feedback Summary"], str)

            # Logical Assertions
            self.assertGreaterEqual(results["Volume"], 0)
            self.assertLessEqual(results["Volume"], 100)
            self.assertIn(results["Tone"], [
                "Conversational", "Authoritative", "Persuasive", "Inspirational",
                "Empathetic", "Enthusiastic", "Serious", "Humorous", "Reflective", "Urgent"
            ])