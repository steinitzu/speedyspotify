from functools import partialmethod, partial, update_wrapper
import json
import inspect
import math

from requests.packages.urllib3.util.retry import Retry
import requests
from gevent.pool import Pool as GeventPool
import gevent
from gevent import monkey

from .util import get_id, get_ids, find_item, get_uri, get_uris, chunked, extract_list

monkey.patch_all(thread=False, select=False)
#monkey.patch_all(select=False)


class SpotiRetry(Retry):
    def parse_retry_after(self, retry_after):
        seconds = super().parse_retry_after(retry_after)
        if seconds:
            return seconds*2
        return seconds
    

def max_limit(limit):
    def decorator(func):
        func.max_limit = limit
        return func
    return decorator


def chunk_size(limit):
    def decorator(func):
        func.chunk_size = limit
        return func
    return decorator


def object_type(otype):
    def decorator(func):
        func.object_type = otype
        return func
    return decorator


class GletList(list):
    def __init__(self, items, fetchmethod):
        super().__init__(items)
        self.fetch = partial(fetchmethod, self)
        update_wrapper(self.fetch, fetchmethod)

        
class SpotifyException(Exception):
    def __init__(self, request, response):
        self.http_status = response.status_code
        if response.text:
            try:
                msg = response.json()['error']['message']
            except (ValueError, KeyError):
                msg = response.text
        else:
            msg = 'Unkown error occured'
        self.response = response
        self.request = request
        super().__init__(self.http_status, msg, request.url)

