import hmac
import hashlib
import base64
import requests
import json
import os
from datetime import datetime, timedelta, timezone # Import timezone

from django.conf import settings
from django.utils import timezone as django_timezone # Use alias to avoid conflict with datetime.timezone
from django.contrib.auth import get_user_model # Needed for linking transactions to users

# Import Intuit libraries
from intuitlib.client import AuthClient
from intuitlib.enums import Scopes
from intuitlib.exceptions import AuthClientError

# Import your models
from .models import QuickBooksToken, PaymentTransaction, UserCredit

# --- Tiers and Credits Definitions ---
# Define these here as they are used by the processing logic
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


# --- Helper Functions (Database Interaction) ---
# These functions interact directly with your Django models

def get_quickbooks_token_obj(realm_id=None):
    """Retrieves the QuickBooksToken model instance from the database."""
    try:
        # For a single-merchant app, use get_singleton()
        # For multi-merchant, filter by user or realm_id if needed
        if realm_id:
             # If you need to support multiple realms, filter here
             token = QuickbooksToken.objects.filter(realm_id=realm_id).first()
        else:
             # Assuming singleton for this example
             token = QuickbooksToken.get_singleton()

        if token:
            print(f"Django Util: Loaded token object from DB for Realm ID: {token.realm_id}")
            return token
        else:
            print("Django Util: No token object found in database.")
            return None
    except Exception as e:
        print(f"Django Util: Error loading token object from database: {e}")
        return None


def save_quickbooks_token_obj(access_token, refresh_token, expires_in, realm_id):
    """Saves or updates the QuickBooksToken instance in the database."""
    try:
        # For single-merchant, get or create the token record based on realm_id or a fixed key
        # Using realm_id as the unique identifier for upsert
        token, created = QuickbooksToken.objects.get_or_create(
            realm_id=realm_id,
            defaults={
                'access_token': access_token,
                'refresh_token': refresh_token,
                'expires_at': django_timezone.now() + timedelta(seconds=expires_in), # Use Django's timezone.now()
            }
        )
        # If not created, update the existing one
        if not created:
            token.access_token = access_token
            token.refresh_token = refresh_token
            token.expires_at = django_timezone.now() + timedelta(seconds=expires_in) # Use Django's timezone.now()
            # realm_id is already set and unique

        # Need to handle linking to a Django User if this is a multi-user app
        # token.user = request.user # Example if linking to current user during OAuth callback

        token.save()
        print(f"Django Util: QuickBooks token data saved/updated in DB for Realm ID: {token.realm_id}")
        return token
    except Exception as e:
        print(f"Django Util: Error saving QuickBooks token data: {e}")
        return None # Indicate save failed


def update_customer_credits_in_db(customer_id_qbo, credits_to_add):
     """Updates customer credits in the database."""
     if not customer_id_qbo:
          print("Django Util: Cannot update credits without a QBO Customer ID.")
          return False

     try:
         # Use get_or_create to add credits or set initial
         customer_credits_obj, created = UserCredit.objects.get_or_create(
             customer_id_qbo=customer_id_qbo,
             defaults={'total_credits': credits_to_add}
         )
         if not created:
              customer_credits_obj.total_credits += credits_to_add
              customer_credits_obj.save()
         print(f"Django Util: Updated credits for customer ID {customer_id_qbo} by {credits_to_add}. Total credits: {customer_credits_obj.total_credits}")

         # You might also update a 'credits' field directly on your Django User model if you have one
         # Find the user linked to this QBO customer ID
         # try:
         #      User = get_user_model()
         #      user = User.objects.filter(quickbooks_customer_id=customer_id_qbo).first() # Assuming a field exists
         #      if user and hasattr(user, 'credits'): # Assuming User model has a credits field
         #           user.credits += credits_to_add
         #           user.save()
         #           print(f"Django Util: Updated credits on User model for {user.username}.")
         # except Exception as e:
         #      print(f"Django Util: Error updating User model credits: {e}")


         return True
     except Exception as e:
         print(f"Django Util: Error updating User Credit in database: {e}")
         return False


