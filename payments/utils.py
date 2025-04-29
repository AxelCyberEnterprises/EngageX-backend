import hmac
import hashlib
import base64
import requests
import json
import os
from datetime import datetime, timedelta, timezone 

from django.conf import settings
from django.utils import timezone as django_timezone
from django.contrib.auth import get_user_model 
from django.db.models import F

from intuitlib.client import AuthClient
from intuitlib.enums import Scopes
from intuitlib.exceptions import AuthClientError

from .models import QuickBooksToken, PaymentTransaction
from users.models import UserProfile



AMOUNT_TO_TIER = {
    5.0: "tester",
    10.0: "starter",
    15.0: "growth",
    20.0: "pro",
    25.0: "ultimate",
}

TIER_CREDITS = {
    "tester": 5,
    "starter": 4,
    "growth": 6,
    "pro": 8,
    "ultimate": 12,
}


# INTUIT_API_BASE_URL = "https://sandbox-quickbooks.api.intuit.com" 
# INTUIT_BASE_URL = "https://quickbooks.api.intuit.com"


def get_quickbooks_token_obj(realm_id=None):
    """Retrieves the QuickBooksToken model instance from the database."""
    print(f"Step 'get_quickbooks_token_obj': Attempting to load token. Requested realm_id: {realm_id}")
    try:
        # Use the .load() method from your SingletonModel
        # This should get or create the instance with pk=1_URL
        token, created = QuickBooksToken.load()
        print(f"Step 'get_quickbooks_token_obj': QuickBooksToken.load() returned: {token}")

        # Check the values of the loaded token object's fields
        if token:
            print(f"Step 'get_quickbooks_token_obj': Loaded token details:")
            print(f"  pk: {token.pk}")
            print(f"  access_token (first 10): {token.access_token[:10] if token.access_token else 'None/Empty'}")
            print(f"  refresh_token (first 10): {token.refresh_token[:10] if token.refresh_token else 'None/Empty'}")
            print(f"  expires_at: {token.expires_at}")
            print(f"  realm_id: {token.realm_id}")
            print(f"  created_at: {token.created_at}")
            print(f"  updated_at: {token.updated_at}")
        else:
             print("Step 'get_quickbooks_token_obj': QuickBooksToken.load() returned None.")
             return None


        # Now perform the checks to determine if it's a "valid" token for use
        if token and token.access_token and token.refresh_token and token.realm_id:
            print("Step 'get_quickbooks_token_obj': Loaded token object has required fields.")
            # Check if the loaded token is for the requested realm if realm_id is provided
            # Convert realm_id to string for comparison as it's CharField
            if realm_id and str(token.realm_id) != str(realm_id):
                 print(f"Django Util: Realm ID mismatch. Expected {realm_id}, found {token.realm_id}. Returning None.")
                 return None # Return None if realm ID doesn't match requested
            print(f"Django Util: QuickBooks token data retrieved from DB for Realm ID: {token.realm_id}. Returning token.")
            return token
        else:
            # If .load() returned an object but it's incomplete (no tokens/realm)
            print("Django Util: Loaded token object is incomplete (missing access_token, refresh_token, or realm_id). Returning None.")
            return None
    except Exception as e:
        print(f"Django Util: !!! ERROR LOADING TOKEN OBJECT FROM DATABASE: {e}")
        return None


def save_quickbooks_token_obj(access_token, refresh_token, expires_in, realm_id):
    """Saves or updates the QuickBooksToken instance in the database."""
    try:
        token, created = QuickBooksToken.load()

        token.access_token=access_token
        token.refresh_token=refresh_token
        token.expires_at=django_timezone.now() + timedelta(seconds=expires_in)
        token.realm_id=realm_id

        token.save()
        print(f"QuickBooks token saved/updated for realm {realm_id} (created={created})")
        return token
    except Exception as e:
        print(f"Django Util: Error saving QuickBooks token data: {e}")
        return None


def update_customer_credits_in_db(user_instance, credits):
     """Updates customer credits in the database."""
     if not user_instance or credits is None:
            print("Django Util: No user instance or credits provided for update.")
            return False
     try:
        user_profile = user_instance.user_profile
        user_profile.available_credits = F('available_credits') + credits # Update available_credits field in UserProfile
        user_profile.save()

        user_profile.refresh_from_db() # Refresh the object to get updated value
        print(f"Django Util: Updated credits for user {user_instance.username} by {credits}. Total credits: {user_profile.available_credits}")
        return True
     except Exception as e:
        print(f"Django Util: Error updating UserCredit for user {user_instance.username}: {e}")
        return False
     

def get_transactions_from_db(realm_id=None):
    """Retrieves payment transactions from the database."""
    try:
        queryset = PaymentTransaction.objects.all()

        if realm_id:
            queryset = queryset.filter(realm_id=realm_id)

        # Order by the correct field name (assuming created_at or updated_at)
        transactions = queryset.order_by('-created_at') # Use created_at or updated_at

        print(f"Django Util: Retrieved {transactions.count()} transactions from database.")
        # Returning queryset allows using serializers later.
        # If you specifically need list of dicts, use .values()
        return transactions
    except Exception as e:
        print(f"Django Util: Error retrieving transactions from database: {e}")
        return PaymentTransaction.objects.none() # Return empty queryset on error


