from setuptools import setup

setup(
    name='speedyspotify',
    version='0.1.1',
    description='Async Spotify web API client',
    author='Steinthor Palsson',
    author_email='steini90@gmail.com',
    url='https://github.com/steinitzu/speedyspotify',
    install_requires=[
        'requests>=2.13.0',
        'gevent>=1.2.1'
    ],
    license='LICENSE',
    packages=['speedyspotify']
)
