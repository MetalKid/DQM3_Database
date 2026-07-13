# Reference data

Authored / externally-sourced tables for *Dragon Quest Monsters: The Dark Prince*
that are **not** part of the game's exported data (the tables under `data/json/raw/`).
They capture knowledge gathered from community guides and cross-checked against the
raw data, and are the single source of truth loaded by `scripts/synthesis_tree.py`.

Unlike `data/json/computed/`, nothing here is derived from `raw/` — these facts
cannot be recomputed from the shipped tables, which is why they are kept as data.

---

## `LocationProgression.json`

Every location (35 total, matching `raw/Location.json`), in the order it unlocks
during the story, with which Circle and echelon it belongs to.

Each entry:

| Field          | Meaning                                                                 |
| -------------- | ----------------------------------------------------------------------- |
| `location`     | Location name (matches `raw/Location.json`).                            |
| `unlock_order` | 1-based story-progression rank (1 = available first).                   |
| `circle`       | Which of the five Circles (Conquest / Temper / Indulgence / Caprice / Fortitude), or `null` for Terrestria, the Cradle of Corruption, and post-game areas. |
| `echelon`      | `Lower` / `Middle` / `Upper` (the Cradle's Lower/Middle/Upper *Level* is recorded the same way), or `null` where it does not apply. |

**Sources**

- Unlock order: [Game8 — Complete Story Walkthrough](https://game8.co/games/DQM-Dark-Prince/archives/436683)
  (the "Chart" progression; each echelon wave opens the five Circles in a fixed order).
- Circle + echelon of the 14 optional sub-dungeons (Gleaming Grotto, Sepulchre of
  Flames, Fort Nox, etc.): [Pro Game Guides — Local Monsters guide](https://progameguides.com/dragon-quest-monsters/local-monsters-guide-dragon-quest-monsters-the-dark-prince/),
  cross-checked against the monster overlap in `raw/MonsterLocation.json`.
- Circle grouping cross-checked with the [Dragon Quest Wiki](https://dragon-quest.org/wiki/Dragon_Quest_Monsters:_The_Dark_Prince).

## `EggGradeUnlock.json`

The story point at which each egg grade first becomes available. Eggs do not exist
in the Lower echelons at all; the grades then unlock in waves.

Each entry: `grade` (matches `raw/EggType.json`) and `unlock_location` (matches a
`location` in `LocationProgression.json`; the script turns it into a rank).

| Grade    | Unlocks at                          |
| -------- | ----------------------------------- |
| White    | Circle of Fortitude – Middle Echelon |
| Silver   | Circle of Fortitude – Middle Echelon |
| Gold     | Cradle of Corruption – Middle Level  |
| Rainbow  | Circle of Caprice – Upper Echelon    |
| Rainbow – Post Game Only | The Whispwoods           |

**Sources**

- [Samurai Gamers — Egg Guide](https://samurai-gamers.com/dragon-quest-monsters-the-dark-prince/egg-guide/),
  which lists the unlock points using the Japanese location names.
- Those names were mapped to the English locations via the `JapaneseName` field in
  `raw/Location.json`: 鉄鋼砦 (Steel Fortress) = **Fortitude**, 災厄の魔宮 (Palace of
  Calamity) = **Cradle of Corruption**, 流神殿 (Flowing Temple) = **Caprice**;
  初級 / 中級 / 上級 = Lower / **Middle** (Intermediate) / **Upper** (Advanced).

## `EventMonsters.json`

Monsters obtainable only through a real-world distribution event that has ended.
Their eggs can no longer be found, so the tool treats them as unobtainable and
removes them from the egg pools (so they neither offer an egg route nor dilute the
odds of other monsters in the same grade).

Schema: a `note` string plus a `monsters` array of names (matching `raw/Monster.json`).

**Source:** [Game8 — event / distribution monsters](https://game8.co/games/DQM-Dark-Prince/archives/436082).
These are event-exclusive monsters no longer distributed. If any later become
obtainable by other means, remove them here.
