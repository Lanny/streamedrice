#! /usr/bin/python
from gevent import monkey; monkey.patch_all()

import urllib2
import re
import json
from flask import Flask, Response, request

from gevent.pywsgi import WSGIServer
from gevent import coros
from gevent.event import AsyncResult

class IcyHandler(urllib2.HTTPHandler):
  def http_response(self, req, res):
    # Hacky workaround for urllib not grasping SHOUTcast's retarded
    # response headers.

    # Pop the first line off because it's what chokes urllib2
    res.readline()
    while 1:
      new_header = res.readline().strip()
      if not new_header:
        break

      print new_header
      res.headers.addheader(*new_header.split(':', 1))

    return res

stream_events = {}

app = Flask(__name__)

@app.route('/')
def index():
  return open('index.html', 'r').read()

@app.route('/parse-pls/', methods=['POST'])
def parse_pls():
  if not request.form.has_key('playlist_url'):
    return 'pleb'

  url = request.form['playlist_url']
  res = urllib2.urlopen(url)
  playlist = res.read()
  stream_url = re.search('File1=(.+?)\n', playlist).group(1)
  stream_url = stream_url.encode('Base64')

  return Response(json.dumps(stream_url), mimetype='application/json')

@app.route('/stream/<path:url>/s.mp3')
def stream(url):
  encurl = url
  url = url.decode('Base64')

  if 'http://' not in url:
    url = 'http://' + url

  def gen(url):
    req = urllib2.Request(url)
    req.add_header('Icy-MetaData', '1')

    opener = urllib2.build_opener(IcyHandler())
    res = opener.open(req)

    metadata_interval = int(res.headers['icy-metaint'])
    metadata_string = ''

    while 1:
      yield res.read(metadata_interval)

      metadata_length = ord(res.read(1)) * 16
      if metadata_length:
        print 'laaa'
        entries = res.read(metadata_length).split(';')
        metadata = {}
        for metadatum in entries:
          if 'StreamTitle' in metadatum:
            metadatum = metadatum.split('=')[1]
            artist, song = metadatum[1:-1].split('-')
            metadata['artist'] = artist
            metadata['song'] = song

        update_event = stream_events.get(encurl)
        if update_event:
          update_event.set(metadata)

        stream_events[encurl] = AsyncResult()

        print metadata

  return Response(gen(url), mimetype='audio/mpeg')

@app.route('/metadata/<stream>/d.json')
def metadata(stream):
  if stream not in stream_events:
    stream_events[stream] = AsyncResult()

  # Return control until there's an update
  metadata = stream_events[stream].get()

  print 'hai'
  return Response(json.dumps(metadata), mimetype='application/json')

if __name__ == '__main__':
  app.debug = True
  #app.run(host="0.0.0.0")

  http_server = WSGIServer(('', 5000), app)
  http_server.serve_forever()
