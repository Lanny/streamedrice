#! /usr/bin/python
import urllib2
import re
import json
from flask import Flask, Response, request

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
        metadata = res.read(metadata_length)
        print metadata

  return Response(gen(url), mimetype='audio/mpeg')

if __name__ == '__main__':
  app.debug = True
  app.run(host="0.0.0.0")
