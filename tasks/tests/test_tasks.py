from django.test import TestCase
from unittest.mock import patch
from .tasks import delete_video_from_s3

class DeleteVideoTaskTest(TestCase):
    @patch('boto3.client')
    def test_delete_video_from_s3(self, mock_boto_client):
        mock_s3 = mock_boto_client.return_value
        delete_video_from_s3('test-video-key')
        mock_s3.delete_object.assert_called_once_with(Bucket='mybucket', Key='test-video-key') 