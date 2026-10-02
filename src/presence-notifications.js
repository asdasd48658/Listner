/**
 * Sends a notification when a followed user transitions from offline to online.
 *
 * The first presence value received for a user is treated as a snapshot rather
 * than a transition, so connecting to a presence stream does not generate a
 * notification for everyone who is already online.
 */
export class PresenceNotificationService {
  /**
   * @param {{ notify: (notification: { title: string, body: string, data: { userId: string } }) => void }} notifier
   * @param {Iterable<string>} followedUserIds Users whose presence should produce alerts.
   */
  constructor(notifier, followedUserIds = []) {
    if (!notifier || typeof notifier.notify !== 'function') {
      throw new TypeError('A notifier with a notify method is required.');
    }

    this.notifier = notifier;
    this.followedUserIds = new Set(followedUserIds);
    this.presenceByUserId = new Map();
  }

  /** Add a user to the notification list. */
  follow(userId) {
    this.followedUserIds.add(userId);
  }

  /** Stop delivering online notifications for a user. */
  unfollow(userId) {
    this.followedUserIds.delete(userId);
  }

  /**
   * Apply a presence update from the realtime transport.
   *
   * @param {{ userId: string, displayName?: string, status: 'online' | 'offline' }} update
   */
  handlePresenceUpdate({ userId, displayName = 'A user', status }) {
    if (status !== 'online' && status !== 'offline') {
      throw new TypeError('Presence status must be "online" or "offline".');
    }

    const previousStatus = this.presenceByUserId.get(userId);
    this.presenceByUserId.set(userId, status);

    if (
      previousStatus === 'offline' &&
      status === 'online' &&
      this.followedUserIds.has(userId)
    ) {
      this.notifier.notify({
        title: `${displayName} is online`,
        body: 'Send them a message while they are available.',
        data: { userId },
      });
    }
  }
}
