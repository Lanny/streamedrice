#! /usr/bin/python
import urllib2
from flask import Flask, Response

app = Flask(__name__)

@app.route('/')
def index():
  return open('index.html', 'r').read()

@app.route('/parse-pls/', methods=['POST'])
def parse_pls():
  if not request.form.has_key('plylist_url'):
    return 'pleb'

  url = request.form['playlist_url']
  urllib2.urlopen(url)

@app.route('/stream/<path:url>/s.mp3')
def stream(url):
  if 'http://' not in url:
    url = 'http://' + url

  print url
  def gen(url):
    req = urllib2.urlopen(url)
    while 1:
      yield req.read(4096)

  return Response(gen(url), mimetype='audio/mpeg')

if __name__ == '__main__':
  app.debug = True
  app.run(host="0.0.0.0")
