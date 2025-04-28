import os
import json
from django.shortcuts import get_object_or_404
from django.contrib.auth import get_user_model
from django.http import HttpResponse, JsonResponse, HttpResponseForbidden, HttpResponseBadRequest, HttpResponseRedirect
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt
from django.urls import reverse
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, viewsets
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from .models import PaymentTransaction
from .serializers import PaymentTransactionSerializer
from .utils import (
    get_quickbooks_token_obj, 
    save_quickbooks_token_obj, 
    update_customer_credits_in_db,
    get_transactions_from_db,
    get_credits_from_db,
    clear_db_data,
    get_auth_client,
    refresh_intuit_tokens,
    verify_signature,
    make_api_call,
    get_payment_details_from_intuit,
    get_customer_details_from_intuit,
    process_payment_create,
    process_payment_update
)

from django.conf import settings
from intuitlib.client import AuthClient
from intuitlib.enums import Scopes
from intuitlib.exceptions import AuthClientError

# Fixed credits for each payment tier
TIER_CREDITS = {
    "tester": 1,
    "starter": 4,
    "growth": 6,
    "pro": 8,
    "ultimate": 12,
}

# {
#   "Payment": {
#     "SyncToken": "0", 
#     "domain": "QBO", 
#     "DepositToAccountRef": {
#       "value": "4"
#     }, 
#     "UnappliedAmt": 25.0, 
#     "TxnDate": "2014-12-30", 
#     "TotalAmt": 25.0, 
#     "ProjectRef": {
#       "value": "39298034"
#     }, 
#     "ProcessPayment": false, 
#     "sparse": false, 
#     "Line": [], 
#     "CustomerRef": {
#       "name": "Red Rock Diner", 
#       "value": "20"
#     }, 
#     "Id": "154", 
#     "MetaData": {
#       "CreateTime": "2014-12-30T10:26:03-08:00", 
#       "LastUpdatedTime": "2014-12-30T10:26:03-08:00"
#     }
#   }, 
#   "time": "2014-12-30T10:26:03.668-08:00"
# }

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
    permission_classes = [IsAuthenticated]

    def post(self, request):
        data = request.data
        transaction_id = data.get("Payment", {}).get("Id")
        amount = data.get("TotalAmt")
        transaction_date = data.get("TxnDate")
        status_str = "success" if data.get("ProcessPayment", False) else "failed"
        tier = data.get("tier")
        user_email = data.get("user_email")
        gateway_response = data.get("gateway_response", {})

        # Validate required fields
        if not transaction_id or not status_str or not tier:
            return Response({"error": "Missing required fields."}, status=status.HTTP_400_BAD_REQUEST)
        
        tier = tier.lower()
        if tier not in TIER_CREDITS:
            return Response({"error": "Invalid tier specified."}, status=status.HTTP_400_BAD_REQUEST)
        
        # Determine credits based on tier if payment succeeded; else, no credits.
        credits_to_add = TIER_CREDITS[tier] if status_str.lower() == "success" else 0


        # Create or update the PaymentTransaction record.
        transaction, created = PaymentTransaction.objects.update_or_create(
            transaction_id=transaction_id,
            defaults={
                "status": status_str.lower(),
                "gateway_response": gateway_response,
                "amount": amount,
                "credits": credits_to_add
            }
        )

        # On successful payment, update the user's available credits.
        if status_str.lower() == "success":
            profile = user_email.userprofile
            profile.available_credits += credits_to_add
            profile.save()
        
        serializer = PaymentTransactionSerializer(transaction)
        return Response(serializer.data, status=status.HTTP_200_OK)
        

# def intuit_auth(request):
#     """Index page: Initiate OAuth flow or show connection status."""
#     print("Step 'index': Loading index page.")
#     token = get_quickbooks_token_obj() # Try to load the token object from the database

#     # Check if a valid token object exists and has an access token and realm ID
#     is_connected = token is not None and token.access_token is not None and token.realm_id is not None

#     if is_connected:
#         # Construct the HTML string for the connected state
#         html_content = f"""
#         <!DOCTYPE html>
#         <html>
#         <head>
#             <title>Intuit Integration App</title>
#             <style>
#                 body {{ font-family: sans-serif; margin: 20px; }}
#                 code {{ background-color: #f0f0f0; padding: 2px 5px; border-radius: 4px; }}
#                 a {{ color: #006491; text-decoration: none; }}
#                 a:hover {{ text-decoration: underline; }}
#             </style>
#         </head>
#         <body>
#             <h1>Intuit Integration App</h1>
#             <p>QuickBooks Connected (Realm ID: {token.realm_id})</p>
#             <p>Access Token Expires: {token.expires_at.strftime('%Y-%m-%d %H:%M:%S %Z') if token.expires_at else 'N/A'}</p>
#             <p>Tokens are stored persistently in your database models.</p>
#             <p><a href="{reverse('payments:list_transactions_api')}">View Processed Transactions (API Endpoint)</a></p>
#             <p><a href="{reverse('payments:intuit_auth')}">Reconnect to QuickBooks (if needed)</a></p>
#             <p><a href="{reverse('payments:clear_data')}" onclick="return confirm('Are you sure you want to clear all QuickBooks related data and disconnect?');">Clear All QuickBooks Data & Disconnect</a></p>
#             <p>Configure your webhook in Intuit Developer Portal pointing to <code>{request.build_absolute_uri(reverse('payments:webhook_payment'))}</code></p>
#         </body>
#         </html>
#         """
#     else:
#         auth_url = reverse('payments:intuit_auth')
#         redirect_uri_setting = settings.INTUIT_REDIRECT_URI

