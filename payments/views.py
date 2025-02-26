from django.shortcuts import get_object_or_404
from django.contrib.auth import get_user_model
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, viewsets
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from .models import PaymentTransaction
from .serializers import PaymentTransactionSerializer

# Fixed credits for each payment tier
TIER_CREDITS = {
    "starter": 4,
    "growth": 6,
    "pro": 8,
    "ultimate": 12,
}

class PaymentCallbackView(APIView):
    """
    Endpoint to be called after a payment is processed.
    
    Expected payload:
    {
       "transaction_id": "ABC123",
       "status": "success",         // or "failed"
       "tier": "starter",           // one of: "starter", "growth", "pro", "ultimate"
       "user_email": "user@example.com",
       "gateway_response": { ... }
    }
    """
    permission_classes = [IsAuthenticated]  # Adjust if needed
    
    def post(self, request):
        data = request.data
        transaction_id = data.get("transaction_id")
        status_str = data.get("status")
        tier = data.get("tier")
        user_email = data.get("user_email")
        gateway_response = data.get("gateway_response", {})

        # Validate required fields
        if not transaction_id or not status_str or not tier or not user_email:
            return Response({"error": "Missing required fields."}, status=status.HTTP_400_BAD_REQUEST)
        
        tier = tier.lower()
        if tier not in TIER_CREDITS:
            return Response({"error": "Invalid tier specified."}, status=status.HTTP_400_BAD_REQUEST)
        
        # Determine credits based on tier if payment succeeded; else, no credits.
        credits_to_add = TIER_CREDITS[tier] if status_str.lower() == "success" else 0

        # Locate the user by email
        User = get_user_model()
        user = get_object_or_404(User, email=user_email)

        # Create or update the PaymentTransaction record.
        transaction, created = PaymentTransaction.objects.update_or_create(
            transaction_id=transaction_id,
            defaults={
                "user": user,
                "status": status_str.lower(),
                "gateway_response": gateway_response,
                "credits": credits_to_add,
            }
        )

        # On successful payment, update the user's available credits.
        if status_str.lower() == "success":
            profile = user.userprofile
            profile.available_credits += credits_to_add
            profile.save()
        
        serializer = PaymentTransactionSerializer(transaction)
        return Response(serializer.data, status=status.HTTP_200_OK)


class PaymentTransactionViewSet(viewsets.ModelViewSet):
    """
    Admin viewset to monitor payment transactions.
    """
    queryset = PaymentTransaction.objects.all()
    serializer_class = PaymentTransactionSerializer
    permission_classes = [IsAuthenticated, IsAdminUser]