def get_transactions_from_db(realm_id=None):
    """Retrieves payment transactions from the database."""
    try:
        if realm_id:
             transactions = PaymentTransaction.objects.filter(realm_id=realm_id).order_by('-processed_at')
        else:
             transactions = PaymentTransaction.objects.all().order_by('-processed_at') # Get all if no realm_id
        print(f"Django Util: Retrieved {len(transactions)} transactions from database.")
        # Return a list of dictionaries for easier serialization if needed outside a ViewSet
        return list(transactions.values()) # Use .values() to get dictionaries
    except Exception as e:
        print(f"Django Util: Error retrieving transactions from database: {e}")
        return []


def get_credits_from_db(customer_id_qbo=None):
    """Retrieves customer credits from the database."""
    try:
        if customer_id_qbo:
             credits_data = UserCredit.objects.filter(customer_id_qbo=customer_id_qbo).first()
             return {credits_data.customer_id_qbo: float(credits_data.total_credits)} if credits_data else {}
        else:
             credits_list = UserCredit.objects.all()
             # Convert list of model instances to a single dict {customer_id: total_credits}
             credits_dict = {item.customer_id_qbo: float(item.total_credits) for item in credits_list}
             return credits_dict
        print(f"Django Util: Retrieved credits data from database.")
    except Exception as e:
        print(f"Django Util: Error retrieving credits from database: {e}")
        return {}


def clear_db_data():
    """Clears all QuickBooks related data from the database."""
    try:
        # Delete all records from the models
        QuickBooksToken.objects.all().delete()
        PaymentTransaction.objects.all().delete()
        UserCredit.objects.all().delete()
        print("Django Util: All QuickBooks related data cleared from database.")
        return True
    except Exception as e:
        print(f"Django Util: Error clearing database data: {e}")
        return False


# --- Helper Functions (Intuit API Interaction) ---

