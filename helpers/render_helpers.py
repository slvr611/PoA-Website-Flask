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

                query_dict = {"name": 1, "_id": 1}

                field_preview = attributes.get("preview", None)
                if field_preview:
                    for p in field_preview:
                        query_dict[p] = 1

                # Handle join tables (many-to-many relationships)
                if attributes.get("linkCollection") and attributes.get("linkQueryTarget"):
                    link_collection = attributes["linkCollection"]
                    link_query_target = attributes["linkQueryTarget"]
                    item_id = str(item["_id"])
                    
                    # Find all links in the join table
                    links = list(mongo.db[link_collection].find({link_query_target: item_id}))
                    
                    if links:
                        linked_objects[field] = []
                        
                        # Get the target field from the schema
                        target_field = attributes.get("queryTarget")
                        
                        if target_field:
                            # For each link, find the corresponding object
                            for link in links:
                                if target_field in link:
                                    target_id = link[target_field]
                                    
                                    # Find the target object in any of the related collections
                                    for related_collection in related_collections:
                                        try:
                                            target_obj = mongo.db[related_collection].find_one({"_id": ObjectId(target_id)}, query_dict)
                                            if target_obj:
                                                target_obj["link"] = f"/{related_collection}/item/{target_obj.get('name', target_obj['_id'])}"
                                                
                                                # Add link attributes to the target object
                                                for link_key, link_value in link.items():
                                                    if link_key not in ["_id", target_field, link_query_target]:
                                                        target_obj[link_key] = link_value
                                                
                                                # Add the link ID and link collection for reference
                                                target_obj["link_id"] = str(link["_id"])
                                                target_obj["link_collection"] = link_collection
                                                
                                                if field_preview:
                                                    preview_schema = category_data[related_collection]["schema"]
                                                    target_obj["linked_objects"] = get_linked_objects(preview_schema, target_obj, preview_items=field_preview)
                                                
                                                linked_objects[field].append(target_obj)
                                                break
                                        except:
                                            continue
                
                # Handle direct queryTargetAttribute (one-to-many relationships)
                elif attributes.get("queryTargetAttribute"):
                    query_target = attributes["queryTargetAttribute"]
                    item_id = str(item["_id"])

                    related_items = []
                    for related_collection in related_collections:
                        items = mongo.db[related_collection].find({query_target: item_id}, query_dict)
                        if attributes.get("sort_by"):
                            sort_by = attributes["sort_by"]
                            if isinstance(sort_by, list):
                                sort_by_tuples = []
                                for sort_field in sort_by:
                                    sort_by_tuples.append((sort_field, ASCENDING))
                                items.sort(sort_by_tuples)
                            else:
                                items.sort(sort_by, ASCENDING)
                        
                        related_items += list(items)

                    if related_items:
                        linked_objects[field] = []
                        for obj in related_items:
                            obj["link"] = f"/{related_collection}/item/{obj.get('name', obj['_id'])}"

                            if field_preview:
                                preview_schema = category_data[related_collection]["schema"]
                                obj["linked_objects"] = get_linked_objects(preview_schema, obj, preview_items=field_preview)

                            linked_objects[field].append(obj)
                
                # Handle direct object references (one-to-one relationships)
                else:
                    if field in item:
                        object_id_to_find = item[field]
                        try:
                            object_id_to_find = ObjectId(object_id_to_find)
                        except:
                            continue

                        for related_collection in related_collections:
                            linked_object = mongo.db[related_collection].find_one({"_id": object_id_to_find}, query_dict)
                            if linked_object:
                                linked_object["link"] = f"/{related_collection}/item/{linked_object.get('name', linked_object['_id'])}"

                                if field_preview:
                                    preview_schema = category_data[related_collection]["schema"]
                                    linked_object["linked_objects"] = get_linked_objects(preview_schema, linked_object, preview_items=field_preview)

                                linked_objects[field] = linked_object
                                break
    
    return linked_objects
