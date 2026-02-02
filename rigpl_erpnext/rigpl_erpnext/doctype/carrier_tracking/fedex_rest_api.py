# -*- coding: utf-8 -*-
# Copyright (c) 2026, Rohit Industries Ltd. and contributors
# For license information, please see license.txt

"""
FedEx REST API Integration Module

This module handles all interactions with FedEx's REST API v1.
It replaces the deprecated SOAP-based fedex Python library.

Key Features:
- OAuth 2.0 authentication with token caching
- Rate quote API
- Shipment booking API
- Shipment tracking API
- Shipment deletion API
- Address validation API
"""

from __future__ import unicode_literals
import frappe
import requests
import json
import base64
from datetime import datetime, timedelta
from frappe.utils import flt, cstr, now_datetime

# UOM mapper for FedEx API
uom_mapper = {"Kg": "KG", "LB": "LB", "kg": "KG", "cm": "CM"}

# API Base URLs
FEDEX_API_URLS = {
    "test": {
        "auth": "https://apis-sandbox.fedex.com/oauth/token",
        "ship": "https://apis-sandbox.fedex.com/ship/v1/shipments",
        "rate": "https://apis-sandbox.fedex.com/rate/v1/rates/quotes",
        "track": "https://apis-sandbox.fedex.com/track/v1/trackingnumbers",
        "address": "https://apis-sandbox.fedex.com/address/v1/addresses/resolve",
        "cancel": "https://apis-sandbox.fedex.com/ship/v1/shipments/cancel"
    },
    "production": {
        "auth": "https://apis.fedex.com/oauth/token",
        "ship": "https://apis.fedex.com/ship/v1/shipments",
        "rate": "https://apis.fedex.com/rate/v1/rates/quotes",
        "track": "https://apis.fedex.com/track/v1/trackingnumbers",
        "address": "https://apis.fedex.com/address/v1/addresses/resolve",
        "cancel": "https://apis.fedex.com/ship/v1/shipments/cancel"
    }
}


def get_api_base_url(transporter_doc, endpoint_type):
    """
    Get the appropriate API base URL based on environment and endpoint type
    
    Args:
        transporter_doc: Transporters doctype document
        endpoint_type: Type of endpoint (auth, ship, rate, track, address, cancel)
    
    Returns:
        str: Full URL for the endpoint
    """
    env = "test" if 1 else "production"
    return FEDEX_API_URLS[env][endpoint_type]


def get_oauth_token(transporter_doc, force_refresh=False):
    """
    Get OAuth 2.0 access token for FedEx API
    Tokens are cached for 55 minutes (5 minutes before expiry)
    
    Args:
        transporter_doc: Transporters doctype document
        force_refresh: Force token regeneration even if cached
    
    Returns:
        str: OAuth access token
    """
    cache_key = f"fedex_oauth_token_{transporter_doc.name}"
    
    # Check cache first
    if not force_refresh:
        cached_token = frappe.cache().get_value(cache_key)
        if cached_token:
            return cached_token
    
    # Generate new token
    auth_url = get_api_base_url(transporter_doc, "auth")
    
    payload = {
        "grant_type": "client_credentials",
        "client_id": transporter_doc.fedex_client_id,
        "client_secret": transporter_doc.fedex_client_secret
    }
    
    headers = {
        "Content-Type": "application/x-www-form-urlencoded"
    }
    
    try:
        response = requests.post(auth_url, data=payload, headers=headers, timeout=30)
        response.raise_for_status()
        
        token_data = response.json()
        access_token = token_data.get("access_token")
        
        # Cache token for 55 minutes (expires in 60)
        frappe.cache().set_value(cache_key, access_token, expires_in_sec=3300)
        
        return access_token
        
    except requests.exceptions.RequestException as e:
        frappe.throw(f"FedEx OAuth Authentication Failed: {str(e)}")
    except Exception as e:
        frappe.throw(f"FedEx OAuth Error: {str(e)}")


