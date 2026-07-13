#!/usr/bin/env python3
# Finds the least-effort way to obtain a given monster, as a tree.
#
# Recipes come from the computed synthesis table
# (data/json/computed/Syntheses.json). Every leaf of the tree is one of:
#   - a wild-catch location (from the MonsterLocation table),
#   - an egg (the monster's grade, with the best place to hunt it), or
#   - "unknown" when the game data records no way to obtain the monster.
#
# Cost model (a single "effort" unit; see the constants below). Each monster's
# cost is the minimum over the ways to get it:
#   - wild catch = 1 (a mini-boss catch costs much more; mini-bosses are hard),
#   - egg = the expected number of rolls to hatch it, 1 / p,
#   - synthesis = one step per combine plus the cost of every ingredient.
# The cheapest method becomes the node shown; the other viable ways are listed
# beneath it as alternatives with their own costs (so you can e.g. weigh hunting
# an egg against synthesising, or take a mini-boss-free synthesis route). A few
# monsters are only obtainable from an expired real-world event; they are
# treated as unobtainable (see event_monsters).
#
# Wildcard parents ("Beast Family (D)" etc.) mean "any monster of that family
# and rank". They are kept in the tree AS wildcards but also resolved to the
# cheapest concrete example, excluding anything already on the path (no cycles).
#
# The player's progress ("last location unlocked") gates the WHOLE tree: only
# currently-unlocked wild spots and egg areas are usable, so the result is the
# cheapest path achievable right now. If the monster is not obtainable yet, the
# script reports the minimum progress required and shows the cheapest path that
# becomes available at that point. Pass progress with --progress N; if omitted,
# the numbered location list is printed and the script asks for the number. Pass
# the maximum progress to ignore gating and see the globally cheapest tree.
#
# The monster name is fuzzy-matched: a non-exact query offers close matches.
#
# Usage:
#   scripts/synthesis_tree.py "Monster Name"                # prompts for progress
#   scripts/synthesis_tree.py "Monster Name" --progress 20  # gated to progress
#   scripts/synthesis_tree.py "Monster Name" --progress 35  # everything unlocked
#   scripts/synthesis_tree.py "Monster Name" --progress 20 --json
#
# Paths are resolved relative to the repo root, so it can be run from anywhere.

import sys
import json
import difflib
import argparse
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent
raw_dir = repo_root / "data" / "json" / "raw"
computed_dir = repo_root / "data" / "json" / "computed"
reference_dir = repo_root / "data" / "json" / "reference"

def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)
season_order = ["Spring", "Summer", "Fall", "Winter"]
all_seasons = set(season_order)
all_weathers = {"Sun", "Precipitation"}  # every weather type in the game
INFINITY = float("inf")  # cost of a monster that cannot (yet) be obtained
# --- Cost model -------------------------------------------------------------
# Obtainment effort is measured in a single unit: one wild catch = one egg roll.
#   wild catch (normal)   = WILD_COST
#   wild catch (mini-boss)= MINI_BOSS_COST     (mini-bosses are hard to catch)
#   one synthesis step    = SYNTH_STEP_COST    (the combine action itself)
#   egg for a monster     = expected rolls to hatch it = 1 / p  (see below)
# A monster obtainable several ways costs the minimum over those ways.
WILD_COST = 1.0
MINI_BOSS_COST = 20.0
SYNTH_STEP_COST = 1.0

# Egg cost is the expected number of rolls to get the monster, 1/p, where
#   p = P(this monster per roll) = P(grade) * P(monster | grade).
# The game does not expose P(monster | grade) and it is not published anywhere,
# so we approximate it as 1 / (number of monsters sharing that egg grade). Set
# EGG_DIVIDE_BY_POOL = False to instead treat getting the grade as getting the
# monster (much cheaper, optimistic). See the module notes for the trade-off.
EGG_DIVIDE_BY_POOL = True

