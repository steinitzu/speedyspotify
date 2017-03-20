
# Speedyspotify #

An async Python client library for the Spotify web API   
Speedyspotify uses gevent to perform all Spotify API requests asynchronously as well as implementing several convenience methods to make working with the Spotify WEB API a breeze  

This package is in experimental stages, use at your own risk.  
Issues and pull requests are welcome!  

Tested on Python 3.4.3. Should work on any version after 3.4. Python 2 is not supported.  

## Features ##

All requests async by default  

Super convenient `all` method to perform in API requests in bulk  

OAuth2 and Client Credentials Authorization flows (borrowed from Spotipy (see oauth2.py module))  

Uses requests.Session with a connection pool and retry handler by default which respects Spotify's 429 retry-after header.  

## Usage ##

``` python
from speedyspotify import Spotify

s = Spotify()
request = s.search('artist:frank zappa', type='artist', limit=1)
results = request.fetch('items')
frank_zappa = results[0]['name']
print(frank_zappa)

>>> Frank Zappa
```


Lets get all of Frank Zappa's albums. He has a lot and we don't want to deal with pagination.  
Speedyspotify's `all` method makes this very convenient:  

``` python
from speedyspotify import Spotify
s = Spotify()

artist = s.search('artist:frank zappa', type='artist', limit=1).fetch('items')[0]

requests = s.artist_albums.all(artist, album_type='album', country='GB')
results = requests.fetch('items')  # returns a generator containing all albums

print([album['name'] for album in results])

>>> ["Chicago '78", 'Little Dots', 'Meat Light: The Uncle Meat Project/Object', 'Road Tapes, Venue #1 (Live Kerrisdale Arena, Vancouver B.C. - 25 August 1968)', ..., 'A Token Of His Extreme (Live)', 'Finer Moments', 'Understanding America', ...]
```

We can also use `all` on any endpoint that accepts a list of Spotify IDs

``` python
from speedyspotify import Spotify
s = Spotify()

artist = s.search('artist:frank zappa', type='artist', limit=1).fetch('items')[0]
albums = s.artist_albums.all(artist, album_type='album', country='GB').fetch('items')

# Get full album objects for all the albums
albums = s.albums.all(albums).fetch('albums')

# Get all the tracks from all the albums
tracks = s.tracks.all(albums).fetch('tracks')

# Add all the tracks to a playlist (this requires  Spotify() with an OAuth2 token for the given user)  
s.user_playlist_add_tracks.all(tracks, 'some user id', 'some playlist id').fetch()
```



## Authentication ##  

Speedyspotify uses spotipy's oauth2 module to handle authorization flow, read the documentation here: http://spotipy.readthedocs.io/en/latest/#authorized-requests

``` python
# Client credentials class
from speedyspotify.oauth2 import SpotifyClientCredentials

# User oauth class
from speedyspotify.oauth2 import SpotifyOAuth
```