class Spotify(object):
    prefix = 'https://api.spotify.com/v1'
    _pool_size = 10
    _max_retries = 5
    _timeout = 10

    def __init__(self, access_token=None, requests_session=None, pool_size=5, max_retries=5, timeout=10, gpool=False):
        """
        :param access_token: a spotify access token, either a token dict or access token string
        :param requests_session: used for API requests, if not specified self.default_session() is used
        :param pool_size: int connection pool size (if no requests_session provided)
        :param max_retries: int max number of retries on request failure (used only if no requests_session provided)
        :param timeout: time in seconds to wait for a response before failing
        """
        self._pool_size = pool_size
        self._max_retries = max_retries
        self._timeout = timeout
        self.session = requests_session or self.default_session()
        if isinstance(access_token, dict):
            self.access_token = access_token['access_token']
        else:
            self.access_token = access_token
        if gpool:
            self._gpool = GeventPool(self._pool_size)
        else: self._gpool = None

        for method in inspect.getmembers(self, predicate=inspect.ismethod):
            f = method[1]
            if any([hasattr(f, 'chunk_size'), hasattr(f, 'max_limit')]):
                f.__func__.all = partial(self.all, f)
                update_wrapper(f.__func__.all, self.all)

    def default_session(self):
        session = requests.session()
        retry = SpotiRetry(total=self._max_retries,
                           backoff_factor=1.5,
                           method_whitelist=['GET', 'POST', 'PUT', 'DELETE'],
                           status_forcelist=[429]+list(range(500, 600)),
                           respect_retry_after_header=True,
                           raise_on_status=False)
        ap = requests.adapters.HTTPAdapter(
            max_retries=retry,
            pool_block=False,
            pool_maxsize=self._pool_size,
            pool_connections=self._pool_size)
        session.mount('http://', ap)
        session.mount('https://', ap)
        return session

    def _auth_headers(self):
        if self.access_token:
            return {'Authorization': 'Bearer {}'.format(self.access_token)}
        return {}

    def _request(self, method, url, payload=None, **params):
        """
        Perform a Spotify API request and return a greenlet.
        """
        kwargs = dict(params=params)
        kwargs["timeout"] = self._timeout
        if not url.startswith('http'):
            url = self.prefix + url
        headers = self._auth_headers()
        headers['Content-Type'] = 'application/json'

        if payload:
            kwargs["data"] = json.dumps(payload)
        gs = self._gpool.spawn if self._gpool else gevent.spawn
        r = gs(self.session.request, method, url, headers=headers, **kwargs)
        r.fetch = partial(self.join, r)
        update_wrapper(r.fetch, self.join)
        gevent.sleep(0.05)
        return r

    _get = partialmethod(_request, 'GET')
    _post = partialmethod(_request, 'POST')
    _put = partialmethod(_request, 'PUT')
    _delete = partialmethod(_request, 'DELETE')

    def _all_with_offset(self, func, *args, **kwargs):
        kwargs['limit'] = 1
        first = func(*args, **kwargs).fetch()
        total = find_item('total', first)
        limit = func.max_limit
        kwargs['limit'] = limit

        callcount = math.ceil(total/limit)
        i = 0
        reqs = []
        for c in range(callcount):
            kwargs['offset'] = i
            reqs.append(func(*args, **kwargs))
            i += limit
        return reqs

    def _all_chunked(self, func, *args, **kwargs):
        unique = kwargs.pop('unique', False)
        args = list(args)
        items = get_ids(func.object_type, args[0])
        if unique:
            seen = set()
            seen_add = seen.add
            items = [iid for iid in items if not (iid in seen or seen_add(iid))]
        reqs = []
        chunk_size = kwargs.get('chunk_size') or func.chunk_size
        for chunk in chunked(items, chunk_size):
            args[0] = chunk
            reqs.append(func(*args, **kwargs))
        return reqs

    def _all_with_next(self, func, *args, **kwargs):
        raise NotImplementedError

    def all(self, func, *args, **kwargs):
        """
        Get a list of Greenlet requests for all results from given endpoint/function.

        :param func: Any Spotify function which normally returns paginated result sets
        or accepts a limited length argument list.
        :param unique: bool default False - each unique ID only passed to func once
        
        Other *args/**kwargs passed to |func|

        :return GletList:
        """
        if isinstance(func, str):
            func = getattr(self, func)
        result = None
        if 'offset' in inspect.signature(func).parameters:
            result = self._all_with_offset(func, *args, **kwargs)
        elif func.chunk_size:
            result = self._all_chunked(func, *args, **kwargs)
        else:
            raise NotImplementedError
        return GletList(result, self._join_many)

    def _join_one(self, one_request, extract=None):
        one_request.join()
        response = one_request.value
        if not response.ok:
            raise SpotifyException(response.request, response)
        if not response.text:
            return None
        result = one_request.value.json()
        if extract:
            return find_item(extract, result)
        return result

    def _join_many(self, request_objects, extract=False):
        """
        Join given list of request_objects and return a list
        of Spotify objects in dict format.
        
        :param request_objects: List of gevent Greenlets
        :param extract: Any truthy value to automatically concat list results.
        Useful when fetching multiple pages of a paged result set.
        """
        gevent.joinall(request_objects)
        results = []
        for g in request_objects:
            response = g.value
            if not response.ok:
                raise SpotifyException(response.request, response)
            if not response.text:
                results.append(None)
                continue
                # yield None
                # continue
            jso = response.json()
            if extract:
                results += [x for x in extract_list(jso)]
                # yield from find_item(extract, jso)
            else:
                results.append(jso)
        return results

    def join(self, request_objects, extract=None):
        if isinstance(request_objects, gevent.Greenlet):
            return self._join_one(request_objects, extract)
        return self._join_many(request_objects, extract)

    def next(self, result):
        if result['next']:
            return self._get(result['next'])
        else:
            return None

    def previous(self, result):
        if result['previous']:
            return self._get(result['previous'])
        else:
            return None

    def track(self, track_id):
        url = '/tracks/{id}'
        trid = get_id('track', track_id)
        return self._get(url.format(id=trid))

    @chunk_size(50)
    @object_type('track')
    def tracks(self, tracks, market=None):
        url = '/tracks'  # ?ids=...
        tids = ','.join(get_ids('track', tracks))
        return self._get(url, ids=tids, market=market)

    def artist(self, artist):
        url = '/artists/{id}'
        aid = get_id('artist', artist)
        return self.get(url.format(id=aid))

    @chunk_size(50)
    @object_type('artist')
    def artists(self, artists):
        url = '/artists'
        aids = ','.join(get_ids('artist', artists))
        return self._get(url, ids=aids)

    @max_limit(50)
    def artist_albums(self, artist, album_type=None, country=None, limit=20, offset=0):
        url = '/artists/{id}/albums'
        aid = get_id('artist', artist)
        return self._get(url.format(id=aid), album_type=album_type,
                         country=country, limit=limit, offset=offset)

    def artist_top_tracks(self, artist, country):
        url = '/artists/{id}/top-tracks'
        aid = get_id('artist', artist)
        return self._get(url.format(id=aid), country=country)

    def artist_related_artists(self, artist):
        url = '/artists/{id}/related_artists'
        aid = get_id('artist', artist)
        return self._get(url.format(id=aid))

    def album(self, album):
        url = '/albums/{id}'
        aid = get_id('album', album)
        return self._get(url.format(id=aid))

    @max_limit(50)
    def album_tracks(self, album, limit=20, offset=0):
        url = '/albums/{id}/tracks'
        aid = get_id('album', album)
        return self._get(url.format(id=aid), limit=limit, offset=offset)

    @chunk_size(20)
    @object_type('album')
    def albums(self, albums):
        url = '/albums'
        aids = ','.join(get_ids('albums', albums))
        return self._get(url, ids=aids)

    @max_limit(50)
    def search(self, q, limit=20, offset=0, type='track', market=None):
        url = '/search'
        return self._get(url, q=q, limit=limit, offset=offset, type=type, market=market)

    def user(self, user):
        url = '/users/{id}'
        uid = get_id('user', user)
        return self._get(url.format(id=uid))

    @max_limit(50)
    def current_user_playlists(self, limit=20, offset=0):
        url = '/me/playlists'
        return self._get(url, limit=limit, offset=offset)

    @max_limit(50)
    def user_playlists(self, user, limit=20, offset=0):
        url = '/users/{user_id}/playlists'
        uid = get_id('user', user)
        return self._get(url.format(user_id=uid), limit=limit, offset=offset)

    def user_playlist(self, user, playlist, fields=None):
        url = '/users/{user_id}/playlists/{playlist_id}'
        uid = get_id('user', user)
        plid = get_id('playlist', playlist)
        params = dict(fields=fields) if fields else {}
        return self._get(url.format(user_id=uid, playlist_id=plid), **params)

    @max_limit(100)
    def user_playlist_tracks(self, user, playlist, fields=None,
                             limit=100, offset=0, market=None):
        url = '/users/{user_id}/playlists/{playlist_id}/tracks'
        uid = get_id('user', user)
        plid = get_id('playlist', playlist)
        params = dict(limit=limit, offset=offset)
        if fields:
            params['fields'] = fields
        if market:
            params['market'] = market
        return self._get(url.format(user_id=uid, playlist_id=plid), **params)

    def user_playlist_create(self, user, name, public=True, collaborative=False):
        """
        Create playlist for given user. 

        :param user: user id or spotify User object  
        :param name: str name for new playlist  
        :param public: bool
        :param collaborative: bool
        """
        url = '/users/{user_id}/playlists'
        uid = get_id('user', user)
        body = dict(name=name, public=public, collaborative=collaborative)
        return self._post(url.format(user_id=uid), payload=body)

    def user_playlist_change_details(self, user, playlist, name=None, public=None, collaborative=None):
        url = '/users/{user_id}/playlists/{playlist_id}'
        uid = get_id('user', user)
        plid = get_id('playlist', playlist)
        body = {}
        if name is not None:
            body['name'] = name
        if public is not None:
            body['public'] = public
        if collaborative is not None:
            body['collaborative'] = collaborative
        return self._put(url.format(user_id=uid, playlist_id=plid), payload=body)

    def user_playlist_unfollow(self, user, playlist):
        url = '/users/{user_id}/playlists/{playlist_id}/followers'
        uid = get_id('user', user)
        plid = get_id('playlist', playlist)
        return self._delete(url.format(user_id=uid, playlist_id=plid))

    @chunk_size(50)
    @object_type('track')
    def user_playlist_add_tracks(self, tracks, user, playlist, position=None):
        url = '/users/{user_id}/playlists/{playlist_id}/tracks'
        uid = get_id('user', user)
        plid = get_id('playlist', playlist)
        turis = list(get_uris('track', get_ids('track', tracks)))

        body = dict(uris=turis)
        if position is not None:
            body['position'] = position
        return self._post(url.format(user_id=uid, playlist_id=plid), payload=body)

    def user_playlist_replace_tracks(self, tracks, user, playlist):
        url = '/users/{user_id}/playlists/{playlist_id}/tracks'
        uid = get_id('user', user)
        plid = get_id('playlist', playlist)
        turis = get_uris('track', get_ids('track', tracks))
        body = dict(uris=turis)
        return self._put(url.format(user_id=uid, playlist_id=plid), payload=body)

    def user_playlist_reorder_tracks(self, user, playlist, range_start, insert_before,
                                     range_length=1, snapshot_id=None):
        url = '/users/{user_id}/playlists/{playlist_id}/tracks'
        uid = get_id('user', user)
        plid = get_id('playlist', playlist)
        payload = {"range_start": range_start,
                   "range_length": range_length,
                   "insert_before": insert_before}
        if snapshot_id:
            payload["snapshot_id"] = snapshot_id
        return self._put(url.format(user_id=uid, playlist_id=plid), payload=payload)

    @chunk_size(50)
    @object_type('track')
    def user_playlist_remove_all_occurrences_of_tracks(self, tracks, user, playlist, snapshot_id=None):
        url = '/users/{user_id}/playlists/{playlist_id}/tracks'
        uid = get_id('user', user)
        plid = get_id('playlist', playlist)
        turis = get_uris('track', get_ids('track', tracks))
        payload = {'tracks': [{'uri': turi} for turi in turis]}
        if snapshot_id:
            payload["snapshot_id"] = snapshot_id
        return self._delete(url.format(user_id=uid, playlist_id=plid), payload=payload)

    def user_playlist_remove_specific_occurrences_of_tracks(self, tracks, user, playlist, snapshot_id=None):
        url = '/users/{user_id}/playlists/{playlist_id}/tracks'
        uid = get_id('user', user)
        plid = get_id('playlist', playlist)
        
        ftracks = []
        for tr in tracks:
            ftracks.append({
                "uri": get_uri("track", tr["uri"]),
                "positions": tr["positions"],
            })
        payload = {"tracks": ftracks}
        if snapshot_id:
            payload["snapshot_id"] = snapshot_id
        return self._delete(url.format(user_id=uid, playlist_id=plid), payload=payload)

    def current_user_playlist_follow_playlist(self, owner, playlist):
        url = '/users/{user_id}/playlists/{playlist_id}/followers'
        uid = get_id('user', owner)
        plid = get_id('playlist', playlist)
        return self._put(url.format(playlist_id=plid, user_id=uid))

    def playlist_followers_contains(self, owner, playlist, users):
        url = '/users/{user_id}/playlists/{playlist_id}/followers/contains'
        oid = get_id('user', owner)
        plid = get_id('playlist', playlist)
        uids = ','.join(get_ids('user', users))
        return self._get(url.format(user_id=oid, playlist_id=plid), ids=uids)

    def me(self):
        return self._get('/me')
    current_user = me

    @max_limit(50)
    def current_user_saved_albums(self, limit=20, offset=0, market=None):
        url = '/me/albums'
        return self._get(url, limit=limit, offset=offset, market=market)

    @max_limit(50)
    def current_user_saved_tracks(self, limit=20, offset=0, market=None):
        url = '/me/tracks'
        return self._get(url, limit=limit, offset=offset, market=market)

    @max_limit(50)
    def current_user_followed_artists(self, limit=20, after=None):
        url = '/me/following'
        return self._get(url, type='artist', limit=limit, after=after)

    @chunk_size(50)
    @object_type('track')
    def current_user_saved_tracks_delete(self, tracks):
        url = '/me/tracks'
        tids = ','.join(get_ids('track', tracks))
        return self._delete(url, ids=tids)

    def current_user_saved_tracks_contains(self, tracks):
        url = '/me/tracks/contains'  # ?ids=
        tids = ','.join(get_ids('track', tracks))
        return self._get(url, ids=tids)

    def current_user_saved_tracks_add(self, tracks):
        url = '/me/tracks'  # ?ids=
        tids = ','.join(get_ids('track', tracks))
        return self._put(url, ids=tids)

    @max_limit(50)
    def current_user_top_artists(self, limit=20, offset=0, time_range='medium_term'):
        url = '/me/top/artists'
        return self._get(url, time_range=time_range, limit=limit, offset=offset)

    @max_limit(50)
    def current_user_top_tracks(self, limit=20, offset=0, time_range='medium_term'):
        url = '/me/top/tracks'
        return self._get(url, time_range=time_range, limit=limit, offset=offset)

    @max_limit(50)
    def current_user_recently_played_tracks(self, after=None, before=None, limit=20):
        url = '/me/player/recently-played'
        return self._get(url, after=after, before=before, limit=limit)

    def current_user_saved_albums_add(self, albums):
        url = '/me/albums'  # ?ids=
        aids = ','.join(get_ids('album', albums))
        return self._put(url, ids=aids)

    @max_limit(50)
    def featured_playlists(self, locale=None, country=None, timestamp=None, limit=20, offset=0):
        url = '/browse/featured-playlists'
        return self._get(url, locale=locale, country=country, timestamp=timestamp, limit=limit, offset=offset)

    @max_limit(50)
    def new_releases(self, country=None, limit=20, offset=0):
        url = '/browse/new-releases'
        return self._get(url, country=country, limit=limit, offset=offset)

    @max_limit(50)
    def categories(self, country=None, locale=None, limit=20, offset=0):
        url = '/browse/categories'
        return self._get(url, country=country, locale=locale, limit=limit, offset=offset)

    @max_limit(50)
    def category_playlists(self, category_id, country=None, limit=20, offset=0):
        url = '/browse/categories/{category_id}/playlists'
        return self._get(url.format(category_id=category_id), country=country, limit=limit, offset=offset)

    @max_limit(100)
    def recommendations(self, seed_artists=(), seed_genres=(), seed_tracks=(), country=None, limit=20, **params):
        url = '/recommendations'
        if seed_artists:
            params['seed_artists'] = ','.join(get_ids('artist', seed_artists))
        if seed_tracks:
            params['seed_tracks'] = ','.join(get_ids('track', seed_tracks))
        if seed_genres:
            params['seed_genres'] = ','.join(seed_genres)
        params['limit'] = limit
        return self._get(url, **params)
    
    @chunk_size(5)
    @object_type('artist')
    def artist_recommendations(self, seed_artists, chunk_size=5, country=None, limit=20, **params):
        """
        Use artist_recommendations.all(args...) to get multiple sets of
        artist recommendations

        :param chunk_size: number of artists for each set of recommendations (max=5)
        :param country: limit recommendations to given ISO2 country code
        :param limit: max number of tracks per artist chunk
        :param **params: acoustic attributes prefixed with min_|max_|target
        """
        url = '/recommendations'
        params['seed_artists'] = ','.join(get_ids('artists', seed_artists))
        params['limit'] = limit
        return self._get(url, **params)

    def recommendation_genre_seeds(self):
        return self._get('/recommendations/available-genre-seeds')

    @chunk_size(100)
    @object_type('track')
    def audio_features(self, tracks):
        url = '/audio-features'  # ?ids=
        tids = ','.join(get_ids('track', tracks))
        return self._get(url, ids=tids)

    def audio_analysis(self, track):
        url = '/audio-analysis/{id}'
        tid = get_id('track', track)
        return self._get(url.format(id=tid))