# Order in which locations become available, following the game's story-chart
# progression. This drives two things:
#   - the "last location unlocked" menu the user picks from, and
#   - choosing the earliest place an egg grade can be found.
# The order and the sub-dungeons' circle/echelon are web-derived (Game8 story
# walkthrough + progameguides), and live in the reference data file so they are
# a single source of truth shared with any other consumer.
_location_progression = load_json(reference_dir / "LocationProgression.json")
_location_progression.sort(key=lambda e: e["unlock_order"])
progression_locations = [e["location"] for e in _location_progression]
# name -> 1-based unlock rank (lower unlocks earlier).
location_unlock_order = {e["location"]: e["unlock_order"] for e in _location_progression}

# Egg-grade hatch chance is defined per echelon, not per location, so the same
# grade is likeliest at whichever echelon has its highest chance. Terrestria is
# the hub and has no echelon, so it is excluded from this "best echelon" logic.
echelon_names = ("Lower", "Middle", "Upper")

# Story progress (unlock rank) at which each egg grade first appears. Eggs are
# not available at all before the earliest of these -- there are no eggs in the
# Lower echelons; grades then unlock in waves as you descend. The unlock
# locations (web-derived, mapped via the locations' Japanese names) live in the
# reference data file; here they become unlock ranks.
egg_grade_unlock = {
    e["grade"]: location_unlock_order[e["unlock_location"]]
    for e in load_json(reference_dir / "EggGradeUnlock.json")
}

event_monsters = load_json(reference_dir / "EventMonsters.json")["monsters"] # Monsters obtainable only from an expired real-world event -- treated as unobtainable (see the reference data file for the rationale).

def echelon_of(location_name):
    for e in echelon_names:
        if f"{e} Echelon" in location_name:
            return e
    return None
def load_raw(name):
    return load_json(raw_dir / name)
def load_computed(name):
    return load_json(computed_dir / name)

class Data: # Bundles every lookup the tree builder needs, keyed by monster name.
    def __init__(self):
        monsters = load_raw("Monster.json")
        ranks = {r["RankId"]: r["Name"] for r in load_raw("Rank.json")}
        families = {f["FamilyId"]: f["Name"] for f in load_raw("Family.json")}
        locations = {l["LocationId"]: l for l in load_raw("Location.json")}
        seasons = {s["SeasonId"]: s["Name"] for s in load_raw("Season.json")}
        weathers = {w["WeatherId"]: w["Name"] for w in load_raw("Weather.json")}
        egg_types = {e["EggTypeId"]: e for e in load_raw("EggType.json")}
        self.by_name = {}  # monster name -> raw monster record
        self.id_to_name = {}
        self.wildcards = {} # placeholder name -> (family_id, rank_id) for wildcard resolution
        self.family_rank_members = {} # (family_id, rank_id) -> list of concrete monster names
        self.family_members = {}  # family_id -> list of concrete names (any rank)
        for m in monsters:
            name = m["Name"]
            if not name:
                continue
            if name in event_monsters:
                m["EggTypeId"] = None
            self.by_name[name] = m
            self.id_to_name[m["MonsterId"]] = name
            if "Family (" in name:
                self.wildcards[name] = (m["FamilyId"], m["RankId"])
            else:
                key = (m["FamilyId"], m["RankId"])
                self.family_rank_members.setdefault(key, []).append(name)
                self.family_members.setdefault(m["FamilyId"], []).append(name)
        self.ranks = ranks
        self.families = families
        self.egg_types = egg_types
        self.grade_counts = {} # How many monsters share each egg grade (EggTypeId), the pool an egg of that grade draws from. Used to estimate the odds of a specific monster.
        for m in monsters:
            grade = m.get("EggTypeId")
            if grade is not None:
                self.grade_counts[grade] = self.grade_counts.get(grade, 0) + 1
        self.egg_spawn_locations = set() # Locations that actually contain egg spawns (echelon circles + Terrestria). Used to recommend where to hunt a monster's egg.
        for r in load_raw("EggLocation.json"):
            loc = locations.get(r["LocationId"])
            if loc:
                self.egg_spawn_locations.add(loc["Name"])
        self.locations_by_name = {} # Wild-catch locations grouped by monster name.
        for r in load_raw("MonsterLocation.json"):
            name = self.id_to_name.get(r["MonsterId"])
            if name is None:
                continue
            loc = locations.get(r["LocationId"], {})
            bucket = self.locations_by_name.setdefault(name, {})
            entry = bucket.setdefault(loc.get("Name", "Unknown"), {"seasons": set(), "weathers": set(), "mini_boss": False})
            if r.get("SeasonId") in seasons:
                entry["seasons"].add(seasons[r["SeasonId"]])
            if r.get("WeatherId") in weathers:
                entry["weathers"].add(weathers[r["WeatherId"]])
            if r.get("IsMiniBoss"):
                entry["mini_boss"] = True
        self.recipes = {} # Recipes grouped by result name. Each recipe is a list of parent slots; a 2-parent recipe has 2 slots, a grandparent recipe has two "pair" slots that are each synthesised from two grandparents.
        for r in load_computed("Syntheses.json"):
            result = r["Result"]
            if r.get("Parent1") and r.get("Parent2"):
                recipe = {"kind": "parents", "parents": [r["Parent1"], r["Parent2"]]}
            else:
                recipe = {"kind": "grandparents", "pairs": [[r["GrandParent1A"], r["GrandParent1B"]], [r["GrandParent2A"], r["GrandParent2B"]]]}
            self.recipes.setdefault(result, []).append(recipe)
    def is_wildcard(self, name):
        return name in self.wildcards
    def rank_letter(self, rank_id):
        return self.ranks.get(rank_id, "?")
    def family_name(self, family_id):
        return self.families.get(family_id, "?")
    def wild_locations(self, name):
        return self.locations_by_name.get(name)
    def egg_type(self, name):
        m = self.by_name.get(name)
        if not m or not m.get("EggTypeId"):
            return None
        return self.egg_types.get(m["EggTypeId"])
    def wildcard_members(self, name): # Concrete monster names matching a "Family (Rank)" placeholder.
        family_id, rank_id = self.wildcards[name]
        if self.rank_letter(rank_id) == "Any":
            return self.family_members.get(family_id, [])
        return self.family_rank_members.get((family_id, rank_id), [])

