from app_core import category_data, mongo
from bson import ObjectId
from pymongo import ASCENDING

def get_linked_objects(schema, item, preview_items=None):
    properties = schema.get("properties", {})
    linked_objects = {}

    for field, attributes in properties.items():
        if preview_items is None or field in preview_items:
            if attributes.get("collections"):
                related_collections = attributes["collections"]

                if attributes.get("queryTargetAttribute"):
                    query_target = attributes["queryTargetAttribute"]
                    item_id = str(item["_id"])

                    query_dict = {"name": 1, "_id": 1}
                    field_preview = attributes.get("preview", None)
                    if field_preview:
                        for p in field_preview:
                            query_dict[p] = 1

                    related_items = []
                    for related_collection in related_collections:
                        related_items += list(mongo.db[related_collection].find({query_target: item_id}, query_dict).sort("name", ASCENDING))

                    if related_items:
                        linked_objects[field] = []
                        for obj in related_items:
                            object_to_add = {"name": obj.get("name", obj["_id"]), "link": f"/{related_collection}/item/{obj.get('name', obj['_id'])}"}

                            if field_preview:
                                preview_schema = category_data[related_collection]["schema"]
                                object_to_add["linked_objects"] = get_linked_objects(preview_schema, obj, preview_items=field_preview)

                            linked_objects[field].append(object_to_add)
                else:
                    if field in item:
                        object_id_to_find = item[field]
                        try:
                            object_id_to_find = ObjectId(object_id_to_find)
                        except:
                            continue

                        for related_collection in related_collections:
                            linked_object = mongo.db[related_collection].find_one({"_id": object_id_to_find})
                            if linked_object:
                                linked_object["link"] = f"/{related_collection}/item/{linked_object.get('name', linked_object['_id'])}"
                                linked_objects[field] = linked_object
                                break
    
    return linked_objects
