# Copyright (c) 2013, Rohit Industries Ltd. and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from datetime import datetime
from frappe.utils import flt, date_diff
from six import iteritems
from ....manufacturing_rigpl.utils.job_card_utils import get_bin

def execute(filters=None):
    if not filters:
        filters = {}

    if filters.get("date") and isinstance(filters.get("date"), str):
        try:
            filters["date"] = datetime.strptime(filters["date"], '%Y-%m-%d').date()
        except TypeError:
            pass

    columns = get_columns(filters)
    to_date = filters.get("date")
    
    conditions, conditions_it, params = get_conditions(filters)
    
    items, item_map = get_item_details(filters, params, conditions_it)
    
    if not items:
        return columns, []

    iwb_map = get_item_warehouse_map(filters, params, conditions, items)
    pl_map = get_pl_map(filters, items)
    value_map = get_value_map(filters, items)
    lpr_map = get_lpr_map(filters, items)
    item_fifo = get_fifo_queue(filters, params, conditions, items)

    data = []
    for item, item_dict in iteritems(item_fifo):
        if item_dict.get("total_qty", 0) > 0.5:
            fifo_queue = item_dict["fifo_queue"]
            details = item_dict["details"]
            if not fifo_queue:
                continue

            qty_dict = iwb_map.get(details.name, {}).get(details.warehouse, frappe._dict({"cur_qty": 0.0, "val_rate": 0.0, "value": 0.0}))
            
            average_age = get_average_age(fifo_queue, to_date)
            earliest_age = date_diff(to_date, fifo_queue[0][1])
            latest_age = date_diff(to_date, fifo_queue[-1][1])
            it_dict = item_map[details.name]
            
            row = [details.name, it_dict["desc"], details.warehouse, item_dict.get("total_qty"), qty_dict.cur_qty,
                    pl_map.get(details.name,{}).get("LP"), qty_dict.val_rate, qty_dict.value,
                    it_dict["bm"], it_dict["quality"], it_dict["tt"], it_dict["d1"], it_dict["w1"],
                    it_dict["l1"], it_dict["d2"], it_dict["l2"], it_dict["rm"], it_dict["brand"],
                    it_dict["vr"], lpr_map.get(details.name,{}).get("lpr"), it_dict["is_purchase_item"],
                    average_age, earliest_age, latest_age]

            data.append(row)

    return columns, data

def get_average_age(fifo_queue, to_date):
    batch_age = age_qty = total_qty = 0.0
    for batch in fifo_queue:
        batch_age = date_diff(to_date, batch[1])
        age_qty += batch_age * batch[0]
        total_qty += batch[0]

    return (age_qty / total_qty) if total_qty else 0.0

def get_fifo_queue(filters, params, conditions, items):
    item_details = {}
    for d in get_stock_ledger_entries(filters, params, conditions, items):
        key = (d.name, d.warehouse)
        item_details.setdefault(key, {"details": d, "fifo_queue": []})
        fifo_queue = item_details[key]["fifo_queue"]

        if d.voucher_type == "Stock Reconciliation":
            d.actual_qty = flt(d.qty_after_transaction) - flt(item_details[key].get("qty_after_transaction", 0))

        if d.actual_qty > 0:
            fifo_queue.append([d.actual_qty, d.posting_date])
        else:
            qty_to_pop = abs(d.actual_qty)
            while qty_to_pop:
                batch = fifo_queue[0] if fifo_queue else [0, None]
                if 0 < batch[0] <= qty_to_pop:
                    qty_to_pop -= batch[0]
                    fifo_queue.pop(0)
                else:
                    batch[0] -= qty_to_pop
                    qty_to_pop = 0

        item_details[key]["qty_after_transaction"] = d.qty_after_transaction

        if "total_qty" not in item_details[key]:
            item_details[key]["total_qty"] = d.actual_qty
        else:
            item_details[key]["total_qty"] += d.actual_qty

    return item_details

def get_stock_ledger_entries(filters, params, conditions, items):
    item_codes = tuple(d.name for d in items)
    query_params = params.copy()
    query_params["item_codes"] = item_codes
    
    sle_entries = frappe.db.sql(f"""SELECT
        sle.item_code as name, sle.stock_uom,
        sle.actual_qty, sle.posting_date, sle.posting_time, sle.voucher_type, sle.qty_after_transaction, sle.warehouse
        FROM `tabStock Ledger Entry` sle, `tabWarehouse` wh
        WHERE wh.name = sle.warehouse 
        AND CAST(IFNULL(wh.is_group, '0') AS CHAR) IN ('0', 'No', '')
        AND CAST(IFNULL(wh.disabled, '0') AS CHAR) IN ('0', 'No', '')
        AND CAST(IFNULL(sle.is_cancelled, '0') AS CHAR) IN ('0', 'No', '')
        {conditions} AND sle.item_code IN %(item_codes)s
        ORDER BY sle.item_code, sle.warehouse, posting_date, sle.posting_time ASC
        """, query_params, as_dict=1)
    return sle_entries

