from bson import ObjectId


class ScopeResolver:
    """
    Resolves modifier scopes declared in scope_definitions.json.

    forward: given a source entity, return the target entities for a given scope.
    reverse: given a target entity, return all (source_entity, scope_key) pairs
             whose modifiers should flow into that target.
    """

    def __init__(self, scope_definitions: dict, db):
        self._defs = scope_definitions
        self._db = db

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def resolve_targets(self, source_entity: dict, scope_key: str) -> list:
        """Return list of target entities that scope_key points to from source_entity."""
        scope = self._defs.get(scope_key)
        if not scope:
            return []
        return self._execute_resolution(source_entity, scope["resolution"])

    def resolve_sources(self, target_entity: dict, target_type: str) -> list:
        """
        Return list of (source_entity, scope_key) pairs whose modifiers should
        flow into target_entity via cross-entity scopes.

        Only returns scopes whose target_type matches the provided target_type
        and whose resolution points at this target.
        """
        results = []
        for scope_key, scope in self._defs.items():
            if scope.get("target_type") != target_type:
                continue
            if scope["resolution"]["type"] == "direct":
                continue
            sources = self._reverse_resolve(target_entity, scope, scope_key)
            for src in sources:
                results.append((src, scope_key))
        return results

    # ------------------------------------------------------------------
    # Internal resolution helpers
    # ------------------------------------------------------------------

    def _execute_resolution(self, entity: dict, resolution: dict) -> list:
        rtype = resolution["type"]
        if rtype == "direct":
            return [entity]
        elif rtype == "forward_link":
            return self._forward_link(entity, resolution)
        elif rtype == "reverse_link":
            return self._reverse_link(entity, resolution)
        elif rtype == "chain":
            return self._chain(entity, resolution["steps"])
        return []

    def _forward_link(self, entity: dict, resolution: dict) -> list:
        """Follow a field on the entity to find the target document."""
        link_field = resolution["link_field"]
        collection = resolution["collection"]
        raw_id = entity.get(link_field, "")
        if not raw_id:
            return []
        try:
            target = self._db[collection].find_one({"_id": ObjectId(str(raw_id))})
        except Exception:
            return []
        return [target] if target else []

    def _reverse_link(self, entity: dict, resolution: dict) -> list:
        """Find all documents in collection where link_field == entity._id."""
        link_field = resolution["link_field"]
        collection = resolution["collection"]
        entity_id = str(entity.get("_id", ""))
        if not entity_id:
            return []
        return list(self._db[collection].find({link_field: entity_id}))

    def _chain(self, entity: dict, steps: list) -> list:
        """Execute a sequence of resolution steps, threading results through."""
        current = [entity]
        for step in steps:
            next_entities = []
            for ent in current:
                next_entities.extend(self._execute_resolution(ent, step))
            current = next_entities
        return current

    def _reverse_resolve(self, target_entity: dict, scope: dict, scope_key: str) -> list:
        """
        Given a target, find source entities that would forward-resolve to it.
        Supports simple forward_link and chain scopes only (reverse of reverse_link
        is not needed for the current use cases).
        """
        resolution = scope["resolution"]
        rtype = resolution["type"]

        if rtype == "forward_link":
            collection = resolution["collection"]
            link_field = resolution["link_field"]
            target_id = str(target_entity.get("_id", ""))
            return list(self._db[collection].find({link_field: target_id}))

        if rtype == "reverse_link":
            link_field = resolution["link_field"]
            collection = resolution["collection"]
            raw_id = target_entity.get(link_field, "")
            if not raw_id:
                return []
            try:
                src = self._db[collection].find_one({"_id": ObjectId(str(raw_id))})
            except Exception:
                return []
            return [src] if src else []

        return []