#         html_content = f"""
#         <!DOCTYPE html>
#         <html>
#         <head>
#             <title>Intuit Integration App</title>
#             <style>
#                 body {{ font-family: sans-serif; margin: 20px; }}
#                 code {{ background-color: #f0f0f0; padding: 2px 5px; border-radius: 4px; }}
#                 a {{ color: #006491; text-decoration: none; }}
#                 a:hover {{ text-decoration: underline; }}
#             </style>
#         </head>
#         <body>
#             <h1>Intuit Integration App</h1>
#             <p>QuickBooks not connected.</p>
#             <p>Click below to connect your QuickBooks Online account. This is required for the app to fetch payment details from your account after receiving webhooks.</p>
#             <p>Make sure your <code>settings.py</code> and the Redirect URI (<code>{redirect_uri_setting}</code>) in the Intuit Developer Portal are configured correctly.</p>
#             <p><a href="{auth_url}">Connect to QuickBooks</a></p>
#         </body>
#         </html>
#         """

#     # Return the HTML content as an HttpResponse
#     return HttpResponse(html_content)

# @require_http_methods(["GET"]) # Only allow GET requests
# def intuit_auth(request):
#     """Initiates the OAuth 2.0 authorization flow by redirecting to Intuit."""
#     print("Step 'intuit_auth': Initiating OAuth flow.")

#     auth_client = get_auth_client()

#     scopes = [Scopes.PAYMENT, Scopes.ACCOUNTING]

#     state = os.urandom(16).hex()  # Generate a random state parameter for CSRF protection
#     request.session['oauth_state'] = state
#     print(f"Step 'intuit_auth' stored in session.")

#     # Get the authorization URL from Intuit
#     auth_url = auth_client.get_authorization_url(scopes=scopes, state_token=state)
#     print(f"Step 'intuit_auth': Generated Authorization URL: {auth_url}")
#     print(f"Step 'intuit_auth': Redirecting user to Intuit for authorization...")

#     # Redirect the user's browser to the Intuit authorization server
#     return redirect(auth_url)

# @require_http_methods(["GET"])
# def intuit_oauth_callback(request):
#     """Handles the OAuth 2.0 calback from Intuit."""
#     print("Step 'oauth_callback': Handling OAuth callback.")

#     auth_code = request.GET.get("code")
#     realm_id = request.GET.get("realmId")
#     state = request.GET.get("state")
#     error = request.GET.get("error")
#     error_description = request.GET.get("error_description")

#     print(f"Step 'oauth_callback': Received auth_code: {auth_code}, realm_id: {realm_id}, state: {state}")

#     stored_state = request.session.get('oauth_state')
#     print(f"Step 'oauth_callback': Stored state from session: {stored_state}")
#     if not stored_state or state != stored_state:
#         print("Step 'oauth_callback': State mismatch. Possible CSRF attack.")
#         return HttpResponse("State mismatch. Possible CSRF attack.")

#     if error:
#         print(f"Step 'oauth_callback': Error: {error}, Description: {error_description}")
#         return HttpResponse(f"Error: {error}, Description: {error_description}")
         
#     if not auth_code or not realm_id:
#         print("Step 'oauth_callback': Missing auth_code or realm_id.")
#         return HttpResponse("Missing auth_code or realm_id.")
    
#     auth_client = get_auth_client()

#     print("Step 'oauth_callback': Exchanging auth code for tokens.")
#     try:
#         auth_client.get_bearer_token(auth_code, realm_id=realm_id)
#         print("Step 'oauth_callback': Tokens exchanged successfully.")
#         saved_token = save_quickbooks_token_obj(
#             auth_client.access_token,
#             auth_client.refresh_token,
#             auth_client.expires_at,
#             auth_client.realm_id,
#         )

#         if saved_token:
#             print("Step 'oauth_callback': Tokens saved successfully.")
#             return HttpResponse("QuickBooks connected successfully!")
#         else:
#             print("Step 'oauth_callback': Failed to save tokens.")
#             return HttpResponse("Failed to save tokens.")
        
#     except AuthClientError as e:
#         print(f"Step 'oauth_callback': Error exchanging auth code: {e}")
#         return HttpResponse(f"Error exchanging auth code: {e}")
#     except Exception as e:
#         print(f"Step 'oauth_callback': Unexpected error: {e}")
#         return HttpResponse(f"Unexpected error: {e}")
    

# @require_http_methods(["POST"])
# def clear_data_route(request):
#     """Clears all QuickBooks related data from the database."""
#     print("Step 'clear_data_route': Clearing all QuickBooks related data.")
#     success = clear_db_data() # Call the utility function

