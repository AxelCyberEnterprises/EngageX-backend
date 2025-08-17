from django.db import models
from django.conf import settings
from django.utils import timezone
from django.core.validators import MinValueValidator
from enterprise.models import Enterprise


class Credit(models.Model):
    """
    Tracks the total and available credits for an enterprise.
    """
    enterprise = models.OneToOneField(
        Enterprise,
        on_delete=models.CASCADE,
        related_name='credit_pool',
        primary_key=True
    )
    total_credits = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0.00,
        validators=[MinValueValidator(0)]
    )
    credits_used = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0.00,
        validators=[MinValueValidator(0)]
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def balance(self):
        """Calculate remaining credits."""
        return self.total_credits - self.credits_used

    def __str__(self):
        return f"Credits for {self.enterprise.name} (Balance: {self.balance})"


class CreditTransaction(models.Model):
    """
    Records all credit transactions (additions and usage) for an enterprise.
    """
    TRANSACTION_TYPES = [
        ('add', 'Add Credits'),
        ('use', 'Use Credits'),
        ('refund', 'Refund Credits'),
        ('expire', 'Credits Expired'),
    ]

    enterprise = models.ForeignKey(
        Enterprise,
        on_delete=models.CASCADE,
        related_name='credit_transactions'
    )
    transaction_type = models.CharField(max_length=10, choices=TRANSACTION_TYPES)
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(0.01)]
    )
    description = models.TextField(blank=True, null=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="User who initiated the transaction (if applicable)"
    )
    reference_id = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        help_text="External reference ID (e.g., payment ID, session ID)"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.get_transaction_type_display()}: {self.amount} credits for {self.enterprise.name}"


class PaymentTransaction(models.Model):
    STATUS_PENDING = 'pending'
    STATUS_SUCCESS = 'success'
    STATUS_FAILED = 'failed'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_SUCCESS, 'Success'),
        (STATUS_FAILED, 'Failed'),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='payment_transactions'
    )
    realm_id = models.CharField(max_length=255)
    transaction_id = models.CharField(max_length=100)
    transaction_date = models.DateTimeField(blank=True, null=True)

    customer_name = models.CharField(max_length=255, blank=True, null=True)
    customer_email = models.EmailField(blank=True, null=True)

    amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    currency = models.CharField(max_length=10, default='USD')

    status = models.CharField(max_length=20, default='pending', choices=STATUS_CHOICES)
    tier = models.CharField(max_length=50, blank=True, null=True)
    credits = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)

    payment_gateway_response = models.JSONField(blank=True, null=True)
    customer_gateway_response = models.JSONField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        unique_together = ('transaction_id', 'realm_id')

    def __str__(self):
        return f"Transaction {self.transaction_id or 'N/A'} for {self.user.email}"