def get_credits_from_db(user_instance=None):
    """
    Retrieves customer credits from the database.
    Pass a User instance to get credits for one user.
    If None, returns a queryset of all UserCredit objects.
    """
    try:
        user_profile = user_instance.user_profile if user_instance else None
        return user_profile.available_credits
    except Exception as e:
        print(f"Django Util: Error retrieving credits from database: {e}")
        # Return empty queryset or None depending on expected output for single/all
        return 0.0



def clear_db_data():
    """Clears all QuickBooks related data from the database."""
    try:
        # Delete all records from the models
        QuickBooksToken.objects.all().delete()
        PaymentTransaction.objects.all().delete()
        print("Django Util: All QuickBooks related data cleared from database.")
        return True
    except Exception as e:
        print(f"Django Util: Error clearing database data: {e}")
        return False
    

# --- INTUIT HELPERS ---- #
# INTUIT_REDIRECT_URI="https://abb6-2a02-c7c-c476-4f00-a85e-243e-545b-2875.ngrok-free.app/payments/oauth_callback"
# INTUIT_REDIRECT_URI="https://main.d2wwdi7x8g70xe.amplifyapp.com/payments/oauth_callback"


def get_auth_client():
    """Helper to create AuthClient instance using Django settings."""
    return AuthClient(
        client_id=settings.INTUIT_CLIENT_ID,
        client_secret=settings.INTUIT_CLIENT_SECRET,
        redirect_uri=settings.NEW_INTUIT_REDIRECT_URI,
        environment=settings.INTUIT_ENVIRONMENT
    )

def is_token_expired(token_obj):
    """Checks if the provided QuickBooksToken model instance is expired."""
    if not token_obj or not token_obj.access_token or not token_obj.expires_at:
         print("Django Util: Token object missing or incomplete for expiry check.")
         return True # Assume expired if no valid token object/data

    # is_expired method is already on the model, including the timezone check
    is_expired = token_obj.is_expired()
    # print(f"Django Util: Token expiry check: Is expired? {is_expired} (Expires at: {token_obj.expires_at}, Now: {django_timezone.now()})")
    return is_expired


def refresh_intuit_tokens(token_obj):
    """Refreshes expired QuickBooks tokens and updates the database."""
    if not token_obj or not token_obj.refresh_token or not token_obj.realm_id:
        print("Django Util: No QuickBooks token object or refresh token available to refresh.")
        return False

    auth_client = get_auth_client()
    auth_client.refresh_token = token_obj.refresh_token
    auth_client.realm_id = token_obj.realm_id # Set realm_id on client for refresh

    print("Django Util: Attempting to refresh tokens...")
    try:
        auth_client.refresh()
        print("Django Util: AuthClient refresh call successful.")

        # Update the token_obj instance and save to the database
        token_obj.access_token = auth_client.access_token
        token_obj.refresh_token = auth_client.refresh_token
        token_obj.expires_at = django_timezone.now() + timedelta(seconds=auth_client.expires_in) # Use Django's timezone.now()
        # realm_id should remain the same

        token_obj.save() # Save updated instance to database
        print(f"Django Util: Tokens refreshed successfully and saved for Realm ID: {token_obj.realm_id}")
        return True
    except AuthClientError as e:
        # Handle specific Intuit OAuth errors during refresh
        print(f"Django Util: AuthClient Error during token refresh: {e}")
        # Depending on the error (e.g., invalid_grant due to expired refresh token)
        # you might want to clear the token data and force re-auth.
        # For now, just print and indicate failure.
        return False
    except Exception as e:
        print(f"Django Util: General Error during token refresh: {e}")
        return False

def verify_signature(request_body, signature_header):
    """Verify Intuit's webhook signature using Django settings."""
    verifier_token = settings.INTUIT_VERIFIER_TOKEN # Assuming this is in settings
    if not signature_header or not verifier_token:
        print("Django Util: Signature verification failed: Missing header or verifier token in settings.")
        return False

    try:
        # Ensure request_body is bytes
        if isinstance(request_body, str):
            request_body = request_body.encode('utf-8')

        computed_hash = hmac.new(
            key=verifier_token.encode('utf-8'), # Ensure key is bytes
            msg=request_body,
            digestmod=hashlib.sha256
        ).digest()

        computed_signature = base64.b64encode(computed_hash).decode('utf-8')

        is_valid = hmac.compare_digest(computed_signature, signature_header)
        if not is_valid:
             print(f"Django Util: Signature mismatch. Computed: {computed_signature}, Received: {signature_header}")
        return is_valid
    except Exception as e:
        print(f"Django Util: Error during signature verification: {e}")
        return False