def get_auth_client():
    """Helper to create AuthClient instance using Django settings."""
    return AuthClient(
        client_id=settings.INTUIT_CLIENT_ID,
        client_secret=settings.INTUIT_CLIENT_SECRET,
        redirect_uri=settings.INTUIT_REDIRECT_URI,
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
    verifier_token = settings.INTUIT_WEBHOOK_VERIFIER_TOKEN # Assuming this is in settings
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

    response = requests.get(url, headers=headers)

    if response.status_code == 200:
        print(f"Django Util: API call successful: {url}")
        try:
            return response.json()
        except json.JSONDecodeError:
            print(f"Django Util: Warning: API call successful but response is not valid JSON: {response.text}")
            return None # Or handle appropriately
    elif response.status_code == 401:
        print(f"Django Util: API call failed with 401 Unauthorized: {url}. Attempting refresh and retry.")
        # If 401 occurs, it means the token was bad right now. Try one more refresh and retry.
        if refresh_intuit_tokens(token_obj): # Refresh the token (updates the token_obj in place and saves to DB)
             print("Django Util: Retry after 401. Using potentially new token from object.")
             # token_obj is updated in place by refresh_intuit_tokens
             if token_obj.access_token:
                  headers["Authorization"] = f"Bearer {token_obj.access_token}" # Update header
                  response = requests.get(url, headers=headers) # Retry request
                  if response.status_code == 200:
                      print(f"Django Util: Retry after 401 successful: {url}")
                      try:
                          return response.json()
                      except json.JSONDecodeError:
                           print(f"Django Util: Warning: Retry API call successful but response is not valid JSON: {response.text}")
                           return None
                  else:
                      print(f"Django Util: Retry after 401 failed with status code: {response.status_code}, response: {response.text}")
                      print(f"Retry failed body: {response.text}")
             else:
                 print("Django Util: Failed to get a new access token from object after 401 retry refresh.")
        else:
            print("Django Util: Failed to refresh token after 401. Cannot retry API call.")
        return None # Return None if refresh or retry fails
    else:
        print(f"Django Util: API call failed. Status code: {response.status_code}, response: {response.text}")
        print(f"Django Util: Response body: {response.text}")
        return None # Return None for non-200/401 errors


def get_payment_details_from_intuit(token_obj, realm_id, payment_id):
    """Get payment details from QuickBooks API"""
    # Check if token is missing, expired, or close to expiry and refresh if needed proactively
    if not token_obj or is_token_expired(token_obj):
         print("Django Util: Access token missing, expired, or close to expiry BEFORE API call. Attempting refresh proactively.")
         # refresh_intuit_tokens refreshes the token_obj in place and saves to DB
         if not refresh_intuit_tokens(token_obj): # This function loads, refreshes, and saves to DB
             print("Django Util: Failed proactive refresh. Cannot proceed with API call.")
             return None
         # token_obj is updated in place by refresh_intuit_tokens

    # Get the access token from the (potentially refreshed) token object
    access_token = token_obj.access_token
    if not access_token:
        print("Django Util: No valid access token available AFTER proactive refresh for API call.")
        return None


    url = f"{settings.INTUIT_API_BASE_URL}/v3/company/{realm_id}/payment/{payment_id}"
    response_data = make_api_call(token_obj, url) # Pass token_obj to make_api_call
    return response_data.get("Payment") if response_data else None


def get_customer_details_from_intuit(token_obj, realm_id, customer_id):
    """Get customer details from QuickBooks API"""
    # Check if token is missing, expired, or close to expiry and refresh if needed proactively
    if not token_obj or is_token_expired(token_obj):
         print("Django Util: Access token missing, expired, or close to expiry BEFORE Customer API call. Attempting refresh proactively.")
         if not refresh_intuit_tokens(token_obj): # refresh_intuit_tokens updates token_obj in place
             print("Django Util: Failed proactive refresh for Customer API call. Cannot proceed.")
             return None
         # token_obj is updated in place

    # Get the access token from the (potentially refreshed) token object
    access_token = token_obj.access_token
    if not access_token:
        print("Django Util: No valid access token available AFTER proactive refresh for Customer API call.")
        return None

    # Construct the customer API endpoint URL
    url = f"{settings.INTUIT_API_BASE_URL}/v3/company/{realm_id}/customer/{customer_id}?minorversion=75"
    response_data = make_api_call(token_obj, url) # Pass token_obj to make_api_call
    return response_data.get("Customer") if response_data else None


# --- Processing Functions (Called by Webhook View) ---

def process_payment_create(realm_id, payment_id, payment_data, customer_data):
    """Processes a Payment Create event and saves/updates transaction and credits."""
    try:
        amount = payment_data.get("TotalAmt", 0)
        currency = payment_data.get("CurrencyRef", {}).get("value", "USD")
        customer_ref = payment_data.get("CustomerRef")
        customer_id_qbo = customer_ref.get("value") if customer_ref else None
        customer_display_name = customer_ref.get("name") if customer_ref else "Unknown Customer"
        customer_email = customer_data.get("PrimaryEmailAddr", {}).get("Address") if customer_data else None

        # --- Determine tier and credits (using the defined dictionaries) ---
        tier = None
        credits = 0
        # Ensure amount is float for lookup, handle potential errors
        try:
            amount_float = float(amount)
            tier = AMOUNT_TO_TIER.get(amount_float)
            if tier:
                credits = TIER_CREDITS.get(tier, 0)
            else:
                 tier = "N/A"
                 credits = 0
                 print(f"Django Util: No tier defined for amount: {amount}. Assigning N/A tier and 0 credits.")
        except (ValueError, TypeError):
             print(f"Django Util: Could not convert amount {amount} to float for tier lookup. Assigning N/A tier and 0 credits.")
             tier = "N/A"
             credits = 0


        print(f"Django Util: Determined Tier: {tier}, Credits: {credits}")

        # --- Implement your logic to find the corresponding Django User ---
        # This is crucial! How do you link customer_id_qbo or customer_email to a User in your app?
        # Example (very basic - find user by email if email exists):
        user_for_transaction = None
        if customer_email:
            try:
                User = get_user_model()
                user_for_transaction = User.objects.filter(email=customer_email).first()
                if user_for_transaction:
                     print(f"Django Util: Found user {user_for_transaction.username} for customer email {customer_email}.")
                else:
                     print(f"Django Util: No user found with email {customer_email}.")
            except Exception as e:
                 print(f"Django Util: Error looking up user by email: {e}")

        # If no user found by email, maybe try linking via QBO Customer ID if you store it on your User model
        if not user_for_transaction and customer_id_qbo:
             try:
                  User = get_user_model()
                  user_for_transaction = User.objects.filter(quickbooks_customer_id=customer_id_qbo).first() # Assuming a field exists
                  if user_for_transaction:
                       print(f"Django Util: Found user {user_for_transaction.username} for QBO Customer ID {customer_id_qbo}.")
                  else:
                       print(f"Django Util: No user found with QBO Customer ID {customer_id_qbo}.")
             except Exception as e:
                  print(f"Django Util: Error looking up user by QBO Customer ID: {e}")


        if not user_for_transaction:
             print("Django Util: No user found to link payment transaction to. Skipping transaction and credit save.")
             return False, "No user found to link transaction." # Indicate failure and message


        # --- Create/Update PaymentTransaction in DB ---
        try:
            # Use get_or_create to handle potential duplicate webhooks
            transaction, created = PaymentTransaction.objects.get_or_create(
                realm_id=realm_id,
                transaction_id=payment_id,
                defaults={
                    'user': user_for_transaction,
                    # removed status field
                    'amount': amount,
                    'currency': currency,
                    'tier': tier,
                    'credits': credits,
                    'customer_id_qbo': customer_id_qbo,
                    'customer_display_name': customer_display_name,
                    'customer_email': customer_email,
                    'payment_data': payment_data,
                    'customer_data': customer_data,
                    # processed_at is auto_now_add
                }
            )
            if not created:
                print(f"Django Util: PaymentTransaction for ID {payment_id} already exists. Updating fields.")
                # Update fields on the existing transaction if needed
                transaction.user = user_for_transaction # Ensure user is linked
                # Removed status update
                transaction.amount = amount
                transaction.currency = currency
                transaction.tier = tier
                transaction.credits = credits # Update credits field in transaction record
                transaction.customer_id_qbo = customer_id_qbo
                transaction.customer_display_name = customer_display_name
                transaction.customer_email = customer_email
                transaction.payment_gateway_response = payment_data
                transaction.customer_gateway_response = customer_data
                # processed_at is auto_now
                transaction.save()
                # If updating, decide if credits need to be adjusted for the user
                # This is complex - e.g., if the amount/tier changed, you need to reverse old credits and add new ones.
                # For simplicity in this example, we'll just add credits on creation.
                # If you need robust update credit logic, it requires more thought.

            else: # Transaction was just created
                 print(f"Django Util: Created new PaymentTransaction for ID {payment_id}.")
                 # --- Update Customer Credits in DB ---
                 # This adds credits to the UserCredits table based on QBO Customer ID
                 if customer_id_qbo:
                     try:
                         customer_credits_obj, credits_created = UserCredits.objects.get_or_create(
                             customer_id_qbo=customer_id_qbo,
                             defaults={'total_credits': credits}
                         )
                         if not credits_created:
                              customer_credits_obj.total_credits += credits
                              customer_credits_obj.save()
                         print(f"Django Util: Updated credits for customer ID {customer_id_qbo} by {credits}. Total credits: {customer_credits_obj.total_credits}")

                         # You might also update a 'credits' field directly on your Django User model if you have one
                         # user_for_transaction.credits += credits # Assuming User model has a credits field
                         # user_for_transaction.save()

                     except Exception as e:
                          print(f"Django Util: Error updating UserCredits for ID {customer_id_qbo}: {e}")
                          # Decide how to handle credit update failure (e.g., log, alert)
                 else:
                     print("Django Util: Cannot update credits without a QBO Customer ID.")


        except Exception as e:
             print(f"Django Util: Error saving/updating PaymentTransaction for ID {payment_id}: {e}")
             return False, f"Failed to save transaction to database: {e}" # Indicate failure

        return True, "Successfully processed Payment Create." # Indicate success

    except Exception as e:
        print(f"Django Util: Unexpected error processing Payment Create for ID {payment_id}: {e}")
        return False, f"Unexpected error: {e}"


def process_payment_update(realm_id, payment_id, updated_payment_data, updated_customer_data):
    """Processes a Payment Update event and updates transaction and potentially credits."""
    try:
        updated_amount = updated_payment_data.get("TotalAmt", 0)
        updated_currency = updated_payment_data.get("CurrencyRef", {}).get("value", "USD")
        customer_ref = updated_payment_data.get("CustomerRef")
        customer_id_qbo = customer_ref.get("value") if customer_ref else None
        updated_customer_display_name = customer_ref.get("name") if customer_ref else "Unknown Customer"
        updated_customer_email = updated_customer_data.get("PrimaryEmailAddr", {}).get("Address") if updated_customer_data else None

        # --- Determine new tier and credits (using the defined dictionaries) ---
        updated_tier = None
        updated_credits = 0
        try:
            updated_amount_float = float(updated_amount)
            updated_tier = AMOUNT_TO_TIER.get(updated_amount_float)
            if updated_tier:
                updated_credits = TIER_CREDITS.get(updated_tier, 0)
            else:
                 updated_tier = "N/A"
                 updated_credits = 0
                 print(f"Django Util: No tier defined for updated amount: {updated_amount}. Assigning N/A tier and 0 credits.")
        except (ValueError, TypeError):
             print(f"Django Util: Could not convert updated amount {updated_amount} to float for tier lookup. Assigning N/A tier and 0 credits.")
             updated_tier = "N/A"
             updated_credits = 0

        print(f"Django Util: Determined Updated Tier: {updated_tier}, Credits: {updated_credits}")


        # --- Implement your logic to find the corresponding Django User ---
        # You need to find the user linked to the original transaction or the customer ID
        user_for_transaction = None
        if customer_id_qbo:
             try:
                 # Find the user linked to this QBO customer ID or linked to the original transaction
                 # This logic depends heavily on how you link users to QBO customers
                 # Example: Find a User who has this QBO Customer ID stored
                 User = get_user_model()
                 user_for_transaction = User.objects.filter(quickbooks_customer_id=customer_id_qbo).first() # Assuming a field exists
                 if user_for_transaction:
                      print(f"Django Util: Found user {user_for_transaction.username} for QBO Customer ID {customer_id_qbo}.")
                 else:
                      print(f"Django Util: No user found with QBO Customer ID {customer_id_qbo}.")
             except Exception as e:
                  print(f"Django Util: Error looking up user by QBO Customer ID for update: {e}")


        if not user_for_transaction:
             print("Django Util: No user found to link payment transaction update to. Skipping update.")
             return False, "No user found to link transaction update."


        # --- Find and Update PaymentTransaction in DB ---
        try:
            # Find the existing PaymentTransaction in your database
            transaction = PaymentTransaction.objects.get(
                realm_id=realm_id,
                transaction_id=payment_id,
                user=user_for_transaction # Match based on the linked user
            )
            print(f"Django Util: Found existing PaymentTransaction for update (ID {payment_id}). Updating in database.")

            # --- Credit Adjustment Logic (Complex for Updates) ---
            # If the amount/tier changed, you need to adjust the user's credits.
            # This requires knowing the original credits granted for this transaction.
            # Option 1: Store original credits on the transaction model.
            # Option 2: Re-calculate original credits based on original amount/tier (if stored).
            # Option 3: Simply replace the credits for this transaction with the new amount (simpler, but less accurate if credits were manually adjusted).
            # For simplicity here, we'll just update the transaction record with the new credit value.
            # If you need credit adjustment, you'd calculate credit_adjustment = updated_credits - transaction.credits
            # Then update the UserCredits/User credits by credit_adjustment.

            # Update fields on the existing transaction
            # Removed status update
            transaction.amount = updated_amount
            transaction.currency = updated_currency
            transaction.tier = updated_tier
            transaction.credits = updated_credits # Update credits field in transaction record
            transaction.customer_id_qbo = customer_id_qbo # Update in case customer changed (rare)
            transaction.customer_display_name = updated_customer_display_name
            transaction.customer_email = updated_customer_email
            transaction.payment_data = updated_payment_data
            transaction.customer_data = updated_customer_data
            transaction.processed_at = django_timezone.now() # Update timestamp
            transaction.save()
            print(f"Django Util: PaymentTransaction for ID {payment_id} updated in database.")

            # If you implemented credit adjustment logic, call it here
            # if credit_adjustment != 0 and customer_id_qbo:
            #      update_customer_credits_in_db(customer_id_qbo, credit_adjustment)


        except PaymentTransaction.DoesNotExist:
             print(f"Django Util: Could not find existing PaymentTransaction for update (ID {payment_id}) in database. This might happen if the create webhook was missed.")
             # Decide how to handle this - maybe insert as a new transaction with a special status?
             # For now, just log and return failure.
             return False, f"Transaction with ID {payment_id} not found for update."

        except Exception as e:
             print(f"Django Util: Error updating PaymentTransaction for ID {payment_id}: {e}")
             return False, f"Failed to update transaction in database: {e}"


        return True, "Successfully processed Payment Update." # Indicate success

    except Exception as e:
        print(f"Django Util: Unexpected error processing Payment Update for ID {payment_id}: {e}")
        return False, f"Unexpected error: {e}"


def process_payment_delete(realm_id, payment_id):
    """Processes a Payment Delete event and removes transaction and potentially adjusts credits."""
    try:
        # --- Implement your delete logic here ---
        # Find the transaction in the database and remove it or mark as deleted
        # You likely need the transaction's credits to reverse them from the user/customer.
        # This requires fetching the transaction before deleting it.
        try:
            transaction_to_delete = PaymentTransaction.objects.get(
                realm_id=realm_id,
                transaction_id=payment_id,
                # You might need to filter by user if you have multiple connections
            )
            print(f"Django Util: Found existing PaymentTransaction for delete (ID {payment_id}).")

            # --- Credit Reversal Logic (Complex for Deletes) ---
            # If you need to reverse credits, you need the original amount/tier/credits
            # from the transaction before deleting it.
            # Example: Get credits from the transaction object
            # credits_to_reverse = transaction_to_delete.credits
            # customer_id_qbo = transaction_to_delete.customer_id_qbo
            # user_for_transaction = transaction_to_delete.user

            transaction_to_delete.delete() # Delete the transaction record
            print(f"Django Util: PaymentTransaction for ID {payment_id} deleted from database.")

            # If you implemented credit reversal logic, call it here
            # if credits_to_reverse > 0 and customer_id_qbo:
            #      # Deduct credits from UserCredits table
            #      update_customer_credits_in_db(customer_id_qbo, -credits_to_reverse) # Deduct credits
            #      print(f"Django Util: Attempted credit reversal for customer {customer_id_qbo} by {credits_to_reverse}.")

            #      # Deduct from User model credits if applicable
            #      # if user_for_transaction and hasattr(user_for_transaction, 'credits'):
            #      #      user_for_transaction.credits -= credits_to_reverse
            #      #      user_for_transaction.save()


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