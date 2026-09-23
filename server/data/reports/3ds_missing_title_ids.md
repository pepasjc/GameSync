# 3DS Missing `title_id` Report

Generated: 2026-04-22T23:58:58.827321+00:00

## Summary

- Total missing rows: `85`
- Missing rows whose 4-char code exists in `3dstdb.txt`: `0`
- Missing rows with a unique core-name candidate in existing 3DS DAT `title_id` entries: `27`
- Missing rows with ambiguous core-name candidates in existing 3DS DAT `title_id` entries: `28`

### By DAT

- `Nintendo - Nintendo 3DS (Digital).dat`: `47`
- `Nintendo - Nintendo 3DS.dat`: `38`

### By Region

- `Europe`: `32`
- `USA`: `24`
- `Japan`: `8`
- `France`: `5`
- `Korea`: `3`
- `Spain`: `2`
- `Taiwan`: `2`
- `Canada`: `2`
- `NONE`: `2`
- `China`: `2`
- `United Kingdom`: `1`
- `Germany`: `1`
- `Italy`: `1`

### By Name-Match Type

- `ambiguous_core_match`: `28`
- `none`: `30`
- `unique_core_match`: `27`

## Sample Unique Core-Name Matches

- `CTR-P-BGRZ` — Disney Violetta - Rhythm & Music (USA) -> 000400000012F400:Disney Violetta - Rhythm & Music (Europe) (En,Fr,De,Es,It,Pt)
- `CTR-P-BFXZ` — Fire Emblem If - Byakuya Oukoku (Taiwan) (Ja) -> 000400000012DC00:Fire Emblem If - Byakuya Oukoku (Japan)
- `` — Hidden Expedition - Titanic (Europe) (En,Fr,De,Nl) (Rev 2) -> 00040000000D7200:Hidden Expedition - Titanic (Europe) (En,Fr,De,Nl)
- `` — Imagine Collection (Europe) (En,Fr,De,Es,Nl,Sv,No,Da) (Rev 1) -> 0004000000150A00:Imagine Collection (Europe) (En,Fr,De,Es,Nl,Sv,No,Da)
- `` — Kobito Dukan - Kobito Kansatsu Set (Japan) -> 0004000000095B00:Kobito Dukan - Kobito Kansatsu Set (Japan) (Rev 1)
- `` — LEGO Ninjago - L'Ombre de Ronin (France) (En,Fr,De,Es,It,Nl,Da) (Rev 1) -> 000400000014E800:LEGO Ninjago - L'Ombre de Ronin (France) (En,Fr,De,Es,It,Nl,Da)
- `` — Nintendo presents - New Style Boutique 2 - Fashion Forward (Europe) (En,Fr,De,Es,It) (Rev 1) -> 000400000016A100:Nintendo presents - New Style Boutique 2 - Fashion Forward (Europe) (En,Fr,De,Es,It)
- `` — Professeur Layton et le Masque des Miracles (France) (Rev 1) -> 00040000000A8800:Professeur Layton et le Masque des Miracles (France)
- `` — Rabbids 3D (Europe) (En,Fr,De,Es,It,Nl,Pt,Sv,No,Da) (Rev 1) -> 0004000000037700:Rabbids 3D (Europe) (En,Fr,De,Es,It,Nl,Pt,Sv,No,Da)
- `` — 3D After Burner II (Japan) (eShop) (Encrypted CIA) -> 0004000000157A00:3D After Burner II (Europe) (eShop)
- `` — 3D After Burner II (USA) (eShop) (Encrypted CIA) -> 0004000000157A00:3D After Burner II (Europe) (eShop)
- `CTR-N-BCCP` — Conception II - Children of the Seven Stars (Europe) (eShop) -> 0004000000112C00:Conception II - Children of the Seven Stars (USA)
- `CTR-N-PAAP` — F-Zero - Maximum Velocity (Europe) (GBA) (Virtual Console) -> 0004000000074900:F-Zero - Maximum Velocity (USA) (GBA) (Virtual Console)
- `CTR-N-PAJP` — Fire Emblem - The Sacred Stones (Europe) (En,Fr,De,Es,It) (GBA) (Virtual Console) -> 0004000000076A00:Fire Emblem - The Sacred Stones (USA) (GBA) (Virtual Console)
- `CTR-N-JKZP` — Flipnote Studio 3D (Europe) (En,Fr,De,Es,It) (Rev 1) (eShop) -> 00040000000C6600:Flipnote Studio 3D (USA) (eShop)
- `CTR-N-JKZP` — Flipnote Studio 3D (Europe) (En,Fr,De,Es,It) (eShop) -> 00040000000C6600:Flipnote Studio 3D (USA) (eShop)
- `CTR-N-PAGP` — Kirby & The Amazing Mirror (Europe) (GBA) (Virtual Console) -> 0004000000075C00:Kirby & the Amazing Mirror (USA) (GBA) (Virtual Console)
- `CTR-N-PAKP` — Legend of Zelda, The - The Minish Cap (Europe) (En,Fr,De,Es,It) (GBA) (Virtual Console) -> 0004000000076D00:Legend of Zelda, The - The Minish Cap (USA) (GBA) (Virtual Console)
- `CTR-N-PABP` — Mario Kart - Super Circuit (Europe) (GBA) (Virtual Console) -> 0004000000074C00:Mario Kart - Super Circuit (USA) (GBA) (Virtual Console)
- `CTR-N-JESP` — Nintendo Video (Europe) (Rev 1) (eShop) -> 000400000004AA00:Nintendo Video (USA) (En,Fr,Es) (eShop)
- `CTR-N-JESP` — Nintendo Video (Europe) (eShop) -> 000400000004AA00:Nintendo Video (USA) (En,Fr,Es) (eShop)
- `CTR-N-JUPE` — Order Up!! (USA) (eShop) -> 0004000000065200:Order Up!! (Europe) (En,Fr,De,Es,It)
- `CTR-N-NACP` — Photos with Mario (Europe) (eShop) -> 0004000000130500:Photos with Mario (USA) (En,Fr,Es) (eShop)
- `CTR-N-JR3E` — Rabi Laby 3 (USA) (eShop) -> 00040000000FCC00:Rabi Laby 3 (Europe) (eShop)
- `CTR-N-JRBE` — Rising Board 3D (USA) (eShop) -> 00040000000B0D00:Rising Board 3D (Europe) (En,Fr,De,Es,It) (eShop)
- `CTR-N-PACP` — Wario Land 4 (Europe) (GBA) (Virtual Console) -> 0004000000074F00:Wario Land 4 (USA) (GBA) (Virtual Console)
- `CTR-N-PADP` — Yoshi's Island - Super Mario Advance 3 (Europe) (En,Fr,De,Es,It) (GBA) (Virtual Console) -> 0004000000075300:Yoshi's Island - Super Mario Advance 3 (USA) (GBA) (Virtual Console)

## Files

- CSV: `server\data\reports\3ds_missing_title_ids.csv`
- This summary: `server\data\reports\3ds_missing_title_ids.md`