class Solver: # Computes the minimum synthesis cost of every monster, then builds trees.
    def __init__(self, data, available_locations=None):
        self.data = data
        self.available = available_locations # Locations the player has unlocked. The whole tree is limited to these; None means "ignore progress" (the global view).
        self.required = {} # Earliest story progress (unlock rank) at which each monster can be obtained at all, ignoring the current progress. Used to tell the user how far they must get. Progress-independent.
        self._compute_required() # Minimum synthesis steps, using only currently-unlocked sources.
        self.cost = {}
        self._compute_costs()

    # --- Direct (non-synthesis) obtainment costs ---------------------------
    def _available_wild_locations(self, name): # Wild locations the player can currently reach (all of them if the progress limit is disabled).
        locs = self.data.wild_locations(name)
        if not locs:
            return None
        if self.available is None:
            return locs
        reachable = {loc: e for loc, e in locs.items() if loc in self.available}
        return reachable or None
    def _wild_cost(self, name): # Cost of catching this monster in the wild now: cheap unless the only reachable spots are mini-boss encounters. None if not wild-catchable now.
        locs = self._available_wild_locations(name)
        if not locs:
            return None
        if any(not e["mini_boss"] for e in locs.values()):
            return WILD_COST
        return MINI_BOSS_COST
    def _progress_rank(self): # Highest unlock rank the player has reached (all locations if global).
        if self.available is None:
            return len(progression_locations)
        return max((location_unlock_order.get(l, 0) for l in self.available), default=0)
    def _egg_available(self, egg): # A grade is obtainable only once it has unlocked at the current progress (eggs do not exist in the Lower echelons) and there is an unlocked echelon egg-spawn area to hunt it in.
        unlock = egg_grade_unlock.get(egg["Name"], INFINITY)
        if self._progress_rank() < unlock:
            return False
        return self._recommend_egg_source(egg) is not None
    def _egg_probability(self, egg): # P(hatching this specific monster) from one egg roll at the best unlocked echelon: P(grade) times an approximate P(monster | grade).
        rec = self._recommend_egg_source(egg)
        if rec is None:
            return None
        p = rec["chance"] / 100.0
        if EGG_DIVIDE_BY_POOL:
            pool = self.data.grade_counts.get(egg["EggTypeId"], 1) or 1
            p /= pool
        return p if p > 0 else None
    def _egg_cost(self, name): # Expected rolls to hatch the monster = 1 / p. None if not egg-obtainable.
        egg = self.data.egg_type(name)
        if not egg or not self._egg_available(egg):
            return None
        p = self._egg_probability(egg)
        if not p:
            return None
        return WILD_COST / p
    def _direct_cost(self, name): # Cheapest way to get the monster without synthesis, or None.
        costs = [c for c in (self._wild_cost(name), self._egg_cost(name)) if c is not None]
        return min(costs) if costs else None

    # --- Synthesis costs ----------------------------------------------------
    def _slot_cost(self, name): # Cost to supply one parent slot, which may be a wildcard.
        if self.data.is_wildcard(name):
            members = self.data.wildcard_members(name)
            return min((self.cost.get(x, INFINITY) for x in members), default=INFINITY)
        return self.cost.get(name, INFINITY)
    def _recipe_cost(self, recipe): # Weighted cost of a recipe: one combine per synthesis plus the cost of every ingredient. A grandparent recipe has two extra combines (for the two intermediate parents).
        if recipe["kind"] == "parents":
            a, b = recipe["parents"]
            return SYNTH_STEP_COST + self._slot_cost(a) + self._slot_cost(b)
        total = 3 * SYNTH_STEP_COST
        for pair in recipe["pairs"]:
            for gp in pair:
                total += self._slot_cost(gp)
        return total
    def _recipe_min_cost(self, name):
        return min((self._recipe_cost(r) for r in self.data.recipes.get(name, [])),
                   default=INFINITY)
    def _compute_costs(self):
        d = self.data
        for name in d.by_name: # Seed with direct-catch/egg costs; everything else starts unobtainable.
            direct = self._direct_cost(name)
            self.cost[name] = direct if direct is not None else INFINITY
        # Relax until costs stop improving. A monster's cost is the min of its direct cost and its cheapest recipe, so synthesis can beat a costly egg or mini-boss catch.
        changed = True
        while changed:
            changed = False
            for name, recipes in d.recipes.items():
                best = min((self._recipe_cost(r) for r in recipes), default=INFINITY)
                if best < self.cost.get(name, INFINITY):
                    self.cost[name] = best
                    changed = True
    def _recipes_by_cost(self, name): # Achievable recipes for a monster, cheapest first.
        recipes = [
            r for r in self.data.recipes.get(name, [])
            if self._recipe_cost(r) < INFINITY
        ]
        return sorted(recipes, key=self._recipe_cost)

    # --- Earliest progress required to obtain a monster ---------------------
    def _base_required(self, name): # Earliest unlock rank at which a monster is directly obtainable.
        req = INFINITY
        locs = self.data.wild_locations(name)
        if locs:
            req = min(location_unlock_order.get(l, INFINITY) for l in locs)
        egg = self.data.egg_type(name)
        if egg:
            req = min(req, egg_grade_unlock.get(egg["Name"], INFINITY))
        return req
    def _slot_required(self, name):
        if self.data.is_wildcard(name):
            members = self.data.wildcard_members(name)
            return min((self.required.get(x, INFINITY) for x in members), default=INFINITY)
        return self.required.get(name, INFINITY)
    def _recipe_required(self, recipe): # A synthesis needs every ingredient available, so its requirement is the latest-unlocking ingredient.
        if recipe["kind"] == "parents":
            return max(self._slot_required(p) for p in recipe["parents"])
        return max(self._slot_required(gp) for pair in recipe["pairs"] for gp in pair)
    def _compute_required(self):
        d = self.data
        for name in d.by_name:
            self.required[name] = self._base_required(name)
        changed = True
        while changed:
            changed = False
            for name, recipes in d.recipes.items():
                best = min((self._recipe_required(r) for r in recipes), default=INFINITY)
                if best < self.required.get(name, INFINITY):
                    self.required[name] = best
                    changed = True
    def _wildcard_candidates(self, name, exclude=frozenset()): # Concrete monsters for a wildcard slot, cheapest first (then by name for determinism). Monsters already on the current path are excluded so a recipe never depends on something it is a step toward making.
        members = [m for m in self.data.wildcard_members(name) if m not in exclude]
        return sorted(members, key=lambda x: (self.cost.get(x, INFINITY), x))
    def _wild_node(self, name): # Wild-catch leaf (costed), or None if not wild-catchable now. If every reachable spot is a mini-boss, the node is flagged and costs more.
        locs = self._available_wild_locations(name)
        if not locs:
            return None
        mini = all(e["mini_boss"] for e in locs.values())
        out = []
        for loc_name, e in sorted(locs.items()):
            out.append({"location": loc_name, "seasons": sorted(e["seasons"], key=season_order.index), "weathers": sorted(e["weathers"]), "mini_boss": e["mini_boss"]})
        return { "monster": name, "method": "wild", "cost": MINI_BOSS_COST if mini else WILD_COST, "mini_boss": mini, "locations": out}
    def _egg_node(self, name): # Egg leaf (costed = expected rolls), or None if not egg-obtainable now.
        egg = self.data.egg_type(name)
        if not egg or not self._egg_available(egg):
            return None
        cost = self._egg_cost(name)
        if cost is None:
            return None
        return {
            "monster": name,
            "method": "egg",
            "cost": cost,
            "egg": {
                "type": egg["Name"],
                "echelon_chances": {
                    "lower": egg.get("LowerEchelonChance"),
                    "middle": egg.get("MiddleEchelonChance"),
                    "upper": egg.get("UpperEchelonChance"),
                },
                "recommended": self._recommend_egg_source(egg),
            },
        }
    def _recommend_egg_source(self, egg): # Of the unlocked egg-spawn locations, return the one where this grade is most probable, breaking ties toward the earliest-unlocked location. Terrestria has no echelon (hence no grade chance) and is skipped.
        chance_by_echelon = {"Lower": egg.get("LowerEchelonChance"), "Middle": egg.get("MiddleEchelonChance"), "Upper": egg.get("UpperEchelonChance")}
        available = self.available
        best = None # (chance, -unlock_rank, location, echelon)
        for loc in self.data.egg_spawn_locations:
            if available is not None and loc not in available:
                continue
            echelon = echelon_of(loc)
            if echelon is None:
                continue
            chance = chance_by_echelon.get(echelon)
            if chance is None:
                continue
            rank = location_unlock_order.get(loc, len(progression_locations) + 1)
            candidate = (chance, -rank, loc, echelon)
            if best is None or candidate > best:
                best = candidate
        if best is None:
            return None
        chance, neg_rank, loc, echelon = best
        return {"location": loc, "echelon": echelon, "chance": chance}
    def _unobtainable_node(self, name): # A monster that cannot be built at the current progress. If it becomes obtainable later, record the minimum progress needed.
        required = self.required.get(name, INFINITY)
        node = {"monster": name, "method": "unknown", "cost": INFINITY}
        if required == INFINITY:
            node["reason"] = "never"
        else:
            node["reason"] = "locked"
            node["required_rank"] = required
            node["required_location"] = progression_locations[required - 1]
        return node
    def build(self, name, _stack=None, augment=True):
        # Builds the cheapest-cost tree for `name`. Returns None to signal a
        # cycle (the monster is already on the path, so the caller backtracks).
        # The cheapest acquisition method becomes the node; the other viable
        # methods are attached as alternatives (so the player can choose, e.g.
        # a mini-boss-free synthesis, or hatching an egg instead of synthesising)
        # unless `augment` is false, which keeps alternative subtrees flat.
        _stack = _stack or set()
        if name in _stack:
            return None # would depend on something it is a step toward making
        wild = self._wild_node(name)
        if wild is not None and not wild["mini_boss"]:
            return wild # a normal wild catch is the cheapest thing possible
        egg = self._egg_node(name)
        direct = min([o for o in (wild, egg) if o is not None], key=lambda o: o["cost"], default=None)
        recipe_cost = self._recipe_min_cost(name)
        synth_is_primary = recipe_cost < (direct["cost"] if direct else INFINITY)
        synth = None
        if recipe_cost < INFINITY: # Build alternatives flat (augment=False) so they can't nest further.
            synth = self._synth_node(name, _stack, augment and synth_is_primary)
        options = [o for o in (wild, egg, synth) if o is not None]
        if not options:
            if self.cost.get(name, INFINITY) == INFINITY:
                return self._unobtainable_node(name)
            return None # everything cycled back to an ancestor
        options.sort(key=lambda o: o["cost"])
        primary, alternatives = options[0], options[1:]
        if augment and alternatives:
            primary["alternatives"] = [self._as_alternative(a, primary["cost"]) for a in alternatives]
        return primary
    @staticmethod
    def _as_alternative(node, primary_cost): # Tags a node as a not-chosen alternative, noting the cheaper primary.
        alt = dict(node)
        labels = {"wild": "or catch", "egg": "or hatch from egg", "synthesis": "or synthesize"}
        label = labels.get(node["method"], "or")
        if node["method"] == "wild" and node.get("mini_boss"):
            label = "or catch mini-boss"
        alt["alt_label"] = label
        alt["primary_cost"] = primary_cost
        return alt
    def _synth_node(self, name, stack, augment): # Cheapest achievable synthesis for `name`, or None if every recipe cycles back to an ancestor. Tries recipes cheapest-first.
        for recipe in self._recipes_by_cost(name):
            children = self._try_children(recipe, stack | {name}, augment)
            if children is not None:
                cost = SYNTH_STEP_COST + sum(c["cost"] for c in children)
                return {"monster": name, "method": "synthesis", "recipe_kind": recipe["kind"], "cost": cost, "children": children}
        return None
    def _try_children(self, recipe, stack, augment): # Builds the child nodes for one recipe, or returns None if any ingredient cannot be built on this path (i.e. it would form a cycle).
        if recipe["kind"] == "parents":
            out = []
            for p in recipe["parents"]:
                child = self._build_slot(p, stack, augment)
                if child is None:
                    return None
                out.append(child)
            return out
        out = []
        for pair in recipe["pairs"]:
            gp_children = []
            for gp in pair:
                child = self._build_slot(gp, stack, augment)
                if child is None:
                    return None
                gp_children.append(child)
            out.append({"monster": None, "method": "synthesis", "recipe_kind": "intermediate", "cost": SYNTH_STEP_COST + sum(x["cost"] for x in gp_children), "children": gp_children})
        return out
    def _build_slot(self, name, stack, augment): # Builds one recipe slot. A wildcard tries its candidate monsters cheapest-first until one builds without cycling. Returns None if the slot cannot be filled on this path.
        if not self.data.is_wildcard(name):
            return self.build(name, stack, augment)
        family_id, rank_id = self.data.wildcards[name]
        annotation = {"placeholder": name, "family": self.data.family_name(family_id), "rank": self.data.rank_letter(rank_id)}
        for choice in self._wildcard_candidates(name, exclude=stack):
            node = self.build(choice, stack, augment)
            if node is not None:
                node["wildcard"] = annotation # keep the wildcard visible
                return node
        return None
    def obtain_tree(self, name): # Top-level entry point: builds the tree, coercing a cycle signal into a proper "unobtainable" node.
        tree = self.build(name)
        return tree if tree is not None else self._unobtainable_node(name)

