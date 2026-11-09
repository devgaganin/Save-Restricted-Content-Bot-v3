"""Guard payment history from MongoDB TTL deletion."""


def check_premium_indexes(indexes):
    # A TTL index on *any* field can delete the whole document, including the
    # payment IDs used to prevent duplicate grants on event redelivery.
    for name, spec in indexes.items():
        if 'expireAfterSeconds' in spec:
            raise RuntimeError(f'Unsafe TTL index on premium_users: {name}. Review README before billing.')


async def check_premium_indexes_async(collection):
    check_premium_indexes(await collection.index_information())