def make_api_request(transporter_doc, endpoint_type, payload, method="POST"):
    """
    Make an authenticated request to FedEx API
    Handles token refresh on 401 errors
    
    Args:
        transporter_doc: Transporters doctype document
        endpoint_type: Type of endpoint (ship, rate, track, address, cancel)
        payload: Request payload dictionary
        method: HTTP method (POST, PUT, etc.)
    
    Returns:
        dict: Response JSON data
    """
    url = get_api_base_url(transporter_doc, endpoint_type)
    token = get_oauth_token(transporter_doc)
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}"
    }
    
    try:
        if method == "POST":
            response = requests.post(url, json=payload, headers=headers, timeout=60)
        elif method == "PUT":
            response = requests.put(url, json=payload, headers=headers, timeout=60)
        else:
            frappe.throw(f"Unsupported HTTP method: {method}")
        
        # Handle 401 - token expired, refresh and retry once
        if response.status_code == 401:
            token = get_oauth_token(transporter_doc, force_refresh=True)
            headers["Authorization"] = f"Bearer {token}"
            
            if method == "POST":
                response = requests.post(url, json=payload, headers=headers, timeout=60)
            elif method == "PUT":
                response = requests.put(url, json=payload, headers=headers, timeout=60)
        
        response.raise_for_status()
        return response.json()
        
    except requests.exceptions.HTTPError as e:
        error_msg = f"FedEx API Error: {response.status_code}"
        try:
            error_data = response.json()
            if "errors" in error_data:
                errors_list = []
                for error in error_data["errors"]:
                    err_msg = error.get('message', '')
                    err_code = error.get('code', '')
                    param_list = error.get('parameterList', [])
                    if param_list:
                        params = ', '.join([f"{p.get('key', '')}: {p.get('value', '')}" for p in param_list])
                        errors_list.append(f"{err_code} - {err_msg} ({params})")
                    else:
                        errors_list.append(f"{err_code} - {err_msg}")
                error_msg = f"{error_msg}\n" + "\n".join(errors_list)
        except:
            error_msg = f"{error_msg} - {str(e)}"
        
        frappe.throw(error_msg)
        
    except requests.exceptions.RequestException as e:
        frappe.throw(f"FedEx API Request Failed: {str(e)}")


def build_address_payload(address_doc, country_doc, state_doc=None):
    """
    Build address payload for FedEx API
    
    Args:
        address_doc: Address doctype document
        country_doc: Country doctype document
        state_doc: State doctype document (optional)
    
    Returns:
        dict: Address payload for API
    """
    state_code = state_doc.state_code if state_doc else ""
    
    address_payload = {
        "streetLines": [
            str(address_doc.address_line1)[:35],
            str(address_doc.address_line2)[:35]
        ],
        "city": str(address_doc.city)[:20],
        "stateOrProvinceCode": state_code[:2] if state_code else "",
        "postalCode": str(address_doc.pincode)[:10],
        "countryCode": country_doc.code[:2].upper(),    
    }
    # Remove empty street lines
    address_payload["streetLines"] = [line for line in address_payload["streetLines"] if line and line.strip()]
    
    return address_payload


def build_party_info(address_doc, country_doc, state_doc=None, account_number=None, 
                     contact_person=None, is_shipper=False):
    """
    Build party information (shipper or recipient) for FedEx API
    
    Args:
        address_doc: Address doctype document
        country_doc: Country doctype document
        state_doc: State doctype document (optional)
        account_number: FedEx account number (for shipper)
        contact_person: Contact doctype (for recipient)
        is_shipper: Boolean indicating if this is shipper info
    
    Returns:
        dict: Party information payload
    """
    # Build contact information
    if contact_person and not is_shipper:
        # For recipient, use contact person details
        sal = (contact_person.salutation + " ") if contact_person.salutation else ""
        first_n = (contact_person.first_name + " ") if contact_person.first_name else ""
        last_n = contact_person.last_name if contact_person.last_name else ""
        full_name = (sal + first_n + last_n)[:35]
        phone = (str(contact_person.phone) + str(contact_person.mobile_no))[:15]
    else:
        # For shipper, use address details
        full_name = str(address_doc.address_title)[:35]
        phone = str(address_doc.phone)[:15] if address_doc.phone else ""
    
    party_info = {
        "contact": {
            "personName": full_name,
            "phoneNumber": phone,
            "companyName": str(address_doc.address_title)[:35]
        },
        "address": build_address_payload(address_doc, country_doc, state_doc)
    }
    
    # Add account number for shipper
    if is_shipper and account_number:
        party_info["accountNumber"] = {"value": account_number}
    
    # Add TIN (Taxpayer Identification Number) if available
    if address_doc.gstin and address_doc.gstin != 'NA':
        party_info["tins"] = [{
            "number": address_doc.gstin,
            "tinType": "BUSINESS_NATIONAL"
        }]
    
    return party_info


