import stripe
from django.conf import settings
from .models import PaymentTransaction
from users.models import UserProfile
from django.contrib.auth import get_user_model
from django.utils import timezone
import json

# Initialize Stripe with the API key
stripe.api_key = settings.STRIPE_SECRET_KEY
endpoint_secret = settings.STRIPE_WEBHOOK_SECRET

# Fixed credits for each payment tier
TIER_CREDITS = {
    "tester": 1,
    "starter": 4,
    "growth": 6,
    "pro": 8,
    "ultimate": 12,
}

# Stripe Price IDs for each tier (test/live keys must match the mode)
TIER_PRICE_IDS = {
    "tester": "price_1RfGhBPH9MsL890i9cF5VLWz",
    "starter": "price_1RM6UmP8pRFcBjQOxisxySua",  # replace with your test/live IDs
    "growth": "price_1RM6VXP8pRFcBjQORtgODcjH",
    "pro": "price_1RM6Z4P8pRFcBjQOj9Fl9gQL",
    "ultimate": "price_1RM6b6P8pRFcBjQONwKtapuP",
}

User = get_user_model()

def create_checkout_session(price_id, email, tier):
    """
    Create a Stripe checkout session for the given price ID and email.
    
    Args:
        price_id (str): Stripe price ID
        email (str): Customer email
        tier (str): Subscription tier
        
    Returns:
        tuple: (response_dict, status_code)
    """
    try:
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            customer_email=email,
            line_items=[
                {
                    "price": price_id,
                    "quantity": 1,
                }
            ],
            mode="payment",
            success_url=settings.STRIPE_SUCCESS_URL,
            cancel_url=settings.STRIPE_CANCEL_URL,
            metadata={"tier": tier},
        )
        return {"url": session.url}, 200
    except stripe.error.StripeError as e:
        return {"error": str(e)}, 400
    except Exception as e:
        return {"error": "An unexpected error occurred: " + str(e)}, 500

def handle_webhook(payload, sig_header):
    """
    Handle Stripe webhook events.
    
    Args:
        payload (bytes): Request body
        sig_header (str): Stripe signature header
        
    Returns:
        tuple: (response_dict, status_code)
    """
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, endpoint_secret)
    except ValueError as e:
        print(f"Invalid payload: {e}")
        return {"error": "Invalid payload"}, 400
    except stripe.error.SignatureVerificationError as e:
        print(f"Invalid signature: {e}")
        return {"error": "Invalid signature"}, 400

    event_type = event["type"]
    
    # Handle specific event types
    if event_type == "checkout.session.completed":
        handle_successful_payment(event)
    elif event_type == "payment_intent.payment_failed":
        handle_failed_payment(event)
    elif event_type == "charge.failed":
        handle_failed_charge(event)
    else:
        print(f"Unhandled event type {event_type}")

    return {"success": True}, 200

# In stripe_utils.py

def handle_successful_payment(event):
    """
    Process a successful payment event from Stripe.

    Args:
        event (dict): Stripe event object
    """
    session = event["data"]["object"]
    print(session)

    # Get user from email
    email = session.get("customer_email")
    try:
        user = User.objects.get(email=email)
        # Get the related UserProfile
        user_profile = user.user_profile # Access the related profile using the related_name
    except User.DoesNotExist:
        print(f"User with email {email} not found")
        return
    except UserProfile.DoesNotExist:
        print(f"UserProfile not found for user {email}")
        # Handle this case: maybe create a profile? Or log a critical error.
        return


    # Extract payment details
    payment_intent_id = session.get("payment_intent")
    amount = session.get("amount_total", 0) / 100  # Stripe amounts are in cents
    currency = session.get("currency", "usd").upper()
    tier = session.get("metadata", {}).get("tier")

    # Determine credits from tier
    credits = TIER_CREDITS.get(tier, 0)

    # Record the transaction
    transaction = PaymentTransaction(
        user=user,
        realm_id="stripe", # Or 'checkout.session' if you prefer
        transaction_id=payment_intent_id,
        transaction_date=timezone.now(),
        customer_name=f"{user.first_name} {user.last_name}".strip(),
        customer_email=email,
        amount=amount,
        currency=currency,
        status=PaymentTransaction.STATUS_SUCCESS,
        tier=tier,
        credits=credits, # This is credits for *this transaction*
        payment_gateway_response=session
    )
    transaction.save()

    # Update user credits on the UserProfile
    try:
        user_profile.available_credits += credits # Update the credits on the profile
        user_profile.save()
        print(f"Added {credits} credits to user {user.email} (Profile ID: {user_profile.id}). New total: {user_profile.available_credits}")
    except Exception as e:
         print(f"Error updating user credits on profile for {user.email}: {e}")
         # Log this error properly, as it indicates a problem updating the profile

    print(f"Payment transaction recorded: {transaction.transaction_id}")

def handle_failed_payment(event):
    """
    Process a failed payment event from Stripe.
    
    Args:
        event (dict): Stripe event object
    """
    payment_intent = event["data"]["object"]
    error_message = payment_intent.get("last_payment_error", {}).get("message", "Unknown error")
    
    payment_intent_id = payment_intent.get("id")
    try:
        transaction = PaymentTransaction.objects.get(transaction_id=payment_intent_id)
        transaction.status = PaymentTransaction.STATUS_FAILED
        transaction.payment_gateway_response = payment_intent
        transaction.save()
        print(f"Payment failed: ID={payment_intent_id}, Error={error_message}")
    except PaymentTransaction.DoesNotExist:
        print(f"No transaction found for failed payment: {payment_intent_id}")

def handle_failed_charge(event):
    """
    Process a failed charge event from Stripe.
    
    Args:
        event (dict): Stripe event object
    """
    charge = event["data"]["object"]
    error_message = charge.get("failure_message", "Unknown error")
    
    payment_intent_id = charge.get("payment_intent")
    if payment_intent_id:
        try:
            transaction = PaymentTransaction.objects.get(transaction_id=payment_intent_id)
            transaction.status = PaymentTransaction.STATUS_FAILED
            transaction.payment_gateway_response = charge
            transaction.save()
            print(f"Charge failed: ID={charge.get('id')}, Error={error_message}")
        except PaymentTransaction.DoesNotExist:
            print(f"No transaction found for failed charge: {payment_intent_id}")
