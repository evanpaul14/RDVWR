export function setMutePref(muted) {
  state.userPrefersMuted = muted;
  localStorage.setItem('mutePreference', muted ? 'muted' : 'unmuted');
}

export function setVolumePref(vol) {
  state.userVolume = vol;
  localStorage.setItem('volumePreference', String(vol));
}

export const state = {
  userPrefersMuted: localStorage.getItem('mutePreference') !== 'unmuted',
  userVolume: (() => {
    const v = parseFloat(localStorage.getItem('volumePreference'));
    return isFinite(v) && v >= 0 && v <= 1 ? v : 1;
  })(),

  // Feed
  currentSub: '',
  currentSort: 'top',
  currentTime: 'all',
  afterToken: null,
  currentAfter: null,
  loading: false,
  feedGen: 0,

  // Home
  homeMode: false,
  homeLastRecSource: null,

  // Multi
  multiMode: false,
  multiUsername: '',
  multiName: '',

  // Profile
  profileMode: false,
  profileTab: 'posts',
  profileSort: 'new',
  profileTime: 'all',
  profileUser: '',
  profileAfter: null,

  // Search
  searchMode: false,
  searchQuery: '',
  searchSort: 'relevance',
  searchTime: 'all',
  searchSub: '',
  searchSubStored: '',
  searchNsfw: false,
  searchAfter: null,
  searchType: 'posts',
  communityAfter: null,
  userAfter: null,
  searchFlairNav: null,

  // Duplicates
  duplicatesMode: false,
  duplicatesSub: '',
  duplicatesPostId: '',
  duplicatesAfter: null,

  // Wiki
  wikiMode: false,
  _wikiSub: '',
  _wikiPage: '',

  // Post view
  currentCommentSort: 'confidence',
  _pvSub: '',
  _pvPostId: '',
  _pvCommentId: '',
  _pvShowingContext: false,
  _pvData: null,

  // Keyboard navigation
  selectedPostIdx: -1,

  // Live thread
  liveMode: false,
  liveThreadId: '',
  liveState: 'complete',
  liveAfter: null,
  _liveNewestId: '',
};
