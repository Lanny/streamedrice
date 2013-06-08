#! /usr/bin/python

import urllib2
import socket
from urllib import urlencode
import re
import json
from flask import Flask, Response, request, redirect

from gevent import monkey, spawn, sleep
from gevent.pywsgi import WSGIServer
from gevent import coros
from gevent.event import Event

monkey.patch_all()

class RiceException(Exception):
  pass

class StreamHandler(object):
  def __init__(self, stream_url):
    '''Open a connection to the remote stream and read through the headers.'''
    print stream_url
    url_data = re.match(r'(http://)?(?P<host>.+?\.\w{2,5})(?P<path>/.+?)?:(?P<port>\d+)/?', stream_url)
    print url_data.groups()

    self._buf = ''
    self._metabuf = ''
    self._metadata_json = ''
    self._chunk_read = 0

    self._cont = False
    self._data_available = Event()
    self._metadata_available = Event()

    self._s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    self._s.connect((url_data.group('host'), int(url_data.group('port'))))

    path = url_data.group('path')
    if not path: path = '/'

    request = '\r\n'.join([
      'GET %s HTTP/1.1' % path,
      'Icy-MetaData: 1',
      '\r\n'
    ])

    print repr(request)

    self._s.send(request)

    while '\r\n\r\n' not in self._buf:
      self._buf += self._s.recv(64)

    raw_headers, self._buf = self._buf.split('\r\n\r\n', 1)
    self.headers = raw_headers.split('\r\n')

    for idx, header in enumerate(self.headers[1:]):
      self.headers[idx+1] = tuple(header.split(':', 1))

    print self.headers
    if '200' not in self.headers[0]:
      raise RiceException('HTTP Error: %s' % self.headers[0])

    self._metaint = int(filter(lambda x: x[0] == 'icy-metaint', self.headers)[0][1])
    self._chunk_read = len(self._buf)

  def read_data(self):
    '''Sleep until some music data is available forthis stream.'''
    self._data_available.wait()
    return self._buf

  def read_metadata(self, blocking=True):
    '''Sleep until some metadata is available for this stream.'''
    if blocking:
      self._metadata_available.wait()

    return self._metadata_json

  def pump_forever(self):
    '''So long as the _cont flag is true and the socket remails open, read from
    it and publish to anyone who will listen. That includes metadata.'''
    self._cont = True
    while self._cont:
      while (self._chunk_read < self._metaint):
        self._buf = self._s.recv(self._metaint - self._chunk_read)
        self._chunk_read += len(self._buf)

        self._data_available.set()
        self._data_available.clear()
        sleep(0)

      self._chunk_read = 0

      mlen = ord(self._s.recv(1))*16
      if mlen > 0:
        self._metabuf = ''
        while (len(self._metabuf) < mlen):
          self._metabuf += self._s.recv(mlen - len(self._metabuf))

        # Run this in another eventlet because we're probs going to make a last.fm
        # call and we don't want to get in the way of actual music loading.
        spawn(self.process_metadata, self._metabuf)
        sleep(0)

  def process_metadata(self, raw_metadata):
    '''Parse raw metadata into clean JSON and fetch Last.fm data if possible. Needs
    to be run as a seperate eventlet because of possible API calls.'''
    metadata = {}
    for line in raw_metadata.split(';'):
      parts = line.rsplit('=', 1)
      if len(parts) == 2:
        metadata[parts[0]] = parts[1]

      elif len(parts) == 1:
        metadata[parts[0]] = None

    if 'StreamTitle' in metadata:
      artist, song = metadata['StreamTitle'][1:-1].split('-', 1)

      metadata['artist'] = artist.strip()
      metadata['song'] = song.strip()

    if settings.get('Last.fm Integration'):
      params = {
        'method':'track.getInfo',
        'artist':metadata['artist'],
        'track':metadata['song'],
        'api_key':settings['key'],
        'format':'json'
        }

      resp = urllib2.urlopen('http://ws.audioscrobbler.com/2.0/?'+ urlencode(params))
      last_fm_data = json.loads(resp.read())
      resp.close()

      if not last_fm_data.get('error'):
        metadata['album_art'] = last_fm_data['track']['album']['image'][0]['#text']

    self._metadata_json = json.dumps(metadata)
    self._metadata_available.set()
    self._metadata_available.clear()
    sleep(0)

def gen(encurl, url):
  '''The generator that we throw at clients for the .mp3 stream'''
  stream = streams.get(encurl)
  if not stream:
    stream = StreamHandler(url)
    streams[encurl] = stream
    spawn(stream.pump_forever)

    print streams

  local_buf = ''
  while 1:
    # Build a buffer this side because Windows flips shit if you send small 
    # messages too quickly (I think)
    local_buf += stream.read_data()

    if len(local_buf) > 44998:
      yield local_buf
      local_buf = ''

# b64 encoded stream URLs -> StreamHandler map shared by whole app.
streams = {}

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 54 * 1024

def find_stream_url(pls_string):
  '''Return the Base64 encoded stream url from a .pls file'''
  stream_url = re.search('File1=(.+?)\n', pls_string).group(1)
  stream_url = stream_url.encode('Base64')

  return stream_url.strip()

@app.route('/')
def index():
  return open('index.html', 'r').read()

@app.route('/parse-pls/', methods=['POST'])
def parse_pls():
  if not request.form.has_key('playlist_url'):
    return 'pleb'

  url = request.form['playlist_url']
  res = urllib2.urlopen(url)
  stream_url = find_stream_url(res.read())

  return Response(json.dumps(stream_url), mimetype='application/json')

@app.route('/parse-pls-file/', methods=['POST'])
def parse_plse_file():
  pls_string = request.files['pls'].stream.read()
  stream_url = find_stream_url(pls_string)

  print stream_url
  return redirect('/#' + stream_url)

@app.route('/stream/<url>/s.mp3')
def stream(url):
  encurl = url
  url = url.decode('Base64')

  return Response(gen(encurl, url), mimetype='audio/mpeg')

@app.route('/metadata/<stream>/d.json')
def metadata(stream):
  '''Return json encoded metadata for the current track on this stream'''
  stream = streams.get(stream)
  if not stream:
    return 'Fuck off pleb'

  b = not request.args.get('initial')
  metadata = stream.read_metadata(blocking=b)
  return Response(metadata, mimetype='application/json')

if __name__ == '__main__':
  try:
    f = open('settings.json', 'r')
    settings = json.loads(f.read())
    f.close()

  except IOError:
    settings = {'Last.fm Integration':False}

  app.debug = True
  #app.run(host="0.0.0.0")

  http_server = WSGIServer(('', 5000), app)
  http_server.serve_forever()
