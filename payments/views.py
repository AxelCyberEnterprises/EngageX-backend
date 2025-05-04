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
from .utils import is_token_expired

from django.conf import settings
from intuitlib.client import AuthClient
from intuitlib.enums import Scopes
from intuitlib.exceptions import AuthClientError

from .serializers import PaymentTransactionSerializer
from django.utils.decorators import method_decorator
from rest_framework.permissions import IsAuthenticated, IsAdminUser, AllowAny
from rest_framework.request import Request

# Fixed credits for each payment tier
TIER_CREDITS = {
    "tester": 1,
    "starter": 4,
    "growth": 6,
    "pro": 8,
    "ultimate": 12,
}


def intuit_index(request):
    """Basic index view to show connection status and link to auth."""
    print("Step 'index': Loading index page.")
    token = get_quickbooks_token_obj()

    is_connected = token is not None and token.access_token is not None and token.realm_id is not None
    expires_at = token.expires_at if token else None

    # Get the webhook URL to display in the template
    webhook_url_example = request.build_absolute_uri(reverse('payments:payment_callback'))

    context = {
        'is_connected': is_connected,
        'realm_id': token.realm_id if token else None,
        'expires_at': expires_at,  # Pass the datetime object
        'webhook_url_example': webhook_url_example,
    }
    return render(request, 'payments/index.html', context)


@require_http_methods(["GET"])
def intuit_auth(request):
    """Initiates the OAuth 2.0 authorization flow by redirecting to Intuit."""
    print("Step 'intuit_auth': Initiating OAuth flow.")

    # Get the AuthClient instance using settings defined in settings.py
    auth_client = get_auth_client()

    # Define the scopes your application needs to request from Intuit.
    # Scopes.PAYMENT is required for receiving payment webhooks and using payment-related APIs.
    # Scopes.ACCOUNTING is often needed to fetch related data like Customers, Invoices, etc.
    scopes = [Scopes.PAYMENT, Scopes.ACCOUNTING]

    # Generate a unique state parameter for CSRF protection.
    # This state should be stored in the user's session and verified upon callback.
    # intuitlib provides a helper method to generate a secure state token.
    state = os.urandom(16).hex()  # Generate a random state parameter
    request.session['oauth_state'] = state  # Store the state in the Django session
    print(f"Step 'intuit_auth': Generated state: {state}, stored in session.")

    # Get the authorization URL from Intuit's servers.
    # The user's browser will be redirected to this URL.
    # The redirect_uri parameter included in this URL is pulled from settings.INTUIT_REDIRECT_URI.
    auth_url = auth_client.get_authorization_url(scopes=scopes, state_token=state)
    print(f"Step 'intuit_auth': Generated Authorization URL: {auth_url}")
    print(f"Step 'intuit_auth': Redirecting user to Intuit for authorization...")

    return redirect(auth_url)


# This view handles the redirect from Intuit after the user has granted or denied authorization.
# The URL path for this view MUST exactly match the Redirect URI configured in your Intuit Developer Portal.
@require_http_methods(["GET"])  # This view should only respond to GET requests
def oauth_callback(request):
    """Handles the redirect from Intuit after user authorization."""
    print("Step 'oauth_callback': Received redirect from Intuit.")

    # Get the parameters from the URL query string provided by Intuit.
    auth_code = request.GET.get('code')  # The authorization code needed to exchange for tokens
    realm_id = request.GET.get('realmId')  # The QuickBooks company ID (Data Services realm ID)
    state = request.GET.get('state')  # The state parameter returned by Intuit
    error = request.GET.get('error')  # Check if Intuit returned an error instead of a code
    error_description = request.GET.get('error_description')  # Description of the error

    print(
        f"Step 'oauth_callback': Received parameters: code={auth_code}, realmId={realm_id}, state={state}, error={error}, error_description={error_description}")

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
            'index_url_name': 'payments:quickbooks_status'
        }, status=400)  # Use a 400 status code for client errors

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
            'received_params': request.GET,  # Include received GET params for debugging
            'index_url_name': 'payments:quickbooks_status'
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

        saved_token = save_quickbooks_token_obj(
            auth_client.access_token,
            auth_client.refresh_token,
            auth_client.expires_in,  # Use expires_in (seconds) from the client
            auth_client.realm_id  # Use realm_id from the client
        )

        if saved_token:
            print(f"Step 'oauth_callback': Tokens successfully saved to database for Realm ID: {saved_token.realm_id}")
            # Redirect the user back to your application's frontend or a status page after successful connection.
            # Redirecting to a DRF status API endpoint might not be the final user experience,
            # but it's useful for confirming the connection status.
            return redirect(reverse('payments:quickbooks_status'))  # Redirect to your status API endpoint

        else:
            print("Step 'oauth_callback': Failed to save tokens to database.")
            # Handle token save failure - render an error page
            return render(request, 'payments/oauth_error.html', {
                'error': 'Token Save Failed',
                'error_description': 'Successfully received tokens from Intuit, but failed to save them to the database.',
                'index_url_name': 'payments:quickbooks_status'
            }, status=500)


    except AuthClientError as e:
        print(f"Step 'oauth_callback': Intuit AuthClient Error during token exchange: {e}")
        # Handle specific errors from the intuitlib client during the token exchange process.
        return render(request, 'payments/oauth_error.html', {
            'error': 'Intuit Token Exchange Error',
            'error_description': str(e),  # Convert exception to string for display
            'index_url_name': 'payments:quickbooks_status'
        }, status=500)  # Use a 500 status code for server-side errors like failed exchange
    except Exception as e:
        print(f"Step 'oauth_callback': General Error during token exchange: {e}")
        # Catch any other unexpected exceptions during the process.
        return render(request, 'payments/oauth_error.html', {
            'error': 'Token Exchange Failed',
            'error_description': str(e),  # Convert exception to string for display
            'index_url_name': 'payments:quickbooks_status'
        }, status=500)