# ---------------------------------------------------------------------------
# Pretty printing
# ---------------------------------------------------------------------------
def fmt_cost(cost): # Costs are effort estimates; show whole numbers plainly, fractions to 1dp.
    if cost == INFINITY:
        return "inf"
    if abs(cost - round(cost)) < 0.05:
        return str(int(round(cost)))
    return f"{cost:.1f}"
def format_leaf_detail(node):
    if node["method"] == "wild":
        parts = []
        for loc in node["locations"]:
            extra = []
            if loc["seasons"]:
                all_yr = all_seasons.issubset(loc["seasons"])
                extra.append("All Seasons" if all_yr else "/".join(loc["seasons"]))
            if loc["weathers"]:
                any_wx = all_weathers.issubset(loc["weathers"])
                extra.append("All Weather" if any_wx else "/".join(loc["weathers"]))
            if loc["mini_boss"]:
                extra.append("mini-boss")
            suffix = f" ({', '.join(extra)})" if extra else ""
            parts.append(loc["location"] + suffix)
        tag = "wild mini-boss" if node.get("mini_boss") else "wild"
        return f"{tag}: " + "; ".join(parts)
    if node["method"] == "egg":
        egg = node["egg"]
        c = egg["echelon_chances"]
        chances = f"L{c['lower']:g}/M{c['middle']:g}/U{c['upper']:g}%"
        rec = egg.get("recommended")
        if rec:
            where = f"best: {rec['location']} ({rec['echelon']}, {rec['chance']:g}%)"
        else:
            where = "no egg location unlocked yet"
        return f"egg: {egg['type']} [{where}] ({chances} by echelon)"
    if node["method"] == "unknown":
        if node.get("reason") == "locked":
            return (
                f"not obtainable until progress {node['required_rank']}: "
                f"{node['required_location']}"
            )
        return "no known way to obtain"
    return None