def make_api_call(token_obj, url):
    """Helper to make a generic GET API call with token refresh retry logic."""
    if not token_obj or not token_obj.access_token:
         print(f"Django Util: Cannot make API call to {url}: No valid token object or access token.")
         return None

    headers = {
        "Authorization": f"Bearer {token_obj.access_token}",
        "Accept": "application/json",
        "Content-Type": "application/json" # Good practice to include
    }

    print(f"Django Util: Making API call to: {url}")
    print(f"Django Util: Using Access Token (first 10 chars): {token_obj.access_token[:10]}...")

    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)

        print(f"Django Util: API call successful: {url}")
        try:
            return response.json()
        except json.JSONDecodeError:
            print(f"Django Util: Warning: API call successful but response is not valid JSON: {response.text}")
            return None # Or handle appropriately

    except requests.exceptions.HTTPError as e:
        print(f"Django Util: HTTP error during API call to {url}: {e.response.status_code} - {e.response.text}")
        if e.response.status_code == 401:
            print("Django Util: API call failed with 401 Unauthorized. Token likely invalid/expired. Attempting refresh and retry.")
            if refresh_intuit_tokens(token_obj):
                 print("Django Util: Retry after 401. Using potentially new token from object.")
                 # token_obj is updated in place by refresh_intuit_tokens
                 if token_obj.access_token:
                      headers["Authorization"] = f"Bearer {token_obj.access_token}" # Update header
                      try:
                          response = requests.get(url, headers=headers) # Retry request
                          response.raise_for_status() # Check status again
                          print(f"Django Util: Retry after 401 successful: {url}")
                          return response.json()
                      except (requests.exceptions.RequestException, json.JSONDecodeError) as retry_e:
                           print(f"Django Util: Retry after 401 failed: {retry_e}")
                           return None
                 else:
                      print("Django Util: Failed to get a new access token from object after 401 retry refresh.")
            else:
                print("Django Util: Failed to refresh token after 401. Cannot retry API call.")
            return None # Return None if refresh or retry fails

        return None # Return None for other HTTP errors (non-401)

    except requests.exceptions.RequestException as e:
        print(f"Django Util: Error during API call to {url}: {e}")
        return None
    except Exception as e:
        print(f"Django Util: An unexpected error occurred during API call to {url}: {e}")
        return None


def get_payment_details_from_intuit(token_obj, realm_id, payment_id):
    """Get payment details from QuickBooks API"""
    if not token_obj or is_token_expired(token_obj):
         print("Django Util: Access token missing, expired, or close to expiry BEFORE API call. Attempting refresh proactively.")
         if not refresh_intuit_tokens(token_obj):
             print("Django Util: Failed proactive refresh. Cannot proceed with API call.")
             return None

    access_token = token_obj.access_token
    if not access_token:
        print("Django Util: No valid access token available AFTER proactive refresh for API call.")
        return None

    url = f"{settings.INTUIT_API_BASE_URL}/v3/company/{realm_id}/payment/{payment_id}"
    response_data = make_api_call(token_obj, url) # Pass token_obj to make_api_call
    return response_data.get("Payment") if response_data else None


def get_customer_details_from_intuit(token_obj, realm_id, customer_id):
    """Get customer details from QuickBooks API"""
    if not token_obj or is_token_expired(token_obj):
         print("Django Util: Access token missing, expired, or close to expiry BEFORE Customer API call. Attempting refresh proactively.")
         if not refresh_intuit_tokens(token_obj):
             print("Django Util: Failed proactive refresh for Customer API call. Cannot proceed.")
             return None

    access_token = token_obj.access_token
    if not access_token:
        print("Django Util: No valid access token available AFTER proactive refresh for Customer API call.")
        return None

    url = f"{settings.INTUIT_API_BASE_URL}/v3/company/{realm_id}/customer/{customer_id}?minorversion=75"
    response_data = make_api_call(token_obj, url)
    return response_data.get("Customer") if response_data else None


# --- Processing Functions (Called by Webhook View) ---

# def process_payment_create(realm_id, payment_id, payment_data, customer_data):
#     """Processes a Payment Create event and saves/updates transaction and credits."""
#     try:
#         amount = payment_data.get("TotalAmt", 0)
#         currency = payment_data.get("CurrencyRef", {}).get("value", "USD")
#         customer_ref = payment_data.get("CustomerRef")
#         customer_id_qbo = customer_ref.get("value") if customer_ref else None
#         customer_display_name = customer_ref.get("name") if customer_ref else "Unknown Customer"
#         customer_email = customer_data.get("PrimaryEmailAddr", {}).get("Address") if customer_data else None
#         # status = "Success"

#         # --- Determine tier and credits (using the defined dictionaries) ---
#         tier = None
#         credits = 0
#         # Ensure amount is float for lookup, handle potential errors
#         try:
#             amount_float = float(amount)
#             tier = AMOUNT_TO_TIER.get(amount_float)
#             if tier:
#                 credits = TIER_CREDITS.get(tier, 0)
#             else:
#                  tier = "N/A"
#                  credits = 0
#                  print(f"Django Util: No tier defined for amount: {amount}. Assigning N/A tier and 0 credits.")
#         except (ValueError, TypeError):
#              print(f"Django Util: Could not convert amount {amount} to float for tier lookup. Assigning N/A tier and 0 credits.")
#              tier = "N/A"
#              credits = 0


#         print(f"Django Util: Determined Tier: {tier}, Credits: {credits}")

#         # --- Implement your logic to find the corresponding Django User ---
#         # This is crucial! How do you link customer_id_qbo or customer_email to a User in your app?
#         # Example (very basic - find user by email if email exists):
#         user_for_transaction = None
#         if customer_email:
#             try:
#                 User = get_user_model()
#                 user_for_transaction = User.objects.filter(email=customer_email).first()
#                 if user_for_transaction:
#                      print(f"Django Util: Found user {user_for_transaction.username} for customer email {customer_email}.")
#                 else:
#                      print(f"Django Util: No user found with email {customer_email}.")
#             except Exception as e:
#                  print(f"Django Util: Error looking up user by email: {e}")

