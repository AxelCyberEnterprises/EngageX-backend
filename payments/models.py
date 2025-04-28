from django.db import models
from django.conf import settings
from django.utils import timezone

class SingletonModel(models.Model):
    class Meta:
        abstract = True # no db table

    def save(self, *args, **kwargs):
        # ensure there's only one instance by forcing the pk to 1
        # when saving, we will always overwrite the instance with pk=1
        self.pk = 1
        super(SingletonModel, self).save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        # Prevent deletion of the single instance
        pass


    @classmethod
    def load(cls):
        """
        Load the single model instance.
        Creates the instance if it doesn't exist.
        """
        # Use get_or_create with pk=1 to always retrieve or create the single instance
        obj, created = cls.objects.get_or_create(pk=1)
        return obj

class QuickBooksToken(SingletonModel):
    access_token = models.CharField(max_length=255)
    refresh_token = models.CharField(max_length=255)
    expires_at = models.DateTimeField()
    realm_id = models.CharField(max_length=255, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def is_expired(self):
        if self.expires_at is None:
            return True
        return self.expires_at <= timezone.now() - timezone.timedelta(minutes=5)
    
    def __str__(self):
        return f"QuickBooks Token for  Realm ID: {self.realm_id}"

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