def get_columns(filters):
    return ["Item:Link/Item:120"] + ["Description::300"] + \
        ["Warehouse:Link/Warehouse:150"] + ["Quantity:Float:80"] + ["Current Stock:Float:100"] + \
        ["List Price:Currency:80"] + ["VR:Currency:80"] + ["Value:Currency:100"] + \
        ["BM::80"] + ["Qual::80"] +["TT::120"] + ["D1:Float:50"] + \
        ["W1:Float:50"] + ["L1:Float:60"] + ["D2:Float:50"] + \
        ["L2:Float:60"] + ["Is RM::50"] + ["Brand::60"] + ["Set Value:Currency:80"] + \
        ["Last PO Price:Currency:80"] + ["Is Purchase::80"] + ["Av Age:Float:80"] + \
        ["Earliest:Int:80"] + ["Latest:Int:80"]

def get_item_warehouse_map(filters, params, conditions, items):
    iwb_map = {}
    item_codes = tuple(d.name for d in items)
    query_params = params.copy()
    query_params["item_codes"] = item_codes
    
    bin_data = frappe.db.sql("""
        SELECT item_code, warehouse, actual_qty
        FROM `tabBin`
        WHERE item_code IN %(item_codes)s
    """, query_params, as_dict=1)
    
    bin_map = {}
    for b in bin_data:
        bin_map.setdefault(b.item_code, {})[b.warehouse] = flt(b.actual_qty)

    entries = frappe.db.sql(f"""SELECT sle.item_code, sle.warehouse,
        sle.qty_after_transaction as balance, sle.valuation_rate, sle.stock_value,
        TIMESTAMP(sle.posting_date, sle.posting_time) as pd_pt
        FROM `tabStock Ledger Entry` sle, `tabWarehouse` wh
        WHERE wh.name = sle.warehouse 
        AND CAST(IFNULL(wh.is_group, '0') AS CHAR) IN ('0', 'No', '')
        AND CAST(IFNULL(wh.disabled, '0') AS CHAR) IN ('0', 'No', '')
        AND CAST(IFNULL(sle.is_cancelled, '0') AS CHAR) IN ('0', 'No', '')
        {conditions} AND sle.item_code IN %(item_codes)s
        ORDER BY sle.item_code, sle.warehouse, pd_pt ASC
        """, query_params, as_dict=1)
        
    if entries:
        for d in entries:
            iwb_map.setdefault(d.item_code, {}).setdefault(d.warehouse, frappe._dict({
                            "bal_qty": 0.0, "val_rate":0.0, "value":0.0, "cur_qty": 0.0
                    }))
            qty_dict = iwb_map[d.item_code][d.warehouse]
            qty_dict.val_rate = flt(d.valuation_rate)
            qty_dict.value = flt(d.stock_value)
            qty_dict.bal_qty = flt(d.balance)
            qty_dict.cur_qty = bin_map.get(d.item_code, {}).get(d.warehouse, 0.0)

    return iwb_map

