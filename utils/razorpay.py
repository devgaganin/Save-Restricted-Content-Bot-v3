"""Signature and payment validation for optional Razorpay payment links."""
import hashlib
import hmac


def valid_signature(secret, raw_body, signature):
    if not secret or not signature or len(signature) != 64:
        return False
    digest = hmac.new(secret.encode('utf-8'), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, signature)


def paid_event_details(event):
    """Return link and payment entities, or reject incomplete/partial payments."""
    if not isinstance(event, dict) or event.get('event') != 'payment_link.paid':
        return None
    try:
        link = event['payload']['payment_link']['entity']
        payment = event['payload']['payment']['entity']
        if (not link['id'] or not link['reference_id'] or link['status'] != 'paid'
                or payment['status'] != 'captured' or not payment['id']
                or link['currency'] != 'INR' or payment['currency'] != 'INR'
                or not isinstance(link['amount'], int) or link['amount'] <= 0
                or link['amount_paid'] != link['amount']
                or payment['amount'] != link['amount']):
            return None
        return link, payment
    except (KeyError, TypeError):
        return None


def grant_pipeline(user_id, days, charge_id, now):
    milliseconds = days * 86400 * 1000
    end = {'$add': [{'$max': [{'$ifNull': ['$subscription_end', now]}, now]}, milliseconds]}
    return [{'$set': {
        'user_id': user_id,
        'subscription_start': {'$ifNull': ['$subscription_start', now]},
        'subscription_end': end,
        'expireAt': end,
        'applied_payments': {'$concatArrays': [{'$ifNull': ['$applied_payments', []]}, [charge_id]]},
    }}]
