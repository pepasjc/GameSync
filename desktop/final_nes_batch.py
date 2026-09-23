#!/usr/bin/env python3
"""
Final comprehensive NES alias batch - accept all high-quality matches.
"""
import json
from pathlib import Path
from rom_normalizer import load_no_intro_dat
import re

def _to_base_slug(name: str) -> str:
    name = re.sub(r'\s*\([^)]*\)$', '', name)
    name = re.sub(r'\s*\([^)]*\)', '', name)
    name = re.sub(r'[^\w\s]', '', name)
    name = re.sub(r'\s+', ' ', name).lower()
    return name.strip()

# Manually verified and researched matches
manual_batch = {
    # From batch research - clearly matching candidates
    "Aim! Top Pro - Dream in Green (Japan)": "Mezase! Top Pro - Green ni Kakeru Yume (Japan)",
    "Burn!! Judo Warriors (Japan)": "Moero!! Juudou Warriors (Japan)",
    "Chef Cookie in Gourmet World (Japan)": "Wanpaku Kokkun no Gourmet World (Japan)",
    "Columbus - Golden Dawn (Japan)": "Columbus - Ougon no Yoake (Japan)",
    "Dash Rascal (Japan)": "Dash Yarou (Japan) (En)",
    "Deep Dungeon III - The Journey to Become a Hero (Japan)": "Deep Dungeon III - Yuushi e no Tabi (Japan)",
    "Detective Sanma (Japan)": "Sanma no Meitantei (Japan)",
    "Doki! Doki! Amusement Park (Japan)": "Doki! Doki! Yuuenchi - Crazy Land Daisakusen (Japan) (En)",
    "Elementary Math Grade 1 - Improvement Game (Japan)": "Sansuu 1 Nen - Keisan Game (Japan)",
    "Elementary Math Grade 2 - Improvement Game (Japan)": "Sansuu 2 Nen - Keisan Game (Japan)",
    "Elementary Math Grade 3 - Calculation Game (Japan)": "Sansuu 3 Nen - Keisan Game (Japan)",
    "Elementary Math Grade 4 - Improvement Game (Japan)": "Sansuu 4 Nen - Keisan Game (Japan)",
    "Esper Corps (Japan)": "Esper Bouken Tai (Japan)",
    "Go for it Goemon! (Japan)": "Ganbare Goemon! (Japan)",
    "Hudson's Adventure Island IV (Japan)": "Hudson's Adventure Island IV (Japan)",
    "Ice Ice! Hockey Challenge (Japan)": "Stick Hunter - Exciting Ice Hockey (Japan)",
    "Jubei Quest (Japan)": "Castle Quest (Japan)",
    "Kero Kero Keroppi's Big Adventure 1 (Japan)": "Keroppi and Sanrio Friends - Ehon no Kuni no Daibouken (Japan)",
    "Kid Niki 3 (Japan)": "Kid Niki 3 - Arashi Yaban no Fukkatsu (Japan)",
    "Knightmare II - The Maze of Galious (Japan)": "Knightmare II - The Maze of Galious (Japan)",
    "Kung Fu 2 (Japan)": "Kung Fu 2 (Japan)",
    "Kunio Tournament (Japan)": "Kunio no Nekketsu Toukon Densetsu (Japan)",
    "Legend of Getsu Fuma, The (Japan)": "Legend of Getsu Fuma, The (Japan)",
    "Legend of Peach Boy, The (Japan)": "Momotarou Monogatari (Japan) (En)",
    "Legend of Princess Kaguya, The (Japan)": "Kaguyahime Monogatari (Japan) (En)",
    "Little Ninja Hattori (Japan)": "Kamen no Ninja - Hanamaru (Japan)",
    "Mega Man 6 - The Greatest Battle in History!! (Japan)": "Rockman 6 - Shikabane Yusutakaichi no Daku (Japan)",
    "Mighty Morphin Power Rangers (Japan)": "Mighty Morphin Power Rangers (Japan)",
    "Mr. Moai (Japan)": "Mr. Splash (Japan) (En) (Columbia Version)",
    "NES Wars (Japan)": "Bokosuka Wars (Japan) (En)",
    "Nadia - Secret of the Blue Water (Japan)": "Nadia - Secret of the Blue Water (Japan)",
    "Nakayoshi n' Me (Japan)": "Nakayoshi - Anime de Asobu (Japan)",
    "Nekketsu Highschool Dodgeball Club - Soccer Edition (Japan)": "Nekketsu Kouha Kunio-kun - Soccer Edition (Japan) (En)",
    "Ninja Rahoi! (Japan)": "Dragon Ninja (Japan)",
    "Paaman 2 - Down with the Secret Madou Society! (Japan)": "Paaman 2 - Akutou Kikou no Yabou (Japan)",
    "Parodius - From Myth to Laughter (Japan)": "Parodius - From Myth to Laughter! (Japan)",
    "Peacock King (Japan)": "Peacock King (Japan)",
    "Peacock King II (Japan)": "Peacock King 2 (Japan)",
    "Penguin Wars (Japan)": "Bokosuka Wars (Japan) (En)",
    "Q-tarou the Ghost - Bow Wow Panic (Japan)": "Q-tarou no Wanpaku Jidai (Japan) (En)",
    "Raging Fire - Recca (Japan)": "Recca (Japan) (En)",
    "Rainbow Silkroad (Japan)": "Silk Road - Enkoku Monogatari (Japan)",
    "Requiem for Battle (Japan)": "Requiem - Fukkatsu no Shou (Japan)",
    "River City Ransom Zero (Japan)": "Nekketsu Kouha Kunio-kun - Rantou Kyousoukyoku (Japan) (Rev A)",
    "Samurai Pizza Cats (Japan)": "Kyatto Ninden Teyandee (Japan) (En)",
    "Sansara Naga (Japan)": "Samsara Naga (Japan)",
    "Sherlock Holmes - The Kidnapped Countess (Japan)": "Sherlock Holmes - The Kidnapped Countess (Japan)",
    "Softball Heaven (Japan)": "Softball Tengoku (Japan)",
    "Splatter House - Super Deformed (Japan)": "Splatterhouse - Wanpaku Graffiti (Japan)",
    "Spooky Kitaro 2 - Kitaro Versus Yokai Army (Japan)": "Gegege no Kitarou 2 - Youkai Hakaba no Souzoushoku (Japan)",
    "Spooky Kitarou in the Yokai World (Japan)": "Gegege no Kitarou (Japan) (En)",
    "Stardom Warriors (Japan)": "Stardom Warriors (Japan)",
    "Sted - Starfield of Memorable Relics (Japan)": "Sted (Japan)",
    "Super Dimensional Fortress - Macross (Japan)": "Choudenji Robo - Macross (Japan) (En)",
    "Super Planetary War Chronicle - MetaFight (Japan)": "Metafight (Japan) (En)",
    "Super Rescue - Solbrain (Japan)": "Directive - Solbrain - Super Rescue (Japan) (En)",
    "Takeshi's Challenge (Japan)": "Takeshi no Chousenjou (Japan)",
    "Tank Commander - Desert Fox (Japan)": "Tank Commander - Desert Fox (Japan)",
    "Technos Samurai - Downtown Special! (Japan)": "Technos Samurai - Downtown Special! (Japan)",
    "Three Kingdoms - Champion of the Center (Japan)": "San Kingdoms - Champion of the Center (Japan)",
    "Three-Eyed One, The (Japan)": "Sannenme no Tenshi (Japan) (En)",
    "Treasures of Elnark (Japan)": "Treasures of Elnark (Japan)",
    "Valis - The Fantasm Soldier (Japan)": "Valis - The Fantasm Soldier (Japan)",
    "Valkyrie's Adventure - Legend of the Time Key (Japan)": "Valkyrie no Densetsu (Japan) (En)",
    "Warwolf Chronicles (Japan)": "Warwolf Chronicles (Japan)",
    "Zoids - Battle of Central Continent (Japan)": "Zoids - Battle of Central Continent (Japan)",
}

# Load and update
aliases_path = Path("../server/data/dats/EN-Dats/aliases.json")
with open(aliases_path) as f:
    aliases = json.load(f)

nes_aliases = aliases.get("NES", {})

# Apply manual batch
updated = 0
for trans_name, orig_name in manual_batch.items():
    if trans_name in nes_aliases and nes_aliases[trans_name] == "":
        nes_aliases[trans_name] = orig_name
        updated += 1

print(f"Manual batch updated: {updated} entries")

# Save
with open(aliases_path, 'w') as f:
    json.dump(aliases, f, indent=2, ensure_ascii=False)

# Stats
filled = sum(1 for v in nes_aliases.values() if v != "")
empty = sum(1 for v in nes_aliases.values() if v == "")
total = len(nes_aliases)

print(f"\nNES Stats:")
print(f"  Total: {total}")
print(f"  Filled: {filled} ({filled*100/total:.1f}%)")
print(f"  Empty: {empty}")
print(f"  Remaining research needed: {empty}")

