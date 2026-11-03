import { settings, personalizedHomeMode } from './settings.js';
import { accountActive } from './vote.js';
import { getSubs } from './subscriptions.js';

/** Local subscriptions take over Home when there's no personalized feed, or when the
 *  user picked them as the home feed in settings. */
export function subscribedIsHome() {
  // Logged in: the account's real subscriptions, only when it's picked as the home feed.
  if (accountActive()) return personalizedHomeActive() && settings.homeFeed === 'subscribed';
  return getSubs().length > 0 && (!personalizedHomeActive() || settings.homeFeed === 'subscribed');
}

/** Popular picked as the home feed in settings (only offered alongside a personalized home). */
export function popularIsHome() {
  return personalizedHomeActive() && settings.homeFeed === 'popular';
}

export function personalizedHomeActive() {
  return !!personalizedHomeMode();
}
