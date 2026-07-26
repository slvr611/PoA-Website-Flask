"""Builds a pre-categorized view model for the generic data-item page
(templates/dataItem.html), mirroring the field-by-field branching that used
to live entirely in that template's Jinja loop. Categorizing fields here
keeps the template a straightforward render of a handful of buckets (kv rows,
capped-number bars, chip groups, note cards, and assorted tables) instead of
one 400-line per-field conditional.

Every branch below corresponds 1:1 to a branch that existed in the old
dataItem.html loop; the gating condition (hidden / hideIfNone / resource
dup-suppression / view_access_level / visibility tier) is evaluated exactly
as before, per field, before a field is placed into any bucket.
"""


def _field_excluded(field, properties, schema, item, view_access_level):
    """Fields that never appear at all, regardless of visibility tier (as opposed
    to fields that are merely tier-locked — see _required_tier — which still show
    a "Requires Tier N visibility" placeholder instead of being skipped)."""
    if not properties or properties.get("hidden"):
        return True
    hide_if_none = properties.get("hideIfNone")
    if hide_if_none is not None:
        v = item.get(hide_if_none)
        if v is None or v == "" or not v:
            return True
    if field in ("resource_storage", "resource_capacity") and "resource_capacity" in schema.get("properties", {}):
        return True
    if view_access_level < properties.get("view_access_level", 0):
        return True
    return False


def _required_tier(field, field_tiers, visibility_level, visibility_bypassed):
    """Returns the visibility tier still needed to view `field`, or None if it's
    already visible (no field_tiers registry, bypassed, or tier already met)."""
    if field_tiers is None or visibility_level is None or visibility_bypassed:
        return None
    tier = field_tiers.get(field, 0)
    return tier if visibility_level < tier else None


def _resolve_resource_name(key, json_data):
    for coll in ("general_resources", "unique_resources", "luxury_resources"):
        for r in json_data.get(coll, []):
            if r.get("key") == key:
                return r.get("name", key)
    return key