#         # if not user_for_transaction and customer_id_qbo:
#         #      try:
#         #           User = get_user_model()
#         #           user_for_transaction = User.objects.filter(quickbooks_customer_id=customer_id_qbo).first()
#         #           if user_for_transaction:
#         #                print(f"Django Util: Found user {user_for_transaction.username} for QBO Customer ID {customer_id_qbo}.")
#         #           else:
#         #                print(f"Django Util: No user found with QBO Customer ID {customer_id_qbo}.")
#         #      except Exception as e:
#         #           print(f"Django Util: Error looking up user by QBO Customer ID: {e}")


#         if not user_for_transaction:
#              print("Django Util: No user found to link payment transaction to. Skipping transaction and credit save.")
#              return False, "No user found to link transaction." # Indicate failure and message


#         # --- Create/Update PaymentTransaction in DB ---
#         try:
#             # Use get_or_create to handle potential duplicate webhooks
#             status = "Success" # Assuming success for this example, adjust as needed
#             transaction, created = PaymentTransaction.objects.get_or_create(
#                 realm_id=realm_id,
#                 transaction_id=payment_id,
#                 defaults={
#                     'user': user_for_transaction,
#                     'amount': amount,
#                     'currency': currency,
#                     'tier': tier,
#                     'status': status,   
#                     'credits': credits,
#                     'customer_id_qbo': customer_id_qbo,
#                     'customer_display_name': customer_display_name,
#                     'customer_email': customer_email,
#                     'payment_data': payment_data,
#                     'customer_data': customer_data,
#                 }
#             )
#             if not created:
#                 print(f"Django Util: PaymentTransaction for ID {payment_id} already exists. Updating fields.")
#                 # Update fields on the existing transaction if needed
#                 transaction.user = user_for_transaction # Ensure user is linked
#                 transaction.amount = amount
#                 transaction.currency = currency
#                 transaction.status = status # Update status if needed
#                 transaction.tier = tier
#                 transaction.credits = credits
#                 transaction.customer_id_qbo = customer_id_qbo
#                 transaction.customer_display_name = customer_display_name
#                 transaction.customer_email = customer_email
#                 transaction.payment_gateway_response = payment_data
#                 transaction.customer_gateway_response = customer_data
#                 # processed_at is auto_now
#                 transaction.save()
#                 # If updating, decide if credits need to be adjusted for the user
#                 # This is complex - e.g., if the amount/tier changed, you need to reverse old credits and add new ones.
#                 # For simplicity in this example, we'll just add credits on creation.
#                 # If you need robust update credit logic, it requires more thought.

#             else: # Transaction was just created
#                  print(f"Django Util: Created new PaymentTransaction for ID {payment_id}.")
#                  # --- Update Customer Credits in DB ---
#                  # This adds credits to the UserCredit table based on QBO Customer ID
#                  if customer_id_qbo:
#                      try:
#                          customer_credits_obj, credits_created = UserCredit.objects.get_or_create(
#                              customer_id_qbo=customer_id_qbo,
#                              defaults={'total_credits': credits}
#                          )
#                          if not credits_created:
#                               customer_credits_obj.total_credits += credits
#                               customer_credits_obj.save()
#                          print(f"Django Util: Updated credits for customer ID {customer_id_qbo} by {credits}. Total credits: {customer_credits_obj.total_credits}")

#                          # You might also update a 'credits' field directly on your Django User model if you have one
#                          # user_for_transaction.credits += credits # Assuming User model has a credits field
#                          # user_for_transaction.save()

#                      except Exception as e:
#                           print(f"Django Util: Error updating UserCredit for ID {customer_id_qbo}: {e}")
#                           # Decide how to handle credit update failure (e.g., log, alert)
#                  else:
#                      print("Django Util: Cannot update credits without a QBO Customer ID.")


#         except Exception as e:
#              print(f"Django Util: Error saving/updating PaymentTransaction for ID {payment_id}: {e}")
#              return False, f"Failed to save transaction to database: {e}" # Indicate failure

#         return True, "Successfully processed Payment Create." # Indicate success

#     except Exception as e:
#         print(f"Django Util: Unexpected error processing Payment Create for ID {payment_id}: {e}")
#         return False, f"Unexpected error: {e}"


# def process_payment_update(realm_id, payment_id, updated_payment_data, updated_customer_data):
#     """Processes a Payment Update event and updates transaction and potentially credits."""
#     try:
#         updated_amount = updated_payment_data.get("TotalAmt", 0)
#         updated_currency = updated_payment_data.get("CurrencyRef", {}).get("value", "USD")
#         customer_ref = updated_payment_data.get("CustomerRef")
#         customer_id_qbo = customer_ref.get("value") if customer_ref else None
#         updated_customer_display_name = customer_ref.get("name") if customer_ref else "Unknown Customer"
#         updated_customer_email = updated_customer_data.get("PrimaryEmailAddr", {}).get("Address") if updated_customer_data else None

#         # --- Determine new tier and credits (using the defined dictionaries) ---
#         updated_tier = None
#         updated_credits = 0
#         try:
#             updated_amount_float = float(updated_amount)
#             updated_tier = AMOUNT_TO_TIER.get(updated_amount_float)
#             if updated_tier:
#                 updated_credits = TIER_CREDITS.get(updated_tier, 0)
#             else:
#                  updated_tier = "N/A"
#                  updated_credits = 0
#                  print(f"Django Util: No tier defined for updated amount: {updated_amount}. Assigning N/A tier and 0 credits.")
#         except (ValueError, TypeError):
#              print(f"Django Util: Could not convert updated amount {updated_amount} to float for tier lookup. Assigning N/A tier and 0 credits.")
#              updated_tier = "N/A"
#              updated_credits = 0

