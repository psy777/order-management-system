import json
import pathlib
import sys

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import _prepare_contact_details_for_storage, _deserialize_contact_details


def test_prepare_contact_details_normalizes_payload_structures():
    payload = {
        "contactDetails": {
            "addresses": [
                {
                    "label": "HQ",
                    "kind": "shipping",
                    "street": "123 A St",
                    "city": "Townsville",
                    "state": "CA",
                    "postalCode": "90001",
                    "isPrimary": True,
                },
                {
                    "label": "Invoices",
                    "kind": "billing",
                    "street": "500 Ledger Way",
                    "city": "Townsville",
                    "state": "CA",
                    "postalCode": "90002",
                },
            ],
            "emails": [
                {"label": "Work", "value": "orders@example.com", "isPrimary": True},
                {"label": "Billing", "value": "billing@example.com"},
            ],
            "phones": [
                {"label": "Main", "value": "123-456-7890", "isPrimary": True},
                {"label": "Mobile", "value": "(555) 222-1212"},
            ],
        }
    }

    details_info = _prepare_contact_details_for_storage(payload, force=True)
    details = details_info["details"]

    assert details["emails"][0]["value"] == "orders@example.com"
    assert details["phones"][0]["value"] == "1234567890"

    kinds = {entry["kind"] for entry in details["addresses"]}
    assert {"shipping", "billing"}.issubset(kinds)
    assert any(entry["isPrimary"] for entry in details["addresses"] if entry["kind"] == "shipping")
    assert any(entry["kind"] == "billing" for entry in details["addresses"])

    assert details_info["shipping"]["street"] == "123 A St"
    assert details_info["billing"]["city"] == "Townsville"


def test_prepare_contact_details_infers_addresses_from_flat_fields():
    payload = {
        "contactName": "Pat Customer",
        "shippingAddress": "42 Ocean Ave",
        "shippingCity": "Seaside",
        "shippingState": "WA",
        "shippingZipCode": "98111",
        "billingAddress": "840 Ledger Rd",
        "billingCity": "Accounting",
        "billingState": "WA",
        "billingZipCode": "98112",
        "email": "pat@example.com",
        "phone": "(425) 555-0100",
    }

    details_info = _prepare_contact_details_for_storage(payload, force=True)
    details = details_info["details"]

    assert details_info["primary_email"] == "pat@example.com"
    assert details_info["primary_phone"] == "4255550100"

    shipping_entry = next(entry for entry in details["addresses"] if entry["kind"] == "shipping")
    billing_entry = next(entry for entry in details["addresses"] if entry["kind"] == "billing")

    assert shipping_entry["street"] == "42 Ocean Ave"
    assert billing_entry["postalCode"] == "98112"


def test_deserialize_contact_details_merges_snapshot_defaults():
    contact_snapshot = {
        "email": "snapshot@example.com",
        "phone": "5559991234",
        "shippingAddress": "100 Legacy St",
        "shippingCity": "Legacy",
        "shippingState": "NY",
        "shippingZipCode": "10001",
        "billingAddress": "200 Invoice Ave",
        "billingCity": "Ledger",
        "billingState": "NY",
        "billingZipCode": "10002",
    }
    raw_details = json.dumps(
        {
            "emails": [],
            "phones": [],
            "addresses": [
                {
                    "label": "HQ",
                    "kind": "shipping",
                    "street": "500 Updated St",
                    "city": "Legacy",
                    "state": "NY",
                    "postalCode": "10011",
                }
            ],
        }
    )

    details = _deserialize_contact_details(contact_snapshot, raw_details)

    assert details["emails"][0]["value"] == "snapshot@example.com"
    assert details["phones"][0]["value"] == "5559991234"

    shipping_entry = next(entry for entry in details["addresses"] if entry["kind"] == "shipping")
    assert shipping_entry["street"] == "500 Updated St"

    billing_entry = next(entry for entry in details["addresses"] if entry["kind"] == "billing")
    assert billing_entry["postalCode"] == "10002"
