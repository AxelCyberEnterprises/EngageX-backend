# from django.contrib import admin
# from .models import PaymentTransaction, QuickBooksToken, UserCredits


# @admin.register(PaymentTransaction)
# class PaymentTransactionAdmin(admin.ModelAdmin):
#     list_display = ('transaction_id', 'user', 'credits', 'status', 'created_at')
#     search_fields = ('transaction_id', 'user__email')
#     list_filter = ('status', 'created_at')


from django.contrib import admin
from .models import QuickBooksToken, PaymentTransaction, UserCredit

class QuickBooksTokenAdmin(admin.ModelAdmin):
    list_display = ('realm_id', 'expires_at', 'created_at', 'updated_at')

    search_fields = ('realm_id',)

    readonly_fields = ('created_at', 'updated_at')

admin.site.register(QuickBooksToken, QuickBooksTokenAdmin)

class PaymentTransactionAdmin(admin.ModelAdmin):
    list_display = ('transaction_id', 'realm_id', 'user', 'amount', 'credits', 'transaction_date', 'created_at')

    search_fields = ('transaction_id', 'realm_id', 'user__username', 'user__email', 'customer_name', 'customer_email')

    list_filter = ('realm_id', 'transaction_date')

    readonly_fields = ('user', 'realm_id', 'transaction_id', 'transaction_date',
                       'customer_name', 'customer_email', 'amount', 'currency',
                       'credits', 'payment_gateway_response', 'customer_gateway_response',
                       'created_at', 'updated_at') # Making most fields read-only

admin.site.register(PaymentTransaction, PaymentTransactionAdmin)


class UserCreditAdmin(admin.ModelAdmin):
    list_display = ('user', 'credits', 'last_updated')

    search_fields = ('user__username', 'user__email')

    readonly_fields = ('last_updated',)

# Register the model with the custom Admin class
admin.site.register(UserCredit, UserCreditAdmin)

# If you want to make credits read-only and manage them only via code:
# class UserCreditsAdmin(admin.ModelAdmin):
#     list_display = ('user', 'credits', 'last_updated')
#     search_fields = ('user__username', 'user__email')
#     readonly_fields = ('credits', 'last_updated',) # Make credits read-only
# admin.site.register(UserCredits, UserCreditsAdmin)