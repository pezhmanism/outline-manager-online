"""Two-step permanent deletion of local keys, bound to the signed-in session."""
delete_confirmations = {}


def delete_session(handler):
    cookie = SimpleCookie()
    cookie.load(handler.headers.get('Cookie',''))
    return cookie['sid'].value if 'sid' in cookie else ''


def delete_local_key(uid):
    user = next((u for u in state['users'] if u['id']==uid),None)
    if user is None: return False
    state['users'].remove(user)
    # Restart first: collect outstanding old counters, then remove their history.
    restart()
    db.execute('DELETE FROM usage WHERE uid=?',(uid,))
    db.commit()
    recent.discard(uid)
    return True


DeleteBaseH = H
class H(DeleteBaseH):
    def do_POST(self):
        if self.path != '/delete-key': return super().do_POST()
        host = self.headers.get('Host','').lower()
        if self.server.server_port!=9000 or not self.auth() or host not in (globals().get('LOCAL_ADMIN_HOST','localhost:18082'),'localhost:18082','127.0.0.1:18082',ADMIN_HOST):
            return self.send(403,'Forbidden','text/plain')
        if host==ADMIN_HOST and self.headers.get('X-Forwarded-Proto','').lower()!='https':
            return self.send(403,'Use HTTPS','text/plain')
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=4096: raise ValueError('Invalid form')
            fields=parse_qs(self.rfile.read(size).decode(),keep_blank_values=True)
            v=lambda key:fields.get(key,[''])[0]
            if not hmac.compare_digest(v('csrf'),csrf): raise ValueError('Form expired. Reload the dashboard.')
            with lock:
                user=next((u for u in state['users'] if u['id']==v('id')),None)
                if not user: return self.send(404,'Key no longer exists.','text/plain')
                now=time.time()
                for token, record in list(delete_confirmations.items()):
                    if record[2]<now: delete_confirmations.pop(token,None)
                if v('stage')=='confirm':
                    record=delete_confirmations.pop(v('confirmation'),None)
                    if not record or record[:2]!=(user['id'],delete_session(self)) or record[2]<now or v('agree')!='yes':
                        raise ValueError('Deletion confirmation expired or incomplete. Return to the key and try again.')
                    delete_local_key(user['id'])
                    return self.redirect()
                token=secrets.token_urlsafe(32)
                delete_confirmations[token]=(user['id'],delete_session(self),now+120)
                body='<input type="hidden" name="id" value="'+E(user['id'])+'"><input type="hidden" name="stage" value="confirm"><input type="hidden" name="confirmation" value="'+token+'"><label class="delete-agree"><input type="checkbox" name="agree" value="yes" required> Permanently delete this key and its usage history.</label><button class="danger">Delete permanently</button>'
                return self.send(200,STYLE+MODERN+'<main class="login"><h1>Delete key?</h1><p><strong>'+E(user['name'])+'</strong></p><p>This revokes access and removes this key and its recorded usage. It cannot be restored through the panel.</p>'+form('/delete-key',body)+'<p><a href="/">Cancel and return</a></p></main>')
        except (ValueError, UnicodeError) as error:
            return self.send(400,STYLE+MODERN+'<p class="error">'+E(error)+'</p><a href="/">Return to dashboard</a>')
