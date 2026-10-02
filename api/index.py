"""Vercel Python serverless control API backed by the shared database."""
from http.server import BaseHTTPRequestHandler
import json, os
from listner.config import Settings
from listner.db import Store

class handler(BaseHTTPRequestHandler):
    def _send(self, status, payload):
        encoded=json.dumps(payload).encode(); self.send_response(status); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(encoded))); self.end_headers(); self.wfile.write(encoded)
    def _store(self):
        s=Settings()
        if s.control_secret and self.headers.get('Authorization') != f'Bearer {s.control_secret}': self._send(401,{'error':'unauthorized'}); return None
        db=Store(s.database); db.initialize(); return db
    def do_GET(self):
        db=self._store()
        if db: self._send(200, {'watched_user_ids':db.watched(), 'groups':[dict(x) for x in db.groups()]})
    def do_POST(self):
        db=self._store()
        if not db:return
        try: payload=json.loads(self.rfile.read(int(self.headers.get('Content-Length','0')))); user_id=int(payload['telegram_user_id'])
        except (ValueError,KeyError,json.JSONDecodeError): return self._send(400,{'error':'telegram_user_id must be a numeric integer'})
        if self.path.endswith('/unwatch'): db.remove_watched(user_id); action='removed'
        else: db.add_watched(user_id, str(payload.get('display_name',''))); action='watching'
        self._send(200, {'status':action,'telegram_user_id':user_id})
