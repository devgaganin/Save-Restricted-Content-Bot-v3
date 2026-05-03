import os
from collections import defaultdict

from dotenv import load_dotenv
from pymongo import MongoClient


def build_key(doc):
    if doc.get("file_unique_id"):
        return ("file_unique_id", doc.get("collection_id"), doc["file_unique_id"])
    return (
        "source_fallback",
        doc.get("collection_id"),
        doc.get("source_chat_id"),
        doc.get("source_message_id"),
        doc.get("file_name"),
        doc.get("file_size"),
    )


def main():
    load_dotenv()
    mongo_uri = os.getenv("MONGO_DB", "mongodb://127.0.0.1:27017")
    db_name = os.getenv("DB_NAME", "telegram_downloader")

    client = MongoClient(mongo_uri)
    db = client[db_name]
    vault_files = db["vault_files"]
    source_cache = db["vault_source_cache"]

    docs = list(vault_files.find().sort("created_at", 1))
    buckets = defaultdict(list)
    for doc in docs:
        buckets[build_key(doc)].append(doc)

    removed = 0
    updated_cache = 0
    for _, items in buckets.items():
        if len(items) < 2:
            continue

        keep = items[0]
        dupes = items[1:]
        for dupe in dupes:
            result = source_cache.update_many(
                {"file_doc_id": dupe["_id"]},
                {"$set": {"file_doc_id": keep["_id"]}},
            )
            updated_cache += result.modified_count
            vault_files.delete_one({"_id": dupe["_id"]})
            removed += 1

    print(f"Removed duplicate vault files: {removed}")
    print(f"Updated cache references: {updated_cache}")


if __name__ == "__main__":
    main()
