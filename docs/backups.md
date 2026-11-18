# Backups

Everything PaperJay knows is in its data folder (`DATA_DIR`: `./data` with Docker,
`/var/lib/paperjay` on Proxmox):

```text
paperjay.db        documents, labels, notes, history, accounts, settings (SQLite)
media/originals/   every file as it was added
media/archive/     the searchable PDFs
media/thumbs/, media/pages/   pictures made from them (can be rebuilt)
backups/           database copies made before upgrades and by `paperjay backup`
```

## Recommended

1. **Back up the whole data folder** with whatever you already use (Proxmox's nightly vzdump of the
   container, restic, Borg, a NAS snapshot). For a consistent database copy while PaperJay runs, run
   `paperjay backup` first, or back up while it's stopped.
2. **Keep an export somewhere else** too, e.g. nightly to a NAS:
   `paperjay export /mnt/nas/paperjay-export` (only copies what changed). It's readable without PaperJay,
   which protects you against PaperJay itself.
3. **Check now and then:** `paperjay check` re-hashes every file and reports anything missing or damaged.

## Restoring

- From a data-folder backup: stop PaperJay, put the folder back, start it.
- From an export: install PaperJay fresh, then `paperjay import-export /path/to/export`.
- After a bad upgrade: the database copy from just before it is in `backups/`
  (`paperjay-v<old version>-<date>.db`); stop PaperJay, copy it over `paperjay.db`, and run the old version.
  On Proxmox the Update button does this by itself when the new version doesn't start.
