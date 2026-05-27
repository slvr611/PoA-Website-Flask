from app_core import category_data, json_data


def synergy_matches(node, requirement):
    if not node:
        return False
    if isinstance(requirement, list):
        return "any" in requirement or node in requirement
    return requirement == "any" or node == requirement


def get_synergies(details):
    if "synergies" in details:
        return details["synergies"]
    req = details.get("synergy_requirement", "")
    if req:
        return [{"requirement": req}]
    return []


def district_synergy_active(district_type, district_node):
    return False  # legacy JSON districts removed


def imperial_synergy_active(district_type, district_node):
    details = json_data["nation_imperial_districts"].get(district_type, {})
    return any(synergy_matches(district_node, syn.get("requirement", "")) for syn in get_synergies(details))


def main():
    db = category_data["nations"]["database"]
    nations = list(db.find().sort("name", 1))

    print("Nation name, District 1, Synergy (true/false), District 2, Synergy, District 3, Synergy, ...")
    for nation in nations:
        row = [nation.get("name", "Unknown")]
        for district in nation.get("districts", []):
            if isinstance(district, dict):
                district_type = district.get("type", "")
                district_node = district.get("node", "")
            else:
                district_type = district
                district_node = ""
            synergy_active = district_synergy_active(district_type, district_node)
            row.append(district_type)
            row.append(str(synergy_active).lower())

        if nation.get("empire", False):
            imperial_district = nation.get("imperial_district", {})
            imperial_type = imperial_district.get("type", "")
            imperial_node = imperial_district.get("node", "")
            if imperial_type:
                synergy_active = imperial_synergy_active(imperial_type, imperial_node)
                row.append(f"Imperial {imperial_type}")
                row.append(str(synergy_active).lower())

        print(", ".join(row))


if __name__ == "__main__":
    main()