#         print(f"Django Util: Determined Updated Tier: {updated_tier}, Credits: {updated_credits}")


#         # --- Implement your logic to find the corresponding Django User ---
#         # You need to find the user linked to the original transaction or the customer ID
#         user_for_transaction = None
#         if customer_id_qbo:
#              try:
#                  # Find the user linked to this QBO customer ID or linked to the original transaction
#                  # This logic depends heavily on how you link users to QBO customers
#                  # Example: Find a User who has this QBO Customer ID stored
#                  User = get_user_model()
#                  user_for_transaction = User.objects.filter(quickbooks_customer_id=customer_id_qbo).first() # Assuming a field exists
#                  if user_for_transaction:
#                       print(f"Django Util: Found user {user_for_transaction.username} for QBO Customer ID {customer_id_qbo}.")
#                  else:
#                       print(f"Django Util: No user found with QBO Customer ID {customer_id_qbo}.")
#              except Exception as e:
#                   print(f"Django Util: Error looking up user by QBO Customer ID for update: {e}")


#         if not user_for_transaction:
#              print("Django Util: No user found to link payment transaction update to. Skipping update.")
#              return False, "No user found to link transaction update."


#         # --- Find and Update PaymentTransaction in DB ---
#         try:
#             # Find the existing PaymentTransaction in your database
#             transaction = PaymentTransaction.objects.get(
#                 realm_id=realm_id,
#                 transaction_id=payment_id,
#                 user=user_for_transaction # Match based on the linked user
#             )
#             print(f"Django Util: Found existing PaymentTransaction for update (ID {payment_id}). Updating in database.")

#             # --- Credit Adjustment Logic (Complex for Updates) ---
#             # If the amount/tier changed, you need to adjust the user's credits.
#             # This requires knowing the original credits granted for this transaction.
#             # Option 1: Store original credits on the transaction model.
#             # Option 2: Re-calculate original credits based on original amount/tier (if stored).
#             # Option 3: Simply replace the credits for this transaction with the new amount (simpler, but less accurate if credits were manually adjusted).
#             # For simplicity here, we'll just update the transaction record with the new credit value.
#             # If you need credit adjustment, you'd calculate credit_adjustment = updated_credits - transaction.credits
#             # Then update the UserCredit/User credits by credit_adjustment.

#             # Update fields on the existing transaction
#             # Removed status update
#             transaction.amount = updated_amount
#             transaction.currency = updated_currency
#             transaction.tier = updated_tier
#             transaction.credits = updated_credits # Update credits field in transaction record
#             transaction.customer_id_qbo = customer_id_qbo # Update in case customer changed (rare)
#             transaction.customer_display_name = updated_customer_display_name
#             transaction.customer_email = updated_customer_email
#             transaction.payment_data = updated_payment_data
#             transaction.customer_data = updated_customer_data
#             transaction.processed_at = django_timezone.now() # Update timestamp
#             transaction.save()
#             print(f"Django Util: PaymentTransaction for ID {payment_id} updated in database.")

#             # If you implemented credit adjustment logic, call it here
#             # if credit_adjustment != 0 and customer_id_qbo:
#             #      update_customer_credits_in_db(customer_id_qbo, credit_adjustment)


#         except PaymentTransaction.DoesNotExist:
#              print(f"Django Util: Could not find existing PaymentTransaction for update (ID {payment_id}) in database. This might happen if the create webhook was missed.")
#              # Decide how to handle this - maybe insert as a new transaction with a special status?
#              # For now, just log and return failure.
#              return False, f"Transaction with ID {payment_id} not found for update."

#         except Exception as e:
#              print(f"Django Util: Error updating PaymentTransaction for ID {payment_id}: {e}")
#              return False, f"Failed to update transaction in database: {e}"


#         return True, "Successfully processed Payment Update." # Indicate success

#     except Exception as e:
#         print(f"Django Util: Unexpected error processing Payment Update for ID {payment_id}: {e}")
#         return False, f"Unexpected error: {e}"


# def process_payment_delete(realm_id, payment_id):
#     """Processes a Payment Delete event and removes transaction and potentially adjusts credits."""
#     try:
#         # --- Implement your delete logic here ---
#         # Find the transaction in the database and remove it or mark as deleted
#         # You likely need the transaction's credits to reverse them from the user/customer.
#         # This requires fetching the transaction before deleting it.
#         try:
#             transaction_to_delete = PaymentTransaction.objects.get(
#                 realm_id=realm_id,
#                 transaction_id=payment_id,
#                 # You might need to filter by user if you have multiple connections
#             )
#             print(f"Django Util: Found existing PaymentTransaction for delete (ID {payment_id}).")

#             # --- Credit Reversal Logic (Complex for Deletes) ---
#             # If you need to reverse credits, you need the original amount/tier/credits
#             # from the transaction before deleting it.
#             # Example: Get credits from the transaction object
#             # credits_to_reverse = transaction_to_delete.credits
#             # customer_id_qbo = transaction_to_delete.customer_id_qbo
#             # user_for_transaction = transaction_to_delete.user

#             transaction_to_delete.delete() # Delete the transaction record
#             print(f"Django Util: PaymentTransaction for ID {payment_id} deleted from database.")

