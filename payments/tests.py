from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from django.contrib.auth import get_user_model
from payments.models import PaymentTransaction
from decimal import Decimal

class PaymentCallbackTestCase(APITestCase):
    def setUp(self):
        # Create a test user and ensure their profile exists.
        self.user = get_user_model().objects.create_user(email='test@example.com', password='password123')
        # Refresh profile to ensure it exists
        self.user_profile = self.user.userprofile  
        self.user_profile.available_credits = Decimal('0.00')
        self.user_profile.save()
        
        # Force authentication for all client requests.
        self.client.force_authenticate(user=self.user)
        
        # Define the URL for the payment callback endpoint.
        self.url = reverse('payment-callback')
        
        # Define a valid payload corresponding to the new tier-based logic.
        self.valid_payload = {
            "transaction_id": "TX123",
            "status": "success",
            "tier": "starter",  # for example, 'starter' grants 4 credits
            "user_email": "test@example.com",
            "gateway_response": {"info": "test response"}
        }

    def test_successful_payment_callback(self):
        response = self.client.post(self.url, self.valid_payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # Verify that the transaction record was created/updated
        transaction = PaymentTransaction.objects.get(transaction_id="TX123")
        self.assertEqual(transaction.status, "success")
        self.assertEqual(transaction.credits, 4)  # Assuming 'starter' tier equals 4 credits
        
        # Verify that the user's available credits were updated
        self.user_profile.refresh_from_db()
        self.assertEqual(self.user_profile.available_credits, Decimal('4.00'))

    def test_missing_fields(self):
        invalid_payload = {
            "transaction_id": "TX124",
            "status": "success",
            "tier": "starter"
            # Missing user_email
        }
        response = self.client.post(self.url, invalid_payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_invalid_tier(self):
        payload = self.valid_payload.copy()
        payload["tier"] = "invalid_tier"
        response = self.client.post(self.url, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
