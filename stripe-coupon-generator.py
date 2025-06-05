import stripe
import random
import string

stripe.api_key = ""

def generate_random_code(length=10):
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))

coupon_id = "d3zpeYJ9"  # Replace with your actual coupon ID
num_codes = 75

with open("stripe-promo-codes.txt", "w") as file:
    for _ in range(num_codes):
        code = generate_random_code()
        promo = stripe.PromotionCode.create(
            coupon=coupon_id,
            code=code,
            max_redemptions=1,
            metadata={"batch": "June2025"}
        )
        file.write(f"{promo.code}\n")

print("✅ All promo codes saved to stripe-promo-codes.txt")
