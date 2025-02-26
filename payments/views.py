from django.shortcuts import render, get_object_or_404

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, viewsets
from rest_framework.permissions import IsAuthenticated, IsAdminUser

from .models import PaymentTransaction
from .serializers import PaymentTransactionSerializer

from decimal import Decimal


# Define a conversion rate from amount to credits. Adjust as needed.
CREDITS_PER_DOLLAR = 0.1  # for example: $1 equals 0.1 credits

class PaymentCallbackView(APIView):
    """
    Endpoint to be called after a payment is processed.
    Frontend should send a payload like:
    {
       "transaction_id": "ABC123",
       "status": "success",
       "amount": "100.00",
       "user_email": "user@example.com",
       "gateway_response": { ... }
    }
    """
    def post(self, request):
        data = request.data
        transaction_id = data.get("transaction_id")
        status_str = data.get("status")
        amount = data.get("amount")
        user_email = data.get("user_email")
        gateway_response = data.get("gateway_response", {})

        if not transaction_id or not status_str or not amount or not user_email:
            return Response({"error": "Missing required fields."}, status=status.HTTP_400_BAD_REQUEST)

        # In a real scenario, you might locate the user by email
        from django.contrib.auth import get_user_model
        User = get_user_model()
        user = get_object_or_404(User, email=user_email)
        
        # Create or update the PaymentTransaction record.
        transaction, created = PaymentTransaction.objects.update_or_create(
            transaction_id=transaction_id,
            defaults={
                'user': user,
                'amount': amount,
                'status': status_str,
                'gateway_response': gateway_response,
                'credits': float(amount) * CREDITS_PER_DOLLAR if status_str == PaymentTransaction.STATUS_SUCCESS else 0.00
            }
        )
        
        # If payment is successful, add credits to user's profile.
        if status_str == PaymentTransaction.STATUS_SUCCESS:
            profile = user.userprofile
            profile.available_credits += Decimal(transaction.credits)
            profile.save()

        serializer = PaymentTransactionSerializer(transaction)
        return Response(serializer.data, status=status.HTTP_200_OK)


class PaymentTransactionViewSet(viewsets.ModelViewSet):
    """
    admin monitoring of transactions.
    """
    queryset = PaymentTransaction.objects.all()
    serializer_class = PaymentTransactionSerializer
    permission_classes = [IsAuthenticated, IsAdminUser]