#     if success:
#         print("Step 'clear_data_route': Data cleared successfully.")
#         # Return a success response (e.g., JSON) for an API endpoint
#         return JsonResponse({'status': 'success', 'message': 'QuickBooks data cleared.'}, status=status.HTTP_200_OK)
#     else:
#         print("Step 'clear_data_route': Failed to clear data.")
#         # Return an error response (e.g., JSON)
#         return JsonResponse({'status': 'error', 'message': 'Failed to clear QuickBooks data.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


def intuit_index(request):
    """Basic index view to show connection status and link to auth."""
    print("Step 'index': Loading index page.")
    token = get_quickbooks_token_obj() # Try to load the token

    is_connected = token is not None and token.access_token is not None and token.realm_id is not None
    expires_at = token.expires_at if token else None

    # Get the webhook URL to display in the template
    # Assuming your webhook URL name is 'payments:payment-callback' as per your urls.py
    webhook_url_example = request.build_absolute_uri(reverse('payments:payment-callback'))


    context = {
        'is_connected': is_connected,
        'realm_id': token.realm_id if token else None,
        'expires_at': expires_at, # Pass the datetime object
        'webhook_url_example': webhook_url_example,
        # The template uses {% url 'payments:...' %} directly for other links
    }
    # Render the index.html template, passing the context
    return render(request, 'payments/index.html', context)


@require_http_methods(["GET"]) # This view should only respond to GET requests
def intuit_auth(request):
    """Initiates the OAuth 2.0 authorization flow by redirecting to Intuit."""
    print("Step 'intuit_auth': Initiating OAuth flow.")

    # Get the AuthClient instance using settings defined in settings.py
    auth_client = get_auth_client()

    # Define the scopes your application needs to request from Intuit.
    # Scopes.PAYMENT is required for receiving payment webhooks and using payment-related APIs.
    # Scopes.ACCOUNTING is often needed to fetch related data like Customers, Invoices, etc.
    # Add other scopes here if your application needs them (e.g., Scopes.OPENID, Scopes.PROFILE)
    scopes = [Scopes.PAYMENT, Scopes.ACCOUNTING]

    # Generate a unique state parameter for CSRF protection.
    # This state should be stored in the user's session and verified upon callback.
    # intuitlib provides a helper method to generate a secure state token.
    state = os.urandom(16).hex()  # Generate a random state parameter
    request.session['oauth_state'] = state # Store the state in the Django session
    print(f"Step 'intuit_auth': Generated state: {state}, stored in session.")

    # Get the authorization URL from Intuit's servers.
    # The user's browser will be redirected to this URL.
    # The redirect_uri parameter included in this URL is pulled from settings.INTUIT_REDIRECT_URI.
    auth_url = auth_client.get_authorization_url(scopes=scopes, state_token=state)
    print(f"Step 'intuit_auth': Generated Authorization URL: {auth_url}")
    print(f"Step 'intuit_auth': Redirecting user to Intuit for authorization...")

    # Redirect the user's browser to the generated authorization URL.
    return redirect(auth_url)