def get_item_details(filters, params, conditions_it):
    query = f"""SELECT it.name AS "name", it.description AS "desc",
        it.valuation_rate AS "vr", it.is_purchase_item
        FROM `tabItem` it
        WHERE IFNULL(it.end_of_life, '2099-12-31') > CURDATE() {conditions_it}"""

    raw_items = frappe.db.sql(query, params, as_dict=1)
    if not raw_items:
        return [], {}

    item_codes = [d.name for d in raw_items]

    item_attrs = {}
    attrs_to_fetch = [
        'Is RM', 'Base Material', 'HSS Quality', 'Carbide Quality',
        'Brand', 'Tool Type', 'd1_mm', 'w1_mm', 'l1_mm', 'd2_mm', 'l2_mm'
    ]
    if item_codes:
        chunk_size = 5000
        for i in range(0, len(item_codes), chunk_size):
            chunk = item_codes[i:i+chunk_size]
            attrs_data = frappe.db.sql("""
                SELECT parent, attribute, attribute_value
                FROM `tabItem Variant Attribute`
                WHERE parent IN %s AND attribute IN %s
            """, (tuple(chunk), tuple(attrs_to_fetch)), as_dict=1)
            
            for ad in attrs_data:
                item_attrs.setdefault(ad.parent, {})[ad.attribute] = ad.attribute_value

    items = []
    item_map = {}
    for d in raw_items:
        attrs = item_attrs.get(d.name, {})
        
        c_qual = attrs.get("Carbide Quality")
        h_qual = attrs.get("HSS Quality")
        quality_val = h_qual if h_qual else (c_qual if c_qual else "-")

        rm_val = attrs.get("Is RM") if attrs.get("Is RM") is not None else "-"
        bm_val = attrs.get("Base Material") if attrs.get("Base Material") is not None else "-"
        brand_val = attrs.get("Brand") if attrs.get("Brand") is not None else "-"
        tt_val = attrs.get("Tool Type") if attrs.get("Tool Type") is not None else "-"
        
        d1_v = flt(attrs.get("d1_mm"))
        w1_v = flt(attrs.get("w1_mm"))
        l1_v = flt(attrs.get("l1_mm"))
        d2_v = flt(attrs.get("d2_mm"))
        l2_v = flt(attrs.get("l2_mm"))

        d.update({
            "bm": bm_val,
            "brand": brand_val,
            "quality": quality_val,
            "tt": tt_val,
            "rm": rm_val,
            "d1": d1_v,
            "w1": w1_v,
            "l1": l1_v,
            "d2": d2_v,
            "l2": l2_v,
            "sort_key": (
                rm_val,
                bm_val,
                h_qual if h_qual is not None else "-",
                c_qual if c_qual is not None else "-",
                tt_val,
                d1_v,
                w1_v,
                l1_v,
                d2_v,
                l2_v
            )
        })
        items.append(d)

    items.sort(key=lambda x: x["sort_key"])
    
    for d in items:
        item_map.setdefault(d.name, d)

    return items, item_map

def get_pl_map(filters, items):
    pl_map = {}
    if not items: return pl_map
    item_codes = tuple(d.name for d in items)
    
    if filters.get("pl"):
        pl_map_int = frappe.db.sql("""SELECT p.item_code, p.price_list, p.price_list_rate AS LP
            FROM `tabItem Price` p
            WHERE p.price_list = %s AND p.item_code IN %s
        """, (filters["pl"], item_codes), as_dict=1)
        
        for d in pl_map_int:
            pl_map.setdefault(d.item_code, d)
            
    return pl_map

def get_value_map(filters, items):
    value_map = {}
    for d in items:
        value_map[d.name] = frappe._dict({'name': d.name, 'vr': d.vr})
    return value_map

def get_lpr_map(filters, items):
    lpr_map={}
    
    cond_po = " AND pr.posting_date <= '%s'" % filters.get("date") if filters.get("date") else ""
    for it in items:
        grn = frappe.db.sql("""SELECT pr.name, pri.item_code, pri.base_net_rate AS lpr,
            pri.base_rate, pri.rate, pri.net_rate
            FROM `tabPurchase Receipt` pr, `tabPurchase Receipt Item` pri
            WHERE pr.name = pri.parent AND pri.item_code = '%s' %s
            ORDER BY pr.posting_date DESC
            LIMIT 1""" %(it.name, cond_po), as_dict=1)
        if grn:
            lpr_map.setdefault(grn[0].item_code, frappe._dict({"lpr": grn[0].lpr}))
            
    return lpr_map

def get_conditions(filters):
    conditions = ""
    conditions_it = ""
    params = {}
    
    if filters.get("item"):
        conditions += " AND sle.item_code = %(item)s"
        conditions_it += " AND it.name = %(item)s"
        params["item"] = filters["item"]

    if filters.get("warehouse"):
        conditions += " AND sle.warehouse = %(warehouse)s"
        params["warehouse"] = filters["warehouse"]

    if filters.get("date"):
        conditions += " AND sle.posting_date <= %(date)s"
        params["date"] = filters.get("date")

    attr_filters = {
        "rm": "Is RM",
        "bm": "Base Material",
        "brand": "Brand",
        "tt": "Tool Type"
    }

    for f_key, attr_name in attr_filters.items():
        if filters.get(f_key):
            p_val = f"f_{f_key}"
            params[f"{p_val}_attr"] = attr_name
            params[f"{p_val}_val"] = filters.get(f_key)
            conditions_it += f" AND EXISTS (SELECT 1 FROM `tabItem Variant Attribute` WHERE parent = it.name AND attribute = %({p_val}_attr)s AND attribute_value = %({p_val}_val)s)"

    return conditions, conditions_it, params