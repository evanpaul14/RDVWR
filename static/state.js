export const state = {
  userPrefersMuted: localStorage.getItem('mutePreference') !== 'unmuted',

  // Feed
  currentSub: '',
  currentSort: 'top',
  currentTime: 'all',
  afterToken: null,
  currentAfter: null,
  loading: false,
  feedGen: 0,

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