def build_recipient_display(address_doc, country_doc, state_doc, contact_doc):
    """
    Build recipient details string for display
    
    Args:
        address_doc: Address doctype document
        country_doc: Country doctype document
        state_doc: State doctype document
        contact_doc: Contact doctype document
    
    Returns:
        str: Formatted recipient details
    """
    sal = (contact_doc.salutation + " ") if contact_doc.salutation else ""
    first_n = (contact_doc.first_name + " ") if contact_doc.first_name else ""
    last_n = contact_doc.last_name if contact_doc.last_name else ""
    full_name = sal + first_n + last_n
    
    state_code = state_doc.state_code if state_doc else ""
    
    return "\n".join([
        str(address_doc.address_title)[:35],
        full_name,
        str(address_doc.phone)[:15] if address_doc.phone else "",
        str(address_doc.address_line1)[:35],
        str(address_doc.address_line2)[:35] if address_doc.address_line2 else "",
        f"{str(address_doc.city)[:20]} {state_code} {str(address_doc.pincode)[:10]} {country_doc.code}"
    ])


def build_customs_detail(track_doc, transporter_doc, is_international, 
                         allowed_docs_items, allowed_docs, from_country_doc):
    """
    Build customs clearance detail for international or express shipments
    
    Args:
        track_doc: Carrier Tracking doctype document
        transporter_doc: Transporters doctype document
        is_international: Boolean indicating if shipment is international
        allowed_docs_items: List of allowed document types with items
        allowed_docs: List of allowed document types without items
        from_country_doc: Country doctype for origin
    
    Returns:
        dict: Customs clearance detail payload
    """
    # Map purpose to FedEx values
    purpose_mapping = {
        "SAMPLE": "NOT_SOLD",
        "GIFT": "GIFT",
        "REPAIR": "REPAIR_AND_RETURN",
        "RETURN": "REPAIR_AND_RETURN",
        "PERSONAL": "PERSONAL_EFFECTS",
        "SOLD": "SOLD"
    }
    
    fedex_purpose = "NOT_SOLD"
    if track_doc.purpose:
        for key, value in purpose_mapping.items():
            if key in track_doc.purpose.upper():
                fedex_purpose = value
                break
    
    # Build commodities list
    commodities = build_commodities_list(track_doc, allowed_docs_items, allowed_docs, from_country_doc)
    
    # Calculate customs value
    customs_amount = flt(track_doc.amount) if track_doc.amount else 100.0
    customs_amount = max(customs_amount, 100.0)  # Minimum 100
    
    customs_detail = {
        "dutiesPayment": {
            "paymentType": track_doc.duties_payment_by if track_doc.duties_payment_by else "SENDER",
            "payor": {
                "responsibleParty": {
                    "accountNumber": {
                        "value": transporter_doc.fedex_account_number
                    }
                }
            }
        },
        "commodities": commodities,
        "commercialInvoice": {
            "shipmentPurpose": fedex_purpose
        }
    }
    
    # Add invoice number for SOLD shipments
    if fedex_purpose == "SOLD" and track_doc.document_name:
        customs_detail["commercialInvoice"]["customerReferences"] = [{
            "customerReferenceType": "INVOICE_NUMBER",
            "value": track_doc.document_name
        }]
    
    return customs_detail