# This standard Django view handles clearing all QuickBooks related data.
# It's typically accessed via a POST request from an admin interface or a dedicated button.
@require_http_methods(["POST"])  # Only allow POST requests for clearing data
# @login_required # Optional: Restrict who can clear data
# @permission_classes([IsAdminUser]) # Optional: Restrict to admin users (if using DRF permissions)
def clear_data_route(request):
    """Clears all QuickBooks related data from the database."""
    print("Step 'clear_data_route': Clearing all QuickBooks related data.")
    success = clear_db_data()  # Call the utility function to delete data

    if success:
        print("Step 'clear_data_route': Data cleared successfully.")
        # Return a success response (e.g., JSON) for an API endpoint
        # Use DRF status codes for clarity, even in a standard view returning JsonResponse
        return JsonResponse({'status': 'success', 'message': 'QuickBooks data cleared.'}, status=status.HTTP_200_OK)
    else:
        print("Step 'clear_data_route': Failed to clear data.")
        # Return an error response (e.g., JSON)
        return JsonResponse({'status': 'error', 'message': 'Failed to clear QuickBooks data.'},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# This standard Django view handles incoming QuickBooks Payment webhooks.
# It needs to be exempted from CSRF protection as webhooks don't send CSRF tokens.
# Intuit sends webhooks via POST requests.
@csrf_exempt
@require_http_methods(["POST"])  # Intuit sends webhooks via POST
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
        realm_id = notification.get("realmId")  # The QuickBooks company ID for this event
        data_event = notification.get("dataChangeEvent")  # The data change event details

        if not realm_id or not data_event:
            print("Step 'handle_payment_webhook': Skipping notification due to missing realmId or dataChangeEvent.")
            continue  # Skip to the next notification if essential data is missing

        entities = data_event.get("entities", [])  # List of entities that changed (e.g., Payments)
        if not entities:
            print(
                f"Step 'handle_payment_webhook': No entities found in dataChangeEvent for realm {realm_id}. Skipping.")
            continue  # Skip if no entities in the data change event

        # Process each entity within the data change event
        for entity in entities:
            entity_type = entity.get("name")  # e.g., "Payment", "Invoice", "Customer"
            entity_id = entity.get("id")  # The ID of the entity in QuickBooks
            operation_type = entity.get("operation")  # e.g., "Create", "Update", "Delete"

            if not entity_type or not entity_id or not operation_type:
                print(
                    f"Step 'handle_payment_webhook': Skipping entity due to missing name, id, or operation: {entity}. Realm: {realm_id}.")
                continue  # Skip incomplete entity data

            print(
                f"Step 'handle_payment_webhook': Processing entity: Type={entity_type}, ID={entity_id}, Operation={operation_type}, Realm={realm_id}")

            # We are specifically interested in 'Payment' entities for this webhook handler
            if entity_type == "Payment":
                # Need to fetch the full payment and potentially customer details from QuickBooks API
                # using the provided realmId and entityId (payment_id)
                try:
                    # Get the QuickBooks token for this realm from your database
                    # Uses: get_quickbooks_token_obj
                    token_obj = get_quickbooks_token_obj(realm_id)
                    if not token_obj:
                        print(
                            f"Step 'handle_payment_webhook': No QuickBooks token found for realm {realm_id}. Cannot fetch payment details.")
                        # Log this error, but don't necessarily return an error response to Intuit yet.
                        # Handle this failure asynchronously if needed (e.g., add to retry queue).
                        continue  # Skip processing this payment event

                    # Fetch the full Payment details from the Intuit API using the utility function
                    # Uses: get_payment_details_from_intuit
                    payment_data = get_payment_details_from_intuit(token_obj, realm_id, entity_id)
                    if not payment_data:
                        print(
                            f"Step 'handle_payment_webhook': Failed to fetch payment details for ID {entity_id} from Intuit API.")
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
                            print(
                                f"Step 'handle_payment_webhook': Failed to fetch customer details for ID {customer_id_qbo} from Intuit API.")
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
                        print(
                            f"Step 'handle_payment_webhook': Unhandled operation type: {operation_type} for Payment ID {entity_id}.")
                        # Log unhandled operation type

                    if not success:
                        print(
                            f"Step 'handle_payment_webhook': Processing failed for Payment ID {entity_id}, Operation {operation_type}: {message}")
                        # Decide how to handle processing failures (e.g., log, retry queue, send alert).
                        # For webhooks, it's often best to return 200 OK quickly and handle failures asynchronously.

                except Exception as e:
                    print(
                        f"Step 'handle_payment_webhook': Unexpected error processing Payment webhook for ID {entity_id}: {e}")
                    # Catch any unexpected errors during processing of a single entity
                    # Again, return 200 OK to Intuit and handle failure asynchronously
                    pass  # Continue processing other events if any

    # Intuit expects a 200 OK response quickly to acknowledge receipt of the webhook payload.
    # Any processing failures within the loop should be handled asynchronously (e.g., logging, retries).
    print("Step 'handle_payment_webhook': Webhook processing finished (returning 200 OK).")
    return HttpResponse("Webhook received and processed.", status=status.HTTP_200_OK)


class QuickbooksStatusAPIView(APIView):
    """
    API view to get the current QuickBooks connection status.
    """

    permission_classes = [AllowAny]

    def get(self, request, *args, **kwargs):
        print("Step 'QuickbooksStatusAPIView': Getting QuickBooks connection status.")
        # Retrieve the QuickBooks token object from the database
        # Uses: get_quickbooks_token_obj
        token = get_quickbooks_token_obj()

        # Determine connection status
        # is_connected = token is not None and token.access_token is not None and token.realm_id is not None
        is_expired = is_token_expired(token) if token else True
        is_connected = token and token.access_token and token.realm_id and not is_expired
        expires_at = token.expires_at if token else None

        # Prepare status data as a dictionary
        status_data = {
            'is_connected': is_connected,
            "token_expired": is_expired,
            'realm_id': token.realm_id if token else None,
            'expires_at': expires_at.isoformat() if expires_at else None,
            'auth_initiate_url': request.build_absolute_uri(reverse('payments:intuit_auth')),
            'clear_data_url': request.build_absolute_uri(reverse('payments:clear_data')),
            'webhook_url_example': request.build_absolute_uri(reverse('payments:payment_callback')),
            'token_storage_info': 'database models',  # Indicate where tokens are stored
        }

        # Return the status data as a DRF Response
        return Response(status_data)


# Admin-facing view to list processed transactions and credits
class TransactionListView(APIView):
    # Restrict to authenticated staff users
    permission_classes = [IsAuthenticated, IsAdminUser]

    # permission_classes = [AllowAny]

    def get(self, request: Request, *args, **kwargs):
        print("Django DRF View (List): Accessing processed transactions from DB.")
        # Load data from the database using utility functions
        transactions = get_transactions_from_db()  # Gets list of dictionaries
        credits_data = get_credits_from_db()  # Gets dictionary of credits

        # Format credits for display - maybe list them? Or just show the total
        # credits_display is already the dictionary {customer_id: total_credits}

        # Load token data from the database for current status display
        token = get_quickbooks_token_obj()

        print("Django DRF View (List): Loaded data from DB.")

        # Return a DRF Response
        return Response({
            "processed_transactions": transactions,
            "customer_credits": credits_data,
            "token_status": "Loaded from DB" if token else "Not Loaded (No token in DB)",
            "connected_realm": token.realm_id if token else "None"
        }, status=status.HTTP_200_OK)


# Admin-facing view to clear all data (for testing)
class ClearDataView(APIView):
    # Restrict to authenticated staff users
    permission_classes = [IsAuthenticated, IsAdminUser]

    def post(self, request: Request, *args, **kwargs):  # Use POST for destructive action
        print("Django DRF View (Clear): Clearing all stored data from the database.")
        success = clear_db_data()  # Use utility function

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
    # permission_classes = [IsAuthenticated, IsAdminUser]
    permission_classes = [AllowAny]
