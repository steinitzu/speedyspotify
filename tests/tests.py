import os
from uuid import uuid4

import pytest

from speedyspotify import Spotify as Client
from speedyspotify.oauth2 import SpotifyOAuth
from speedyspotify.util import find_item, get_id, get_ids

from items import hungry_freaks_daddy

import logging
import sys
requests_log = logging.getLogger("requests.packages.urllib3")
requests_log.setLevel(logging.DEBUG)
requests_log.propagate = True
requests_log.addHandler(logging.StreamHandler(stream=sys.stdout))

@pytest.fixture()
def spotify():
    return Client()


@pytest.fixture()
def access_token():
    auth = SpotifyOAuth(
        os.environ['SPOTIFY_CLIENT_ID'],
        os.environ['SPOTIFY_CLIENT_SECRET'],
        os.environ['SPOTIFY_REDIRECT_URI'],
        scope=' '.join(['playlist-read-private',
                        'playlist-read-collaborative',
                        'playlist-modify-public',
                        'playlist-modify-private',
                        'user-follow-read',
                        'user-library-read',
                        'user-library-modify',
                        'user-read-private',
                        'user-top-read']))
    token = auth.refresh_access_token(os.environ['SPOTIFY_REFRESH_TOKEN'])
    return token


@pytest.fixture()
def authorized_client(access_token):
    return Client(access_token=access_token)


def uid():
    return str(uuid4())


def test_get_ids(spotify):
    saved_album = {'album': {'id': uid()}}
    assert get_id('album', saved_album) == saved_album['album']['id']

    saved_track = {'track': {'id': uid(), 'type': 'track'}}
    assert get_id('track', saved_track) == saved_track['track']['id']

    aids = [uid(), uid()]
    artists = {'album': {'artists': [{'id': aids[0]}, {'id': aids[1]}]}}
    assert list(get_ids('artist', [artists])) == aids

    assert get_id('album', hungry_freaks_daddy) == hungry_freaks_daddy['album']['id']


def test_find_item():
    d = {'artist': {'album': {'items': 'found me'}}}
    assert find_item('items', d) == 'found me'

    
def test_album(spotify):
    freak_out = '3PZXB9NBWf11eDS72JCGaY'
    album = spotify.join(spotify.album(freak_out))
    assert album['name'].lower() == 'freak out!'

    
def test_album_from_track(spotify):
    album = spotify.join(spotify.album(hungry_freaks_daddy))
    assert album['name'].lower() == 'freak out!'

    
def test_track(spotify):
    track = spotify.join(spotify.track(hungry_freaks_daddy))
    assert track['id'] == hungry_freaks_daddy['id']


def test_tracks_from_album(spotify):
    tracks = spotify.join(spotify.album_tracks(hungry_freaks_daddy))
    assert tracks['items'][0]['id'] == hungry_freaks_daddy['id']


def test_saved_albums(authorized_client):
    c = authorized_client
    albums = c.join(c.current_user_saved_albums())
    for album in albums['items']:
        assert set(album.keys()) == set(['added_at', 'album'])
    albums = c.join(c.current_user_saved_albums(market='DE'))
    for album in albums['items']:
        assert set(album.keys()) == set(['added_at', 'album'])        

        
def test_all_saved_tracks(authorized_client):
    c = authorized_client
    first = c.join(c.current_user_saved_tracks(limit=1))
    total = first['total']
    tracks = list(c.join(c.all(c.current_user_saved_tracks), extract='items'))
    assert len(tracks) == total
    assert 'added_at' in tracks[0]


def test_all_chunked(authorized_client):
    c = authorized_client
    albums = c.all(c.artist_albums, hungry_freaks_daddy, country='CA')
    albums = list(c.join(albums, 'items'))
    albums = list(c.join(c.all(c.albums, albums), 'albums'))

    tracks = list(c.tracks.all(albums).fetch('tracks'))
    #c.all(c.tracks, albums)
    #tracks = list(c.join(tracks, extract='tracks'))
    for i in tracks:
        print(i['id'])

    albums = list(c.join(c.all(c.albums, tracks), 'albums'))
    assert len(albums) > 0


def test_recommendations(authorized_client):
    c = authorized_client
    r = c.recommendations(seed_artists=[hungry_freaks_daddy])
    r2 = c.recommendations(seed_artists=[hungry_freaks_daddy])
    print(list(c.join([r, r2], 'tracks')))
    
def test_all_search(authorized_client):
    c = authorized_client
    artists = c.all(c.search, 'eels', type='artist')
    
