
from django.contrib import admin
from .models import PaymentTransaction, CreditTransaction, Credit


class PaymentTransactionAdmin(admin.ModelAdmin):
    list_display = ('transaction_id', 'realm_id', 'user', 'amount', 'credits', 'transaction_date', 'created_at')
    search_fields = ('transaction_id', 'realm_id', 'user__username', 'user__email', 'customer_name', 'customer_email')
    list_filter = ('realm_id', 'transaction_date')


class CreditTransactionAdmin(admin.ModelAdmin):
    list_display = ('enterprise', 'transaction_type', 'amount', 'description', 'created_at')
    list_filter = ('transaction_type', 'created_at')
    search_fields = ('enterprise__name', 'description')
    readonly_fields = ('created_at',)
    date_hierarchy = 'created_at'


class CreditAdmin(admin.ModelAdmin):
    list_display = ('enterprise', 'total_credits', 'credits_used', 'balance')
    search_fields = ('enterprise__name',)
    readonly_fields = ('balance', 'created_at')
    list_select_related = ('enterprise',)


admin.site.register(PaymentTransaction, PaymentTransactionAdmin)
admin.site.register(CreditTransaction, CreditTransactionAdmin)
admin.site.register(Credit, CreditAdmin)