# This view handles the redirect from Intuit after the user has granted or denied authorization.
# The URL path for this view MUST exactly match the Redirect URI configured in your Intuit Developer Portal.
@require_http_methods(["GET"]) # This view should only respond to GET requests
def intuit_oauth_callback(request):
    """Handles the redirect from Intuit after user authorization."""
    print("Step 'oauth_callback': Received redirect from Intuit.")

    # Get the parameters from the URL query string provided by Intuit.
    auth_code = request.GET.get('code') # The authorization code needed to exchange for tokens
    realm_id = request.GET.get('realmId') # The QuickBooks company ID (Data Services realm ID)
    state = request.GET.get('state') # The state parameter returned by Intuit
    error = request.GET.get('error') # Check if Intuit returned an error instead of a code
    error_description = request.GET.get('error_description') # Description of the error

    print(f"Step 'oauth_callback': Received parameters: code={auth_code}, realmId={realm_id}, state={state}, error={error}, error_description={error_description}")

    # --- CSRF Protection Check ---
    # Retrieve the stored state from the session and compare it with the received state.
    # This prevents Cross-Site Request Forgery attacks.
    # Use .pop() to retrieve and remove the state from the session in one step.
    stored_state = request.session.pop('oauth_state', None)

    if not stored_state or state != stored_state:
        print("Step 'oauth_callback': CSRF state mismatch or missing. Potential attack.")
        # If the state doesn't match or is missing, it's a potential security issue.
        # Return a Forbidden response or redirect to a specific error page.
        # Returning HttpResponseForbidden is a good practice for security.
        return HttpResponseForbidden("Invalid state parameter or state missing from session.")

    # --- Handle Errors Returned by Intuit ---
    # If the 'error' parameter is present, the user denied authorization or an error occurred on Intuit's side.
    if error:
        print(f"Step 'oauth_callback': Intuit returned an error in the redirect: {error} - {error_description}")
        # Render an error template to inform the user about the connection failure.
        # Assuming you have an 'oauth_error.html' template in payments/templates/payments/.
        # Pass the error details and a URL name to link back to a relevant page (e.g., your status page).
        return render(request, 'payments/oauth_error.html', {
            'error': error,
            'error_description': error_description,
            # Use the URL name for your QuickBooks status API endpoint or a frontend route
            'index_url_name': 'payments:quickbooks-status'
        }, status=400) # Use a 400 status code for client errors

    # --- Process Successful Callback ---
    # If no error occurred, check if the required parameters (auth_code and realm_id) are present.
    # These should be provided by Intuit on a successful authorization.
    if not auth_code or not realm_id:
        print("Step 'oauth_callback': Missing authorization code or realm ID in callback parameters.")
        # This usually indicates a misconfiguration in the Intuit Developer Portal Redirect URI
        # or an unexpected response from Intuit.
        # Render an error template with details.
        return render(request, 'payments/oauth_error.html', {
            'error': 'Missing parameters',
            'error_description': 'Did not receive auth code or realmId from Intuit. Check Redirect URI configuration in Intuit Developer Portal.',
            'received_params': request.GET, # Include received GET params for debugging
            'index_url_name': 'payments:quickbooks-status'
        }, status=400)


    # Get the AuthClient instance again to perform the token exchange.
    auth_client = get_auth_client()

    print(f"Step 'oauth_callback': Attempting to exchange authorization code for tokens...")
    try:
        # Exchange the authorization code for access_token and refresh_token.
        # The get_bearer_token method makes a POST request to Intuit's token endpoint.
        # It updates the auth_client instance with the received tokens and expiry.
        auth_client.get_bearer_token(auth_code, realm_id=realm_id)
        print("Step 'oauth_callback': Token exchange successful.")
        print(f"Step 'oauth_callback': Received Access Token (first 10 chars): {auth_client.access_token[:10]}...")
        print(f"Step 'oauth_callback': Received Refresh Token (first 10 chars): {auth_client.refresh_token[:10]}...")
        print(f"Step 'oauth_callback': Received Expires In (seconds): {auth_client.expires_in}")
        print(f"Step 'oauth_callback': Received Realm ID: {auth_client.realm_id}")


        # --- Save Tokens to Database ---
        # Use your utility function to save or update the received tokens in your database model (QuickBooksToken).
        # This function should handle the logic of finding the single token row (or creating it)
        # and updating its fields.
        saved_token = save_quickbooks_token_obj(
            auth_client.access_token,
            auth_client.refresh_token,
            auth_client.expires_in, # Use expires_in (seconds) from the client
            auth_client.realm_id # Use realm_id from the client
        )

        if saved_token:
            print(f"Step 'oauth_callback': Tokens successfully saved to database for Realm ID: {saved_token.realm_id}")
            # Redirect the user back to your application's frontend or a status page after successful connection.
            # Redirecting to a DRF status API endpoint might not be the final user experience,
            # but it's useful for confirming the connection status.
            return redirect(reverse('payments:quickbooks-status')) # Redirect to your status API endpoint

        else:
            print("Step 'oauth_callback': Failed to save tokens to database.")
            # Handle token save failure - render an error page
            return render(request, 'payments/oauth_error.html', {
                'error': 'Token Save Failed',
                'error_description': 'Successfully received tokens from Intuit, but failed to save them to the database.',
                'index_url_name': 'payments:quickbooks-status'
            }, status=500)


    except AuthClientError as e:
        print(f"Step 'oauth_callback': Intuit AuthClient Error during token exchange: {e}")
        # Handle specific errors from the intuitlib client during the token exchange process.
        return render(request, 'payments/oauth_error.html', {
            'error': 'Intuit Token Exchange Error',
            'error_description': str(e), # Convert exception to string for display
            'index_url_name': 'payments:quickbooks-status'
        }, status=500) # Use a 500 status code for server-side errors like failed exchange
    except Exception as e:
        print(f"Step 'oauth_callback': General Error during token exchange: {e}")
        # Catch any other unexpected exceptions during the process.
        return render(request, 'payments/oauth_error.html', {
            'error': 'Token Exchange Failed',
            'error_description': str(e), # Convert exception to string for display
             'index_url_name': 'payments:quickbooks-status'
        }, status=500)
    

# import hmac
# import hashlib
# import base64
# import requests
# from django.shortcuts import get_object_or_404
# from django.contrib.auth import get_user_model
# from django.conf import settings
# from django.utils import timezone
# from datetime import timedelta
# from rest_framework.views import APIView
# from rest_framework.response import Response
# from rest_framework import status, viewsets
# from rest_framework.permissions import IsAuthenticated, IsAdminUser
# from .models import PaymentTransaction, QuickbooksToken
from .serializers import PaymentTransactionSerializer
# from intuitlib.client import AuthClient
# from intuitlib.enums import Scopes


# AMOUNT_TO_TIER = {
#     5.0: "tester",
#     10.0: "starter",
#     15.0: "growth",
#     20.0: "pro",
#     25.0: "ultimate",
# }

# TIER_CREDITS = {
#     "tester": 5,
#     "starter": 4,
#     "growth": 6,
#     "pro": 8,
#     "ultimate": 12,
# }

# class PaymentCallbackView(APIView):
#     permission_classes = [IsAuthenticated]

