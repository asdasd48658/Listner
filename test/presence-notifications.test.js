import test from 'node:test';
import assert from 'node:assert/strict';
import { PresenceNotificationService } from '../src/presence-notifications.js';

function makeNotifier() {
  const notifications = [];
  return { notifications, notify: (notification) => notifications.push(notification) };
}

test('notifies followed users when they return online', () => {
  const notifier = makeNotifier();
  const service = new PresenceNotificationService(notifier, ['ada']);

  service.handlePresenceUpdate({ userId: 'ada', displayName: 'Ada', status: 'offline' });
  service.handlePresenceUpdate({ userId: 'ada', displayName: 'Ada', status: 'online' });

  assert.deepEqual(notifier.notifications, [{
    title: 'Ada is online',
    body: 'Send them a message while they are available.',
    data: { userId: 'ada' },
  }]);
});

test('does not notify for initial snapshots, repeated online updates, or unfollowed users', () => {
  const notifier = makeNotifier();
  const service = new PresenceNotificationService(notifier, ['ada']);

  service.handlePresenceUpdate({ userId: 'ada', displayName: 'Ada', status: 'online' });
  service.handlePresenceUpdate({ userId: 'ada', displayName: 'Ada', status: 'online' });
  service.handlePresenceUpdate({ userId: 'grace', displayName: 'Grace', status: 'offline' });
  service.handlePresenceUpdate({ userId: 'grace', displayName: 'Grace', status: 'online' });

  assert.deepEqual(notifier.notifications, []);
});

test('respects follow and unfollow changes', () => {
  const notifier = makeNotifier();
  const service = new PresenceNotificationService(notifier);

  service.follow('ada');
  service.handlePresenceUpdate({ userId: 'ada', status: 'offline' });
  service.handlePresenceUpdate({ userId: 'ada', status: 'online' });
  service.unfollow('ada');
  service.handlePresenceUpdate({ userId: 'ada', status: 'offline' });
  service.handlePresenceUpdate({ userId: 'ada', status: 'online' });

  assert.equal(notifier.notifications.length, 1);
});