def build_commodities_list(track_doc, allowed_docs_items, allowed_docs, from_country_doc):
    """
    Build list of commodities for customs declaration
    
    Args:
        track_doc: Carrier Tracking doctype document
        allowed_docs_items: List of allowed document types with items
        allowed_docs: List of allowed document types without items
        from_country_doc: Country doctype for origin
    
    Returns:
        list: List of commodity dictionaries
    """
    commodities = []
    
    if track_doc.document in allowed_docs_items:
        # Get items from linked document
        doc = frappe.get_doc(track_doc.document, track_doc.document_name)
        total_qty = 0
        
        for row in doc.items:
            total_qty += row.get("qty")
            
            # Get HSN code
            hsn_doc = frappe.get_doc("GST HSN Code",
                                    frappe.get_value('Item', row.get('item_code'), 'customs_tariff_number'))
            
            # Get country of origin
            item_doc = frappe.get_doc("Item", row.get("item_code"))
            country_doc = frappe.get_doc("Country", item_doc.country_of_origin)
            
            commodity = {
                "description": hsn_doc.description[:30],
                "weight": {
                    "units": uom_mapper.get(track_doc.weight_uom, "KG"),
                    "value": float(track_doc.total_weight)
                },
                "quantity": int(total_qty),
                "quantityUnits": "EA",
                "unitPrice": {
                    "amount": float(doc.grand_total / total_qty) if track_doc.purpose == 'SOLD' else 1.0,
                    "currency": doc.currency
                },
                "customsValue": {
                    "amount": float(doc.grand_total) if track_doc.purpose == 'SOLD' else 1.0,
                    "currency": doc.currency
                },
                "harmonizedCode": hsn_doc.name[:14],  # First 14 characters
                "countryOfManufacture": country_doc.code[:2].upper()
            }
            commodities.append(commodity)
            
    elif track_doc.document in allowed_docs:
        # Generic commodity for documents without items
        total_qty = track_doc.total_handling_units
        desc = "OTHER PRINTED MATTER, INCLUDING PRINTED PICTURES AND PHOTOGRAPHS"
        
        commodity = {
            "description": desc[:30],
            "weight": {
                "units": uom_mapper.get(track_doc.weight_uom, "KG"),
                "value": float(track_doc.total_weight)
            },
            "quantity": int(total_qty),
            "quantityUnits": "EA",
            "unitPrice": {
                "amount": float(track_doc.amount / total_qty) if total_qty > 0 else 1.0,
                "currency": track_doc.currency if track_doc.currency else "INR"
            },
            "customsValue": {
                "amount": float(track_doc.amount) if track_doc.amount else 1.0,
                "currency": track_doc.currency if track_doc.currency else "INR"
            },
            "harmonizedCode": "49111010",
            "countryOfManufacture": from_country_doc.code[:2].upper()
        }
        commodities.append(commodity)
    else:
        frappe.throw(f"Document type {track_doc.document} is not supported for shipment booking")
    
    return commodities


def build_package_item(track_doc, pkg, pkg_doc, sequence_number):
    """
    Build package line item for FedEx API
    
    Args:
        track_doc: Carrier Tracking doctype document
        pkg: Package row from shipment_package_details
        pkg_doc: Shipment Package doctype document
        sequence_number: Package sequence number
    
    Returns:
        dict: Package item payload
    """
    package_item = {
        "sequenceNumber": sequence_number,
        "weight": {
            "units": uom_mapper.get(track_doc.weight_uom, "KG"),
            "value": float(pkg.weight_per_unit)
        }
    }
    
    # Add dimensions if available
    if pkg_doc.length and pkg_doc.width and pkg_doc.height:
        package_item["dimensions"] = {
            "length": int(pkg_doc.length),
            "width": int(pkg_doc.width),
            "height": int(pkg_doc.height),
            "units": uom_mapper.get(pkg_doc.dimension_uom, "CM")
        }
    
    # Add customer reference if document name is available
    if track_doc.document_name:
        package_item["customerReferences"] = [{
            "customerReferenceType": "CUSTOMER_REFERENCE",
            "value": track_doc.document_name[:30]
        }]
    
    return package_item


