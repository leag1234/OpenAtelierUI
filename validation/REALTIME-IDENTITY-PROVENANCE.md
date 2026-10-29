# Realtime identity prototype

This bounded prototype uses the existing HTTP `get_current_user` and
`get_verified_user` checks before adding a Socket.IO session to the user and
session pools. An unauthenticated transport can stay open for sign-in; it gets
no authenticated presence, usage, room membership, or callback delivery until
the existing `user-join` event supplies a currently valid signed session.

Admin session revocation rotates the existing session epoch, then removes and
disconnects only that user's recorded sockets and usage entries. Event
callbacks check the live pool binding, so a removed callback cannot target its
former socket. Presence and usage are emitted to registered user/admin
sessions individually rather than to every connected socket. The socket
handlers do not create accounts or change roles.

Focused local source tests:

```sh
cd backend/open_webui/test
python3 test_socket_session_identity.py -v
```

The source-extracted tests cover quiet anonymous transport, late authenticated
join, invalid/stale and unsupported-role denial, verified room entry and
channel events, callback removal, and target-only revocation ordering. They
use a controlled HTTP-validator boundary; they do not replace a full app test
of JWT signing, epoch storage, Redis, Socket.IO transport, browser behavior,
multi-process revocation, or concurrency. Those remain primary-owned
integration checks. This prototype also does not claim to match every legacy
presence event emitted by another application.
