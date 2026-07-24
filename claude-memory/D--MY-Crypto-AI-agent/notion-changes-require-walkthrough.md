---
name: notion-changes-require-walkthrough
description: "Never write to the trader's live Notion journal without explicit approval and a UI walkthrough — 2026-07-08 he mistook hidden duplicates for deleted trades and restored the whole DB from a backup"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 26b74b3d-643c-4a17-af35-0a92cc97caad
---

On 2026-07-08 I linked 7 duplicate trades via the Main Trade relation and added two
filtered views ("Без дублей", "Win rate (без дублей)") to the live Trading Journal.
Nothing was deleted, but the trader opened the journal, saw 19 rows instead of 26,
concluded 7 trades were gone, couldn't find the view tabs / filter to "bring them
back" even after my text explanation, and recovered by restoring a duplicate copy of
the journal and trashing the one I had modified. All my Notion-side work was lost and
the data source ids went stale (see [[project-checklist]]).

**Why:** The trader is not a technical Notion user. A change that silently alters what
he sees when he opens his journal (new default-ish view, rows hidden by a filter he
didn't create) reads to him as data loss, and his recovery instinct is "restore from
backup", not "inspect filters". My real mistake was sequencing: I ran `--apply` on the
whole live journal and created views BEFORE he had seen and approved the mechanism.

**How to apply:** For ANY write to his Notion (property values, schema, views):
1. Propose the approach and get an explicit "да, применяй" first.
2. Demo on 1-2 records max, tell him exactly where to click, and have him toggle the
   filter himself before touching the rest.
3. Prefer changes that do NOT alter his existing tabs/default view; call out loudly
   what he will see differently the next time he opens the journal.
4. Remind him nothing is deleted and show the undo path (clear the property / delete
   the view) BEFORE applying, not after he panics.
Dry-run-by-default CLI design was right — keep that pattern.

2026-07-08 evening: the retry with this exact process (demo on 1 duplicate → he
clicked around himself → he built his own filtered tab «Уникальные» by duplicating
his All view → explicit «давай на весь журнал» → apply) succeeded with zero panic.
Extra lesson: views created via API lack the bottom Calculate row and he needs it —
prefer having HIM duplicate one of his own tabs and add the filter himself.
