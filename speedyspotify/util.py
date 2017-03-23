import os


def id_from_str(strid):
    if strid.startswith('http'):
        return strid.split('/')[-1]
    elif strid.startswith('spotify:'):
        return strid.split(':')[-1]
    return strid


def get_ids(item_type, items):
    """
    Gets ids of itype from items.
    items can be a list of str ids,
    a list of dicts of itype or a list of dicts
    containing references to simplified itype objects
    """
    if isinstance(items, dict):
        items = [items]
    for item in items:
        if isinstance(item, str):
            yield id_from_str(item)
        elif item.get('type') == item_type:
            yield item['id']
        elif item_type == 'album':
            yield find_item('album', item)['id']
        elif item_type == 'artist':
            artists = find_item('artists', item)
            yield from (a['id'] for a in artists)
        elif item_type == 'track':
            if 'track' in item:
                yield item['track']['id']
                continue
            tracks = find_item('tracks', item)['items']
            yield from (t['id'] for t in tracks)
        else:
            raise ValueError('{} is not a valid item type'.format(item_type))

        
def get_id(item_type, item):
    return next(get_ids(item_type, [item]))


def get_uri(type, id):
    return 'spotify:' + type + ":" + get_id(type, id)


def get_uris(itype, ids):
    yield from (get_uri(itype, iid) for iid in ids)
        

def find_item(key, item):
    if key in item:
        return item[key]
    for k, v in item.items():
        if isinstance(v, dict):
            item = find_item(key, v)
            if item is not None:
                return item
        

def chunked(seq, n):
    """
    yield n sized chunks (list) from seq (sequence/generator)
    """
    chunk = []
    for i in seq:
        chunk.append(i)
        if len(chunk) == n:
            yield chunk
            chunk = []
    if chunk:
        yield chunk
        

def extract_list(item):
    if isinstance(item, list):
        return item
    if 'items' in item:
        return item['items']
    if 'tracks' in item:
        return item['tracks']
    if 'albums' in item:
        return item['albums']
    if 'artists' in item:
        return item['artists']
    raise Exception('No item list detected')
