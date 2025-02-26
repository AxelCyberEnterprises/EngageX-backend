from rest_framework import serializers
from .models import PaymentTransaction

class PaymentTransactionSerializer(serializers.ModelSerializer):
    user_email = serializers.EmailField(source='user.email', read_only=True)
    
    class Meta:
        model = PaymentTransaction
        fields = [
            'id', 'user_email', 'transaction_id', 'amount', 
            'credits', 'status', 'gateway_response', 'created_at', 'updated_at'
        ]