def node_label(node):
    name = node["monster"]
    if name is None and node.get("recipe_kind") == "intermediate":
        label = "(intermediate synthesis)"
    elif name is None:
        label = "(unresolved)"
    else:
        label = name
    if "alt_label" in node:
        label = f"{node['alt_label']}: {label}"
    if "wildcard" in node:
        w = node["wildcard"]
        label = f"[wildcard: any {w['family']} rank {w['rank']}] -> {label or '?'}"
    detail = format_leaf_detail(node)
    if detail:
        label += f"  <{detail}>"
    elif node["method"] == "synthesis" and node.get("recipe_kind") != "intermediate":
        label += "  [synthesize]"
    cost = node.get("cost")
    if cost is not None and cost != INFINITY:
        label += f"  (cost {fmt_cost(cost)})"
    if "primary_cost" in node and cost is not None: # On an alternative, note when the chosen route is the easier one.
        if cost > node["primary_cost"]:
            label += " -- the route above is expected to be easier"
        elif cost < node["primary_cost"]:
            label += " -- but this is cheaper than the route above"
    return label
def print_tree(node, prefix="", is_last=True, is_root=True):
    if is_root:
        print(node_label(node))
    else:
        connector = "└─ " if is_last else "├─ "
        print(prefix + connector + node_label(node))
        prefix += "   " if is_last else "│  "
    entries = node.get("children", []) + node.get("alternatives", []) # Real ingredients first, then any alternative acquisition methods.
    for i, child in enumerate(entries):
        print_tree(child, prefix, i == len(entries) - 1, is_root=False)
