from rest_framework.test import APIClient
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from unittest.mock import patch
from .models import CustomUser


class SlideUploadViewTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = CustomUser.objects.create(email='user@example.com', username='user')
        self.client.force_authenticate(user=self.user)

    @patch('boto3.client')
    def test_slide_upload(self, mock_boto_client):
        mock_s3 = mock_boto_client.return_value
        file = SimpleUploadedFile("file.txt", b"file_content", content_type="text/plain")
        response = self.client.post('/slide-upload/', {'file': file}, format='multipart')
        self.assertEqual(response.status_code, 201)
        mock_s3.upload_fileobj.assert_called_once()