#             # If you implemented credit reversal logic, call it here
#             # if credits_to_reverse > 0 and customer_id_qbo:
#             #      # Deduct credits from UserCredit table
#             #      update_customer_credits_in_db(customer_id_qbo, -credits_to_reverse) # Deduct credits
#             #      print(f"Django Util: Attempted credit reversal for customer {customer_id_qbo} by {credits_to_reverse}.")

#             #      # Deduct from User model credits if applicable
#             #      # if user_for_transaction and hasattr(user_for_transaction, 'credits'):
#             #      #      user_for_transaction.credits -= credits_to_reverse
#             #      #      user_for_transaction.save()


#         except PaymentTransaction.DoesNotExist:
#             print(f"Django Util: Could not find existing PaymentTransaction for delete (ID {payment_id}) in database.")
#             return False, f"Transaction with ID {payment_id} not found for delete."

#         except Exception as e:
#             print(f"Django Util: Error deleting PaymentTransaction for ID {payment_id}: {e}")
#             return False, f"Failed to delete transaction from database: {e}"


#         return True, "Successfully processed Payment Delete." # Indicate success

#     except Exception as e:
#         print(f"Django Util: Unexpected error processing Payment Delete for ID {payment_id}: {e}")
#         return False, f"Unexpected error: {e}"



def process_payment_create(realm_id, payment_id, payment_data, customer_data):
    """Processes a Payment Create event and saves transaction and updates user credits."""
    try:
        # Extract relevant data from API responses
        amount = payment_data.get("TotalAmt", 0)
        currency = payment_data.get("CurrencyRef", {}).get("value", "USD")
        transaction_date_str = payment_data.get("TxnDate") # Get the date string
        transaction_date = None
        if transaction_date_str:
             try:
                 # Assuming YYYY-MM-DD format from QBO API for DateField
                 transaction_date = datetime.strptime(transaction_date_str, '%Y-%m-%d').date()
             except ValueError:
                 print(f"Django Util: Warning: Could not parse transaction date string: {transaction_date_str}")

        customer_ref = payment_data.get("CustomerRef")
        customer_id_qbo = customer_ref.get("value") if customer_ref else None
        customer_display_name = customer_ref.get("name") if customer_ref else "Unknown Customer"
        customer_email = customer_data.get("PrimaryEmailAddr", {}).get("Address") if customer_data else None

        # Status should probably default to success for a *created* payment webhook,
        # but you might have logic to set it based on payment data if needed.
        # Using the model constant
        status = PaymentTransaction.STATUS_SUCCESS

        # --- Determine tier and credits (using the defined dictionaries) ---
        tier = None
        credits_calculated = 0 # Renamed to avoid conflict with model field
        try:
            # Ensure amount is float for lookup, handle potential errors
            amount_float = float(amount)
            tier = AMOUNT_TO_TIER.get(amount_float)
            if tier:
                credits_calculated = TIER_CREDITS.get(tier, 0)
                # Ensure credits is a Decimal if your model field is DecimalField
                # Since your model field is Decimal, keep as float/Decimal
            else:
                 tier = "N/A"
                 credits_calculated = 0
                 print(f"Django Util: No tier defined for amount: {amount}. Assigning N/A tier and 0 credits.")
        except (ValueError, TypeError):
             print(f"Django Util: Could not convert amount {amount} to float for tier lookup. Assigning N/A tier and 0 credits.")
             tier = "N/A"
             credits_calculated = 0

        print(f"Django Util: Determined Tier: {tier}, Calculated Credits: {credits_calculated}")


        user_for_transaction = None
        User = get_user_model() # Get the user model once

        # Priority 1: Try linking via QBO Customer ID if you store it on your User model or UserProfile
        # Assuming you added a quickbooks_customer_id field to UserProfile
        if customer_id_qbo:
            try:
                 user_for_transaction = User.objects.filter(user_profile__quickbooks_customer_id=customer_id_qbo).first()
                 if user_for_transaction:
                     print(f"Django Util: Found user {user_for_transaction.username} linked by QBO Customer ID {customer_id_qbo} on UserProfile.")
            except Exception as e:
                 print(f"Django Util: Error looking up user by QBO Customer ID on UserProfile: {e}")

        # Priority 2: If not found by ID, try linking via customer email
        if not user_for_transaction and customer_email:
            try:
                 user_for_transaction = User.objects.filter(email=customer_email).first()
                 if user_for_transaction:
                     print(f"Django Util: Found user {user_for_transaction.username} linked by email {customer_email}.")
                 else:
                     print(f"Django Util: No user found with email {customer_email}.")
            except Exception as e:
                 print(f"Django Util: Error looking up user by email: {e}")


        if not user_for_transaction:
            print("Django Util: No user found to link payment transaction to. Skipping transaction and credit save.")
            # Return a clear indication of failure
            return False, "No user found to link transaction."

        # --- Create/Update PaymentTransaction in DB ---
        try:
            # Use get_or_create based on the unique pair (realm_id, transaction_id)
            # This handles potential duplicate webhook deliveries gracefully
            transaction, created = PaymentTransaction.objects.get_or_create(
                realm_id=realm_id,
                transaction_id=payment_id,
                defaults={
                    'user': user_for_transaction,
                    'transaction_date': transaction_date, # Use parsed date
                    'amount': amount,
                    'currency': currency,
                    'tier': tier,
                    'status': status, # Use the determined status
                    'credits': credits_calculated, # Use calculated credits (matches Decimal field name)
                    'customer_name': customer_display_name,
                    'customer_email': customer_email,
                    'customer_id_qbo': customer_id_qbo, # Save the QBO Customer ID
                    # Use correct model field names for JSON data:
                    'payment_gateway_response': payment_data,
                    'customer_gateway_response': customer_data,
                    # created_at/updated_at are auto
                }
            )
            if not created:
                 # If it was not created, it means it already existed (likely a duplicate webhook)
                 print(f"Django Util: PaymentTransaction for ID {payment_id} already exists. Skipping duplicate processing.")
                 # Decide if you need to update fields or adjust credits on duplicates.
                 # For simplicity, we'll assume the first webhook delivery is the source of truth for credits.
                 # If you need to update fields on duplicates, add them here before saving.
                 # transaction.save() # Only if you updated fields

            else: # Transaction was just created (first webhook delivery)
                print(f"Django Util: Created new PaymentTransaction for ID {payment_id}.")
                # --- Update User Available Credits on UserProfile ---
                # Add credits to the linked user's UserProfile
                if user_for_transaction and credits_calculated > 0:
                     update_success = update_customer_credits_in_db(user_for_transaction, credits_calculated)
                     if update_success:
                         print(f"Django Util: Successfully added {credits_calculated} credits to user {user_for_transaction.username}.")
                     else:
                         print(f"Django Util: Failed to add credits to user {user_for_transaction.username}.")
                         # Decide how to handle credit update failure (e.g., log, alert, mark transaction)
                elif not user_for_transaction:
                     print("Django Util: Cannot update credits: No user found.")
                elif credits_calculated <= 0:
                    print(f"Django Util: No credits ({credits_calculated}) to add for this transaction amount.")


        except Exception as e:
             print(f"Django Util: Error saving/updating PaymentTransaction for ID {payment_id}: {e}")
             # Indicate failure and message
             return False, f"Failed to save transaction to database: {e}"

        # Indicate overall success if we reached here (created or skipped duplicate)
        return True, "Successfully processed Payment Create."

    except Exception as e:
        print(f"Django Util: Unexpected error processing Payment Create for ID {payment_id}: {e}")
        # Indicate failure and message
        return False, f"Unexpected error during processing: {e}"