#     def verify_signature(self, request):
#         """Verify Intuit's webhook signature"""
#         verifier_token = settings.INTUIT_WEBHOOK_VERIFIER_TOKEN
#         signature_header = request.headers.get("intuit-signature")
#         raw_body = request.body
        
#         computed_hash = hmac.new(
#             key=verifier_token.encode(),
#             msg=raw_body,
#             digestmod=hashlib.sha256
#         ).digest()
        
#         return hmac.compare_digest(
#             base64.b64encode(computed_hash).decode(),
#             signature_header
#         )
    
#     def refresh_tokens(self):
#         token = QuickbooksToken.get_singleton()
#         auth_client = AuthClient(
#             client_id=settings.CLIENT_ID,
#             client_secret=settings.CLIENT_SECRET,
#             redirect_uri=settings.REDIRECT_URI,
#             environment="sandbox"
#         )
#         auth_client.refresh()
#         token.access_token = auth_client.access_token
#         token.refresh_token = auth_client.refresh_token
#         token.expires_at = timezone.now() + timedelta(seconds=auth_client.expires_in)
#         token.save()
#         return token
    
#     def get_payment_details(self, payment_id):
#         token = QuickbooksToken.get_singleton()
#         if token.is_expired():
#             self.refresh_tokens()
#             token = QuickbooksToken.get_singleton()

#         response = requests.get(
#             url=f"https://sandbox.api.intuit.com/quickbooks/v4/payments/{payment_id}/Payment",
#             headers={"Authorization": f"Bearer {token.access_token}"}
#         )

#         if response.status_code == 401:
#             return self.get_payment_details(payment_id)  # Retry with new token
        
#         return response.json()

#     def post(self, request):
#         if not self.verify_signature(request):
#             return Response({"error": "Invalid signature"}, status=status.HTTP_401_UNAUTHORIZED)

#         data = request.data
#         processed_transactions = []

#         for notification in data.get("eventNotifications", []):
#             realm_id = notification.get("realmId")
            
#             for entity in notification.get("dataChangeEvent", {}).get("entities", []):
#                 if entity.get("name") == "Payment" and entity.get("operation") == "Create":
#                     payment_id = entity.get("id")
#                     transaction_date = entity.get("lastUpdated")
                    
#                     payment_payload = self.get_payment_details(payment_id)
                    
#                     amount = float(payment_payload.get("amount", 0))
#                     status_str = payment_payload.get("status", "").lower()

#                     tier = AMOUNT_TO_TIER.get(amount)
#                     if not tier:
#                         continue  # Skip unrecognized amounts

#                     credits = TIER_CREDITS.get(tier, 0)

#                     transaction = PaymentTransaction.objects.update_or_create(
#                         user=get_object_or_404(get_user_model())
#                         transaction_id=payment_id,
#                         transaction_date=transaction_date,
#                         status=status_str,
#                         tier=tier,
#                         amount=amount,
#                         # customer_email=customer_email,
#                         credits=credits,
#                         gateway_response=payment_payload
#                     )

#                     # Update user credits if payment succeeded
#                     if status_str == "captured":
#                         try:
#                             user = User.objects.get(email=customer_email)
#                             user.profile.available_credits += credits
#                             user.profile.save()
#                         except User.DoesNotExist:
#                             pass

#                     processed_transactions.append(PaymentTransactionSerializer(transaction).data)

#         return Response({"processed": processed_transactions}, status=status.HTTP_200_OK)
    

            



# import hmac
# import hashlib
# import base64
# import requests
# import json
# import os
# from datetime import datetime, timedelta, timezone

# # Django imports
# from django.shortcuts import render, redirect
# from django.urls import reverse
# from django.http import HttpResponse, JsonResponse, HttpResponseRedirect
# from django.views.decorators.csrf import csrf_exempt # Still needed for webhook POST
# from django.views import View # Can still use for non-API HTML views if preferred
# from django.conf import settings
# from django.contrib.auth import get_user_model # Needed for linking transactions to users
from django.utils.decorators import method_decorator # For applying decorators to class-based views
# from django.contrib.auth.decorators import user_passes_test # For restricting views to staff/admins

# # DRF imports
# from rest_framework.views import APIView
# from rest_framework.response import Response
# from rest_framework import status # Use DRF status codes
# from rest_framework.permissions import IsAuthenticated, IsAdminUser, AllowAny # DRF permission classes
from rest_framework.request import Request # Import DRF Request object
# from rest_framework import viewsets # If using ViewSets elsewhere

# # Import Intuit libraries
# from intuitlib.client import AuthClient
# from intuitlib.enums import Scopes
# from intuitlib.exceptions import AuthClientError

# # Import your models (assuming they are in payments/models.py)
# from .models import QuickbooksToken, PaymentTransaction, CustomerCredits

# # Import your utility functions (assuming they are in payments/utils.py)
# from .utils import (
#     get_auth_client, get_quickbooks_token_obj, save_quickbooks_token_obj,
#     verify_signature, get_payment_details_from_intuit,
#     get_customer_details_from_intuit,
#     process_payment_create, process_payment_update, process_payment_delete,
#     get_transactions_from_db, get_credits_from_db, clear_db_data
# )


