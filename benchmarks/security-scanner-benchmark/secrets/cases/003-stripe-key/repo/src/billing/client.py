import stripe

stripe.api_key = "__BENCHMARK_PLACEHOLDER_STRIPE_KEY__"


def charge_customer(customer_id, amount_cents):
    return stripe.Charge.create(customer=customer_id, amount=amount_cents, currency="usd")
