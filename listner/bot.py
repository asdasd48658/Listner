"""Optional Bot API command loop; runs separately from the user-account client."""
from __future__ import annotations
import json, time, urllib.parse, urllib.request
from .config import Settings
from .db import Store

def request(token, method, data):
    return json.loads(urllib.request.urlopen(f"https://api.telegram.org/bot{token}/{method}", urllib.parse.urlencode(data).encode(), timeout=35).read())
def main():
    s=Settings(); db=Store(s.database); db.initialize(); offset=0
    if not s.bot_token: raise RuntimeError("BOT_TOKEN is required")
    while True:
      for update in request(s.bot_token, 'getUpdates', {'offset':offset,'timeout':30}).get('result',[]):
        offset=update['update_id']+1; message=update.get('message',{}); chat=str(message.get('chat',{}).get('id',''))
        if s.bot_chat_id and chat != s.bot_chat_id: continue
        words=message.get('text','').split()
        if words[:1] == ['/watch'] and len(words)==2 and words[1].lstrip('-').isdigit(): db.add_watched(int(words[1])); reply='Watching '+words[1]
        elif words[:1] == ['/unwatch'] and len(words)==2: db.remove_watched(int(words[1])); reply='Removed '+words[1]
        elif words[:1] == ['/list']: reply='Watching: '+(', '.join(map(str,db.watched())) or 'nobody')
        else: reply='Commands: /watch <numeric_user_id>, /unwatch <numeric_user_id>, /list'
        request(s.bot_token,'sendMessage',{'chat_id':chat,'text':reply})
if __name__=='__main__': main()
