# Listner

`PresenceNotificationService` provides the presence-to-notification behavior for
users a person follows. Feed it presence events from the realtime transport and
provide a notifier adapter for the application's notification API.

```js
const service = new PresenceNotificationService(browserNotifier, ['user-123']);

service.handlePresenceUpdate({
  userId: 'user-123',
  displayName: 'Ada',
  status: 'offline',
});
service.handlePresenceUpdate({
  userId: 'user-123',
  displayName: 'Ada',
  status: 'online', // sends one notification
});
```

The service only alerts on an `offline` to `online` transition. Initial stream
snapshots and duplicate online events are intentionally ignored.

## Test

```sh
npm test
```