def store_file(filename, file_data, doctype, docname):
    """
    Store file as attachment in Frappe
    
    Args:
        filename: Name of the file
        file_data: Binary file data
        doctype: Doctype to attach to
        docname: Document name to attach to
    """
    from frappe.utils.file_manager import save_file
    
    try:
        save_file(filename, file_data, doctype, docname, is_private=0)
        frappe.msgprint(f"File {filename} saved successfully")
    except Exception as e:
        frappe.log_error(f"Error saving file {filename}: {str(e)}", "FedEx File Storage Error")



def get_rate_quote_rest(track_doc, transporter_doc, from_address_doc, to_address_doc, 
                        from_country_doc, to_country_doc):
    """
    Get rate quote from FedEx REST API
    
    Args:
        track_doc: Carrier Tracking doctype document
        transporter_doc: Transporters doctype document
        from_address_doc: From Address doctype document
        to_address_doc: To Address doctype document
        from_country_doc: From Country doctype document
        to_country_doc: To Country doctype document
    """
    # Build shipper info
    from_state_doc = None
    if from_address_doc.state_rigpl and from_address_doc.state_rigpl != "":
        from_state_doc = frappe.get_doc("State", from_address_doc.state_rigpl)
    
    shipper = {
        "address": build_address_payload(from_address_doc, from_country_doc, from_state_doc),
        "accountNumber": {
            "value": transporter_doc.fedex_account_number
        }
    }
    
    # Build recipient info
    to_state_doc = None
    if to_address_doc.state_rigpl and to_address_doc.state_rigpl != "":
        to_state_doc = frappe.get_doc("State", to_address_doc.state_rigpl)
    
    recipient = {
        "address": build_address_payload(to_address_doc, to_country_doc, to_state_doc)
    }
    
    # Build package line items
    requested_packages = []
    for row in track_doc.shipment_package_details:
        package = {
            "groupPackageCount": 1,
            "weight": {
                "units": uom_mapper.get(row.weight_uom, "KG"),
                "value": flt(row.package_weight)
            }
        }
        requested_packages.append(package)
    
    # Build customs clearance detail
    # FedEx requires customs clearance for:
    # 1. International shipments (different countries)
    # 2. EXPRESS/INTERNATIONAL service types (even on domestic routes!)
    customs_detail = None
    is_international = from_country_doc.code != to_country_doc.code
    is_express_service = "EXPRESS" in transporter_doc.fedex_service_code or "INTERNATIONAL" in transporter_doc.fedex_service_code
    
    if is_international or is_express_service:
        # Map Frappe purpose to FedEx shipment purpose
        purpose_mapping = {
            "SAMPLE": "NOT_SOLD",
            "GIFT": "GIFT",
            "REPAIR": "REPAIR_AND_RETURN",
            "RETURN": "REPAIR_AND_RETURN",
            "PERSONAL": "PERSONAL_EFFECTS",
            "SOLD": "SOLD"
        }
        
        fedex_purpose = "NOT_SOLD"  # Default
        if track_doc.purpose:
            for key, value in purpose_mapping.items():
                if key in track_doc.purpose.upper():
                    fedex_purpose = value
                    break
        
        # Calculate customs value with minimum of 100.0 to meet FedEx requirements
        customs_amount = flt(track_doc.amount) if track_doc.amount else 100.0
        customs_amount = max(customs_amount, 100.0)  # Enforce minimum 100
        
        customs_detail = {
            "customsValue": {
                "amount": customs_amount,
                "currency": track_doc.currency if track_doc.currency else "INR"
            },
            "commercialInvoice": {
                "shipmentPurpose": fedex_purpose
            }
        }
        
        # Add duties payment for true international shipments
        if is_international:
            customs_detail["dutiesPayment"] = {
                "paymentType": track_doc.duties_payment_by if track_doc.duties_payment_by else "SENDER"
            }
    
    # Build request payload
    payload = {
        "accountNumber": {
            "value": transporter_doc.fedex_account_number
        },
        "requestedShipment": {
            "shipper": shipper,
            "recipient": recipient,
            "shipDatestamp": datetime.now().strftime("%Y-%m-%d"),  # Required field
            "pickupType": "USE_SCHEDULED_PICKUP",  # REST API enum (was REGULAR_PICKUP in SOAP)
            "serviceType": transporter_doc.fedex_service_code,
            "rateRequestType": ["ACCOUNT"],
            "requestedPackageLineItems": requested_packages,
            "shippingChargesPayment": {
                "paymentType": "SENDER",
                "payor": {
                    "responsibleParty": {
                        "accountNumber": {
                            "value": transporter_doc.fedex_account_number
                        }
                    }
                }
            }
        }
    }
    
    # Add preferred currency only if specified
    if track_doc.currency and track_doc.currency != "":
        payload["requestedShipment"]["preferredCurrency"] = track_doc.currency
    
    # Add customs clearance ONLY if international
    if customs_detail:
        payload["requestedShipment"]["customsClearanceDetail"] = customs_detail
    
    # Debug: Log payload (remove in production)
    frappe.log_error(f"FedEx Rate Request Payload:\n{json.dumps(payload, indent=2)}", "FedEx Rate Debug")
    
    # Make API request
    response = make_api_request(transporter_doc, "rate", payload)
    
    # Parse response
    if response.get("output") and response["output"].get("rateReplyDetails"):
        rate_details = response["output"]["rateReplyDetails"][0]
        rated_shipment = rate_details.get("ratedShipmentDetails", [{}])[0]
        
        # Check for ODA surcharge
        surcharges = rated_shipment.get("shipmentRateDetail", {}).get("surCharges", [])
        oda_found = False
        for surcharge in surcharges:
            if surcharge.get("type") == "OUT_OF_DELIVERY_AREA":
                oda_found = True
                frappe.msgprint(f"ODA Surcharge: {surcharge.get('amount', {}).get('amount', 0)}")
        
        if oda_found and track_doc.allow_oda_shipment != 1:
            frappe.throw("Out of Delivery Area, Booking of Shipment Not Allowed")
        
        # Get cost and currency
        total_charge = rated_shipment.get("totalNetFedExCharge", 0)
        
        # Handle both formats: float or dict with amount/currency
        if isinstance(total_charge, dict):
            track_doc.shipment_cost = flt(total_charge.get("amount", 0))
            track_doc.shipment_cost_currency = total_charge.get("currency", track_doc.currency)
        else:
            # Direct float value
            track_doc.shipment_cost = flt(total_charge)
            # Get currency from rated shipment detail or use document currency
            track_doc.shipment_cost_currency = rated_shipment.get("currency") or \
                                               rated_shipment.get("shipmentRateDetail", {}).get("currency") or \
                                               track_doc.currency
        
        # Get delivery date if available
        if rate_details.get("commit") and rate_details["commit"].get("dateDetail"):
            delivery_date = rate_details["commit"]["dateDetail"].get("dayFormat")
            if delivery_date:
                try:
                    track_doc.expected_delivery_date = datetime.strptime(delivery_date, "%Y-%m-%d").date()
                except:
                    pass
        
        track_doc.save()
        frappe.msgprint(f"Rate Quote: {track_doc.shipment_cost} {track_doc.shipment_cost_currency}")
    else:
        frappe.throw("No rate details received from FedEx")


