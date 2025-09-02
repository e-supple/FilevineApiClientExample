from jose.exceptions import JOSEError
import functions_framework
from google.cloud import firestore
import os
from flask import make_response
import requests
from jose import jwt as jose_jwt
import time

# init firestore
db = firestore.Client(database="invoicestaging-firestore")


# --- Configuration from Environment Variables ---
# in the function's runtime environment.
FILEVINE_IDENTITY_AUTHORITY = os.getenv(
    "FILEVINE_IDENTITY_AUTHORITY", "https://identity.filevine.com")
FILEVINE_WEBHOOK_AUDIENCE = os.getenv(
    "FILEVINE_WEBHOOK_AUDIENCE", "filevine-v2-webhooks")


# Cache for JWKS
_jwks_cache = None
_jwks_fetch_time = 0
JWKS_CACHE_DURATION_SECONDS = 3600  # Cache JWKS for 1 hour


def get_jwks():
    """Fetches and caches the JWKS from Filevine's identity server."""
    global _jwks_cache, _jwks_fetch_time

    is_cache_valid = _jwks_cache and (
        time.monotonic() - _jwks_fetch_time) < JWKS_CACHE_DURATION_SECONDS
    if is_cache_valid:
        print("Using cached JWKS.")
        return _jwks_cache

    try:
        discovery_url = f"{FILEVINE_IDENTITY_AUTHORITY}/.well-known/openid-configuration"
        discovery_res = requests.get(discovery_url, timeout=5)
        discovery_res.raise_for_status()
        jwks_uri = discovery_res.json()["jwks_uri"]

        jwks_res = requests.get(jwks_uri, timeout=5)
        jwks_res.raise_for_status()

        _jwks_cache = jwks_res.json()
        _jwks_fetch_time = time.monotonic()

        print("Fetched and cached new JWKS.")
        return _jwks_cache
    except (requests.exceptions.RequestException, KeyError) as e:
        print(f"Error fetching JWKS: {e}")
        _jwks_cache = None
        raise


def find_signing_key(token, jwks):
    """Finds the correct public key from the JWKS to verify the token."""
    unverified_header = jose_jwt.get_unverified_header(token)
    kid = unverified_header.get("kid")
    if not kid:
        raise JOSEError("JWT header is missing 'kid' (Key ID).")

    for key in jwks["keys"]:
        if key["kid"] == kid:
            return key

    raise JOSEError(
        f"Public key with matching 'kid' ({kid}) not found in JWKS.")


@functions_framework.http
def filevine_webhook_handler(request):
    """Handles and validates Filevine webhooks using JWT."""
    if request.method == "GET":
        return make_response("Function is active and ready to receive POST requests.", 200)

    if request.method != "POST":
        return make_response("Method Not Allowed", 405)

    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return make_response("Unauthorized: Missing Authorization header.", 401)

    token = auth_header.split(" ")[1]

    try:
        jwks = get_jwks()
        signing_key = find_signing_key(token, jwks)

        jose_jwt.decode(
            token,
            key=signing_key,
            algorithms=["RS512"],
            audience=FILEVINE_WEBHOOK_AUDIENCE,
            issuer=FILEVINE_IDENTITY_AUTHORITY
        )
        print("JWT validated successfully.")

        webhook_payload = request.get_json()
        if not isinstance(webhook_payload, dict):
            print("Error: Invalid JSON in request body.")
            return make_response("Bad Request: Invalid JSON.", 400)

        print(
            f"Successfully parsed webhook payload for Event: {webhook_payload.get('Event')}")

        # Check for specific criteria before storing
        object_id = webhook_payload.get('ObjectId', {})
        if object_id.get('SectionSelector') != "expenses" or object_id.get('FieldSelector') != "sendtofvcheckreq":
            print("Payload does not match criteria; skipping storage.")
            return make_response("Webhook received but not stored (does not match criteria).", 200)

        # Extract key fields and add raw_payload
        doc_data = {
            'event_type': webhook_payload.get('Event'),
            'object_type': webhook_payload.get('Object'),
            'user_id': webhook_payload.get('UserId'),
            'project_id': webhook_payload.get('ProjectId'),
            'field_selector': webhook_payload.get('ObjectId', {}).get('FieldSelector'), # sendtofvcheckreq
            'project_type_id': webhook_payload.get('ObjectId', {}).get('ProjectTypeId'), # 32506 for sendToQB
            'section_selector': webhook_payload.get('ObjectId', {}).get('SectionSelector'), # expenses
            'item_id': webhook_payload.get('Other', {}).get('ItemId'),
            'field_id': webhook_payload.get('Other', {}).get('FieldId'), #55550550
            'timestamp': webhook_payload.get('Timestamp'),
            'received_at': firestore.SERVER_TIMESTAMP,
            'processed': False,
            'processed_at': None,
            'raw_payload': webhook_payload
        }

        doc_ref = db.collection("filevine_webhook_events").document()
        doc_ref.set(doc_data)

        print(f"Stored webhook payload in Firestore with document ID: {doc_ref.id}")

        return make_response("Webhook received and stored.", 200)

    except JOSEError as e:
        print(f"JWT Validation Error: {e}")
        return make_response(f"Unauthorized: {e}", 401)
    except Exception as e:
        print(f"An unhandled error occurred: {e}")
        return make_response("Internal Server Error", 500)