def build_item_view(schema, item, breakdowns, linked_objects, json_data,
                     field_tiers, visibility_level, visibility_bypassed,
                     view_access_level, find_dict_in_list, scope_definitions):
    """Returns a dict with the render-ready buckets consumed by dataItem.html."""
    properties_map = schema.get("properties", {})
    breakdowns = breakdowns or {}

    # Optional schema-driven grouping: a field with "view_group": "<key>" is
    # pulled out of the default kv_rows/bars/num_rows buckets into its own
    # named card, as long as "<key>" is declared in the schema's top-level
    # "view_groups" dict (e.g. {"attributes": {"label": "Attributes"}}).
    # Card order/labels follow declaration order in view_groups; groups with
    # no visible members are omitted. Unrecognized/absent view_group values
    # fall through to the default buckets unchanged.
    group_defs = schema.get("view_groups", {})
    grouped_items = {key: [] for key in group_defs}

    laws = [item.get(f) for f in schema.get("laws", []) if item.get(f)]
    view = {
        "title": item.get("name") or str(item.get("_id", "")),
        "kicker": " · ".join(str(l) for l in laws),
        "image": None,
        "kv_rows": [],
        "bars": [],
        "num_rows": [],
        "chip_groups": [],
        "notes": [],
        "tables": [],
        "groups": [],
    }

    def place(bucket, kind, item_dict, view_group):
        """Append `item_dict` (tagged with `kind`) to its schema-declared view_group if any, else `bucket`."""
        item_dict = dict(item_dict, kind=kind)
        if view_group and view_group in grouped_items:
            grouped_items[view_group].append(item_dict)
        else:
            view[bucket].append(item_dict)

    for field, properties in properties_map.items():
        if field == "name":
            continue
        if _field_excluded(field, properties, schema, item, view_access_level):
            continue

        label = properties.get("label", field)
        bson_type = properties.get("bsonType")

        view_group = properties.get("view_group")

        required_tier = _required_tier(field, field_tiers, visibility_level, visibility_bypassed)
        if required_tier is not None:
            if bson_type in ("string", "enum", "date") and properties.get("long_text"):
                view["notes"].append({"title": label, "text": None, "locked_tier": required_tier})
            elif bson_type in ("string", "enum", "date") and properties.get("image"):
                pass
            elif bson_type == "number" and isinstance(properties.get("max"), str):
                place("bars", "locked", {"label": label, "tier": required_tier}, view_group)
            elif bson_type == "object":
                view["tables"].append({"title": label, "headers": None, "rows": [], "locked_tier": required_tier})
            elif bson_type == "array":
                item_bson_type = (properties.get("items") or {}).get("bsonType")
                if item_bson_type == "string":
                    view["chip_groups"].append({"title": label, "chips": [], "locked_tier": required_tier})
                else:
                    view["tables"].append({"title": label, "headers": None, "rows": [], "locked_tier": required_tier})
            else:
                place("kv_rows", "locked", {"label": label, "tier": required_tier}, view_group)
            continue

        if bson_type in ("string", "enum", "date"):
            if properties.get("image"):
                view["image"] = {"label": label, "url": item.get(field)}
                continue
            if properties.get("long_text"):
                text = item.get(field)
                if text:
                    view["notes"].append({"title": label, "text": text})
                continue
            place("kv_rows", "kv", {"label": label, "value": item.get(field) or "None"}, view_group)

        elif bson_type == "json_district_enum":
            district_data = json_data.get(properties.get("json_data"), {})
            v = item.get(field)
            if v and v in district_data:
                place("kv_rows", "kv", {"label": label, "value": district_data[v]["display_name"]}, view_group)
            else:
                place("kv_rows", "kv", {"label": label, "value": "None"}, view_group)

        elif bson_type == "json_resource_enum":
            v = item.get(field)
            if v is not None:
                resource = find_dict_in_list(json_data.get("general_resources", []), "key", v) \
                    or find_dict_in_list(json_data.get("unique_resources", []), "key", v)
                place("kv_rows", "kv", {"label": label, "value": resource["name"] if resource else f"Unknown Resource ({v})"}, view_group)
            else:
                place("kv_rows", "kv", {"label": label, "value": "None"}, view_group)

        elif bson_type == "number":
            cap_field = properties.get("max") if isinstance(properties.get("max"), str) else None
            cap_literal = properties.get("max") if isinstance(properties.get("max"), (int, float)) else None
            bd = breakdowns.get(field, [])
            value = item.get(field, 0)
            is_pct = properties.get("format") == "percentage"

            if cap_field:
                cap_value = item.get(cap_field, 0)
                cap_bd = breakdowns.get(cap_field, [])
                cap_label = properties_map.get(cap_field, {}).get("label", cap_field)
                bar_kind = "kv_capped" if properties.get("bar_style") == "kv" else "bar"
                place("bars", bar_kind, {
                    "label": label,
                    "value": value,
                    "cap": cap_value,
                    "pct": min(100, (value / cap_value * 100)) if cap_value else 0,
                    "breakdown": bd,
                    "cap_breakdown": cap_bd,
                    "cap_label": cap_label,
                    "is_percentage": is_pct,
                }, view_group)
            else:
                if is_pct:
                    value_str = f"{int(value * 100)}%"
                else:
                    value_str = str(value)
                    if cap_literal is not None:
                        value_str += f" / {cap_literal}"
                place("num_rows", "num", {
                    "label": label,
                    "value": value_str,
                    "breakdown": bd if properties.get("show_breakdown") else None,
                }, view_group)

        elif bson_type == "boolean":
            place("kv_rows", "kv", {
                "label": label,
                "value": "✓" if item.get(field) else "✗",
                "good": bool(item.get(field)),
                "is_bool": True,
            }, view_group)

        elif bson_type == "linked_object":
            ref = linked_objects.get(field)
            if ref:
                place("kv_rows", "kv", {"label": label, "value": ref.get("name", ""), "link": ref.get("link", "")}, view_group)
            elif item.get(field):
                place("kv_rows", "kv", {"label": label, "value": item[field]}, view_group)
            else:
                place("kv_rows", "kv", {"label": label, "value": properties.get("noneResult", "None")}, view_group)

        elif bson_type == "object":
            if field == "resource_production" and "resource_capacity" in properties_map:
                res_prod = item.get("resource_production", {})
                res_store = item.get("resource_storage", {})
                res_cap = item.get("resource_capacity", {})
                prod_bd = breakdowns.get("resource_production", {})
                cap_bd = breakdowns.get("resource_capacity", {})
                rows = []
                for resource in (json_data.get("general_resources", []) + json_data.get("unique_resources", [])
                                  + json_data.get("luxury_resources", [])):
                    rkey = resource["key"]
                    cap = res_cap.get(rkey, 0)
                    prod = res_prod.get(rkey, 0)
                    store = res_store.get(rkey, 0)
                    if cap == 0 and prod == 0 and store == 0:
                        continue
                    rows.append({
                        "cells": [
                            {"text": resource["name"]},
                            {"text": prod, "tooltip_title": f"{resource['name']} Production", "breakdown": prod_bd.get(rkey, [])},
                            {"text": store, "cap_text": cap, "cap_tooltip_title": f"{resource['name']} Capacity", "cap_breakdown": cap_bd.get(rkey, [])},
                        ]
                    })
                view["tables"].append({"title": label, "headers": ["Resource", "Production", "Stockpile / Capacity"], "rows": rows})
            elif (properties.get("resource_storage") or field in
                  ("resource_storage", "resource_capacity", "resource_production", "resource_consumption", "resource_excess")):
                res_cap = item.get("resource_capacity", {})
                rows = []
                for rkey, rvalue in sorted(item.get(field, {}).items()):
                    if res_cap and res_cap.get(rkey, 1) == 0:
                        continue
                    rows.append({"cells": [{"text": rkey}, {"text": rvalue}]})
                view["tables"].append({"title": label, "headers": None, "rows": rows})
            else:
                rows = [{"cells": [{"text": k}, {"text": v}]} for k, v in sorted(item.get(field, {}).items())]
                view["tables"].append({"title": label, "headers": None, "rows": rows})

        elif bson_type == "array":
            items_schema = properties.get("items", {})
            item_bson_type = items_schema.get("bsonType", "None")
            arr = item.get(field) or []

            if item_bson_type == "string":
                if arr:
                    view["chip_groups"].append({"title": label, "chips": [str(v) for v in arr]})

            elif item_bson_type == "linked_object":
                refs = linked_objects.get(field)
                if refs:
                    preview_fields = properties.get("preview", [])
                    preview_formats = properties.get("preview_formats", {})
                    extra_link_fields = []
                    if refs[0] and refs[0].get("linked_objects"):
                        extra_link_fields = list(refs[0]["linked_objects"].keys())
                    headers = ["Name"] + [f.title() for f in extra_link_fields] + \
                              [properties_map.get(pf, {}).get("label", pf.title()) for pf in preview_fields]
                    rows = []
                    for ref in refs:
                        cells = [{"text": ref.get("name", ""), "link": ref.get("link", "")}]
                        for lf in extra_link_fields:
                            sub_ref = (ref.get("linked_objects") or {}).get(lf)
                            if sub_ref:
                                cells.append({"text": sub_ref.get("name", ""), "link": sub_ref.get("link", "")})
                            else:
                                cells.append({"text": "None"})
                        for pf in preview_fields:
                            if preview_formats.get(pf, "string") == "boolean":
                                cells.append({"text": "✓" if ref.get(pf, False) else "✗", "is_bool": True, "good": bool(ref.get(pf, False))})
                            else:
                                cells.append({"text": ref.get(pf, "None")})
                        rows.append({"cells": cells})
                    view["tables"].append({"title": label, "headers": headers, "rows": rows})
                else:
                    view["tables"].append({"title": label, "headers": None, "rows": [], "empty": True})

            elif item_bson_type == "object":
                if field == "modifiers":
                    rows = []
                    for sub in arr:
                        mod_type_key = sub.get("modifier_type", sub.get("key", sub.get("field", "")))
                        type_def = json_data.get("modifier_types", {}).get(mod_type_key, {})
                        type_name = type_def.get("name", mod_type_key)
                        field_template = type_def.get("field_template", mod_type_key)
                        resolved_field = (field_template
                                          .replace("{attribute}", sub.get("attribute", ""))
                                          .replace("{resource}", sub.get("resource", ""))
                                          .replace("{resource_from}", sub.get("resource_from", ""))
                                          .replace("{resource_to}", sub.get("resource_to", ""))
                                          .replace("{job}", sub.get("job", "")))
                        scope_key = sub.get("scope", "")
                        scope_name = scope_definitions.get(scope_key, {}).get("name", scope_key) if scope_key else ""
                        name_cell_text = type_name
                        if type_def.get("extra_fields"):
                            name_cell_text = f"{type_name} ({resolved_field})"
                        rows.append({"cells": [
                            {"text": name_cell_text},
                            {"text": scope_name},
                            {"text": sub.get("value", 0)},
                            {"text": sub.get("duration", -1)},
                            {"text": sub.get("source", "")},
                        ]})
                    view["tables"].append({"title": label, "headers": ["Modifier", "Scope", "Value", "Duration", "Source"], "rows": rows})

                elif field == "progress_quests":
                    rows = []
                    for sub in arr:
                        slot_key = sub.get("slot", "no_slot")
                        slot_name = (json_data.get("slot_types", {}).get(slot_key) or {"name": slot_key})["name"]
                        rows.append({"cells": [
                            {"text": sub.get("quest_name")},
                            {"text": slot_name},
                            {"text": sub.get("bonus_progress_per_tick")},
                            {"text": sub.get("total_progress_per_tick")},
                            {"text": f"{sub.get('current_progress')} / {sub.get('required_progress')}"},
                            {"text": sub.get("link", ""), "discord_link": True},
                        ]})
                    view["tables"].append({"title": label, "headers": ["Quest Name", "Slot", "Bonus Progress Per Tick", "Total Progress Per Tick", "Current Progress", "Link"], "rows": rows})

                else:
                    sub_props = items_schema.get("properties", {})
                    if sub_props.get("name"):
                        preview_fields = [pf for pf in sub_props if pf != "name"]
                        headers = ["Name"] + [sub_props[pf].get("label", pf) for pf in preview_fields]
                        rows = [{"cells": [{"text": sub.get("name")}] + [{"text": sub.get(pf)} for pf in preview_fields]} for sub in arr]
                    else:
                        preview_fields = list(sub_props.keys())
                        headers = [sub_props[pf].get("label", pf) for pf in preview_fields]
                        rows = [{"cells": [{"text": sub.get(pf)} for pf in preview_fields]} for sub in arr]
                    view["tables"].append({"title": label, "headers": headers, "rows": rows})

            elif item_bson_type in ("json_district_enum", "json_unit_enum", "db_unit_enum", "json_resource_enum"):
                length = len(arr)
                max_length = properties.get("max_length")
                if isinstance(max_length, str):
                    max_length = item.get(max_length, 0)
                if max_length and length < max_length:
                    length = max_length

                entries = []
                if item_bson_type == "json_district_enum":
                    district_data = json_data.get(items_schema.get("json_data"), {})
                    for i in range(length):
                        v = arr[i] if i < len(arr) else None
                        entries.append(district_data[v]["display_name"] if v and v in district_data else "None")
                elif item_bson_type == "json_unit_enum":
                    combined = {}
                    for file_name in items_schema.get("json_data", []):
                        combined.update(json_data.get(file_name, {}))
                    for i in range(length):
                        v = arr[i] if i < len(arr) else None
                        entries.append(combined[v]["display_name"] if v and v in combined else "None")
                elif item_bson_type == "db_unit_enum":
                    for i in range(length):
                        v = arr[i] if i < len(arr) else None
                        entries.append(v if v else "None")
                else:  # json_resource_enum
                    for i in range(length):
                        v = arr[i] if i < len(arr) else None
                        entries.append(_resolve_resource_name(v, json_data) if v else "None")

                view["tables"].append({"title": label, "headers": None, "rows": [{"cells": [{"text": e}]} for e in entries], "is_list": True})

    view["groups"] = [
        {"key": key, "label": group_defs[key].get("label", key), "fields": items}
        for key, items in grouped_items.items() if items
    ]

    return view