def resolve_monster_name(query, names): # Returns the canonical monster name for `query`. On an exact (case-insensitive) hit it returns immediately; otherwise it offers a few close matches (substring hits first, then fuzzy matches) and asks the user.
    by_lower = {n.lower(): n for n in names}
    q = query.lower()
    if q in by_lower:
        return by_lower[q]
    substring = sorted((n for n in names if q in n.lower()), key=lambda n: (not n.lower().startswith(q), n.lower())) # Substring hits, with names that start with the query ranked first.
    fuzzy = [by_lower[c] for c in difflib.get_close_matches(q, by_lower, n=8, cutoff=0.5)]
    candidates = []
    for n in substring + fuzzy: # substring matches ranked first
        if n not in candidates:
            candidates.append(n)
    candidates = candidates[:8]
    if not candidates:
        sys.exit(f"No monster matching {query!r}.")
    print(f"No exact match for {query!r}. Did you mean:")
    for i, n in enumerate(candidates, start=1):
        print(f"  {i}. {n}")
    print("  0. cancel")
    while True:
        try:
            raw = input("Choose a number: ").strip()
        except EOFError:
            sys.exit(f"\nNo selection made for {query!r}.")
        if raw in ("", "0"):
            sys.exit("Cancelled.")
        try:
            choice = int(raw)
        except ValueError:
            print("Please enter a number.")
            continue
        if 1 <= choice <= len(candidates):
            return candidates[choice - 1]
        print(f"Please enter a number between 0 and {len(candidates)}.")