def process_payment_update(realm_id, payment_id, customer_email, updated_payment_data, updated_customer_data):
    """Processes a Payment Update event and updates transaction and potentially adjusts credits."""
    try:
        # Find the existing PaymentTransaction in your database first
        # Need the user to filter correctly if unique_together is not enough
        # Or find by realm_id and transaction_id first, then get the user
        try:
             transaction = PaymentTransaction.objects.get(
                 realm_id=realm_id,
                 transaction_id=payment_id,
                 customer_email=customer_email
                 # if you have multiple users connecting, you might need an extra filter here
             )
             user_for_transaction = transaction.user # Get the user from the existing transaction
             print(f"Django Util: Found existing PaymentTransaction for update (ID {payment_id}, User: {user_for_transaction}).")
        except PaymentTransaction.DoesNotExist:
             print(f"Django Util: Could not find existing PaymentTransaction for update (ID {payment_id}). This might happen if the create webhook was missed.")
             # Decide how to handle this - maybe attempt to process as a create? Log and return failure.
             # Attempting to process as create might require more data extraction here.
             print("Django Util: Attempting to re-fetch data and process as if it were a delayed 'Create' webhook.")
             try:
                 # Re-fetch the full data using API
                 token_obj = get_quickbooks_token_obj(realm_id)
                 if token_obj:
                      re_fetched_payment_data = get_payment_details_from_intuit(token_obj, realm_id, payment_id)
                      if re_fetched_payment_data:
                          # Need customer data too
                          cust_ref = re_fetched_payment_data.get("CustomerRef")
                          cust_id_qbo = cust_ref.get("value") if cust_ref else None
                          re_fetched_customer_data = None
                          if cust_id_qbo:
                              re_fetched_customer_data = get_customer_details_from_intuit(token_obj, realm_id, cust_id_qbo)

                          # Now process as a create with re-fetched data
                          print("Django Util: Re-fetched data, processing update as create.")
                          return process_payment_create(realm_id, payment_id, re_fetched_payment_data, re_fetched_customer_data)
                      else:
                           print("Django Util: Failed to re-fetch payment data for update. Cannot process.")
                 else:
                     print("Django Util: No token available to re-fetch data for update. Cannot process.")

             except Exception as re_fetch_e:
                 print(f"Django Util: Error during re-fetch for update: {re_fetch_e}")


             return False, f"Transaction with ID {payment_id} not found for update, re-fetch failed."

        # If transaction was found, proceed with update logic
        # Extract relevant updated data
        updated_amount = updated_payment_data.get("TotalAmt", 0)
        updated_currency = updated_payment_data.get("CurrencyRef", {}).get("value", "USD")
        customer_ref = updated_payment_data.get("CustomerRef")
        customer_id_qbo = customer_ref.get("value") if customer_ref else None # Update in case customer changed (rare)
        updated_customer_display_name = customer_ref.get("name") if customer_ref else "Unknown Customer"
        updated_customer_email = updated_customer_data.get("PrimaryEmailAddr", {}).get("Address") if updated_customer_data else None

        # --- Credit Adjustment Logic (Complex for Updates) ---
        # If the amount/tier changed, you need to adjust the user's credits.
        # This requires knowing the original credits granted for this transaction.
        # Compare the new credits with the old credits stored on the transaction.
        old_credits = transaction.credits # Get credits from the existing transaction object

        updated_tier = None
        updated_credits_calculated = 0
        try:
            updated_amount_float = float(updated_amount)
            updated_tier = AMOUNT_TO_TIER.get(updated_amount_float)
            if updated_tier:
                updated_credits_calculated = TIER_CREDITS.get(updated_tier, 0)
            else:
                 updated_tier = "N/A"
                 updated_credits_calculated = 0
        except (ValueError, TypeError):
             updated_tier = "N/A"
             updated_credits_calculated = 0

        credit_adjustment = updated_credits_calculated - old_credits # Calculate the difference

        print(f"Django Util: Processing Update for ID {payment_id}. Old Credits: {old_credits}, New Calculated Credits: {updated_credits_calculated}, Adjustment: {credit_adjustment}")


        # Update fields on the existing transaction instance
        # Use the instance we fetched: `transaction`
        # Status might change on update (e.g., Voided), you might want to handle that
        # For now, we won't change status automatically unless specific logic is added
        # transaction.status = determined_status # Example if you add status logic

        # transaction.status = PaymentTransaction.STATUS_SUCCESS
        transaction.amount = updated_amount
        transaction.currency = updated_currency
        transaction.tier = updated_tier
        transaction.credits = updated_credits_calculated # Update credits field in transaction record
        transaction.customer_name = updated_customer_display_name
        transaction.customer_email = updated_customer_email
        transaction.customer_id_qbo = customer_id_qbo # Update in case customer changed (rare)
        # Use correct model field names for JSON data:
        transaction.payment_gateway_response = updated_payment_data
        transaction.customer_gateway_response = updated_customer_data
        # updated_at is auto_now

        transaction.save() # Save the updated transaction instance
        print(f"Django Util: PaymentTransaction for ID {payment_id} updated in database.")

        # --- Adjust User Available Credits on UserProfile ---
        # If there was a credit adjustment, update the user's total credits on UserProfile
        if user_for_transaction and credit_adjustment != 0:
             update_success = update_customer_credits_in_db(user_for_transaction, credit_adjustment)
             if update_success:
                 print(f"Django Util: Successfully adjusted credits for user {user_for_transaction.username} by {credit_adjustment}.")
             else:
                 print(f"Django Util: Failed to adjust credits for user {user_for_transaction.username}.")
                 # Decide how to handle credit update failure (e.g., log, alert, mark transaction)
        elif not user_for_transaction:
             print("Django Util: Cannot adjust credits: No user found linked to transaction.")
        elif credit_adjustment == 0:
            print("Django Util: No credit adjustment needed.")


        return True, "Successfully processed Payment Update." # Indicate success

    except Exception as e:
        print(f"Django Util: Unexpected error processing Payment Update for ID {payment_id}: {e}")
        return False, f"Unexpected error during processing: {e}"


