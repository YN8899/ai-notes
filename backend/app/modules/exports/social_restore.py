"""Rekey private historical assets; never restore an active external relation."""
def prepare(rows, collections, *, prefix, keys):
    for row, source in zip(rows["social_archive_assets"], collections.get("social_archive_assets", []), strict=True):
        key = f"{prefix}/{row['id'].hex}/original"
        row["object_key"] = key
        keys[source["path"]] = key