def create_shipment_rest(track_doc, transporter_doc, from_address_doc, to_address_doc,
                        from_country_doc, to_country_doc, contact_doc):
    """
    Create shipment using FedEx REST API and generate labels
    
    Args:
        track_doc: Carrier Tracking doctype document
        transporter_doc: Transporters doctype document
        from_address_doc: From Address doctype document
        to_address_doc: To Address doctype document
        from_country_doc: From Country doctype document
        to_country_doc: To Country doctype document
        contact_doc: Contact doctype document
    """
    # Allowed document types for commodities
    allowed_docs_items = ['Sales Invoice', 'Purchase Order', 'Delivery Note']
    allowed_docs = ['Carrier Tracking']
    
    # Get state documents if available
    from_state_doc = None
    if from_address_doc.state_rigpl and from_address_doc.state_rigpl != "":
        from_state_doc = frappe.get_doc("State", from_address_doc.state_rigpl)
    
    to_state_doc = None
    if to_address_doc.state_rigpl and to_address_doc.state_rigpl != "":
        to_state_doc = frappe.get_doc("State", to_address_doc.state_rigpl)
    
    # Build shipper information with TIN
    shipper = build_party_info(from_address_doc, from_country_doc, from_state_doc, 
                               transporter_doc.fedex_account_number, is_shipper=True)
    
    # Build recipient information with contact details
    recipient = build_party_info(to_address_doc, to_country_doc, to_state_doc, 
                                 contact_person=contact_doc, is_shipper=False)
    
    # Store recipient details for display
    track_doc.recipient_details = build_recipient_display(to_address_doc, to_country_doc, 
                                                          to_state_doc, contact_doc)
    
    # Determine if international shipment
    is_international = from_country_doc.code != to_country_doc.code
    is_express_service = "EXPRESS" in transporter_doc.fedex_service_code or "INTERNATIONAL" in transporter_doc.fedex_service_code
    
    # Build customs clearance detail if needed
    customs_detail = None
    if is_international or is_express_service:
        customs_detail = build_customs_detail(track_doc, transporter_doc, is_international,
                                               allowed_docs_items, allowed_docs, from_country_doc)
    
    # Process packages - FedEx requires sequential API calls for multi-package shipments
    pkg_count = track_doc.total_handling_units
    master_tracking_id = None
    
    for index, pkg in enumerate(track_doc.shipment_package_details):
        pkg_doc = frappe.get_doc("Shipment Package", pkg.shipment_package)
        
        # Build package line item
        package_item = build_package_item(track_doc, pkg, pkg_doc, index + 1)
        
        # Build base payload
        payload = {
            "labelResponseOptions": "LABEL",
            "requestedShipment": {
                "shipper": shipper,
                "recipients": [recipient],
                "shipDatestamp": datetime.now().strftime("%Y-%m-%d"),
                "serviceType": transporter_doc.fedex_service_code,
                "packagingType": "YOUR_PACKAGING",
                "pickupType": "USE_SCHEDULED_PICKUP",
                "blockInsightVisibility": False,
                "shippingChargesPayment": {
                    "paymentType": "SENDER",
                    "payor": {
                        "responsibleParty": {
                            "accountNumber": {
                                "value": transporter_doc.fedex_account_number
                            }
                        }
                    }
                },
                "labelSpecification": {
                    "imageType": "PDF",
                    "labelStockType": "PAPER_85X11_TOP_HALF_LABEL",
                    "labelFormatType": "COMMON2D",
                    "labelOrder": "SHIPPING_LABEL_FIRST"
                },
                "requestedPackageLineItems": [package_item]
            }
        }
        
        # Add master tracking ID for subsequent packages
        if index > 0 and master_tracking_id:
            payload["requestedShipment"]["masterTrackingId"] = {
                "trackingNumber": master_tracking_id
            }
            payload["requestedShipment"]["packageCount"] = pkg_count
        
        # Add customs detail if needed
        if customs_detail:
            payload["requestedShipment"]["customsClearanceDetail"] = customs_detail
            
            # Add shipping documents for commercial invoice
            if track_doc.purpose == "SOLD":
                payload["requestedShipment"]["shippingDocumentSpecification"] = {
                    "shippingDocumentTypes": ["COMMERCIAL_INVOICE"],
                    "commercialInvoiceDetail": {
                        "documentFormat": {
                            "imageType": "PDF",
                            "stockType": "PAPER_LETTER"
                        }
                    }
                }
        
        # Add reference if document name is available
        if track_doc.document_name:
            payload["requestedShipment"]["shipmentSpecialServices"] = {
                "specialServiceTypes": ["RETURN_SHIPMENT"] if track_doc.purpose == "RETURN" else []
            }
            payload["requestedShipment"]["customsClearanceDetail"] = payload["requestedShipment"].get("customsClearanceDetail", {})
            if track_doc.purpose == "SOLD":
                payload["requestedShipment"]["customsClearanceDetail"]["commercialInvoice"] = {
                    "shipmentPurpose": "SOLD"
                }
        
        # Log payload for debugging
        frappe.log_error(f"FedEx Shipment Request Payload (Package {index+1}/{pkg_count}):\n{json.dumps(payload, indent=2)}", 
                        "FedEx Shipment Debug")
        
        # Make API request
        response = make_api_request(transporter_doc, "ship", payload, method="POST")
        
        # Extract tracking number and label
        if response and "output" in response:
            output = response["output"]
            
            # Get tracking number
            if "transactionShipments" in output and len(output["transactionShipments"]) > 0:
                shipment_data = output["transactionShipments"][0]
                
                if "masterTrackingNumber" in shipment_data:
                    tracking_number = shipment_data["masterTrackingNumber"]
                elif "pieceResponses" in shipment_data and len(shipment_data["pieceResponses"]) > 0:
                    tracking_number = shipment_data["pieceResponses"][0].get("trackingNumber")
                else:
                    frappe.throw("No tracking number received from FedEx")
                
                # Store master tracking ID for subsequent packages
                if index == 0:
                    master_tracking_id = tracking_number
                    track_doc.awb_number = tracking_number
                
                # Store label
                if "pieceResponses" in shipment_data and len(shipment_data["pieceResponses"]) > 0:
                    piece = shipment_data["pieceResponses"][0]
                    if "packageDocuments" in piece:
                        for doc in piece["packageDocuments"]:
                            if doc.get("docType") == "LABEL" and "encodedLabel" in doc:
                                label_data = base64.b64decode(doc["encodedLabel"])
                                store_file(f'FEDEX-ID-{tracking_number}.pdf', label_data, 
                                         track_doc.doctype, track_doc.name)
                
                # Store commercial invoice if present
                if "shipmentDocuments" in shipment_data:
                    for doc in shipment_data["shipmentDocuments"]:
                        if doc.get("contentKey") == "COMMERCIAL_INVOICE" and "encodedLabel" in doc:
                            invoice_data = base64.b64decode(doc["encodedLabel"])
                            store_file(f'COMMER-INV-{track_doc.name}-{tracking_number}.pdf', 
                                     invoice_data, track_doc.doctype, track_doc.name)
                
                # Update package details
                pkg.tracking_number = tracking_number
                pkg.api_response = json.dumps(response, indent=2)
                
            else:
                frappe.throw("Invalid response structure from FedEx")
        else:
            frappe.throw("No response received from FedEx")
    
    # Update tracking document status
    track_doc.status = "Booked"
    track_doc.save()
    
    frappe.msgprint(f"Shipment booked successfully. Tracking Number: {track_doc.awb_number}")
    return track_doc.awb_number


def track_shipment_rest(track_doc, transporter_doc):
    """
    Track shipment using FedEx REST API
    
    Args:
        track_doc: Carrier Tracking doctype document
        transporter_doc: Transporters doctype document
    """
    # This is a placeholder for the tracking implementation
    # Will be implemented in the next iteration
    frappe.throw("Tracking REST API implementation in progress")


def delete_shipment_rest(track_doc, transporter_doc):
    """
    Delete/cancel shipment using FedEx REST API
    
    Args:
        track_doc: Carrier Tracking doctype document
        transporter_doc: Transporters doctype document
    """
    # This is a placeholder for the deletion implementation
    # Will be implemented in the next iteration
    frappe.throw("Shipment deletion REST API implementation in progress")


def validate_address_rest(transporter_doc, address_doc, country_doc):
    """
    Validate address using FedEx REST API
    
    Args:
        transporter_doc: Transporters doctype document
        address_doc: Address doctype document
        country_doc: Country doctype document
    """
    # This is a placeholder for the address validation implementation
    # Will be implemented in the next iteration
    frappe.throw("Address validation REST API implementation in progress")
