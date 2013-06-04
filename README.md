# Streamed Rice

Streamed Rice is a lightweight SHOUTcast (.pls) internet radio pass-though server leveraging the HTML5 audio element. You need to run Steamed Rice yourself, or find a public server, but you will be able to play arbritrary SHOUTcast streams from any modern browser.

## Quick Setup
```
$ git clone https://github.com/RyanJenkins/streamedrice.git
$ cd streamedrice
$ ./streamedrice.py
```

and navigate to localhost using a modern web browser. Streamed Rice depends on Gevent to mange simultanious connections, you may be able to run using Flask's development server but it would be grossly inefficient.

## Last.fm Integration
Streamed Rice supports Last.fm integration in the form of album art fetching and/or scrobbleing. Both these things require a Last.fm API key. On startup Streamed Rice will search its current directory for a settings.json file containing Last.fm API information in the following format:
```
{
  "scrobbling":true,
  "albumart":true,
  "key":"",
  "secret":""
}
```

if this file isn't found Streamed Rice will start and operate without these features.