def parse_progress(text): # argparse type for --progress: a number, or "all" for the maximum.
    if text.strip().lower() == "all":
        return len(progression_locations)
    try:
        return int(text)
    except ValueError:
        raise argparse.ArgumentTypeError("progress must be a number or 'all'")
def prompt_for_progress(): # Print the numbered location list and ask which one the player last unlocked. Returns a 1-based index into progression_locations.
    print("How far have you progressed? Locations unlock in this order:\n")
    for i, loc in enumerate(progression_locations, start=1):
        print(f"  {i:2}. {loc}")
    print()
    while True:
        try:
            raw = input("Enter the number of the last location you've unlocked: ")
        except EOFError:
            sys.exit("\nNo progress given. Re-run with --progress N.")
        try:
            choice = int(raw)
        except ValueError:
            print("Please enter a number.")
            continue
        if 1 <= choice <= len(progression_locations):
            return choice
        print(f"Please enter a number between 1 and {len(progression_locations)}.")
def summarize(tree, solver, name): # Prints the one-line summary that follows the tree.
    method = tree["method"]
    cost = fmt_cost(tree.get("cost", INFINITY))
    if method == "wild":
        boss = " (mini-boss)" if tree.get("mini_boss") else ""
        print(f"\nBest route: catch in the wild{boss} -- effort cost {cost}.")
    elif method == "egg":
        print(f"\nBest route: hatch from an egg -- expected {cost} rolls.")
    elif method == "synthesis":
        print(f"\nBest route: synthesis -- effort cost {cost}.")
    if tree.get("alternatives"):
        print("Alternatives (with their costs) are shown in the tree above.")

