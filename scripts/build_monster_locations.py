#!/usr/bin/env python3
# Builds the computed monster-location mapping from the raw JSON tables.
#
# Joins MonsterLocation (the MonsterId <-> LocationId link table) against
# Monster, Location, Season and Weather to produce, for each monster, the list
# of maps/areas where it is found along with the season/weather conditions.
#
# Outputs:
#   data/json/computed/MonsterLocations.json  (nested: monster -> [locations])
#   data/csv/computed/MonsterLocations.csv    (long: one row per monster-location)
#
# Run from anywhere; paths are resolved relative to the repo root.

import csv
import json
import collections
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent
raw_dir = repo_root / "data" / "json" / "raw"
json_out = repo_root / "data" / "json" / "computed" / "MonsterLocations.json"
csv_out = repo_root / "data" / "csv" / "computed" / "MonsterLocations.csv"

# Seasons are ordered by the in-game calendar rather than by id.
season_order = ["Spring", "Summer", "Fall", "Winter"]

# Delimiter used inside a single CSV cell that holds multiple values.
multi_value_sep = "|"


def load(name):
    with open(raw_dir / name, encoding="utf-8") as f:
        return json.load(f)


def build():
    monsters = {m["MonsterId"]: m for m in load("Monster.json")}
    locations = {l["LocationId"]: l for l in load("Location.json")}
    seasons = {s["SeasonId"]: s["Name"] for s in load("Season.json")}
    weathers = {w["WeatherId"]: w["Name"] for w in load("Weather.json")}

    # monster_id -> location_id -> aggregated conditions across raw rows
    def new_entry():
        return {"seasons": set(), "weathers": set(), "mini_boss": False}

    by_monster = collections.defaultdict(lambda: collections.defaultdict(new_entry))

    for row in load("MonsterLocation.json"):
        entry = by_monster[row["MonsterId"]][row["LocationId"]]
        if row.get("SeasonId") in seasons:
            entry["seasons"].add(seasons[row["SeasonId"]])
        if row.get("WeatherId") in weathers:
            entry["weathers"].add(weathers[row["WeatherId"]])
        if row.get("IsMiniBoss"):
            entry["mini_boss"] = True

    def season_key(name):
        return season_order.index(name)

    result = []
    ordered_ids = sorted(
        by_monster,
        key=lambda i: (monsters[i]["Name"] or "") if i in monsters else "",
    )
    for monster_id in ordered_ids:
        monster = monsters.get(monster_id, {})
        locs = []
        for location_id, entry in by_monster[monster_id].items():
            location = locations.get(location_id, {})
            locs.append(
                {
                    "location": location.get("Name", f"Unknown ({location_id})"),
                    "location_identifier": location.get("Identifier"),
                    "seasons": sorted(entry["seasons"], key=season_key),
                    "weathers": sorted(entry["weathers"]),
                    "mini_boss": entry["mini_boss"],
                }
            )
        locs.sort(key=lambda x: x["location"])
        result.append(
            {
                "monster_id": monster_id,
                "monster": monster.get("Name"),
                "monster_identifier": monster.get("Identifier"),
                "locations": locs,
            }
        )
    return result


def write_json(result):
    with open(json_out, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
        f.write("\n")


def write_csv(result):
    with open(csv_out, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "Monster",
                "MonsterIdentifier",
                "Location",
                "LocationIdentifier",
                "Seasons",
                "Weathers",
                "MiniBoss",
            ]
        )
        for monster in result:
            for loc in monster["locations"]:
                writer.writerow(
                    [
                        monster["monster"],
                        monster["monster_identifier"],
                        loc["location"],
                        loc["location_identifier"],
                        multi_value_sep.join(loc["seasons"]),
                        multi_value_sep.join(loc["weathers"]),
                        loc["mini_boss"],
                    ]
                )


def main():
    result = build()
    write_json(result)
    write_csv(result)
    pairs = sum(len(m["locations"]) for m in result)
    print(f"monsters with locations: {len(result)}")
    print(f"monster-location pairs:  {pairs}")
    print(f"wrote {json_out.relative_to(repo_root)}")
    print(f"wrote {csv_out.relative_to(repo_root)}")


if __name__ == "__main__":
    main()
