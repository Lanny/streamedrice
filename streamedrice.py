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

CRLF = '\r\n'
PLS_FORMAT = 0
M3U_FORMAT = 1

def split_n_pad(line):
  pair = line.split(':', 1)
  pair += [None for _ in range(2 - len(pair))]
  
  return tuple(pair)

class RiceException(Exception):
  def __init__(*args, **kwargs):
    if 'r_cause' in kwargs:
      self.r_cause = kwargs['r_cause']
      del kwargs['r_cause']

    super(RiceException, self).__init__(*args, **kwargs)

class StreamHandler(object):
  def __init__(self, stream_url, encurl, stream_format=PLS_FORMAT):
    '''Open a connection to the remote stream and read through the headers.'''
    url_data = re.match((r'(http://)?(?P<host>(\w+\.?)+)(:(?P<port>\d+))?(?P<path>/.+)?/?'), stream_url.strip())

    self._encurl = encurl

    self._buf = ''
    self._metabuf = ''
    self._metadata_json = ''
    self._chunk_read = 0
    self._subscribers = 0

    self._turns_without_sub = 0

    self._cont = False
    self._data_available = Event()
    self._metadata_available = Event()

    port = url_data.group('port') if url_data.group('port') else 80

    self._s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    self._s.connect((url_data.group('host'), int(port)))

    path = url_data.group('path')
    if not path: path = '/'

    request = '\r\n'.join([
      'GET %s HTTP/1.1' % path,
      'Icy-MetaData: 1',
      '\r\n'
    ])

    print request

    self._s.send(request)

    while '\r\n\r\n' not in self._buf:
      self._buf += self._s.recv(64)

    raw_headers, self._buf = self._buf.split(CRLF+CRLF, 1)

    self._headers = dict(map(split_n_pad, raw_headers.split(CRLF)))

    if not filter(lambda x: re.match(r'.+ 200 OK.*', x), self._headers.keys()):
      raise RiceException('HTTP Error connecting to new stream.', 
          r_cause="icy_error")
    
    try:
      self._metaint = int(self._headers.get('icy-metaint', '0'))
    except ValueError:
      raise RiceException('Bad metaint: %s' % repr(self._headers['icy-metaint']),
        r_cause="no_metaint") 

    self._chunk_read = len(self._buf)

  def read_data(self):
    '''Sleep until some music data is available forthis stream.'''
    # Keep track of this subscriber count, it it's ever zero when it comes time
    # to write data to the clients, the stream is aborted.
    self._subscribers += 1

    self._data_available.wait()

    self._subscribers -= 1
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
      while (len(self._buf) < self._metaint):
        self._buf += self._s.recv(self._metaint - len(self._buf))

      # Check if there are any subscribers for this stream, if not we terminate
      # this stream until someone new subscribes.
      if self._subscribers < 1:
        self._turns_without_sub += 1
        if self._turns_without_sub > 15:
          print 'No active subscribes, terminating stream.'
          del streams[self._encurl]
          return

      else:
        self._turns_without_sub = 0
          
      self._data_available.set()
      self._data_available.clear()
      sleep(0)
      self._buf = ''

      # If metaint is set to 0 then we're probably reading a m3u string and
      # there's to metadata to grab either way.
      if self._metaint:
        mlen = ord(self._s.recv(1))*16
        if mlen > 0:
          self._metabuf = ''
          while len(self._metabuf) < mlen:
            self._metabuf += self._s.recv(mlen - len(self._metabuf))

          # Run this in another eventlet because we're probs going to make a 
          # last.fm call and we don't want to get in the way of actual music 
          # loading.
          spawn(self.process_metadata, self._metabuf)
          sleep(0)

  def process_metadata(self, raw_metadata):
    '''Parse raw metadata into clean JSON and fetch Last.fm data if possible. 
    Needs to be run as a seperate eventlet because of possible API calls.'''
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

      resp = urllib2.urlopen('http://ws.audioscrobbler.com/2.0/?' + 
          urlencode(params))
      last_fm_data = json.loads(resp.read())
      resp.close()

      if not last_fm_data.get('error'):
        try:
          metadata['album_art'] = last_fm_data['track']['album']['image'][0]['#text']
        except KeyError:
          pass

    self._metadata_json = json.dumps(metadata)
    self._metadata_available.set()
    self._metadata_available.clear()
    sleep(0)

def gen(encurl, url):
  '''The generator that we throw at clients for the .mp3 stream'''
  stream = streams.get(encurl)
  if not stream:
    stream = StreamHandler(url, encurl)
    streams[encurl] = stream
    spawn(stream.pump_forever)

  local_buf = ''
  while 1:
    # Build a buffer this side because Windows flips shit if you send small 
    # messages too quickly (I think)
    local_buf += stream.read_data()

    if len(local_buf) > 24 * 1024:
      yield local_buf
      local_buf = ''

# b64 encoded stream URLs -> StreamHandler map shared by whole app.
streams = {}

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 54 * 1024

def find_stream_url(pls_string):
  '''Return the Base64 encoded stream url from a .pls or .m3u file and a
  string indicating which it is.'''
  lines = pls_string.split()

  if lines[0] == '[playlist]':
    # Looks like we've got a .pls style file
    fields = {}
    for line in lines[1:]:
      k, v = split_n_pad(line)
      fields[k] = v.strip()

    return fields['File1'].encode('Base64').strip(), PLS_FORMAT

  else:
    # Let's assume this is a .m3u file
    for line in lines:
      if line[0] == '#':
        continue
      else:
        return line.strip().encode('Base64').strip(), M3U_FORMAT

@app.route('/')
def index():
  return open('index.html', 'r').read()

@app.route('/parse-pls/', methods=['POST'])
def parse_pls():
  if not request.form.has_key('playlist_url'):
    return 'pleb'

  url = request.form['playlist_url']
  res = urllib2.urlopen(url)
  stream_url, stream_format = find_stream_url(res.read())
  res.close()

  return Response(json.dumps({'url': stream_url, 'format': 'stream_format'}), 
      mimetype='application/json')

@app.route('/parse-pls-file/', methods=['POST'])
def parse_plse_file():
  pls_string = request.files['pls'].stream.read()
  stream_url, stream_format = find_stream_url(pls_string)

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
    settings = json.load(f)
    f.close()

  except IOError:
    settings = {'Last.fm Integration':False}

  app.debug = True
  #app.run(host="0.0.0.0")

  http_server = WSGIServer(('', 5000), app)
  http_server.serve_forever()