def main():
    parser = argparse.ArgumentParser(description="Shortest synthesis tree for a monster.")
    parser.add_argument("monster", help="monster name (exact, case-insensitive)")
    parser.add_argument("--progress", type=parse_progress, metavar="N", help="number of the last location you've unlocked (see the list printed when this is omitted); the whole tree is limited to unlocked sources. Pass 'all' (or the maximum) to ignore progress")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of a tree")
    args = parser.parse_args()
    data = Data()
    searchable = [n for n in data.by_name if "Family (" not in n] # Resolve the requested monster, falling back to a fuzzy-match prompt. Family placeholders are not valid targets, so exclude them from search.
    name = resolve_monster_name(args.monster, searchable)
    progress = args.progress
    if progress is None:
        progress = prompt_for_progress()
        print()
    if not 1 <= progress <= len(progression_locations):
        sys.exit(f"--progress must be between 1 and {len(progression_locations)}.")
    available = set(progression_locations[:progress])
    solver = Solver(data, available_locations=available)
    tree = solver.obtain_tree(name)
    if args.json:
        print(json.dumps(tree, indent=2, ensure_ascii=False))
        return
    print(f"Progress: unlocked through {progression_locations[progress - 1]}\n")
    if tree["method"] == "unknown" and tree.get("reason") == "locked": # A locked target is not buildable yet: report the minimum progress needed and show the shortest path that becomes available at that point.
        rank = tree["required_rank"]
        loc = tree["required_location"]
        print(f"{name} is not obtainable yet.")
        print(f"Minimum progress required: reach #{rank} ({loc}).\n")
        reached = Solver(data, available_locations=set(progression_locations[:rank]))
        future = reached.obtain_tree(name)
        print(f"Shortest path once {loc} is unlocked:\n")
        print_tree(future)
        summarize(future, reached, name)
        return
    print_tree(tree)
    if tree["method"] == "unknown":
        print("\n(no known obtainment path in the data)")
    else:
        summarize(tree, solver, name)
        req = solver.required.get(name, INFINITY)
        if req != INFINITY:
            print(f"Earliest obtainable at progress #{req}: {progression_locations[req - 1]}.")
if __name__ == "__main__":
    main()