# class IntuitStatusView(APIView):
#     # Restrict to authenticated staff users
#     permission_classes = [IsAuthenticated, IsAdminUser]

#     def get(self, request: Request, *args, **kwargs):
#         """Shows the QuickBooks connection status and relevant links."""
#         print("Django DRF View (Status): Accessing QuickBooks connection status.")
#         token = get_quickbooks_token_obj() # Load token from DB using utility
#         # Return JSON response with status and links
#         if token and token.access_token and token.realm_id:
#              return Response({
#                  'status': 'QuickBooks Connected',
#                  'realm_id': token.realm_id,
#                  'expires_at': token.expires_at.isoformat() if token.expires_at else None, # Serialize datetime
#                  'webhook_url': request.build_absolute_uri(reverse('payments:webhook_payment')), # Build absolute URL
#                  'connect_url': request.build_absolute_uri(reverse('payments:intuit_auth')), # Link to re-auth
#                  'list_transactions_url': request.build_absolute_uri(reverse('payments:list_intuit_transactions')), # Link to list view
#                  'clear_data_url': request.build_absolute_uri(reverse('payments:clear_intuit_data')), # Link to clear data
#              }, status=status.HTTP_200_OK)
#         else:
#              return Response({
#                  'status': 'QuickBooks Not Connected',
#                  'message': 'Click connect to link your QuickBooks Online account.',
#                  'connect_url': request.build_absolute_uri(reverse('payments:intuit_auth')), # Link to connect
#                  'redirect_uri_setting': settings.INTUIT_REDIRECT_URI # Show setting value for debugging
#              }, status=status.HTTP_200_OK)


# # Admin-facing view to initiate the OAuth flow
# class IntuitAuthView(APIView):
#     """Initiates the OAuth 2.0 authorization flow (for the app owner's account)."""
#     # Restrict to authenticated staff users
#     permission_classes = [IsAuthenticated, IsAdminUser]

#     def get(self, request: Request, *args, **kwargs):
#         print("Django DRF View (Auth): Initiating Intuit OAuth flow.")
#         auth_client = get_auth_client() # Get AuthClient using settings

#         # Request scopes needed for your application
#         scopes = [Scopes.PAYMENT, Scopes.ACCOUNTING]

#         # Generate and store a state parameter for CSRF protection in session
#         state = os.urandom(16).hex()
#         request.session['oauth_state'] = state # Store state in Django session

#         try:
#             # Get the authorization URL from intuitlib
#             auth_url = auth_client.get_authorization_url(scopes=scopes, state_token=state)
#             print(f"Django DRF View (Auth): Generated Authorization URL: {auth_url}")
#             print("Django DRF View (Auth): Redirecting user to Intuit for authorization...")
#             # Redirect the user's browser to the Intuit authorization URL
#             # Return HttpResponseRedirect for browser redirects
#             return HttpResponseRedirect(auth_url)