def process_payment_delete(realm_id, payment_id):
    """Processes a Payment Delete event and removes transaction and potentially adjusts credits."""
    try:
        # Find the transaction in the database and remove it or mark as deleted
        # You likely need the transaction's credits to reverse them from the user/customer.
        # This requires fetching the transaction before deleting it.
        try:
            # Find the transaction based on realm_id and transaction_id
            # Assuming unique_together covers this lookup
            transaction_to_delete = PaymentTransaction.objects.get(
                realm_id=realm_id,
                transaction_id=payment_id,
                # If you have multiple users connecting, you might need an extra filter here
            )
            print(f"Django Util: Found existing PaymentTransaction for delete (ID {payment_id}).")

            # --- Credit Reversal Logic (Complex for Deletes) ---
            # If you need to reverse credits granted for this transaction:
            credits_to_reverse = transaction_to_delete.credits # Get the credits granted for this transaction
            user_for_transaction = transaction_to_delete.user # Get the linked user

            if user_for_transaction and credits_to_reverse > 0: # Only reverse if user and credits > 0
                # Reverse the credits (add a negative amount)
                update_success = update_customer_credits_in_db(user_for_transaction, -credits_to_reverse)
                if update_success:
                    print(f"Django Util: Successfully reversed {credits_to_reverse} credits from user {user_for_transaction.username}.")
                else:
                    print(f"Django Util: Failed to reverse credits for user {user_for_transaction.username}.")
                    # Decide how to handle credit reversal failure

            elif not user_for_transaction:
                print("Django Util: Cannot reverse credits: No user found linked to transaction.")
            elif credits_to_reverse <= 0:
                 print("Django Util: No credits to reverse for this transaction.")


            # --- Delete the transaction record ---
            transaction_to_delete.delete() # Delete the model instance
            print(f"Django Util: PaymentTransaction for ID {payment_id} deleted from database.")

        except PaymentTransaction.DoesNotExist:
            print(f"Django Util: Could not find existing PaymentTransaction for delete (ID {payment_id}) in database.")
            return False, f"Transaction with ID {payment_id} not found for delete."

        except Exception as e:
            print(f"Django Util: Error deleting PaymentTransaction for ID {payment_id}: {e}")
            return False, f"Failed to delete transaction from database: {e}"


        return True, "Successfully processed Payment Delete." # Indicate success

    except Exception as e:
        print(f"Django Util: Unexpected error processing Payment Delete for ID {payment_id}: {e}")
        return False, f"Unexpected error: {e}"

