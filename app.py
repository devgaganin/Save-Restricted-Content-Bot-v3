# Copyright (c) 2025 devgagan : https://github.com/devgaganin.
# Licensed under the GNU General Public License v3.0.
# See LICENSE file in the repository root for full license text.

import os
from flask import Flask, render_template

app = Flask(__name__)

@app.route("/")
def welcome():
    # Render the welcome page with animated "Tgwithrohit" text
    return render_template("welcome.html")


# Signed provider webhook. Run this web service publicly only when Razorpay is
# explicitly enabled and the webhook secret is configured.
from flask import request, jsonify
from datetime import datetime
from pymongo import MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError
from config import MONGO_DB, DB_NAME
from utils.razorpay import valid_signature, paid_event_details, grant_pipeline
from utils.billing import check_premium_indexes

app.config['MAX_CONTENT_LENGTH'] = 1024 * 1024


@app.route('/health')
def health():
    # Web-process liveness only; it does not imply bot/database readiness.
    return jsonify(status='ok', component='web')


@app.route('/webhooks/razorpay', methods=['POST'])
def razorpay_webhook():
    if os.getenv('RAZORPAY_ENABLED', '').lower() != 'true':
        return 'Disabled', 404
    secret = os.getenv('RAZORPAY_WEBHOOK_SECRET', '')
    raw = request.get_data(cache=True)
    if not valid_signature(secret, raw, request.headers.get('X-Razorpay-Signature', '')):
        return 'Invalid signature', 401
    event = request.get_json(silent=True)
    if not isinstance(event, dict):
        return 'Invalid payload', 400
    if event.get('event') != 'payment_link.paid':
        return 'Ignored', 200
    details = paid_event_details(event)
    if not details:
        return 'Invalid payment details', 400
    link, payment = details
    try:
        mongo = MongoClient(MONGO_DB, serverSelectionTimeoutMS=5000)
        try:
            database = mongo[DB_NAME]
            invoices = database['razorpay_payments']
            premium = database['premium_users']
            premium.create_index('user_id', unique=True)
            invoices.create_index('payment_id', unique=True, sparse=True)
            invoice = invoices.find_one({'_id': link['reference_id']})
            # A paid webhook can arrive before the link ID has been stored.
            if not invoice or invoice.get('status') == 'creating':
                return 'Retry later', 503
            if (invoice.get('provider_link_id') != link['id'] or invoice.get('amount') != link['amount']
                    or invoice.get('currency') != link['currency']):
                return 'Invoice mismatch', 400
            if invoice.get('status') == 'completed':
                return ('OK', 200) if invoice.get('payment_id') == payment['id'] else ('Payment mismatch', 409)
            check_premium_indexes(premium.index_information())
            invoice = invoices.find_one_and_update({
                '_id': invoice['_id'], 'status': {'$in': ['created', 'processing']},
                '$or': [{'payment_id': {'$exists': False}}, {'payment_id': payment['id']}],
            }, {'$set': {'status': 'processing', 'payment_id': payment['id']}}, return_document=ReturnDocument.AFTER)
            if not invoice:
                return 'Payment conflict', 409
            charge = 'razorpay:' + payment['id']
            selector = {'user_id': invoice['user_id'], 'applied_payments': {'$ne': charge}}
            pipeline = grant_pipeline(invoice['user_id'], invoice['days'], charge, datetime.utcnow())
            try:
                premium.update_one(selector, pipeline, upsert=True)
            except DuplicateKeyError:
                premium.update_one(selector, pipeline)
            granted = premium.find_one({'user_id': invoice['user_id'], 'applied_payments': charge})
            if not granted:
                return 'Retry later', 503
            invoices.update_one({'_id': invoice['_id'], 'payment_id': payment['id']},
                                {'$set': {'status': 'completed', 'completed_at': datetime.utcnow()}})
            return 'OK', 200
        finally:
            mongo.close()
    except DuplicateKeyError:
        return 'Payment conflict', 409
    except Exception:
        app.logger.exception('Razorpay webhook processing failed')
        return 'Retry later', 503


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