#         except Exception as e:
#             print(f"Django DRF View (Auth): Error generating authorization URL: {e}")
#             # Return a DRF Response with an error status
#             return Response({
#                 'error_message': 'Could not generate authorization URL',
#                 'details': str(e)
#             }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# This standard Django view handles clearing all QuickBooks related data.
# It's typically accessed via a POST request from an admin interface or a dedicated button.
@require_http_methods(["POST"]) # Only allow POST requests for clearing data
# @login_required # Optional: Restrict who can clear data
# @permission_classes([IsAdminUser]) # Optional: Restrict to admin users (if using DRF permissions)
def clear_data_route(request):
    """Clears all QuickBooks related data from the database."""
    print("Step 'clear_data_route': Clearing all QuickBooks related data.")
    success = clear_db_data() # Call the utility function to delete data

    if success:
        print("Step 'clear_data_route': Data cleared successfully.")
        # Return a success response (e.g., JSON) for an API endpoint
        # Use DRF status codes for clarity, even in a standard view returning JsonResponse
        return JsonResponse({'status': 'success', 'message': 'QuickBooks data cleared.'}, status=status.HTTP_200_OK)
    else:
        print("Step 'clear_data_route': Failed to clear data.")
        # Return an error response (e.g., JSON)
        return JsonResponse({'status': 'error', 'message': 'Failed to clear QuickBooks data.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# This standard Django view handles incoming QuickBooks Payment webhooks.
# It needs to be exempted from CSRF protection as webhooks don't send CSRF tokens.
# Intuit sends webhooks via POST requests.
@csrf_exempt
@require_http_methods(["POST"]) # Intuit sends webhooks via POST
def handle_payment_webhook(request):
    """Handles incoming QuickBooks Payment webhooks."""
    print("Step 'handle_payment_webhook': Received webhook.")

    # 1. Verify Signature
    # Get the signature header from the request
    intuit_signature = request.headers.get('Intuit-Signature')
    # Get the raw request body as bytes
    request_body = request.body

    # Use the utility function to verify the signature
    # Uses: verify_signature
    if not verify_signature(request_body, intuit_signature):
        print("Step 'handle_payment_webhook': Signature verification failed.")
        # Return 400 Bad Request for failed signature verification (as per Intuit docs)
        return HttpResponseBadRequest("Invalid signature.")

    print("Step 'handle_payment_webhook': Signature verified successfully.")

    # 2. Parse the Webhook Payload
    try:
        # The request body is bytes, decode it to a string and then parse the JSON
        webhook_payload = json.loads(request_body.decode('utf-8'))
        print(f"Step 'handle_payment_webhook': Parsed webhook payload: {webhook_payload}")
    except json.JSONDecodeError as e:
        print(f"Step 'handle_payment_webhook': Failed to parse JSON payload: {e}")
        # Return 400 Bad Request for invalid JSON payload
        return HttpResponseBadRequest("Invalid JSON payload.")

    # Intuit webhook payload structure: { "eventNotifications": [ { ...event... } ] }
    event_notifications = webhook_payload.get("eventNotifications", [])
    if not event_notifications:
        print("Step 'handle_payment_webhook': No eventNotifications found in payload.")
        # Return 200 OK even if no events, as it's a valid payload structure
        return HttpResponse("No event notifications received.", status=status.HTTP_200_OK)

    # Process each event notification included in the payload
    for notification in event_notifications:
        realm_id = notification.get("realmId") # The QuickBooks company ID for this event
        data_event = notification.get("dataChangeEvent") # The data change event details

        if not realm_id or not data_event:
            print("Step 'handle_payment_webhook': Skipping notification due to missing realmId or dataChangeEvent.")
            continue # Skip to the next notification if essential data is missing

        entities = data_event.get("entities", []) # List of entities that changed (e.g., Payments)
        if not entities:
            print(f"Step 'handle_payment_webhook': No entities found in dataChangeEvent for realm {realm_id}. Skipping.")
            continue # Skip if no entities in the data change event

        # Process each entity within the data change event
        for entity in entities:
            entity_type = entity.get("name") # e.g., "Payment", "Invoice", "Customer"
            entity_id = entity.get("id") # The ID of the entity in QuickBooks
            operation_type = entity.get("operation") # e.g., "Create", "Update", "Delete"

            if not entity_type or not entity_id or not operation_type:
                print(f"Step 'handle_payment_webhook': Skipping entity due to missing name, id, or operation: {entity}. Realm: {realm_id}.")
                continue # Skip incomplete entity data

            print(f"Step 'handle_payment_webhook': Processing entity: Type={entity_type}, ID={entity_id}, Operation={operation_type}, Realm={realm_id}")

            # We are specifically interested in 'Payment' entities for this webhook handler
            if entity_type == "Payment":
                # Need to fetch the full payment and potentially customer details from QuickBooks API
                # using the provided realmId and entityId (payment_id)
                try:
                    # Get the QuickBooks token for this realm from your database
                    # Uses: get_quickbooks_token_obj
                    token_obj = get_quickbooks_token_obj(realm_id)
                    if not token_obj:
                        print(f"Step 'handle_payment_webhook': No QuickBooks token found for realm {realm_id}. Cannot fetch payment details.")
                        # Log this error, but don't necessarily return an error response to Intuit yet.
                        # Handle this failure asynchronously if needed (e.g., add to retry queue).
                        continue # Skip processing this payment event

                    # Fetch the full Payment details from the Intuit API using the utility function
                    # Uses: get_payment_details_from_intuit
                    payment_data = get_payment_details_from_intuit(token_obj, realm_id, entity_id)
                    if not payment_data:
                        print(f"Step 'handle_payment_webhook': Failed to fetch payment details for ID {entity_id} from Intuit API.")
                        # Log error, handle failure asynchronously, skip processing this payment event
                        continue

                    # Fetch Customer details from Intuit API (if CustomerRef exists in payment data)
                    customer_data = None
                    customer_ref = payment_data.get("CustomerRef")
                    customer_id_qbo = customer_ref.get("value") if customer_ref else None

                    if customer_id_qbo:
                        # Fetch the full Customer details from the Intuit API using the utility function
                        # Uses: get_customer_details_from_intuit
                        customer_data = get_customer_details_from_intuit(token_obj, realm_id, customer_id_qbo)
                        if not customer_data:
                             print(f"Step 'handle_payment_webhook': Failed to fetch customer details for ID {customer_id_qbo} from Intuit API.")
                             # Customer data might not be critical for all processing, decide how to handle failure.
                             # For now, just log and continue without customer_data.


                    # --- Call the appropriate processing function based on operation type ---
                    # These processing functions are responsible for saving/updating PaymentTransaction
                    # and updating UserProfile credits.
                    success = False
                    message = "Unknown operation type."

                    if operation_type == "Create":
                        print(f"Step 'handle_payment_webhook': Calling process_payment_create for ID {entity_id}.")
                        # Pass the fetched data to the processing function
                        # Uses: process_payment_create
                        success, message = process_payment_create(realm_id, entity_id, payment_data, customer_data)
                    elif operation_type == "Update":
                         print(f"Step 'handle_payment_webhook': Calling process_payment_update for ID {entity_id}.")
                         # Pass the fetched data to the processing function
                         # Uses: process_payment_update
                         success, message = process_payment_update(realm_id, entity_id, payment_data, customer_data)
                    # elif operation_type == "Delete":
                    #      print(f"Step 'handle_payment_webhook': Calling process_payment_delete for ID {entity_id}.")
                    #      # For delete, you might only need realm_id and entity_id, but passing fetched data is safer
                    #      # Uses: process_payment_delete
                    #      success, message = process_payment_delete(realm_id, entity_id) # process_payment_delete needs to handle reversal using stored transaction data
                    else:
                         print(f"Step 'handle_payment_webhook': Unhandled operation type: {operation_type} for Payment ID {entity_id}.")
                         # Log unhandled operation type

                    if not success:
                         print(f"Step 'handle_payment_webhook': Processing failed for Payment ID {entity_id}, Operation {operation_type}: {message}")
                         # Decide how to handle processing failures (e.g., log, retry queue, send alert).
                         # For webhooks, it's often best to return 200 OK quickly and handle failures asynchronously.

                except Exception as e:
                    print(f"Step 'handle_payment_webhook': Unexpected error processing Payment webhook for ID {entity_id}: {e}")
                    # Catch any unexpected errors during processing of a single entity
                    # Again, return 200 OK to Intuit and handle failure asynchronously
                    pass # Continue processing other events if any

    # Intuit expects a 200 OK response quickly to acknowledge receipt of the webhook payload.
    # Any processing failures within the loop should be handled asynchronously (e.g., logging, retries).
    print("Step 'handle_payment_webhook': Webhook processing finished (returning 200 OK).")
    return HttpResponse("Webhook received and processed.", status=status.HTTP_200_OK)

class QuickbooksStatusAPIView(APIView):
    """
    API view to get the current QuickBooks connection status.
    """
    def get(self, request, *args, **kwargs):
        print("Step 'QuickbooksStatusAPIView': Getting QuickBooks connection status.")
        # Retrieve the QuickBooks token object from the database
        # Uses: get_quickbooks_token_obj
        token = get_quickbooks_token_obj()

        # Determine connection status
        is_connected = token is not None and token.access_token is not None and token.realm_id is not None
        expires_at = token.expires_at if token else None

        # Prepare status data as a dictionary
        status_data = {
            'is_connected': is_connected,
            'realm_id': token.realm_id if token else None,
            # Format datetime for JSON using isoformat()
            'expires_at': expires_at.isoformat() if expires_at else None,
            # Add URLs for frontend consumption using reverse() and build_absolute_uri()
            'auth_initiate_url': request.build_absolute_uri(reverse('payments:intuit_auth')),
            'clear_data_url': request.build_absolute_uri(reverse('payments:clear_data')),
            # Use the correct URL name for the webhook endpoint
            'webhook_url_example': request.build_absolute_uri(reverse('payments:payment-callback')),
            'token_storage_info': 'database models', # Indicate where tokens are stored
        }

        # Return the status data as a DRF Response
        return Response(status_data)


# Admin-facing view to list processed transactions and credits
class TransactionListView(APIView):
    # Restrict to authenticated staff users
    permission_classes = [IsAuthenticated, IsAdminUser]

    def get(self, request: Request, *args, **kwargs):
        print("Django DRF View (List): Accessing processed transactions from DB.")
        # Load data from the database using utility functions
        transactions = get_transactions_from_db() # Gets list of dictionaries
        credits_data = get_credits_from_db() # Gets dictionary of credits

        # Format credits for display - maybe list them? Or just show the total
        # credits_display is already the dictionary {customer_id: total_credits}

        # Load token data from the database for current status display
        token = get_quickbooks_token_obj()

        print("Django DRF View (List): Loaded data from DB.")

        # Return a DRF Response
        return Response({
            "processed_transactions": transactions,
            "customer_credits": credits_data, # Use credits_data directly
            "token_status": "Loaded from DB" if token else "Not Loaded (No token in DB)",
            "connected_realm": token.realm_id if token else "None"
        }, status=status.HTTP_200_OK)


# Admin-facing view to clear all data (for testing)
class ClearDataView(APIView):
    # Restrict to authenticated staff users
    permission_classes = [IsAuthenticated, IsAdminUser]

    def post(self, request: Request, *args, **kwargs): # Use POST for destructive action
        print("Django DRF View (Clear): Clearing all stored data from the database.")
        success = clear_db_data() # Use utility function

        if success:
            print("Django DRF View (Clear): All QuickBooks related data cleared from database.")
            message = "All QuickBooks related data cleared."
            # Redirect back to status page after clearing
            # Use HttpResponseRedirect for browser redirect
            return HttpResponseRedirect(reverse('payments:intuit_status'))
        else:
            print("Django DRF View (Clear): Failed to clear data from database.")
            message = f"Failed to clear data from database."
            # Return a DRF Response with an error status
            return Response({'error_message': message}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)



class PaymentTransactionViewSet(viewsets.ModelViewSet):
    """
    Admin viewset to monitor payment transactions.
    """
    queryset = PaymentTransaction.objects.all()
    serializer_class = PaymentTransactionSerializer
    permission_classes = [IsAuthenticated, IsAdminUser]
